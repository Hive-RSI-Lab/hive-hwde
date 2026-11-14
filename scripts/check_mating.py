"""check_mating.py - can every mating connector be mated? (P8 verify suite)

One concern: a connector nobody can plug into. The rules and the family
table live in lib/matinglib.py and reference/connector_mating.yaml; the P6
place gate (place_metrics.py) runs the same rules, so this re-check catches
a fix or edit after placement that turns a connector round or puts a part
in front of it.

 - mating_faces_inward: a horizontal connector's mouth does not face its
   nearest board edge.
 - mating_mouth_inset: it faces the edge but sits too far inside it.
 - mating_zone_blocked: a part in the plug's insertion zone, or a tall part
   in a vertical connector's finger room.
 - mating_model_reversed: its copper faces the edge but its 3D model lies
   behind the contact pads, so renders show it turned round.
 - mating_mate_misaligned: a connector declared in constraints.json's
   placement.mates (its mate lies on the board, e.g. a display's flex
   tail) whose corridor does not reach its mouth; such a connector is held
   to its corridor instead of the board-edge rule.
 - mating_mate_invalid: a placement.mates entry that cannot be used.
 - mating_direction_unknown (warning): the mouth cannot be read from the
   pad layout.

CLI: --pcb board.kicad_pcb [--constraints c.json] [--parts DIR]
[--out report.json]
constraints.json defaults to the board's own directory, as in the place
gate; the parts dir to the board dir's parent's parts/ (P3's parts.json and
extractions). With it a connector whose footprint name no family matches is
matched on its value, MPN or LCSC category (#953), and a part's extraction
saying which end its contacts are at (mechanical.entry.contacts_at) sets its
mouth over the yaml and the pad rule (#952):
 - mating_mouth_disagrees (warning): the extraction and the yaml `mouth`
   entry or the pad rule read the mouth opposite ways.
exit 0/1/2 per SPEC section 6.

A J/P/CN/CON part whose footprint no family matches isn't judged: the report's
not_judged names each (a side-entry wire-to-board socket no row knows reads
the same as a test pad, until a row is added), and a board where nothing was
judged reports nothing_to_judge. One of them whose courtyard comes within
matinglib.EDGE_NEAR_MM of the outline is also in edge_unjudged ({ref,
footprint, pos, edge_gap_mm}): an edge connector nobody showed faces off the
board (#911, the laptop board's backwards microSD). verify_all --strict
makes each an error (mating_edge_unjudged), so the release gate fails until
a family row, a placement.mates entry or a waiver naming it covers it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import matinglib  # noqa: E402
import placelib  # noqa: E402

SCRIPT = "check_mating"
CONNECTOR_REF = re.compile(r"^(J|P|CN|CON)\d+$")


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (default: next to the board)")
    ap.add_argument("--parts", default=None,
                    help="parts dir with parts.json and extractions "
                         "(default: ../parts from the board's dir)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    cpath = Path(args.constraints) if args.constraints \
        else pcb.parent / "constraints.json"
    placement = None
    if args.constraints or cpath.is_file():
        placement = checklib.load_json(cpath, "constraints").get("placement")
    model = placelib.PlaceModel(pcb)
    parts = Path(args.parts) if args.parts else pcb.parent.parent / "parts"
    violations, facts = matinglib.violations(
        model, pcb, placement, parts if parts.is_dir() else None)
    for v in violations:
        v["check"] = "mating"
        v["source"] = SCRIPT
    judged = {f["ref"] for f in facts}
    unjudged = sorted(r for r in model.footprints
                      if CONNECTOR_REF.match(r) and r not in judged)
    left = [f"{r} ({matinglib._name(model.footprints[r].fpid)})"
            for r in unjudged]
    edge = []
    for r in unjudged:
        f = model.footprints[r]
        gap = matinglib.edge_gap_mm(model, f)
        if gap is not None and gap <= matinglib.EDGE_NEAR_MM:
            c = f.center_abs()
            edge.append({"ref": r, "footprint": matinglib._name(f.fpid),
                         "pos": [checklib.rnd(c[0]), checklib.rnd(c[1])],
                         "edge_gap_mm": checklib.rnd(gap, 2)})
    return checklib.report(
        SCRIPT, str(pcb), violations, connectors=facts, not_judged=left,
        edge_unjudged=edge,
        nothing_to_judge=None if facts else
        "no connector reference/connector_mating.yaml knows"), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
