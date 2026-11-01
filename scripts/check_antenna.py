"""check_antenna.py - a radio module's PCB antenna has no copper under it and
sits at the board edge (P8 verify suite).

The hit: hifi-receiver-2's core passed every gate with GND and +3V3 planes
on all four layers, and two tracks, under about 5.4 mm of its ESP32-WROVER-E
antenna (review-2 DIGITAL-19, found by hand with pcbnew scripts). A plane
under a PCB inverted-F antenna detunes it and costs 10-20 dB; nothing in
ERC or DRC knows where a module's antenna is.

Which parts have a PCB antenna: reference/rf_modules.yaml (by Value, then
footprint name), or constraints.json {"antennas": {ref: "pcb" |
"external"}}. Where the antenna is: the module body (its courtyard) beyond
the pad field (less PAD_MARGIN_MM), on the end with the longest pad-free
run, across the full body width; the antenna end is that courtyard edge. A module with no
courtyard, or no pad-free end of MIN_END_MM, is reported, not guessed.

Findings, per module (Espressif ESP32 Hardware Design Guidelines §1.4.8):
  antenna_copper            error: copper of any kind (zone fill, track, via,
                            another part's pad) under the antenna on a copper
                            layer, inside the board: one per layer
  antenna_inboard           warning: the antenna end is more than EDGE_MM
                            inside the board edge (the guideline wants it
                            over the edge, or at most 1 mm in)
  antenna_side_clearance    warning: copper closer than SIDE_MM beside the
                            antenna, along the board, where the board goes
                            on (the guideline's 15 mm clearance, or cut the
                            board away there)
  antenna_unknown_end       warning: a PCB-antenna module whose antenna end
                            can't be told from its footprint

CLI: --pcb board.kicad_pcb [--constraints c.json] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml
from shapely import affinity
from shapely.geometry import box
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
import placelib  # noqa: E402

SCRIPT = "check_antenna"
SOURCE = SCRIPT  # verify_all, gate and the scorecard match a finding to its check by source
MODULES_YAML = HERE.parent / "reference" / "rf_modules.yaml"
EDGE_MM = 1.0       # antenna end at most this far inside the board edge
SIDE_MM = 15.0      # copper-free beside the antenna along the board
# the antenna end must be a pad-free run at least this long (mm): the
# ESP32/ESP8266 modules' are 6-7 mm, a pad row's own gap is well under 3
MIN_END_MM = 3.0
# the antenna stops this short of the pad field: the strip by the end pins
# is the module's ground end, and a pour up to its GND pads is normal
# (lite-main's ANT_KEEPOUT stops 0.42 mm short of them)
PAD_MARGIN_MM = 1.0
TOL_MM2 = 0.01      # copper under the antenna below this is arc sampling


def load_modules(path: Path = MODULES_YAML) -> list[dict]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out = []
    for m in doc.get("modules") or []:
        out.append({**m, "_match": re.compile(m["match"], re.I),
                    "_external": re.compile(m.get("external") or "$^", re.I)})
    return out


def antenna_kind(value: str, fpid: str, modules: list[dict]) -> tuple:
    """("pcb" | "external" | None, the module row). The Value decides when
    it names a known module; the footprint name only when it doesn't."""
    for text in (value or "", fpid or ""):
        for m in modules:
            if m["_match"].search(text):
                return ("external" if m["_external"].search(text) else "pcb",
                        m)
    return None, None


def board_area(bg) -> "object":
    """The board outline less the windows cut out of it."""
    out = bg.outline
    for face in getattr(bg, "outline_faces", [])[1:]:
        if out.contains(face.representative_point()) and \
                face.area < out.area:
            out = out.difference(face)
    return out


def _to_local(g, f):
    return affinity.rotate(affinity.translate(g, -f.pos[0], -f.pos[1]),
                           f.angle, origin=(0, 0))


def _to_board(g, f):
    return affinity.translate(affinity.rotate(g, -f.angle, origin=(0, 0)),
                              f.pos[0], f.pos[1])


