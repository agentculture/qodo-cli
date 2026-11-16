"""The verb registry: every bridge verb, registered once; every surface reads it.

Cited (cite-don't-import) from culture-rules' ``culture_rules/cli/registry.py`` (the registry
shape: a ``Verb`` with a name, a params schema and a mutating flag, a ``Registry`` that refuses a
duplicate path, and one object the CLI, ``learn``, ``explain`` and later the API and MCP surfaces
all enumerate), itself cited from agentfront 0.20.0. This copy is owned by qodo-cli and may
diverge: it is standard library only, and a verb here also says where it comes from and which
surfaces carry it.

Two kinds of verb sit beside each other:

* ``upstream``: one for each tool the exposure policy (:mod:`qodo.exposure`) names, under the verb
  the policy gives it. Its params schema is the tool's recorded input schema (``tool_schemas.json``,
  a packaged snapshot of the policy's tools, so building the registry or the CLI's argparse tree
  needs no network call; ``scripts/export_tool_schemas.py`` rewrites it from the recorded
  ``tools/list`` reply). Its handler checks the params against that schema, then calls the gateway
  for the caller's surface, so the policy is enforced again at call time.
* ``local``: the verbs that do not go through the gateway (the older rules search over HTTP, the
  ``gh`` driven review triage, the repo reviewer config).

A handler takes a params ``dict`` (and the caller's ``surface``, default ``"cli"``) and returns a
plain JSON-ready object; a failure is a :class:`qodo.cli._errors.CliError`. Standard library only.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from qodo.exposure import ExposurePolicy, is_read_only, load_policy

__all__ = [
    "Registry",
    "Verb",
    "build_registry",
    "default_registry",
    "load_schema_snapshot",
    "summarise_findings",
    "validate_params",
]

SNAPSHOT_PATH = Path(__file__).with_name("tool_schemas.json")
Handler = Callable[..., Any]

# Short summaries for the shipped policy's tools; another policy falls back to the description.
_SUMMARIES = {
    "get-pr-review-findings": "Qodo's findings on a pull request, by its URL.",
    "pr-review-mark-implemented": "Mark findings implemented (a write: the fix has landed).",
    "pr-review-dismiss": "Dismiss findings with a reason (a write: no code change).",
    "rules-search": "Semantic search of the org's Qodo rules (MCP).",
    "rules-list": "List the org's Qodo rules, filtered.",
    "rules-get": "One Qodo rule by id.",
    "rules-metadata": "The values rules filters accept (categories, severities, states).",
    "pull-request-review-history": "Past review suggestions for a file or directory of a repo.",
    "pull-request-similar-suggestions": "Past review suggestions similar to a description.",
    "pull-request-stats": "What PR history Qodo has indexed for a repo.",
}

OPEN_STATUSES = frozenset(
    {"pending", "partial_implementation", "not_implemented", "focus_areas_edited"}
)
CLOSED_STATUSES = frozenset(
    {"full_implementation", "dismissed", "detected_after_merge", "outdated"}
)
_MAX_TEXT = 300


@dataclass
class Verb:
    """One registered verb."""

    noun: str
    name: str
    summary: str
    kind: str  # "upstream" (a tool the policy exposes) or "local"
    params: dict[str, Any]  # a JSON Schema object
    writes: bool
    surfaces: tuple[str, ...]
    handler: Handler
    tool: str | None = None  # the upstream tool of an "upstream" verb
    cli_generated: bool = False  # True: the CLI command is built from ``params``, not hand-written

    def __post_init__(self) -> None:
        if self.kind not in ("upstream", "local"):
            raise ValueError(f"unknown verb kind {self.kind!r}")

    @property
    def path(self) -> tuple[str, str]:
        return (self.noun, self.name)

    @property
    def verb(self) -> str:
        return f"{self.noun} {self.name}"


class Registry:
    """The verbs, keyed by ``(noun, name)``; a path is registered once."""

    def __init__(self) -> None:
        self._verbs: dict[tuple[str, str], Verb] = {}

    def add(self, verb: Verb) -> Verb:
        if verb.path in self._verbs:
            raise ValueError(f"verb {verb.verb!r} is already registered")
        self._verbs[verb.path] = verb
        return verb

    def extend(self, verbs: Iterable[Verb]) -> None:
        for verb in verbs:
            self.add(verb)

    def get(self, noun: str, name: str) -> Verb | None:
        return self._verbs.get((noun, name))

    def verbs(self, noun: str | None = None) -> list[Verb]:
        return [v for v in self._verbs.values() if noun is None or v.noun == noun]

    def nouns(self) -> list[str]:
        return list(dict.fromkeys(v.noun for v in self._verbs.values()))

    def on_surface(self, surface: str) -> list[Verb]:
        return [v for v in self._verbs.values() if surface in v.surfaces]


# --- the schema snapshot --------------------------------------------------------------------------


def load_schema_snapshot(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """The packaged input schemas of the policy's tools: ``{tool: {description, annotations,
    inputSchema}}``."""
    target = Path(path) if path else SNAPSHOT_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        tools = data["tools"]
        if not isinstance(tools, dict):
            raise TypeError("tools is not an object")
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot read the tool schema snapshot {target}: {err}",
            remediation="run `python3 scripts/export_tool_schemas.py --write`",
        ) from err
    return tools


# --- params validation (the JSON Schema subset the recorded schemas use) --------------------------


def _bad(
    message: str, remediation: str = "see `qodo-cli explain <verb>` for the parameters"
) -> CliError:
    return CliError(code=EXIT_USER_ERROR, message=message, remediation=remediation)


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "null":
        return value is None
    return True


def _check_value(where: str, value: Any, schema: dict[str, Any]) -> None:
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else [k for k in kinds or [] if isinstance(k, str)]
    if kinds and not any(_type_ok(value, k) for k in kinds):
        raise _bad(f"{where} must be of type {' or '.join(kinds)}")
    if "enum" in schema and value not in schema["enum"]:
        raise _bad(
            f"{where} must be one of {', '.join(str(e) for e in schema['enum'])}",
            "pass one of the allowed values",
        )
    if isinstance(value, str):
        if not value.strip():
            raise _bad(f"{where} must not be blank")
        if len(value) > schema.get("maxLength", len(value)):
            raise _bad(f"{where} is longer than {schema['maxLength']} characters")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise _bad(f"{where} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise _bad(f"{where} must be at most {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise _bad(f"{where} must be greater than {schema['exclusiveMinimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise _bad(f"{where} needs at least {schema['minItems']} item(s)")
        if len(value) > schema.get("maxItems", len(value)):
            raise _bad(f"{where} takes at most {schema['maxItems']} item(s), got {len(value)}")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                _check_value(f"{where}[{i}]", item, schema["items"])


def validate_params(schema: dict[str, Any], params: dict[str, Any]) -> None:
    """Check ``params`` against a verb's params schema; a violation is a user error (exit 1)."""
    props = schema.get("properties") or {}
    missing = [k for k in schema.get("required", []) if k not in params]
    if missing:
        raise _bad(
            f"missing required parameter(s): {', '.join(missing)}", "pass every required parameter"
        )
    if schema.get("additionalProperties") is False:
        extra = sorted(set(params) - set(props))
        if extra:
            raise _bad(
                f"unknown parameter(s): {', '.join(extra)}",
                f"known parameters: {', '.join(sorted(props))}",
            )
    for name, value in params.items():
        if name in props:
            _check_value(name, value, props[name])


