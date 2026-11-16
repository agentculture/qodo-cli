"""``config propose``: compare a checkout's committed HEAD with a base ref and return the change
only when it is a safe Qodo configuration change (plan task t6).

Every case runs against a real temporary git repository built in the test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from qodo import api, changelog
from qodo import propose as pr
from qodo import registry as reg
from qodo.cli import main
from qodo.cli._errors import EXIT_ENV_ERROR, CliError

REPO = "acme/widgets"
TOML_BASE = "[pr_reviewer]\nnum_max_findings = 3\n"


# --- helpers -------------------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return env


def git(repo: Path, *args: str, input: bytes | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        env=_git_env(),
        input=input,
        check=True,
    )
    return proc.stdout.decode()


class Checkout:
    """A temporary repository with ``main`` holding a base config and a ``work`` branch."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        git(path, "init", "-q", "-b", "main")
        self.write(".pr_agent.toml", TOML_BASE)
        self.write("README.md", "# widgets\n")
        self.commit("base")
        git(path, "checkout", "-q", "-b", "work")

    def write(self, name: str, content: str | bytes) -> Path:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def commit(self, message: str = "change") -> str:
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.path, "rev-parse", "HEAD").strip()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "checkouts"
    r.mkdir()
    return r.resolve()


@pytest.fixture
def co(root: Path) -> Checkout:
    return Checkout(root / "acme" / "widgets")


def settings(root: Path, **over) -> pr.ProposeSettings:
    base = pr.load_settings(env={}, config_path=None)
    values = {"repos": (REPO,), "checkout_roots": (str(root),), **over}
    return base.replace(**values)


def propose(co: Checkout, root: Path, base: str = "main", repo: str = REPO, **over) -> dict:
    return api.config_propose(repo, str(co.path), base, settings=settings(root, **over))


def refused(result: dict, reason: str) -> dict:
    assert result["ok"] is False, result
    assert result["reason"] == reason, result
    assert set(result) == {"ok", "reason", "detail", "paths"}
    assert isinstance(result["detail"], str) and result["detail"]
    assert "files" not in result and "content" not in json.dumps(result)
    return result


# --- success -------------------------------------------------------------------------------------


def test_an_allowlisted_valid_change_returns_the_committed_files(co, root):
    co.write("REVIEW.md", "# Review\n\nBe strict about tests.\n")
    co.write(".pr_agent.toml", TOML_BASE + '\n[review_agent]\nissues_user_guidelines = "x"\n')
    head = co.commit()
    out = propose(co, root)
    assert out["ok"] is True
    assert out["repo"] == REPO and out["base"] == "main" and out["head"] == head
    files = {f["path"]: f["content"] for f in out["files"]}
    assert files == {
        ".pr_agent.toml": TOML_BASE + '\n[review_agent]\nissues_user_guidelines = "x"\n',
        "REVIEW.md": "# Review\n\nBe strict about tests.\n",
    }
    assert out["warnings"] == []
    assert len(json.dumps(out)) < 64 * 1024


def test_uncommitted_work_is_ignored_and_contents_come_from_head(co, root):
    co.write("REVIEW.md", "committed\n")
    co.commit()
    co.write("REVIEW.md", "uncommitted edit\n")  # working tree differs from HEAD
    co.write("best_practices.md", "untracked\n")
    out = propose(co, root)
    assert out["ok"] is True
    assert out["files"] == [{"path": "REVIEW.md", "content": "committed\n"}]


def test_a_working_tree_symlink_cannot_redirect_the_read(co, root):
    co.write("REVIEW.md", "committed\n")
    co.commit()
    (co.path / "REVIEW.md").unlink()
    (co.path / "REVIEW.md").symlink_to("/etc/passwd")  # uncommitted
    out = propose(co, root)
    assert out["ok"] is True and out["files"][0]["content"] == "committed\n"


def test_legacy_settings_are_warnings_not_refusals(co, root):
    co.write("best_practices.md", "# Practices\n")
    co.write(".pr_agent.toml", TOML_BASE.replace("num", 'extra_instructions = "x"\nnum'))
    co.commit()
    out = propose(co, root)
    assert out["ok"] is True
    codes = {(w["code"], w["path"]) for w in out["warnings"]}
    assert ("legacy_file", "best_practices.md") in codes
    assert ("legacy_setting", ".pr_agent.toml") in codes
    for w in out["warnings"]:
        assert "not in Qodo's current configuration reference" in w["detail"]


