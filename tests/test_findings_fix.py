"""``findings fix``: ask for an agent to fix Qodo findings by publishing ONE event on the mesh's
event bus (events-cli). No test reaches a broker: a fake publisher stands in at the one seam
(``qodo.api._publisher``), and the events-cli client is replaced by a fake for the real-publisher
cases (the closed-port case uses the real client against a loopback port nothing listens on).
"""

from __future__ import annotations

import json
import socket
import sys
from datetime import UTC, datetime, timedelta

import pytest

from qodo import api, changelog
from qodo import fix_request as fx
from qodo import registry as reg
from qodo.cli import main
from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

REPO = "acme/widgets"
PR = "https://github.com/acme/widgets/pull/42"
IDS = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]


class FakePublisher:
    def __init__(self, error: CliError | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error = error

    def __call__(self, event_type: str, data: dict) -> dict:
        self.calls.append((event_type, json.loads(json.dumps(data))))
        if self.error is not None:
            raise self.error
        return {"event_id": f"evt_TEST{len(self.calls)}", "topic": "events/qodo/fix/requested"}


@pytest.fixture
def allow(monkeypatch, tmp_path):
    monkeypatch.setenv("QODO_PROPOSE_CONFIG", str(tmp_path / "absent.json"))
    monkeypatch.setenv("QODO_PROPOSE_REPOS", REPO)
    monkeypatch.delenv("QODO_FIX_REQUEST_WINDOW", raising=False)


@pytest.fixture
def pub(monkeypatch, allow) -> FakePublisher:
    fake = FakePublisher()
    monkeypatch.setattr(api, "_publisher", lambda: fake)
    return fake


def fix_lines() -> list[dict]:
    return [e for e in changelog.read() if e["kind"] == "fix.requested"]


# --- the event ----------------------------------------------------------------------------------


def test_a_request_publishes_one_event_with_the_exact_type_and_data(pub):
    api.findings_fix(PR, IDS, note="keep the public API", actor="cli")
    assert pub.calls == [
        (
            "qodo.fix.requested",
            {
                "pr_url": PR,
                "repo": REPO,
                "number": 42,
                "finding_ids": IDS,
                "note": "keep the public API",
                "requested_by": "cli",
            },
        )
    ]


def test_without_a_note_the_event_says_null(pub):
    api.findings_fix(PR, IDS[:1], actor="mcp")
    assert pub.calls[0][1]["note"] is None and pub.calls[0][1]["requested_by"] == "mcp"


def test_the_result_names_the_event_and_the_pr(pub):
    out = api.findings_fix(PR, IDS, actor="cli")
    assert out == {
        "event_id": "evt_TEST1",
        "type": "qodo.fix.requested",
        "pr_url": PR,
        "repo": REPO,
        "number": 42,
        "finding_ids": IDS,
    }


def test_the_pr_url_is_canonical_in_the_event(pub):
    api.findings_fix("http://GitHub.com/acme/widgets/pull/42/", IDS, actor="cli")
    assert pub.calls[0][1]["pr_url"] == PR


def test_a_success_logs_one_line_with_ids_and_the_event_but_no_note(pub):
    api.findings_fix(PR, IDS, note="NOTE-TEXT", actor="api")
    (entry,) = changelog.read()
    assert entry["kind"] == "fix.requested" and entry["actor"] == "api"
    assert entry["pr_url"] == PR and entry["finding_ids"] == IDS
    assert entry["event_id"] == "evt_TEST1"
    assert "NOTE-TEXT" not in json.dumps(entry)


def test_the_real_events_cli_envelope_carries_the_event(allow, monkeypatch):
    """Through events-cli's own Envelope and topic mapping, with only the client faked: the
    envelope validates, its id is events-cli's ``evt_`` id, QoS is 1, and the id is returned."""
    pytest.importorskip("events_cli")
    import events_cli.client as ec
    from events_cli.core import Envelope

    sent = []

    class Client:
        def __init__(self, *a, **k):
            self.is_connected = True

        def publish_event(self, envelope, topic, *, qos, wait):
            sent.append((envelope, topic, qos, wait))
            return ec.PublishResult(ok=True, connected=True, reason="ok", mid=1)

        def close(self):
            sent.append("closed")

    monkeypatch.setattr(ec, "EventClient", Client)
    monkeypatch.setattr(api, "_publisher", lambda: fx.publish)
    out = api.findings_fix(PR, IDS, actor="cli")
    envelope, topic, qos, wait = sent[0]
    assert sent[1] == "closed"
    assert isinstance(envelope, Envelope) and topic == "events/qodo/fix/requested" and qos == 1
    assert wait > 0
    wire = envelope.to_dict()
    assert wire["type"] == "qodo.fix.requested" and wire["source"] == "app://qodo-cli"
    assert wire["id"].startswith("evt_") and out["event_id"] == wire["id"]
    assert Envelope.from_dict(json.loads(envelope.to_json())) == envelope
    assert set(wire) == {"id", "type", "source", "time", "schemaVersion", "data"}  # no marks
    assert wire["data"]["finding_ids"] == IDS and wire["data"]["repo"] == REPO
    assert fix_lines()[0]["event_id"] == wire["id"]


def test_two_requests_get_two_event_ids(allow, monkeypatch):
    pytest.importorskip("events_cli")
    import events_cli.client as ec

    class Client:
        is_connected = True

        def __init__(self, *a, **k):
            pass

        def publish_event(self, envelope, topic, *, qos, wait):
            return ec.PublishResult(ok=True, connected=True, reason="ok", mid=1)

        def close(self):
            pass

    monkeypatch.setattr(ec, "EventClient", Client)
    monkeypatch.setattr(api, "_publisher", lambda: fx.publish)
    a = api.findings_fix(PR, IDS[:1], actor="cli")["event_id"]
    b = api.findings_fix(PR, IDS[1:], actor="cli")["event_id"]
    assert a != b


# --- refusals: nothing published, nothing logged ------------------------------------------------


def _refused(pub, code=EXIT_USER_ERROR, **kw):
    params = {"pr_url": PR, "finding_ids": IDS, "actor": "cli", **kw}
    with pytest.raises(CliError) as err:
        api.findings_fix(params.pop("pr_url"), params.pop("finding_ids"), **params)
    assert err.value.code == code
    assert pub.calls == [] and fix_lines() == []
    return err.value


def test_a_repo_outside_the_allowlist_is_refused_before_anything_is_published(pub):
    err = _refused(pub, pr_url="https://github.com/evil/repo/pull/1")
    assert "allowlist" in err.message and "QODO_PROPOSE_REPOS" in err.remediation


def test_an_empty_allowlist_refuses_everything(pub, monkeypatch):
    monkeypatch.setenv("QODO_PROPOSE_REPOS", "")
    _refused(pub)


def test_the_allowlist_is_checked_before_the_rate_guard_and_the_bus(pub, monkeypatch):
    def boom(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("reached past the allowlist")

    monkeypatch.setattr(fx, "_recent_request", boom)
    _refused(pub, pr_url="https://github.com/acme/other/pull/1")


@pytest.mark.parametrize(
    "url",
    ["ftp://github.com/acme/widgets/pull/1", "https://gitlab.com/acme/widgets/-/merge_requests/1",
     "https://github.com/acme/widgets/issues/1", "https://github.com/acme/widgets/pull/x",
     "https://github.com/acme/widgets/pull/0", "https://github.com/acme/widgets/pull/1/files",
     "https://github.com/acme/widgets/pull/1?x=1", "https://github.com/acme/../pull/1",
     "https://user@github.com/acme/widgets/pull/1", "https://github.com:8443/acme/widgets/pull/1",
     "not a url", ""],
)  # fmt: skip
def test_a_url_that_is_not_a_github_pull_request_is_refused(pub, url):
    _refused(pub, pr_url=url)


@pytest.mark.parametrize(
    "ids",
    [[], [f"id{i}" for i in range(21)], [""], ["   "], ["x" * 129], ["a b"], ["a\nb"], ["../x"],
     [7]],
)  # fmt: skip
def test_finding_ids_must_be_one_to_twenty_plain_ids(pub, ids):
    _refused(pub, finding_ids=ids)


def test_a_note_over_the_cap_is_refused(pub):
    _refused(pub, note="x" * 501)


def test_the_schema_bounds_the_params_on_every_surface():
    verb = reg.default_registry().get("findings", "fix")
    props = verb.params["properties"]
    assert props["finding_ids"]["minItems"] == 1 and props["finding_ids"]["maxItems"] == 20
    assert props["finding_ids"]["items"]["maxLength"] == 128
    assert props["note"]["maxLength"] == 500
    assert set(verb.params["required"]) == {"pr_url", "finding_ids"}


def test_a_failed_publish_logs_nothing(allow, monkeypatch):
    fake = FakePublisher(CliError(code=EXIT_ENV_ERROR, message="bus down", remediation="x"))
    monkeypatch.setattr(api, "_publisher", lambda: fake)
    with pytest.raises(CliError) as err:
        api.findings_fix(PR, IDS, actor="cli")
    assert err.value.code == EXIT_ENV_ERROR
    assert len(fake.calls) == 1 and fix_lines() == []


# --- the rate guard -----------------------------------------------------------------------------


def test_the_same_request_twice_within_the_window_is_refused(pub):
    api.findings_fix(PR, IDS, actor="cli")
    with pytest.raises(CliError) as err:
        api.findings_fix(PR, list(reversed(IDS)), actor="api")  # same set, another order
    assert err.value.code == EXIT_USER_ERROR
    assert "already requested" in err.value.message and "evt_TEST1" in err.value.message
    assert len(pub.calls) == 1 and len(fix_lines()) == 1


def test_another_set_or_another_pr_is_not_a_repeat(pub):
    api.findings_fix(PR, IDS, actor="cli")
    api.findings_fix(PR, IDS[:1], actor="cli")
    api.findings_fix(PR.replace("/42", "/43"), IDS, actor="cli")
    assert len(pub.calls) == 3


def test_a_request_older_than_the_window_is_not_a_repeat(pub, monkeypatch):
    api.findings_fix(PR, IDS, actor="cli")
    later = datetime.now(UTC) + timedelta(seconds=fx.DEFAULT_WINDOW_SECONDS + 5)
    monkeypatch.setattr(fx, "_now", lambda: later)
    api.findings_fix(PR, IDS, actor="cli")
    assert len(pub.calls) == 2


def test_the_window_is_configurable_and_zero_turns_it_off(pub, monkeypatch):
    monkeypatch.setenv("QODO_FIX_REQUEST_WINDOW", "0")
    api.findings_fix(PR, IDS, actor="cli")
    api.findings_fix(PR, IDS, actor="cli")
    assert len(pub.calls) == 2
    monkeypatch.setenv("QODO_FIX_REQUEST_WINDOW", "3600")
    later = datetime.now(UTC) + timedelta(seconds=fx.DEFAULT_WINDOW_SECONDS + 5)
    monkeypatch.setattr(fx, "_now", lambda: later)
    with pytest.raises(CliError):
        api.findings_fix(PR, IDS, actor="cli")


def test_two_requests_arriving_together_publish_once(allow, monkeypatch):
    import threading
    import time

    calls = []

    def slow(event_type, data):
        calls.append(data)
        time.sleep(0.2)  # the second request arrives while the first is publishing
        return {"event_id": f"evt_SLOW{len(calls)}", "topic": "t"}

    monkeypatch.setattr(api, "_publisher", lambda: slow)
    outcomes = []

    def ask():
        try:
            outcomes.append(api.findings_fix(PR, IDS, actor="api")["event_id"])
        except CliError as err:
            outcomes.append(err.code)

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and sorted(map(str, outcomes)) == ["1", "evt_SLOW1"]
    assert len(fix_lines()) == 1


@pytest.mark.parametrize("bad", ["soon", "-1", "1.5", "\u00b2"])
def test_a_bad_window_is_an_environment_error(pub, monkeypatch, bad):
    monkeypatch.setenv("QODO_FIX_REQUEST_WINDOW", bad)
    err = _refused(pub, code=EXIT_ENV_ERROR)
    assert "QODO_FIX_REQUEST_WINDOW" in err.message


# --- the real publisher: missing extra, broker down ---------------------------------------------


def test_without_the_events_extra_it_is_an_environment_error_naming_the_extra(allow, monkeypatch):
    monkeypatch.setitem(sys.modules, "events_cli", None)
    monkeypatch.setattr(api, "_publisher", lambda: fx.publish)
    with pytest.raises(CliError) as err:
        api.findings_fix(PR, IDS, actor="cli")
    assert err.value.code == EXIT_ENV_ERROR
    assert "qodo-cli[events]" in err.value.remediation
    assert fix_lines() == []


def test_without_the_extra_the_cli_exits_2(allow, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "events_cli", None)
    rc = main(["findings", "fix", "--pr-url", PR, "--finding-ids", IDS[0], "--json"])
    err = json.loads(capsys.readouterr().err)
    assert rc == 2 and "events" in err["remediation"]
    assert fix_lines() == []


def test_a_broker_that_never_answers_is_an_environment_error(allow, monkeypatch):
    pytest.importorskip("events_cli")
    import events_cli.client as ec

    class Down:
        is_connected = False

        def __init__(self, *a, **k):
            pass

        def publish_event(self, envelope, topic, *, qos, wait):
            return ec.PublishResult(ok=False, connected=False, reason="unconfirmed", mid=None)

        def close(self):
            pass

    monkeypatch.setattr(ec, "EventClient", Down)
    monkeypatch.setattr(fx, "CONNECT_WAIT", 0.05)
    monkeypatch.setattr(api, "_publisher", lambda: fx.publish)
    with pytest.raises(CliError) as err:
        api.findings_fix(PR, IDS, actor="cli")
    assert err.value.code == EXIT_ENV_ERROR
    assert "EVENTS_BROKER_HOST" in err.value.remediation
    assert fix_lines() == []


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_real_client_against_a_closed_loopback_port_fails_cleanly(allow, monkeypatch):
    """events-cli's real client and paho, aimed at a loopback port nothing listens on (no
    broker): an environment error, nothing logged, within the bounded waits."""
    pytest.importorskip("events_cli")
    pytest.importorskip("paho.mqtt.client")
    monkeypatch.setenv("EVENTS_BROKER_HOST", "127.0.0.1")
    monkeypatch.setenv("EVENTS_BROKER_PORT", str(_closed_port()))
    monkeypatch.setattr(fx, "CONNECT_WAIT", 0.3)
    monkeypatch.setattr(fx, "DELIVERY_WAIT", 0.3)
    monkeypatch.setattr(api, "_publisher", lambda: fx.publish)
    with pytest.raises(CliError) as err:
        api.findings_fix(PR, IDS, actor="cli")
    assert err.value.code == EXIT_ENV_ERROR and fix_lines() == []


def test_a_malformed_broker_port_is_an_environment_error(allow, monkeypatch):
    pytest.importorskip("events_cli")
    monkeypatch.setenv("EVENTS_BROKER_PORT", "not-a-port")
    monkeypatch.setattr(api, "_publisher", lambda: fx.publish)
    with pytest.raises(CliError) as err:
        api.findings_fix(PR, IDS, actor="cli")
    assert err.value.code == EXIT_ENV_ERROR and "EVENTS_BROKER_PORT" in err.value.message
    assert fix_lines() == []


# --- the verb on its surfaces -------------------------------------------------------------------


def test_the_verb_is_a_local_write_on_cli_api_and_mcp():
    verb = reg.default_registry().get("findings", "fix")
    assert verb.kind == "local" and verb.writes is True
    assert verb.surfaces == ("cli", "api", "mcp")
    assert verb.params["additionalProperties"] is False


def test_the_handler_passes_the_surface_as_requested_by(pub):
    verb = reg.default_registry().get("findings", "fix")
    out = verb.handler({"pr_url": PR, "finding_ids": IDS[:1]}, surface="mcp")
    assert out["event_id"] == "evt_TEST1"
    assert pub.calls[0][1]["requested_by"] == "mcp" and fix_lines()[0]["actor"] == "mcp"


def test_the_cli_publishes_and_prints_the_result(pub, capsys):
    rc = main(["findings", "fix", "--pr-url", PR, "--finding-ids", IDS[0], "--finding-ids",
               IDS[1], "--note", "small please", "--json"])  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["finding_ids"] == IDS and out["number"] == 42
    assert pub.calls[0][1]["note"] == "small please"


def test_the_mcp_tool_publishes(pub):
    from qodo import mcp_tools

    (tool,) = [t for t in mcp_tools.tools() if t.name == "findings_fix"]
    out = tool.handler({"pr_url": PR, "finding_ids": IDS})
    assert out["repo"] == REPO and pub.calls[0][1]["requested_by"] == "mcp"


def test_explain_learn_and_overview_describe_findings_fix(capsys):
    from qodo.explain import resolve

    text = resolve(("findings", "fix"))
    assert "qodo.fix.requested" in text and "QODO_PROPOSE_REPOS" in text
    assert "EVENTS_BROKER_HOST" in text and "qodo-cli[events]" in text
    for argv in (["learn"], ["overview"], ["findings", "overview"]):
        assert main(argv) == 0
        assert "findings fix" in capsys.readouterr().out


# --- HTTP: roles and errors ---------------------------------------------------------------------


@pytest.fixture
def http(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from qodo.bridge.filetokens import FileTokenStore
    from qodo.bridge.tokens import ServiceTokens
    from qodo.server.app import TOKEN_PREFIX, create_app

    tokens = ServiceTokens(FileTokenStore(tmp_path / "tokens.json"), prefix=TOKEN_PREFIX)
    client = TestClient(create_app(tokens=tokens, web_dist=None), raise_server_exceptions=False)
    auth = {
        role: {"Authorization": f"Bearer {tokens.issue('t', name=role, roles=[role]).token}"}
        for role in ("viewer", "editor")
    }
    return client, auth


def test_http_needs_a_token_and_the_editor_role(pub, http):
    client, auth = http
    body = {"pr_url": PR, "finding_ids": IDS}
    assert client.post("/verbs/findings/fix", json=body).status_code == 401
    assert client.post("/verbs/findings/fix", json=body, headers=auth["viewer"]).status_code == 403
    assert pub.calls == []
    r = client.post("/verbs/findings/fix", json=body, headers=auth["editor"])
    assert r.status_code == 200 and r.json()["event_id"] == "evt_TEST1"
    assert pub.calls[0][1]["requested_by"] == "api:token:editor"


def test_http_refuses_a_repo_outside_the_allowlist_with_400(pub, http):
    client, auth = http
    body = {"pr_url": "https://github.com/evil/repo/pull/1", "finding_ids": IDS}
    r = client.post("/verbs/findings/fix", json=body, headers=auth["editor"])
    assert r.status_code == 400 and pub.calls == []


def test_http_answers_503_without_the_extra(allow, http, monkeypatch):
    monkeypatch.setitem(sys.modules, "events_cli", None)
    client, auth = http
    body = {"pr_url": PR, "finding_ids": IDS}
    r = client.post("/verbs/findings/fix", json=body, headers=auth["editor"])
    assert r.status_code == 503 and "events" in r.json()["error"]["remediation"]


@pytest.mark.parametrize("bad", ["abc\n", "abc\r", "a b", "abc\x00", "\nabc"])
def test_a_finding_id_with_a_control_character_is_refused(bad):
    from qodo import fix_request

    with pytest.raises(CliError):
        fix_request._finding_ids([bad])
