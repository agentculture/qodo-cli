"""``api/openapi.json`` is pinned: the committed file is exactly the served schema."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from qodo import registry
from qodo.server.app import create_app, render_openapi

ROOT = Path(__file__).resolve().parents[2]
PINNED = ROOT / "api" / "openapi.json"


class NoTokens:
    def authenticate(self, presented):
        return None


def served() -> dict:
    return create_app(tokens=NoTokens(), web_dist=None).openapi()


def test_the_pinned_file_is_the_served_schema_text_for_text():
    assert PINNED.read_text(encoding="utf-8") == render_openapi(
        served()
    ), "api/openapi.json is stale: run `uv run python scripts/export_openapi.py --write`"


def test_the_pinned_schema_is_a_stable_function_of_the_registry():
    first, second = render_openapi(served()), render_openapi(served())
    assert first == second
    pinned = json.loads(PINNED.read_text(encoding="utf-8"))
    for verb in registry.default_registry().on_surface("api"):
        assert f"/verbs/{verb.noun}/{verb.name}" in pinned["paths"]
    assert pinned["openapi"].startswith("3.")
    assert "version" in pinned["info"] and pinned["info"]["title"]


def test_the_schema_does_not_change_with_the_package_version(monkeypatch):
    import qodo

    monkeypatch.setattr(qodo, "__version__", "99.0.0")
    assert render_openapi(served()) == PINNED.read_text(encoding="utf-8")


def test_the_schema_declares_bearer_auth_everywhere_but_health():
    doc = json.loads(PINNED.read_text(encoding="utf-8"))
    assert doc["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    for path, item in doc["paths"].items():
        for op in item.values():
            if path == "/health":
                assert op["security"] == []
            else:
                assert op["security"] == [{"bearerAuth": []}], path
            assert {"401", "403"} <= set(op["responses"]) or path == "/health", path


def test_the_regeneration_script_checks_and_reports_a_stale_file(tmp_path):
    ok = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_openapi.py")],
        capture_output=True, text=True, cwd=ROOT,
    )  # fmt: skip
    assert ok.returncode == 0, ok.stderr
    stale = tmp_path / "openapi.json"
    stale.write_text("{}\n", encoding="utf-8")
    bad = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_openapi.py"), "--path", str(stale)],
        capture_output=True, text=True, cwd=ROOT,
    )  # fmt: skip
    assert bad.returncode == 1 and "--write" in bad.stderr
    fixed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_openapi.py"), "--path", str(stale),
         "--write"],
        capture_output=True, text=True, cwd=ROOT,
    )  # fmt: skip
    assert fixed.returncode == 0 and stale.read_text(encoding="utf-8") == PINNED.read_text(
        encoding="utf-8"
    )
