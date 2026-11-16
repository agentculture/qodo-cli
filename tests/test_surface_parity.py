"""Surface parity: every registry verb is a CLI command, an MCP tool and an HTTP operation, on
exactly the surfaces the registry says it is on.

Cited from culture-rules tests/test_surface_parity.py @ b7f861f690b57c4355f81371cc02ad1dd2ae9cac
(cite-don't-import: this repo owns its copy) and from associate's adaptation of it
(tests/test_surface_parity.py @ e332ee715d1b5f89f9e76bec25b266e6cff589c7). Adapted: the verbs are
``qodo.registry.default_registry()``'s; a verb is not on every surface by design (its ``surfaces``
say where), so the rule is per surface: where the registry puts a verb its operation must exist
with the same param names and the same required set as the verb's schema; where it does not, the
operation must be absent; and no surface has an operation no verb declares.

The test is data-driven: it names no verb, so adding one needs no edit here; only a surface that
lacks it (or has it by mistake) fails. Each surface is introspected for real:

* CLI: the argparse tree built by ``_build_parser()``, under the registry's nouns (an alias of a
  noun is the same group): each verb parser's actions give the accepted names (``dest``) and the
  required ones;
* MCP: a real in-memory client session's ``list_tools()``, each tool's ``inputSchema``;
* HTTP: the real FastAPI app's routes, and each operation's request-body schema in its OpenAPI
  document.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("mcp")

from fastapi.routing import APIRoute  # noqa: E402

from qodo import mcp_tools  # noqa: E402
from qodo import registry as reg  # noqa: E402
from qodo.bridge import mcpserver  # noqa: E402
from qodo.bridge.filetokens import FileTokenStore  # noqa: E402
from qodo.bridge.tokens import ServiceTokens  # noqa: E402
from qodo.cli import _build_parser  # noqa: E402
from qodo.registry import Verb  # noqa: E402
from qodo.server.app import TOKEN_PREFIX, create_app  # noqa: E402

#: CLI commands under a registry noun that are not registry verbs (self-description only).
LOCAL_ONLY_CLI = frozenset({"overview"})
#: HTTP operations that are not registry verbs: the fixed read endpoints (``GET /`` is the
#: no-UI answer; the app is built here with no UI).
LOCAL_ONLY_HTTP = frozenset(
    {"GET /", "GET /health", "GET /verbs", "GET /changes", "GET /openapi.json"}
)
SURFACES = ("cli", "mcp", "api")
LABEL = {"cli": "CLI", "mcp": "MCP tool", "api": "HTTP operation"}

Params = dict[str, bool]  # accepted name -> required


def op_key(surface: str, verb: Verb) -> str:
    """How each surface names a verb's operation."""
    if surface == "cli":
        return verb.verb
    if surface == "mcp":
        return f"{verb.noun}_{verb.name}"
    return f"POST /verbs/{verb.noun}/{verb.name}"


def schema_params(schema: dict[str, Any], *, skip_deprecated: bool = False) -> Params:
    required = set(schema.get("required", []))
    return {
        name: name in required
        for name, prop in schema.get("properties", {}).items()
        if not (skip_deprecated and isinstance(prop, dict) and prop.get("deprecated"))
    }


def wanted_params(surface: str, verb: Verb) -> Params:
    # a property the schema marks deprecated has no CLI flag by design (see qodo/cli/_build.py)
    return schema_params(verb.params, skip_deprecated=surface == "cli")


# --------------------------------------------------------------------------- the three surfaces


def collect_cli(parser: argparse.ArgumentParser, nouns: list[str]) -> dict[str, Params]:
    """``{"noun verb": {dest: required}}`` for every command the argparse tree has under the
    registry's nouns (exempting :data:`LOCAL_ONLY_CLI`)."""
    out: dict[str, Params] = {}
    seen: set[int] = set()
    for top in parser._actions:
        if not isinstance(top, argparse._SubParsersAction):
            continue
        for noun, noun_parser in top.choices.items():
            if noun not in nouns or id(noun_parser) in seen:  # an alias is the same group
                continue
            seen.add(id(noun_parser))
            for inner in noun_parser._actions:
                if not isinstance(inner, argparse._SubParsersAction):
                    continue
                for name, verb_parser in inner.choices.items():
                    if name in LOCAL_ONLY_CLI:
                        continue
                    out[f"{noun} {name}"] = {
                        a.dest: bool(a.required)
                        for a in verb_parser._actions
                        if a.dest not in ("help", "json") and a.option_strings != ["-h", "--help"]
                    }
    return out


