"""Who is calling, for the length of one request: a context variable the HTTP layer sets and the
code that records an actor reads.

A verb handler is called as ``handler(params, surface=...)`` and is never handed the request, so
the HTTP layer sets the caller around the call with :func:`acting_as` and the code that writes an
audit line or publishes an event asks :func:`actor` for ``<surface>`` or ``<surface>:<caller>``.
A call that no HTTP request wraps (the CLI, a stdio MCP server, a test) has no caller and gets
the bare surface, so nothing changes for those surfaces.

The caller label (:func:`label`) names the principal without a secret: a person's identity as
asserted (``person@example.org``), a service token's own name (``token:<name>``) or a mesh
agent's (``agent:<name>``). It is cleaned before it is stored: control and format characters
(newlines, escapes, bidi overrides) are dropped, whitespace is collapsed and the result is cut to
:data:`MAX_LABEL_CHARS`, so an identity cannot forge a second log line or flood one.
Standard library only.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

__all__ = ["MAX_LABEL_CHARS", "acting_as", "actor", "clean", "current", "label"]

#: The longest caller label kept (after cleaning); a longer one is cut and ends in ``...``.
MAX_LABEL_CHARS = 120
_UNKNOWN = "unknown"
_PREFIX = {"service": "token:", "agent": "agent:"}

_CALLER: ContextVar[str | None] = ContextVar("bridge_caller", default=None)


def clean(text: Any, limit: int = MAX_LABEL_CHARS) -> str:
    """``text`` safe to log or publish: no control, format, line or paragraph separator
    characters, whitespace runs collapsed to one space, at most ``limit`` characters."""
    raw = text if isinstance(text, str) else str(text)
    kept = []
    for ch in raw:
        category = unicodedata.category(ch)
        if ch.isspace():
            kept.append(" ")
        elif category[0] == "C" or category in ("Zl", "Zp"):
            continue
        else:
            kept.append(ch)
    out = " ".join("".join(kept).split())
    if len(out) > limit:
        out = out[: max(limit - 3, 0)].rstrip() + "..."
    return out or _UNKNOWN


def label(principal: Any) -> str:
    """The caller label of a principal (anything with ``identity`` and ``kind``): the identity
    for a person (``sso``), ``token:<name>`` for a service token, ``agent:<name>`` for an agent."""
    identity = clean(getattr(principal, "identity", "") or "")
    prefix = _PREFIX.get(str(getattr(principal, "kind", "")), "")
    return clean(prefix + identity)


@contextmanager
def acting_as(caller: str | None) -> Iterator[None]:
    """Run the block with ``caller`` (cleaned) as the current caller; ``None`` clears it."""
    token = _CALLER.set(clean(caller) if caller is not None else None)
    try:
        yield
    finally:
        _CALLER.reset(token)


def current() -> str | None:
    """The current caller label, or ``None`` outside :func:`acting_as`."""
    return _CALLER.get()


def actor(surface: str) -> str:
    """``<surface>:<caller>`` inside :func:`acting_as`, else ``surface`` alone."""
    caller = _CALLER.get()
    return f"{surface}:{caller}" if caller else surface
