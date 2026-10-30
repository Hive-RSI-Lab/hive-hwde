"""core: remove dangling copper reported by the drc_routed gate, repeatedly
(a ripped stub can leave the next segment dangling).
  python3 kicad/gen/rip_dangling.py PCB WORKSPACE [rounds] [NET,NET,...]
Only nets in the list are touched when one is given (a route broken on
purpose would otherwise be eaten back from both ends)."""
import json
import subprocess
import sys
import os
pcb, ws = sys.argv[1], sys.argv[2]
rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 6
only = set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None
H = ["sh", os.environ["HIVE_MODULES"] + "/hive-hwde/bin/hwde"]
env = dict(os.environ)
out = os.path.expanduser("~/scratch/rip_dangling_drc.json")
kpy = os.path.join(os.path.dirname(subprocess.run(H + ["env"], capture_output=True, text=True).stdout.split("HWDE_KICAD_CLI='")[1].split("'")[0]), "python3")
here = os.path.dirname(os.path.abspath(__file__))
for r in range(rounds):
    subprocess.run(H + ["gate", "--gate", "drc_routed", pcb, "--workspace", ws, "--out", out], capture_output=True)
    d = json.load(open(out))
    args = []
    for f in d["failing"]:
        if f["check"] in ("track_dangling", "via_dangling") and (only is None or f["net"] in only):
            args += [f["net"], str(f["pos"][0]), str(f["pos"][1])]
    print("round", r, "dangling", len(args) // 3, flush=True)
    if not args:
        break
    subprocess.run(["xvfb-run", "-a", "-s", "-extension GLX", kpy, os.path.join(here, "rip_at.py"), pcb] + args, capture_output=True)
