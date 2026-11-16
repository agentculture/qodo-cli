"""The HTTP API: a FastAPI app generated from the verb registry.

Route scheme (one operation per verb on the ``api`` surface; nothing is written by hand per verb):

- ``POST /verbs/<noun>/<name>``: the verb's params as a JSON object body (an empty body means no
  params); the reply is the verb's result as JSON. The verb's params schema is the operation's
  request schema in ``api/openapi.json``. One method and one body shape for every verb is what an
  HTTP step of a rules engine (a JSON body in, a JSON reply of under 64 KiB out) and a browser
  ``fetch`` both call most simply; a read verb is a POST because its params (a list of ids, a
  nested filter) do not fit a query string. A verb that is not on the ``api`` surface, or whose
  tool the exposure policy does not name for it, has no route at all.
  A body must be sent as ``application/json`` (415 otherwise: it keeps another origin from
  riding a browser's Access cookie into a write without a CORS preflight).
- ``GET /health``: public; service name, version and the key status (``present``, ``state``,
  ``days_left``), never the key.
- ``GET /verbs``: the registry listing (names, summaries, params schemas, writes, role).
- ``GET /changes``: the newest change-log entries (``?limit=``, 1 to 500, default 50).
- ``GET /openapi.json``: the schema this module renders into ``api/openapi.json``.

Roles (:func:`build_policy`): a read verb needs ``viewer``; a verb that writes (``writes=True``)
needs ``editor``; ``config propose`` and ``fix propose`` need ``editor`` as well (see
:data:`ROLE_OVERRIDES`);
``/verbs``, ``/changes`` and ``/openapi.json`` need ``viewer``; ``/health`` is public; anything
unlisted needs ``admin``. The credential and role are checked in a middleware, before any handler
runs: no credential is 401, a role that is too low is 403. Errors are one JSON envelope,
``{"error": {"code", "message", "remediation", "errors"}}``, never a traceback; every reply
carries ``Cache-Control: no-store`` (except the built UI's own files).

Handlers call the registry's handlers (which call :mod:`qodo.api`) with ``surface="api"`` and
nothing else. FastAPI is imported inside :func:`create_app` only (the ``server`` extra).
"""

# No `from __future__ import annotations`: FastAPI reads the handlers' annotations at runtime,
# and `Request` is imported inside create_app (the lazy `server` extra).
import json
import logging
import re
from pathlib import Path
from typing import Any

from qodo import api
from qodo.bridge.policy import RoutePolicy
from qodo.bridge.principal import AuthError
from qodo.bridge.resolve import AuthSettings, Resolver, TokenAuthenticator
from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from qodo.registry import Registry, Verb, default_registry
from qodo.server.tokens import TOKEN_PREFIX

__all__ = [
    "API_VERSION",
    "DEFAULT_UI",
    "MAX_BODY_BYTES",
    "ROLE_OVERRIDES",
    "SERVICE",
    "TOKEN_PREFIX",
    "build_policy",
    "create_app",
    "default_web_dist",
    "render_openapi",
    "role_for",
    "route_path",
]

SERVICE = "qodo-cli"
#: The version of the HTTP contract (``api/openapi.json``), not of the package: the pinned
#: schema must not change on a release that leaves the contract alone.
API_VERSION = "1"
#: A request body is at most this many bytes (a verb's params are small: 100 finding ids are
#: under 5 KiB).
MAX_BODY_BYTES = 64 * 1024
MAX_CHANGES = 500
DEFAULT_CHANGES = 50
#: A verb whose role is not simply "writes need editor". ``config propose`` writes nothing to
#: the checkout, but it reads files from the bridge machine's disk (under the allowlisted roots,
#: for the allowlisted repos) and returns their contents, and a success appends to the change
#: log: a deliberate grant (editor) rather than the default every read-only token carries.
#: ``fix propose`` is the same gate for an agent's code fix, and needs the same grant.
ROLE_OVERRIDES: dict[tuple[str, str], str] = {
    ("config", "propose"): "editor",
    ("fix", "propose"): "editor",
}