def _given(params: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if v is not None}


# --- findings list --summary ----------------------------------------------------------------------


def _clip(value: Any) -> Any:
    return value[:_MAX_TEXT] if isinstance(value, str) else value


def summarise_findings(reply: dict[str, Any]) -> dict[str, Any]:
    """Counts, ids, statuses and URLs only: what ``findings list --summary`` prints.

    A finding is kept as ``{id, status}`` plus its ``url`` when upstream gave one; titles,
    descriptions and every other field are dropped, so the size grows only with the count.
    """
    findings = [f for f in reply.get("findings") or [] if isinstance(f, dict)]
    rows: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    for finding in findings:
        status = str(finding.get("attribution_status") or finding.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        row: dict[str, Any] = {"id": _clip(finding.get("id")), "status": _clip(status)}
        url = finding.get("url") or finding.get("html_url")
        if isinstance(url, str) and url:
            row["url"] = _clip(url)
        rows.append(row)
    n_open = sum(n for s, n in by_status.items() if s in OPEN_STATUSES)
    n_closed = sum(n for s, n in by_status.items() if s in CLOSED_STATUSES)
    out: dict[str, Any] = {
        "pr_url": _clip(reply.get("pr_url")),
        "finding_count": reply.get("finding_count", len(rows)),
        "counts": {
            "total": len(rows),
            "open": n_open,
            "closed": n_closed,
            "other": len(rows) - n_open - n_closed,
            "by_status": dict(sorted(by_status.items())),
        },
        "findings": rows,
    }
    session = reply.get("review_session")
    if isinstance(session, dict):
        out["review_session"] = {
            k: _clip(session[k]) for k in ("status", "commit_sha", "run_id") if k in session
        }
    return out


def _check_pr_url(given: dict[str, Any]) -> None:
    from urllib.parse import urlparse

    url = str(given.get("pr_url", ""))
    if urlparse(url).scheme not in ("http", "https"):
        raise _bad(
            f"pr_url must be a PR URL, got {url!r}",
            "pass the pull request's web URL, e.g. https://github.com/org/repo/pull/1",
        )


# Per-verb extras for an upstream verb: local params (never sent upstream), a check before the
# call, and a reducer for the reply. Keyed by the upstream tool.
_LOCAL_PARAMS: dict[str, dict[str, Any]] = {
    "get-pr-review-findings": {
        "summary": {
            "type": "boolean",
            "description": "Print counts, ids, statuses and URLs only (a small reply).",
        }
    }
}
_PREFLIGHT: dict[str, Callable[[dict[str, Any]], None]] = {"get-pr-review-findings": _check_pr_url}
_REDUCERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], Any]] = {
    "get-pr-review-findings": lambda reply, local: (
        summarise_findings(reply) if local.get("summary") else reply
    )
}


