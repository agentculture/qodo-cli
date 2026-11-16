"""``findings fix``: ask for an agent to fix Qodo findings, by publishing ONE event on the mesh's
event bus.

The bridge does not fix anything and does not open a pull request. It checks the request,
publishes an event of type :data:`EVENT_TYPE` through events-cli's own client (the MQTT fabric
the mesh shares), and logs one ``fix.requested`` line in the change log. culture-rules ingests the
event; a rule (configuration, not this repo's code) starts a workflow that runs an agent in a
dedicated checkout of the pull request's branch, and the fix comes back through ``fix propose``
and culture-rules' own open-PR action as a new pull request against the reviewed branch.

Order of checks, each refusing before anything is published or logged:

1. the params: ``pr_url`` is a github.com pull request URL, 1 to :data:`MAX_FINDING_IDS` plain
   finding ids, an optional note of at most :data:`MAX_NOTE_CHARS` characters (exit 1);
2. the **repo allowlist**: the PR's ``owner/name`` must be in ``config propose``'s repo allowlist
   (``QODO_PROPOSE_REPOS`` or the settings file, :func:`qodo.propose.load_settings`), so a token
   holder cannot aim an agent at an arbitrary repository; an empty allowlist refuses everything
   (exit 1);
3. the **rate guard**: the same PR and the same set of finding ids requested within
   ``QODO_FIX_REQUEST_WINDOW`` seconds (default :data:`DEFAULT_WINDOW_SECONDS`; ``0`` turns it
   off) is refused, read from the change log (exit 1);
4. the publish: without the ``events`` extra, or with the broker unreachable, an environment
   error (exit 2, HTTP 503) and nothing is logged.

The findings are **not** looked up first: the bridge would have to call Qodo (with the personal
key, rate-limited as that person) on every click, the agent reads the findings itself before it
changes anything, and an id that names no finding costs one agent run that finds nothing to do,
not a change. The event carries identifiers only: never the API key, never finding text.

The event (events-cli's envelope, minted by ``events_cli.core.Envelope.new``, so its id is
events-cli's ``evt_`` + ULID, unique per request; consumers dedupe on it): ``type``
:data:`EVENT_TYPE`, ``source`` :data:`EVENT_SOURCE`, published at QoS 1 to events-cli's canonical
topic (``events/qodo/fix/requested``), no content marks (a reader on events-cli 0.10 drops an
envelope carrying them), and ``data``::

    {"pr_url", "repo", "number", "finding_ids", "note", "requested_by"}

``requested_by`` is the ``actor`` the registry passes: the calling surface (``cli`` or ``mcp``),
or over HTTP the surface and the caller (``api:person@example.org``, ``api:token:<name>``), a
label :mod:`qodo.bridge.caller` has cleaned of control characters and capped in length.

events-cli is imported lazily inside :func:`publish` (the ``events`` extra): the base install
keeps zero third-party dependencies. The broker address is events-cli's own setting,
``EVENTS_BROKER_HOST`` / ``EVENTS_BROKER_PORT`` (default ``127.0.0.1:1883``).
"""

from __future__ import annotations

import fcntl
import os
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from qodo import changelog
from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

__all__ = [
    "DEFAULT_WINDOW_SECONDS",
    "EVENT_SOURCE",
    "EVENT_TYPE",
    "MAX_FINDING_IDS",
    "MAX_NOTE_CHARS",
    "parse_pr_url",
    "publish",
    "request_fix",
]

EVENT_TYPE = "qodo.fix.requested"
EVENT_SOURCE = "app://qodo-cli"
MAX_FINDING_IDS = 20
MAX_FINDING_ID_CHARS = 128
MAX_NOTE_CHARS = 500
ENV_WINDOW = "QODO_FIX_REQUEST_WINDOW"
DEFAULT_WINDOW_SECONDS = 300
#: How long :func:`publish` waits for the connection, then for the broker to take the event.
#: Finite: a broker that is down costs these seconds and then an environment error.
CONNECT_WAIT = 5.0
DELIVERY_WAIT = 5.0
_POLL = 0.02
EXTRA_HINT = "install the events extra: pip install 'qodo-cli[events]' (or uv sync --extra events)"
BROKER_HINT = (
    "start the broker (`events up` on the bridge's host) or point EVENTS_BROKER_HOST / "
    "EVENTS_BROKER_PORT at it (default 127.0.0.1:1883)"
)

#: A finding id: Qodo's are UUIDs. Plain characters only, because the id travels into an
#: agent's brief.
_FINDING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PR_PATH = re.compile(r"^/([^/]+)/([^/]+)/pull/([1-9][0-9]{0,9})/?$")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

