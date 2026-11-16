"""Two listeners: Access JWTs only on the loopback one, service tokens on both, and a real
start and stop on ephemeral loopback ports."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi.testclient import TestClient

from qodo.server import serve as srv
from tests.bridge.jwks import AUD, TEAM, jwks, wall_clock_token

ROOT = Path(__file__).resolve().parents[2]
ACCESS = {
    "QODO_ACCESS_LISTEN": "127.0.0.1:9100",
    "QODO_ACCESS_TEAM_DOMAIN": TEAM,
    "QODO_ACCESS_AUD": AUD,
}


def listeners(tokens, env, **kw):
    specs = srv.plan_listeners(env)
    built = srv.build_listeners(specs, tokens, env=env, fetch_jwks=jwks, web_dist=None, **kw)
    return {lst.name: TestClient(lst.app, raise_server_exceptions=False) for lst in built}


def test_an_access_jwt_works_on_the_loopback_listener_only(tokens):
    apps = listeners(tokens, {**ACCESS, "QODO_LAN_HOST": "0.0.0.0"})
    assert set(apps) == {"loopback", "lan"}
    header = {"Cf-Access-Jwt-Assertion": wall_clock_token()}
    ok = apps["loopback"].get("/verbs", headers=header)
    assert ok.status_code == 200  # an SSO person is a viewer
    assert apps["lan"].get("/verbs", headers=header).status_code == 401


def test_an_sso_person_is_a_viewer_unless_the_environment_names_them(tokens):
    env = {**ACCESS, "QODO_EDITORS": "alice@example.com"}
    apps = listeners(tokens, env)
    header = {"Cf-Access-Jwt-Assertion": wall_clock_token()}
    body = {"finding_ids": ["a"], "reason": "false_positive"}
    assert apps["loopback"].post("/verbs/findings/dismiss", json=body, headers=header
                                 ).status_code != 403  # fmt: skip
    plain = listeners(tokens, ACCESS)["loopback"]
    assert plain.post("/verbs/findings/dismiss", json=body, headers=header).status_code == 403


def test_a_bad_access_jwt_is_refused_not_downgraded(tokens, auth):
    apps = listeners(tokens, ACCESS)
    r = apps["loopback"].get(
        "/verbs", headers={**auth["admin"], "Cf-Access-Jwt-Assertion": "garbage"}
    )
    assert r.status_code == 401


def test_a_service_token_works_on_both_listeners(tokens, auth):
    apps = listeners(tokens, {**ACCESS, "QODO_LAN_HOST": "0.0.0.0"})
    for name in ("loopback", "lan"):
        assert apps[name].get("/verbs", headers=auth["viewer"]).status_code == 200, name


def test_without_access_the_loopback_listener_ignores_the_header(tokens):
    apps = listeners(tokens, {})
    header = {"Cf-Access-Jwt-Assertion": wall_clock_token()}
    assert apps["loopback"].get("/verbs", headers=header).status_code == 401


# --- a real start and stop -----------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=2) as resp:  # nosec B310 - loopback
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def wait_for(url, deadline=15.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            return get(url)
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.1)
    raise AssertionError(f"{url} never answered")


def test_serve_starts_both_listeners_answers_and_stops_on_sigterm(tmp_path):
    loop, lan = free_port(), free_port()
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "QODO_SERVER_TOKENS": str(tmp_path / "tokens.json"),
        "QODO_CHANGE_LOG": str(tmp_path / "changes.jsonl"),
    }
    env.pop("QODO_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "qodo", "serve", "--port", str(loop),
         "--lan-host", "127.0.0.1", "--lan-port", str(lan)],  # fmt: skip
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        for port in (loop, lan):
            status, body = wait_for(f"http://127.0.0.1:{port}/health")
            assert status == 200 and body["service"] == "qodo-cli"
        assert get(f"http://127.0.0.1:{loop}/verbs")[0] == 401
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def test_serve_does_not_say_listening_when_a_port_is_taken(tmp_path):
    import socket

    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    taken = held.getsockname()[1]
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "QODO_SERVER_TOKENS": str(tmp_path / "tokens.json"),
        "QODO_CHANGE_LOG": str(tmp_path / "changes.jsonl"),
    }
    try:
        done = subprocess.run(
            [sys.executable, "-m", "qodo", "serve", "--port", str(taken)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False,
        )  # fmt: skip
    finally:
        held.close()
    assert done.returncode != 0
    assert "listening" not in done.stdout and "listening" not in done.stderr
