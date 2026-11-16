"""Where this app's service tokens live, and the one way the CLI and the API open them.

The store is one file (:class:`qodo.bridge.filetokens.FileTokenStore`, mode 0600, hashes only):
``--tokens PATH`` on the commands, else ``$QODO_SERVER_TOKENS``, else
``$XDG_STATE_HOME/qodo-cli/service_tokens.json`` (``~/.local/state`` when unset). The CLI that
issues a token and the server that checks it must read the same file. Standard library only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from qodo.bridge.filetokens import FileTokenStore
from qodo.bridge.tokens import ServiceTokens

__all__ = ["ENV_TOKENS", "TOKEN_PREFIX", "open_tokens", "token_path"]

ENV_TOKENS = "QODO_SERVER_TOKENS"
#: The service-token prefix of this app's tokens (``qcl_<id>.<secret>``).
TOKEN_PREFIX = "qcl_"  # nosec B105 - a public token prefix, not a secret


def token_path(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """``path``, else ``$QODO_SERVER_TOKENS``, else the XDG state default."""
    if path:
        return Path(path)
    env = os.environ if env is None else env
    explicit = (env.get(ENV_TOKENS) or "").strip()
    if explicit:
        return Path(explicit)
    state = (env.get("XDG_STATE_HOME") or "").strip()
    base = Path(state) if state and os.path.isabs(state) else Path.home() / ".local" / "state"
    return base / "qodo-cli" / "service_tokens.json"


def open_tokens(
    path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> ServiceTokens:
    """The service tokens stored at :func:`token_path`."""
    return ServiceTokens(FileTokenStore(token_path(path, env)), prefix=TOKEN_PREFIX)
