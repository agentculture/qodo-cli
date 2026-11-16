"""Builds CLI commands from the verb registry (:mod:`qodo.registry`).

Cited from culture-rules' ``culture_rules/cli/_build.py`` (the registry builds the argparse tree
and dispatches to its handlers), trimmed to what qodo-cli needs. A verb the registry marks
``cli_generated`` gets its flags here from its params JSON Schema, with no network call at parse
time (the schema is the registry's packaged snapshot); a verb with a hand-written command (the
older ``rules get``, ``review``, ``config show|validate|init``) is only listed. A verb whose
policy entry leaves out the ``cli`` surface has no command.

Flags: one ``--kebab-name`` per property; ``string`` takes a value (``choices`` from an ``enum``),
``integer`` and ``number`` are typed, ``boolean`` is ``--name`` or ``--no-name``, an ``array``
repeats the flag, and a shape the flags cannot say (an object, a union) takes inline JSON. A
``required`` property is a required flag; a property marked ``deprecated`` upstream gets no flag.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from qodo import registry as _registry
from qodo.cli._errors import EXIT_SUCCESS
from qodo.cli._output import add_json_flag, emit_result
from qodo.registry import Verb

NOUN_HELP = {
    "findings": "Qodo's findings on a pull request: list them, mark implemented, dismiss, or "
    "ask for an agent to fix them.",
    "fix": "Check an agent's committed code fix before culture-rules proposes it as a PR.",
    "history": "What Qodo's PR history says about a file, a similar change, or a repo.",
    "prs": "A repo's pull requests, as Qodo's read-only litegit tool lists them.",
}


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    return name[:-1] if name.endswith("s") else name


def _json_arg(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"not valid JSON: {err}") from err


def _help(prop: dict[str, Any]) -> str:
    text = " ".join(str(prop.get("description", "")).split())
    return text if len(text) <= 160 else text[:157].rstrip() + "..."


def _scalar_type(kind: Any) -> Any:
    return {"integer": int, "number": float}.get(kind) if isinstance(kind, str) else None


def _add_param(parser: argparse.ArgumentParser, name: str, prop: dict[str, Any], required: bool):
    kind = prop.get("type")
    kw: dict[str, Any] = {"dest": name, "help": _help(prop) or None}
    if kind == "boolean":
        # --name / --no-name, so false can be said; left off, the param is not sent
        parser.add_argument(_flag(name), action=argparse.BooleanOptionalAction, default=None, **kw)
        return
    kw["required"] = required
    if kind == "array":
        item = prop.get("items") if isinstance(prop.get("items"), dict) else {}
        kw["action"] = "append"
        if _scalar_type(item.get("type")):
            kw["type"] = _scalar_type(item["type"])
        elif item.get("type") not in (None, "string"):
            kw["type"] = _json_arg
        kw["metavar"] = _singular(name).upper()
    elif isinstance(prop.get("enum"), list):
        kw["choices"] = prop["enum"]
    elif _scalar_type(kind):
        kw["type"] = _scalar_type(kind)
    elif kind != "string":
        kw["type"] = _json_arg
        kw["metavar"] = "JSON"
    parser.add_argument(_flag(name), **kw)


def add_params(parser: argparse.ArgumentParser, schema: dict[str, Any]) -> None:
    required = set(schema.get("required", []))
    for name, prop in schema.get("properties", {}).items():
        if isinstance(prop, dict) and not prop.get("deprecated"):
            _add_param(parser, name, prop, name in required)


def params_from_args(args: argparse.Namespace, schema: dict[str, Any]) -> dict[str, Any]:
    """The params the user gave: a flag left off (``None``) is absent."""
    out: dict[str, Any] = {}
    for name, prop in schema.get("properties", {}).items():
        value = getattr(args, name, None)
        if value is None:
            continue
        out[name] = value
    return out


# --- rendering ------------------------------------------------------------------------------------


def render_summary(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    lines = [
        f"# {summary.get('pr_url') or 'findings'}",
        f"{summary['finding_count']} finding(s): {counts['open']} open, {counts['closed']} closed"
        + (f", {counts['other']} other" if counts["other"] else ""),
    ]
    lines += [f"  {status}: {n}" for status, n in counts["by_status"].items()]
    session = summary.get("review_session")
    if session:
        lines.append("review: " + ", ".join(f"{k}={v}" for k, v in session.items()))
    for row in summary["findings"]:
        lines.append(" ".join(str(row[k]) for k in ("id", "status", "url") if k in row))
    return "\n".join(lines)


def render_text(verb: Verb, params: dict[str, Any], result: Any) -> str:
    if verb.tool == "get-pr-review-findings" and params.get("summary"):
        return render_summary(result)
    return json.dumps(result, indent=2, ensure_ascii=False)


def _command(verb: Verb):
    def run(args: argparse.Namespace) -> int:
        params = params_from_args(args, verb.params)
        result = verb.handler(params, surface="cli")
        if getattr(args, "json", False):
            emit_result(result, json_mode=True)
        else:
            emit_result(render_text(verb, params, result), json_mode=False)
        return EXIT_SUCCESS

    return run


# --- the parser tree ------------------------------------------------------------------------------


def cli_verbs(noun: str | None = None, *, generated_only: bool = False) -> list[Verb]:
    """The registry's verbs on the cli surface (the registry is read at call time)."""
    return [
        v
        for v in _registry.default_registry().on_surface("cli")
        if (noun is None or v.noun == noun) and (v.cli_generated or not generated_only)
    ]


