"""place_replicate - stamp a repeated channel's placement and routing (#482).

One job: a board with N identical channels gets ONE layout. constraints.json
`channels` names a source channel (a hierarchical sheet instance, or a ref
list) and its targets; this copies the source's relative placement into
every target under the declared transform (grid pitch, or explicit offsets
with 0/90/180/270 rotation), then the source's tracks and vias with nets
mapped through the pad correspondence. Semantics (identity, net mapping,
dropped copper, fallback for parts only one channel has):
scripts/lib/replicatelib.py.

  place_replicate.py --pcb B [--constraints C] [--netlist N]
                     [--stage place|route|all] [--apply] [--out R.json]

  --stage place   P6, after anneal: footprints only. Lay out (and anneal)
                  the source channel, then stamp; the stamped parts are
                  locked so later placement passes leave them.
  --stage route   P7, once the source channel is routed: copper only. The
                  targets must already sit where the stamp puts them
                  (`replicate_not_placed` otherwise). Then route_auto the
                  rest (the nets between channels).
  --stage all     both, in that order (default).

Without --apply it is a dry run: the ops and the report, board untouched.
The report's `channel_symmetry` (also in place_metrics / route_quality) is
measured on the board as it is after the run. Exit 0 clean / 1 warnings or
errors reported (unmatched parts, dropped copper) / 2 error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import checklib  # noqa: E402
import replicatelib  # noqa: E402
from checklib import CheckError  # noqa: E402


def _placed(pcb: Path, place_ops: list[dict]) -> list[str]:
    import placelib
    m = placelib.PlaceModel(pcb)
    bad = []
    for op in place_ops:
        if op["op"] != "place":
            continue
        f = m.footprints[op["ref"]]
        if (abs(f.pos[0] - op["x"]) > replicatelib.POS_TOL
                or abs(f.pos[1] - op["y"]) > replicatelib.POS_TOL
                or abs((f.angle - op["deg"] + 180) % 360 - 180)
                > replicatelib.ANG_TOL or f.side != op["side"]):
            bad.append(op["ref"])
    return bad


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (default: next to the board)")
    ap.add_argument("--netlist", default=None,
                    help="KiCad netlist with the sheet paths (default: "
                         "<board>.net); a board with footprint paths "
                         "needs none")
    ap.add_argument("--stage", choices=("place", "route", "all"),
                    default="all")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-lock", action="store_true",
                    help="leave the stamped parts unlocked")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    cpath = Path(args.constraints) if args.constraints else \
        pcb.parent / "constraints.json"
    if not cpath.is_file():
        raise CheckError(f"constraints not found: {cpath}")
    constraints = checklib.load_json(cpath, "constraints")
    if not constraints.get("channels"):
        raise CheckError(f"{cpath} declares no channels")
    netlist = Path(args.netlist) if args.netlist else None

    try:
        p = replicatelib.plan(pcb, constraints, netlist, stage=args.stage)
    except replicatelib.ReplicateError as e:
        raise CheckError(str(e)) from e
    viol = list(p["violations"])
    applied = {"place": 0, "route": 0}
    if args.stage == "route":
        full = replicatelib.plan(pcb, constraints, netlist, stage="place")
        stamped = [op for op in full["place_ops"] if op["op"] == "place"]
        off = _placed(pcb, stamped)
        if off:
            raise CheckError("replicate_not_placed: run --stage place first; "
                             f"not at the stamped position: {', '.join(off)}")
    if args.apply and p["place_ops"]:
        import place_edit
        ops = list(p["place_ops"])
        if not args.no_lock:
            ops += [{"op": "lock", "ref": op["ref"], "locked": True}
                    for op in p["place_ops"] if op["op"] == "place"]
        place_edit.apply_ops(pcb, ops)
        applied["place"] = len(p["place_ops"])
        if args.stage == "all":
            # copper is planned against the board as stamped
            p2 = replicatelib.plan(pcb, constraints, netlist, stage="route")
            p["route_ops"] = p2["route_ops"]
            viol = [v for v in viol if v["kind"] not in (
                "replicate_track_dropped", "replicate_not_copied")] + [
                v for v in p2["violations"] if v["kind"] in (
                    "replicate_track_dropped", "replicate_not_copied")]
    if args.apply and p["route_ops"]:
        import route_edit
        route_edit.apply_ops(pcb, p["route_ops"])
        applied["route"] = len(p["route_ops"])
    sym = replicatelib.symmetry(pcb, constraints, netlist,
                                routing=args.stage != "place")
    payload = checklib.report("place_replicate", str(pcb), viol,
                              replicate=p["report"], channel_symmetry=sym,
                              place_ops=p["place_ops"],
                              route_ops=p["route_ops"], applied=applied,
                              dry_run=not args.apply)
    return payload, args.out


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("place_replicate", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
