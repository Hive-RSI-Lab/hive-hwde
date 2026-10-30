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

CLI: --pcb board.kicad_pcb [--constraints c.json] [--out report.json]
constraints.json defaults to the board's own directory, as in the place
gate.   exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import matinglib  # noqa: E402
import placelib  # noqa: E402

SCRIPT = "check_mating"


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (default: next to the board)")
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
    violations, facts = matinglib.violations(placelib.PlaceModel(pcb), pcb,
                                             placement)
    for v in violations:
        v["check"] = "mating"
        v["source"] = SCRIPT
    return checklib.report(SCRIPT, str(pcb), violations,
                           connectors=facts), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