def _log_finding_status(status: str) -> Callable[[dict[str, Any], str], None]:
    def record(given: dict[str, Any], surface: str) -> None:
        from qodo import changelog  # lazy, as the handler's own import

        changelog.record_finding_status(
            list(given.get("finding_ids") or []), status, reason=given.get("reason"), actor=surface
        )

    return record


#: What a successful call of a write tool records in the change log (``qodo.changelog``).
_AFTER: dict[str, Callable[[dict[str, Any], str], None]] = {
    "pr-review-mark-implemented": _log_finding_status("implemented"),
    "pr-review-dismiss": _log_finding_status("dismissed"),
}


# --- the upstream verbs ---------------------------------------------------------------------------


def _upstream_handler(tool: str, schema: dict[str, Any]) -> Handler:
    local_names = tuple(_LOCAL_PARAMS.get(tool, {}))
    preflight = _PREFLIGHT.get(tool)
    reduce = _REDUCERS.get(tool)
    after = _AFTER.get(tool)

    def handler(params: dict[str, Any] | None = None, *, surface: str = "cli") -> Any:
        from qodo import api  # lazy: api imports the gateway, which imports the cli package

        given = _given(params)
        validate_params(schema, given)
        local = {k: given.pop(k) for k in local_names if k in given}
        if preflight is not None:
            preflight(given)
        reply = api._gateway().call(tool, given, surface=surface)
        if after is not None:
            after(given, surface)
        return reduce(reply, local) if reduce is not None else reply

    return handler


def _summary_of(tool: str, spec: dict[str, Any]) -> str:
    if tool in _SUMMARIES:
        return _SUMMARIES[tool]
    first = str(spec.get("description") or tool).strip().splitlines()[0]
    return first if len(first) <= 120 else first[:117].rstrip() + "..."


