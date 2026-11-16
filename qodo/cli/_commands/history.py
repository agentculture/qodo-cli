"""``qodo-cli history`` — verbs generated from the verb registry (``qodo.registry``).

The commands under this noun are the exposure policy's tools that carry this noun, with flags built
from their recorded input schemas (see ``qodo.cli._build``); nothing is hand-written here.
"""

from __future__ import annotations

import argparse

from qodo.cli import _build


def register(sub: argparse._SubParsersAction) -> None:
    _build.register_noun(sub, "history")
