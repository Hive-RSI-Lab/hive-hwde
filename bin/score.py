"""hwde score: the share of gate steps, over all boards, with no workaround.

    hwde score            (or: python3 bin/score.py)

Each board under boards/ has up to five gate steps, the ones `hwde check-all`
re-runs (erc, drc_routed, verify, fab, dfm). So does each board docs/boards.md
records, the product boards built with hwde in other projects (the steps that
table gives it; the score never reads the other project's files). A step is charged when a row of
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
    path: Path | None              # None: a board in another project
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


def read_ledger(path: Path) -> list[Board]:
    """The boards docs/boards.md records: `| board | project | commit | steps
    | ... |`, steps as in workarounds.md."""
    out: list[Board] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = _ROW.match(line)
        if not m or m.group(1) == "board":
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        if len(cells) < 3 or not cells[0] or not cells[1]:
            raise ValueError(f"{path}:{n}: a row needs board, project, commit, steps")
        st = [s.strip() for s in cells[2].split(",") if s.strip() not in ("", "-")]
        for s in st:
            if s not in STEPS:
                raise ValueError(f"{path}:{n}: step {s!r} is not one of {', '.join(STEPS)}")
        out.append(Board(m.group(1), None, [s for s in STEPS if s in st]))
    return out


def all_boards() -> list[Board]:
    """This repo's boards, then the ledger's; a name is one board."""
    boards = read_boards(ROOT / "boards") + read_ledger(ROOT / "docs" / "boards.md")
    seen: set[str] = set()
    for b in boards:
        if b.name in seen:
            raise ValueError(f"board {b.name} is both under boards/ and in docs/boards.md")
        seen.add(b.name)
    return boards


def main() -> int:
    try:
        table = read_workarounds(ROOT / "docs" / "workarounds.md")
        boards = all_boards()
        names = {b.name for b in boards}
        for w in table.values():
            unknown = [b for b in w.boards if b != "*" and b not in names]
            if unknown:
                raise ValueError(f"workaround {w.id} names {', '.join(unknown)}, "
                                 f"not a board under boards/ or in docs/boards.md")
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
