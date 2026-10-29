"""Flip fixture builder + pad-position oracle. RUNS UNDER KiCad's BUNDLED python.

Loads a golden board, flips the named footprints to the back side (pcbnew
Flip(), left-right mirror - the same call pcb_build.py uses for side:"bottom"),
saves the result to --out, and dumps every flipped footprint's pad ground truth
(absolute position, copper layers, orientation) as JSON to --dump.

The S3 test compares geom.py's parsed pad centers/layers on the SAVED file
against this dump, validating the flip transform that no golden board exercises
(PROGRESS V10).

    <bundled-python> flip_fixture.py --pcb in.kicad_pcb --refs C10,J1 \
        --out flipped.kicad_pcb --dump pads.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pcbnew  # noqa: E402  (bundled python only)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--refs", required=True, help="comma-separated references")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dump", required=True)
    args = ap.parse_args()
    refs = [r.strip() for r in args.refs.split(",") if r.strip()]

    board = pcbnew.LoadBoard(args.pcb)
    wanted = {fp.GetReference(): fp for fp in board.GetFootprints()
              if fp.GetReference() in refs}
    missing = set(refs) - set(wanted)
    if missing:
        print(json.dumps({"script": "flip_fixture", "status": "error",
                          "error": f"refs not found: {sorted(missing)}"}))
        return 2

    for ref in refs:
        fp = wanted[ref]
        try:
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFTRIGHT)
        except AttributeError:  # older SWIG enum name
            fp.Flip(fp.GetPosition(), True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not board.Save(str(out)):
        print(json.dumps({"script": "flip_fixture", "status": "error",
                          "error": f"save failed: {out}"}))
        return 2

    # Reload the SAVED file so the dump reflects exactly what geom.py parses.
    # Pads are a LIST: KiCad pad numbers are not unique (e.g. multi-pad "SH"
    # shields), so a dict keyed by number would silently collapse them.
    board2 = pcbnew.LoadBoard(str(out))
    copper = [board2.GetLayerName(l) for l in board2.GetEnabledLayers().CuStack()]
    lid = {n: board2.GetLayerID(n) for n in copper}
    dump: dict = {}
    for fp in board2.GetFootprints():
        ref = fp.GetReference()
        if ref not in refs:
            continue
        pads = []
        for pad in fp.Pads():
            p = pad.GetPosition()
            pads.append({
                "number": pad.GetNumber(),
                "at": [pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)],
                "layers": [n for n in copper if pad.IsOnLayer(lid[n])],
                "net": pad.GetNetname(),
            })
        dump[ref] = {
            "pos": [pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y)],
            "orientation": fp.GetOrientationDegrees(),
            "flipped": fp.IsFlipped(),
            "pads": pads,
        }
    Path(args.dump).write_text(
        json.dumps({"script": "flip_fixture", "status": "pass", "refs": dump},
                   indent=1), encoding="utf-8")
    print(json.dumps({"script": "flip_fixture", "status": "pass",
                      "out": str(out), "dump": args.dump}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