def test_a_refused_key_set_to_an_allowed_value_is_proposable(co, root):
    co.write(".pr_agent.toml", TOML_BASE + "enable_auto_approval = false\n")
    co.commit()
    assert propose(co, root)["ok"] is True


def test_a_success_logs_paths_with_the_surface_and_a_refusal_logs_nothing(co, root, monkeypatch):
    co.write("README.md", "changed\n")
    co.commit()
    assert propose(co, root)["ok"] is False
    assert changelog.read() == []
    git(co.path, "reset", "-q", "--hard", "main")
    co.write("REVIEW.md", "r\n")
    co.commit()
    s = settings(root)
    out = api.config_propose(REPO, str(co.path), "main", settings=s, actor="api")
    assert out["ok"] is True
    (entry,) = changelog.read()
    assert entry["kind"] == "config.proposed" and entry["actor"] == "api"
    assert entry["repo"] == REPO and entry["files"] == ["REVIEW.md"]
    assert "r\n" not in json.dumps(entry)


# --- the repo allowlist comes first --------------------------------------------------------------


def test_a_repo_outside_the_allowlist_is_refused_before_the_checkout_is_touched(root, tmp_path):
    missing = tmp_path / "does-not-exist"
    s = settings(root)
    out = api.config_propose("evil/repo", str(missing), "-bad", settings=s)
    refused(out, "repo_not_allowed")
    assert out["paths"] == []


def test_an_empty_repo_allowlist_refuses_everything(co, root):
    refused(propose(co, root, repos=()), "repo_not_allowed")


def test_the_repo_allowlist_is_exact(co, root):
    refused(propose(co, root, repo="ACME/widgets"), "repo_not_allowed")
    refused(propose(co, root, repo="acme/widgets/../x"), "repo_not_allowed")


def test_the_repo_check_does_not_touch_git(root, tmp_path, monkeypatch):
    def boom(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("git ran")

    monkeypatch.setattr(pr.subprocess, "run", boom)
    out = api.config_propose("evil/repo", str(tmp_path / "x"), "main", settings=settings(root))
    refused(out, "repo_not_allowed")


# --- checkout roots ------------------------------------------------------------------------------


def test_a_checkout_outside_the_roots_is_refused(tmp_path, root):
    outside = Checkout(tmp_path / "elsewhere")
    refused(propose(outside, root), "checkout_not_allowed")


def test_a_checkout_reached_through_a_symlink_under_a_root_is_refused(tmp_path, root):
    outside = Checkout(tmp_path / "elsewhere")
    link = root / "link"
    link.symlink_to(outside.path)
    out = api.config_propose(REPO, str(link), "main", settings=settings(root))
    refused(out, "checkout_not_allowed")


def test_a_root_prefix_is_not_a_root(tmp_path, root):
    sibling = Checkout(Path(str(root) + "-evil") / "w")
    refused(propose(sibling, root), "checkout_not_allowed")


def test_no_roots_configured_refuses_everything(co, root):
    refused(propose(co, root, checkout_roots=()), "checkout_not_allowed")


def test_a_relative_checkout_path_is_refused(co, root, monkeypatch):
    monkeypatch.chdir(root)
    out = api.config_propose(REPO, "acme/widgets", "main", settings=settings(root))
    refused(out, "checkout_not_allowed")


def test_a_checkout_that_is_not_a_repository_is_an_environment_error(root):
    (root / "acme" / "widgets").mkdir(parents=True)
    with pytest.raises(CliError) as err:
        api.config_propose(REPO, str(root / "acme" / "widgets"), "main", settings=settings(root))
    assert err.value.code == EXIT_ENV_ERROR


def test_a_gitfile_checkout_is_an_environment_error(root, tmp_path):
    other = Checkout(tmp_path / "other")
    d = root / "acme" / "widgets"
    d.mkdir(parents=True)
    (d / ".git").write_text(f"gitdir: {other.path / '.git'}\n")
    with pytest.raises(CliError) as err:
        api.config_propose(REPO, str(d), "main", settings=settings(root))
    assert err.value.code == EXIT_ENV_ERROR


# --- the base ref --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base",
    ["-bad", "--output=/tmp/x", "main..work", "main@{1}", "a b", "x.lock", "/abs", "a//b", "~1",
     "HEAD^", "main:README.md", ""],
)  # fmt: skip
def test_a_hostile_base_ref_is_refused(co, root, base):
    co.write("REVIEW.md", "r\n")
    co.commit()
    refused(propose(co, root, base=base), "invalid_base")


