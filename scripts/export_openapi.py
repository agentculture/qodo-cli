"""Check, or with ``--write`` rewrite, ``api/openapi.json``: the served schema, pinned.

The committed file must equal what ``qodo.server.app.create_app(...).openapi()`` serves for the
default verb registry (a test checks it). After a verb, a params schema, a role or a route
changes, run ``uv run python scripts/export_openapi.py --write`` and commit the file. Without
``--write`` it only checks (exit 1 when the file is stale). ``--path`` points at another file.
Needs the ``server`` extra (FastAPI).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PINNED = ROOT / "api" / "openapi.json"


class _NoTokens:
    def authenticate(self, presented):
        return None


def build() -> str:
    from qodo.server.app import create_app, render_openapi

    return render_openapi(create_app(tokens=_NoTokens(), web_dist=None).openapi())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the file")
    parser.add_argument("--path", type=Path, default=PINNED, help="the file (default: the pinned)")
    args = parser.parse_args(argv)
    text = build()
    current = args.path.read_text(encoding="utf-8") if args.path.exists() else None
    if args.write:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(text, encoding="utf-8")
        print(f"wrote {args.path}")
        return 0
    if current != text:
        print(
            f"{args.path} is stale: run `uv run python scripts/export_openapi.py --write`",
            file=sys.stderr,
        )
        return 1
    print(f"{args.path} is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
