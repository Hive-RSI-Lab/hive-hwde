"""krt-dir: does hwde find the Hive's KiCadRoutingTools?

KRT 0.19.0 is in the Hive's tools folder (kicad-routing-tools/), but bin/hwde
doesn't point HWDE_KRT_DIR at it and env.find_krt() looks only under the
skill's tools/krt, so route_auto's KRT finish pass is skipped. The hi-fi
boards in hifi-receiver-2 export HWDE_KRT_DIR themselves before routing
(lite-main's build.sh, core's kicad/gen/route.sh). Fixed when, with a tools
folder holding kicad-routing-tools/KiCadRoutingTools-*/plugins and no pin,
the environment bin/hwde sets up makes find_krt() return it.
"""
import os
import shlex
import subprocess
import sys

import _probe

tools = _probe.scratch() / "tools"
plugins = tools / "kicad-routing-tools" / "KiCadRoutingTools-0.19.0" / "plugins"
plugins.mkdir(parents=True)
base = {k: v for k, v in os.environ.items()
        if k not in ("HWDE_KRT_DIR", "HWDE_TOOLS_DIR")}
base["HIVE_TOOLS"] = str(tools)
r = subprocess.run([_probe.HWDE, "env"], cwd=_probe.ROOT, env=base,
                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
if r.returncode != 0:
    raise SystemExit(f"hwde env errored: {(r.stdout + r.stderr).strip()[-300:]}")
env = dict(base)
for line in r.stdout.splitlines():
    if line.startswith("export ") and "=" in line:
        k, v = line[len("export "):].split("=", 1)
        env[k] = shlex.split(v)[0] if v else ""
f = subprocess.run([sys.executable, "-c", "import env; print(env.find_krt())"],
                   cwd=_probe.ROOT, env=env, capture_output=True, text=True)
if f.returncode != 0:
    raise SystemExit(f"find_krt errored: {f.stderr.strip()[-300:]}")
got = f.stdout.strip()
if got == str(plugins):
    _probe.fixed("find_krt() finds the tools folder's KRT")
_probe.stands(f"find_krt() gives {got} with KRT in the tools folder")
