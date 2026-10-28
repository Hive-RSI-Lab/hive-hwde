"""symdir: can kicad-sch-api read the stock libraries the skill points it at?

With bin/hwde's flat copies out of the way, the skill's own
env.export_kicad_symbol_dir() picks the symbol dir, and kicad-sch-api is
asked for power:GND and Device:R, the symbols every board places.
"""
import contextlib
import io
import os
import re

import _probe

for k in list(os.environ):
    if re.fullmatch(r"KICAD\d*_SYMBOL_DIR", k):
        del os.environ[k]
import env  # noqa: E402  (the skill's lib/env.py)

env.export_kicad_symbol_dir()
d = os.environ.get("KICAD_SYMBOL_DIR") or "(none)"
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    import kicad_sch_api as ksa
    cache = ksa.get_symbol_cache()
    missing = [s for s in ("power:GND", "Device:R") if cache.get_symbol(s) is None]
if missing:
    _probe.stands(f"kicad-sch-api finds no {', '.join(missing)} in {d}")
_probe.fixed(f"kicad-sch-api reads power:GND and Device:R from {d}")
