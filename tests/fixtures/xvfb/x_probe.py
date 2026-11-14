"""Run by tests/test_xvfb_noreset.py through bin/pcb-flow, so it gets the X
display pcb-flow's x() gives a script (#898).

  reset  set a root-window property, disconnect, read it back: an X server
         that reset when its last client left has lost it
  wx     start wx in KiCad's python, as every SWIG worker does
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "lib"))
import env  # noqa: E402

if sys.argv[1] == "reset":
    subprocess.run(["xprop", "-root", "-f", "HWDE_X_PROBE", "8s",
                    "-set", "HWDE_X_PROBE", "kept"], check=True)
    print(subprocess.run(["xprop", "-root", "HWDE_X_PROBE"],
                         capture_output=True, text=True).stdout.strip())
else:
    kpy = env.find_kicad_python(env.find_kicad_cli())
    out = subprocess.run([str(kpy), "-c",
                          "import pcbnew, wx; app = wx.App(); print('wx ok')"],
                         capture_output=True, text=True)
    print(out.stdout + out.stderr)
