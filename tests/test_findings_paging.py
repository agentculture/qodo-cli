"""``findings list --offset/--limit``: the full form of one PR's findings, a page at a time, under
the 64 KiB reply expectation, with counts over every finding (what the Findings screen reads).

Without ``offset``/``limit`` the verb answers as before (the whole upstream reply).
"""

from __future__ import annotations

import json

import pytest

from qodo import api
from qodo import registry as reg
from qodo.cli._errors import EXIT_USER_ERROR, CliError

PR = "https://github.com/o/r/pull/1"
RECORDED_KEYS = {
    "action_level",
    "attribution_status",
    "category",
    "comment_id",
    "created_at",
    "description",
    "finding_source",
    "git_sha",
    "id",
    "inline_comment_id",
    "review_run_id",
    "review_source",
    "title",
}


def finding(i: int, status: str = "pending", **extra) -> dict:
    return {
        "id": f"00000000-0000-0000-0000-{i:012d}",
        "attribution_status": status,
        "action_level": "remediation_recommended",
        "category": "Correctness",
        "title": f"title {i}",
        "description": f"description {i}",
        "comment_id": "1",
        "inline_comment_id": None,
        "created_at": "2026-01-01T00:00:00.000000Z",
        "finding_source": "IssueFinding",
        "git_sha": "0" * 40,
        "review_run_id": 1,
        "review_source": None,
        **extra,
    }


class Canned:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def call(self, name, arguments=None, *, surface=None):
        self.calls.append((name, dict(arguments or {})))
        return self.reply


@pytest.fixture
def canned(monkeypatch):
    def install(reply):
        gw = Canned(reply)
        monkeypatch.setattr(api, "_gateway", lambda: gw)
        return gw

    return install


def call(params):
    return reg.default_registry().get("findings", "list").handler(params, surface="api")


def reply_of(findings, session=None):
    return {
        "finding_count": len(findings),
        "findings": findings,
        "pr_url": PR,
        "review_session": (
            session
            if session is not None
            else {"status": "completed", "commit_sha": "a" * 40, "run_id": 1}
        ),
    }


def test_the_paging_params_are_local_and_bounded():
    props = reg.default_registry().get("findings", "list").params["properties"]
    assert props["offset"] == {
        "type": "integer",
        "minimum": 0,
        "description": props["offset"]["description"],
    }
    assert props["limit"]["type"] == "integer"
    assert props["limit"]["minimum"] == 1 and props["limit"]["maximum"] == 50


def test_offset_and_limit_are_not_sent_upstream(canned):
    gw = canned(reply_of([finding(1)]))
    call({"pr_url": PR, "offset": 0, "limit": 10})
    assert gw.calls == [("get-pr-review-findings", {"pr_url": PR})]


def test_a_page_keeps_every_recorded_field_and_counts_every_finding(canned):
    rows = [finding(i, ["pending", "dismissed", "full_implementation"][i % 3]) for i in range(7)]
    canned(reply_of(rows))
    out = call({"pr_url": PR, "offset": 2, "limit": 3})
    assert [f["id"] for f in out["findings"]] == [r["id"] for r in rows[2:5]]
    assert set(out["findings"][0]) == RECORDED_KEYS
    assert out["offset"] == 2 and out["limit"] == 3 and out["next_offset"] == 5
    assert out["finding_count"] == 7
    assert out["counts"]["total"] == 7
    assert out["counts"]["by_status"] == {"dismissed": 2, "full_implementation": 2, "pending": 3}
    assert out["counts"]["open"] == 3 and out["counts"]["closed"] == 4
    assert out["review_session"]["commit_sha"] == "a" * 40
    assert out["pr_url"] == PR


def test_the_last_page_has_no_next_offset(canned):
    canned(reply_of([finding(i) for i in range(3)]))
    out = call({"pr_url": PR, "offset": 0, "limit": 50})
    assert len(out["findings"]) == 3 and out["next_offset"] is None
    out = call({"pr_url": PR, "offset": 9, "limit": 5})
    assert out["findings"] == [] and out["next_offset"] is None


def test_a_page_of_huge_findings_stays_under_64_kib_and_says_what_was_clipped(canned):
    big = [
        finding(i, title="t" * 2000, description="é" * 20000, evidence={"x": "y" * 50000})
        for i in range(60)
    ]
    canned(reply_of(big))
    seen = []
    offset = 0
    while offset is not None:
        out = call({"pr_url": PR, "offset": offset, "limit": 50})
        assert len(json.dumps(out).encode()) < 64 * 1024
        assert len(json.dumps(out, ensure_ascii=False).encode()) < 64 * 1024
        assert out["findings"], "every page makes progress"
        seen += [f["id"] for f in out["findings"]]
        offset = out["next_offset"]
    assert seen == [f["id"] for f in big]  # nothing skipped, nothing repeated
    one = out["findings"][0]
    assert len(one["title"]) <= 300 and len(one["description"]) <= 4000
    assert one["clipped"] == ["description", "title"]
    assert "evidence" not in one and one["omitted"] == ["evidence"]
    assert len(one["id"]) == 36  # an id is never cut


def test_a_null_review_session_is_kept(canned):
    canned({"finding_count": 0, "findings": [], "pr_url": PR, "review_session": None})
    out = call({"pr_url": PR, "limit": 20})
    assert out["review_session"] is None and out["findings"] == [] and out["offset"] == 0


def test_summary_and_paging_together_are_refused(canned):
    gw = canned(reply_of([]))
    with pytest.raises(CliError) as err:
        call({"pr_url": PR, "summary": True, "limit": 5})
    assert err.value.code == EXIT_USER_ERROR
    assert gw.calls == []


@pytest.mark.parametrize("bad", [{"limit": 0}, {"limit": 51}, {"offset": -1}, {"limit": "5"}])
def test_bad_paging_values_are_refused(canned, bad):
    canned(reply_of([]))
    with pytest.raises(CliError) as err:
        call({"pr_url": PR, **bad})
    assert err.value.code == EXIT_USER_ERROR


def test_without_paging_the_reply_is_unchanged(canned):
    reply = reply_of([finding(1)])
    canned(reply)
    assert call({"pr_url": PR}) == reply
