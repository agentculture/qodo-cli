"""``qodo-cli mcp``: one MCP tool per registry verb on the ``mcp`` surface, through the gateway."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import pytest

pytest.importorskip("mcp")

from qodo import mcp_tools  # noqa: E402
from qodo import api, changelog, exposure  # noqa: E402
from qodo import gateway as gw  # noqa: E402
from qodo import registry as reg  # noqa: E402
from qodo.bridge import mcpserver  # noqa: E402
from qodo.cli import main  # noqa: E402
from qodo.cli._errors import CliError  # noqa: E402
from tests.fake_qodo_mcp import FakeResponse, FakeUpstream  # noqa: E402

KEY = "sk-test-SECRET-key-123"
PR = "https://github.com/o/r/pull/1"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QODO_API_KEY", KEY)


@pytest.fixture
def up(monkeypatch):
    fake = FakeUpstream()
    monkeypatch.setattr(api, "_gateway", lambda: gw.Gateway(api_key=KEY, opener=fake))
    return fake


def session_call(tools, fn):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        server = mcpserver.build_server(mcp_tools.SERVER_NAME, tools)
        async with create_connected_server_and_client_session(server) as session:
            return await fn(session)

    return asyncio.run(go())


def call(tools, name, arguments):
    async def fn(session):
        return await session.call_tool(name, arguments)

    return session_call(tools, fn)


def listed(tools):
    async def fn(session):
        return (await session.list_tools()).tools

    return session_call(tools, fn)


def policy(*entries):
    return exposure.parse_policy(
        {
            "version": 1,
            "tools": [
                {"tool": t, "verb": v, "surfaces": list(s), "allow_write": w}
                for t, v, s, w in entries
            ],
        }
    )


# --- one tool per verb on the mcp surface -----------------------------------------------


def test_one_tool_per_mcp_verb_named_from_the_verb_with_its_params_schema():
    registry = reg.default_registry()
    wanted = registry.on_surface("mcp")
    assert wanted
    tools = listed(mcp_tools.tools())
    assert {t.name: t.inputSchema for t in tools} == {
        f"{v.noun}_{v.name}": v.params for v in wanted
    }
    names = {t.name for t in tools}
    assert {"findings_list", "findings_implement", "findings_dismiss", "rules_search"} <= names
    # verbs that are not on the mcp surface by design are absent
    assert not names & {"config_propose", "review_list", "review_resolve", "config_show"}


def test_every_tool_says_what_the_verb_does_and_whether_it_writes():
    for t in listed(mcp_tools.tools()):
        verb = reg.default_registry().get(*t.name.split("_", 1))
        assert verb.summary in t.description
        assert ("write" in t.description) == verb.writes


def test_an_exposed_upstream_tool_is_proxied_under_the_name_the_policy_gives(monkeypatch):
    p = policy(("rules-metadata", "catalogue facts", ("mcp",), False))
    registry = reg.build_registry(p)
    fake = FakeUpstream()
    monkeypatch.setattr(api, "_gateway", lambda: gw.Gateway(p, api_key=KEY, opener=fake))
    tools = mcp_tools.tools(registry)
    names = [t.name for t in listed(tools)]
    assert "catalogue_facts" in names
    assert "rules_metadata" not in names  # the tool is served under the policy's name only
    res = call(tools, "catalogue_facts", {})
    assert not res.isError
    assert fake.calls == [("rules-metadata", {})]
    assert json.loads(res.content[0].text)


def test_an_unexposed_upstream_tool_is_absent_and_a_call_to_it_is_refused(monkeypatch):
    p = policy(("rules-metadata", "catalogue facts", ("mcp",), False))
    tools = mcp_tools.tools(reg.build_registry(p))
    names = [t.name for t in listed(tools)]
    # the policy names one upstream tool: no other upstream tool is served (the local verbs
    # that are on mcp are not upstream tools)
    assert "catalogue_facts" in names
    assert not {n for n in names if n.split("_")[0] in ("findings", "history")}
    res = call(tools, "findings_list", {"pr_url": PR})
    assert res.isError and "unknown tool" in res.content[0].text


def test_a_policy_that_leaves_mcp_out_removes_the_tool():
    p = policy(
        ("rules-metadata", "catalogue facts", ("cli", "api"), False),
        ("rules-list", "rules list", ("mcp",), False),
    )
    names = [t.name for t in listed(mcp_tools.tools(reg.build_registry(p)))]
    assert "catalogue_facts" not in names and "rules_list" in names


def test_the_gateway_refuses_a_verb_its_own_policy_does_not_put_on_mcp(monkeypatch):
    """The registry lists the tool, but the gateway the handler calls is stricter: the surface
    rule applies at call time, and the refusal is a tool error with message and hint."""
    narrow = policy(("get-pr-review-findings", "findings list", ("cli",), False))
    monkeypatch.setattr(
        api, "_gateway", lambda: gw.Gateway(narrow, api_key=KEY, opener=FakeUpstream())
    )
    res = call(mcp_tools.tools(), "findings_list", {"pr_url": PR})
    assert res.isError
    text = res.content[0].text
    assert "not exposed on the mcp surface" in text and "hint:" in text
    assert "Traceback" not in text and KEY not in text


# --- real calls -------------------------------------------------------------------------


def test_a_read_verb_runs_against_the_fake_upstream(up):
    res = call(mcp_tools.tools(), "findings_list", {"pr_url": PR})
    assert not res.isError
    body = json.loads(res.content[0].text)
    assert body["findings"] and body["pr_url"]
    assert up.calls[-1] == ("get-pr-review-findings", {"pr_url": PR})
    assert KEY not in res.content[0].text


def test_a_write_verb_runs_and_the_change_log_names_mcp_as_the_actor(up):
    res = call(mcp_tools.tools(), "findings_implement", {"finding_ids": ["f1", "f2"]})
    assert not res.isError
    assert up.calls[-1][0] == "pr-review-mark-implemented"
    entries = changelog.read()
    assert [(e["id"], e["status"], e["actor"]) for e in entries] == [
        ("f1", "implemented", "mcp"),
        ("f2", "implemented", "mcp"),
    ]


# --- refusals ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, arguments, expect",
    [
        ("findings_list", {}, "missing required"),
        ("findings_list", {"pr_url": PR, "bogus": 1}, "unexpected argument"),
        ("findings_list", {"pr_url": 5}, "declared type"),
        ("findings_dismiss", {"finding_ids": ["a"], "reason": "because"}, "reason"),
        ("findings_implement", {"finding_ids": []}, "finding"),
        ("no_such_tool", {}, "unknown tool"),
    ],
)
def test_a_refused_call_is_a_tool_error_that_says_why_and_reaches_no_upstream(
    up, name, arguments, expect
):
    res = call(mcp_tools.tools(), name, arguments)
    assert res.isError
    text = res.content[0].text
    assert expect in text and "Traceback" not in text
    assert up.calls == []
    assert changelog.read() == []


def test_a_cli_error_becomes_a_tool_error_with_its_message_and_remediation():
    err = mcp_tools.tool_error(CliError(code=2, message="upstream down", remediation="retry"))
    assert isinstance(err, mcpserver.ToolError)
    assert str(err) == "upstream down (hint: retry)"
    assert str(mcp_tools.tool_error(CliError(code=1, message="bad", remediation=""))) == "bad"


def test_an_upstream_failure_is_a_tool_error_and_never_shows_the_key(up):
    up.override["tools/call"] = FakeResponse(
        b"boom " + KEY.encode(), status=500, ctype="text/plain"
    )
    res = call(mcp_tools.tools(), "findings_list", {"pr_url": PR})
    assert res.isError
    assert KEY not in res.content[0].text and "Traceback" not in res.content[0].text


def test_an_unexpected_handler_failure_names_only_its_class(monkeypatch):
    def boom(params=None, *, surface="cli"):
        raise RuntimeError(f"secret {KEY}")

    registry = reg.build_registry()
    verb = registry.get("findings", "list")
    monkeypatch.setattr(verb, "handler", boom)
    res = call(mcp_tools.tools(registry), "findings_list", {"pr_url": PR})
    assert res.isError and "RuntimeError" in res.content[0].text
    assert KEY not in res.content[0].text


def test_the_handler_is_called_with_the_mcp_surface(monkeypatch):
    seen = []

    def spy(params=None, *, surface="cli"):
        seen.append((dict(params or {}), surface))
        return {"ok": True}

    registry = reg.build_registry()
    monkeypatch.setattr(registry.get("findings", "list"), "handler", spy)
    call(mcp_tools.tools(registry), "findings_list", {"pr_url": PR, "summary": None})
    assert seen == [({"pr_url": PR}, "mcp")]


# --- the command ------------------------------------------------------------------------


def _run(code, *argv, env=None):
    return subprocess.run(
        [sys.executable, "-c", code, *argv], capture_output=True, text=True, timeout=120, env=env
    )


def test_the_mcp_verb_without_the_extra_is_a_clear_environment_error():
    code = (
        "import sys\nfor m in ('mcp', 'anyio'):\n    sys.modules[m] = None\n"
        "from qodo.cli import main\nsys.exit(main(['mcp', '--json']))\n"
    )
    done = _run(code)
    assert done.returncode == 2
    assert "Traceback" not in done.stderr
    err = json.loads(done.stderr.strip().splitlines()[-1])
    assert "mcp" in err["message"]
    assert "qodo-cli[mcp]" in err["message"] + err["remediation"]
    assert done.stdout == ""  # stdout is the protocol channel


def test_the_mcp_verb_serves_over_real_stdio(tmp_path):
    code = """
