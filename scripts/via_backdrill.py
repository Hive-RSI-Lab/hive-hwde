#!/usr/bin/env python
"""via_backdrill.py - set KiCad 10's back-drill on the vias of the nets in
constraints `backdrill` (hive-hwde #478). Opt-in: nothing happens without the
list, and it plans only unless --apply is given.

constraints.json:
  "backdrill": [{"net": "/SIG", "drill_mm": 0.5, "depth_tol_mm": 0.1,
                 "max_stub_mm": 0.4, "vendor": "pcbway"}]

Each side of each via with a stub gets the drill viastub.plan_side picks: it
stops in the first copper layer past the exit layer that the depth tolerance
can't carry the drill through. Tool size: drill_mm, else the via's hole + the
fab profile's backdrill.drill_oversize_mm, else + 0.2. A side no layer can
serve is listed in `not_drillable`. A side already drilled is left alone.
check_via_stubs judges the result; fab_export ships the drill file and a note.

CLI: --pcb board.kicad_pcb --constraints constraints.json [--apply] [--out F]
     exit 0 done / 1 sides not drillable / 2 error.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import sexpdata

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
import viastub  # noqa: E402

SCRIPT = "via_backdrill"
WORKER = HERE / "lib" / "backdrill_swig.py"


def plan(pcb: Path, cons: dict) -> dict:
    bg = geom.load_board(pcb)
    st = viastub.Stack(bg.stackup)
    existing = viastub.read_backdrills(sexpdata.loads(pcb.read_text(encoding="utf-8")))
    vendor, known = viastub.board_vendor(bg.stackup)
    drills, skipped = [], []
    for e in viastub.backdrill_entries(cons):
        net = e.get("net")
        block = viastub.load_vendor_backdrill(e.get("vendor") or vendor) \
            if (known or e.get("vendor")) else {}
        tol = e.get("depth_tol_mm", block.get("depth_tolerance_mm",
                                              viastub.DEFAULT_DEPTH_TOL_MM))
        over = block.get("drill_oversize_mm", viastub.DEFAULT_OVERSIZE_MM)
        for v in bg.vias_of(net):
            used = viastub.used_layers(bg, v)
            if len(used) < 2:
                continue
            here = existing.get((round(v.at[0], 4), round(v.at[1], 4))) or {}
            for row in viastub.stubs(st, v, used):
                if row["side"] in here:
                    continue
                p = viastub.plan_side(st, v, used, row["side"], float(tol))
                if p is None:
                    skipped.append({"net": net, "at": list(v.at), "side": row["side"],
                                    "stub_mm": row["length_mm"]})
                    continue
                drills.append({"uuid": v.uuid, "net": net, "at": list(v.at),
                               "side": row["side"], "end": p["end"],
                               "exit": p["exit"], "left_mm": p["left_mm"],
                               "size_mm": float(e.get("drill_mm") or v.drill + float(over))})
    return {"drills": drills, "not_drillable": skipped}


def apply_plan(pcb: Path, drills: list[dict]) -> dict:
    import kc
    import routelib
    from lib import env
    py = env.find_kicad_python(kc.resolve_cli())
    if py is None:
        raise checklib.CheckError("KiCad's bundled python not found")
    with tempfile.TemporaryDirectory() as td:
        return routelib.run_worker(
            py, {"board": str(pcb.resolve()), "out": str(pcb.resolve()),
                 "drills": drills}, Path(td), worker=WORKER)


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", required=True)
    ap.add_argument("--apply", action="store_true", help="write the drills to the board")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    cons = checklib.load_json(args.constraints, "constraints")
    p = plan(pcb, cons)
    res = {"set": 0}
    if args.apply and p["drills"]:
        res = apply_plan(pcb, p["drills"])
    vios = [checklib.violation(
        SCRIPT, "warning", s["at"], None, s["net"], [],
        f"{s['net']}: no copper layer can end a back-drill on this via's "
        f"{'top' if s['side'] == 'F' else 'bottom'} side inside the depth "
        f"tolerance; its {s['stub_mm']} mm stub stays", SCRIPT,
        kind="backdrill_not_drillable") for s in p["not_drillable"]]
    return checklib.report(SCRIPT, pcb, vios, applied=bool(args.apply),
                           set=res.get("set", 0), **p), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
