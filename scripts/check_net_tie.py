"""check_net_tie.py - every net-tie footprint's pads are joined by its own
copper (P8 verify suite).

A net-tie footprint (KiCad's net_tie_pad_groups: a Bridged solder jumper, a
NetTie, a package or antenna whose pads its copper ties) joins the pads of
each group with copper it draws itself, a graphic on a copper layer. KiCad's
connectivity ignores footprint graphics and each pad keeps its own net, so
DRC passes the board the same when that copper is gone, and the pads are
open on the board that gets built. The hit (#1008): KRT's writer moved every
net-less copper graphic to silkscreen, a footprint's included, and two
SolderJumper-3 Bridged12 boot straps on a 6-layer board lost their bridges
at the escape step; a later silk clean-up deleted them.

One error per net-tie group that the footprint's copper (its pads and its
copper graphics, layer by layer; a plated pad joins its layers) leaves in
more than one piece:
  net_tie_open   the footprint, the group's pads, the pieces they fall in
                 and the layer the bridge belongs on (the footprint's side
                 when every pad of the group is on it); `moved_to` names the
                 silkscreen layer of a drawing of the footprint's that spans
                 the pieces, a bridge moved off copper
A group with fewer than two of its pads on the board is not judged.

The repair puts the bridge back on copper: update the footprint from its
library, or move the drawing back to its copper layer. A jumper meant to be
open takes a footprint without net_tie_pad_groups (an Open variant).

CLI: --pcb board.kicad_pcb [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402

SCRIPT = "check_net_tie"
SOURCE = SCRIPT  # verify_all, gate and the scorecard match a finding to its check by source
# copper this close (mm) touches: arcs and circles are sampled
TOUCH_MM = 1e-3


def _num_key(n: str):
    return (0, int(n), "") if n.isdigit() else (1, 0, n)


def pieces(pads, copper, group) -> list[list[str]]:
    """The pad numbers of `group` in the pieces the footprint's own copper
    leaves them in, one sorted list per piece. pads: the footprint's Pads;
    copper: its drawings on copper ({"layer", "shape"}). Pads of one number
    count as one pad: KiCad gives them one net, so DRC sees them apart."""
    n = len(pads)
    parent = list(range(n + len(copper)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[str, int] = {}
    for i, p in enumerate(pads):
        if p.number:
            parent[find(i)] = find(first.setdefault(p.number, i))
    layers = {ln for p in pads for ln in p.layers} | {c["layer"] for c in copper}
    for layer in layers:
        items = [(i, p.poly) for i, p in enumerate(pads) if p.on(layer)]
        items += [(n + k, c["shape"]) for k, c in enumerate(copper)
                  if c["layer"] == layer]
        for x, (i, a) in enumerate(items):
            for j, b in items[x + 1:]:
                if find(i) != find(j) and a.distance(b) <= TOUCH_MM:
                    parent[find(i)] = find(j)
    split: dict[int, set] = {}
    for i, p in enumerate(pads):
        if p.number in group:
            split.setdefault(find(i), set()).add(p.number)
    return sorted((sorted(s, key=_num_key) for s in split.values()),
                  key=lambda s: _num_key(s[0]))


def moved_to(tie: dict, split: list[list[str]]):
    """The layer of a silkscreen drawing of the footprint's that touches pads
    of two of the pieces (its bridge, moved off copper), or None."""
    by_piece = [[p for p in tie["pads"] if p.number in s] for s in split]
    for g in tie["graphics"]:
        if not g["layer"].endswith(".SilkS"):
            continue
        touched = sum(any(g["shape"].distance(p.poly) <= TOUCH_MM for p in piece)
                      for piece in by_piece)
        if touched >= 2:
            return g["layer"]
    return None


def check(pcb: Path) -> tuple[list[dict], dict, list[str]]:
    """(violations, what was seen, groups not judged)."""
    bg = geom.load_board(pcb)
    on_copper = set(bg.copper_layers)
    viols, not_judged = [], []
    seen = {"net_ties": len(bg.net_ties), "groups": 0}
    for tie in bg.net_ties:
        ref, pads = tie["ref"], tie["pads"]
        copper = [g for g in tie["graphics"] if g["layer"] in on_copper]
        for group in tie["groups"]:
            have = [n for n in dict.fromkeys(group)
                    if any(p.number == n for p in pads)]
            if len(have) < 2:
                not_judged.append(f"{ref} group {', '.join(group)}: "
                                  f"{len(have)} of its pads on the board")
                continue
            seen["groups"] += 1
            split = pieces(pads, copper, set(have))
            if len(split) < 2:
                continue
            members = [p for p in pads if p.number in have]
            shared = set(members[0].layers).intersection(
                *(p.layers for p in members[1:]))
            layer = tie["layer"] if tie["layer"] in shared else next(
                (ln for ln in bg.copper_layers if ln in shared), None)
            moved = moved_to(tie, split)
            where = f"on {layer}" if layer else "(no copper layer holds them all)"
            msg = (f"{ref} ({tie['lib']}): net-tie pads {', '.join(have)} are "
                   f"open {where}: the footprint's copper leaves them in "
                   f"{len(split)} pieces ("
                   + " | ".join(", ".join(s) for s in split) +
                   "), which DRC doesn't see (each pad keeps its own net and "
                   "footprint graphics aren't connectivity). ")
            if moved:
                msg += (f"A drawing of the footprint's on {moved} spans them: "
                        f"the bridge was moved off copper. ")
            msg += "Put the bridge back on copper (update the footprint from its library)"
            viols.append(checklib.violation(
                SCRIPT, "error",
                (sum(p.center[0] for p in members) / len(members),
                 sum(p.center[1] for p in members) / len(members)),
                layer, None, [ref], msg, SOURCE, kind="net_tie_open",
                footprint=tie["lib"], pads=have, pieces=split,
                nets=sorted({p.net for p in members if p.net}),
                moved_to=moved))
    return viols, seen, not_judged


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    viols, seen, not_judged = check(pcb)
    return checklib.report(
        SCRIPT, pcb, viols, checked=[seen],
        nothing_to_judge=None if seen["groups"] else
        "no net tie (net_tie_pad_groups) with two of a group's pads on the board",
        not_judged=not_judged), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
