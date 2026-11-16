"""The exposure policy: which upstream Qodo MCP tools are exposed, as what, and where.

The policy is data, held in a JSON file (``exposure_default.json`` ships in the package; an
operator can load another with :func:`load_policy`). Each entry names an upstream tool, the verb it
gets and the surfaces (``cli``, ``api``, ``mcp``) it appears on. A tool that is not named is
refused. An entry marks ``allow_write`` for a tool whose upstream annotation is not read-only; the
gateway refuses such a tool without it. Standard library only.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

SURFACES = ("cli", "api", "mcp")
DEFAULT_POLICY_PATH = Path(__file__).with_name("exposure_default.json")


@dataclass(frozen=True)
class Exposure:
    """One exposed upstream tool."""

    tool: str
    verb: str
    surfaces: tuple[str, ...]
    allow_write: bool = False


class ExposurePolicy:
    """The named tools; anything else is refused."""

    def __init__(self, entries: Iterable[Exposure]) -> None:
        self._by_tool: dict[str, Exposure] = {}
        verbs: set[str] = set()
        for entry in entries:
            if entry.tool in self._by_tool:
                raise _bad(f"tool {entry.tool!r} is named twice")
            if entry.verb in verbs:
                raise _bad(f"verb {entry.verb!r} is named twice")
            verbs.add(entry.verb)
            self._by_tool[entry.tool] = entry

    @property
    def entries(self) -> list[Exposure]:
        return list(self._by_tool.values())

    def get(self, tool: str) -> Exposure | None:
        return self._by_tool.get(tool)

    def require(self, tool: str) -> Exposure:
        """The entry for ``tool``, or a refusal (exit 1) when the policy does not name it."""
        entry = self._by_tool.get(tool)
        if entry is None:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"tool {tool!r} is not exposed by the exposure policy",
                remediation="name the tool in the exposure policy to expose it",
            )
        return entry

    def for_verb(self, verb: str) -> Exposure | None:
        return next((e for e in self._by_tool.values() if e.verb == verb), None)

    def on_surface(self, surface: str) -> list[Exposure]:
        return [e for e in self._by_tool.values() if surface in e.surfaces]


def _bad(message: str) -> CliError:
    return CliError(
        code=EXIT_ENV_ERROR,
        message=f"invalid exposure policy: {message}",
        remediation="fix the exposure policy file (see qodo/exposure_default.json)",
    )


def _entry(raw: Any) -> Exposure:
    if not isinstance(raw, dict):
        raise _bad("each tool entry must be an object")
    unknown = set(raw) - {"tool", "verb", "surfaces", "allow_write"}
    if unknown:
        raise _bad(f"unknown key(s) {sorted(unknown)}")
    tool, verb = raw.get("tool"), raw.get("verb")
    if not isinstance(tool, str) or not tool or not isinstance(verb, str) or not verb.strip():
        raise _bad("each entry needs a non-empty string tool and verb")
    surfaces = raw.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        raise _bad(f"{tool}: surfaces must be a non-empty list")
    for s in surfaces:
        if s not in SURFACES:
            raise _bad(f"{tool}: unknown surface {s!r} (known: {', '.join(SURFACES)})")
    allow = raw.get("allow_write", False)
    if not isinstance(allow, bool):
        raise _bad(f"{tool}: allow_write must be true or false")
    return Exposure(tool, verb.strip(), tuple(dict.fromkeys(surfaces)), allow)


def parse_policy(data: Any) -> ExposurePolicy:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise _bad("expected an object with version 1")
    tools = data.get("tools")
    if not isinstance(tools, list):
        raise _bad("tools must be a list")
    return ExposurePolicy(_entry(t) for t in tools)


def load_policy(path: Path | str | None = None) -> ExposurePolicy:
    """Load the policy from ``path``; without one, from the file ``QODO_EXPOSURE_POLICY``
    names, and without that, the packaged ``exposure_default.json``. An operator's file
    replaces the shipped policy whole: a tool it does not name is not exposed."""
    target = Path(path or os.environ.get(ENV_POLICY) or DEFAULT_POLICY_PATH)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot read the exposure policy {target}: {err.strerror or err}",
            remediation="check the policy path",
        ) from err
    except json.JSONDecodeError as err:
        raise _bad(f"{target} is not valid JSON: {err}") from err
    return parse_policy(data)


#: The environment variable naming an operator's exposure policy file.
ENV_POLICY = "QODO_EXPOSURE_POLICY"


def is_read_only(annotations: Any) -> bool:
    """True only when the annotations say ``readOnlyHint`` is true and do not say
    ``destructiveHint`` is true; absent annotations mean not read-only."""
    if not isinstance(annotations, dict):
        return False
    return (
        annotations.get("readOnlyHint") is True and annotations.get("destructiveHint") is not True
    )
