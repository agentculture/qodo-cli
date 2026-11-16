"""The upstream gateway: a client for Qodo's remote MCP server, behind an exposure policy.

Transport is JSON-RPC over MCP Streamable HTTP, built on ``urllib`` (standard library only). The
reply to a POST is a ``text/event-stream`` whose last ``data:`` line holds the response (a plain
JSON reply is handled too); the server returns no session id, so there is nothing to carry. The
exchange is ``initialize``, ``notifications/initialized``, then ``tools/list`` (following
``nextCursor``) or ``tools/call``. Recorded in ``docs/qodo-mcp-tools.md``.

Default deny: a tool the :mod:`qodo.exposure` policy does not name is refused, and a tool whose
upstream annotation is not ``readOnlyHint: true`` (or has no annotation, or is not offered) is
refused unless the policy marks it ``allow_write``. The endpoint must be HTTPS and the API key is
never put in an error message, a repr or a log.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from qodo.cli import _qodo_api
from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from qodo.exposure import ExposurePolicy, is_read_only, load_policy

DEFAULT_ENDPOINT = "https://sdk.qodo.ai/v1/tools/mcp/"
PROTOCOL_VERSION = "2025-03-26"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
MAX_LIST_PAGES = 50
_CLIENT_INFO = {"name": "qodo-cli", "version": "0"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would carry the Authorization header to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _default_opener() -> Any:
    return urllib.request.build_opener(_NoRedirect())


class Gateway:
    """List Qodo's upstream tools and call them by name, through the exposure policy."""

    def __init__(
        self,
        policy: ExposurePolicy | None = None,
        *,
        api_key: str | None = None,
        endpoint: str = DEFAULT_ENDPOINT,
        opener: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message="refusing to call a non-HTTPS Qodo MCP endpoint",
                remediation="the endpoint must be an https:// URL",
            )
        self.policy = policy if policy is not None else load_policy()
        self._endpoint = endpoint
        self._key = (
            api_key
            if api_key is not None
            else _qodo_api.resolve_api_key(_qodo_api.load_qodo_config())
        )
        self._opener = opener if opener is not None else _default_opener()
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._next_id = 0
        self._ready = False
        self._tools: dict[str, dict[str, Any]] | None = None

    def __repr__(self) -> str:
        return f"Gateway(endpoint={self._endpoint!r})"

    # --- transport ---------------------------------------------------------

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "[redacted]") if self._key else text

    def _env_error(self, message: str, remediation: str = "") -> CliError:
        return CliError(code=EXIT_ENV_ERROR, message=self._redact(message), remediation=remediation)

    def _post(self, message: dict[str, Any]) -> bytes:
        request = urllib.request.Request(  # nosec B310 - https asserted in __init__
            self._endpoint,
            data=json.dumps(message).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            resp = self._opener.open(request, timeout=self._timeout)
            try:
                raw = resp.read(self._max_bytes + 1)
            finally:
                resp.close()
        except urllib.error.HTTPError as err:
            hint = (
                "check QODO_API_KEY"
                if err.code in (401, 403)
                else "try again, or check Qodo's status"
            )
            raise self._env_error(f"Qodo MCP server answered HTTP {err.code}", hint) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as err:
            raise self._env_error(
                f"cannot reach the Qodo MCP server: {type(err).__name__}",
                "check the network connection",
            ) from None
        if len(raw) > self._max_bytes:
            raise self._env_error(
                f"Qodo MCP reply too large (over {self._max_bytes} bytes)", "report this upstream"
            )
        return raw

    def _decode(self, raw: bytes, request_id: int) -> dict[str, Any]:
        text = raw.decode("utf-8", errors="replace")
        candidates: list[dict[str, Any]] = []
        data: list[str] = []
        for line in [*text.splitlines(), ""]:
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif line == "" and data:
                candidates.append(_json_or_none("\n".join(data)))
                data = []
        if not candidates:  # a plain JSON reply
            candidates.append(_json_or_none(text))
        messages = [m for m in candidates if isinstance(m, dict)]
        for m in reversed(messages):
            if m.get("id") == request_id and ("result" in m or "error" in m):
                return m
        raise self._env_error("Qodo MCP reply held no JSON-RPC response", "report this upstream")

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        reply = self._decode(self._post(message), self._next_id)
        error = reply.get("error")
        if error is not None:
            detail = error.get("message", "") if isinstance(error, dict) else ""
            raise self._env_error(f"Qodo MCP {method} failed: {detail}".rstrip(": "))
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    def _handshake(self) -> None:
        if self._ready:
            return
        self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": _CLIENT_INFO},
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._ready = True

    # --- tools ---------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Every upstream tool (``name``, ``description``, ``inputSchema``, ``annotations``)."""
        self._handshake()
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_LIST_PAGES):
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in result.get("tools", []) if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        else:
            raise self._env_error("Qodo MCP tools/list did not end", "report this upstream")
        self._tools = {t["name"]: t for t in tools if isinstance(t.get("name"), str)}
        return tools

    def _upstream(self, name: str) -> dict[str, Any]:
        if self._tools is None:
            self.list_tools()
        assert self._tools is not None
        tool = self._tools.get(name)
        if tool is None:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"tool {name!r} is not offered by the Qodo MCP server",
                remediation="re-probe the server; the tool may have been renamed or removed",
            )
        return tool

    def _check_arguments(self, name: str, tool: dict[str, Any], arguments: dict[str, Any]) -> None:
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            return
        missing = [k for k in schema.get("required", []) if k not in arguments]
        if missing:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"{name}: missing required argument(s) {', '.join(missing)}",
                remediation="pass every required argument",
            )
        props = schema.get("properties")
        if schema.get("additionalProperties") is False and isinstance(props, dict):
            extra = sorted(set(arguments) - set(props))
            if extra:
                raise CliError(
                    code=EXIT_USER_ERROR,
                    message=f"{name}: unknown argument(s) {', '.join(extra)}",
                    remediation=f"known arguments: {', '.join(sorted(props))}",
                )

    def call(
        self, name: str, arguments: dict[str, Any] | None = None, *, surface: str | None = None
    ) -> dict[str, Any]:
        """Call an upstream tool by name; returns its ``structuredContent``.

        Refused before any call when the policy does not name the tool, when it is not offered
        upstream, when ``surface`` (``cli``, ``api`` or ``mcp``; every surface passes its own)
        is not one the policy lists for it, or when it is not read-only and the policy does not
        allow the write. An
        ``isError`` reply raises :class:`CliError` (exit 1).
        """
        arguments = dict(arguments or {})
        entry = self.policy.require(name)
        if surface is not None and surface not in entry.surfaces:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"tool {name!r} is not exposed on the {surface} surface",
                remediation="add the surface to its policy entry to expose it there",
            )
        tool = self._upstream(name)
        if not is_read_only(tool.get("annotations")) and not entry.allow_write:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"tool {name!r} is not read-only and the policy does not allow the write",
                remediation='set "allow_write": true on its policy entry to permit it',
            )
        self._check_arguments(name, tool, arguments)
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        structured = result.get("structuredContent")
        if result.get("isError"):
            raise CliError(
                code=EXIT_USER_ERROR,
                message=self._redact(f"{name} failed: {_error_text(structured, result)}"),
                remediation="check the arguments, or retry if the upstream is down",
            )
        if isinstance(structured, dict):
            return structured
        return {"content": result.get("content", [])}

    # --- the rules search API (the older HTTP path) --------------------------------

    def search_rules_api(
        self, query: str, *, top_k: int = _qodo_api.DEFAULT_TOP_K, scopes: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Wrap the rules search API (``<base>/rules/search``); ranked rules, as ``rules_get``.

        It is the same capability as the ``rules-search`` tool, so the policy must name that
        tool or the search is refused."""
        self.policy.require("rules-search")
        return _qodo_api.search_rules(query, top_k=top_k, scopes=scopes)


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _error_text(structured: Any, result: dict[str, Any]) -> str:
    if isinstance(structured, dict) and isinstance(structured.get("error"), dict):
        err = structured["error"]
        return f"{err.get('code', 'error')}: {err.get('message', '')}".rstrip(": ")
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            return block["text"][:300]
    return "the tool reported an error"
