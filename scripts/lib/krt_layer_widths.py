"""Run a KRT script with per-layer track widths (#489).

KRT (KiCadRoutingTools 0.19.0) draws each layer at
GridRouteConfig.layer_widths[layer] when that map is set, but only its own
--impedance option fills it, from KRT's own solver. hwde wants the widths
rules_gen wrote into the .kicad_dru (zrules.layer_geometry), so the router,
rules_gen and DRC agree. This launcher fills layer_widths from a JSON file
and then runs the KRT script unchanged:

  cd <krt plugins>; python krt_layer_widths.py <widths.json> route_diff.py ARGS...

widths.json = {"F.Cu": 0.0948, "In2.Cu": 0.0889}. A run that already sets
layer_widths (KRT's --impedance) keeps its own.
"""
from __future__ import annotations

import json
import os
import runpy
import sys


def main() -> None:
    widths_path, script = sys.argv[1], sys.argv[2]
    with open(widths_path, encoding="utf-8") as fh:
        widths = {str(k): float(v) for k, v in json.load(fh).items()}
    here = os.getcwd()                  # KRT runs with cwd = its plugins dir
    if here not in sys.path:
        sys.path.insert(0, here)
    import routing_config

    cls = routing_config.GridRouteConfig
    orig = cls.__init__

    def init(self, *a, **kw):
        if not kw.get("layer_widths"):
            kw["layer_widths"] = dict(widths)
        orig(self, *a, **kw)

    cls.__init__ = init
    sys.argv = [script, *sys.argv[3:]]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
