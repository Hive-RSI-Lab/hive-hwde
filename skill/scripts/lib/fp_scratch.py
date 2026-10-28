#!/usr/bin/env python
"""fp_scratch.py - SWIG worker: build a scratch board of loose footprints.

BUNDLED python only (launched by fpfix/lib_pull through env.find_kicad_python).

One instance of each requested footprint, spaced far enough apart that no two
can interact, inside a bare Edge.Cuts rectangle. Running `kc.py drc
--severity-all` on the result therefore reports INTRA-footprint findings only -
the measurement method every library claim in LEARNINGS/EDITS.md was verified
with (LEARNINGS 2026-07-28 [easyeda2kicad][drc]).

Job JSON on argv[1]:
  pretty   the .pretty directory
  names    [footprint name, ...]  (default: every .kicad_mod in the dir)
  out      output .kicad_pcb path
  spacing  grid pitch between parts, mm (default 30)

Result JSON to stdout: {status, out, placed[], missing[]}. Exit 0 ok, 2 error.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pcbnew  # noqa: E402  (bundled python only)

EDGE_W = 0.1
MARGIN = 10.0


def _mm(x: float, y: float):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def build(job: dict) -> dict:
    pretty = Path(job["pretty"])
    names = job.get("names")
    if not names:
        names = sorted(p.name[:-len(".kicad_mod")]
                       for p in pretty.glob("*.kicad_mod"))
    spacing = float(job.get("spacing", 30.0))
    out = Path(job["out"])

    board = pcbnew.CreateEmptyBoard()
    cols = max(1, int(math.ceil(math.sqrt(len(names)))))
    placed, missing = [], []
    for i, name in enumerate(names):
        fp = pcbnew.FootprintLoad(str(pretty), name)
        if fp is None:
            missing.append(name)
            continue
        x = MARGIN + spacing / 2 + (i % cols) * spacing
        y = MARGIN + spacing / 2 + (i // cols) * spacing
        fp.SetPosition(_mm(x, y))
        fp.SetReference(f"X{i + 1}")
        board.Add(fp)
        placed.append(name)

    rows = int(math.ceil(len(names) / cols))
    w = MARGIN * 2 + cols * spacing
    h = MARGIN * 2 + rows * spacing
    for a, b in (((0, 0), (w, 0)), ((w, 0), (w, h)), ((w, h), (0, h)), ((0, h), (0, 0))):
        seg = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(pcbnew.FromMM(EDGE_W))
        seg.SetStart(_mm(*a))
        seg.SetEnd(_mm(*b))
        board.Add(seg)

    out.parent.mkdir(parents=True, exist_ok=True)
    board.Save(str(out))
    return {"status": "ok", "out": str(out), "placed": placed, "missing": missing}


def main() -> int:
    try:
        job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        print(json.dumps(build(job)))
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(json.dumps({"status": "error", "error": str(exc),
                          "trace": traceback.format_exc()}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