Publisher = Callable[[str, dict[str, Any]], Mapping[str, Any]]


def _user(message: str, remediation: str) -> CliError:
    return CliError(code=EXIT_USER_ERROR, message=message, remediation=remediation)


def _env(message: str, remediation: str) -> CliError:
    return CliError(code=EXIT_ENV_ERROR, message=message, remediation=remediation)


# --- the params ---------------------------------------------------------------------------------


def parse_pr_url(url: Any) -> tuple[str, str, int]:
    """``(canonical URL, "owner/name", number)`` for a github.com pull request URL.

    Only ``http(s)://github.com/<owner>/<name>/pull/<n>`` (a trailing ``/`` allowed; no user,
    port, query or fragment): the fix lands through culture-rules' GitHub App.
    """
    from qodo.propose import _REPO  # the owner/name shape the repo allowlist uses

    hint = "pass the pull request's web URL, e.g. https://github.com/org/repo/pull/1"
    if not isinstance(url, str) or not url.strip():
        raise _user("pr_url is required", hint)
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise _user(f"pr_url is not a URL: {url[:200]!r}", hint) from None
    match = _PR_PATH.fullmatch(parts.path)
    plain = not (parts.username or parts.password or port or parts.query or parts.fragment)
    host_ok = (parts.hostname or "").lower() == "github.com"
    if parts.scheme not in ("http", "https") or not host_ok or not plain or not match:
        raise _user(f"pr_url must be a github.com pull request URL, got {url[:200]!r}", hint)
    owner, name, number = match.groups()
    repo = f"{owner}/{name}"
    if not _REPO.fullmatch(repo) or ".." in repo:
        raise _user(f"pr_url names no owner/name repository: {url[:200]!r}", hint)
    return f"https://github.com/{repo}/pull/{number}", repo, int(number)


def _finding_ids(ids: Any) -> list[str]:
    hint = f"pass 1 to {MAX_FINDING_IDS} finding ids, from `findings list`"
    if not isinstance(ids, (list, tuple)) or not ids:
        raise _user("finding ids are required", hint)
    if len(ids) > MAX_FINDING_IDS:
        raise _user(f"at most {MAX_FINDING_IDS} finding ids per request, got {len(ids)}", hint)
    for one in ids:
        if not isinstance(one, str) or not _FINDING_ID.fullmatch(one):
            shown = repr(one)[:60]
            raise _user(
                f"finding id {shown} is not a plain id (letters, digits, '.', '_', ':', '-'; "
                f"at most {MAX_FINDING_ID_CHARS} characters)",
                hint,
            )
    return list(dict.fromkeys(ids))


def _note(note: Any) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        raise _user("note must be text", "pass a short note for the agent, or none")
    if len(note) > MAX_NOTE_CHARS:
        raise _user(
            f"note is longer than {MAX_NOTE_CHARS} characters",
            "shorten the note; the agent reads the findings themselves",
        )
    # one line, always: the workflow hands the note to an agent between marker lines, and a
    # note that could start a line of its own could forge the closing marker
    one_line = " ".join("".join(c if c.isprintable() else " " for c in note).split())
    return one_line or None


# --- the rate guard -----------------------------------------------------------------------------


def window_seconds(env: Mapping[str, str] | None = None) -> int:
    """``QODO_FIX_REQUEST_WINDOW`` (whole seconds, ``0`` turns the guard off), default
    :data:`DEFAULT_WINDOW_SECONDS`; anything else is an environment error."""
    env = os.environ if env is None else env
    raw = env.get(ENV_WINDOW, "").strip()
    if not raw:
        return DEFAULT_WINDOW_SECONDS
    if not (raw.isascii() and raw.isdigit()):
        raise _env(
            f"{ENV_WINDOW}={raw[:40]!r} is not a whole number of seconds",
            f"unset {ENV_WINDOW} (default {DEFAULT_WINDOW_SECONDS}) or set it to 0 or more",
        )
    return int(raw)


def _now() -> datetime:
    return datetime.now(UTC)