def attach(verb_sub: argparse._SubParsersAction, noun: str) -> None:
    """Add a command under ``noun``'s verb subparsers for each generated verb of the registry."""
    for verb in cli_verbs(noun, generated_only=True):
        parser = verb_sub.add_parser(verb.name, help=verb.summary)
        add_params(parser, verb.params)
        add_json_flag(parser)
        parser.set_defaults(func=_command(verb))


def register_noun(sub: argparse._SubParsersAction, noun: str) -> None:
    """A noun group made only of registry verbs (``findings``, ``history``), with its overview."""
    if not cli_verbs(noun):
        return  # the policy leaves this noun off the cli

    from qodo.cli._commands.overview import emit_overview  # lazy: overview imports this module

    def overview(args: argparse.Namespace) -> int:
        emit_overview(
            f"qodo-cli {noun}",
            noun_sections(noun),
            json_mode=bool(getattr(args, "json", False)),
        )
        return EXIT_SUCCESS

    p = sub.add_parser(noun, help=NOUN_HELP.get(noun, f"The {noun} verbs."))
    add_json_flag(p)
    p.set_defaults(func=overview, json=False)
    verb_sub = p.add_subparsers(dest=f"{noun}_command", parser_class=type(p))
    attach(verb_sub, noun)
    ov = verb_sub.add_parser("overview", help=f"Describe the qodo-cli {noun} noun.")
    ov.add_argument("target", nargs="?", help=f"Ignored: overview describes the {noun} noun.")
    add_json_flag(ov)
    ov.set_defaults(func=overview)


def register_other_nouns(sub: argparse._SubParsersAction, taken: set[str]) -> None:
    """A group for every noun the policy adds beyond the ones ``taken`` already registered, so
    a verb the operator's policy names always has a command."""
    for noun in sorted({v.noun for v in cli_verbs(generated_only=True)} - taken):
        register_noun(sub, noun)


# --- self-description (learn, overview) -----------------------------------------------------------


def verb_line(verb: Verb) -> str:
    """``findings dismiss --finding-ids ID --reason REASON [options]: summary``"""
    props = verb.params.get("properties", {})
    required = [n for n in verb.params.get("required", []) if n in props]
    flags = " ".join(f"{_flag(n)} {n.upper()}" for n in required)
    more = any(n not in required for n, p in props.items() if not p.get("deprecated"))
    usage = " ".join(
        x for x in (f"{verb.noun} {verb.name}", flags, "[options]" if more else "") if x
    )
    kind = "write" if verb.writes else "read-only"
    return f"{usage} — {verb.summary} ({kind})"


def noun_items(noun: str, existing: list[str] | None = None) -> list[str]:
    """Lines for ``noun``'s cli verbs, leaving out the ones ``existing`` already names."""
    text = "\n".join(existing or [])
    return [verb_line(v) for v in cli_verbs(noun) if f"{v.noun} {v.name}" not in text]


def missing_lines(existing_text: str) -> list[str]:
    """Lines for every cli verb whose ``<noun> <name>`` the given text does not mention yet."""
    return [verb_line(v) for v in cli_verbs() if f"{v.noun} {v.name}" not in existing_text]


def noun_sections(noun: str) -> list[dict[str, object]]:
    verbs = cli_verbs(noun)
    conventions = []
    if any(v.kind == "upstream" for v in verbs):
        conventions += [
            "built from the exposure policy: a tool the policy does not name has no verb",
            "calls Qodo's remote MCP server with QODO_API_KEY; supports --json",
            "exit 1 on bad input or a refused call, 2 when Qodo cannot be reached",
        ]
    local = [f"{v.noun} {v.name}" for v in verbs if v.kind == "local"]
    if local:
        conventions += [
            f"{', '.join(local)}: local (no exposure policy, no Qodo call); supports --json; "
            f"`qodo-cli explain <verb>` says what each checks and refuses"
        ]
    return [
        {"title": "Verbs", "items": noun_items(noun) + [f"{noun} overview — describe this noun"]},
        {"title": "Conventions", "items": conventions},
    ]
