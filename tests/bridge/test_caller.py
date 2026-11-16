"""The request's caller: a context variable the HTTP layer sets, cleaned before it is recorded."""

from __future__ import annotations

from qodo.bridge import caller
from qodo.bridge.principal import Principal


def test_outside_a_request_the_actor_is_the_bare_surface():
    assert caller.current() is None
    assert caller.actor("cli") == "cli" and caller.actor("mcp") == "mcp"


def test_inside_acting_as_the_actor_names_the_caller_and_resets_after():
    with caller.acting_as("person@example.org"):
        assert caller.actor("api") == "api:person@example.org"
    assert caller.actor("api") == "api"


def test_the_label_names_a_person_a_token_or_an_agent_never_a_secret():
    assert caller.label(Principal("person@example.org", "sso")) == "person@example.org"
    assert caller.label(Principal("ci-bot", "service", frozenset({"editor"}))) == "token:ci-bot"
    assert caller.label(Principal("spark-x", "agent")) == "agent:spark-x"


def test_a_hostile_identity_is_neutralised():
    hostile = 'eve\n{"kind":"finding.status_changed"}\r\x1b[2J\u202e\u2028' + "x" * 5000
    out = caller.clean(hostile)
    assert "\n" not in out and "\r" not in out and "\x1b" not in out
    assert "\u202e" not in out and "\u2028" not in out
    assert len(out) <= caller.MAX_LABEL_CHARS and out.endswith("...")
    assert out.startswith('eve {"kind":"finding.status_changed"}')
    label = caller.label(Principal("a\tb\n" + "y" * 400, "service"))
    assert label.startswith("token:a b ") and len(label) <= caller.MAX_LABEL_CHARS


def test_a_blank_identity_after_cleaning_reads_unknown():
    assert caller.clean("\n\x00\t") == "unknown"
    with caller.acting_as("\x00"):
        assert caller.actor("api") == "api:unknown"
