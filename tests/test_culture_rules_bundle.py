"""The culture-rules bundle in ``docs/rules/qodo`` (t18), as data and end to end.

The bundle is configuration for culture-rules >= 0.47.0 (the first release with
``github.pr_open``). culture-rules is **not** a dependency of qodo-cli, so this module skips,
with the reason, when it is not importable, older than 0.47.0 or lacks ``github.pr_open``. Run it
for real with::

    uv run --with /path/to/culture-rules pytest tests/test_culture_rules_bundle.py

End to end, the shipped definitions are imported with culture-rules' own import code into a
``MemoryStore``; GitHub events are recorded by culture-rules' real webhook receiver
(``extensions/github/hooks.py``, signed deliveries) and driven by a real engine node
(``node/daemon.py``) through the real ``http.call``, ``github.pr_open``, ``keyset.*``,
``json.parse`` and ``text.join`` ports. Only the far ends are doubles: the two bridges (an
opener factory under ``http.call``), GitHub's REST API (a transport under ``github.pr_open``)
and the runner commands (``machine.command``, which would run git and sleep). The doubles follow
the patterns of culture-rules' own tests (``tests/node/test_github_pr_open.py``,
``tests/rules/test_green_merge_flow.py``); culture-rules' test helpers are not installed with it,
so the few this module needs are restated here.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import io
import json
import re
import urllib.error
import urllib.parse
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path

import pytest

MIN_CULTURE_RULES = (0, 47, 0)


def _skip_reason() -> str | None:
    try:
        installed = metadata.version("culture-rules")
    except metadata.PackageNotFoundError:
        return "culture-rules is not installed (it is not a qodo-cli dependency)"
    found = re.match(r"(\d+)\.(\d+)\.(\d+)", installed)
    if found is None or tuple(int(x) for x in found.groups()) < MIN_CULTURE_RULES:
        return f"culture-rules {installed} is older than 0.47.0 (the bundle needs github.pr_open)"
    try:
        from culture_rules.extensions.github import pr_open  # noqa: F401
    except ImportError:
        return f"culture-rules {installed} has no github.pr_open"
    return None


if (_REASON := _skip_reason()) is not None:
    pytest.skip(_REASON, allow_module_level=True)

from culture_rules.engine.actorport import InvocationResult  # noqa: E402
from culture_rules.engine.runs import RUNS_COLLECTION  # noqa: E402
from culture_rules.extensions import registry  # noqa: E402
from culture_rules.extensions.github import hooks  # noqa: E402
from culture_rules.extensions.github.pr_open import GitHubPrOpenPort  # noqa: E402
from culture_rules.extensions.http.port import HttpCallPort  # noqa: E402
from culture_rules.io.exchange import import_bundle, read_bundle  # noqa: E402
from culture_rules.machines.enrol import enrol  # noqa: E402
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION  # noqa: E402
from culture_rules.machines.probe import ProbeResult  # noqa: E402
from culture_rules.model.action_step import action_spec  # noqa: E402
from culture_rules.model.machine import Machine  # noqa: E402
from culture_rules.node.actions.json_parse import JsonParsePort  # noqa: E402
from culture_rules.node.actions.keyset import KeysetAddPort, KeysetFilterPort  # noqa: E402
from culture_rules.node.actions.value_steps import TextJoinPort  # noqa: E402
from culture_rules.node.daemon import HeartbeatOptions, Node, NodeOptions  # noqa: E402
from culture_rules.node.runner import BuiltinCodePort, NoopAction  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "docs" / "rules" / "qodo"
FINDINGS_RULES = (
    "qodo-findings-on-review",
    "qodo-findings-on-comment",
    "qodo-findings-on-review-comment",
)
IMPROVE_RULE = "qodo-improve-on-merge"
RULES = (*FINDINGS_RULES, IMPROVE_RULE)
MACHINE = "core"
REPO = "example-owner/example-repo"  # the bundle's placeholder repo: tests keep its condition
FORK = "someone/example-repo"
QODO_BOT = "qodo-code-review[bot]"
APP_BOT = "rules-test-app[bot]"
HUMAN = "ori"
SHA_A, SHA_B, MERGED = "a" * 40, "b" * 40, "e" * 40
BASE_SHA, BASE_TREE = "c" * 40, "d" * 40
BRANCH = "qodo-config/update"
LABELS = ["qodo-config", "automated"]
HOOK_SECRET = "hook-secret-for-tests"
FAKE_BEARER = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"
PROPOSED = [{"path": ".pr_agent.toml", "content": "[review_agent]\nenabled = true\n"}]
PARAMS = "params"


# --------------------------------------------------------------------------- the data


def bundle():
    read = read_bundle(BUNDLE)
    assert read.errors == [], [e.to_dict() for e in read.errors]
    return read.bundle


def wf(workflow_id: str):
    return next(w for w in bundle().workflows if w.id == workflow_id)


def rule_doc(rule_id: str) -> dict:
    return next(r for r in bundle().rules if r.id == rule_id).to_dict()


def steps(workflow_id: str) -> dict:
    """Every step of a workflow by id, loop bodies included."""
    out = {}
    for s in wf(workflow_id).steps:
        out[s.id] = s
        out.update({b.id: b for b in s.body})
    return out


def instruction() -> str:
    return action_spec(steps("qodo-improve")["start"])[PARAMS]["body"]["instruction"]


def test_the_bundle_holds_three_actors_four_rules_and_two_workflows():
    b = bundle()
    assert sorted(a.id for a in b.actors) == ["associate-bridge", "qodo-bridge", "qodo-checkout"]
    assert sorted(r.id for r in b.rules) == sorted(RULES)
    assert sorted(w.id for w in b.workflows) == ["qodo-findings", "qodo-improve"]


def test_the_bundle_imports_with_culture_rules_import_code_and_every_rule_is_disabled():
    store = MemoryStore()
    plan = import_bundle(BUNDLE, store, apply=True)
    assert plan.errors == [] and plan.applied
    assert sorted(d["id"] for d in store.find("rules")) == sorted(RULES)
    assert all(d["enabled"] is False for d in store.find("rules"))


def test_the_api_import_that_rollout_import_calls_accepts_the_bundle():
    """``rollout import --bundle DIR`` posts ``workflows/`` and ``rules/`` to ``POST /import``,
    which runs ``Definitions.import_files``; the files are collected the same way."""
    server = pytest.importorskip("culture_rules.server.service")
    from culture_rules.cli._nounlib import collect_files

    files = {
        **collect_files(str(BUNDLE), None, "workflows"),
        **collect_files(str(BUNDLE), None, "rules"),
    }
    store = MemoryStore()
    plan = server.Definitions(store).import_files(files, "tester", apply=True)
    assert plan["applied"] is True and plan["errors"] == []
    assert all(d["enabled"] is False for d in store.find("rules"))


def test_the_bundle_ships_no_release_manifest_and_its_readme_names_the_lowest_version():
    # a release.json would tie the bundle to one culture-rules release (rollout refuses any
    # other); the README states the lowest one instead
    assert not (BUNDLE / "release.json").exists()
    readme = (BUNDLE / "README.md").read_text(encoding="utf-8")
    assert "culture-rules 0.47.0" in readme
    for value in (
        "QODO_BRIDGE_SERVICE_TOKEN",
        "ASSOCIATE_BRIDGE_SERVICE_TOKEN",
        "github.pr_open",
        "self_identity",
        REPO,
        "/srv/qodo-checkouts",
        "8794",
    ):
        assert value in readme, value


def test_the_findings_rules_need_qodo_same_repo_non_draft_and_not_self_authored():
    for rule_id in FINDINGS_RULES:
        doc = rule_doc(rule_id)
        args = doc["condition"]["args"]
        assert {
            "op": "in",
            "value": {"field": "data.author"},
            "items": {"literal": [QODO_BOT]},
        } in (args)
        assert _eq("data.self_authored", False) in args
        assert _eq("data.draft", False) in args
        assert {
            "op": "compare",
            "cmp": "==",
            "left": {"field": "data.head_repo"},
            "right": {"field": "data.base_repo"},
        } in args
        assert (
            doc["concurrency_key"]
            == "qodo-findings:{trigger.data.repository}#{trigger.data.number}"
        )
        assert doc["max_attempts"] == 1


def test_the_open_pr_step_takes_its_branch_and_labels_as_literals():
    spec = action_spec(steps("qodo-improve")["open_pr"])
    assert spec["kind"] == "github.pr_open"
    assert spec[PARAMS]["branch"] == BRANCH
    assert spec[PARAMS]["labels"] == LABELS
    # the loop guard on merge is the same prefix the branch carries
    guard = rule_doc(IMPROVE_RULE)["condition"]["args"][-1]
    needle = guard["arg"]["needle"]["literal"]
    assert guard["op"] == "not" and guard["arg"]["op"] == "contains"
    assert BRANCH.startswith(needle)
    # the files are the bridge's, never the agent's
    assert spec[PARAMS]["files"] == "inputs.proposal.files"


def test_the_runner_commands_bind_and_pass_culture_rules_inline_guard():
    """fetch, reset and pause bind as argv lists that culture-rules' runner accepts (no shell,
    no inline evaluation); nothing here runs them."""
    from culture_rules.actors.code import bind_argv
    from culture_rules.actors.inline_guard import inline_eval_reason

    (runner,) = [a for a in bundle().actors if a.id == "qodo-checkout"]
    commands = runner.params["commands"]
    checkout = f"/srv/qodo-checkouts/{REPO}"
    fetch = bind_argv(commands["fetch"], {"checkout": checkout})
    reset = bind_argv(commands["reset"], {"checkout": checkout})
    pause = bind_argv(commands["pause"], {})
    assert fetch == ["git", "-C", checkout, "fetch", "--quiet", "origin", "main"]
    assert reset == ["git", "-C", checkout, "reset", "--hard", "--quiet", "origin/main"]
    assert pause == ["sleep", "30"]
    assert [inline_eval_reason(a) for a in (fetch, reset, pause)] == [None, None, None]


def test_the_improvement_rule_runs_one_at_a_time_per_repo():
    assert rule_doc(IMPROVE_RULE)["concurrency_key"] == "qodo-improve:{trigger.data.repository}"


def test_the_instruction_lives_only_in_the_bundle_never_under_qodo():
    text = instruction()
    assert len(text) > 200
    probe = text[:80]
    hits = [
        p
        for p in (ROOT / "qodo").rglob("*")
        if p.is_file() and probe in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == []
    # in the bundle, the step configuration is the one place that holds it
    bundle_hits = [
        p.relative_to(BUNDLE).as_posix()
        for p in BUNDLE.rglob("*.json")
        if probe in p.read_text(encoding="utf-8")
    ]
    assert bundle_hits == ["workflows/qodo-improve.json"]


def test_culture_rules_needs_no_qodo_line_for_this_bundle():
    """Every kind, builtin and event the bundle uses is a generic, registered one, and the
    installed culture_rules package holds no file mentioning qodo."""
    reg = registry()
    kinds, builtins = set(), set()
    for workflow in bundle().workflows:
        for s in steps(workflow.id).values():
            spec = action_spec(s)
            if spec is not None:
                kinds.add(spec["kind"])
            elif s.config.get("builtin"):
                builtins.add(s.config["builtin"])
    assert kinds == {"http.call", "machine.command", "github.pr_open"}
    assert builtins == {"keyset.filter", "keyset.add", "json.parse", "text.join"}
    assert kinds <= set(reg.action_kinds())
    assert builtins <= {b.name for b in reg.builtins()}
    assert {r.trigger.params["type"] for r in bundle().rules} == {
        "github.review.submitted",
        "github.comment.created",
        "github.review_comment.created",
        "github.pr.closed",
    }
    import culture_rules

    package = Path(culture_rules.__file__).parent
    mentions = [
        p.relative_to(package).as_posix()
        for p in package.rglob("*")
        if p.is_file()
        and p.suffix in (".py", ".json", ".md", ".txt", ".html", ".js", ".css")
        and "qodo" in p.read_text(encoding="utf-8", errors="ignore").lower()
    ]
    assert mentions == []


def _eq(field: str, literal) -> dict:
    return {"op": "compare", "cmp": "==", "left": {"field": field}, "right": {"literal": literal}}


# --------------------------------------------------------------------------- the doubles


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class _Response(io.BytesIO):
    def __init__(self, status: int, payload) -> None:
        super().__init__(json.dumps(payload).encode())
        self.status = status
        self.headers = {"Content-Type": "application/json"}


class Bridges:
    """The qodo-cli and associate bridges as http.call sees them (an opener factory)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, str | None]] = []
        self.findings = {
            "pr_url": f"https://github.com/{REPO}/pull/7",
            "finding_count": 3,
            "counts": {"total": 3, "open": 2, "closed": 1, "other": 0, "by_status": {}},
            "findings": [{"id": "f1", "status": "open"}],
        }
        self.run_polls = 2  # GETs answering running before the run's end
        self.final = {"status": "finished", "ok": True, "merge": "merged"}
        self.proposal = {
            "ok": True,
            "repo": REPO,
            "base": BASE_SHA,
            "base_sha": BASE_SHA,
            "head": MERGED,
            "files": PROPOSED,
            "warnings": [],
        }
        self.start_status = 202
        self._gets = 0
        self.started = 0

    def __call__(self, pinned: str):
        assert pinned == "127.0.0.1"
        return self

    def open(self, request, timeout=None):
        method = request.get_method()
        url = request.full_url
        body = json.loads(request.data) if request.data else None
        self.calls.append((method, url, body, request.get_header("Authorization")))
        path = urllib.parse.urlsplit(url).path
        if url.startswith("http://127.0.0.1:8792/"):
            if path == "/verbs/findings/list":
                return _Response(200, self.findings)
            if path == "/verbs/config/propose":
                return _Response(200, self.proposal)
        if url.startswith("http://127.0.0.1:8794/"):
            if method == "POST" and path == "/runs":
                if self.start_status != 202:
                    raise urllib.error.HTTPError(
                        url, self.start_status, "busy", {}, io.BytesIO(b"{}")
                    )
                self.started += 1
                self._gets = 0
                return _Response(202, {"run_id": f"run{self.started}", "status": "running"})
            if method == "GET" and path.startswith("/runs/"):
                self._gets += 1
                reply = {"run_id": path.rsplit("/", 1)[1], "ok": False, "base_commit": BASE_SHA}
                if self._gets <= self.run_polls:
                    return _Response(200, {**reply, "status": "running"})
                return _Response(200, {**reply, **self.final})
        raise AssertionError(f"unexpected call {method} {url}")

    def paths(self) -> list[str]:
        return [f"{m} {urllib.parse.urlsplit(u).path}" for m, u, _, _ in self.calls]


