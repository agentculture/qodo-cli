"""Zero-dependency Qodo rules API client.

Cites ``qodo-ai/qodo-skills`` ``qodo-get-rules`` as the behavioral source of
truth: the endpoint, base-URL construction, auth header, request/response
schema, and severity labels below mirror that skill's
``references/search-endpoint.md`` and ``references/output-format.md``. We
reimplement the mechanics natively over the stdlib (``urllib``) — we do not
vendor, fork, or npx-install the skill. See ``docs/qodo-skills-sources.md`` for
the provenance ledger.

What this module owns is the *deterministic* slice: read the existing
credentials, build the URL, POST the search, and hand back the ranked rules. The
*query generation* step the skill performs (an LLM turning a task into two
structured queries) is the caller's job — a Claude agent calling
``qodo rules get`` can run it twice, once per generated query.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - resolved absolute path, no shell (B603 project-skipped)
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

# Cited from qodo-get-rules/references/search-endpoint.md + output-format.md.
DEFAULT_TOP_K = 20
SEVERITIES = ("ERROR", "WARNING", "RECOMMENDATION")
# Our telemetry identity. The upstream skill sends "skill-qodo-get-rules"; this
# header is telemetry only and does not affect the search behavior.
_CLIENT_TYPE = "qodo-cli"
_REQUEST_TIMEOUT = 30


def config_path() -> Path:
    """Path to the Qodo config the skills already use (``~/.qodo/config.json``)."""
    return Path.home() / ".qodo" / "config.json"


def load_qodo_config() -> dict[str, Any]:
    """Read ``~/.qodo/config.json`` if present.

    An absent file is not fatal here (the API key may come from ``QODO_API_KEY``);
    :func:`resolve_api_key` makes the final call. An *unreadable* or malformed
    file is an environment error — we never prompt for credentials.
    """
    path = config_path()
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot read {path}: {err.strerror or err}",
            remediation="check the permissions on ~/.qodo/config.json",
        ) from err
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"{path} is not valid JSON: {err}",
            remediation="repair ~/.qodo/config.json (it must be a JSON object)",
        ) from err
    if not isinstance(data, dict):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"{path} must contain a JSON object",
            remediation="repair ~/.qodo/config.json",
        )
    return data


def resolve_api_key(config: dict[str, Any]) -> str:
    """Return the Qodo API key. ``QODO_API_KEY`` wins over the config ``API_KEY``."""
    key = os.environ.get("QODO_API_KEY") or config.get("API_KEY")
    if not key:
        path = config_path()
        if path.is_file():
            hint = 'add an "API_KEY" to ~/.qodo/config.json, or export QODO_API_KEY'
        else:
            hint = f'create {path} with an "API_KEY", or export QODO_API_KEY'
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="no Qodo API key found",
            remediation=hint,
        )
    return str(key)


def resolve_base_url(config: dict[str, Any]) -> str:
    """Build the rules API base URL, per qodo-get-rules' construction rules.

    ``QODO_API_URL`` (env or config) is an explicit override; otherwise the host
    is derived from ``ENVIRONMENT_NAME`` (env ``QODO_ENVIRONMENT_NAME`` wins):
    empty -> production, anything else -> ``qodo-platform.<env>.qodo.ai``.
    """
    override = os.environ.get("QODO_API_URL") or config.get("QODO_API_URL")
    if override:
        return f"{str(override).rstrip('/')}/rules/v1"
    env_name = os.environ.get("QODO_ENVIRONMENT_NAME")
    if env_name is None:
        env_name = config.get("ENVIRONMENT_NAME") or ""
    env_name = str(env_name).strip()
    host = "qodo-platform.qodo.ai" if not env_name else f"qodo-platform.{env_name}.qodo.ai"
    return f"https://{host}/rules/v1"


# --- scope auto-detection (cited from qodo-get-rules) ----------------------
#
# qodo-get-rules derives a repository scope from `git remote get-url origin`
# (parsing the org/repo slug) and a module scope when the working path contains
# `modules/<name>/`, then passes them as `scopes`. We mirror that, non-raising:
# absence of an origin (or of git) simply yields no scope — `scopes` is omitted
# entirely rather than sent empty. See docs/qodo-skills-sources.md.


def _origin_url() -> str | None:
    """The ``origin`` remote URL, or ``None`` if git/origin is unavailable (non-raising)."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        proc = subprocess.run(  # nosec B603 - resolved absolute path, no shell
            [git, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
    except OSError:
        # git vanished / not executable between the which() probe and the run,
        # or any spawn failure — scope detection is best-effort, so stay silent.
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def repo_slug(url: str) -> str | None:
    """Parse the ``org/repo`` slug from an SSH or HTTPS remote URL (or ``None``).

    Handles ``git@host:org/repo.git``, ``https://host/org/repo(.git)``,
    ``ssh://git@host[:port]/org/repo.git``; preserves multi-level namespaces
    (e.g. GitLab subgroups ``org/sub/repo``).
    """
    u = (url or "").strip()
    if not u:
        return None
    if "://" in u:
        path = urllib.parse.urlparse(u).path
    elif ":" in u:
        # scp-like: [user@]host:org/repo
        path = u.split(":", 1)[1]
    else:
        return None
    # Strip the path's surrounding slashes *before* the ``.git`` suffix, so a
    # ``org/repo.git/`` (trailing slash after .git) still yields ``org/repo``.
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path.strip("/") or None


def module_scope(cwd: Path) -> str | None:
    """The ``<name>`` from a ``modules/<name>/`` path segment, or ``None``."""
    parts = cwd.parts
    if "modules" in parts:
        idx = parts.index("modules")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def detect_scopes(cwd: Path | None = None) -> list[str]:
    """Auto-detect the rules scopes from the git origin + cwd (never raises).

    Returns the repository slug and, when inside a ``modules/<name>/`` path, the
    module name. Empty when nothing is detectable (caller then omits ``scopes``).
    """
    scopes: list[str] = []
    url = _origin_url()
    if url:
        slug = repo_slug(url)
        if slug:
            scopes.append(slug)
    if cwd is None:
        try:
            cwd = Path.cwd()
        except OSError:
            # cwd was deleted out from under us — skip module detection.
            cwd = None
    if cwd is not None:
        module = module_scope(cwd)
        if module:
            scopes.append(module)
    return scopes


def search_rules(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    scopes: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """POST ``{base}/rules/search`` and return the ranked rules list.

    The returned rules preserve the API's relevance ordering (most relevant
    first). Each rule is ``{id, name, content, severity}``; ``severity`` is one
    of :data:`SEVERITIES`. Raises :class:`CliError` (exit 2) on a missing key,
    network failure, or non-2xx response.
    """
    if top_k <= 0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--top-k must be a positive integer, got {top_k}",
            remediation="pass --top-k with a value >= 1",
        )
    cfg = load_qodo_config() if config is None else config
    api_key = resolve_api_key(cfg)
    base = resolve_base_url(cfg)
    url = f"{base}/rules/search"
    if not url.lower().startswith("https://"):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"refusing to call a non-HTTPS Qodo URL: {url}",
            remediation="QODO_API_URL must be an https:// endpoint",
        )

    body: dict[str, Any] = {"query": query, "top_k": top_k}
    # The skill omits `scopes` entirely when empty — never send null/[].
    if scopes:
        body["scopes"] = list(scopes)
    payload = json.dumps(body).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "request-id": str(uuid.uuid4()),
        "qodo-client-type": _CLIENT_TYPE,
    }
    trace_id = os.environ.get("TRACE_ID")
    if trace_id:
        headers["trace_id"] = trace_id

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        # nosec B310 - scheme is asserted https above; URL derives from the
        # user's own ~/.qodo/config.json, not untrusted input.
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # nosec B310
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"Qodo API returned HTTP {err.code} for {url}",
            remediation=_http_remediation(err.code),
        ) from err
    except urllib.error.URLError as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot reach the Qodo API at {base}: {err.reason}",
            remediation="check your network and QODO_API_URL / ENVIRONMENT_NAME",
        ) from err

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="the Qodo API returned a non-JSON response",
            remediation="retry; if it persists the Qodo API may be unavailable",
        ) from err

    rules = data.get("rules", []) if isinstance(data, dict) else []
    return rules if isinstance(rules, list) else []


def _http_remediation(code: int) -> str:
    if code in (401, 403):
        return "check your Qodo API key (QODO_API_KEY or ~/.qodo/config.json API_KEY)"
    if code == 404:
        return "verify the Qodo environment / base URL (ENVIRONMENT_NAME or QODO_API_URL)"
    return "retry; if it persists the Qodo API may be unavailable"
