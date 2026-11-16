"""``config propose``: is the change a checkout's HEAD makes against a base ref a safe Qodo
configuration change? If so, return it; if not, say why, with no file contents.

The verb is the gate between an improvement agent's local commits and a pull request someone
else opens (culture-rules, through its own GitHub App). It **reads** a checkout and returns
data; nothing here pushes, merges, commits or opens a pull request. Every input is treated as
hostile: the repo name, the checkout path, the base ref, and the checkout's own contents (file
names, contents, symlinks, ``.git/config``).

Order of checks:

1. **repo allowlist** -- before the filesystem or git is touched at all;
2. **checkout roots** -- the checkout's real path must be ``<root>/<owner>/<name>`` for a
   configured root, which is what binds the repo name to its checkout;
3. **base ref** -- a strict pattern, resolved with ``--end-of-options``, and an ancestor of HEAD;
4. **the change set** -- ``git diff-tree -r -z --raw -M`` between the base and HEAD commits,
   so renames, deletions, mode and type changes are explicit; every path must be in the
   path allowlist exactly, every file a regular, non-executable blob at HEAD;
5. **contents** -- read from the git objects at HEAD (never the working tree), each a UTF-8
   text within the size cap, ``.pr_agent.toml`` valid TOML with no change to a refused setting.

Uncommitted work is **ignored**: only committed objects are read, and the working tree is never
consulted (``git status`` would run the checkout's filter drivers and fsmonitor hook).

Configuration (none of it is a constant in the logic): the packaged ``propose_default.json``
(the path allowlist, the refused and legacy settings, the caps; **no** repos, **no** roots),
overlaid key by key by a JSON file at ``$QODO_PROPOSE_CONFIG`` or ``~/.qodo/propose.json``, then
by ``QODO_PROPOSE_REPOS`` (comma or space separated) and ``QODO_PROPOSE_CHECKOUT_ROOTS``
(``os.pathsep`` separated). An empty repo or root allowlist refuses everything.

A refusal is a normal result, ``{ok: false, reason, detail, paths}``; a broken environment (git
missing, a timeout, a checkout that is not a plain git repository) is a :class:`CliError`.
Standard library only.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import stat
import subprocess  # nosec B404 - argv lists, resolved absolute git, no shell
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qodo.cli._errors import EXIT_ENV_ERROR, CliError

__all__ = [
    "ProposeSettings",
    "RefusedSetting",
    "REASONS",
    "load_settings",
    "parse_refused",
    "path_allowed",
    "propose",
]

DEFAULTS_PATH = Path(__file__).with_name("propose_default.json")
ENV_CONFIG = "QODO_PROPOSE_CONFIG"
ENV_REPOS = "QODO_PROPOSE_REPOS"
ENV_ROOTS = "QODO_PROPOSE_CHECKOUT_ROOTS"
KINDS = ("toml", "markdown")
LEGACY_DETAIL = "not in Qodo's current configuration reference (whether Qodo still reads it is "
LEGACY_DETAIL += "unverified)"

#: Every refusal reason, in the order the checks run.
REASONS = (
    "repo_not_allowed",
    "checkout_not_allowed",
    "invalid_base",
    "unknown_base",
    "base_not_ancestor",
    "no_changes",
    "too_many_files",
    "rename",
    "path_not_allowed",
    "deletion",
    "type_change",
    "not_regular_file",
    "executable",
    "file_too_large",
    "binary",
    "not_utf8",
    "invalid_toml",
    "refused_setting",
    "too_large",
)
MAX_PATHS = 20
MAX_PATH_CHARS = 256

_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}$")
_REF_CHARS = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_KEY = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*(\.\*)?$")
_REGULAR, _EXECUTABLE = "100644", "100755"


# --- configuration -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RefusedSetting:
    """A TOML key (``section.key``) or a whole table (``section.*``) a proposal may not change,
    except to one of the ``allow`` values."""

    key: str
    allow: tuple[Any, ...] = ()

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.key.removesuffix(".*").split("."))


@dataclass(frozen=True)
class ProposeSettings:
    repos: tuple[str, ...] = ()
    checkout_roots: tuple[str, ...] = ()
    paths: Mapping[str, str] = field(default_factory=dict)
    legacy_files: tuple[str, ...] = ()
    legacy_settings: tuple[str, ...] = ()
    refused_settings: tuple[RefusedSetting, ...] = ()
    max_files: int = 10
    max_file_bytes: int = 32768
    max_reply_bytes: int = 61440
    git_timeout_seconds: int = 20

    def replace(self, **changes: Any) -> ProposeSettings:
        return dataclasses.replace(self, **changes)


_INT_KEYS = ("max_files", "max_file_bytes", "max_reply_bytes", "git_timeout_seconds")
_KNOWN = {
    "version",
    "repos",
    "checkout_roots",
    "paths",
    "legacy_files",
    "legacy_settings",
    "refused_settings",
    *_INT_KEYS,
}


def _config_error(where: Any, message: str) -> CliError:
    return CliError(
        code=EXIT_ENV_ERROR,
        message=f"invalid config propose settings ({where}): {message}",
        remediation=f"fix the settings file (see qodo/propose_default.json), {ENV_REPOS} or "
        f"{ENV_ROOTS}",
    )


def _str_list(where: Any, key: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _config_error(where, f"{key} must be a list of strings")
    return tuple(value)


def _repos(where: Any, value: tuple[str, ...]) -> tuple[str, ...]:
    for repo in value:
        if not _REPO.match(repo) or ".." in repo:
            raise _config_error(where, f"repo {repo!r} is not owner/name")
    return value


def _roots(where: Any, value: tuple[str, ...]) -> tuple[str, ...]:
    for root in value:
        if not os.path.isabs(root):
            raise _config_error(where, f"checkout root {root!r} is not an absolute path")
    return value


def parse_refused(raw: Any, where: Any = "refused_settings") -> tuple[RefusedSetting, ...]:
    """``[{"key": "section.key" | "section.*", "allow": [values]}]`` to settings."""
    if not isinstance(raw, (list, tuple)):
        raise _config_error(where, "refused_settings must be a list")
    out = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"key", "allow"}:
            raise _config_error(where, "each refused setting is {key, allow?}")
        key, allow = item.get("key"), item.get("allow", [])
        if not isinstance(key, str) or not _KEY.match(key):
            raise _config_error(where, f"refused setting key {key!r} is not section.key")
        if not isinstance(allow, list):
            raise _config_error(where, f"{key}: allow must be a list")
        out.append(RefusedSetting(key, tuple(allow)))
    return tuple(out)


def _parse(data: dict[str, Any], where: Any) -> ProposeSettings:
    unknown = set(data) - _KNOWN
    if unknown:
        raise _config_error(where, f"unknown key(s) {sorted(unknown)}")
    if data.get("version", 1) != 1:
        raise _config_error(where, "version must be 1")
    paths = data.get("paths", {})
    if not isinstance(paths, dict):
        raise _config_error(where, "paths must be an object of path -> kind")
    for path, kind in paths.items():
        if not _normal(path):
            raise _config_error(where, f"path {path!r} is not a normal repo-relative path")
        if kind not in KINDS:
            raise _config_error(where, f"{path}: kind must be one of {', '.join(KINDS)}")
    ints = {}
    for key in _INT_KEYS:
        value = data.get(key, getattr(ProposeSettings, key))
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise _config_error(where, f"{key} must be a positive integer")
        ints[key] = value
    legacy = _str_list(where, "legacy_settings", data.get("legacy_settings", []))
    if not all(_KEY.match(k) for k in legacy):
        raise _config_error(where, "legacy_settings must be section.key names")
    return ProposeSettings(
        repos=_repos(where, _str_list(where, "repos", data.get("repos", []))),
        checkout_roots=_roots(
            where, _str_list(where, "checkout_roots", data.get("checkout_roots", []))
        ),
        paths=dict(paths),
        legacy_files=_str_list(where, "legacy_files", data.get("legacy_files", [])),
        legacy_settings=legacy,
        refused_settings=parse_refused(data.get("refused_settings", []), where),
        **ints,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise _config_error(path, err.strerror or str(err)) from err
    except ValueError as err:
        raise _config_error(path, f"not valid JSON: {err}") from err
    if not isinstance(data, dict):
        raise _config_error(path, "expected a JSON object")
    return data


_UNSET: Any = object()


def load_settings(
    env: Mapping[str, str] | None = None, config_path: Path | str | None = _UNSET
) -> ProposeSettings:
    """The packaged defaults, overlaid by the settings file, then by the environment.

    ``config_path`` defaults to ``$QODO_PROPOSE_CONFIG`` or ``~/.qodo/propose.json`` (a missing
    file is skipped; an unreadable or invalid one is an environment error); ``None`` reads none.
    """
    env = os.environ if env is None else env
    data = _read_json(DEFAULTS_PATH)
    if config_path is _UNSET:
        config_path = env.get(ENV_CONFIG) or Path.home() / ".qodo" / "propose.json"
    if config_path is not None and Path(config_path).exists():
        data.update(_read_json(Path(config_path)))
    if ENV_REPOS in env:
        data["repos"] = [r for r in re.split(r"[,\s]+", env[ENV_REPOS]) if r]
    if ENV_ROOTS in env:
        data["checkout_roots"] = [r for r in env[ENV_ROOTS].split(os.pathsep) if r]
    return _parse(data, config_path or DEFAULTS_PATH)


# --- paths and refs ------------------------------------------------------------------------------


def _normal(path: Any) -> bool:
    """A non-empty, relative, ``/``-separated path with no empty, ``.`` or ``..`` segment, no
    backslash and no control character."""
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return False
    return all(seg not in ("", ".", "..") for seg in path.split("/"))


def path_allowed(path: Any, paths: Mapping[str, str]) -> bool:
    """True only for a normal path that is exactly (case included) a key of ``paths``."""
    return _normal(path) and path in paths


def _base_ok(base: Any) -> bool:
    """A branch, tag or commit name in a strict subset of git's ref format: letters, digits,
    ``. _ / -``, never a leading ``-`` or ``/``, no ``..``, ``//``, ``@{``, no segment starting with
    ``.`` or ending in ``.lock``, no trailing ``/`` or ``.``."""
    if not isinstance(base, str) or not _REF_CHARS.match(base):
        return False
    if base.startswith(("-", "/")) or base.endswith(("/", ".")) or ".." in base:
        return False
    segments = base.split("/")
    return all(s and not s.startswith(".") and not s.endswith(".lock") for s in segments)


def _under_root(real: str, roots: tuple[str, ...]) -> bool:
    for root in roots:
        top = os.path.realpath(root)
        if real != top and os.path.commonpath([real, top]) == top:
            return True
    return False


def _is_checkout_of(real: str, repo: str, roots: tuple[str, ...]) -> bool:
    """Whether ``real`` is exactly ``<root>/<repo>`` for one of the roots."""
    segments = repo.split("/")
    if not all(seg and seg not in (".", "..") and os.sep not in seg for seg in segments):
        return False
    return any(real == os.path.join(os.path.realpath(root), *segments) for root in roots)


# --- results -------------------------------------------------------------------------------------


def _refuse(reason: str, detail: str, paths: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    assert reason in REASONS, reason  # nosec B101 - a programming error, not input
    unique = list(dict.fromkeys(paths))
    shown = [p for p in unique if len(p) <= MAX_PATH_CHARS][:MAX_PATHS]
    if len(shown) < len(unique):
        detail = f"{detail} ({len(unique) - len(shown)} more path(s) not shown)"
    return {"ok": False, "reason": reason, "detail": detail, "paths": shown}


def _warning(code: str, path: str, detail: str) -> dict[str, str]:
    return {"code": code, "path": path, "detail": detail}


# --- git, read-only and with the checkout's configuration neutralised ----------------------------

#: Overrides for the checkout's own ``.git/config``: no hooks, fsmonitor, pager, attributes
#: file, credential helper or transport (so a promisor remote cannot trigger a fetch).
_SAFE_CONFIG = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.pager=cat",
    "core.attributesFile=/dev/null",
    "core.untrackedCache=false",
    "credential.helper=",
    "protocol.allow=never",
    "gc.auto=0",
    "maintenance.auto=false",
    "diff.renames=true",
)


class _Git:
    def __init__(self, checkout: str, timeout: int) -> None:
        exe = shutil.which("git")
        if not exe:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message="git is not installed or not on PATH",
                remediation="install git on the bridge's machine",
            )
        self.timeout = timeout
        self.cwd = checkout
        # an explicit git dir (no discovery, no gitfile) and work tree (a hostile core.worktree,
        # or the server's own cwd, is never taken as the work tree)
        self.argv = [exe, f"--git-dir={checkout}/.git", f"--work-tree={checkout}"]
        self.argv += ["--no-pager", "--no-optional-locks"]
        self.argv += [arg for pair in _SAFE_CONFIG for arg in ("-c", pair)]
        # a fresh environment: nothing the caller exported (GIT_DIR, GIT_EXTERNAL_DIFF,
        # GIT_CONFIG_PARAMETERS, ...) reaches git; no global or system config either
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.devnull,
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_CEILING_DIRECTORIES": os.path.dirname(checkout),
        }

    def run(self, *args: str, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        try:
            proc = subprocess.run(  # nosec B603 - argv list, resolved absolute git, no shell
                [*self.argv, *args],
                capture_output=True,
                env=self.env,
                cwd=self.cwd,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as err:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"git {args[0]} timed out after {self.timeout}s",
                remediation="check the checkout (its size, its disk) and git_timeout_seconds",
            ) from err
        except OSError as err:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"cannot run git: {err.strerror or err}",
                remediation="check the git installation on the bridge's machine",
            ) from err
        if proc.returncode not in ok:
            detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"git {args[0]} failed (exit {proc.returncode}): "
                f"{(detail[-1] if detail else '')[:200]}",
                remediation="check that the checkout is a readable git repository",
            )
        return proc

    def commit(self, ref: str) -> str | None:
        """The commit sha ``ref`` names, or ``None``."""
        proc = self.run(
            "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}", ok=(0, 1)
        )
        sha = proc.stdout.decode("ascii", "replace").strip()
        return sha if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", sha) else None

    def is_ancestor(self, base: str, head: str) -> bool:
        proc = self.run("merge-base", "--is-ancestor", "--end-of-options", base, head, ok=(0, 1))
        return proc.returncode == 0

    def changes(self, base: str, head: str) -> list[_Change]:
        out = self.run(
            "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "-M", "--no-ext-diff",
            "--no-textconv", "--ignore-submodules=none", "--end-of-options", base, head, "--",
        ).stdout  # fmt: skip
        return _parse_raw(out)

    def size(self, sha: str) -> int:
        return int(self.run("cat-file", "-s", sha).stdout.strip())

    def blob(self, sha: str) -> bytes:
        return self.run("cat-file", "blob", sha).stdout


@dataclass(frozen=True)
class _Change:
    status: str
    src_mode: str
    dst_mode: str
    src_sha: str
    dst_sha: str
    paths: tuple[str, ...]  # (path,) or, for a rename or copy, (source, destination)

    @property
    def path(self) -> str:
        return self.paths[-1]


def _decode_path(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "backslashreplace") + "\x00"  # never a normal path


def _parse_raw(out: bytes) -> list[_Change]:
    """``:srcmode dstmode srcsha dstsha status\\0path\\0`` records (two paths for R and C)."""
    tokens = out.split(b"\x00")
    changes: list[_Change] = []
    i = 0
    while i < len(tokens) and tokens[i]:
        head = tokens[i].decode("ascii", "replace")
        if not head.startswith(":"):
            raise CliError(
                code=EXIT_ENV_ERROR,
                message="unexpected git diff-tree output",
                remediation="check the git version on the bridge's machine",
            )
        src_mode, dst_mode, src_sha, dst_sha, status = head[1:].split(" ", 4)
        count = 2 if status[:1] in ("R", "C") else 1
        paths = tuple(_decode_path(p) for p in tokens[i + 1 : i + 1 + count])
        changes.append(_Change(status[:1], src_mode, dst_mode, src_sha, dst_sha, paths))
        i += 1 + count
    return changes


# --- the checks ----------------------------------------------------------------------------------


def _structure(changes: list[_Change], s: ProposeSettings) -> dict[str, Any] | None:
    """The change set's shape, before any content is read."""
    if not changes:
        return _refuse("no_changes", "HEAD makes no change against the base")
    if len(changes) > s.max_files:
        return _refuse(
            "too_many_files",
            f"{len(changes)} changed files, at most {s.max_files}",
            [c.path for c in changes],
        )
    renamed = [p for c in changes if c.status in ("R", "C") for p in c.paths]
    if renamed:
        return _refuse("rename", "a rename or copy is never proposable", renamed)
    outside = [p for c in changes for p in c.paths if not path_allowed(p, s.paths)]
    if outside:
        allowed = ", ".join(sorted(s.paths)) or "(none)"
        return _refuse("path_not_allowed", f"only these paths may change: {allowed}", outside)
    rules = (
        ("deletion", lambda c: c.status == "D", "deleting a configuration file is not proposable"),
        ("type_change", lambda c: c.status not in ("A", "M"), "the file changed type"),
        (
            "not_regular_file",
            lambda c: c.dst_mode not in (_REGULAR, _EXECUTABLE),
            "a symlink, submodule or other non-regular entry at HEAD",
        ),
        ("executable", lambda c: c.dst_mode == _EXECUTABLE, "the file is executable at HEAD"),
    )
    for reason, bad, detail in rules:
        hits = [c.path for c in changes if bad(c)]
        if hits:
            return _refuse(reason, detail, hits)
    return None