def test_an_unknown_base_is_refused(co, root):
    refused(propose(co, root, base="no-such-branch"), "unknown_base")


def test_a_base_that_is_not_an_ancestor_is_refused(co, root):
    co.write("REVIEW.md", "r\n")
    co.commit()
    git(co.path, "checkout", "-q", "main")
    co.write("REVIEW.md", "other\n")
    co.commit()
    git(co.path, "checkout", "-q", "work")
    refused(propose(co, root, base="main"), "base_not_ancestor")


def test_a_commit_sha_is_a_valid_base(co, root):
    sha = git(co.path, "rev-parse", "main").strip()
    co.write("REVIEW.md", "r\n")
    co.commit()
    assert propose(co, root, base=sha)["ok"] is True


# --- the change set ------------------------------------------------------------------------------


def test_no_changes_is_a_refusal(co, root):
    co.commit("empty")
    refused(propose(co, root), "no_changes")


def test_a_path_outside_the_allowlist_is_refused_with_no_contents(co, root):
    co.write("REVIEW.md", "SECRET-REVIEW-TEXT\n")
    co.write("src/app.py", "print('x')\n")
    co.commit()
    out = refused(propose(co, root), "path_not_allowed")
    assert out["paths"] == ["src/app.py"]
    assert "SECRET-REVIEW-TEXT" not in json.dumps(out)


def test_the_path_allowlist_is_case_exact(co, root):
    co.write("review.md", "r\n")
    co.commit()
    refused(propose(co, root), "path_not_allowed")


def test_an_allowlisted_name_in_a_subdirectory_is_not_allowed(co, root):
    co.write("docs/REVIEW.md", "r\n")
    co.commit()
    refused(propose(co, root), "path_not_allowed")


def test_a_rename_onto_an_allowlisted_name_is_refused(co, root):
    co.write("notes.md", "a long enough body to be detected as a rename\n" * 5)
    co.commit("notes")
    git(co.path, "branch", "-f", "base2")
    git(co.path, "mv", "notes.md", "REVIEW.md")
    co.commit()
    out = refused(propose(co, root, base="base2"), "rename")
    assert out["paths"] == ["notes.md", "REVIEW.md"]


def test_a_deletion_is_refused(co, root):
    (co.path / ".pr_agent.toml").unlink()
    co.commit()
    out = refused(propose(co, root), "deletion")
    assert out["paths"] == [".pr_agent.toml"]


def test_too_many_files_is_refused(co, root):
    co.write("REVIEW.md", "r\n")
    co.write("best_practices.md", "b\n")
    co.commit()
    refused(propose(co, root, max_files=1), "too_many_files")


@pytest.mark.parametrize("target", ["/etc/passwd", "../outside.txt", "README.md"])
def test_an_allowlisted_name_that_is_a_symlink_is_refused(co, root, target):
    (co.path / "REVIEW.md").symlink_to(target)
    co.commit()
    out = refused(propose(co, root), "not_regular_file")
    assert "root:" not in json.dumps(out)


def test_a_regular_file_turned_into_a_symlink_is_refused(co, root):
    (co.path / ".pr_agent.toml").unlink()
    (co.path / ".pr_agent.toml").symlink_to("/etc/passwd")
    co.commit()
    refused(propose(co, root), "type_change")


def test_a_submodule_entry_is_refused(co, root):
    sha = git(co.path, "rev-parse", "HEAD").strip()
    git(co.path, "update-index", "--add", "--cacheinfo", f"160000,{sha},REVIEW.md")
    git(co.path, "commit", "-q", "-m", "gitlink")
    refused(propose(co, root), "not_regular_file")


def test_an_executable_file_is_refused(co, root):
    f = co.write("REVIEW.md", "r\n")
    f.chmod(0o755)
    co.commit()
    refused(propose(co, root), "executable")


def test_an_executable_mode_change_is_refused(co, root):
    (co.path / ".pr_agent.toml").chmod(0o755)
    co.commit()
    refused(propose(co, root), "executable")


def test_a_binary_file_is_refused(co, root):
    co.write("REVIEW.md", b"text\x00more\n")
    co.commit()
    refused(propose(co, root), "binary")


def test_a_non_utf8_file_is_refused(co, root):
    co.write("REVIEW.md", b"caf\xe9\n")
    co.commit()
    refused(propose(co, root), "not_utf8")


def test_an_oversized_file_is_refused(co, root):
    co.write("REVIEW.md", "x" * 200)
    co.commit()
    refused(propose(co, root, max_file_bytes=100), "file_too_large")