def antenna_geometry(f, pads) -> dict | None:
    """The antenna rectangle, its end edge and the two side bands, in the
    board frame, or None when the footprint has no pad-free end."""
    if f.courtyard_local is None or f.courtyard_missing or not pads:
        return None
    cy = f.courtyard_local
    cx0, cy0, cx1, cy1 = cy.bounds
    px0, py0, px1, py1 = unary_union(
        [_to_local(p.poly, f) for p in pads]).bounds
    ends = {"-y": py0 - cy0, "+y": cy1 - py1,
            "-x": px0 - cx0, "+x": cx1 - px1}
    side, depth = max(ends.items(), key=lambda kv: kv[1])
    depth -= PAD_MARGIN_MM
    if depth < MIN_END_MM:
        return None
    m = PAD_MARGIN_MM
    px0, py0, px1, py1 = px0 - m, py0 - m, px1 + m, py1 + m
    s = SIDE_MM
    if side == "-y":
        ant, end = box(cx0, cy0, cx1, py0), box(cx0, cy0, cx1, cy0)
        bands = [box(cx0 - s, cy0, cx0, py0), box(cx1, cy0, cx1 + s, py0)]
    elif side == "+y":
        ant, end = box(cx0, py1, cx1, cy1), box(cx0, cy1, cx1, cy1)
        bands = [box(cx0 - s, py1, cx0, cy1), box(cx1, py1, cx1 + s, cy1)]
    elif side == "-x":
        ant, end = box(cx0, cy0, px0, cy1), box(cx0, cy0, cx0, cy1)
        bands = [box(cx0, cy0 - s, px0, cy0), box(cx0, cy1, px0, cy1 + s)]
    else:
        ant, end = box(px1, cy0, cx1, cy1), box(cx1, cy0, cx1, cy1)
        bands = [box(px1, cy0 - s, cx1, cy0), box(px1, cy1, cx1, cy1 + s)]
    return {"antenna": _to_board(ant, f), "end": _to_board(end, f),
            "bands": [_to_board(b, f) for b in bands], "depth": depth,
            "body": _to_board(cy, f)}


def copper_in(bg, region, layer: str, skip_ref: str) -> dict:
    """Every copper item on `layer` that reaches into `region`."""
    found = {"shapes": [], "nets": set(), "tracks": 0, "vias": 0, "pads": [],
             "zones": 0}
    if region.is_empty:
        return found
    for z in bg.zones_of(layer=layer):
        hit = z.fill_on(layer).intersection(region)
        if hit.area > TOL_MM2:
            found["shapes"].append(hit)
            found["nets"].add(z.net)
            found["zones"] += 1
    for t in bg.tracks_of(layer=layer):
        if t.poly.intersects(region):
            found["shapes"].append(t.poly.intersection(region))
            found["nets"].add(t.net)
            found["tracks"] += 1
    for v in bg.vias_of(layer=layer):
        if v.poly.intersects(region):
            found["shapes"].append(v.poly.intersection(region))
            found["nets"].add(v.net)
            found["vias"] += 1
    for p in bg.pads_of(layer=layer):
        if p.ref != skip_ref and p.poly.intersects(region):
            found["shapes"].append(p.poly.intersection(region))
            found["nets"].add(p.net or "(no net)")
            found["pads"].append(f"{p.ref}.{p.number}")
    return found


def _overrides(constraints: dict | None) -> dict:
    ants = (constraints or {}).get("antennas") or {}
    bad = {r: k for r, k in ants.items() if k not in ("pcb", "external")}
    if bad:
        raise checklib.CheckError(
            f"constraints antennas: {bad} - each must be 'pcb' or "
            "'external'")
    return ants


