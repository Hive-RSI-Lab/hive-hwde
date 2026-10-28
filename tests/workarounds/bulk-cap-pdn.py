"""bulk-cap-pdn: with only what schlib records on its own (no hand-written
`extra` association for C1), does check_pdn accept hwde-led-test's +5V rail?"""
import contextlib
import io
import json
import subprocess
import sys

import _probe

ws = _probe.board_copy("hwde-led-test")
sys.path.insert(0, str(ws / "kicad" / "gen"))
import root  # noqa: E402  (the board's generator, from the copy)

with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    sh = root.build()
    sh.save(ws.parent)
    dec = ws.parent / "decoupling.json"
    sh.emit_decoupling(dec)
rep = ws.parent / "pdn.json"
r = subprocess.run([sys.executable, str(_probe.SKILL / "check_pdn.py"),
                    "--pcb", str(ws / "kicad" / "hwde-led-test.kicad_pcb"),
                    "--constraints", str(ws / "kicad" / "constraints.json"),
                    "--decoupling", str(dec), "--out", str(rep)],
                   capture_output=True, text=True)
if r.returncode == 2:
    raise SystemExit(f"check_pdn errored: {(r.stdout + r.stderr).strip()[-300:]}")
errs = [v for v in json.loads(rep.read_text()).get("violations", [])
        if v.get("severity") == "error"]
if errs:
    _probe.stands(f"check_pdn: {errs[0].get('msg') or errs[0]}"[:200])
_probe.fixed(f"check_pdn passes +5V with schlib's {len(sh.decoupling)} own association(s)")