def _split_verb(verb: str) -> tuple[str, str]:
    parts = verb.split()
    if len(parts) != 2:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"invalid exposure policy: verb {verb!r} must be '<noun> <name>'",
            remediation="fix the exposure policy file (see qodo/exposure_default.json)",
        )
    return parts[0], parts[1]


def _upstream_verbs(policy: ExposurePolicy, schemas: dict[str, dict[str, Any]]) -> Iterable[Verb]:
    for entry in policy.entries:
        spec = schemas.get(entry.tool)
        if not isinstance(spec, dict) or not isinstance(spec.get("inputSchema"), dict):
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"no recorded input schema for the exposed tool {entry.tool!r}",
                remediation="re-record the tool schemas and run "
                "`python3 scripts/export_tool_schemas.py --write`",
            )
        noun, name = _split_verb(entry.verb)
        source = spec["inputSchema"]
        props = {**source.get("properties", {}), **_LOCAL_PARAMS.get(entry.tool, {})}
        schema = {
            "type": "object",
            "properties": props,
            "required": list(source.get("required", [])),
            "additionalProperties": False,
        }
        yield Verb(
            noun=noun,
            name=name,
            summary=_summary_of(entry.tool, spec),
            kind="upstream",
            params=schema,
            writes=not is_read_only(spec.get("annotations")),
            surfaces=entry.surfaces,
            handler=_upstream_handler(entry.tool, schema),
            tool=entry.tool,
            cli_generated=True,
        )


# --- the local verbs ------------------------------------------------------------------------------


def _obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _rules_get(params: dict[str, Any] | None = None, *, surface: str = "cli") -> Any:
    from qodo import api

    given = _given(params)
    schema = _LOCAL_SCHEMAS[("rules", "get")]
    validate_params(schema, given)
    scopes = api.rules_scopes(scopes=given.get("scopes"), auto_scope=not given.get("no_scope"))
    rules = api.rules_get(
        given["query"], top_k=given.get("top_k", api.DEFAULT_TOP_K), scopes=scopes, auto_scope=False
    )
    return {"query": given["query"], "scopes": scopes, "count": len(rules), "rules": rules}


def _review_list(params: dict[str, Any] | None = None, *, surface: str = "cli") -> Any:
    from qodo import api

    given = _given(params)
    validate_params(_LOCAL_SCHEMAS[("review", "list")], given)
    return api.review_list(pr=given.get("pr"), kind=given.get("kind", "all"))


def _review_resolve(params: dict[str, Any] | None = None, *, surface: str = "cli") -> Any:
    from qodo import api

    given = _given(params)
    validate_params(_LOCAL_SCHEMAS[("review", "resolve")], given)
    result = api.review_resolve(
        list(given.get("ids", [])),
        pr=given.get("pr"),
        all=bool(given.get("all")),
        severity=given.get("severity"),
        reply=given.get("reply"),
        resolve_thread=not given.get("no_resolve_thread"),
    )
    return {key: result[key] for key in ("pr", "count", "ok", "resolved")}


def _config_show(params: dict[str, Any] | None = None, *, surface: str = "cli") -> Any:
    from qodo import api

    validate_params(_LOCAL_SCHEMAS[("config", "show")], _given(params))
    return api.config_show()


def _config_validate(params: dict[str, Any] | None = None, *, surface: str = "cli") -> Any:
    from qodo import api

    validate_params(_LOCAL_SCHEMAS[("config", "validate")], _given(params))
    return api.config_validate()


def _config_init(params: dict[str, Any] | None = None, *, surface: str = "cli") -> Any:
    from qodo import api

    given = _given(params)
    validate_params(_LOCAL_SCHEMAS[("config", "init")], given)
    return api.config_init(force=bool(given.get("force")))


def _config_propose(params: dict[str, Any] | None = None, *, surface: str = "cli") -> Any:
    """The name is registered; its handler belongs to a later task."""
    raise CliError(
        code=EXIT_USER_ERROR,
        message="config propose is not implemented yet",
        remediation="use `qodo-cli config show` and `config validate` for now",
    )