class Runner:
    """machine.command on qodo-checkout: records the command, runs nothing."""

    supports_idempotency_key = True

    def __init__(self) -> None:
        self.commands: list[tuple[str, dict]] = []
        self.hold = False  # a busy runner: pause answers blocked, as a capped actor does

    def invoke(self, input, idempotency_key, deadline, *, context):
        if self.hold and input.get("command") == "pause":
            return InvocationResult.blocked("busy")
        self.commands.append((input.get("command"), dict(input.get("args") or {})))
        return InvocationResult.completed({"stdout": "", "stderr": "", "exit_code": 0})


class FakeGitHub:
    """GitHub's REST API for github.pr_open: one repo with refs, commits, pulls and labels
    (after culture-rules' tests/node/test_github_pr_open.py)."""

    def __init__(self) -> None:
        self.refs = {"main": BASE_SHA}
        self.commits = {BASE_SHA: {"tree": {"sha": BASE_TREE}}}
        self.pulls: list[dict] = []
        self.trees: list[dict] = []
        self._n = 0

    def _sha(self) -> str:
        self._n += 1
        return f"{self._n:040x}"

    def __call__(self, method, url, headers, body, timeout):
        payload = json.loads(body) if body else None
        path = url.split("api.github.com", 1)[1]
        if path.endswith("/access_tokens"):
            exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": FAKE_BEARER, "expires_at": exp}).encode()
        status, out = self._route(method, path, payload)
        return status, json.dumps(out).encode()

    def _route(self, method, path, payload):  # noqa: C901 - one branch per GitHub call
        prefix = f"/repos/{REPO}"
        assert path.startswith(prefix), path
        rest = path[len(prefix) :]
        if method == "GET" and rest == "":
            return 200, {"full_name": REPO, "default_branch": "main"}
        if method == "GET" and rest.startswith("/git/ref/heads/"):
            name = rest[len("/git/ref/heads/") :]
            if name not in self.refs:
                return 404, {"message": "Not Found"}
            return 200, {"ref": f"refs/heads/{name}", "object": {"sha": self.refs[name]}}
        if method == "GET" and rest.startswith("/git/commits/"):
            return 200, {"sha": rest.rsplit("/", 1)[1], **self.commits[rest.rsplit("/", 1)[1]]}
        if method == "POST" and rest == "/git/trees":
            self.trees.append(payload)
            return 201, {"sha": self._sha()}
        if method == "POST" and rest == "/git/commits":
            sha = self._sha()
            self.commits[sha] = {"tree": {"sha": payload["tree"]}, "parents": payload["parents"]}
            return 201, {"sha": sha}
        if method == "POST" and rest == "/git/refs":
            name = payload["ref"][len("refs/heads/") :]
            if name in self.refs:
                return 422, {"message": "Reference already exists"}
            self.refs[name] = payload["sha"]
            return 201, {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
        if method == "GET" and rest.startswith("/pulls?"):
            query = urllib.parse.parse_qs(rest.split("?", 1)[1])
            branch = query["head"][0].split(":", 1)[1]
            items = [
                p
                for p in reversed(self.pulls)
                if (query["state"] == ["all"] or p["state"] == "open")
                and p["head"]["ref"] == branch
            ]
            return 200, items
        if method == "POST" and rest == "/pulls":
            number = 100 + len(self.pulls)
            pull = {
                "number": number,
                "html_url": f"https://github.com/{REPO}/pull/{number}",
                "state": "open",
                "title": payload["title"],
                "body": payload.get("body"),
                "head": {"ref": payload["head"], "sha": self.refs[payload["head"]]},
                "base": {"ref": payload["base"]},
                "labels": [],
            }
            self.pulls.append(pull)
            return 201, pull
        if method == "POST" and rest.startswith("/issues/") and rest.endswith("/labels"):
            number = int(rest.split("/")[2])
            pull = next(p for p in self.pulls if p["number"] == number)
            pull["labels"].extend(x for x in payload["labels"] if x not in pull["labels"])
            return 200, [{"name": n} for n in pull["labels"]]
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture(scope="module")
def pem():
    pytest.importorskip("cryptography", reason="github.pr_open signs the App's JWT")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def github_actor() -> dict:
    """The live GitHub app actor as the bundle README asks the operator to configure it."""
    return {
        "id": "github-app",
        "name": "GitHub",
        "kind": "app",
        "machine": MACHINE,
        "enabled": True,
        "params": {
            "surface": "github",
            "events": [
                "github.pr.closed",
                "github.pr.synchronize",
                "github.comment.created",
                "github.review.submitted",
                "github.review_comment.created",
            ],
            "actions": ["github.comment", "github.pr_open"],
            "self_identity": APP_BOT,
            "connection": {
                "app_id": "1",
                "installation_id": "2",
                "private_key": "grant:GH_KEY",
                "webhook_secret": "grant:GH_HOOK",
                "repos": [REPO],
            },
        },
        "schema_version": "1.0",
    }


BEATS = HeartbeatOptions(
    probe=lambda: ProbeResult(tools={}),
    load_reader=lambda: {"cpu": 0.1, "mem": 0.2},
    engine_version="test",
)


def pull(
    number: int = 7,
    *,
    head_sha: str = SHA_A,
    head_repo: str = REPO,
    head_branch: str = "fix/widget",
    draft: bool = False,
    merged: bool = False,
    author: str = HUMAN,
) -> dict:
    return {
        "number": number,
        "title": "Fix the widget",
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "state": "closed" if merged else "open",
        "merged": merged,
        "draft": draft,
        "user": {"login": author},
        "head": {"sha": head_sha, "ref": head_branch, "repo": {"full_name": head_repo}},
        "base": {"sha": BASE_SHA, "ref": "main", "repo": {"full_name": REPO}},
    }


class World:
    """One engine node on ``core`` with the bundle imported by culture-rules' import code."""

    def __init__(self, pem: str = "", *, enable: tuple[str, ...] = ()) -> None:
        self.clock = Clock(datetime.now(UTC).replace(microsecond=0))
        self.base = MemoryStore(clock=self.clock)
        node = Machine(name=MACHINE, capabilities=(), roles=("engine_node",))
        enrol(self.base, node, apply=True)
        self._beat()
        plan = import_bundle(BUNDLE, self.base, apply=True)
        assert plan.errors == []
        self.base.put("actors", github_actor())
        for rule_id in enable:  # enabled in this test's own store only
            doc = copy.deepcopy(self.base.get("rules", rule_id))
            doc["enabled"] = True
            self.base.put("rules", doc)
        self.bridges, self.runner, self.github = Bridges(), Runner(), FakeGitHub()
        self.pulls: dict[int, dict] = {}
        tokens = {"GH_KEY": pem, "GH_HOOK": HOOK_SECRET}
        secret = lambda name: tokens.get(name, f"value-of-{name}")  # noqa: E731
        builtins = {
            "json.parse": JsonParsePort(),
            "text.join": TextJoinPort(),
            "keyset.filter": KeysetFilterPort(self.base, self.clock),
            "keyset.add": KeysetAddPort(self.base, self.clock),
        }
        ports = {
            "action:noop": NoopAction(),
            "action:http.call": HttpCallPort(
                self.base, opener_factory=self.bridges, secret_runner=secret, clock=self.clock
            ),
            "action:machine.command": self.runner,
            "action:github.pr_open": GitHubPrOpenPort(
                self.base, transport=self.github, secrets=lambda ref: pem, clock=self.clock
            ),
            "code": BuiltinCodePort(builtins),
        }
        self.secret = secret
        self.node = Node(
            self.base.peer(),
            MACHINE,
            actors=ports,
            clock=self.clock,
            heartbeat_options=BEATS,
            options=NodeOptions(listen_gateways=False),
        )
        self.node.start()
        self.delivery = 0

    def _beat(self) -> None:
        self.base.put(
            HEARTBEAT_COLLECTION,
            {"id": MACHINE, "machine": MACHINE, "ts": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")},
        )

    # -- recording deliveries through the real receiver ------------------------------

    def deliver(self, event: str, payload: dict) -> int:
        self.delivery += 1
        body = json.dumps({"repository": {"full_name": REPO}, **payload}).encode()
        signature = "sha256=" + hmac.new(HOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        headers = {
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": f"delivery-{self.delivery}",
            "X-Hub-Signature-256": signature,
        }
        status, _ = hooks.handle(
            self.base,
            body=body,
            headers=headers,
            query={},
            secrets=lambda ref: self.secret(ref.split(":", 1)[1]),
            pull=lambda repo, number: self.pulls[number],
        )
        assert status in (200, 202), status
        return status

    def review(self, author: str = QODO_BOT, **pr) -> None:
        self.deliver(
            "pull_request_review",
            {
                "action": "submitted",
                "review": {"state": "commented", "body": "Qodo review"},
                "pull_request": pull(**pr),
                "sender": {"login": author},
            },
        )

    def comment(self, author: str = QODO_BOT, **pr) -> None:
        number = pr.get("number", 7)
        self.pulls[number] = pull(**pr)
        self.deliver(
            "issue_comment",
            {
                "action": "created",
                "issue": {
                    "number": number,
                    "title": "Fix the widget",
                    "html_url": f"https://github.com/{REPO}/pull/{number}",
                    "pull_request": {"url": "x"},
                },
                "comment": {"id": 501, "body": "Persistent review", "user": {"login": author}},
                "sender": {"login": author},
            },
        )

    def review_comment(self, author: str = QODO_BOT, **pr) -> None:
        self.deliver(
            "pull_request_review_comment",
            {
                "action": "created",
                "comment": {"id": 601, "body": "Inline finding", "user": {"login": author}},
                "pull_request": pull(**pr),
                "sender": {"login": author},
            },
        )

    def push(self, head_sha: str, **pr) -> None:
        self.deliver(
            "pull_request",
            {
                "action": "synchronize",
                "pull_request": pull(head_sha=head_sha, **pr),
                "sender": {"login": HUMAN},
            },
        )

    def merge(self, number: int = 7, *, merged: bool = True, sender: str = HUMAN, **pr) -> None:
        self.deliver(
            "pull_request",
            {
                "action": "closed",
                "pull_request": pull(number, merged=merged, **pr),
                "sender": {"login": sender},
            },
        )

    # -- driving the node ---------------------------------------------------------------

    def settle(self, cycles: int = 60) -> None:
        for _ in range(cycles):
            self.clock.advance(1)
            self._beat()
            report = self.node.run_once()
            assert report.errors == [], report.errors

    def runs(self, rule_ids=RULES) -> list[dict]:
        docs = [d for d in self.base.find(RUNS_COLLECTION) if d.get("rule_id") in rule_ids]
        return sorted(docs, key=lambda d: d["created_at"])

    def step(self, run: dict, step_id: str) -> dict:
        return next(s for s in run["steps"] if s["def"] == step_id and not s.get("loop"))


def finds(world: World) -> list[str]:
    return [p for p in world.bridges.paths() if p.endswith("/verbs/findings/list")]


# --------------------------------------------------------------------------- disabled


def test_with_the_rules_disabled_a_qodo_review_and_a_merge_start_no_run():
    world = World()
    world.review()
    world.comment()
    world.review_comment()
    world.merge()
    world.settle()
    assert world.runs() == []
    assert world.bridges.calls == [] and world.runner.commands == []
    assert len(world.base.find("events")) == 4  # recorded, and nothing fired


# --------------------------------------------------------------------------- findings


def findings_world() -> World:
    return World(enable=FINDINGS_RULES)


def test_a_qodo_review_starts_one_run_that_collects_the_findings():
    world = findings_world()
    world.review()
    world.settle()
    (run,) = world.runs()
    assert run["status"] == "succeeded", run.get("error")
    assert run["rule_id"] == "qodo-findings-on-review"
    assert run["outputs"]["findings"]["counts"]["open"] == 2
    method, url, body, auth = world.bridges.calls[0]
    assert (method, url) == ("POST", "http://127.0.0.1:8792/verbs/findings/list")
    assert body == {"pr_url": f"https://github.com/{REPO}/pull/7", "summary": True}
    assert auth == "Bearer value-of-QODO_BRIDGE_SERVICE_TOKEN"


def test_a_qodo_comment_starts_a_run():
    world = findings_world()
    world.comment()
    world.settle()
    (run,) = world.runs()
    assert run["rule_id"] == "qodo-findings-on-comment"
    assert run["status"] == "succeeded", run.get("error")
    assert len(finds(world)) == 1


def test_a_qodo_inline_comment_starts_a_run():
    world = findings_world()
    world.review_comment()
    world.settle()
    (run,) = world.runs()
    assert run["rule_id"] == "qodo-findings-on-review-comment"
    assert len(finds(world)) == 1


def test_a_human_comment_starts_no_run():
    world = findings_world()
    world.comment(author=HUMAN)
    world.review(author=HUMAN)
    world.settle()
    assert world.runs() == [] and world.bridges.calls == []


def test_a_self_authored_event_starts_no_run():
    """culture-rules' own App commenting (self_authored) never starts the findings flow."""
    world = findings_world()
    world.comment(author=APP_BOT)
    world.review_comment(author=APP_BOT)
    world.settle()
    assert world.runs() == [] and world.bridges.calls == []
    tags = [d["envelope"]["data"]["self_authored"] for d in world.base.find("events")]
    assert tags == [True, True]  # recorded, tagged as the App's own


def test_a_fork_pr_starts_no_run():
    world = findings_world()
    world.review(head_repo=FORK)
    world.comment(head_repo=FORK)
    world.settle()
    assert world.runs() == []


def test_a_draft_pr_starts_no_run():
    world = findings_world()
    world.review(draft=True)
    world.review_comment(draft=True)
    world.settle()
    assert world.runs() == []


def test_one_review_burst_on_one_head_commit_starts_one_run():
    """A review, its summary comment and inline comments: one run for that head (h26)."""
    world = findings_world()
    world.review()
    world.comment()
    world.review_comment()
    world.review_comment()
    world.settle()
    assert len(world.runs()) == 1
    assert len(finds(world)) == 1


def test_the_same_head_commit_twice_collects_once():
    """With the attempt budget open again on the same head (as green checks reset it, through
    culture-rules' own reset), a later Qodo event runs, and the keyset step skips the bridge for
    a head already collected."""
    from culture_rules.engine.claims import reset_attempt_budget

    world = findings_world()
    world.review()
    world.settle()
    reset_attempt_budget(world.base, f"qodo-findings:{REPO}#7", "evt-green-checks")
    world.review_comment()
    world.settle()
    first, second = world.runs()
    assert first["status"] == second["status"] == "succeeded"
    assert world.step(second, "list")["status"] == "skipped"
    assert second["outputs"]["new_head"] == 0
    assert len(finds(world)) == 1


def test_a_new_head_commit_collects_again():
    world = findings_world()
    world.review()
    world.settle()
    world.push(SHA_B)  # a human push: resets the attempt budget for this PR
    world.review(head_sha=SHA_B)
    world.settle()
    assert len(finds(world)) == 2
    assert all(r["status"] == "succeeded" for r in world.runs())


def test_a_failed_collection_marks_no_head_so_the_next_event_retries():
    world = findings_world()
    world.bridges.findings = "not an object"
    world.review()
    world.settle()
    (run,) = world.runs()
    assert run["status"] == "failed"
    assert world.base.find("keysets") == []


# --------------------------------------------------------------------------- improvement


def improve_world(pem: str) -> World:
    return World(pem, enable=(IMPROVE_RULE,))


def improve_runs(world: World) -> list[dict]:
    return world.runs((IMPROVE_RULE,))


def test_a_merge_runs_associate_then_opens_one_pr_with_the_literal_branch_and_labels(pem):
    world = improve_world(pem)
    world.merge()
    world.settle(120)
    (run,) = improve_runs(world)
    assert run["status"] == "succeeded", run.get("error")
    checkout = f"/srv/qodo-checkouts/{REPO}"
    assert world.runner.commands[:2] == [
        ("fetch", {"checkout": checkout}),
        ("reset", {"checkout": checkout}),
    ]
    start = next(c for c in world.bridges.calls if c[0] == "POST" and c[1].endswith("/runs"))
    assert start[2]["checkout"] == checkout
    assert start[2]["instruction"].startswith("You are improving this repository's Qodo")
    assert f"https://github.com/{REPO}/pull/7" in start[2]["instruction"]
    assert start[3] == "Bearer value-of-ASSOCIATE_BRIDGE_SERVICE_TOKEN"
    propose = next(c for c in world.bridges.calls if c[1].endswith("/verbs/config/propose"))
    assert propose[2] == {"repo": REPO, "checkout": checkout, "base": BASE_SHA}
    (opened,) = world.github.pulls
    assert opened["head"]["ref"] == BRANCH
    assert opened["labels"] == LABELS
    assert world.github.trees[0]["tree"][0]["path"] == ".pr_agent.toml"
    assert run["outputs"]["pr_number"] == opened["number"]
    # it polled until the run was no longer running: 2 running answers, then the end
    polls = [p for p in world.bridges.paths() if p.startswith("GET /runs/")]
    assert len(polls) == 3
    assert [c for c, _ in world.runner.commands].count("pause") == 3


def test_nothing_to_merge_ends_quietly_with_no_pr(pem):
    world = improve_world(pem)
    world.bridges.final = {"status": "finished", "ok": True, "merge": "nothing_to_merge"}
    world.merge()
    world.settle(120)
    (run,) = improve_runs(world)
    assert run["status"] == "succeeded", run.get("error")
    assert world.step(run, "propose")["status"] == "skipped"
    assert world.step(run, "open_pr")["status"] == "skipped"
    assert world.github.pulls == []
    assert not any(p.endswith("/verbs/config/propose") for p in world.bridges.paths())


def test_a_run_that_is_not_ok_fails_the_flow(pem):
    world = improve_world(pem)
    world.bridges.final = {
        "status": "finished",
        "ok": False,
        "merge": "not_attempted",
        "not_ok_because": "the tests did not pass",
    }
    world.merge()
    world.settle(120)
    (run,) = improve_runs(world)
    assert run["status"] == "failed"
    assert run["error"]["step"] == "run_ok"
    assert "the tests did not pass" in run["error"]["message"]
    assert world.github.pulls == []


@pytest.mark.parametrize("status", ["lost", "failed-to-start"])
def test_a_lost_or_unstarted_run_fails_the_flow(pem, status):
    world = improve_world(pem)
    world.bridges.final = {"status": status, "ok": False, "error": "no result"}
    world.merge()
    world.settle(120)
    (run,) = improve_runs(world)
    assert run["status"] == "failed"
    assert run["error"]["step"] == "run_ok"
    assert status in run["error"]["message"]


def test_a_run_still_running_after_the_last_poll_fails_with_a_timeout(pem):
    world = improve_world(pem)
    world.bridges.run_polls = 10_000
    world.merge()
    world.settle(400)
    (run,) = improve_runs(world)
    assert run["status"] == "failed"
    assert run["error"]["step"] == "wait_run"
    assert "loop_max_exceeded" in run["error"]["code"] + run["error"]["message"]
    assert len([p for p in world.bridges.paths() if p.startswith("GET /runs/")]) == 40


def test_a_propose_refusal_ends_with_its_reason_and_opens_nothing(pem):
    world = improve_world(pem)
    world.bridges.proposal = {
        "ok": False,
        "reason": "path_not_allowed",
        "detail": "only Qodo configuration files may change",
        "paths": ["src/app.py"],
    }
    world.merge()
    world.settle(120)
    (run,) = improve_runs(world)
    assert run["status"] == "failed"
    assert run["error"]["step"] == "refusal"
    assert "path_not_allowed: only Qodo configuration files may change" in run["error"]["message"]
    assert world.github.pulls == [] and world.github.trees == []


def test_an_open_configuration_pr_makes_the_next_run_end_branch_exists_without_failing(pem):
    world = improve_world(pem)
    world.merge(7)
    world.settle(120)
    world.merge(8)
    world.settle(120)
    first, second = improve_runs(world)
    assert first["status"] == second["status"] == "succeeded"
    assert world.step(second, "open_pr")["outputs"]["skipped"] == "branch_exists"
    assert second["outputs"]["pr_skipped"] == "branch_exists"
    assert len(world.github.pulls) == 1


def test_a_second_merge_while_a_run_is_in_progress_waits_for_it(pem):
    """One run at a time per repo: the second merge is held, then runs, and ends on the open
    configuration PR (two merges in a minute leave at most one open PR, h28)."""
    world = improve_world(pem)
    world.runner.hold = True  # the first run waits at its first pause, still running
    world.merge(7)
    world.settle(2)
    (first,) = improve_runs(world)
    assert first["status"] == "running"
    world.merge(8)
    world.settle(2)
    runs = improve_runs(world)
    assert len(runs) == 1 and runs[0]["status"] == "running"
    assert world.bridges.started == 1
    world.runner.hold = False
    world.settle(300)
    first, second = improve_runs(world)
    assert first["status"] == second["status"] == "succeeded"
    assert world.bridges.started == 2
    assert len(world.github.pulls) == 1


def test_a_closed_unmerged_pr_starts_no_run(pem):
    world = improve_world(pem)
    world.merge(merged=False)
    world.settle()
    assert improve_runs(world) == [] and world.runner.commands == []


def test_a_merged_fork_pr_starts_no_run(pem):
    world = improve_world(pem)
    world.merge(head_repo=FORK)
    world.settle()
    assert improve_runs(world) == []


def test_a_head_branch_with_the_prefix_starts_no_run(pem):
    world = improve_world(pem)
    world.merge(head_branch="qodo-config/manual-tweak")
    world.settle()
    assert improve_runs(world) == []


def test_merging_the_configuration_pr_this_bundle_opened_starts_no_improvement_run(pem):
    """The loop guard, end to end: the bundle opens its PR as culture-rules' App from the
    fixed branch; a person merges it; that merge starts nothing, and only the first run ran."""
    world = improve_world(pem)
    world.merge(7)
    world.settle(120)
    (opened,) = world.github.pulls
    world.merge(
        opened["number"], head_branch=opened["head"]["ref"], author=APP_BOT, head_sha=MERGED
    )
    world.settle(120)
    assert len(improve_runs(world)) == 1
    assert world.bridges.started == 1
    # and if the App itself merged it, the event is self-authored: still nothing
    world.merge(opened["number"], head_branch=opened["head"]["ref"], author=APP_BOT, sender=APP_BOT)
    world.settle(60)
    assert len(improve_runs(world)) == 1


def test_the_findings_flow_does_not_trigger_itself():
    """The findings flow writes nothing to GitHub, and the bridge's or the App's comments are
    not Qodo's: none of them starts a run."""
    world = findings_world()
    world.review()
    world.settle()
    assert world.github.pulls == [] and world.github.trees == []
    world.comment(author=APP_BOT)
    world.settle()
    assert len(world.runs()) == 1
