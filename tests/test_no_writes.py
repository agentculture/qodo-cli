"""No code path in the package pushes, merges, commits or opens a pull request (plan task t6).

A grep for the words would trip over prose ("merge" in a docstring) and miss an argv built from
pieces, so this scan reads the package's syntax trees instead. It collects every *argv-like
sequence*: a list or tuple literal, and the positional arguments of a call to a command runner
(``subprocess.*``, ``_git``, ``_gh``, ``_glab``, ``_run`` and the like). Each element that is a
string, or an f-string (its placeholders become ``{}``), is a token. A sequence is a write when:

* it holds a git subcommand that writes history, refs, objects or a remote (``push``,
  ``merge``, ``commit``, ``update-ref``, ``hash-object``, ...). Working-tree-only verbs
  (``checkout``, ``switch``, ``restore``) are left out on purpose: they are not a push, merge,
  commit or PR, and the word ``checkout`` is also a plain parameter name here;
* it holds ``pr`` beside a ``gh``/``glab`` verb that creates, merges or approves one;
* it sends a write method (``-X POST|PUT|PATCH|DELETE``, ``--method``) to an endpoint that creates
  or merges a pull/merge request or writes refs, commits or file contents;

and anywhere in the source, a GraphQL mutation that creates, merges or approves a pull request
or writes a commit or a ref is a write.

``review resolve`` legitimately writes through ``gh``: it POSTs a reply to
``pulls/{n}/comments/{id}/replies``, a reaction to ``pulls/comments/{id}/reactions`` and runs the
``resolveReviewThread`` mutation. None of these is a PR creation (``POST .../pulls``), a merge
(``.../pulls/{n}/merge``, ``mergePullRequest``) or a ref/commit/content write, so the scan passes
it, and the positive controls below prove the scan catches each forbidden shape.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "qodo"

GIT_WRITES = frozenset(
    {
        "push", "merge", "commit", "commit-tree", "rebase", "cherry-pick", "revert", "am",
        "apply", "update-ref", "symbolic-ref", "tag", "pull", "fetch", "reset", "write-tree",
        "mktree", "hash-object", "notes", "replace", "send-pack", "receive-pack",
    }
)  # fmt: skip
PR_WRITES = frozenset({"create", "merge", "ready", "review", "edit", "close", "reopen"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WRITE_ENDPOINTS = re.compile(
    r"(/pulls/?$|/pulls/[^/]+/(merge|reviews|update-branch)\b|/merges\b|/git/(refs|commits|"
    r"trees|blobs|tags)\b|/contents/|merge_requests/?$|merge_requests/[^/]+/(merge|approve)\b|"
    r"/repository/(commits|branches|files|tags)\b)"
)
WRITE_MUTATIONS = re.compile(
    r"\b(createPullRequest|mergePullRequest|enablePullRequestAutoMerge|addPullRequestReview|"
    r"submitPullRequestReview|createCommitOnBranch|updateRef|createRef|updateRefs|"
    r"mergeBranch|updatePullRequestBranch)\b"
)
RUNNERS = re.compile(r"^(_?(git|gh|glab)\w*|_run\w*|run|Popen|call|check_call|check_output)$")


def _text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
            for v in node.values
        )
    return None


def _func_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _sequences(tree: ast.AST) -> list[list[str]]:
    out: list[list[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            out.append([t for t in map(_text, node.elts) if t is not None])
        elif isinstance(node, ast.Call) and RUNNERS.match(_func_name(node)):
            out.append([t for t in map(_text, node.args) if t is not None])
    return [s for s in out if s]


def _sequence_problem(tokens: list[str]) -> str | None:
    hit = GIT_WRITES.intersection(tokens)
    if hit:
        return f"git write subcommand {sorted(hit)}"
    if "pr" in tokens and PR_WRITES.intersection(tokens):
        return "pull request write verb"
    methods = {t.upper() for t in tokens} & WRITE_METHODS
    for i, t in enumerate(tokens):
        if t in ("-X", "--method") and i + 1 < len(tokens):
            methods.add(tokens[i + 1].upper())
    if methods & WRITE_METHODS and any(WRITE_ENDPOINTS.search(t) for t in tokens):
        return "write method to a pull request / ref / content endpoint"
    return None


def problems(source: str) -> list[str]:
    tree = ast.parse(source)
    found = [p for p in map(_sequence_problem, _sequences(tree)) if p]
    for node in ast.walk(tree):
        text = _text(node)
        if text and WRITE_MUTATIONS.search(text):
            found.append(f"GraphQL write mutation {WRITE_MUTATIONS.search(text).group(0)}")
    return found


@pytest.mark.parametrize(
    "path", sorted(PACKAGE.rglob("*.py")), ids=lambda p: str(p.relative_to(PACKAGE))
)
def test_no_module_pushes_merges_commits_or_opens_a_pull_request(path):
    assert problems(path.read_text(encoding="utf-8")) == []


# --- positive controls: the scan catches each forbidden shape ---------------------------------


@pytest.mark.parametrize(
    "source",
    [
        '_git("push", "origin", "HEAD")',
        'subprocess.run([git, "-C", d, "merge", "--ff-only", ref])',
        'run(("commit", "-m", msg))',
        'argv = [git, "update-ref", "refs/heads/x", sha]',
        '_gh("pr", "create", "--title", t)',
        'subprocess.run([gh, "pr", "merge", str(n), "--squash"])',
        '_gh("api", f"repos/{o}/{r}/pulls", "-X", "POST", "-f", "title=x")',
        '_gh("api", f"repos/{o}/{r}/pulls/{n}/merge", "--method", "PUT")',
        '_gh("api", "repos/o/r/contents/README.md", "-X", "PUT")',
        '_glab("api", f"projects/{p}/merge_requests", "-X", "POST")',
        'Q = "mutation { mergePullRequest(input: {}) { clientMutationId } }"',
        'Q = f"mutation {{ createPullRequest(input: {x}) {{ id }} }}"',
    ],
)
def test_the_scan_catches_a_write(source):
    assert problems(source)


@pytest.mark.parametrize(
    "source",
    [
        '"""This never pushes, merges or commits; it is not a merge."""',
        '_gh("api", f"repos/{{owner}}/{{repo}}/pulls/{n}/comments/{c}/replies", "-X", "POST",'
        ' "-f", f"body={reply}")',
        '_gh("api", f"repos/{{owner}}/{{repo}}/pulls/comments/{c}/reactions", "-X", "POST")',
        'Q = "mutation($threadId:ID!){ resolveReviewThread(input:{threadId:$threadId}) { x } }"',
        '_gh("api", f"repos/{{owner}}/{{repo}}/pulls/{n}/comments", "--paginate")',
        '_git_read(["diff-tree", "-r", "-z", base, head])',
    ],
)
def test_the_scan_passes_reads_and_review_thread_writes(source):
    assert problems(source) == []
