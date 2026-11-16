"""``fix propose``: an agent's committed code fix, checked like ``config propose`` but with the
fix profile's path policy (any normal path except a refused set shipped as data), its own repo
allowlist and checkout roots, and caps suited to small code changes.

Every case runs against a real temporary git repository built in the test. A refusal never
carries file contents.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from qodo import api, changelog
from qodo import propose as pr
from qodo import registry as reg
from qodo.cli import main
from qodo.cli._errors import EXIT_ENV_ERROR, CliError
from tests.test_config_propose import Checkout, git

REPO = "acme/widgets"
SECRET = "SECRET-FIX-CONTENT"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "fix-checkouts"
    r.mkdir()
    return r.resolve()


@pytest.fixture
def co(root: Path) -> Checkout:
    return Checkout(root / "acme" / "widgets")


def settings(root: Path, **over) -> pr.ProposeSettings:
    base = pr.load_settings(env={}, config_path=None, profile=pr.FIX_PROFILE)
    return base.replace(**{"repos": (REPO,), "checkout_roots": (str(root),), **over})


def propose(co: Checkout, root: Path, base: str = "main", repo: str = REPO, **over) -> dict:
    return api.fix_propose(repo, str(co.path), base, settings=settings(root, **over))


def refused(result: dict, reason: str) -> dict:
    assert result["ok"] is False, result
    assert result["reason"] == reason, result
    assert set(result) == {"ok", "reason", "detail", "paths"}
    assert isinstance(result["detail"], str) and result["detail"]
    text = json.dumps(result)
    assert "files" not in result and "content" not in text and SECRET not in text
    return result


# --- success -------------------------------------------------------------------------------------


def test_a_small_code_fix_returns_the_files_the_base_and_the_head(co, root):
    co.write("src/app.py", "def f():\n    return 1\n")
    co.write("README.md", "# widgets\n\nFixed.\n")
    head = co.commit()
    base_sha = git(co.path, "rev-parse", "main").strip()
    out = propose(co, root)
    assert out["ok"] is True
    assert out["repo"] == REPO and out["base"] == "main"
    assert out["base_sha"] == base_sha and out["head"] == head
    assert {f["path"]: f["content"] for f in out["files"]} == {
        "src/app.py": "def f():\n    return 1\n",
        "README.md": "# widgets\n\nFixed.\n",
    }
    assert out["warnings"] == []
    assert len(json.dumps(out)) < 64 * 1024


def test_contents_come_from_head_and_uncommitted_work_is_ignored(co, root):
    co.write("src/app.py", "committed\n")
    co.commit()
    co.write("src/app.py", "uncommitted\n")
    co.write("src/new.py", "untracked\n")
    out = propose(co, root)
    assert out["files"] == [{"path": "src/app.py", "content": "committed\n"}]


def test_a_success_logs_fix_proposed_with_paths_only_and_a_refusal_logs_nothing(co, root):
    co.write(".github/workflows/ci.yml", SECRET)
    co.commit()
    refused(propose(co, root), "path_not_allowed")
    assert changelog.read() == []
    git(co.path, "reset", "-q", "--hard", "main")
    co.write("src/app.py", SECRET)
    co.commit()
    s = settings(root)
    out = api.fix_propose(REPO, str(co.path), "main", settings=s, actor="api")
    assert out["ok"] is True
    (entry,) = changelog.read()
    assert entry["kind"] == "fix.proposed" and entry["actor"] == "api"
    assert entry["repo"] == REPO and entry["files"] == ["src/app.py"]
    assert SECRET not in json.dumps(entry)


# --- the default refused paths, one case per pattern ---------------------------------------------


def _default_rules() -> tuple[pr.PathRule, ...]:
    return pr.load_settings(env={}, config_path=None, profile=pr.FIX_PROFILE).refused_paths


def _example(rule: pr.PathRule) -> str:
    """A path the rule must refuse, nested where the rule kind allows it."""
    name = rule.pattern.replace("*", "x")
    if rule.kind == "segment":
        return f"sub/{name}/file.txt"
    if rule.kind == "name":
        return f"deep/dir/{name}"
    return name


def _commit_nested(co: Checkout, path: str, data: bytes) -> None:
    """Commit ``path`` built tree by tree (git's index refuses a ``.git`` segment)."""
    blob = git(co.path, "hash-object", "-w", "--stdin", input=data).strip()
    *dirs, name = path.split("/")
    entry = f"100644 blob {blob}\t{name}\n"
    for d in reversed(dirs):
        sha = git(co.path, "mktree", input=entry.encode()).strip()
        entry = f"040000 tree {sha}\t{d}\n"
    listing = git(co.path, "ls-tree", git(co.path, "rev-parse", "HEAD^{tree}").strip())
    tree = git(co.path, "mktree", input=(listing + entry).encode()).strip()
    commit = git(co.path, "commit-tree", tree, "-p", "HEAD", "-m", "nested").strip()
    git(co.path, "reset", "-q", "--soft", commit)


@pytest.mark.parametrize("rule", _default_rules(), ids=lambda r: f"{r.kind}:{r.pattern}")
def test_each_default_refused_pattern_is_refused_with_no_contents(co, root, rule):
    path = _example(rule)
    co.write("src/ok.py", SECRET + "\n")
    if rule.pattern == ".git":
        co.commit()
        try:
            _commit_nested(co, path, (SECRET + "\n").encode())
        except subprocess.CalledProcessError:
            pytest.skip("this git refuses a .git segment in mktree")
        out = refused(propose(co, root), "path_not_allowed")
        assert path in out["paths"] and rule.pattern in out["detail"]
        return
    co.write(path, SECRET + "\n")
    co.commit()
    out = refused(propose(co, root), "path_not_allowed")
    assert out["paths"] == [path]
    assert rule.pattern in out["detail"]


@pytest.mark.parametrize(
    "path",
    [".github/workflows/ci.yml", ".GITHUB/workflows/ci.yml", ".pr_agent.toml", "REVIEW.md",
     "best_practices.md", "docs/REVIEW.md", ".env", ".env.production", "certs/server.pem",
     "secrets/deploy.key", "id_rsa", "home/.ssh/id_ed25519", "package-lock.json", "uv.lock",
     ".gitattributes", "CLAUDE.md", ".claude/skills/x/SKILL.md", "requirements-dev.txt"],
)  # fmt: skip
def test_the_named_paths_are_refused(co, root, path):
    co.write(path, SECRET + "\n")
    co.commit()
    refused(propose(co, root), "path_not_allowed")


@pytest.mark.parametrize(
    "path",
    ["docs/.github-notes.md", "environment.py", "src/env_config.py", "src/keys.py",
     "src/id_utils.py", "docs/review.md.txt", "lib/package_json.py", "github/README.md",
     "src/.gitignore", "Makefile"],
)  # fmt: skip
def test_a_path_that_merely_resembles_a_refused_one_is_allowed(co, root, path):
    co.write(path, "fine\n")
    co.commit()
    out = propose(co, root)
    assert out["ok"] is True, out
    assert out["files"] == [{"path": path, "content": "fine\n"}]


def test_the_refused_paths_are_configuration_and_extendable(co, root, tmp_path):
    co.write("src/generated/api.py", "x\n")
    co.commit()
    assert propose(co, root)["ok"] is True
    cfg = tmp_path / "propose.json"
    cfg.write_text(
        json.dumps(
            {
                "repos": ["config/only"],  # the config profile's own key: not the fix's
                "fix": {
                    "repos": [REPO],
                    "checkout_roots": [str(root)],
                    "extra_refused_paths": [
                        {"kind": "segment", "pattern": "generated", "why": "generated code"}
                    ],
                },
            }
        )
    )
    s = pr.load_settings(env={}, config_path=cfg, profile=pr.FIX_PROFILE)
    assert s.repos == (REPO,) and len(s.refused_paths) == len(_default_rules()) + 1
    out = refused(api.fix_propose(REPO, str(co.path), "main", settings=s), "path_not_allowed")
    assert "generated code" in out["detail"]
    # replacing the list (a deliberate choice) drops the shipped rules
    cfg.write_text(json.dumps({"fix": {"refused_paths": []}}))
    s = pr.load_settings(env={}, config_path=cfg, profile=pr.FIX_PROFILE)
    assert s.refused_paths == ()


# --- every config propose protection holds here too ----------------------------------------------


def test_a_repo_outside_the_fix_allowlist_is_refused_before_git_runs(root, tmp_path, monkeypatch):
    def boom(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("git ran")

    monkeypatch.setattr(pr.subprocess, "run", boom)
    out = api.fix_propose("evil/repo", str(tmp_path / "nope"), "-x", settings=settings(root))
    assert refused(out, "repo_not_allowed")["paths"] == []


def test_unset_allowlists_refuse_everything(co, root):
    co.write("src/app.py", "x\n")
    co.commit()
    unset = pr.load_settings(env={}, config_path=None, profile=pr.FIX_PROFILE)
    assert unset.repos == () and unset.checkout_roots == ()
    refused(api.fix_propose(REPO, str(co.path), "main", settings=unset), "repo_not_allowed")
    refused(propose(co, root, checkout_roots=()), "checkout_not_allowed")


def test_the_checkout_must_be_exactly_root_owner_name(tmp_path, root):
    outside = Checkout(tmp_path / "elsewhere")
    refused(propose(outside, root), "checkout_not_allowed")
    other = Checkout(root / "acme" / "secrets")
    refused(api.fix_propose(REPO, str(other.path), "main", settings=settings(root)),
            "checkout_not_allowed")  # fmt: skip
    link = root / "acme" / "link"
    link.symlink_to(outside.path)
    refused(api.fix_propose(REPO, str(link), "main", settings=settings(root)),
            "checkout_not_allowed")  # fmt: skip


def test_a_gitfile_checkout_is_an_environment_error(root, tmp_path):
    other = Checkout(tmp_path / "other")
    d = root / "acme" / "widgets"
    d.mkdir(parents=True)
    (d / ".git").write_text(f"gitdir: {other.path / '.git'}\n")
    with pytest.raises(CliError) as err:
        api.fix_propose(REPO, str(d), "main", settings=settings(root))
    assert err.value.code == EXIT_ENV_ERROR


@pytest.mark.parametrize("base", ["-bad", "--output=/tmp/x", "main..work", "main@{1}", "HEAD^"])
def test_a_hostile_base_is_refused(co, root, base):
    co.write("src/app.py", SECRET)
    co.commit()
    refused(propose(co, root, base=base), "invalid_base")


def test_unknown_and_non_ancestor_bases_are_refused(co, root):
    refused(propose(co, root, base="nope"), "unknown_base")
    co.write("src/app.py", "a\n")
    co.commit()
    git(co.path, "checkout", "-q", "main")
    co.write("src/app.py", "b\n")
    co.commit()
    git(co.path, "checkout", "-q", "work")
    refused(propose(co, root), "base_not_ancestor")


def test_no_changes_is_a_refusal(co, root):
    co.commit("empty")
    refused(propose(co, root), "no_changes")


def test_a_rename_is_refused(co, root):
    co.write("src/notes.py", "a long enough body to be detected as a rename\n" * 5)
    co.commit("notes")
    git(co.path, "branch", "-f", "base2")
    git(co.path, "mv", "src/notes.py", "src/other.py")
    co.commit()
    refused(propose(co, root, base="base2"), "rename")


def test_a_deletion_is_refused(co, root):
    (co.path / "README.md").unlink()
    co.commit()
    assert refused(propose(co, root), "deletion")["paths"] == ["README.md"]


def test_too_many_files_is_refused_and_twenty_is_the_default_cap(co, root):
    assert settings(root).max_files == 20
    for i in range(21):
        co.write(f"src/m{i}.py", SECRET)
    co.commit()
    refused(propose(co, root), "too_many_files")


@pytest.mark.parametrize("target", ["/etc/passwd", "../outside.txt", "README.md"])
def test_a_symlink_is_refused(co, root, target):
    (co.path / "src").mkdir()
    (co.path / "src" / "link.py").symlink_to(target)
    co.commit()
    out = refused(propose(co, root), "not_regular_file")
    assert "root:" not in json.dumps(out)


def test_a_file_turned_into_a_symlink_is_refused(co, root):
    (co.path / "README.md").unlink()
    (co.path / "README.md").symlink_to("/etc/passwd")
    co.commit()
    refused(propose(co, root), "type_change")


def test_a_submodule_entry_is_refused(co, root):
    sha = git(co.path, "rev-parse", "HEAD").strip()
    git(co.path, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/lib")
    git(co.path, "commit", "-q", "-m", "gitlink")
    refused(propose(co, root), "not_regular_file")


def test_an_executable_file_is_refused(co, root):
    co.write("bin/run.sh", "#!/bin/sh\necho " + SECRET + "\n").chmod(0o755)
    co.commit()
    refused(propose(co, root), "executable")


def test_binary_and_non_utf8_files_are_refused(co, root):
    co.write("src/blob.bin", b"x\x00" + SECRET.encode())
    co.commit()
    refused(propose(co, root), "binary")
    git(co.path, "reset", "-q", "--hard", "main")
    co.write("src/latin.py", b"caf\xe9 " + SECRET.encode())
    co.commit()
    refused(propose(co, root), "not_utf8")


def test_size_caps_refuse_rather_than_truncate(co, root):
    co.write("src/big.py", SECRET + "x" * 300)
    co.commit()
    refused(propose(co, root, max_file_bytes=100), "file_too_large")
    out = refused(propose(co, root, max_file_bytes=1000, max_reply_bytes=200), "too_large")
    assert out["paths"] == []


def test_a_change_too_large_for_the_64k_step_is_too_large_by_default(co, root):
    for i in range(3):
        co.write(f"src/part{i}.py", "y" * 30000)
    co.commit()
    s = settings(root)
    assert s.max_reply_bytes <= 64 * 1024
    refused(propose(co, root), "too_large")


def test_a_dotdot_path_in_a_tree_is_refused(co, root):
    blob = git(co.path, "hash-object", "-w", "--stdin", input=b"x\n").strip()
    listing = git(co.path, "ls-tree", git(co.path, "rev-parse", "HEAD^{tree}").strip())
    try:
        tree = git(co.path, "mktree", input=(listing + f"100644 blob {blob}\t..\n").encode())
    except subprocess.CalledProcessError:
        pytest.skip("this git refuses '..' in mktree")
    commit = git(co.path, "commit-tree", tree.strip(), "-p", "HEAD", "-m", "dotdot").strip()
    git(co.path, "reset", "-q", "--soft", commit)
    refused(propose(co, root), "path_not_allowed")


def test_a_hostile_git_config_runs_nothing(co, root, tmp_path):
    marker = tmp_path / "marker"
    co.write(".gitattributes", "* diff=evil filter=evil\n")
    co.commit("attributes")
    git(co.path, "branch", "-f", "base2")
    co.write("src/app.py", "r\n")
    co.commit()
    with open(co.path / ".git" / "config", "a", encoding="utf-8") as fh:
        fh.write(
            f"[core]\n\tpager = touch {marker}\n\tfsmonitor = touch {marker}\n"
            f"\tworktree = {tmp_path}\n[diff]\n\texternal = touch {marker}\n"
            f'[diff "evil"]\n\ttextconv = touch {marker}\n'
            f'[filter "evil"]\n\tclean = touch {marker}\n\tsmudge = touch {marker}\n'
            f"[alias]\n\tdiff-tree = !touch {marker}\n\tcat-file = !touch {marker}\n"
        )
    out = propose(co, root, base="base2")
    assert not marker.exists()
    assert out["ok"] is True and out["files"] == [{"path": "src/app.py", "content": "r\n"}]
    refused(propose(co, root, base="main"), "path_not_allowed")  # .gitattributes is refused
    assert not marker.exists()


def test_a_hostile_environment_does_not_reach_git(co, root, monkeypatch, tmp_path):
    co.write("src/app.py", "r\n")
    co.commit()
    marker = tmp_path / "marker"
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", f"touch {marker}")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.pager'='touch {marker}'")
    assert propose(co, root)["ok"] is True
    assert not marker.exists()


# --- configuration -------------------------------------------------------------------------------


def test_the_shipped_fix_defaults_allow_no_repo_and_no_root():
    s = pr.load_settings(env={}, config_path=None, profile=pr.FIX_PROFILE)
    assert s.repos == () and s.checkout_roots == () and s.any_path is True
    assert s.paths == {} and s.refused_paths
    assert s.max_files == 20 and s.max_reply_bytes <= 64 * 1024


def test_fix_settings_come_from_its_own_environment_variables(tmp_path):
    env = {
        "QODO_FIX_REPOS": "a/b c/d",
        "QODO_FIX_CHECKOUT_ROOTS": os.pathsep.join(["/srv/fix-one", "/srv/fix-two"]),
        "QODO_PROPOSE_REPOS": "x/y",
        "QODO_PROPOSE_CHECKOUT_ROOTS": "/srv/config",
    }
    fix = pr.load_settings(env=env, config_path=None, profile=pr.FIX_PROFILE)
    assert fix.repos == ("a/b", "c/d") and fix.checkout_roots == ("/srv/fix-one", "/srv/fix-two")
    config = pr.load_settings(env=env, config_path=None)
    assert config.repos == ("x/y",) and config.checkout_roots == ("/srv/config",)


def test_a_fix_section_does_not_disturb_config_propose(tmp_path):
    cfg = tmp_path / "propose.json"
    cfg.write_text(json.dumps({"repos": ["a/b"], "fix": {"repos": ["c/d"]}}))
    assert pr.load_settings(env={}, config_path=cfg).repos == ("a/b",)
    assert pr.load_settings(env={}, config_path=cfg, profile=pr.FIX_PROFILE).repos == ("c/d",)


@pytest.mark.parametrize(
    "bad",
    [
        {"fix": []},
        {"fix": {"paths": {"x": "markdown"}}},
        {"fix": {"repos": ["not a repo"]}},
        {"fix": {"checkout_roots": ["relative"]}},
        {"fix": {"max_files": 0}},
        {"fix": {"refused_paths": [{"kind": "glob", "pattern": "x"}]}},
        {"fix": {"extra_refused_paths": [{"kind": "name", "pattern": "a/b"}]}},
        {"fix": {"extra_refused_paths": [{"kind": "name", "pattern": ""}]}},
        {"fix": {"extra_refused_paths": [{"kind": "name", "pattern": "x", "other": 1}]}},
    ],
)
def test_a_bad_fix_section_is_an_environment_error(tmp_path, bad):
    cfg = tmp_path / "propose.json"
    cfg.write_text(json.dumps(bad))
    with pytest.raises(CliError) as err:
        pr.load_settings(env={}, config_path=cfg, profile=pr.FIX_PROFILE)
    assert err.value.code == EXIT_ENV_ERROR
    assert "fix propose" in err.value.message and "QODO_FIX_REPOS" in err.value.remediation


# --- the verb, the CLI and the HTTP role --------------------------------------------------------


def test_the_verb_is_read_only_on_cli_and_api_only():
    verb = reg.default_registry().get("fix", "propose")
    assert verb.writes is False and verb.kind == "local"
    assert verb.surfaces == ("cli", "api")
    assert set(verb.params["required"]) == {"repo", "checkout", "base"}
    assert verb.params["additionalProperties"] is False


def _env_for(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("QODO_PROPOSE_CONFIG", str(root / "absent.json"))
    monkeypatch.setenv("QODO_FIX_REPOS", REPO)
    monkeypatch.setenv("QODO_FIX_CHECKOUT_ROOTS", str(root))


def test_the_handler_reads_the_fix_configuration_and_logs_the_surface(co, root, monkeypatch):
    _env_for(monkeypatch, root)
    co.write("src/app.py", "r\n")
    co.commit()
    verb = reg.default_registry().get("fix", "propose")
    out = verb.handler({"repo": REPO, "checkout": str(co.path), "base": "main"}, surface="api")
    assert out["ok"] is True
    entry = changelog.read()[-1]
    assert entry["kind"] == "fix.proposed" and entry["actor"] == "api"


def test_the_cli_returns_the_files_and_a_refusal_with_exit_0(co, root, monkeypatch, capsys):
    _env_for(monkeypatch, root)
    co.write("src/app.py", "r\n")
    co.commit()
    argv = ["fix", "propose", "--repo", REPO, "--checkout", str(co.path), "--base", "main"]
    assert main([*argv, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == [
        {"path": "src/app.py", "content": "r\n"}
    ]
    co.write(".github/workflows/ci.yml", "x\n")
    co.commit()
    assert main([*argv, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "path_not_allowed"


def test_explain_learn_and_overview_describe_fix_propose(capsys):
    from qodo.explain import resolve

    text = resolve(("fix", "propose"))
    assert "QODO_FIX_REPOS" in text and "QODO_FIX_CHECKOUT_ROOTS" in text
    assert "too_large" in text and "read-only" in text.lower()
    for argv in (["learn"], ["overview"], ["fix", "overview"]):
        assert main(argv) == 0
        assert "fix propose" in capsys.readouterr().out