def test_a_reply_over_the_cap_is_refused_never_truncated(co, root):
    co.write("REVIEW.md", "x" * 900)
    co.write("best_practices.md", "y" * 900)
    co.commit()
    refused(propose(co, root, max_file_bytes=1000, max_reply_bytes=1500), "too_large")


def test_non_ascii_content_is_measured_escaped(co, root):
    co.write("REVIEW.md", "ש" * 400)  # 800 bytes UTF-8, 2400 chars JSON-escaped
    co.commit()
    refused(propose(co, root, max_file_bytes=1000, max_reply_bytes=2000), "too_large")


def test_invalid_toml_is_refused(co, root):
    co.write(".pr_agent.toml", "[pr_reviewer\nbroken = \n")
    co.commit()
    out = refused(propose(co, root), "invalid_toml")
    assert out["paths"] == [".pr_agent.toml"]


@pytest.mark.parametrize(
    "addition",
    [
        "enable_auto_approval = true\n",
        "auto_approve_for_no_suggestions = true\n",
        "\n[github_app]\nhandle_push_trigger = true\n",
        "\n[github_app]\nignore_bot_pr = false\n",
        '\n[github_app]\npush_commands = ["/review"]\n',
        '\n[config]\nmodel = "x"\n',
    ],
)
def test_a_change_that_widens_what_qodo_does_on_its_own_is_refused(co, root, addition):
    co.write(".pr_agent.toml", TOML_BASE + addition)
    co.commit()
    out = refused(propose(co, root), "refused_setting")
    assert out["paths"] == [".pr_agent.toml"]


def test_an_unchanged_refused_section_does_not_block_another_change(co, root):
    co.write(".pr_agent.toml", TOML_BASE + "\n[github_app]\nhandle_push_trigger = false\n")
    co.commit("base with github_app")
    git(co.path, "branch", "-f", "base2")
    co.write(".pr_agent.toml", TOML_BASE + "\n[github_app]\nhandle_push_trigger = false\n"
             '\n[review_agent]\nissues_user_guidelines = "y"\n')  # fmt: skip
    co.commit()
    assert propose(co, root, base="base2")["ok"] is True


def test_the_refused_settings_are_configuration(co, root):
    co.write(".pr_agent.toml", TOML_BASE + "enable_auto_approval = true\n")
    co.commit()
    assert propose(co, root, refused_settings=())["ok"] is True
    co.write(".pr_agent.toml", TOML_BASE + "num_max_findings = 9\n".replace("num", "x"))
    co.commit()
    s = ({"key": "pr_reviewer.x_max_findings"},)
    refused(propose(co, root, refused_settings=pr.parse_refused(s)), "refused_setting")


# --- path normalisation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["../REVIEW.md", "a/../REVIEW.md", "./REVIEW.md", "/REVIEW.md", "REVIEW.md/", "a\\REVIEW.md",
     "REVIEW.md\x00", "REVIEW.md\n", ""],
)  # fmt: skip
def test_an_unnormalised_path_is_never_allowed(path):
    assert pr.path_allowed(path, {"REVIEW.md": "markdown", "a/REVIEW.md": "markdown"}) is False


def test_a_dotdot_path_in_a_tree_is_refused(co, root):
    # git refuses ".." in the index, so build the tree by hand
    blob = git(co.path, "hash-object", "-w", "--stdin", input=b"x\n").strip()
    head_tree = git(co.path, "rev-parse", "HEAD^{tree}").strip()
    listing = git(co.path, "ls-tree", head_tree)
    try:
        tree = git(
            co.path, "mktree", input=(listing + f"100644 blob {blob}\t..\n").encode()
        ).strip()
    except subprocess.CalledProcessError:
        pytest.skip("this git refuses '..' in mktree")
    commit = git(co.path, "commit-tree", tree, "-p", "HEAD", "-m", "dotdot").strip()
    git(co.path, "reset", "-q", "--soft", commit)
    refused(propose(co, root), "path_not_allowed")


# --- hostile checkout configuration --------------------------------------------------------------


