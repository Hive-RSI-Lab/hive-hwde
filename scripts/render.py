#!/usr/bin/env python
"""render.py - multi-view PCB render wrapper for VLM review (SPEC.md 6.2).

    render.py --views top,bottom,iso --w 2400 board.kicad_pcb [--out-dir DIR]
    render.py --views review --w 2400 board.kicad_pcb     (the fixed review set)

Renders each requested view to `<out-dir>/<stem>_<view>.png` with consistent
naming so the placement / verify-reviewer agents can reference views by name.
"iso" is orthographic isometric (kicad-cli `pcb render` has no iso side, so we
rotate -45,0,45). Thin driver over kc.render_png.

A name in VIEW_SETS stands for a fixed list of views. `review` is the set the
verify-reviewer judges placement and routing style from (top, bottom, iso), so
every review of every board looks at the same pictures; it may be mixed with
single view names and duplicates are dropped.

The render shows the assembled board: kc.render_png points kicad-cli at its
3D model loaders (APPDIR for a KiCad unpacked into a user prefix) and fails
the view when there are none, since the render would silently come out bare.
A model path that no longer resolves but whose file the workspace's own
lib/*.3dshapes holds is relinked in a temp copy of the board (models_relinked;
the board file is never written). Paths that still do not resolve are listed
in models_missing (those parts render as bare pads); the render still passes.

`edge` (#950) is one render per edge connector: every J/P/CN part within
matinglib.EDGE_NEAR_MM of the outline, framed on the part from outside the
board, looking in across the edge nearest it, tilted 30 deg above the board
plane, written to `<stem>_edge_<REF>.png`. Each output carries `ref`, `edge`
(the edge the camera looks in from), and `shows`, what check_mating says
should face the camera: "mouth" (the mouth faces that edge, as it must),
"back" (the mouth faces into the board: the closed back of the housing is at
the edge), "side", "top" (vertical entry), "unknown" (a horizontal family
whose mouth the pads do not tell), or "unjudged" (no family knows the part:
the render is the only look anyone gets). A person reviewing the release
looks at each and checks the picture agrees with `shows`.

JSON to stdout (or --out FILE): {script, outputs:[{view, path, status}],
models_missing, models_relinked, status}. Exit 0 = all views rendered, 2 = any failure.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import kc  # noqa: E402  (sibling wrapper module)

# view name -> (side, iso?) for kc.render_png
VIEWS = {
    "top": ("top", False),
    "bottom": ("bottom", False),
    "left": ("left", False),
    "right": ("right", False),
    "front": ("front", False),
    "back": ("back", False),
    "iso": ("top", True),
    "iso_ne": ("top", "-45,0,-45"),
    "iso_sw": ("top", "-45,0,135"),
    "iso_nw": ("top", "-45,0,-135"),
}

# fixed view sets: name -> views (agents/verify-reviewer.md renders `review`)
VIEW_SETS = {
    "review": ["top", "bottom", "iso"],
    # agents/mech-reviewer.md: all four elevations, where a tall part's side
    # ear shows, and the four corners, where the iso hides the far side
    "mech": ["top", "bottom", "front", "back", "left", "right",
             "iso", "iso_ne", "iso_sw", "iso_nw"],
}


# one render per edge connector (#950); not a fixed view, see edge_plans()
EDGE = "edge"
EDGE_TILT = -60      # degrees about X from straight down: 30 deg above the board
EDGE_MIN_MM = 5.0    # frame at least this much around a small part


def _edge_camera(normal: tuple[float, float]) -> float:
    """--rotate Z (degrees) that puts the camera outside the edge whose
    outward normal is `normal` (board frame, y down): +y edge 0, -y 180,
    -x 90, +x -90."""
    a = round(math.degrees(math.atan2(-normal[0], normal[1])), 1)
    return 180.0 if a == -180.0 else a + 0.0


def edge_plans(pcb: Path) -> list[dict]:
    """One plan per J/P/CN part within matinglib.EDGE_NEAR_MM of the board
    outline: {ref, footprint, side, edge, shows, rotate, pivot, zoom}. The
    camera looks in across the part's nearest edge; `shows` is what
    check_mating says should face it (module docstring)."""
    import check_mating
    import matinglib
    import placelib
    rep = check_mating.run(["--pcb", str(pcb)])[0]
    model = placelib.PlaceModel(pcb)
    outline = model.outline
    if outline is None or outline.is_empty:
        return []
    facts = {f["ref"]: f for f in rep.get("connectors", [])}
    unjudged = {e["ref"] for e in rep.get("edge_unjudged", [])}
    minx, miny, maxx, maxy = outline.bounds
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    span = max(maxx - minx, maxy - miny)
    plans = []
    for ref in sorted(model.footprints):
        f = model.footprints[ref]
        if not check_mating.CONNECTOR_REF.match(ref):
            continue
        gap = matinglib.edge_gap_mm(model, f)
        if gap is None or gap > matinglib.EDGE_NEAR_MM:
            continue
        body = f.extents_abs()
        fact = facts.get(ref)
        edge = (fact or {}).get("nearest_edge") or min(
            matinglib.AXES, key=lambda k: matinglib._edge_gap(
                outline, body, matinglib.AXES[k]))
        if ref in unjudged or fact is None:
            shows = "unjudged"
        elif fact.get("entry") != "horizontal":
            shows = "top"
        elif not fact.get("mouth"):
            shows = "unknown"
        elif fact["mouth"] == edge:
            shows = "mouth"
        elif fact["mouth"] == matinglib.OPPOSITE[edge]:
            shows = "back"
        else:
            shows = "side"
        bx0, by0, bx1, by1 = body.bounds
        x, y = (bx0 + bx1) / 2, (by0 + by1) / 2
        part = max(bx1 - bx0, by1 - by0, EDGE_MIN_MM)
        zoom = min(10.0, max(1.0, span / (1.2 * part)))
        plans.append({
            "ref": ref, "footprint": matinglib._name(f.fpid), "side": f.side,
            "edge": edge, "shows": shows,
            "family": (fact or {}).get("family"),
            "rotate": f"{EDGE_TILT},0,"
                      f"{_edge_camera(matinglib.AXES[edge]):g}",
            # kicad-cli: cm from the board centre, y up
            "pivot": f"{(x - cx) / 10:.3f},{(cy - y) / 10:.3f},0",
            "zoom": round(zoom, 2)})
    return plans


def render_edges(pcb: Path, out_dir: Path, cli, *, width: int, height: int,
                 quality: str) -> list[dict]:
    """Render every edge_plans() entry; one result per connector."""
    out = []
    for p in edge_plans(pcb):
        png = out_dir / f"{pcb.stem}_{EDGE}_{p['ref']}.png"
        r = kc.render_png(cli, pcb, png, side="top", width=width,
                          height=height, quality=quality, rotate=p["rotate"],
                          pivot=p["pivot"], zoom=p["zoom"])
        out.append({"view": f"{EDGE}_{p['ref']}", **p,
                    "path": out_dir_relpath(r, png), "status": r["status"],
                    "stderr_tail": r.get("stderr_tail", ""),
                    "models_missing": r.get("models_missing", []),
                    "models_relinked": r.get("models_relinked", [])})
    return out


def render_views(pcb: Path, views: list[str], out_dir: Path, *,
                 width: int, height: int, quality: str) -> dict:
    cli = kc.resolve_cli()
    out_dir.mkdir(parents=True, exist_ok=True)
    results, missing, relinked = [], set(), set()
    for view in views:
        if view == EDGE:
            for r in render_edges(pcb, out_dir, cli, width=width,
                                  height=height, quality=quality):
                missing.update(r.pop("models_missing"))
                relinked.update(r.pop("models_relinked"))
                results.append(r)
            continue
        side, iso = VIEWS[view]
        out = out_dir / f"{pcb.stem}_{view}.png"
        r = kc.render_png(cli, pcb, out, side=side, width=width, height=height,
                          quality=quality, rotate=(kc.ISO_ROTATE if iso is True else iso) or None)
        missing.update(r.get("models_missing", []))
        relinked.update(r.get("models_relinked", []))
        results.append({"view": view, "path": out_dir_relpath(r, out),
                        "status": r["status"], "stderr_tail": r.get("stderr_tail", "")})
    ok = all(r["status"] == "pass" for r in results)
    return {"script": "render", "input": str(pcb),
            "status": "pass" if ok else "error", "outputs": results,
            "models_missing": sorted(missing),
            "models_relinked": sorted(relinked)}


def out_dir_relpath(result: dict, out: Path) -> str:
    # kc.render_png reports the produced path in outputs[0] when it exists;
    # fall back to the intended path so failures still name their target.
    return result["outputs"][0] if result.get("outputs") else str(out)


def parse_views(spec: str) -> list[str]:
    views: list[str] = []
    for v in (v.strip() for v in spec.split(",")):
        for name in VIEW_SETS.get(v, [v] if v else []):
            if name not in views:
                views.append(name)
    bad = [v for v in views if v not in VIEWS and v != EDGE]
    if bad:
        raise ValueError(f"unknown view(s) {bad}; valid: "
                         f"{','.join([*VIEWS, EDGE, *VIEW_SETS])}")
    if not views:
        raise ValueError("no views requested")
    return views


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="input .kicad_pcb")
    ap.add_argument("--views", default="top,bottom,iso",
                    help="comma list from: " + ",".join([*VIEWS, EDGE, *VIEW_SETS]))
    ap.add_argument("--w", "--width", dest="width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--quality", default="high",
                    choices=["basic", "high", "user", "job_settings"])
    ap.add_argument("--out-dir", help="output directory (default: board's dir)")
    ap.add_argument("--out", help="write JSON summary here instead of stdout")
    args = ap.parse_args(argv)

    try:
        pcb = Path(args.input)
        views = parse_views(args.views)
        out_dir = Path(args.out_dir) if args.out_dir else pcb.resolve().parent
        report = render_views(pcb, views, out_dir, width=args.width,
                              height=args.height, quality=args.quality)
    except Exception:
        print(json.dumps({"script": "render", "status": "error",
                          "error": traceback.format_exc()}))
        return 2

    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
