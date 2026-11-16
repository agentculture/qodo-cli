"""An append-only log of changes made through qodo-cli, for a UI to read.

One JSON object per line (JSON Lines), standard library only. Each line holds the UTC
``time``, the event ``kind``, the ``actor`` when known, and the identifying fields of the
event (finding ids, new status, dismiss reason; for a configuration proposal the repo and
the file *paths*). Never the API key, never finding text, never file contents.

* **Where:** ``$QODO_CHANGE_LOG`` if set, else ``$XDG_STATE_HOME/qodo-cli/changes.jsonl``,
  else ``~/.local/state/qodo-cli/changes.jsonl``: outside any git checkout. The directory is
  mode 0700 and the file 0600.
* **Appending:** one ``write`` of one line on an ``O_APPEND`` descriptor, so concurrent
  appenders do not interleave. If the file ends in a torn line (no newline), the next line
  starts on a fresh one.
* **Bound:** when the file reaches ``MAX_BYTES`` the next append first renames it to
  ``<name>.1`` (replacing the previous one), so at most about twice ``MAX_BYTES`` is kept.
  Two processes rotating at the same instant may drop a few lines; that is accepted.
* **Reading:** :func:`read` returns the newest entries, oldest first, across the rotation,
  and skips lines that are torn or not a JSON object.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_BYTES = 1_000_000
ENV_PATH = "QODO_CHANGE_LOG"

KIND_FINDING_STATUS = "finding.status_changed"
KIND_CONFIG_PROPOSED = "config.proposed"


def log_path() -> Path:
    override = os.environ.get(ENV_PATH)
    if override:
        return Path(override)
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "qodo-cli" / "changes.jsonl"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def append(kind: str, *, actor: str | None = None, **fields: Any) -> dict[str, Any]:
    """Append one line and return the entry. Raises ``OSError`` if it cannot be written."""
    entry: dict[str, Any] = {"time": _now(), "kind": kind, "actor": actor}
    entry.update(fields)
    entry = {k: v for k, v in entry.items() if v is not None}
    data = json.dumps(entry, separators=(",", ":")).encode("utf-8") + b"\n"

    path = log_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one appender rotates or writes at a time
        try:
            if path.stat().st_size >= MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                data = b"\n" + data
            written = os.write(fd, data)
            if written != len(data):
                raise OSError(errno.EIO, "short write to the change log")
        finally:
            os.close(fd)
    finally:
        os.close(lock)  # closing releases the lock
    return entry


def try_append(kind: str, *, actor: str | None = None, **fields: Any) -> bool:
    """Append, but never raise: a log failure warns on stderr and returns ``False``."""
    try:
        append(kind, actor=actor, **fields)
    except OSError as err:
        print(
            f"warning: change log not written ({err.strerror or err}); the change itself "
            f"succeeded. Check {ENV_PATH} or the state directory permissions.",
            file=sys.stderr,
        )
        return False
    return True


def _entries(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def read(limit: int | None = None) -> list[dict[str, Any]]:
    """The newest ``limit`` entries (all when ``None``), oldest first."""
    if limit is not None and limit <= 0:
        return []
    path = log_path()
    entries = _entries(path.with_name(path.name + ".1")) + _entries(path)
    return entries if limit is None else entries[-limit:]


def record_finding_status(
    ids: list[str], status: str, *, reason: str | None = None, actor: str | None = None
) -> bool:
    """Log finding status changes, one line per finding (id, new status, dismiss reason).

    Never raises; ``False`` when any line could not be written."""
    results = [
        try_append(KIND_FINDING_STATUS, actor=actor, id=one, status=status, reason=reason)
        for one in ids
    ]
    return all(results)


def record_config_proposal(repo: str, files: list[str], *, actor: str | None = None) -> bool:
    """Log a proposed configuration change: the repo and the file paths (not contents).

    For the ``config propose`` verb to call after it has proposed. Never raises.
    """
    return try_append(KIND_CONFIG_PROPOSED, actor=actor, repo=repo, files=list(files))
