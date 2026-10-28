"""sch-uuids: does regenerating hwde-led-test's schematic from kicad/gen/root.py
give back the committed schematic's UUIDs?"""
import re
import subprocess
import sys

import _probe

ws = _probe.board_copy("hwde-led-test")
out = ws.parent / "regen"
out.mkdir()
r = subprocess.run([sys.executable, str(ws / "kicad" / "gen" / "root.py"), str(out)],
                   capture_output=True, text=True)
if r.returncode:
    raise SystemExit(f"root.py failed: {(r.stdout + r.stderr).strip()[-300:]}")
uu = re.compile(r'\(uuid "([^"]+)"\)')
old = set(uu.findall((ws / "kicad" / "hwde-led-test.kicad_sch").read_text()))
new = set(uu.findall((out / "hwde-led-test.kicad_sch").read_text()))
if old != new:
    _probe.stands(f"{len(old - new)} of {len(old)} UUIDs differ after regenerating")
_probe.fixed("root.py reproduces every UUID of the committed schematic")
