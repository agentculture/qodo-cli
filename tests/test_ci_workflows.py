"""The CI workflows build and test the web UI: structure, not wording.

The workflow files are parsed (PyYAML), so a renamed job, a dropped step or a trigger path that
no longer lists ``web/**`` fails here rather than on a pull request.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # YAML 1.1 reads the key `on` as the boolean True
    data["on"] = data.pop(True, data.get("on"))
    return data


def run_lines(job: dict) -> list[str]:
    """Every command line of every ``run`` step, in order."""
    return [
        line.strip()
        for step in job["steps"]
        for line in step.get("run", "").splitlines()
        if line.strip()
    ]


def uses(job: dict, action: str) -> list[dict]:
    return [step for step in job["steps"] if str(step.get("uses", "")).startswith(action)]


def test_tests_workflow_has_a_web_job_that_installs_typechecks_tests_and_builds():
    job = load("tests.yml")["jobs"]["web"]
    assert job["defaults"]["run"]["working-directory"] == "web"
    (node,) = uses(job, "actions/setup-node@")
    assert node["with"]["cache"] == "npm"
    assert node["with"]["cache-dependency-path"] == "web/package-lock.json"
    runs = run_lines(job)
    # in this order: install from the lockfile, typecheck, unit tests, then the build
    ordered = ["npm ci", "npm run typecheck", "npm test", "npm run check", "npm run build"]
    positions = [runs.index(cmd) for cmd in ordered]
    assert positions == sorted(positions), runs


def test_the_web_lockfile_is_committed_and_matches_the_package():
    import json

    web = ROOT / "web"
    lock = json.loads((web / "package-lock.json").read_text(encoding="utf-8"))
    package = json.loads((web / "package.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] >= 2
    root = lock["packages"][""]
    assert root["dependencies"] == package["dependencies"]
    assert root["devDependencies"] == package["devDependencies"]


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_publish_triggers_on_web_and_the_package(event):
    paths = load("publish.yml")["on"][event]["paths"]
    for path in ("qodo/**", "web/**", "hatch_build.py", "pyproject.toml"):
        assert path in paths, (event, paths)


def test_every_publish_job_that_builds_a_distribution_builds_the_ui_first():
    jobs = load("publish.yml")["jobs"]
    builders = {
        name: job
        for name, job in jobs.items()
        if any("uv build" in line for line in run_lines(job))
    }
    assert set(builders) == {"test-publish", "publish"}
    for name, job in builders.items():
        steps = job["steps"]
        build_at = next(i for i, s in enumerate(steps) if "uv build" in s.get("run", ""))
        web_at = next(
            i
            for i, s in enumerate(steps)
            if "npm ci" in s.get("run", "") and "npm run build" in s.get("run", "")
        )
        node_at = next(
            i
            for i, s in enumerate(steps)
            if str(s.get("uses", "")).startswith("actions/setup-node@")
        )
        assert node_at < web_at < build_at, name
        assert steps[web_at]["working-directory"] == "web", name
        # a release must never ship a wheel without the UI
        assert steps[build_at]["env"]["QODO_REQUIRE_WEB"] == "1", name


def test_publish_test_job_runs_the_web_unit_tests_before_publishing():
    jobs = load("publish.yml")["jobs"]
    for name in ("test-publish", "publish"):
        assert "test" in jobs[name]["needs"]
    test = jobs["test"]
    runs = run_lines(test)
    # the unit tests and the build run before pytest, so the wheel test packages the real UI
    assert runs.index("npm ci") < runs.index("npm test") < runs.index("npm run build")
    assert runs.index("npm run build") < runs.index("uv run pytest -n auto -v")
