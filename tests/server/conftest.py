"""Server test fixtures: a stub gateway (no network), a file token store and an app per test."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from qodo import api  # noqa: E402
from qodo.bridge.filetokens import FileTokenStore  # noqa: E402
from qodo.bridge.tokens import ServiceTokens  # noqa: E402
from qodo.server.app import TOKEN_PREFIX, create_app  # noqa: E402


class StubGateway:
    def __init__(self):
        self.reply = {"ok": True}
        self.calls = []

    def call(self, name, arguments=None, *, surface=None):
        self.calls.append((name, dict(arguments or {}), surface))
        return self.reply


@pytest.fixture
def gateway(monkeypatch):
    gw = StubGateway()
    monkeypatch.setattr(api, "_gateway", lambda: gw)
    return gw


@pytest.fixture
def tokens(tmp_path):
    return ServiceTokens(FileTokenStore(tmp_path / "tokens.json"), prefix=TOKEN_PREFIX)


@pytest.fixture
def auth(tokens):
    """``auth["viewer"]`` etc.: an Authorization header for a token of that role."""
    return {
        role: {"Authorization": f"Bearer {tokens.issue('test', name=role, roles=[role]).token}"}
        for role in ("viewer", "editor", "admin")
    }


@pytest.fixture
def client(tokens, gateway):
    return TestClient(create_app(tokens=tokens, web_dist=None), raise_server_exceptions=False)


@pytest.fixture
def auth_for(auth):
    return lambda role: auth[role]
