"""The append-only change log and its wiring into ``qodo.api``."""

from __future__ import annotations

import json
import os
import stat
import threading
from datetime import datetime
from pathlib import Path

import pytest

from qodo import api, changelog
from qodo.cli._errors import CliError


@pytest.fixture
def log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "state" / "changes.jsonl"
    monkeypatch.setenv("QODO_CHANGE_LOG", str(p))
    return p


class _Up:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list = []

    def call(self, tool, args):
        self.calls.append((tool, args))
        if self.fail:
            raise CliError(code=2, message="upstream down", remediation="retry")
        return {"ok": True}


def test_default_path_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("QODO_CHANGE_LOG", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xs"))
    assert changelog.log_path() == tmp_path / "xs" / "qodo-cli" / "changes.jsonl"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert changelog.log_path() == tmp_path / ".local/state/qodo-cli/changes.jsonl"


def test_append_writes_one_json_line_mode_0600(log):
    changelog.append("x.y", actor="me", ids=["1"])
    changelog.append("x.z")
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "x.y" and first["actor"] == "me" and first["ids"] == ["1"]
    datetime.fromisoformat(first["time"].replace("Z", "+00:00"))
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(log.parent).st_mode) == 0o700


def test_read_returns_newest_n_in_order(log):
    for i in range(5):
        changelog.append("k", n=i)
    got = changelog.read(limit=3)
    assert [e["n"] for e in got] == [2, 3, 4]
    assert len(changelog.read()) == 5
    assert changelog.read(limit=0) == []


def test_read_missing_file_is_empty(log):
    assert changelog.read() == []


def test_read_tolerates_torn_and_corrupt_lines(log):
    changelog.append("k", n=1)
    with open(log, "a") as fh:
        fh.write("not json\n")
        fh.write('{"kind": "k", "n": 2}\n')
        fh.write('{"kind": "k", "n": 3, "tor')  # torn last line, no newline
    assert [e["n"] for e in changelog.read()] == [1, 2]
    changelog.append("k", n=4)  # appending after a torn line still yields a valid entry
    assert changelog.read()[-1]["n"] == 4


def test_concurrent_appenders_lose_nothing(log):
    def work(t):
        for i in range(25):
            changelog.append("k", t=t, i=i)

    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(changelog.read(limit=1000)) == 200


def test_size_bound_rotates_once(log, monkeypatch):
    monkeypatch.setattr(changelog, "MAX_BYTES", 400)
    for i in range(30):
        changelog.append("k", n=i, pad="x" * 20)
    assert log.stat().st_size <= 400 + 200
    old = log.with_name(log.name + ".1")
    assert old.exists()
    assert not log.with_name(log.name + ".2").exists()
    newest = changelog.read(limit=3)
    assert [e["n"] for e in newest] == [27, 28, 29]  # reader spans the rotation
    assert stat.S_IMODE(os.stat(old).st_mode) == 0o600


def test_entries_never_carry_unknown_free_text_or_key(log, monkeypatch):
    monkeypatch.setenv("QODO_API_KEY", "sekret-key")
    monkeypatch.setattr(api, "_gateway", lambda: _Up())
    api.findings_dismiss(["f1"], "intentional", actor="agent-7")
    assert "sekret-key" not in log.read_text()


def test_implement_appends_after_success(log, monkeypatch):
    up = _Up()
    monkeypatch.setattr(api, "_gateway", lambda: up)
    api.findings_implement(["a", "b"], actor="me")
    first, second = changelog.read()  # one line per finding change
    assert [first["id"], second["id"]] == ["a", "b"]
    for e in (first, second):
        assert e["kind"] == "finding.status_changed"
        assert e["status"] == "implemented" and e["actor"] == "me"
        assert "reason" not in e and "ids" not in e


def test_dismiss_appends_status_and_reason(log, monkeypatch):
    monkeypatch.setattr(api, "_gateway", lambda: _Up())
    api.findings_dismiss(["a"], "deferred")
    (e,) = changelog.read()
    assert e["status"] == "dismissed" and e["reason"] == "deferred" and e["id"] == "a"


def test_failed_call_appends_nothing(log, monkeypatch):
    monkeypatch.setattr(api, "_gateway", lambda: _Up(fail=True))
    with pytest.raises(CliError):
        api.findings_implement(["a"])
    with pytest.raises(CliError):
        api.findings_dismiss(["a"], "deferred")
    with pytest.raises(CliError):
        api.findings_dismiss(["a"], "bogus")
    assert changelog.read() == []


def test_log_write_failure_warns_but_succeeds(log, monkeypatch, capsys):
    monkeypatch.setattr(api, "_gateway", lambda: _Up())
    blocker = log.parent
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("a file where the directory should be")
    out = api.findings_implement(["a"])
    assert out == {"ok": True}
    err = capsys.readouterr().err
    assert "change log" in err and "not written" in err


def test_record_config_proposal_line(log):
    changelog.record_config_proposal(
        repo="org/repo", files=[".pr_agent.toml", "best_practices.md"], actor="me"
    )
    (e,) = changelog.read()
    assert e["kind"] == "config.proposed"
    assert e["repo"] == "org/repo"
    assert e["files"] == [".pr_agent.toml", "best_practices.md"]
    assert e["actor"] == "me"
    assert set(e) == {"time", "kind", "actor", "repo", "files"}
