"""The public Python API (``qodo.api``) and the CLI handlers' use of it (issue 18)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest import mock

import pytest

from qodo import api
from qodo.cli._errors import CliError

_GH = "https://github.com/o/r.git"
_COMMANDS = Path(__file__).resolve().parent.parent / "qodo" / "cli" / "_commands"
_PRIVATE = {"qodo.cli._providers", "qodo.cli._qodo_api"}


def _imports_private(path: Path) -> list[str]:
    """Every import in ``path`` that reaches qodo.cli._providers or qodo.cli._qodo_api."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name in _PRIVATE]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in _PRIVATE:
                found.append(mod)
            elif mod == "qodo.cli":
                found += [
                    f"qodo.cli.{a.name}" for a in node.names if f"qodo.cli.{a.name}" in _PRIVATE
                ]
    return found


@pytest.mark.parametrize("name", ["rules", "review", "config"])
def test_handlers_do_not_import_private_modules(name: str) -> None:
    assert _imports_private(_COMMANDS / f"{name}.py") == []


def test_import_guard_detects_a_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from qodo.cli import _providers\n", encoding="utf-8")
    assert _imports_private(bad) == ["qodo.cli._providers"]


def test_api_is_stdlib_and_exports_the_contract() -> None:
    for fn in (
        "review_list",
        "review_resolve",
        "rules_get",
        "config_show",
        "config_validate",
        "config_init",
    ):
        assert callable(getattr(api, fn))


# --- rules ------------------------------------------------------------------


def test_rules_get_returns_plain_list_of_dicts() -> None:
    fake = [{"name": "n", "severity": "ERROR", "content": "c"}]
    with (
        mock.patch("qodo.cli._qodo_api.detect_scopes", return_value=["o/r"]),
        mock.patch("qodo.cli._qodo_api.search_rules", return_value=fake) as search,
    ):
        assert api.rules_get("q", top_k=3) == fake
    search.assert_called_once_with("q", top_k=3, scopes=["o/r"])


def test_rules_get_no_scope_and_explicit_scopes() -> None:
    with mock.patch("qodo.cli._qodo_api.search_rules", return_value=[]) as search:
        api.rules_get("q", auto_scope=False)
        api.rules_get("q", scopes=["a/b"])
    assert search.call_args_list[0].kwargs["scopes"] is None
    assert search.call_args_list[1].kwargs["scopes"] == ["a/b"]


# --- review -----------------------------------------------------------------

_CANNED = [
    {"kind": "summary", "title": "s", "id": None},
    {"kind": "inline", "id": 11, "severity": "HIGH", "title": "a"},
    {"kind": "inline", "id": 12, "severity": "LOW", "title": "b"},
]


def _provider_patches():
    return (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=_CANNED),
    )


def test_review_list_returns_json_ready_dict() -> None:
    p1, p2 = _provider_patches()
    with p1, p2:
        out = api.review_list(pr=5, kind="inline")
    assert out["provider"] == "github"
    assert out["pr"] == {"number": 5}
    assert out["count"] == 2
    assert [c["id"] for c in out["comments"]] == [11, 12]
    json.dumps(out)  # plain objects only


def test_review_list_no_open_pr_is_user_error() -> None:
    p1, _ = _provider_patches()
    with (
        p1,
        mock.patch("qodo.cli._providers.current_branch", return_value="b"),
        mock.patch("qodo.cli._providers.find_open_pr", return_value=None),
        pytest.raises(CliError) as err,
    ):
        api.review_list()
    assert "no open PR" in err.value.message


def test_review_resolve_by_severity() -> None:
    p1, p2 = _provider_patches()
    ok = [{"action": "resolve thread", "ok": True, "detail": ""}]
    with p1, p2, mock.patch("qodo.cli._providers.resolve_comment", return_value=ok) as res:
        out = api.review_resolve(pr=5, severity="high", reply="done")
    assert out == {
        "pr": 5,
        "count": 1,
        "ok": True,
        "resolved": [{"comment_id": 11, "ok": True, "actions": ok}],
    }
    assert res.call_args.kwargs["reply"] == "done"


def test_review_resolve_selection_errors() -> None:
    p1, p2 = _provider_patches()
    with p1, p2:
        with pytest.raises(CliError):
            api.review_resolve(pr=5)
        with pytest.raises(CliError):
            api.review_resolve([1], pr=5, all=True)
        with pytest.raises(CliError):
            api.review_resolve(["x"], pr=5)


# --- config -----------------------------------------------------------------


def test_config_show_validate_init_roundtrip(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    shown = api.config_show(tmp_path)
    assert shown["pr_agent"]["present"] is False
    assert api.config_validate(tmp_path)["valid"] is False
    created = api.config_init(tmp_path)
    assert created["written"] == [".pr_agent.toml", "best_practices.md"]
    assert api.config_init(tmp_path)["skipped"] == [".pr_agent.toml", "best_practices.md"]
    assert api.config_validate(tmp_path)["valid"] is True
    assert api.config_show(tmp_path)["pr_agent"]["has_pr_reviewer"] is True
