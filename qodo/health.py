"""Qodo API key health: is there a key, and how long until it expires.

The expiry is **not readable from the key itself**. It is configuration set when the key is
created: ``QODO_API_KEY_EXPIRES`` (env, wins) or ``API_KEY_EXPIRES`` next to ``API_KEY`` in
``~/.qodo/config.json``, an ISO date (``2026-12-31``) or ISO datetime (its UTC date is used).

:func:`key_status` is a plain function for ``doctor``, a future HTTP health endpoint and a UI
header ("Qodo key: N days left"). It never raises and never returns the key.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

WARN_DAYS = 14  # fewer days left than this is a failing check
ENV_EXPIRES = "QODO_API_KEY_EXPIRES"
CONFIG_EXPIRES = "API_KEY_EXPIRES"


def parse_expiry(value: str) -> date:
    """An ISO date or datetime to a date (UTC). Raises ``ValueError``."""
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if when.tzinfo is not None:
        when = when.astimezone(UTC)
    return when.date()


def _read_config(home: Path) -> dict[str, Any]:
    try:
        data = json.loads((home / ".qodo" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def key_status(*, now: datetime | None = None, home: Path | None = None) -> dict[str, Any]:
    """``{present, state, expires, days_left, detail}``.

    ``state`` is ``missing`` | ``unknown`` (key present, no expiry configured) | ``invalid``
    (expiry unparseable) | ``expired`` | ``expiring`` (under ``WARN_DAYS`` left) | ``ok``.
    ``days_left`` is whole UTC calendar days (negative once expired), ``None`` when unknown.
    """
    when = now or datetime.now(UTC)
    config = _read_config(home if home is not None else Path.home())
    present = bool(os.environ.get("QODO_API_KEY") or str(config.get("API_KEY") or "").strip())
    status: dict[str, Any] = {
        "present": present,
        "state": "missing",
        "expires": None,
        "days_left": None,
        "detail": "no Qodo API key found",
    }
    if not present:
        return status
    raw = os.environ.get(ENV_EXPIRES) or config.get(CONFIG_EXPIRES)
    if raw is None or not str(raw).strip():
        status.update(state="unknown", detail="no key expiry configured")
        return status
    try:
        expires = parse_expiry(str(raw))
    except ValueError:
        status.update(state="invalid", detail=f"cannot parse key expiry {str(raw)[:40]!r}")
        return status
    days = (expires - when.astimezone(UTC).date()).days
    state = "expired" if days < 0 else "expiring" if days < WARN_DAYS else "ok"
    status.update(state=state, expires=expires.isoformat(), days_left=days, detail="")
    return status