def _lookup(data: Any, parts: tuple[str, ...]) -> Any:
    for part in parts:
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _changed_settings(old: dict, new: dict, refused: tuple[RefusedSetting, ...]) -> list[str]:
    keys = []
    for rule in refused:
        before, after = _lookup(old, rule.parts), _lookup(new, rule.parts)
        if before != after and not (rule.allow and after in rule.allow):
            keys.append(rule.key)
    return keys


def _toml(text: str) -> tuple[dict | None, str]:
    try:
        return tomllib.loads(text), ""
    except tomllib.TOMLDecodeError as err:
        return None, str(err)[:200]


def _content(
    git: _Git, change: _Change, data: bytes, s: ProposeSettings, warnings: list[dict]
) -> tuple[str, str] | str:
    """The text, or ``(reason, detail)`` for a file that may not be proposed."""
    if b"\x00" in data:
        return ("binary", "a binary file (it holds a NUL byte)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ("not_utf8", "not UTF-8 text")
    path = change.path
    if path in s.legacy_files:
        warnings.append(_warning("legacy_file", path, f"{path} is {LEGACY_DETAIL}"))
    if s.paths[path] == "markdown":
        if not text.strip():
            warnings.append(_warning("empty_file", path, f"{path} is empty"))
        return text
    new, error = _toml(text)
    if new is None:
        return ("invalid_toml", f"not valid TOML: {error}")
    old: dict = {}
    if change.status == "M" and change.src_mode in (_REGULAR, _EXECUTABLE):
        # an unreadable or oversized base counts as empty: every refused key then reads changed
        if git.size(change.src_sha) <= s.max_file_bytes:
            old = _toml(git.blob(change.src_sha).decode("utf-8", "replace"))[0] or {}
    changed = _changed_settings(old, new, s.refused_settings)
    if changed:
        return ("refused_setting", f"changes a setting a proposal may not: {', '.join(changed)}")
    for key in s.legacy_settings:
        if _lookup(new, tuple(key.split("."))) is not None:
            warnings.append(_warning("legacy_setting", path, f"[{key}] is {LEGACY_DETAIL}"))
    return text


def _contents(git: _Git, changes: list[_Change], s: ProposeSettings, result: dict) -> dict | None:
    sizes = {c.path: git.size(c.dst_sha) for c in changes}
    big = [p for p, n in sizes.items() if n > s.max_file_bytes]
    if big:
        return _refuse("file_too_large", f"a file is over {s.max_file_bytes} bytes", big)
    if sum(sizes.values()) > s.max_reply_bytes:
        return _refuse("too_large", f"the files are over {s.max_reply_bytes} bytes together")
    failures: dict[str, tuple[list[str], str]] = {}
    for change in changes:
        data = git.blob(change.dst_sha)
        out = _content(git, change, data, s, result["warnings"])
        if isinstance(out, tuple):
            failures.setdefault(out[0], ([], out[1]))[0].append(change.path)
        else:
            result["files"].append({"path": change.path, "content": out})
    for reason in REASONS:
        if reason in failures:
            paths, detail = failures[reason]
            return _refuse(reason, detail, paths)
    return None


def propose(repo: Any, checkout: Any, base: Any, settings: ProposeSettings) -> dict[str, Any]:
    """Run the checks; the success or refusal object (see the module docstring)."""
    s = settings
    if not isinstance(repo, str) or repo not in s.repos:
        return _refuse("repo_not_allowed", "the repo is not in the bridge's repo allowlist")
    if not isinstance(checkout, str) or not os.path.isabs(checkout):
        return _refuse("checkout_not_allowed", "the checkout must be an absolute path")
    real = os.path.realpath(checkout)
    if not _under_root(real, s.checkout_roots):
        return _refuse("checkout_not_allowed", "the checkout is not under an allowed root")
    if not _is_checkout_of(real, repo, s.checkout_roots):
        # the repo name is bound to its checkout by where the operator put it, never by what
        # the checkout says about itself (its remotes are the checkout's own, editable, data)
        return _refuse(
            "checkout_not_allowed",
            "the checkout of a repo is <root>/<owner>/<name>; this path is another repo's place",
        )
    _require_repository(real)
    if not _base_ok(base):
        return _refuse("invalid_base", "the base must be a plain branch, tag or commit name")
    git = _Git(real, s.git_timeout_seconds)
    head = git.commit("HEAD")
    if head is None:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="the checkout has no commit at HEAD",
            remediation="commit the change in the checkout first",
        )
    base_sha = git.commit(base)
    if base_sha is None:
        return _refuse("unknown_base", "the base does not name a commit in the checkout")
    if not git.is_ancestor(base_sha, head):
        return _refuse("base_not_ancestor", "the base is not an ancestor of HEAD")
    changes = git.changes(base_sha, head)
    refusal = _structure(changes, s)
    if refusal:
        return refusal
    result: dict[str, Any] = {
        "ok": True,
        "repo": repo,
        "base": base,
        "base_sha": base_sha,
        "head": head,
        "files": [],
        "warnings": [],
    }
    refusal = _contents(git, changes, s, result)
    if refusal:
        return refusal
    # measured as the HTTP step will see it: ASCII-escaped JSON, the worst case
    if len(json.dumps(result).encode("utf-8")) > s.max_reply_bytes:
        return _refuse("too_large", f"the reply would be over {s.max_reply_bytes} bytes")
    return result


def _require_repository(real: str) -> None:
    """The checkout is a directory holding a ``.git`` directory (not a symlink, not a gitfile
    pointing elsewhere)."""
    try:
        top = os.lstat(real)
        dot_git = os.lstat(os.path.join(real, ".git"))
    except OSError as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"the checkout is not readable as a git repository: {err.strerror or err}",
            remediation="point checkout at a plain git clone under an allowed root",
        ) from err
    if not stat.S_ISDIR(top.st_mode) or not stat.S_ISDIR(dot_git.st_mode):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="the checkout is not a plain git repository (no .git directory)",
            remediation="use a plain clone (not a linked worktree or a gitfile) under an allowed "
            "root",
        )