def _recent_request(
    pr_url: str, ids: list[str], window: int, now: datetime
) -> dict[str, Any] | None:
    """The newest ``fix.requested`` line for the same PR and the same set of ids within
    ``window`` seconds of ``now``, or ``None``."""
    if window <= 0:
        return None
    wanted = sorted(set(ids))
    for entry in reversed(changelog.read()):
        if entry.get("kind") != changelog.KIND_FIX_REQUESTED or entry.get("pr_url") != pr_url:
            continue
        logged = entry.get("finding_ids")
        if not isinstance(logged, list) or sorted(set(map(str, logged))) != wanted:
            continue
        try:
            at = datetime.strptime(str(entry.get("time")), _TIME_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            continue
        if 0 <= (now - at).total_seconds() < window:
            return entry
    return None


class _Lock:
    """An exclusive lock beside the change log, so two clicks arriving together cannot both
    pass the rate guard. Best effort: if it cannot be taken, the guard rests on the log alone."""

    def __enter__(self) -> _Lock:
        self.fd = None
        try:
            path = changelog.log_path()
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.fd = os.open(
                path.with_name(path.name + ".fix.lock"), os.O_RDWR | os.O_CREAT, 0o600
            )
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except OSError:
            if self.fd is not None:
                os.close(self.fd)
            self.fd = None
        return self

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)  # closing releases the lock


# --- publishing ---------------------------------------------------------------------------------


def publish(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Publish one event through events-cli's own client; ``{"event_id", "topic"}``.

    The envelope is minted and validated by ``events_cli.core.Envelope.new`` (its ``evt_`` id),
    the topic is events-cli's canonical one for the type, and the publish is QoS 1, waiting a
    bounded time for the connection and for the broker to take it. Without events-cli (or its
    MQTT client), with a malformed broker setting, or with no broker taking the event: an
    environment error.
    """
    try:
        from events_cli import EventClient
        from events_cli.core import Envelope, EventsError, type_to_topic
    except ImportError as err:
        raise _env(
            f"the event bus client (events-cli) is not installed ({err})", EXTRA_HINT
        ) from err
    try:
        envelope = Envelope.new(event_type, EVENT_SOURCE, data=data)
        topic = type_to_topic(envelope.type)
    except EventsError as err:
        raise _env(
            f"events-cli refused the event: {str(err)[:300]}", "report this as a bug"
        ) from err
    try:
        client = EventClient()
    except ImportError as err:  # pragma: no cover - events-cli raises its own error for paho
        raise _env(f"the MQTT client is not installed ({err})", EXTRA_HINT) from err
    except (EventsError, ValueError) as err:
        hint = getattr(err, "remediation", "") or BROKER_HINT
        raise _env(f"cannot reach the event bus: {str(err)[:300]}", hint) from err
    try:
        deadline = time.monotonic() + CONNECT_WAIT
        while not client.is_connected and time.monotonic() < deadline:
            time.sleep(_POLL)
        result = client.publish_event(envelope, topic, qos=1, wait=DELIVERY_WAIT)
    finally:
        client.close()
    if not getattr(result, "ok", False):
        reason = getattr(result, "reason", None) or "not confirmed"
        state = "connected" if getattr(result, "connected", False) else "not connected"
        raise _env(f"the event bus did not take the event ({reason}; {state})", BROKER_HINT)
    return {"event_id": envelope.id, "topic": topic}


# --- the request --------------------------------------------------------------------------------


def request_fix(
    pr_url: Any,
    finding_ids: Any,
    note: Any = None,
    *,
    publisher: Publisher,
    actor: str | None = None,
    settings: Any = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Check the request, publish the event, log it; ``{event_id, type, pr_url, repo, number,
    finding_ids}``. A refusal raises :class:`CliError` with nothing published or logged."""
    from qodo import propose

    url, repo, number = parse_pr_url(pr_url)
    ids = _finding_ids(finding_ids)
    text = _note(note)
    allowed = (settings if settings is not None else propose.load_settings(env=env)).repos
    if repo not in allowed:
        raise _user(
            f"{repo} is not in the bridge's repo allowlist; no agent was asked",
            f"an operator lists the repo in {propose.ENV_REPOS} (or the settings file's repos)",
        )
    window = window_seconds(env)
    with _Lock():
        earlier = _recent_request(url, ids, window, _now())
        if earlier is not None:
            # the web UI tells this refusal from an error by the words "already requested"
            # (web/src/api/qodo.ts isAlreadyRequested); tests on both sides pin them
            raise _user(
                f"a fix for these findings was already requested at {earlier.get('time')} "
                f"(event {earlier.get('event_id')})",
                f"wait {window} seconds from that request, or change the set of findings",
            )
        data = {
            "pr_url": url,
            "repo": repo,
            "number": number,
            "finding_ids": ids,
            "note": text,
            "requested_by": actor,
        }
        published = publisher(EVENT_TYPE, data)
        event_id = str(published["event_id"])
        changelog.record_fix_request(url, ids, event_id, actor=actor)
    return {
        "event_id": event_id,
        "type": EVENT_TYPE,
        "pr_url": url,
        "repo": repo,
        "number": number,
        "finding_ids": ids,
    }