_LOCAL_SCHEMAS: dict[tuple[str, str], dict[str, Any]] = {
    ("rules", "get"): _obj(
        {
            "query": {"type": "string", "description": "The task or concern to search rules for."},
            "top_k": {"type": "integer", "minimum": 1, "description": "Max rules to request."},
            "scopes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Repository or module scopes (default: from the git origin).",
            },
            "no_scope": {"type": "boolean", "description": "Omit scope: skip the git detection."},
        },
        ("query",),
    ),
    ("review", "list"): _obj(
        {
            "pr": {"type": "integer", "minimum": 1, "description": "PR number (default: branch)."},
            "kind": {"type": "string", "enum": ["all", "summary", "inline"]},
        }
    ),
    ("review", "resolve"): _obj(
        {
            "ids": {"type": "array", "items": {"type": "string"}, "description": "Comment ids."},
            "pr": {"type": "integer", "minimum": 1, "description": "PR number (default: branch)."},
            "all": {"type": "boolean", "description": "Resolve every inline Qodo comment."},
            "severity": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            "reply": {"type": "string", "description": "A reply to post before acknowledging."},
            "no_resolve_thread": {"type": "boolean", "description": "Skip thread resolution."},
        }
    ),
    ("config", "show"): _obj({}),
    ("config", "validate"): _obj({}),
    ("config", "init"): _obj({"force": {"type": "boolean", "description": "Overwrite files."}}),
    ("config", "propose"): _obj({}),
}

# noun, name, summary, handler, writes, surfaces, cli_generated. The surfaces of a local verb
# are the cli only, except the rules search (a read over HTTP): the others read or write the
# working directory or drive ``gh`` as the caller, which a server or an MCP client must not.
_LOCAL_VERBS = (
    ("rules", "get", "Semantic-search the rules API (the older HTTP path).", _rules_get, False,
     ("cli", "api", "mcp"), False),
    ("review", "list", "List the Qodo bot's PR comments.", _review_list, False, ("cli",), False),
    ("review", "resolve", "Reply to, acknowledge and resolve Qodo comments.", _review_resolve,
     True, ("cli",), False),
    ("config", "show", "Show the repo reviewer config.", _config_show, False, ("cli",), False),
    ("config", "validate", "Validate the repo reviewer config.", _config_validate, False,
     ("cli",), False),
    ("config", "init", "Scaffold a minimal reviewer config.", _config_init, True, ("cli",), False),
    ("config", "propose", "Propose a reviewer config change (not implemented yet).",
     _config_propose, True, ("cli",), True),
)  # fmt: skip


def _local_verbs() -> Iterable[Verb]:
    for noun, name, summary, handler, writes, surfaces, generated in _LOCAL_VERBS:
        yield Verb(
            noun=noun,
            name=name,
            summary=summary,
            kind="local",
            params=_LOCAL_SCHEMAS[(noun, name)],
            writes=writes,
            surfaces=surfaces,
            handler=handler,
            cli_generated=generated,
        )


# --- building -------------------------------------------------------------------------------------


def build_registry(
    policy: ExposurePolicy | None = None, schemas: dict[str, dict[str, Any]] | None = None
) -> Registry:
    """The local verbs plus one verb per tool ``policy`` exposes.

    ``schemas`` maps a tool to ``{description, annotations, inputSchema}``: the packaged snapshot
    by default, or a live ``tools/list`` result an integrator turned into that shape.
    """
    policy = policy if policy is not None else load_policy()
    schemas = schemas if schemas is not None else load_schema_snapshot()
    registry = Registry()
    registry.extend(_local_verbs())
    for verb in _upstream_verbs(policy, schemas):
        try:
            registry.add(verb)
        except ValueError:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"invalid exposure policy: verb {verb.verb!r} is already a local verb",
                remediation="give the tool another verb in the exposure policy",
            ) from None
    return registry


_DEFAULT: Registry | None = None


def default_registry() -> Registry:
    """The registry for the shipped exposure policy and the packaged schema snapshot."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = build_registry()
    return _DEFAULT
