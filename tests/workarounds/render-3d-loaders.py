"""render-3d-loaders: does kc.render_env find KiCad's 3D loaders for the
kicad-cli the skill resolves itself (env.find_kicad_cli: the AppDir's own
bin/kicad-cli in the shared tools folder), with no symlink from bin/hwde?"""
import os

import _probe

os.environ.pop("APPDIR", None)
import env  # noqa: E402
import kc  # noqa: E402

cli = env.find_kicad_cli()
if cli is None:
    raise SystemExit("env.find_kicad_cli found no kicad-cli")
_, err = kc.render_env(cli)
if err:
    _probe.stands(err[:160])
_probe.fixed(f"render_env finds the loaders for {cli}")
