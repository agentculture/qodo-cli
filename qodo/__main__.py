"""Entry point for ``python -m qodo``."""

from __future__ import annotations

import sys

from qodo.cli import main

if __name__ == "__main__":
    sys.exit(main())
