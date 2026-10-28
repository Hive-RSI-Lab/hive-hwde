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

JSON to stdout (or --out FILE): {script, outputs:[{view, path, status}],
models_missing, models_relinked, status}. Exit 0 = all views rendered, 2 = any failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
}

# fixed view sets: name -> views (agents/verify-reviewer.md renders `review`)
VIEW_SETS = {
    "review": ["top", "bottom", "iso"],
}


def render_views(pcb: Path, views: list[str], out_dir: Path, *,
                 width: int, height: int, quality: str) -> dict:
    cli = kc.resolve_cli()
    out_dir.mkdir(parents=True, exist_ok=True)
    results, missing, relinked = [], set(), set()
    for view in views:
        side, iso = VIEWS[view]
        out = out_dir / f"{pcb.stem}_{view}.png"
        r = kc.render_png(cli, pcb, out, side=side, width=width, height=height,
                          quality=quality, rotate=kc.ISO_ROTATE if iso else None)
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
    bad = [v for v in views if v not in VIEWS]
    if bad:
        raise ValueError(
            f"unknown view(s) {bad}; valid: {','.join([*VIEWS, *VIEW_SETS])}")
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
                    help="comma list from: " + ",".join([*VIEWS, *VIEW_SETS]))
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
