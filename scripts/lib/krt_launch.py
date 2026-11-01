"""Run a KRT script with hwde's patches (#489, #529).

  cd <krt plugins>
  python krt_launch.py [--layer-widths W.json] [--via-joint-ends] SCRIPT ARGS...

--layer-widths (#489): KRT (KiCadRoutingTools 0.19.0) draws each layer at
  GridRouteConfig.layer_widths[layer] when that map is set, but only its own
  --impedance option fills it, from KRT's own solver. hwde wants the widths
  rules_gen wrote into the .kicad_dru (zrules.layer_geometry), so the
  router, rules_gen and DRC agree. W.json = {"F.Cu": 0.0948, "In2.Cu":
  0.0889}. A run that already sets layer_widths (KRT's --impedance) keeps
  its own.

--via-joint-ends (#529): KRT's connectivity.find_stub_free_ends clusters a
  stub's endpoints per (x, y, layer), so a dog-bone escape (ball -> F.Cu
  neck -> via -> In2 stub to the field edge) reports the via joint itself
  as a "free end" on F.Cu and on In2, beside the real tip. route_diff picks
  its terminals among the free ends by P-N spacing, every candidate ties at
  the ball pitch, and the first one (the via, deep in the ball field) wins:
  the pair then has no room to launch (no-escape-path). With this flag a
  free end that coincides with an endpoint of the same group on ANOTHER
  layer (a via joint) is not a free end; a group whose only ends are such
  joints keeps KRT's answer.

The KRT script itself runs unchanged (runpy).
"""
from __future__ import annotations

import json
import os
import runpy
import sys

JOINT_TOL_MM = 0.01


def via_joint_free_ends(orig):
    """Wrap KRT's find_stub_free_ends(segments, pads, ...) so via joints
    are not reported as stub tips."""
    def find_stub_free_ends(segments, pads, *a, **kw):
        ends = orig(segments, pads, *a, **kw)

        def joint(x, y, layer):
            for s in segments:
                if s.layer == layer:
                    continue
                for sx, sy in ((s.start_x, s.start_y), (s.end_x, s.end_y)):
                    if abs(sx - x) <= JOINT_TOL_MM and abs(sy - y) <= JOINT_TOL_MM:
                        return True
            return False

        keep = [e for e in ends if not joint(e[0], e[1], e[2])]
        return keep or ends
    find_stub_free_ends.__wrapped__ = orig
    return find_stub_free_ends


def _patch_layer_widths(widths: dict) -> None:
    import routing_config

    cls = routing_config.GridRouteConfig
    orig = cls.__init__

    def init(self, *a, **kw):
        if not kw.get("layer_widths"):
            kw["layer_widths"] = dict(widths)
        orig(self, *a, **kw)

    cls.__init__ = init


def _patch_free_ends() -> None:
    import connectivity

    orig = connectivity.find_stub_free_ends
    patched = via_joint_free_ends(orig)
    # every module already holding KRT's function (a `from connectivity
    # import find_stub_free_ends` that ran while connectivity loaded); the
    # ones the script imports later read the patched attribute
    for mod in list(sys.modules.values()):
        if getattr(mod, "find_stub_free_ends", None) is orig:
            mod.find_stub_free_ends = patched


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    widths, joints = None, False
    while argv and argv[0].startswith("--"):
        flag = argv.pop(0)
        if flag == "--layer-widths":
            with open(argv.pop(0), encoding="utf-8") as fh:
                widths = {str(k): float(v) for k, v in json.load(fh).items()}
        elif flag == "--via-joint-ends":
            joints = True
        else:
            raise SystemExit(f"krt_launch: unknown flag {flag}")
    if not argv:
        raise SystemExit("krt_launch: no KRT script given")
    script = argv.pop(0)
    here = os.getcwd()                  # KRT runs with cwd = its plugins dir
    if here not in sys.path:
        sys.path.insert(0, here)
    if widths:
        _patch_layer_widths(widths)
    if joints:
        _patch_free_ends()
    sys.argv = [script, *argv]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
