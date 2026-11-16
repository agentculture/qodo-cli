"""What a long-lived server shares between upstream calls: one gateway, and a findings cache.

A :class:`qodo.gateway.Gateway` does an MCP handshake (``initialize``,
``notifications/initialized``) and reads ``tools/list`` before its first ``tools/call``. A CLI
process makes one call and exits, so building one per call costs nothing extra; a server that
built one per HTTP request repeated that exchange on every request, on the operator's personal
key. :class:`GatewayPool` keeps one handshaken gateway (and its tool list) for as long as the
API key and the exposure policy stay the same, and drops it after a failure that suggests the
session or the tool list is stale, so the next call builds a new one.

:class:`ReplyCache` keeps the last full ``get-pr-review-findings`` reply per PR for a few
seconds, bounded in entries and bytes, for the paged form of ``findings list`` only: paging
through one PR then makes one upstream call instead of one per page. A write to findings through
the bridge clears it; an error is never stored.

Both are turned on by :func:`qodo.api.share_upstream` (``qodo-cli serve`` does it); without
that, nothing here is used. Standard library only; every method is safe to call from the
server's worker threads.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from qodo.cli._errors import CliError

__all__ = [
    "FINDINGS_MAX_BYTES",
    "FINDINGS_MAX_ENTRIES",
    "FINDINGS_TTL_SECONDS",
    "GatewayPool",
    "ReplyCache",
]

#: How long a PR's findings stay cached for paging.
FINDINGS_TTL_SECONDS = 30.0
#: At most this many PRs' replies are kept, and at most this many bytes of them in all.
FINDINGS_MAX_ENTRIES = 32
FINDINGS_MAX_BYTES = 16 * 1024 * 1024

#: ``(policy, api_key) -> gateway``: how the pool builds a gateway.
Factory = Callable[[Any, str], Any]


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


class ReplyCache:
    """A small time-limited cache of JSON replies: bounded in entries and bytes, oldest out."""

    def __init__(
        self,
        *,
        ttl: float = FINDINGS_TTL_SECONDS,
        max_entries: int = FINDINGS_MAX_ENTRIES,
        max_bytes: int = FINDINGS_MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl, self._max_entries, self._max_bytes = ttl, max_entries, max_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._items: OrderedDict[Any, tuple[float, int, Any]] = OrderedDict()
        self.size_bytes = 0

    def get(self, key: Any) -> Any:
        """A copy of the live entry for ``key``, else None."""
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if self._clock() >= item[0]:
                self._drop(key)
                return None
            return copy.deepcopy(item[2])

    def put(self, key: Any, value: Any) -> None:
        """Keep a copy of ``value`` (a reply larger than the whole cache is not kept)."""
        size = _size(value)
        if size > self._max_bytes:
            return
        stored = (self._clock() + self._ttl, size, copy.deepcopy(value))
        with self._lock:
            if key in self._items:
                self._drop(key)
            self._items[key] = stored
            self.size_bytes += size
            while len(self._items) > self._max_entries or self.size_bytes > self._max_bytes:
                self._drop(next(iter(self._items)))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.size_bytes = 0

    def _drop(self, key: Any) -> None:
        self.size_bytes -= self._items.pop(key)[1]


def _identity(policy: Any, key: str) -> tuple[str, tuple[Any, ...]]:
    """What a gateway may be shared under: the key (as a digest) and the policy's entries."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest(), tuple(policy.entries)


class _Shared:
    """The pool's gateway seen by one caller: ``call`` forwards, and a failure that suggests a
    stale session or tool list makes the pool forget the gateway."""

    def __init__(self, pool: GatewayPool, gateway: Any, identity: Any) -> None:
        self._pool, self._gateway, self.identity = pool, gateway, identity

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gateway, name)

    def call(self, name: str, arguments: Any = None, *, surface: str | None = None) -> Any:
        try:
            return self._gateway.call(name, arguments, surface=surface)
        except CliError:
            if getattr(self._gateway, "stale", True):
                self._pool.forget(self._gateway)
            raise
        except Exception:
            self._pool.forget(self._gateway)
            raise


class GatewayPool:
    """One handshaken gateway per (API key, exposure policy), rebuilt when it goes stale.

    ``gateway()`` reads the key and the policy as a fresh gateway would, on every call, so a
    changed key or policy file is never served by the old gateway; when they change, the
    findings cache is cleared too.
    """

    def __init__(self, factory: Factory, cache: ReplyCache) -> None:
        self._factory = factory
        self.cache = cache
        self._lock = threading.Lock()
        self._gateway: Any = None
        self._identity: Any = None

    def gateway(self) -> _Shared:
        from qodo.cli import _qodo_api
        from qodo.exposure import load_policy

        policy = load_policy()
        key = _qodo_api.resolve_api_key(_qodo_api.load_qodo_config())
        identity = _identity(policy, key)
        with self._lock:
            if self._gateway is None or self._identity != identity:
                if self._identity is not None and self._identity != identity:
                    self.cache.clear()
                self._gateway, self._identity = self._factory(policy, key), identity
            return _Shared(self, self._gateway, identity)

    def forget(self, gateway: Any) -> None:
        """Drop ``gateway`` if it is still the shared one: the next call builds a new one."""
        with self._lock:
            if self._gateway is gateway:
                self._gateway = None