import asyncio, json, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def go():
    env = {'HOME': sys.argv[1], 'PATH': os.environ.get('PATH', ''),
           'QODO_CHANGE_LOG': sys.argv[1] + '/changes.jsonl'}
    params = StdioServerParameters(command=sys.executable, args=['-m', 'qodo', 'mcp'], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            bad = await s.call_tool('findings_list', {})
            print(json.dumps([tools, bad.isError, bad.content[0].text]))
asyncio.run(go())
"""
    done = _run(code, str(tmp_path))
    assert done.returncode == 0, done.stderr
    names, is_error, text = json.loads(done.stdout.strip().splitlines()[-1])
    assert names == [f"{v.noun}_{v.name}" for v in reg.default_registry().on_surface("mcp")]
    assert is_error and "missing required" in text


def test_the_verb_is_described_everywhere(capsys):
    from qodo import explain

    assert "qodo-cli mcp" in explain.resolve(("mcp",))
    main(["learn"])
    assert "qodo-cli mcp" in capsys.readouterr().out
    main(["learn", "--json"])
    paths = [c["path"] for c in json.loads(capsys.readouterr().out)["commands"]]
    assert ["mcp"] in paths
    main(["overview", "--json"])
    assert "mcp" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        main(["mcp", "--help"])
    assert stop.value.code == 0
    assert "--json" in capsys.readouterr().out
