"""A write a browser sent for another site is refused before authentication."""

from __future__ import annotations

import pytest

from qodo.bridge import origin
from qodo.server.app import _whole

VERB = "/verbs/findings/list"
JSON = {"Content-Type": "application/json"}
BODY = '{"pr_url": "https://github.com/o/r/pull/1"}'


@pytest.mark.parametrize("role", ["viewer", None])
def test_a_cross_site_post_is_403_with_or_without_a_credential(client, auth, gateway, role):
    headers = {**JSON, "Sec-Fetch-Site": "cross-site", **(auth[role] if role else {})}
    reply = client.post(VERB, content=BODY, headers=headers)
    assert reply.status_code == 403
    assert reply.json()["error"]["code"] == "cross_site_request"
    assert gateway.calls == []


@pytest.mark.parametrize(
    "foreign", ["https://evil.example", "http://testserver:81", "https://testserver", "null"]
)
def test_a_foreign_origin_is_403_and_calls_nothing(client, auth, gateway, foreign):
    reply = client.post(VERB, content=BODY, headers={**auth["viewer"], **JSON, "Origin": foreign})
    assert reply.status_code == 403
    assert reply.json()["error"]["code"] == "cross_origin_request"
    assert gateway.calls == []


def test_a_cross_site_post_with_an_empty_body_is_refused_too(client, auth, gateway):
    # the JSON media type rule is skipped for an empty body, so it cannot be the only guard
    headers = {**auth["editor"], "Origin": "https://evil.example", "Content-Type": "text/plain"}
    reply = client.post(VERB, content="", headers=headers)
    assert reply.status_code == 403
    assert gateway.calls == []


def test_this_apps_own_pages_and_plain_callers_are_unaffected(client, auth):
    own = {**auth["viewer"], **JSON, "Origin": "http://testserver"}
    browser = {**auth["viewer"], **JSON, "Sec-Fetch-Site": "same-origin"}
    plain = {**auth["viewer"], **JSON}
    for headers in (own, browser, plain):
        assert client.post(VERB, content=BODY, headers=headers).status_code == 200


def test_the_browsers_same_origin_word_stands_when_a_proxy_hid_the_scheme(client, auth):
    # the server sees http, the page is https: without Sec-Fetch-Site the Origin would not match
    hidden = {**auth["viewer"], **JSON, "Origin": "https://testserver"}
    assert client.post(VERB, content=BODY, headers=hidden).status_code == 403
    marked = {**hidden, "Sec-Fetch-Site": "same-origin"}
    assert client.post(VERB, content=BODY, headers=marked).status_code == 200


def test_reads_by_get_are_never_refused(client, auth):
    headers = {**auth["viewer"], "Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
    assert client.get("/verbs", headers=headers).status_code == 200


def test_no_reply_carries_a_cors_header(client, auth):
    reply = client.post(VERB, content=BODY, headers={**auth["viewer"], **JSON})
    assert not [h for h in reply.headers if h.lower().startswith("access-control-")]


def test_the_origin_module_is_the_one_the_app_calls():
    assert origin.check_origin("POST", {"sec-fetch-site": "cross-site"}, "http").status == 403


@pytest.mark.parametrize("raw", ["9" * 5000, "0" * 5000 + "9" * 12])
def test_a_number_too_long_for_int_is_out_of_range_not_an_error(raw):
    assert _whole(raw, 65536, low=0) is False


def test_a_number_padded_with_zeros_is_read_as_its_value():
    assert _whole("0" * 5000 + "7", 65536, low=0) is True
