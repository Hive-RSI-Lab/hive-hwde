"""outline-silk: fit hwde-led-test's outline with board_edit at its default
margin: does the new edge leave the reference text whole?"""
import json

import _probe

B = "hwde-led-test"
ws = _probe.board_copy(B)
r = _probe.hwde("board_edit", "--pcb", f"kicad/{B}.kicad_pcb", "--outline", "fit",
                "--no-record", "--out-report", str(ws / "fit.json"), cwd=ws)
if r.returncode:
    raise SystemExit(f"board_edit failed: {(r.stdout + r.stderr).strip()[-300:]}")
_probe.gate("drc_routed", ws, B)
fails = json.loads((ws / "probe-drc_routed.json").read_text()).get("failing", [])
clipped = sorted({ref for v in fails if v.get("check") == "silk_edge_clearance"
                  for ref in v.get("refs") or []})
if clipped:
    _probe.stands(f"fit at the default margin clips the silk of {', '.join(clipped)}")
_probe.fixed("fit at the default margin leaves all silk inside the edge")