def test_a_hostile_git_config_runs_nothing(co, root, tmp_path):
    marker = tmp_path / "marker"
    hooks = co.path / ".git" / "evil-hooks"
    hooks.mkdir()
    for name in ("post-checkout", "pre-commit", "reference-transaction", "post-index-change"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
    co.write(".gitattributes", "* diff=evil filter=evil\n")
    co.commit("attributes")
    git(co.path, "branch", "-f", "base2")
    co.write("REVIEW.md", "r\n")
    co.commit()
    touch = f"!touch {marker}"
    with open(co.path / ".git" / "config", "a", encoding="utf-8") as fh:
        fh.write(
            "[core]\n"
            f"\tfsmonitor = {marker}.sh\n"
            f"\tpager = touch {marker}\n"
            f"\thooksPath = {hooks}\n"
            f"\tsshCommand = touch {marker}\n"
            f"\tworktree = {tmp_path}\n"
            "[diff]\n"
            f"\texternal = touch {marker}\n"
            "\trenames = copies\n"
            '[diff "evil"]\n'
            f"\ttextconv = touch {marker}\n"
            f"\tcommand = touch {marker}\n"
            '[filter "evil"]\n'
            f"\tclean = touch {marker}\n"
            f"\tsmudge = touch {marker}\n"
            f"\tprocess = touch {marker}\n"
            "[alias]\n"
            f"\tdiff-tree = {touch}\n"
            f"\tcat-file = {touch}\n"
            f"\trev-parse = {touch}\n"
            "[pager]\n"
            f"\tdiff-tree = touch {marker}\n"
            f"\tcat-file = touch {marker}\n"
        )
    script = Path(f"{marker}.sh")
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    out = propose(co, root, base="base2")
    assert not marker.exists(), "the checkout's configuration ran a command"
    assert out["ok"] is True and out["files"] == [{"path": "REVIEW.md", "content": "r\n"}]
    out = propose(co, root, base="main")  # .gitattributes is outside the allowlist
    assert not marker.exists()
    refused(out, "path_not_allowed")


def test_a_hostile_environment_does_not_reach_git(co, root, monkeypatch, tmp_path):
    co.write("REVIEW.md", "r\n")
    co.commit()
    marker = tmp_path / "marker"
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", f"touch {marker}")
    monkeypatch.setenv("GIT_PAGER", f"touch {marker}")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.pager'='touch {marker}'")
    assert propose(co, root)["ok"] is True
    assert not marker.exists()


# --- environment errors --------------------------------------------------------------------------


def test_git_missing_is_an_environment_error(co, root, monkeypatch):
    monkeypatch.setattr(pr.shutil, "which", lambda name: None)
    with pytest.raises(CliError) as err:
        propose(co, root)
    assert err.value.code == EXIT_ENV_ERROR


def test_a_git_timeout_is_an_environment_error(co, root, monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(pr.subprocess, "run", slow)
    with pytest.raises(CliError) as err:
        propose(co, root)
    assert err.value.code == EXIT_ENV_ERROR


# --- configuration -------------------------------------------------------------------------------


def test_the_shipped_defaults_allow_paths_but_no_repo_and_no_root():
    s = pr.load_settings(env={}, config_path=None)
    assert s.repos == () and s.checkout_roots == ()
    assert {".pr_agent.toml", "REVIEW.md", "best_practices.md"} <= set(s.paths)
    assert s.paths[".pr_agent.toml"] == "toml"
    assert s.refused_settings  # shipped as data


def test_settings_come_from_a_file_and_the_environment(tmp_path):
    cfg = tmp_path / "propose.json"
    cfg.write_text(
        json.dumps(
            {
                "repos": ["a/b"],
                "checkout_roots": ["/srv/one"],
                "paths": {"REVIEW.md": "markdown"},
                "max_files": 3,
            }
        )
    )
    s = pr.load_settings(env={}, config_path=cfg)
    assert s.repos == ("a/b",) and s.checkout_roots == ("/srv/one",)
    assert s.paths == {"REVIEW.md": "markdown"} and s.max_files == 3
    env = {
        "QODO_PROPOSE_REPOS": "c/d, e/f",
        "QODO_PROPOSE_CHECKOUT_ROOTS": os.pathsep.join(["/srv/two", "/srv/three"]),
    }
    s = pr.load_settings(env=env, config_path=cfg)
    assert s.repos == ("c/d", "e/f") and s.checkout_roots == ("/srv/two", "/srv/three")


def test_the_config_file_path_comes_from_the_environment(tmp_path, monkeypatch):
    cfg = tmp_path / "p.json"
    cfg.write_text(json.dumps({"repos": ["x/y"]}))
    monkeypatch.setenv("QODO_PROPOSE_CONFIG", str(cfg))
    monkeypatch.delenv("QODO_PROPOSE_REPOS", raising=False)
    assert pr.load_settings().repos == ("x/y",)


@pytest.mark.parametrize(
    "bad",
    [
        {"repos": "a/b"},
        {"repos": ["not a repo"]},
        {"checkout_roots": ["relative/root"]},
        {"paths": {"../x": "markdown"}},
        {"paths": {"x.md": "python"}},
        {"max_files": 0},
        {"unknown": 1},
        {"refused_settings": [{"key": ""}]},
    ],
)
def test_a_bad_config_file_is_an_environment_error(tmp_path, bad):
    cfg = tmp_path / "p.json"
    cfg.write_text(json.dumps(bad))
    with pytest.raises(CliError) as err:
        pr.load_settings(env={}, config_path=cfg)
    assert err.value.code == EXIT_ENV_ERROR


# --- the registry verb and the CLI ---------------------------------------------------------------


def test_the_verb_is_read_only_and_on_the_api_surface():
    verb = reg.default_registry().get("config", "propose")
    assert verb.writes is False
    assert "api" in verb.surfaces and "cli" in verb.surfaces
    assert set(verb.params["required"]) == {"repo", "checkout", "base"}
    assert verb.params["additionalProperties"] is False


def _env_for(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("QODO_PROPOSE_CONFIG", str(root / "absent.json"))
    monkeypatch.setenv("QODO_PROPOSE_REPOS", REPO)
    monkeypatch.setenv("QODO_PROPOSE_CHECKOUT_ROOTS", str(root))


def test_the_handler_reads_the_configuration_and_logs_the_surface(co, root, monkeypatch):
    _env_for(monkeypatch, root)
    co.write("REVIEW.md", "r\n")
    co.commit()
    verb = reg.default_registry().get("config", "propose")
    out = verb.handler({"repo": REPO, "checkout": str(co.path), "base": "main"}, surface="api")
    assert out["ok"] is True
    assert changelog.read()[-1]["actor"] == "api"


def test_the_cli_returns_a_refusal_with_exit_0(co, root, monkeypatch, capsys):
    _env_for(monkeypatch, root)
    co.write("README.md", "x\n")
    co.commit()
    rc = main(["config", "propose", "--repo", REPO, "--checkout", str(co.path), "--base", "main",
               "--json"])  # fmt: skip
    cap = capsys.readouterr()
    assert rc == 0 and cap.err == ""
    assert json.loads(cap.out)["reason"] == "path_not_allowed"


def test_the_cli_returns_the_files(co, root, monkeypatch, capsys):
    _env_for(monkeypatch, root)
    co.write("REVIEW.md", "r\n")
    co.commit()
    rc = main(["config", "propose", "--repo", REPO, "--checkout", str(co.path), "--base", "main",
               "--json"])  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert (
        rc == 0 and out["ok"] is True and out["files"] == [{"path": "REVIEW.md", "content": "r\n"}]
    )


def test_the_cli_reports_an_environment_error(root, monkeypatch, capsys):
    _env_for(monkeypatch, root)
    (root / "acme" / "widgets").mkdir(parents=True)
    place = str(root / "acme" / "widgets")
    rc = main(
        ["config", "propose", "--repo", REPO, "--checkout", place, "--base", "main", "--json"]
    )
    assert rc == 2 and json.loads(capsys.readouterr().err)["remediation"]


def test_the_cli_requires_the_params(capsys):
    with pytest.raises(SystemExit):
        main(["config", "propose", "--json"])


def test_git_is_available_for_these_tests():
    assert shutil.which("git")


def test_explain_learn_and_overview_describe_config_propose(capsys):
    from qodo.explain import resolve

    text = resolve(("config", "propose"))
    for reason in pr.REASONS:
        assert f"`{reason}`" in text
    assert "QODO_PROPOSE_REPOS" in text and "read-only" in text.lower()
    for argv in (["learn"], ["overview"], ["config", "overview"]):
        assert main(argv) == 0
        assert "config propose" in capsys.readouterr().out


def test_an_allowed_repo_name_does_not_open_another_repos_checkout(root):
    other = Checkout(root / "acme" / "secrets")
    out = api.config_propose(REPO, str(other.path), "main", settings=settings(root))
    refused(out, "checkout_not_allowed")
    flat = Checkout(root / "widgets")  # right name, wrong place
    refused(
        api.config_propose(REPO, str(flat.path), "main", settings=settings(root)),
        "checkout_not_allowed",
    )


def test_a_base_ref_with_a_trailing_newline_is_refused(co, root):
    out = api.config_propose(REPO, str(co.path), "main\n", settings=settings(root))
    refused(out, "invalid_base")
