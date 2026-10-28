"""render-3d-loaders: does kc.render_env find KiCad's 3D loaders for the
kicad-cli bin/hwde runs, without bin/hwde's usr/lib symlink beside it?"""
import os
import shutil
from pathlib import Path

import _probe

os.environ.pop("APPDIR", None)
cli = os.environ.get("HWDE_KICAD_CLI")
if not cli:
    raise SystemExit("no HWDE_KICAD_CLI")
d = _probe.scratch() / "bin"
d.mkdir()
shutil.copy2(cli, d / "kicad-cli")
import kc  # noqa: E402

_, err = kc.render_env(d / "kicad-cli")
if err:
    _probe.stands(err[:160])
_probe.fixed("render_env finds the loaders for the wrapped kicad-cli")
