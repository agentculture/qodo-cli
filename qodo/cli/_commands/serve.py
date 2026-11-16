"""``qodo-cli serve`` — run the HTTP API (the ``server`` extra).

A loopback listener (always, and always on a loopback address: it is the one that trusts
Cloudflare Access) and, when configured, a LAN listener (service tokens only). Every verb on the
registry's ``api`` surface is one ``POST /verbs/<noun>/<name>``; see ``qodo-cli explain serve``.
"""

from __future__ import annotations

import argparse
import os

from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from qodo.cli._output import add_json_flag, emit_result


def cmd_serve(args: argparse.Namespace) -> int:
    from qodo.bridge.access import AccessConfigError
    from qodo.server import serve as srv
    from qodo.server.tokens import open_tokens

    try:
        srv.require_extra()
        env = os.environ
        specs = srv.plan_listeners(
            env,
            host=args.host,
            port=args.port,
            lan_host=args.lan_host,
            lan_port=args.lan_port,
        )
        listeners = srv.build_listeners(specs, open_tokens(args.tokens), env=env)
    except srv.ServerExtraMissing as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=str(err),
            remediation="install it: pip install 'qodo-cli[server]' (or uv sync --extra server)",
        ) from err
    except (srv.ListenerConfigError, AccessConfigError) as err:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=str(err),
            remediation="see `qodo-cli explain serve` for the flags and QODO_* variables",
        ) from err
    json_mode = bool(getattr(args, "json", False))

    def announce() -> None:
        emit_result(
            (
                {"listening": [{"name": s.name, "host": s.host, "port": s.port} for s in specs]}
                if json_mode
                else "\n".join(f"qodo-cli serve: {s.name} on {s.host}:{s.port}" for s in specs)
            ),
            json_mode=json_mode,
        )

    srv.serve(listeners, log_level=args.log_level, ready=announce)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "serve",
        help="Run the HTTP API (needs the 'server' extra): loopback, and a LAN listener if set.",
    )
    add_json_flag(p)
    p.add_argument("--host", help="loopback listener address (default 127.0.0.1; QODO_HOST)")
    p.add_argument("--port", type=int, help="loopback listener port (default 8792; QODO_PORT)")
    p.add_argument(
        "--lan-host", help="LAN listener address; unset = no LAN listener (QODO_LAN_HOST)"
    )
    p.add_argument("--lan-port", type=int, help="LAN listener port (default 8793; QODO_LAN_PORT)")
    p.add_argument("--tokens", help="service-token file (default: QODO_SERVER_TOKENS or XDG state)")
    p.add_argument(
        "--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"]
    )
    p.set_defaults(func=cmd_serve)
