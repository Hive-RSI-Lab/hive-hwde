"""Per-(net, layer) copper-area oracle. RUNS UNDER KiCad's BUNDLED python.exe.

Independent ground truth for the S3 geometry library: builds each net's copper
on each layer as a pcbnew SHAPE_POLY_SET (tracks/pads/vias via
TransformShapeToPolygon + zone GetFilledPolysList), unions it, and reports the
area in mm^2. geom.py must reproduce these numbers from the raw s-expressions
(see tests/test_geom.py). Uses the SAME primitives as geom.net_copper() so the
two agree by construction where the geometry is parsed correctly.

    <bundled-python> area_oracle.py --pcb board.kicad_pcb [--out areas.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pcbnew  # noqa: E402  (bundled python only)

_IU2 = pcbnew.pcbIUScale.IU_PER_MM ** 2
_ERR = pcbnew.FromMM(0.005)
try:
    _ELOC = pcbnew.ERROR_INSIDE
except AttributeError:  # older SWIG enum exposure
    _ELOC = 0


def copper_layers(board) -> list[str]:
    out = []
    for lid in board.GetEnabledLayers().CuStack():
        out.append(board.GetLayerName(lid))
    return out


def net_areas(pcb: Path) -> dict:
    board = pcbnew.LoadBoard(str(pcb))
    copper = copper_layers(board)
    lid = {n: board.GetLayerID(n) for n in copper}
    acc: dict[tuple[str, str], "pcbnew.SHAPE_POLY_SET"] = {}

    def ps(net: str, layer: str):
        return acc.setdefault((net, layer), pcbnew.SHAPE_POLY_SET())

    for t in board.GetTracks():
        net = t.GetNetname()
        if isinstance(t, pcbnew.PCB_VIA):
            for n in copper:
                if t.IsOnLayer(lid[n]):
                    t.TransformShapeToPolygon(ps(net, n), lid[n], 0, _ERR, _ELOC)
        else:
            n = t.GetLayerName()
            if n in lid:
                t.TransformShapeToPolygon(ps(net, n), lid[n], 0, _ERR, _ELOC)

    for fp in board.GetFootprints():
        for pad in fp.Pads():
            net = pad.GetNetname()
            for n in copper:
                if pad.IsOnLayer(lid[n]):
                    pad.TransformShapeToPolygon(ps(net, n), lid[n], 0, _ERR, _ELOC)

    for z in board.Zones():
        net = z.GetNetname()
        for n in copper:
            if z.IsOnLayer(lid[n]):
                ps(net, n).BooleanAdd(z.GetFilledPolysList(lid[n]))

    out: dict[str, dict[str, float]] = {}
    for (net, layer), poly in acc.items():
        poly.Simplify()
        area = poly.Area() / _IU2
        if area > 1e-9:
            out.setdefault(net, {})[layer] = area
    return {"board": pcb.name, "copper_layers": copper, "net_area_mm2": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    try:
        result = net_areas(Path(args.pcb))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": "area_oracle", "status": "error",
                          "error": str(exc)}))
        return 2
    text = json.dumps({"script": "area_oracle", "status": "pass", **result}, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