_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_LOG = logging.getLogger("qodo.server")
DEFAULT_UI = object()

_ERROR_REF = {"$ref": "#/components/schemas/Error"}
_ERRORS = {
    "400": "The params are not valid for the verb (or the body is not a JSON object).",
    "401": "No credential, or an unknown or revoked one.",
    "403": "The credential's role is too low for this route.",
    "413": "The body is over the size cap.",
    "415": "A body that is not sent as application/json.",
    "500": "An unexpected failure (no detail is returned).",
    "503": "Qodo, or this server's own configuration, could not complete the call.",
}


def route_path(verb: Verb) -> str:
    """``/verbs/<noun>/<name>``; a name that is not a plain lower-case token is refused."""
    for part in (verb.noun, verb.name):
        if not _SEGMENT.match(part):
            raise ValueError(f"verb {verb.verb!r} cannot be a route: {part!r} is not a plain name")
    return f"/verbs/{verb.noun}/{verb.name}"


def role_for(verb: Verb) -> str:
    """The role a verb's route needs."""
    if verb.path in ROLE_OVERRIDES:
        return ROLE_OVERRIDES[verb.path]
    return "editor" if verb.writes else "viewer"


def _write_note(verb: Verb) -> str:
    if verb.kind == "upstream":
        return "A write: it changes Qodo's state. "
    return "A write (the summary says what it changes; never a push, merge or pull request). "


def default_web_dist() -> Path:
    """Where the built web UI lives inside the installed package (it may not exist yet)."""
    return Path(__file__).resolve().parent.parent / "web_dist"


def build_policy(registry: Registry | None = None) -> RoutePolicy:
    """Route -> role for this app: the verb routes by :func:`role_for`, the read endpoints at
    ``viewer``, ``/health`` public, everything else ``admin``."""
    registry = registry if registry is not None else default_registry()
    policy = RoutePolicy(default="admin")
    policy.public("/health", "GET")
    for path in ("/", "/verbs", "/changes", "/openapi.json"):
        policy.add("GET", path, "viewer")
    for verb in registry.on_surface("api"):
        policy.add("POST", route_path(verb), role_for(verb))
    return policy


