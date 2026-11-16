"""The registry's verbs as MCP tools: one per verb on the ``mcp`` surface.

A tool is named ``<noun>_<name>`` (``findings_list``) and takes the verb's own params schema as
its input schema. Its handler is the verb's handler called with ``surface="mcp"``, so the
gateway's surface rule applies (an upstream tool the exposure policy does not list for ``mcp`` is
refused at call time even if a registry listed it) and the change log's actor is ``mcp``. A
verb the policy leaves off the ``mcp`` surface, or a tool it does not name, has no tool at all.

A :class:`qodo.cli._errors.CliError` becomes a tool error ``<message> (hint: <remediation>)``;
nothing else about a failure leaves the process (the generic server reports an unexpected
exception by its class only). The API key is never part of a result or an error: the gateway
never puts it in a message, and nothing here adds one.

The generic server is :mod:`qodo.bridge.mcpserver`; this module is the app wiring. Standard
library only (the SDK is imported by the bridge, lazily).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qodo.bridge.mcpserver import Tool, ToolError
from qodo.cli._errors import CliError
from qodo.registry import Registry, Verb, default_registry

__all__ = ["EXTRA_HINT", "SERVER_NAME", "SURFACE", "tool_error", "tool_name", "tools"]

SURFACE = "mcp"
SERVER_NAME = "qodo-cli"
EXTRA_HINT = "install it: pip install 'qodo-cli[mcp]' (or uv sync --extra mcp)"


def tool_name(verb: Verb) -> str:
    return f"{verb.noun}_{verb.name}"


def tool_error(err: CliError) -> ToolError:
    """The tool error for a refused or failed call: its message and remediation, no more."""
    text = err.message
    if err.remediation:
        text += f" (hint: {err.remediation})"
    return ToolError(text)


def _handler(verb: Verb) -> Callable[[dict[str, Any]], Any]:
    def run(arguments: dict[str, Any]) -> Any:
        try:
            return verb.handler(arguments, surface=SURFACE)
        except CliError as err:
            raise tool_error(err) from None

    return run


def _description(verb: Verb) -> str:
    return f"{verb.summary} ({'writes to Qodo' if verb.writes else 'read-only'})"


def tools(registry: Registry | None = None) -> list[Tool]:
    """One tool per verb of ``registry`` (default: the shipped one, read now) on the mcp
    surface."""
    registry = registry if registry is not None else default_registry()
    return [
        Tool(
            name=tool_name(verb),
            description=_description(verb),
            input_schema=verb.params,
            handler=_handler(verb),
        )
        for verb in registry.on_surface(SURFACE)
    ]