def check(pcb: Path, constraints: dict | None = None,
          modules: list[dict] | None = None) -> tuple[list[dict], dict]:
    modules = load_modules() if modules is None else modules
    overrides = _overrides(constraints)
    bg = geom.load_board(pcb)
    model = placelib.PlaceModel(pcb)
    board = board_area(bg)
    viols, seen = [], []
    for ref in sorted(model.footprints):
        f = model.footprints[ref]
        value = (bg.footprints.get(ref) or {}).get("value") or ""
        kind, row = antenna_kind(value, f.fpid, modules)
        kind = overrides.get(ref, kind)
        if kind is None:
            continue
        fact = {"ref": ref, "value": value, "footprint": f.fpid,
                "antenna": kind}
        seen.append(fact)
        if kind != "pcb":
            continue
        g = antenna_geometry(f, bg.pads_of(ref=ref))
        if g is None:
            viols.append(checklib.violation(
                SCRIPT, "warning", f.pos, None, None, [ref],
                f"{ref} ({value or f.fpid}) has a PCB antenna, but its "
                f"footprint has no courtyard with a pad-free end of "
                f"{MIN_END_MM:g} mm or more, so the antenna can't be placed: "
                "check its keep-out by hand", SOURCE,
                kind="antenna_unknown_end"))
            continue
        ant = g["antenna"]
        fact.update(depth_mm=checklib.rnd(g["depth"], 2),
                    antenna_bbox=[checklib.rnd(v, 3) for v in ant.bounds])
        over = ant.intersection(board)
        fact["over_board_mm2"] = checklib.rnd(over.area, 2)
        for layer in bg.copper_layers:
            found = copper_in(bg, over, layer, ref)
            if not found["shapes"]:
                continue
            hit = unary_union(found["shapes"])
            if hit.area <= TOL_MM2:
                continue
            parts = []
            if found["zones"]:
                parts.append(f"{found['zones']} zone fill(s)")
            if found["tracks"]:
                parts.append(f"{found['tracks']} track(s)")
            if found["vias"]:
                parts.append(f"{found['vias']} via(s)")
            if found["pads"]:
                parts.append("pads " + ", ".join(sorted(found["pads"])))
            nets = sorted(n for n in found["nets"] if n)
            c = hit.centroid
            viols.append(checklib.violation(
                SCRIPT, "error", (c.x, c.y), layer,
                nets[0] if len(nets) == 1 else None, [ref],
                f"{ref} ({value or f.fpid}): {hit.area:.1f} mm2 of copper "
                f"on {layer} under its PCB antenna ({'; '.join(parts)}; "
                f"nets {', '.join(nets)}). Keep the antenna area free of "
                "copper on every layer (a keepout rule area, or cut the "
                "board away / move the module so the antenna overhangs)",
                SOURCE, kind="antenna_copper", nets=nets,
                area_mm2=checklib.rnd(hit.area, 2),
                antenna_bbox=fact["antenna_bbox"]))
        # the antenna end against the board edge
        mid = g["end"].centroid
        if board.contains(mid):
            inset = board.exterior.distance(mid)
            fact["end_inside_mm"] = checklib.rnd(inset, 2)
            if inset > EDGE_MM:
                viols.append(checklib.violation(
                    SCRIPT, "warning", (mid.x, mid.y), None, None, [ref],
                    f"{ref}: the antenna end is {inset:.1f} mm inside the "
                    f"board edge (at most {EDGE_MM:g} mm, or over the edge): "
                    "move the module to the edge or cut the board away "
                    "around the antenna", SOURCE, kind="antenna_inboard",
                    inset_mm=checklib.rnd(inset, 2)))
        else:
            fact["end_inside_mm"] = 0.0
        # copper beside the antenna, where the board goes on
        cleared = []
        for band in g["bands"]:
            on = band.intersection(board).difference(g["body"])
            if on.area <= TOL_MM2:
                continue
            near = [unary_union(copper_in(bg, on, layer, ref)["shapes"])
                    for layer in bg.copper_layers]
            near = [n for n in near if not n.is_empty and n.area > TOL_MM2]
            if near:
                cleared.append(min(ant.distance(n) for n in near))
        fact["side_cleared_mm"] = [checklib.rnd(d, 2) for d in cleared]
        short = [d for d in cleared if d < SIDE_MM]
        if short and over.area > TOL_MM2:
            viols.append(checklib.violation(
                SCRIPT, "warning", (ant.centroid.x, ant.centroid.y), None,
                None, [ref],
                f"{ref}: copper starts {min(short):.1f} mm beside the "
                f"antenna (Espressif asks for {SIDE_MM:g} mm clear along the "
                "edge, or the board cut away there): widen the keepout or "
                "notch the board", SOURCE, kind="antenna_side_clearance",
                cleared_mm=[checklib.rnd(d, 2) for d in short]))
        if row:
            fact["source"] = row.get("source")
    return viols, {"modules": seen}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    cons = (checklib.load_json(args.constraints, "constraints")
            if args.constraints else None)
    viols, seen = check(pcb, cons)
    return checklib.report(SCRIPT, pcb, viols, checked=[seen],
                           nothing_to_judge=None if seen.get("modules") else
                           "no module reference/rf_modules.yaml knows"
                           ), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
