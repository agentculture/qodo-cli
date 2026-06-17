"""Tests for ``qodo config`` — the repo-level Qodo reviewer config verb (#7)."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

from qodo.cli import main

_VALID_TOML = '[pr_reviewer]\nextra_instructions = "be careful"\n'
_BP = "# best practices\n\nsome content\n"


def _mk_repo(
    tmp_path: Path, *, pr_agent: str | None = None, best_practices: str | None = None
) -> Path:
    (tmp_path / ".git").mkdir()
    if pr_agent is not None:
        (tmp_path / ".pr_agent.toml").write_text(pr_agent, encoding="utf-8")
    if best_practices is not None:
        (tmp_path / "best_practices.md").write_text(best_practices, encoding="utf-8")
    return tmp_path


# --- show ------------------------------------------------------------------


def test_config_show_present_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mk_repo(tmp_path, pr_agent=_VALID_TOML, best_practices=_BP)
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "show", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pr_agent"]["present"] is True
    assert out["pr_agent"]["valid_toml"] is True
    assert "pr_reviewer" in out["pr_agent"]["sections"]
    assert out["best_practices"]["present"] is True


def test_config_show_absent_hints_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mk_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "absent" in out
    assert "qodo config init" in out


# --- validate --------------------------------------------------------------


def test_config_validate_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mk_repo(tmp_path, pr_agent=_VALID_TOML, best_practices=_BP)
    monkeypatch.chdir(tmp_path)
    assert main(["config", "validate"]) == 0


def test_config_validate_invalid_toml_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mk_repo(tmp_path, pr_agent="[pr_reviewer\nbroken = ")
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "validate"])
    assert rc == 1
    assert "not valid TOML" in capsys.readouterr().out


def test_config_validate_no_config_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mk_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["config", "validate"]) == 1


def test_config_validate_warns_without_pr_reviewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mk_repo(tmp_path, pr_agent="[config]\nfoo = 1\n", best_practices=_BP)
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "validate", "--json"])
    assert rc == 0  # valid TOML + a config present -> error-checks pass
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is True
    by_id = {c["id"]: c for c in out["checks"]}
    assert by_id["pr_reviewer_section"]["ok"] is False
    assert by_id["pr_reviewer_section"]["severity"] == "warning"


# --- read-only verbs degrade on an unreadable best_practices.md ------------


def test_config_show_unreadable_best_practices_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A present-but-bad-encoding best_practices.md must not crash `show` (exit 1);
    # the read-only verb reports it as UNREADABLE and still exits 0.
    _mk_repo(tmp_path, pr_agent=_VALID_TOML)
    (tmp_path / "best_practices.md").write_bytes(b"\xff\xfe not valid utf-8 \x80")
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "show"])
    assert rc == 0
    assert "UNREADABLE" in capsys.readouterr().out


def test_config_show_unreadable_best_practices_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mk_repo(tmp_path, pr_agent=_VALID_TOML)
    (tmp_path / "best_practices.md").write_bytes(b"\xff\xfe\x80")
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "show", "--json"])
    assert rc == 0
    bp = json.loads(capsys.readouterr().out)["best_practices"]
    assert bp["present"] is True
    assert bp["readable"] is False
    assert "error" in bp


def test_config_validate_unreadable_best_practices_no_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # validate must return a controlled result, not raise, on an unreadable file.
    _mk_repo(tmp_path, pr_agent=_VALID_TOML)
    (tmp_path / "best_practices.md").write_bytes(b"\xff\xfe\x80")
    monkeypatch.chdir(tmp_path)
    # pr_agent present + valid -> error-severity checks pass -> exit 0 (the
    # unreadable best_practices only trips the warning-severity check).
    assert main(["config", "validate"]) == 0


# --- init ------------------------------------------------------------------


def test_config_init_scaffolds_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mk_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "init", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["written"]) == {".pr_agent.toml", "best_practices.md"}
    # the scaffolded TOML is valid and carries [pr_reviewer]
    data = tomllib.loads((tmp_path / ".pr_agent.toml").read_text(encoding="utf-8"))
    assert "pr_reviewer" in data
    # best_practices is personalised with the repo dir name
    assert tmp_path.name in (tmp_path / "best_practices.md").read_text(encoding="utf-8")


def test_config_init_skips_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mk_repo(tmp_path, pr_agent="[pr_reviewer]\n")
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "init", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert ".pr_agent.toml" in out["skipped"]
    assert "best_practices.md" in out["written"]
    assert (tmp_path / ".pr_agent.toml").read_text(encoding="utf-8") == "[pr_reviewer]\n"


def test_config_init_force_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mk_repo(tmp_path, pr_agent="old")
    monkeypatch.chdir(tmp_path)
    assert main(["config", "init", "--force"]) == 0
    assert (tmp_path / ".pr_agent.toml").read_text(encoding="utf-8") != "old"


def test_config_init_skips_symlink_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A symlink at a target path must be treated as "occupied" (skipped), not
    # followed and written through.
    _mk_repo(tmp_path)
    outside = tmp_path / "outside.toml"
    outside.write_text("untouched", encoding="utf-8")
    os.symlink(outside, tmp_path / ".pr_agent.toml")
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "init", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert ".pr_agent.toml" in out["skipped"]
    assert outside.read_text(encoding="utf-8") == "untouched"  # not written through


def test_config_init_force_refuses_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # --force must refuse to write *through* a symlink (could escape the repo)
    # and must not half-scaffold the other target before refusing.
    _mk_repo(tmp_path)
    outside = tmp_path / "outside.toml"
    outside.write_text("untouched", encoding="utf-8")
    os.symlink(outside, tmp_path / ".pr_agent.toml")
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "init", "--force"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err and "symlink" in err
    assert outside.read_text(encoding="utf-8") == "untouched"  # symlink not followed
    # pre-scan aborts before writing anything — no half-scaffold
    assert not (tmp_path / "best_practices.md").exists()


def test_config_init_force_refuses_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A directory where a config file is expected must be refused cleanly, not
    # crash with a generic traceback/IsADirectoryError.
    _mk_repo(tmp_path)
    (tmp_path / ".pr_agent.toml").mkdir()
    monkeypatch.chdir(tmp_path)
    rc = main(["config", "init", "--force"])
    assert rc == 1
    assert "non-regular path" in capsys.readouterr().err


# --- overview / no-verb ----------------------------------------------------


def test_config_overview(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "overview"]) == 0
    assert "reviewer config" in capsys.readouterr().out


def test_config_no_verb_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config"]) == 0
    assert "config show" in capsys.readouterr().out


def test_config_overview_ignores_bad_target(capsys: pytest.CaptureFixture[str]) -> None:
    # descriptive verb must not hard-fail on a stray positional (rubric)
    assert main(["config", "overview", "bogus/path"]) == 0
