"""docs/workarounds.md matches its probes (tests/workarounds/*.py).

The table records every shim and hand edit, so it is held to the truth on
every change: a row whose probe says its cause is fixed, or a cause that
stands with no row, fails here. bin/check_all.py runs the same probes after
it re-runs every board; this is them without the boards.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import check_all  # noqa: E402


def test_workarounds_table_matches_its_probes():
    table = check_all.read_workarounds(ROOT / "docs" / "workarounds.md")
    assert check_all.probes(table) == []
