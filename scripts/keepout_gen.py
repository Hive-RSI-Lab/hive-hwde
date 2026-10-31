"""keepout_gen - copper keep-outs (KiCad rule areas) from constraints.json
(SPEC P7).

constraints["placement"]["keepouts"] steer placement only. An entry that also
says "copper": true becomes a rule area on its side's copper layers ("side"
front -> F.Cu, back -> B.Cu, both or absent -> every copper layer) where no
track, via or pour may go; pads and footprints stay allowed, so an antenna's
own module can sit at its edge. Freerouting gets rule areas as DSN keepouts,
stitch_vias and planes_gen keep out of them, and DRC grades them.
"copper" may instead be a dict naming what is kept out, e.g. {"tracks": true,
"vias": true, "pours": false}. An optional "name" labels the area.
"layers": [...] names the copper layers outright (an inner layer alone, for
a reference-plane cutout under a launch or antenna) in place of "side".

constraints["rf_cutouts"] (check_rf) become rule areas too, keeping tracks,
vias and pours off their layers: a rect / poly, or a footprint's pads (all,
or the listed "pads") grown by "margin_mm", one area per separate piece, so
the cutout follows the part's copper.

Run it after placement and before planes_gen/route_auto (the ESP32 antenna on
lite-main, hifi-receiver-2, was drawn by a board-local script). Idempotent:
every area it drew before (named aiee_keepout_*) is replaced.

Contract (SPEC section 6):
  keepout_gen.py --pcb B.kicad_pcb [--constraints c.json] [--dry-run]
      [--out-report r.json]
  Payload: {areas: [{name, layers, reason, rect|poly}], removed}. exit 0;
  2 = error (board untouched).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import routelib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "keepout_gen"
PREFIX = "aiee_keepout"
KEPT = ("tracks", "vias", "pours")


def plan_areas(constraints: dict, copper_layers: list[str]) -> list[dict]:
    """constraints placement keepouts with "copper" -> set_rule_areas
    entries (pure)."""
    out = []
    keeps = ((constraints.get("placement") or {}).get("keepouts") or [])
    for i, k in enumerate(keeps):
        cu = k.get("copper")
        if not cu:
            continue
        side = k.get("side", "both")
        if k.get("layers"):
            layers = list(k["layers"])
            bad = [l for l in layers if l not in copper_layers]
            if bad:
                raise CheckError(f"placement.keepouts[{i}]: layers {bad} "
                                 "are not copper layers of the board")
        elif side == "front":
            layers = [copper_layers[0]]
        elif side == "back":
            layers = [copper_layers[-1]]
        elif side == "both":
            layers = list(copper_layers)
        else:
            raise CheckError(f"placement.keepouts[{i}]: side {side!r} is "
                             "not front, back or both")
        label = re.sub(r"[^A-Za-z0-9_]+", "_",
                       str(k.get("name") or k.get("reason") or i)).strip("_")
        area = {"name": f"{PREFIX}_{label or i}", "layers": layers,
                "reason": k.get("reason", "")}
        if isinstance(cu, dict):
            area.update({w: bool(cu.get(w, False)) for w in KEPT})
        else:
            area.update({w: True for w in KEPT})
        if "rect" in k:
            x1, y1, x2, y2 = (float(v) for v in k["rect"])
            area["rect"] = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
        elif "poly" in k:
            area["poly"] = [[float(x), float(y)] for x, y in k["poly"]]
        else:
            raise CheckError(f"placement.keepouts[{i}]: needs rect or poly")
        out.append(area)
    names = [a["name"] for a in out]
    for j, a in enumerate(out):          # unique names
        if names.count(a["name"]) > 1:
            a["name"] = f"{a['name']}_{j}"
    return out


def plan_cutouts(constraints: dict, bg) -> list[dict]:
    """constraints rf_cutouts -> set_rule_areas entries (check_rf's own
    geometry, so the area drawn is the one the check holds the board to)."""
    import check_rf  # noqa: PLC0415
    from shapely.geometry import Polygon  # noqa: PLC0415
    out = []
    for i, c in enumerate(constraints.get("rf_cutouts") or []):
        layers = list(c.get("layers") or [])
        bad = [l for l in layers if l not in bg.copper_layers]
        if not layers or bad:
            raise CheckError(f"rf_cutouts[{i}]: layers must name copper "
                             f"layers of the board (got {layers})")
        region = check_rf._region(bg, c, f"rf_cutouts[{i}]")
        label = re.sub(r"[^A-Za-z0-9_]+", "_", str(
            c.get("name") or c.get("ref") or i)).strip("_")
        pieces = [g for g in getattr(region, "geoms", [region])
                  if isinstance(g, Polygon) and g.area > 1e-6]
        for j, g in enumerate(pieces):
            g = g.simplify(0.01)
            area = {"name": f"{PREFIX}_cut_{label}"
                    + (f"_{j}" if len(pieces) > 1 else ""),
                    "layers": layers, "reason": c.get("reason", ""),
                    **{w: True for w in KEPT},
                    "poly": [[checklib.rnd(x, 4), checklib.rnd(y, 4)]
                             for x, y in list(g.exterior.coords)[:-1]]}
            out.append(area)
    return out


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (default: next to the board)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    cons = Path(args.constraints) if args.constraints else \
        pcb.parent / "constraints.json"
    constraints = checklib.load_json(cons, "constraints") \
        if cons.is_file() else {}
    bg = geom.BoardGeom.from_file(pcb)
    areas = plan_areas(constraints, bg.copper_layers) + \
        plan_cutouts(constraints, bg)
    old = [ra.get("name") for ra in bg.rule_areas
           if str(ra.get("name") or "").startswith(PREFIX)]
    facts = {"areas": areas, "removed": len(old), "constraints": str(cons),
             "dry_run": args.dry_run}
    if not args.dry_run and (areas or old):
        cli = env.find_kicad_cli()
        bp = env.find_kicad_python(cli) if cli else None
        if bp is None:
            raise CheckError("KiCad bundled python not found (env.py)")
        stage = Path(tempfile.mkdtemp(prefix=".aiee_keepout_", dir=pcb.parent))
        try:
            staged = stage / pcb.name
            shutil.copy2(pcb, staged)
            res = routelib.run_worker(bp, {
                "verb": "set_rule_areas", "board": str(staged),
                "out": str(staged), "prefix": PREFIX, "areas": areas}, stage)
            got = [ra.get("name") for ra in
                   geom.BoardGeom.from_file(staged).rule_areas
                   if str(ra.get("name") or "").startswith(PREFIX)]
            if sorted(got) != sorted(a["name"] for a in areas):
                raise CheckError(f"rule areas after the edit {sorted(got)} "
                                 "are not the planned ones (board untouched)")
            os.replace(staged, pcb)
            facts["removed"] = res["removed"]
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    payload = {"script": SCRIPT, "status": "pass", "board": str(pcb),
               "facts": facts}
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
