#!/usr/bin/env python3
"""Syntax-check the tree and run the margin-math assertions from selftest (no network, no AI)."""
from __future__ import annotations

import compileall
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ok = compileall.compile_dir(
        str(ROOT),
        quiet=1,
        rx=re.compile(r"[/\\](\.git|out|\.venv|__pycache__)[/\\]"),
    )
    if not ok:
        print("compileall failed")
        return 1
    import config

    got = [round(config.profit_at_roas(0, a)["min_price"]) for a in (5, 10, 20)]
    if got not in ([72, 85, 109], [73, 85, 109]):
        print("profit_at_roas mismatch:", got)
        return 1
    print("compile ok; profit_at_roas", got)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
