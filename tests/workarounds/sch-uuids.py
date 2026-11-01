"""sch-uuids: does regenerating hwde-led-test's schematic from kicad/gen/root.py
give back the UUIDs of the committed schematic's parts, which are what a board's
footprints link to? It regenerates the way a board is rebuilt, in place over the
committed file, in a scratch copy. Wires, labels and #PWR symbols don't count:
the redraw (schem_layout) replaces them, and nothing outside the sheet names them."""
import re
import subprocess
import sys

import _probe

ws = _probe.board_copy("hwde-led-test")
sch = ws / "kicad" / "hwde-led-test.kicad_sch"
uu = re.compile(r'\(uuid "([^"]+)"\)\s*\(property "Reference" "([^"#][^"]*)"')
old = uu.findall(sch.read_text())
r = subprocess.run([sys.executable, str(ws / "kicad" / "gen" / "root.py")],
                   capture_output=True, text=True)
if r.returncode:
    raise SystemExit(f"root.py failed: {(r.stdout + r.stderr).strip()[-300:]}")
new = uu.findall(sch.read_text())
if set(old) != set(new):
    _probe.stands(f"{len(set(old) - set(new))} of {len(set(old))} part UUIDs differ "
                  "after regenerating")
_probe.fixed("root.py reproduces every part UUID of the committed schematic")
