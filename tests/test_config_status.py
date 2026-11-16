"""``config status``: what the Configuration screen shows for each allowlisted repo.

The repo is named from the bridge's repo allowlist, never given as a path; it is refused before
the filesystem is touched when it is not allowlisted; only the allowlisted Qodo configuration
files are read, from the repo's configured checkout (``<root>/<owner>/<name>``); the reply holds
file names, sizes, validation results, warnings and the change log's proposals, never contents.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qodo import api, changelog
from qodo import config_status as cs
from qodo import propose as pr
from qodo import registry as reg
from qodo.cli import main
from qodo.cli._errors import EXIT_USER_ERROR, CliError

REPO = "acme/widgets"
OTHER = "acme/gadgets"
SECRET = "TOP-SECRET-CONTENT-7f3a"


def make_settings(root: Path, repos=(REPO,), **changes) -> pr.ProposeSettings:
    base = pr.load_settings(env={}, config_path=None)
    return base.replace(repos=tuple(repos), checkout_roots=(str(root),), **changes)


def checkout(root: Path, repo: str = REPO, files: dict[str, str] | None = None) -> Path:
    path = root.joinpath(*repo.split("/"))
    (path / ".git").mkdir(parents=True)
    for name, text in (files or {}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return path


GOOD_TOML = "[pr_reviewer]\nnum_max_findings = 3\n\n[review_agent]\nenable_review_md = true\n"


# --- allowlist first, never a path from the caller -------------------------------------------


def test_a_repo_outside_the_allowlist_is_refused_before_the_filesystem(tmp_path, monkeypatch):
    checkout(tmp_path, OTHER, {".pr_agent.toml": GOOD_TOML})
    touched = []
    for name in ("lstat", "stat", "open", "listdir", "scandir"):
        real = getattr(os, name)

        def spy(*a, _real=real, _name=name, **k):
            touched.append(_name)
            return _real(*a, **k)

        monkeypatch.setattr(os, name, spy)
    monkeypatch.setattr(os.path, "realpath", lambda *a, **k: touched.append("realpath") or a[0])
    with pytest.raises(CliError) as err:
        cs.status(OTHER, settings=make_settings(tmp_path))
    assert err.value.code == EXIT_USER_ERROR
    assert "allowlist" in err.value.message
    assert touched == []


@pytest.mark.parametrize("bad", ["../acme/widgets", "/etc/passwd", "acme/widgets/../x", ""])
def test_a_path_shaped_repo_is_refused(tmp_path, bad):
    with pytest.raises(CliError) as err:
        cs.status(bad, settings=make_settings(tmp_path))
    assert err.value.code == EXIT_USER_ERROR


def test_the_verb_takes_no_path_param():
    verb = reg.default_registry().get("config", "status")
    assert set(verb.params["properties"]) == {"repo"}
    assert verb.params["additionalProperties"] is False
    assert verb.writes is False
    assert verb.surfaces == ("cli", "api")
    with pytest.raises(CliError) as err:
        verb.handler({"repo": REPO, "checkout": "/etc"})
    assert err.value.code == EXIT_USER_ERROR and "checkout" in err.value.message


# --- what is read and what is returned ----------------------------------------------------


def test_a_repo_with_valid_files_reports_names_sizes_and_validation_but_no_contents(tmp_path):
    checkout(
        tmp_path,
        files={
            ".pr_agent.toml": GOOD_TOML + f"# {SECRET}\n",
            "REVIEW.md": f"# Review\n{SECRET}\n",
            "README.md": "not a config file",
        },
    )
    out = cs.status(REPO, settings=make_settings(tmp_path))
    text = json.dumps(out)
    assert SECRET not in text
    assert str(tmp_path) not in text  # the bridge's paths are not disclosed
    [repo] = out["repos"]
    assert repo["repo"] == REPO and repo["checkout"] == "found"
    files = {f["path"]: f for f in repo["files"]}
    assert set(files) == {".pr_agent.toml", "REVIEW.md", "best_practices.md"}  # the allowlist
    assert "README.md" not in text
    toml = files[".pr_agent.toml"]
    assert toml["present"] is True and toml["kind"] == "toml"
    assert toml["bytes"] == len((GOOD_TOML + f"# {SECRET}\n").encode())
    assert toml["valid_toml"] is True and toml["sections"] == ["pr_reviewer", "review_agent"]
    assert files["REVIEW.md"]["present"] is True and files["REVIEW.md"]["kind"] == "markdown"
    assert files["best_practices.md"] == {
        "path": "best_practices.md",
        "kind": "markdown",
        "present": False,
    }
    assert repo["validation"]["valid"] is True
    assert {c["id"] for c in repo["validation"]["checks"]} >= {"config_present"}


def test_validation_is_the_existing_validation(tmp_path):
    checkout(tmp_path, files={".pr_agent.toml": "[pr_reviewer\nbroken"})
    [repo] = cs.status(REPO, settings=make_settings(tmp_path))["repos"]
    expected = api._validation_checks(
        {
            "pr_agent": {
                "present": True,
                "path": ".pr_agent.toml",
                "valid_toml": False,
                "error": repo["files"][0]["error"],
            },
            "best_practices": {"present": False, "path": "best_practices.md"},
        }
    )
    assert repo["validation"] == {"valid": False, "checks": expected}
    assert repo["files"][0]["valid_toml"] is False


def test_legacy_files_and_settings_are_warnings(tmp_path):
    checkout(
        tmp_path,
        files={
            ".pr_agent.toml": '[pr_reviewer]\nextra_instructions = "x"\n',
            "best_practices.md": "# bp\n",
        },
    )
    [repo] = cs.status(REPO, settings=make_settings(tmp_path))["repos"]
    codes = {(w["code"], w["path"]) for w in repo["warnings"]}
    assert codes == {
        ("legacy_setting", ".pr_agent.toml"),
        ("legacy_file", "best_practices.md"),
    }
    assert all("unverified" in w["detail"] for w in repo["warnings"])


def test_a_symlinked_config_file_is_not_followed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text(f"[x]\nsecret = '{SECRET}'\n", encoding="utf-8")
    roots = tmp_path / "roots"
    path = checkout(roots)
    (path / ".pr_agent.toml").symlink_to(outside)
    [repo] = cs.status(REPO, settings=make_settings(roots))["repos"]
    assert SECRET not in json.dumps(repo)
    toml = repo["files"][0]
    assert toml["present"] is True and toml["problem"]["code"] == "not_regular_file"
    assert "bytes" not in toml
    assert repo["validation"]["valid"] is False


def test_a_symlinked_checkout_is_not_followed(tmp_path):
    real = checkout(tmp_path / "elsewhere", files={".pr_agent.toml": GOOD_TOML})
    roots = tmp_path / "roots"
    (roots / "acme").mkdir(parents=True)
    (roots / "acme" / "widgets").symlink_to(real)
    [repo] = cs.status(REPO, settings=make_settings(roots))["repos"]
    assert repo["checkout"] == "missing" and repo["files"] == []


def test_an_oversized_file_is_reported_and_not_read(tmp_path, monkeypatch):
    path = checkout(tmp_path)
    (path / "REVIEW.md").write_bytes(b"x" * 5000)
    opened = []
    real_open = os.open
    monkeypatch.setattr(
        os, "open", lambda p, *a, **k: opened.append(str(p)) or real_open(p, *a, **k)
    )
    [repo] = cs.status(REPO, settings=make_settings(tmp_path, max_file_bytes=4096))["repos"]
    review = next(f for f in repo["files"] if f["path"] == "REVIEW.md")
    assert review["bytes"] == 5000 and review["problem"]["code"] == "file_too_large"
    assert not any(p.endswith("REVIEW.md") for p in opened)


def test_binary_and_non_utf8_files_are_problems(tmp_path):
    path = checkout(tmp_path)
    (path / "REVIEW.md").write_bytes(b"a\x00b")
    (path / "best_practices.md").write_bytes(b"\xff\xfe")
    [repo] = cs.status(REPO, settings=make_settings(tmp_path))["repos"]
    problems = {f["path"]: f.get("problem", {}).get("code") for f in repo["files"]}
    assert problems["REVIEW.md"] == "binary" and problems["best_practices.md"] == "not_utf8"


def test_a_missing_checkout_says_so(tmp_path):
    [repo] = cs.status(REPO, settings=make_settings(tmp_path))["repos"]
    assert repo == {
        "repo": REPO,
        "checkout": "missing",
        "files": [],
        "validation": None,
        "warnings": [],
        "proposals": [],
    }


# --- every repo, not configured, sizes ------------------------------------------------------


def test_no_repo_means_every_allowlisted_repo(tmp_path):
    checkout(tmp_path, REPO, {".pr_agent.toml": GOOD_TOML})
    out = cs.status(None, settings=make_settings(tmp_path, repos=(REPO, OTHER)))
    assert out["configured"] is True and out["truncated"] is False
    assert [r["repo"] for r in out["repos"]] == [REPO, OTHER]
    assert [r["checkout"] for r in out["repos"]] == ["found", "missing"]


def test_an_empty_allowlist_is_not_configured(tmp_path):
    out = cs.status(None, settings=make_settings(tmp_path, repos=()))
    assert out == {"configured": False, "repos": [], "truncated": False}


def test_many_repos_stay_under_64_kib(tmp_path):
    repos = tuple(f"acme/repo-{i:02d}-{'x' * 80}" for i in range(40))
    for repo in repos:
        checkout(
            tmp_path,
            repo,
            {
                ".pr_agent.toml": '[pr_reviewer]\nextra_instructions = "x"\n' + "[s]\n" * 50,
                "best_practices.md": "# bp\n",
                "REVIEW.md": "# r\n",
            },
        )
    for _ in range(30):
        changelog.record_config_proposal(repos[0], [".pr_agent.toml", "REVIEW.md"], actor="api")
    out = cs.status(None, settings=make_settings(tmp_path, repos=repos))
    assert len(json.dumps(out).encode()) < 64 * 1024
    assert out["truncated"] is True and len(out["repos"]) == cs.MAX_REPOS


# --- proposals from the change log ----------------------------------------------------------


def test_proposals_are_the_latest_change_log_entries_for_the_repo(tmp_path):
    checkout(tmp_path, files={".pr_agent.toml": GOOD_TOML})
    for i in range(5):
        changelog.record_config_proposal(REPO, [f"f{i}.toml"], actor="api")
    changelog.record_config_proposal(OTHER, ["other.toml"], actor="api")
    changelog.record_finding_status(["id"], "dismissed", reason="deferred", actor="api")
    [repo] = cs.status(REPO, settings=make_settings(tmp_path))["repos"]
    assert [p["files"] for p in repo["proposals"]] == [["f4.toml"], ["f3.toml"], ["f2.toml"]]
    assert set(repo["proposals"][0]) == {"time", "files", "actor"}


# --- the surfaces -------------------------------------------------------------------------


def test_the_cli_command(tmp_path, monkeypatch, capsys):
    checkout(tmp_path, files={".pr_agent.toml": GOOD_TOML})
    monkeypatch.setenv("QODO_PROPOSE_REPOS", REPO)
    monkeypatch.setenv("QODO_PROPOSE_CHECKOUT_ROOTS", str(tmp_path))
    monkeypatch.setenv("QODO_PROPOSE_CONFIG", str(tmp_path / "none.json"))
    assert main(["config", "status", "--repo", REPO, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repos"][0]["files"][0]["valid_toml"] is True
    assert main(["config", "status", "--repo", OTHER, "--json"]) == 1
    assert "allowlist" in json.loads(capsys.readouterr().err)["message"]


def test_a_checkout_swapped_for_a_link_after_the_checks_is_not_read(tmp_path):
    """The earlier checks passed on a real directory; by the time the file is opened the
    directory is a link to somewhere else. Nothing behind the link is read."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".pr_agent.toml").write_text(f'x = "{SECRET}"\n', encoding="utf-8")
    swapped = tmp_path / "swapped"
    swapped.symlink_to(elsewhere, target_is_directory=True)
    entry, text = cs._read(str(swapped), ".pr_agent.toml", 10_000)
    assert text is None and entry["problem"]["code"] == "not_regular_file"

    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    (real / "sub").rmdir()
    (real / "sub").symlink_to(elsewhere, target_is_directory=True)  # a parent directory swapped
    entry, text = cs._read(str(real), "sub/.pr_agent.toml", 10_000)
    assert text is None and entry["problem"]["code"] == "not_regular_file"
    assert SECRET not in json.dumps(entry)
