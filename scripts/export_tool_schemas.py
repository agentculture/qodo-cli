"""Rewrite ``qodo/tool_schemas.json``: the recorded input schemas of the policy's tools.

The verb registry builds its params from this snapshot, so the CLI needs no network call at
parse time. The source is the recorded ``tools/list`` reply in ``tests/fixtures/qodo_mcp``;
re-record that first, then run ``python3 scripts/export_tool_schemas.py --write``. Without
``--write`` it only checks the snapshot (exit 1 when it differs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qodo.exposure import load_policy  # noqa: E402

RECORDED = ROOT / "tests" / "fixtures" / "qodo_mcp" / "tool_schemas.json"
SNAPSHOT = ROOT / "qodo" / "tool_schemas.json"


def build() -> str:
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    tools = {}
    for entry in load_policy().entries:
        tool = recorded[entry.tool]
        tools[entry.tool] = {
            "description": tool["description"],
            "annotations": tool["annotations"],
            "inputSchema": tool["inputSchema"],
        }
    doc = {"version": 1, "tools": dict(sorted(tools.items()))}
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = build()
    if "--write" in argv:
        SNAPSHOT.write_text(text, encoding="utf-8")
        return 0
    return 0 if SNAPSHOT.exists() and SNAPSHOT.read_text(encoding="utf-8") == text else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
