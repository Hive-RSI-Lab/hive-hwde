"""backdrill_swig - BUNDLED-python SWIG worker for via_backdrill.py (#478).

Same job/result-file protocol as route_swig (routelib.run_worker):
  python backdrill_swig.py job.json
job: {"board": pcb, "out": pcb, "result": r.json,
      "drills": [{"uuid", "side": "F"|"B", "end": layer name, "size_mm"}, ...]}
Sets KiCad 10's back-drill on the named vias, keeps a side already drilled by
an earlier run, and saves. Result: {"ok": true, "set": n, "missing": [...]}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def run(job: dict) -> dict:
    import pcbnew
    import safelib
    bottom = pcbnew.BACKDRILL_MODE_BACKDRILL_BOTTOM
    top = pcbnew.BACKDRILL_MODE_BACKDRILL_TOP
    both = pcbnew.BACKDRILL_MODE_BACKDRILL_BOTH
    with safelib.board_locks(job):
        b = pcbnew.LoadBoard(job["board"])
        want: dict = {}
        for d in job["drills"]:
            want.setdefault(d["uuid"], []).append(d)
        done = 0
        for t in b.GetTracks():
            if t.GetClass() != "PCB_VIA" or t.m_Uuid.AsString() not in want:
                continue
            mode = t.GetBackdrillMode()
            for d in want.pop(t.m_Uuid.AsString()):
                layer = b.GetLayerID(d["end"])
                size = pcbnew.FromMM(float(d["size_mm"]))
                if d["side"] == "B":
                    mode = both if mode in (top, both) else bottom
                    t.SetBackdrillMode(mode)
                    t.SetBottomBackdrillSize(size)
                    t.SetBottomBackdrillLayer(layer)
                else:
                    mode = both if mode in (bottom, both) else top
                    t.SetBackdrillMode(mode)
                    t.SetTopBackdrillSize(size)
                    t.SetTopBackdrillLayer(layer)
                done += 1
        pcbnew.SaveBoard(job["out"], b)
    return {"ok": True, "set": done, "missing": sorted(want)}


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        res = run(job)
    except Exception as exc:  # noqa: BLE001 (the parent reads the result file)
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(job["result"]).write_text(json.dumps(res), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
