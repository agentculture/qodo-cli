"""``config status``: what Qodo reads in each allowlisted repo, whether it validates, and the
configuration changes the bridge proposed for it (the web UI's Configuration screen).

The repos are the bridge's own: the repo allowlist of ``config propose``
(:func:`qodo.propose.load_settings`), each read from its configured checkout,
``<root>/<owner>/<name>``. Every input is treated as hostile, like ``config propose``:

1. **the allowlist first**: a repo that is not in it is refused before the filesystem is touched;
   the caller names a repo, never a path;
2. **the checkout**: the first ``<root>/<owner>/<name>`` that is a real directory (no symlink
   anywhere in it) holding a ``.git`` directory; anything else is ``missing``;
3. **the files**: only the paths of the path allowlist, each read only when no part of its path
   is a symlink and it is a regular file within ``max_file_bytes`` (opened with ``O_NOFOLLOW``);
4. **the reply**: file names, kinds, sizes, TOML validity and section names, the existing
   validation (:func:`qodo.api._validation_checks`), warnings, and the newest ``config.proposed``
   change-log entries for the repo. **Never a file's contents** and never a path on the bridge's
   machine: a viewer may call it (``config propose``, which returns contents, needs editor).

Reads the working tree (what a person sees in the checkout), never git: nothing here runs a
subprocess. Standard library only.
"""

from __future__ import annotations

import json
import os
import stat
import tomllib
from typing import Any

from qodo import changelog
from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from qodo.propose import LEGACY_DETAIL, ProposeSettings, _lookup, _warning, load_settings

__all__ = ["MAX_REPOS", "MAX_PROPOSALS", "status"]

#: At most this many repos in one reply (``truncated`` says when more are allowlisted).
MAX_REPOS = 20
#: The newest proposals kept per repo.
MAX_PROPOSALS = 3
_MAX_SECTIONS = 20
_MAX_NAME = 100
_MAX_DETAIL = 200
_MAX_FILES_LISTED = 20
_MAX_PATH = 256

_PR_AGENT = ".pr_agent.toml"
_BEST_PRACTICES = "best_practices.md"


def _refuse_repo() -> CliError:
    return CliError(
        code=EXIT_USER_ERROR,
        message="the repo is not in the bridge's repo allowlist",
        remediation="name an allowlisted repo (run `qodo-cli config status` for the list)",
    )


def _checkout(repo: str, settings: ProposeSettings) -> str | None:
    """The repo's checkout: the first ``<root>/<owner>/<name>`` that is a real directory with a
    ``.git`` directory, reached through no symlink."""
    segments = repo.split("/")
    for root in settings.checkout_roots:
        top = os.path.realpath(root)
        candidate = os.path.join(top, *segments)
        try:
            if os.path.realpath(candidate) != candidate:
                continue  # a symlink on the way: the repo's place is not where it points
            here, dot_git = os.lstat(candidate), os.lstat(os.path.join(candidate, ".git"))
        except OSError:
            continue
        if stat.S_ISDIR(here.st_mode) and stat.S_ISDIR(dot_git.st_mode):
            return candidate
    return None


