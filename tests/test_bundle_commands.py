"""Every ``qodo-cli <verb>`` the culture-rules bundle quotes exists in the real argument parser.

The bundle (``docs/rules/qodo``: the actors', workflows' and rules' JSON and the README) tells
an operator and an agent which commands to run. A quoted command whose verb path or flag the CLI
does not have (``qodo-cli tokens mint`` once) only fails when someone types it, so each quote is
walked through :func:`qodo.cli._build_parser`: every word after ``qodo-cli`` that names a
subcommand must be one, a noun group must be followed by one of its verbs, and every ``--flag``
that follows the verb must be one of that verb's options. Unlike ``test_culture_rules_bundle``,
this needs no culture-rules install.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from qodo.cli import _build_parser

BUNDLE = Path(__file__).resolve().parent.parent / "docs" / "rules" / "qodo"
#: ``qodo-cli`` used as a name in prose, not as a command (``the qodo-cli bridge``).
PROSE = frozenset({"bridge", "service"})
_QUOTE = re.compile(r"qodo-cli((?: +(?:--)?[a-z][a-z0-9-]*(?: +(?:\{\{[^}]*\}\}|<[^>]+>))?)+)")


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _texts() -> list[tuple[str, str]]:
    found = [
        (str(p.relative_to(BUNDLE)), s)
        for p in sorted(BUNDLE.rglob("*.json"))
        for s in _strings(json.loads(p.read_text(encoding="utf-8")))
    ]
    found.append(("README.md", (BUNDLE / "README.md").read_text(encoding="utf-8")))
    return found


def _quotes() -> list[tuple[str, list[str]]]:
    out = []
    for where, text in _texts():
        for match in _QUOTE.finditer(" ".join(text.replace("`", "").split())):
            words = match.group(1).split()
            if words[0] not in PROSE:
                out.append((where, words))
    return out


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _check(words: list[str]) -> str | None:
    """None when ``words`` (after ``qodo-cli``) walk the parser; else what is wrong."""
    parser, path = _build_parser(), []
    rest = list(words)
    while rest and _subcommands(parser):
        word = rest.pop(0)
        if word not in _subcommands(parser):
            return f"no verb {word!r} under {' '.join(['qodo-cli', *path])}"
        parser = _subcommands(parser)[word]
        path.append(word)
    if _subcommands(parser):
        return f"{' '.join(['qodo-cli', *path])} needs a verb"
    for word in rest:
        if word.startswith("--") and word not in parser._option_string_actions:
            return f"no option {word} on qodo-cli {' '.join(path)}"
    return None


QUOTES = _quotes()


def test_the_bundle_quotes_commands_at_all():
    assert len(QUOTES) >= 8, QUOTES
    assert any(w[:2] == ["tokens", "issue"] for _, w in QUOTES)


@pytest.mark.parametrize("where,words", QUOTES, ids=[" ".join(w)[:60] for _, w in QUOTES])
def test_every_quoted_command_parses(where, words):
    assert _check(words) is None, f"{where}: qodo-cli {' '.join(words)}: {_check(words)}"


@pytest.mark.parametrize(
    "words",
    [["tokens", "mint"], ["tokens"], ["findings", "list", "--no-such-flag"], ["nope"]],
)
def test_the_checker_refuses_a_command_that_does_not_exist(words):
    assert _check(words) is not None
