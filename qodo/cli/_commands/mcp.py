"""``qodo-cli mcp`` — serve the registry's verbs as MCP tools over stdio (the ``mcp`` extra).

One tool per verb on the registry's ``mcp`` surface (``findings_list``, ``rules_search``, ...),
calling the same handlers as the CLI and the HTTP API. Stdout is the protocol channel, so
nothing else is ever written there; a missing ``mcp`` package is an environment error (exit 2)
naming the extra, never a traceback. See ``qodo-cli explain mcp``.
"""

from __future__ import annotations

import argparse

from qodo.cli._errors import EXIT_ENV_ERROR, CliError
from qodo.cli._output import add_json_flag, emit_diagnostic


def cmd_mcp(args: argparse.Namespace) -> int:
    from qodo import mcp_tools
    from qodo.bridge import mcpserver

    try:
        if not getattr(args, "json", False):
            emit_diagnostic("serving qodo-cli MCP tools over stdio")
        mcpserver.run_stdio(
            mcp_tools.SERVER_NAME, mcp_tools.tools(), extra_hint=mcp_tools.EXTRA_HINT
        )
    except mcpserver.ServerExtraMissing as err:
        raise CliError(
            code=EXIT_ENV_ERROR, message=str(err), remediation=mcp_tools.EXTRA_HINT
        ) from err
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "mcp",
        help="Serve the registry's verbs as MCP tools over stdio (needs the 'mcp' extra).",
    )
    add_json_flag(p)
    p.set_defaults(func=cmd_mcp)
