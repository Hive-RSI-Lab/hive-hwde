"""krt-finish-width: does route_auto's KRT finish pass route a power net at
its width?

When Freerouting leaves nets open, route_auto hands them to KRT's route.py
with the board floors and no track width (KRT has --track-width and
--power-nets-widths), so a power net comes back at KRT's default width,
under its track_width rule. core (hifi-receiver-2) deletes those tracks
after routing (kicad/gen/post_route.py) and lets its pours carry the
current. The probe runs _krt_finish up to its KRT call, with an open power
net, and reads what it would pass KRT. Fixed when that names a width.
"""
from pathlib import Path

import _probe

import route_auto
import route_critical as rc


class Called(BaseException):
    pass


seen = {}


def run_krt(krt, script, staged, out, extra, floors, fab, grid, timeout, **kw):
    seen.update(script=script, extra=list(extra), floors=dict(floors), kw=kw)
    raise Called


work = _probe.scratch()
staged = work / "b.kicad_pcb"
# _krt_finish parses the board (its tracks, to tell KRT's new ones apart)
# before it calls KRT, so the board needs its copper layers
staged.write_text('(kicad_pcb\n  (layers (0 "F.Cu" signal) (31 "B.Cu" signal))\n)\n')
route_auto.env.find_krt = lambda: work
rc.grading_floors = lambda pro: {"clearance": 0.127, "via_diameter": 0.6,
                                 "via_drill": 0.3, "edge_clearance": 0.3}
rc.write_fab_overrides = lambda w, f: Path(w) / "fab.json"
rc.run_krt = run_krt
drc = {"violations": [{"source": "unconnected", "net": "PVDD"}]}
try:
    route_auto._krt_finish(Path("kicad-cli"), staged, work, drc,
                           refill=False, parity=False, timeout_s=10)
except Called:
    pass
if not seen:
    raise SystemExit("_krt_finish never called KRT for an open PVDD")
args = seen["extra"] + [str(k) for k in seen["kw"]]
if any("width" in a for a in args):
    _probe.fixed(f"the finish pass passes KRT {' '.join(seen['extra'])}")
_probe.stands(f"the finish pass passes KRT {' '.join(seen['extra'])} and no width")