def collect_mcp(tools: Any) -> dict[str, Params]:
    """``{tool name: {param: required}}`` from a real in-memory session's ``list_tools()``."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go() -> list[Any]:
        server = mcpserver.build_server(mcp_tools.SERVER_NAME, tools)
        async with create_connected_server_and_client_session(server) as session:
            return (await session.list_tools()).tools

    return {t.name: schema_params(t.inputSchema) for t in asyncio.run(go())}


def make_app(tmp_path: Any, registry: reg.Registry | None = None) -> Any:
    tokens = ServiceTokens(FileTokenStore(tmp_path / "tokens.json"), prefix=TOKEN_PREFIX)
    return create_app(tokens=tokens, registry=registry, web_dist=None)


def collect_http(app: Any) -> dict[str, Params]:
    """``{"METHOD /path": {param: required}}`` for every route of the real FastAPI app, from the
    request-body schema of its OpenAPI operation (a route without one has no params)."""
    document = app.openapi()
    out: dict[str, Params] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            operation = document["paths"].get(route.path, {}).get(method.lower(), {})
            body = operation.get("requestBody", {}).get("content", {}).get("application/json", {})
            out[f"{method} {route.path}"] = schema_params(body.get("schema", {}))
    return out


# --------------------------------------------------------------------------- the parity check


def parity_failures(
    verbs: list[Verb], found: dict[str, dict[str, Params]], *, local_only: dict[str, frozenset]
) -> list[str]:
    """Every way a verb is missing from, present on, or different on a surface, and every
    operation a surface has that no verb declares. Empty means parity holds."""
    failures: list[str] = []
    for verb in verbs:
        for surface in SURFACES:
            key, ops = op_key(surface, verb), found[surface]
            label = f"{verb.verb}: {{}} the {LABEL[surface]} ({key})"
            if surface in verb.surfaces:
                if key not in ops:
                    failures.append(label.format("missing from"))
                elif ops[key] != wanted_params(surface, verb):
                    failures.append(
                        f"{verb.verb}: {LABEL[surface]} params {ops[key]} differ from "
                        f"{wanted_params(surface, verb)}"
                    )
            elif key in ops:
                failures.append(label.format("not on the registry's surfaces but is on"))
    for surface in SURFACES:
        every = {op_key(surface, v) for v in verbs}
        for key in sorted(set(found[surface]) - every - local_only[surface]):
            failures.append(f"{LABEL[surface]} '{key}' has no registry verb")
    return failures


def surfaces(app: Any, registry: reg.Registry) -> dict[str, dict[str, Params]]:
    return {
        "cli": collect_cli(_build_parser(), registry.nouns()),
        "mcp": collect_mcp(mcp_tools.tools(registry)),
        "api": collect_http(app),
    }


LOCAL_ONLY = {"cli": frozenset(), "mcp": frozenset(), "api": LOCAL_ONLY_HTTP}


def check(app: Any, registry: reg.Registry | None = None, extra: list[Verb] | None = None):
    registry = registry or reg.default_registry()
    return parity_failures(
        [*registry.verbs(), *(extra or [])], surfaces(app, registry), local_only=LOCAL_ONLY
    )


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def a_verb_on_every_surface() -> Verb:
    return next(v for v in reg.default_registry().verbs() if set(SURFACES) <= set(v.surfaces))


# --------------------------------------------------------------------------- tests


def test_the_registry_is_not_empty_and_covers_every_kind_of_surface_set():
    verbs = reg.default_registry().verbs()
    assert verbs
    assert len({v.path for v in verbs}) == len(verbs)
    sets = {tuple(sorted(v.surfaces)) for v in verbs}
    assert any(set(SURFACES) <= set(s) for s in sets)  # one on every surface
    assert any(not set(SURFACES) <= set(s) for s in sets)  # and verbs that are not, by design


def test_every_registry_verb_is_where_the_registry_says_with_the_params_it_declares(app):
    assert check(app) == []


def test_the_introspection_sees_real_operations_and_params(app):
    found = surfaces(app, reg.default_registry())
    for verb in reg.default_registry().verbs():
        for surface in SURFACES:
            key = op_key(surface, verb)
            assert (key in found[surface]) == (surface in verb.surfaces), (surface, key)
            if surface in verb.surfaces:
                assert found[surface][key] == wanted_params(surface, verb), (surface, key)
    verbs = [v for v in reg.default_registry().verbs() if v.params.get("required")]
    assert verbs  # some verb has a required param, and the check saw it as required
    for surface in SURFACES:
        v = next(v for v in verbs if surface in v.surfaces)
        assert True in found[surface][op_key(surface, v)].values()
    assert LOCAL_ONLY_HTTP <= set(found["api"])


def test_the_local_only_exemptions_are_not_stale(app):
    nouns = reg.default_registry().nouns()
    cli_names = set()
    for top in _build_parser()._actions:
        if isinstance(top, argparse._SubParsersAction):
            for noun in nouns:
                for inner in getattr(top.choices.get(noun), "_actions", []):
                    if isinstance(inner, argparse._SubParsersAction):
                        cli_names |= set(inner.choices)
    assert LOCAL_ONLY_CLI <= cli_names
    assert LOCAL_ONLY_CLI.isdisjoint({v.name for v in reg.default_registry().verbs()})
    assert LOCAL_ONLY_HTTP <= set(collect_http(app))


# ----- mutation checks: remove a surface for a real verb, and the check fails for that reason


def test_a_verb_with_no_cli_command_fails_for_that_reason(app, monkeypatch):
    verb = a_verb_on_every_surface()
    real = _build_parser

    def without():
        parser = real()
        for top in parser._actions:
            if isinstance(top, argparse._SubParsersAction):
                for inner in top.choices[verb.noun]._actions:
                    if isinstance(inner, argparse._SubParsersAction):
                        inner.choices.pop(verb.name)
        return parser

    monkeypatch.setattr(f"{__name__}._build_parser", without)
    assert check(app) == [f"{verb.verb}: missing from the CLI ({verb.verb})"]


def test_a_verb_with_no_mcp_tool_fails_for_that_reason(app, monkeypatch):
    verb = a_verb_on_every_surface()
    real = mcp_tools.tools
    name = op_key("mcp", verb)
    monkeypatch.setattr(
        mcp_tools, "tools", lambda *a, **kw: [t for t in real(*a, **kw) if t.name != name]
    )
    assert check(app) == [f"{verb.verb}: missing from the MCP tool ({name})"]


def test_a_verb_with_no_http_operation_fails_for_that_reason(app):
    verb = a_verb_on_every_surface()
    path = f"/verbs/{verb.noun}/{verb.name}"
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != path]
    assert check(app) == [f"{verb.verb}: missing from the HTTP operation (POST {path})"]


# ----- the other direction: an operation where the registry does not put the verb


def _without_surface(verb: Verb, surface: str) -> reg.Registry:
    """The default registry with ``verb`` no longer declared on ``surface``."""
    registry = reg.Registry()
    for v in reg.default_registry().verbs():
        registry.add(
            replace(v, surfaces=tuple(s for s in v.surfaces if s != surface)) if v is verb else v
        )
    return registry


@pytest.mark.parametrize("surface", SURFACES)
def test_a_verb_present_on_a_surface_it_is_not_declared_on_fails(app, surface):
    verb = a_verb_on_every_surface()
    failures = parity_failures(
        _without_surface(verb, surface).verbs(),
        surfaces(app, reg.default_registry()),
        local_only=LOCAL_ONLY,
    )
    assert failures == [
        f"{verb.verb}: not on the registry's surfaces but is on the "
        f"{LABEL[surface]} ({op_key(surface, verb)})"
    ]


# ----- a fake verb added to the registry, lacking one surface


def _fake_verb() -> Verb:
    return Verb(
        noun="findings",
        name="frob",
        summary="A fake verb.",
        kind="local",
        params={
            "type": "object",
            "properties": {"thing": {"type": "string"}},
            "required": ["thing"],
        },
        writes=False,
        surfaces=SURFACES,
        handler=lambda params=None, *, surface="cli": {},
    )


def _complete(app: Any, verb: Verb) -> dict[str, dict[str, Params]]:
    found = surfaces(app, reg.default_registry())
    for surface in SURFACES:
        found[surface][op_key(surface, verb)] = {"thing": True}
    return found


def test_a_fake_verb_on_every_surface_passes(app):
    verb = _fake_verb()
    assert (
        parity_failures(
            [*reg.default_registry().verbs(), verb], _complete(app, verb), local_only=LOCAL_ONLY
        )
        == []
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_a_fake_verb_lacking_one_surface_fails_for_exactly_that_reason(app, surface):
    verb = _fake_verb()
    found = _complete(app, verb)
    del found[surface][op_key(surface, verb)]
    assert parity_failures(
        [*reg.default_registry().verbs(), verb], found, local_only=LOCAL_ONLY
    ) == [f"{verb.verb}: missing from the {LABEL[surface]} ({op_key(surface, verb)})"]


# ----- params must match, and an operation no verb declares must fail


@pytest.mark.parametrize("surface", SURFACES)
def test_a_surface_accepting_other_params_fails(app, surface):
    verb = a_verb_on_every_surface()
    found = surfaces(app, reg.default_registry())
    key = op_key(surface, verb)
    found[surface][key] = {**found[surface][key], "extra": False}
    failures = parity_failures(reg.default_registry().verbs(), found, local_only=LOCAL_ONLY)
    assert len(failures) == 1
    assert (
        failures[0].startswith(f"{verb.verb}: {LABEL[surface]} params") and "differ" in failures[0]
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_a_surface_with_another_required_set_fails(app, surface):
    verb = next(
        v
        for v in reg.default_registry().verbs()
        if set(SURFACES) <= set(v.surfaces) and v.params["properties"]
    )
    found = surfaces(app, reg.default_registry())
    key = op_key(surface, verb)
    name = next(iter(found[surface][key]))
    found[surface][key] = {**found[surface][key], name: not found[surface][key][name]}
    failures = parity_failures(reg.default_registry().verbs(), found, local_only=LOCAL_ONLY)
    assert len(failures) == 1 and "differ" in failures[0]


def test_an_mcp_tool_no_verb_declares_fails(app, monkeypatch):
    real = mcp_tools.tools
    extra = mcpserver.Tool("frob_thing", "x", {"type": "object"}, lambda a: {})
    monkeypatch.setattr(mcp_tools, "tools", lambda *a, **kw: [*real(*a, **kw), extra])
    assert check(app) == ["MCP tool 'frob_thing' has no registry verb"]


def test_a_route_no_verb_declares_fails(app):
    app.add_api_route("/verbs/frob/thing", lambda: {}, methods=["POST"])
    app.add_api_route("/elsewhere", lambda: {}, methods=["GET"])
    assert check(app) == [
        "HTTP operation 'GET /elsewhere' has no registry verb",
        "HTTP operation 'POST /verbs/frob/thing' has no registry verb",
    ]


def test_a_cli_command_no_verb_declares_fails(app, monkeypatch):
    noun = reg.default_registry().nouns()[0]
    real = _build_parser

    def with_frob():
        parser = real()
        for top in parser._actions:
            if isinstance(top, argparse._SubParsersAction):
                for inner in top.choices[noun]._actions:
                    if isinstance(inner, argparse._SubParsersAction):
                        inner.add_parser("frob")
        return parser

    monkeypatch.setattr(f"{__name__}._build_parser", with_frob)
    assert check(app) == [f"CLI '{noun} frob' has no registry verb"]


def test_a_policy_with_a_new_verb_is_checked_with_no_edit_here(tmp_path, monkeypatch):
    """A registry built from another policy is held to the same rule: its verb must be on the
    surfaces the policy names, and (the shipped CLI and tests do not know it) its CLI command is
    built by the registry the parser reads."""
    from qodo import exposure

    policy = exposure.parse_policy(
        {
            "version": 1,
            "tools": [
                {"tool": "rules-metadata", "verb": "catalogue facts", "surfaces": ["api", "mcp"]}
            ],
        }
    )
    registry = reg.build_registry(policy)
    monkeypatch.setattr(reg, "default_registry", lambda: registry)
    app = make_app(tmp_path, registry)
    assert check(app, registry) == []
