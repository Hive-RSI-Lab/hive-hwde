"""parts-refdes: from a parts.json written as agents/part-sourcer.md asks
(no `refdes`), does bom_cpl give every hwde-led-test part its LCSC number?"""
import json

import _probe

ws = _probe.board_copy("hwde-led-test")
pj = ws / "parts" / "parts.json"
d = json.loads(pj.read_text())
for p in d["parts"]:
    p.pop("refdes", None)
pj.write_text(json.dumps(d, indent=1))
rep = ws / "bom.json"
r = _probe.hwde("bom_cpl", "--pcb", "kicad/hwde-led-test.kicad_pcb", "--out-dir",
                str(ws / "fab-probe"), "--parts", "parts/parts.json", "--offline",
                "--no-price-lookup", "--out", str(rep), cwd=ws)
if r.returncode == 2:
    raise SystemExit(f"bom_cpl errored: {(r.stdout + r.stderr).strip()[-300:]}")
uns = json.loads(rep.read_text()).get("unsourced") or []
asks = '"refdes"' in (_probe.ROOT / ".claude/skills/hwde/agents/part-sourcer.md").read_text()
if uns and not asks:
    _probe.stands(f"bom_cpl leaves {', '.join(uns)} with no LCSC number")
_probe.fixed("part-sourcer.md asks for refdes" if uns else
             "bom_cpl sources every part without per-line refdes")
