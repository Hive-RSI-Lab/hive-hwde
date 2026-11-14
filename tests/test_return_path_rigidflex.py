"""check_return_path on rigid-flex boards (#870).

A hatched reference pour (the usual GND on a flex tail) used to read as a
void at every hatch cell, and a flex area, which has no outer copper by
design, used to be judged for F.Cu reference. Both made check_si unusable
on rigid-flex. These tests pin the fix and that real faults in such boards
are still found: a cut-out in a hatched plane, a gap in the outer pour
outside the flex area, and a keepout that isn't a flex area.
"""
from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import Polygon, box

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_return_path  # noqa: E402
import geom  # noqa: E402

W, H = 30.0, 10.0


def _board(tmp_path_factory, name: str, body: str) -> geom.BoardGeom:
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal)
    (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end {W} {H}) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path_factory.mktemp(name) / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return geom.load_board(p)


def _fp(layer: str, x0, y0, x1, y1) -> str:
    return (f'    (filled_polygon (layer "{layer}")\n'
            f'      (pts (xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1})'
            f' (xy {x0} {y1})))\n')


def solid(layer: str, x0=0.0, x1=W, net="GND") -> str:
    return (f'  (zone (net "{net}") (layer "{layer}")\n'
            f'    (polygon (pts (xy {x0} 0) (xy {x1} 0) (xy {x1} {H})'
            f' (xy {x0} {H})))\n' + _fp(layer, x0, 0, x1, H) + "  )\n")


def hatched(layer: str, cut: tuple | None = None, thick=0.25, gap=0.55):
    """A KiCad-style hatched GND pour: bars `thick` wide every
    thick+gap, unioned by geom into one polygon with a hole per cell.
    `cut` (x0, x1) removes the copper there across the whole height."""
    pitch = thick + gap
    bars = []
    x = 0.0
    while x < W:
        bars.append((x, 0, min(x + thick, W), H))
        x += pitch
    y = 0.0
    while y < H:
        bars.append((0, y, W, min(y + thick, H)))
        y += pitch
    bars += [(0, 0, W, thick), (0, H - thick, W, H),
             (0, 0, thick, H), (W - thick, 0, W, H)]
    out = []
    for b in bars:
        if cut:
            for x0, x1 in ((b[0], min(b[2], cut[0])), (max(b[0], cut[1]), b[2])):
                if x1 > x0:
                    out.append(_fp(layer, round(x0, 4), b[1], round(x1, 4), b[3]))
        else:
            out.append(_fp(layer, *[round(v, 4) for v in b]))
    return (f'  (zone (net "GND") (layer "{layer}") (name "GND_HATCH")\n'
            f'    (polygon (pts (xy 0 0) (xy {W} 0) (xy {W} {H}) (xy 0 {H})))\n'
            f'    (fill yes (mode hatch) (hatch_thickness {thick})'
            f' (hatch_gap {gap}))\n' + "".join(out) + "  )\n")


def keepout(name: str, x0, x1, layers=('"F.Cu"', '"B.Cu"')) -> str:
    return (f'  (zone (net "") (layers {" ".join(layers)}) (name "{name}")\n'
            "    (keepout (tracks allowed) (vias not_allowed)"
            " (pads not_allowed) (copperpour not_allowed)"
            " (footprints allowed))\n"
            f"    (polygon (pts (xy {x0} 0) (xy {x1} 0) (xy {x1} {H})"
            f" (xy {x0} {H}))))\n")


IN1_TRACK = ('  (segment (start 2 5) (end 28 5) (width 0.15) (layer "In1.Cu")'
             ' (net "SIG"))\n'
             '  (segment (start 2 5.6) (end 28 5.6) (width 0.15)'
             ' (layer "In1.Cu") (net "GND"))\n')
HS = {"net": "SIG", "reference": "GND"}


def _run(bg):
    vs, facts = check_return_path.check_net(bg, dict(HS), 3.0)
    return vs, facts


def _on(vs, layer):
    return [v for v in vs if v.get("layer") == layer]


# ------------------------------------------------------ hatched reference

def test_hatched_plane_reads_as_solid(tmp_path_factory):
    bg = _board(tmp_path_factory, "hatch",
                IN1_TRACK + solid("F.Cu") + hatched("In2.Cu"))
    fill = bg.zone_fill("GND", "In2.Cu")
    # precondition: geom sees the hatch as copper with a hole per cell
    assert max(len(p.interiors) for p in getattr(fill, "geoms", [fill])) > 100
    vs, facts = _run(bg)
    assert _on(vs, "In2.Cu") == []
    rec = [r for r in facts["reference_resolution"]
           if r["ref_layer"] == "In2.Cu"][0]
    assert rec.get("hatched") is True


def test_cut_in_hatched_plane_still_found(tmp_path_factory):
    bg = _board(tmp_path_factory, "hatchcut",
                IN1_TRACK + solid("F.Cu") + hatched("In2.Cu", cut=(12, 15)))
    vs, _ = _run(bg)
    voids = [v for v in _on(vs, "In2.Cu") if v["kind"] == "corridor_void"]
    assert len(voids) == 1
    assert voids[0]["severity"] == "error"
    assert Polygon(voids[0]["polygon"]).intersects(box(12.5, 4.8, 14.5, 5.2))
    # measured from the trace start (x=2) to the far edge of the cut
    assert abs(voids[0]["crossing_len_mm"] - 13.0) < 0.3


def test_solid_hatch_leaves_ordinary_holes():
    # a solid pour with a few antipads is not a hatch: holes stay holes
    holes = [box(x, 4, x + 0.6, 4.6).exterior.coords for x in range(2, 20, 4)]
    p = Polygon(box(0, 0, 30, 10).exterior.coords, holes)
    q, h = check_return_path._solid_hatch(p)
    assert h is False and len(q.interiors) == len(holes)


# ----------------------------------------------------------- flex areas

def test_flex_area_has_no_outer_reference(tmp_path_factory):
    # F.Cu pour on the rigid part only, pulled back 1 mm from the flex
    # line at x=12; the tail is referenced to In2 alone
    bg = _board(tmp_path_factory, "flex",
                IN1_TRACK + solid("F.Cu", 0, 11) + solid("In2.Cu")
                + keepout("FLEX_TAIL", 12, W))
    vs, facts = _run(bg)
    assert _on(vs, "F.Cu") == []
    rec = [r for r in facts["reference_resolution"]
           if r["ref_layer"] == "F.Cu"][0]
    assert rec["flex_excluded_mm2"] > 0


def test_outer_gap_on_rigid_part_still_found(tmp_path_factory):
    # same board, but the F.Cu pour also has a 3 mm gap on the rigid part
    bg = _board(tmp_path_factory, "flexgap",
                IN1_TRACK + solid("F.Cu", 0, 5) + solid("F.Cu", 8, 11)
                + solid("In2.Cu") + keepout("FLEX_TAIL", 12, W))
    vs, _ = _run(bg)
    voids = [v for v in _on(vs, "F.Cu") if v["kind"] == "corridor_void"]
    assert len(voids) == 1
    assert Polygon(voids[0]["polygon"]).intersects(box(5.5, 4.8, 7.5, 5.2))


def test_other_keepout_is_still_a_void(tmp_path_factory):
    # an antenna keepout removes the pour, not the layer: still a void
    bg = _board(tmp_path_factory, "antkeep",
                IN1_TRACK + solid("F.Cu", 0, 11) + solid("In2.Cu")
                + keepout("ANT_KEEPOUT", 12, W))
    vs, _ = _run(bg)
    assert [v for v in _on(vs, "F.Cu") if v["kind"] == "corridor_void"]
