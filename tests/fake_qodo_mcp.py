"""A fake Qodo MCP server behind the opener interface, built from the recorded fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "qodo_mcp"
REPLIES = {
    "get-pr-review-findings": "get_pr_review_findings.json",
    "rules-search": "rules_search.json",
    "rules-list": "rules_list.json",
    "rules-get": "rules_get.json",
    "rules-metadata": "rules_metadata.json",
    "pull-request-review-history": "pull_request_review_history.json",
}


def schemas() -> dict[str, Any]:
    return json.loads((FIXTURES / "tool_schemas.json").read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, ctype: str = "text/event-stream") -> None:
        self._body, self.status = body, status
        self.headers = {"Content-Type": ctype}
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def close(self) -> None:
        self.closed = True


def sse(message: dict[str, Any]) -> bytes:
    return b"event: message\r\ndata: " + json.dumps(message).encode() + b"\r\n\r\n"


class FakeUpstream:
    """``open(request, timeout=...)`` answers like the probed server; records what it saw."""

    def __init__(self, *, tools: dict[str, Any] | None = None, page_size: int | None = None):
        self.tools = schemas() if tools is None else tools
        self.page_size = page_size
        self.requests: list[Any] = []  # (urllib Request, parsed body)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.override: dict[str, Any] = {}  # method -> FakeResponse | Exception

    def open(self, request: Any, timeout: float | None = None) -> FakeResponse:
        body = json.loads(request.data.decode())
        self.requests.append((request, body))
        method = body["method"]
        if method in self.override:
            out = self.override[method]
            if isinstance(out, Exception):
                raise out
            return out
        if "id" not in body:
            return FakeResponse(b"", status=202, ctype="text/plain")
        rid = body["id"]
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "serverInfo": {"name": "qodo-managed-tools"}}
        elif method == "tools/list":
            result = self._list(body.get("params", {}).get("cursor"))
        elif method == "tools/call":
            name, args = body["params"]["name"], body["params"].get("arguments", {})
            self.calls.append((name, args))
            fixture = REPLIES.get(name)
            if fixture is None:
                result = {"isError": False, "structuredContent": {"ok": True}, "content": []}
            else:
                result = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
        else:
            return FakeResponse(
                sse({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nope"}})
            )
        return FakeResponse(sse({"jsonrpc": "2.0", "id": rid, "result": result}))

    def _list(self, cursor: str | None) -> dict[str, Any]:
        items = [{"name": n, **v} for n, v in self.tools.items()]
        if self.page_size is None:
            return {"tools": items}
        start = int(cursor or 0)
        page = items[start : start + self.page_size]
        out: dict[str, Any] = {"tools": page}
        if start + self.page_size < len(items):
            out["nextCursor"] = str(start + self.page_size)
        return out
