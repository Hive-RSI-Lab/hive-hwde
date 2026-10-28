"""hwde score: the share of gate steps, over all boards, with no workaround.

    hwde score            (or: python3 bin/score.py)

Each board under boards/ has up to five gate steps, the ones `hwde check-all`
re-runs (erc, drc_routed, verify, fab, dfm). A step is charged when a row of
docs/workarounds.md names it for that board: the step's input came through a
shim in bin/hwde or an edit by hand. The score is 100 x clean steps / steps,
higher is better. It reads files only; the test (check-all) is what holds the
table to the truth. Prints one line per board, then the number alone.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = ("erc", "drc_routed", "verify", "fab", "dfm")
GATES = ("erc", "drc_routed", "verify", "dfm")
_ROW = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]*)\s*\|(.*)\|\s*$")


@dataclass
class Workaround:
    id: str
    kind: str
    boards: list[str]
    steps: list[str]

    def charges(self, board: str) -> list[str]:
        return self.steps if "*" in self.boards or board in self.boards else []


@dataclass
class Board:
    name: str
    path: Path
    steps: list[str] = field(default_factory=list)


def read_workarounds(path: Path) -> dict[str, Workaround]:
    """The table's rows by id. A row is `| id | kind | boards | steps | ...`."""
    out: dict[str, Workaround] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = _ROW.match(line)
        if not m or m.group(1) == "id":
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        if len(cells) < 3:
            raise ValueError(f"{path}:{n}: a row needs id, kind, boards, steps")
        kind, boards, steps = cells[0], cells[1], cells[2]
        if kind not in ("shim", "hand"):
            raise ValueError(f"{path}:{n}: kind {kind!r} is not shim or hand")
        st = [] if steps in ("-", "") else [s.strip() for s in steps.split(",")]
        for s in st:
            if s not in STEPS:
                raise ValueError(f"{path}:{n}: step {s!r} is not one of {', '.join(STEPS)}")
        if m.group(1) in out:
            raise ValueError(f"{path}:{n}: {m.group(1)} is listed twice")
        out[m.group(1)] = Workaround(m.group(1), kind,
                                     [b.strip() for b in boards.split(",")], st)
    return out


def read_boards(root: Path) -> list[Board]:
    """Every workspace under boards/ (a dir with state.json), with the steps
    it has reached: each gate its state.json records as passed, and fab once
    fab/ holds a gerber zip."""
    out = []
    for st in sorted(root.glob("*/state.json")):
        d = json.loads(st.read_text(encoding="utf-8"))
        b = Board(d.get("board") or st.parent.name, st.parent)
        gates = d.get("gates") or {}
        for s in STEPS:
            if s == "fab":
                if list((st.parent / "fab").glob("*.zip")):
                    b.steps.append(s)
            elif (gates.get(s) or {}).get("status") == "pass":
                b.steps.append(s)
        out.append(b)
    return out


def main() -> int:
    try:
        table = read_workarounds(ROOT / "docs" / "workarounds.md")
        boards = read_boards(ROOT / "boards")
    except (OSError, ValueError) as e:
        print(f"score: {e}", file=sys.stderr)
        return 1
    total = clean = 0
    for b in boards:
        charged = {}
        for w in table.values():
            for s in w.charges(b.name):
                charged.setdefault(s, []).append(w.id)
        ok = [s for s in b.steps if s not in charged]
        total += len(b.steps)
        clean += len(ok)
        parts = [f"{s}" if s in ok else f"{s} ({', '.join(charged[s])})" for s in b.steps]
        print(f"{b.name}: {len(ok)}/{len(b.steps)} clean: {'; '.join(parts)}")
    if not total:
        print("score: no board has a gate step yet", file=sys.stderr)
        return 1
    print(f"workarounds listed: {len(table)} "
          f"({sum(w.kind == 'shim' for w in table.values())} shim, "
          f"{sum(w.kind == 'hand' for w in table.values())} by hand)")
    print(f"{100 * clean / total:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
