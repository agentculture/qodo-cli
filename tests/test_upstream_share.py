"""The long-lived server shares one handshaken gateway and caches a PR's findings for paging.

Before: every verb call built a new gateway (``initialize``, ``notifications/initialized`` and
``tools/list`` before each ``tools/call``), and every page of ``findings list --offset --limit``
fetched the whole PR again. After :func:`qodo.api.share_upstream` (what ``qodo-cli serve`` turns
on), calls reuse one gateway per API key and policy, and the paged form reads a short-lived copy
of the last full reply per PR. The CLI never turns it on.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error

import pytest

from qodo import api
from qodo import gateway as gw
from qodo import registry as reg
from qodo import upstream_share as share
from qodo.cli._errors import CliError
from tests.fake_qodo_mcp import FakeUpstream

PR = "https://github.com/o/r/pull/1"
UUID = "123e4567-e89b-12d3-a456-426614174000"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake():
    return FakeUpstream()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def shared(monkeypatch, fake, clock):
    """Sharing on, with every gateway built on the fake upstream; off again afterwards."""
    monkeypatch.setenv("QODO_API_KEY", "key-one")
    monkeypatch.delenv("QODO_EXPOSURE_POLICY", raising=False)
    built: list[gw.Gateway] = []

    def factory(policy, key):
        g = gw.Gateway(policy, api_key=key, opener=fake)
        built.append(g)
        return g

    api.share_upstream(factory=factory, clock=clock)
    yield built
    api.share_upstream(False)


def methods(fake, name):
    return [body for _, body in fake.requests if body.get("method") == name]


def findings_calls(fake):
    return [c for c in fake.calls if c[0] == "get-pr-review-findings"]


def verb(noun, name, params):
    return reg.default_registry().get(noun, name).handler(params, surface="api")


def page(offset=0, limit=1, **extra):
    return verb("findings", "list", {"pr_url": PR, "offset": offset, "limit": limit, **extra})


# --- the shared gateway -------------------------------------------------------------------------


def test_paging_one_pr_makes_one_upstream_call_and_one_handshake(shared, fake):
    total = page()["finding_count"]
    offset, pages = 0, 0
    while offset is not None and pages < 50:
        offset = page(offset=offset, limit=1)["next_offset"]
        pages += 1
    assert pages >= 2 and total >= 2
    assert len(findings_calls(fake)) == 1
    assert len(methods(fake, "initialize")) == 1
    assert len(methods(fake, "notifications/initialized")) == 1
    assert len(methods(fake, "tools/list")) == 1
    assert len(shared) == 1


def test_one_gateway_serves_every_verb_and_the_unpaged_forms_read_fresh(shared, fake):
    page()
    verb("findings", "list", {"pr_url": PR})
    verb("findings", "list", {"pr_url": PR, "summary": True})
    verb("rules", "search", {"query": "x"})
    assert len(findings_calls(fake)) == 3  # the page, then the full and summary forms, fresh
    assert len(methods(fake, "initialize")) == 1 and len(shared) == 1


def test_another_key_gets_another_gateway_and_no_cached_reply(shared, fake, monkeypatch):
    page()
    monkeypatch.setenv("QODO_API_KEY", "key-two")
    page()
    assert len(shared) == 2 and shared[0] is not shared[1]
    assert len(findings_calls(fake)) == 2
    sent = [r.headers["Authorization"] for r, b in fake.requests if b.get("method") == "tools/call"]
    assert sent == ["Bearer key-one", "Bearer key-two"]


def test_another_policy_gets_another_gateway(shared, fake, monkeypatch, tmp_path):
    page()
    narrow = tmp_path / "policy.json"
    narrow.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": [
                    {
                        "tool": "get-pr-review-findings",
                        "verb": "findings list",
                        "surfaces": ["cli"],
                        "allow_write": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QODO_EXPOSURE_POLICY", str(narrow))
    with pytest.raises(CliError, match="not exposed on the api surface"):
        page()  # the new policy refuses it even though the old reply is still cached
    assert len(shared) == 2


def test_a_failed_call_rebuilds_the_gateway_and_is_not_cached(shared, fake):
    fake.override["tools/call"] = urllib.error.HTTPError(PR, 502, "bad", {}, io.BytesIO())
    with pytest.raises(CliError) as err:
        page()
    assert err.value.code == 2
    del fake.override["tools/call"]
    assert page()["finding_count"] >= 1  # retried upstream, on a new gateway
    assert len(shared) == 2
    assert len(methods(fake, "initialize")) == 2
    page(offset=1)
    assert len(findings_calls(fake)) == 1  # the success is cached; the failure never was


def test_a_tool_missing_from_a_stale_tool_list_rebuilds_the_gateway(shared, fake):
    tools = dict(fake.tools)
    fake.tools = {k: v for k, v in tools.items() if k != "rules-search"}
    with pytest.raises(CliError, match="not offered"):
        verb("rules", "search", {"query": "x"})
    fake.tools = tools
    verb("rules", "search", {"query": "x"})
    assert len(shared) == 2


def test_a_tool_error_reply_keeps_the_gateway(shared, fake):
    from tests.fake_qodo_mcp import FakeResponse, sse

    page()
    bad = {"isError": True, "structuredContent": {"error": {"code": "x", "message": "no"}}}
    fake.override["tools/call"] = FakeResponse(sse({"jsonrpc": "2.0", "id": 4, "result": bad}))
    with pytest.raises(CliError):
        verb("findings", "list", {"pr_url": PR})
    assert len(shared) == 1  # a tool's own error says nothing about the session


def test_threads_share_one_handshake(shared, fake):
    errors: list[BaseException] = []

    def work():
        try:
            for _ in range(5):
                verb("rules", "search", {"query": "x"})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(methods(fake, "initialize")) == 1 and len(methods(fake, "tools/list")) == 1
    ids = [b["id"] for _, b in fake.requests if "id" in b]
    assert len(ids) == len(set(ids))  # every JSON-RPC request has its own id


# --- the findings cache -------------------------------------------------------------------------


def test_a_write_through_the_bridge_clears_the_cache(shared, fake):
    page()
    verb("findings", "dismiss", {"finding_ids": [UUID], "reason": "deferred"})
    page()
    verb("findings", "implement", {"finding_ids": [UUID]})
    page()
    api.findings_dismiss([UUID], "deferred")
    page()
    api.findings_implement([UUID])
    page()
    assert len(findings_calls(fake)) == 5


def test_a_failed_write_clears_the_cache_too(shared, fake):
    page()
    fake.override["tools/call"] = urllib.error.HTTPError(PR, 502, "bad", {}, io.BytesIO())
    with pytest.raises(CliError):
        verb("findings", "dismiss", {"finding_ids": [UUID], "reason": "deferred"})
    del fake.override["tools/call"]
    page()
    assert len(findings_calls(fake)) == 2  # the write may have landed: read again


def test_the_cache_expires(shared, fake, clock):
    page()
    clock.now += share.FINDINGS_TTL_SECONDS - 1
    page()
    assert len(findings_calls(fake)) == 1
    clock.now += 2
    page()
    assert len(findings_calls(fake)) == 2


def test_extended_and_plain_pages_are_cached_apart(shared, fake):
    page()
    page(extended=True)
    page(extended=True)
    assert [c[1] for c in findings_calls(fake)] == [
        {"pr_url": PR},
        {"pr_url": PR, "extended": True},
    ]


def test_two_threads_paging_at_once_get_whole_pages(shared, fake, clock):
    full = verb("findings", "list", {"pr_url": PR})
    ids = [f["id"] for f in full["findings"]]
    results: dict[int, list[str]] = {}
    errors: list[BaseException] = []

    def walk(n):
        try:
            seen, offset = [], 0
            while offset is not None:
                out = page(offset=offset, limit=1)
                seen += [f["id"] for f in out["findings"]]
                offset = out["next_offset"]
            results[n] = seen
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    for _ in range(3):
        api.share_upstream(factory=lambda p, k: gw.Gateway(p, api_key=k, opener=fake), clock=clock)
        threads = [threading.Thread(target=walk, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert all(seen == ids for seen in results.values()) and len(results) == 6


def test_the_cache_is_bounded_in_entries_and_bytes(clock):
    cache = share.ReplyCache(ttl=30, max_entries=2, max_bytes=200, clock=clock)
    cache.put("a", {"x": 1})
    cache.put("b", {"x": 2})
    cache.put("c", {"x": 3})
    assert cache.get("a") is None and cache.get("b") == {"x": 2} and cache.get("c") == {"x": 3}
    cache.put("big", {"x": "y" * 500})
    assert cache.get("big") is None  # larger than the whole cache: never stored
    cache.put("d", {"x": "z" * 120})
    cache.put("e", {"x": "w" * 120})
    assert cache.get("d") is None and cache.get("e") is not None  # bytes evict the oldest
    assert cache.size_bytes <= 200


def test_a_cached_reply_cannot_be_changed_through_a_result(shared, fake):
    first = page(limit=50)
    first["findings"].clear()
    assert page(limit=50)["findings"]
    assert len(findings_calls(fake)) == 1


# --- off by default -----------------------------------------------------------------------------


def test_without_sharing_every_call_builds_a_new_gateway(monkeypatch):
    api.share_upstream(False)
    built = []

    class Counting:
        def __init__(self, *a, **k):
            built.append(self)

    monkeypatch.setattr(gw, "Gateway", Counting)
    api._gateway()
    api._gateway()
    assert len(built) == 2


def test_serve_turns_sharing_on(monkeypatch):
    pytest.importorskip("uvicorn")
    from qodo.server import serve as srv

    seen = []
    monkeypatch.setattr(api, "share_upstream", lambda *a, **k: seen.append((a, k)))
    srv.serve([])
    assert seen == [((), {})]


def test_a_reply_fetched_before_a_write_finished_is_never_stored():
    """A read misses the cache and fetches; a write completes and clears the cache; the read's
    reply (from before the write) must not be stored afterwards."""
    cache = share.ReplyCache()
    began = cache.generation  # the read notes this before it fetches
    cache.clear()  # the write finished meanwhile
    cache.put("pr", {"findings": ["before the write"]}, generation=began)
    assert cache.get("pr") is None
    cache.put("pr", {"findings": ["after"]}, generation=cache.generation)
    assert cache.get("pr") == {"findings": ["after"]}


def test_a_write_during_a_paged_read_leaves_no_stale_page(shared, fake, monkeypatch):
    real = api._call
    state = {"wrote": False}

    def call(gateway, tool, arguments, surface):
        reply = real(gateway, tool, arguments, surface)
        if tool == api.FINDINGS_TOOL and not state["wrote"]:
            state["wrote"] = True  # a write lands while this read is still in flight
            api.findings_dismiss([UUID], "deferred")
        return reply

    monkeypatch.setattr(api, "_call", call)
    page()
    page()  # the first read's reply was not stored, so this one reads again
    assert len(findings_calls(fake)) == 2