def _problem(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _read(checkout: str, path: str, cap: int) -> tuple[dict[str, Any], str | None]:
    """``(the file's entry, its text or None)``; the text never leaves this module."""
    full = os.path.join(checkout, *path.split("/"))
    try:
        info = os.lstat(full)
    except FileNotFoundError:
        return {"present": False}, None
    except OSError as err:
        return {"present": True, "problem": _problem("unreadable", err.strerror or "")}, None
    if os.path.realpath(full) != full or not stat.S_ISREG(info.st_mode):
        return {
            "present": True,
            "problem": _problem("not_regular_file", "a symlink or other non-regular entry"),
        }, None
    entry: dict[str, Any] = {"present": True, "bytes": info.st_size}
    if info.st_size > cap:
        return {**entry, "problem": _problem("file_too_large", f"over {cap} bytes")}, None
    try:
        fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("not a regular file")
            data = os.read(fd, cap + 1)
        finally:
            os.close(fd)
    except OSError as err:
        return {**entry, "problem": _problem("unreadable", err.strerror or str(err))}, None
    if len(data) > cap:
        return {**entry, "problem": _problem("file_too_large", f"over {cap} bytes")}, None
    if b"\x00" in data:
        return {**entry, "problem": _problem("binary", "a binary file (it holds a NUL byte)")}, None
    try:
        return entry, data.decode("utf-8")
    except UnicodeDecodeError:
        return {**entry, "problem": _problem("not_utf8", "not UTF-8 text")}, None


def _file(checkout: str, path: str, kind: str, s: ProposeSettings, warnings: list) -> tuple:
    """``(entry for the reply, the validation's view of the file)``."""
    entry, text = _read(checkout, path, s.max_file_bytes)
    entry = {"path": path, "kind": kind, **entry}
    view: dict[str, Any] = {"present": entry["present"], "path": path}
    if entry["present"] and path in s.legacy_files:
        warnings.append(_warning("legacy_file", path, f"{path} is {LEGACY_DETAIL}"))
    if text is None:
        if entry["present"]:
            view.update(valid_toml=False, error=entry["problem"]["detail"], non_empty=False)
        return entry, view
    if kind == "toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as err:
            entry.update(valid_toml=False, error=str(err)[:_MAX_DETAIL])
            view.update(valid_toml=False, error=entry["error"])
            return entry, view
        sections = sorted(k for k, v in data.items() if isinstance(v, dict))
        entry.update(valid_toml=True, sections=[k[:_MAX_NAME] for k in sections][:_MAX_SECTIONS])
        view.update(valid_toml=True, sections=sections, has_pr_reviewer="pr_reviewer" in data)
        for key in s.legacy_settings:
            if _lookup(data, tuple(key.split("."))) is not None:
                warnings.append(_warning("legacy_setting", path, f"[{key}] is {LEGACY_DETAIL}"))
    else:
        view.update(bytes=entry["bytes"], non_empty=bool(text.strip()))
        if not text.strip():
            warnings.append(_warning("empty_file", path, f"{path} is empty"))
    return entry, view


def _proposals(entries: list[dict[str, Any]], repo: str) -> list[dict[str, Any]]:
    """The newest ``config.proposed`` entries for ``repo``, newest first: time, files, actor."""
    out = []
    for entry in reversed(entries):
        if entry.get("kind") != changelog.KIND_CONFIG_PROPOSED or entry.get("repo") != repo:
            continue
        files = entry.get("files") if isinstance(entry.get("files"), list) else []
        out.append(
            {
                "time": str(entry.get("time", ""))[:40],
                "files": [f for f in files if isinstance(f, str) and len(f) <= _MAX_PATH][
                    :_MAX_FILES_LISTED
                ],
                "actor": str(entry.get("actor", ""))[:40],
            }
        )
        if len(out) == MAX_PROPOSALS:
            break
    return out


def _repo_status(repo: str, s: ProposeSettings, log: list[dict[str, Any]]) -> dict[str, Any]:
    from qodo.api import _validation_checks  # the existing validation, reused as is

    out: dict[str, Any] = {
        "repo": repo,
        "checkout": "missing",
        "files": [],
        "validation": None,
        "warnings": [],
        "proposals": _proposals(log, repo),
    }
    checkout = _checkout(repo, s)
    if checkout is None:
        return out
    out["checkout"] = "found"
    views: dict[str, dict[str, Any]] = {}
    for path, kind in s.paths.items():
        entry, views[path] = _file(checkout, path, kind, s, out["warnings"])
        out["files"].append(entry)
    info = {
        "pr_agent": views.get(_PR_AGENT, {"present": False, "path": _PR_AGENT}),
        "best_practices": views.get(_BEST_PRACTICES, {"present": False, "path": _BEST_PRACTICES}),
    }
    checks = _validation_checks(info)
    out["validation"] = {
        "valid": all(c["ok"] for c in checks if c["severity"] == "error"),
        "checks": checks,
    }
    return out


def status(repo: str | None = None, *, settings: ProposeSettings | None = None) -> dict[str, Any]:
    """``{configured, truncated, repos: [{repo, checkout, files, validation, warnings,
    proposals}]}`` for ``repo``, or for every allowlisted repo when it is ``None``."""
    s = settings if settings is not None else load_settings()
    if repo is not None:
        if not isinstance(repo, str) or repo not in s.repos:
            raise _refuse_repo()  # before the filesystem is touched at all
        names = [repo]
    else:
        names = list(s.repos)
    if not names:
        return {"configured": False, "repos": [], "truncated": False}
    log = changelog.read()
    out = {
        "configured": True,
        "repos": [_repo_status(name, s, log) for name in names[:MAX_REPOS]],
        "truncated": len(names) > MAX_REPOS,
    }
    if len(json.dumps(out).encode("utf-8")) > s.max_reply_bytes:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"the status of {len(out['repos'])} repos is over {s.max_reply_bytes} bytes",
            remediation="ask for one repo at a time (repo)",
        )
    return out