def render_openapi(document: dict[str, Any]) -> str:
    """The text of ``api/openapi.json``: sorted keys, two-space indent, a trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class _Refused(Exception):
    """A request this module refuses itself (a body problem): status, code, message."""

    def __init__(self, status: int, code: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.remediation = status, code, message, remediation


def _envelope(code: str, message: str, remediation: str = "") -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "errors": []}
    if remediation:
        error["remediation"] = remediation
    return {"error": error}


def create_app(
    *,
    tokens: TokenAuthenticator,
    settings: AuthSettings | None = None,
    registry: Registry | None = None,
    web_dist: Any = DEFAULT_UI,
) -> Any:
    """The FastAPI app for ``registry`` (default: the shipped one).

    ``tokens`` checks bearer credentials; ``settings`` says which listener this is (LAN: service
    tokens only; loopback: also Cloudflare Access). ``web_dist`` is the built web UI directory
    (default: the one inside the installed package; ``None`` or a missing one: API only).
    """
    from fastapi import FastAPI, Request
    from fastapi.openapi.utils import get_openapi
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from qodo import __version__  # read per call: the health route reports the live version
    from qodo.bridge import static
    from qodo.bridge.caching import NoStoreByDefault

    registry = registry if registry is not None else default_registry()
    verbs = registry.on_surface("api")
    policy = build_policy(registry)
    resolver = Resolver(settings or AuthSettings(), tokens)
    ui_dir = default_web_dist() if web_dist is DEFAULT_UI else web_dist

    app = FastAPI(title="qodo-cli API", docs_url=None, redoc_url=None, openapi_url=None)

    def reply(status: int, payload: Any, headers: dict[str, str] | None = None) -> Any:
        return JSONResponse(payload, status_code=status, headers=headers)

    def fail(
        status: int, code: str, message: str, remediation: str = "", headers: Any = None
    ) -> Any:
        return reply(status, _envelope(code, message, remediation), headers)

    def cli_failure(err: CliError) -> Any:
        if err.code == EXIT_USER_ERROR:
            return fail(400, "user_error", err.message, err.remediation)
        if err.code == EXIT_ENV_ERROR:
            return fail(503, "environment_error", err.message, err.remediation)
        return fail(500, "internal_error", "the call failed")

    def unexpected(where: str, exc: Exception) -> Any:
        # the class only: a message may carry a path or a value from the caller's request
        _LOG.error("%s failed: %s", where, type(exc).__name__)
        return fail(500, "internal_error", "unexpected error; nothing more is disclosed")

    @app.middleware("http")
    async def authorize(request: Request, call_next: Any) -> Any:
        """The credential and the role, before any handler (and so before any body is read)."""
        method, path = request.method, request.url.path
        try:
            if policy.required_role(method, path) is not None:
                principal = resolver.resolve(dict(request.headers.items()))
                policy.check(principal, method, path)
                request.state.principal = principal
        except AuthError as err:
            headers = {"WWW-Authenticate": "Bearer"} if err.status == 401 else None
            return fail(err.status, err.code, err.message, headers=headers)
        return await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> Any:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return fail(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def last_resort(_: Request, exc: Exception) -> Any:
        return unexpected("request", exc)

    async def read_params(request: Request) -> dict[str, Any]:
        """The JSON object body, read with a hard size cap (a chunked body too)."""
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            raise _Refused(
                413, "body_too_large", f"the body must be at most {MAX_BODY_BYTES} bytes"
            )
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise _Refused(
                    413, "body_too_large", f"the body must be at most {MAX_BODY_BYTES} bytes"
                )
            chunks.append(chunk)
        raw = b"".join(chunks)
        if not raw.strip():
            return {}
        # a body must say it is JSON: a browser cannot send that cross-site without a CORS
        # preflight, so a page on another origin cannot ride a viewer's Access cookie into a write
        media = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media != "application/json" and not media.endswith("+json"):
            raise _Refused(
                415,
                "unsupported_media_type",
                "send the params as a JSON object with Content-Type: application/json",
            )
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise _Refused(400, "bad_body", "the body must be a JSON object of the verb's params")
        return body

    def verb_endpoint(verb: Verb) -> Any:
        async def endpoint(request: Request) -> Any:
            try:
                params = await read_params(request)
                result = await run_in_threadpool(verb.handler, params, surface="api")
            except _Refused as err:
                return fail(err.status, err.code, err.message, err.remediation)
            except CliError as err:
                return cli_failure(err)
            except Exception as exc:  # noqa: BLE001 - never a traceback to the caller
                return unexpected(verb.verb, exc)
            return reply(200, result)

        endpoint.__name__ = f"{verb.noun}_{verb.name}"
        return endpoint

    @app.get("/health", operation_id="health", summary="Service health (public)")
    def health() -> Any:
        key = api.key_status()
        bad = key["state"] in ("missing", "expired", "invalid")
        return {
            "status": "degraded" if bad else "ok",
            "service": SERVICE,
            "version": __version__,
            "key": {k: key[k] for k in ("present", "state", "days_left")},
        }

    @app.get("/verbs", operation_id="verbs", summary="The verbs this server serves")
    def list_verbs() -> Any:
        return {
            "verbs": [
                {
                    "noun": v.noun,
                    "name": v.name,
                    "summary": v.summary,
                    "kind": v.kind,
                    "writes": v.writes,
                    "role": role_for(v),
                    "method": "POST",
                    "path": route_path(v),
                    "params": v.params,
                }
                for v in verbs
            ]
        }

    @app.get(
        "/changes",
        operation_id="changes",
        summary="The newest change-log entries",
        openapi_extra={
            "parameters": [
                {
                    "name": "limit",
                    "in": "query",
                    "required": False,
                    "schema": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_CHANGES,
                        "default": DEFAULT_CHANGES,
                    },
                }
            ]
        },
    )
    async def changes(request: Request) -> Any:
        raw = request.query_params.get("limit", str(DEFAULT_CHANGES))
        if not raw.isdigit() or not 1 <= int(raw) <= MAX_CHANGES:
            return fail(400, "user_error", f"limit must be a whole number from 1 to {MAX_CHANGES}")
        try:
            entries = await run_in_threadpool(api.changes, int(raw))
        except Exception as exc:  # noqa: BLE001
            return unexpected("changes", exc)
        return {"entries": entries}

    @app.get("/openapi.json", operation_id="openapi", summary="This API's OpenAPI schema")
    def openapi_document() -> Any:
        return app.openapi()

    for verb in verbs:
        app.add_api_route(
            route_path(verb),
            verb_endpoint(verb),
            methods=["POST"],
            operation_id=f"{verb.noun}_{verb.name}",
            summary=verb.summary,
            description=(
                f"Needs the {role_for(verb)} role. "
                + (_write_note(verb) if verb.writes else "")
                + "The body is the verb's params as a JSON object."
            ),
            tags=[verb.noun],
            openapi_extra={
                "requestBody": {
                    "required": False,
                    "content": {"application/json": {"schema": verb.params}},
                },
                "x-role": role_for(verb),
                "x-writes": verb.writes,
            },
        )

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            doc = get_openapi(
                title="qodo-cli API",
                version=API_VERSION,
                summary="Qodo findings, rules and review history over HTTP (unofficial).",
                description=(
                    "One operation per qodo-cli verb on the api surface: POST /verbs/<noun>/<name> "
                    "with the verb's params as a JSON object. Unofficial: not affiliated with "
                    "Qodo."
                ),
                routes=app.routes,
            )
            components = doc.setdefault("components", {})
            components["securitySchemes"] = {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "A service token issued by `qodo-cli tokens issue` (qcl_...).",
                }
            }
            components.setdefault("schemas", {})["Error"] = {
                "type": "object",
                "required": ["error"],
                "properties": {
                    "error": {
                        "type": "object",
                        "required": ["code", "message", "errors"],
                        "properties": {
                            "code": {"type": "string"},
                            "message": {"type": "string"},
                            "remediation": {"type": "string"},
                            "errors": {"type": "array", "items": {}},
                        },
                    }
                },
            }
            for path, item in doc["paths"].items():
                for op in item.values():
                    op["security"] = [] if path == "/health" else [{"bearerAuth": []}]
                    errors = {} if path == "/health" else dict(_ERRORS)
                    if not path.startswith("/verbs/"):
                        errors.pop("413", None)
                        errors.pop("415", None)
                    if path in ("/verbs", "/openapi.json"):
                        errors.pop("400", None)
                    responses = op.setdefault("responses", {})
                    responses.setdefault("200", {"description": "Successful Response"})
                    for status, text in errors.items():
                        responses[status] = {
                            "description": text,
                            "content": {"application/json": {"schema": _ERROR_REF}},
                        }
            app.openapi_schema = doc
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]

    mounted = static.install(app, Path(ui_dir) if ui_dir is not None else None)
    if mounted:
        # the UI's own files (and its client-side routes) are read by any viewer
        policy.add("GET", "/**", "viewer")
    else:

        @app.get("/", include_in_schema=False)
        def no_ui() -> Any:
            return fail(
                404, "not_found", "this server has no web UI; the API is described at /openapi.json"
            )

    # outermost: every answer, the 401 and 403 envelopes included, says it may not be cached
    app.add_middleware(NoStoreByDefault)
    return app
