"""layer_views.py: where the net names go on a copper plot.

Only the label placement is tested here; the plot itself needs kicad-cli and
KiCad's python, and report_gen's use of the output is in test_report_kinds.
"""
from __future__ import annotations

import pytest

import test_report  # noqa: F401  (puts the scripts on sys.path)
import layer_views as lv  # noqa: E402


def test_each_copper_layer_gets_its_own_sides_context():
    assert lv.context_layers("F.Cu") == ["F.Cu", "F.SilkS", "F.Fab", "F.CrtYd", "Edge.Cuts"]
    assert lv.context_layers("B.Cu") == ["B.Cu", "B.SilkS", "B.Fab", "B.CrtYd", "Edge.Cuts"]
    assert lv.context_layers("In1.Cu") == ["In1.Cu", "F.Fab", "Edge.Cuts"]


@pytest.mark.parametrize("deg,want", [(0, 0), (90, 90), (-90, 90), (135, -45),
                                      (180, 0), (270, 90), (-135, 45)])
def test_text_never_reads_upside_down(deg, want):
    assert lv.upright(deg) == pytest.approx(want)


def test_hierarchical_net_shows_its_last_part():
    assert lv.short_net("/amp/OUTL") == "OUTL"
    assert lv.short_net("GND") == "GND"


def pad(net, w, h, angle=0.0, layers=("F.Cu",)):
    return {"layers": list(layers), "net": net, "x": 1.0, "y": 2.0,
            "w": w, "h": h, "angle": angle}


def test_pad_label_runs_along_the_long_side_and_fits():
    (x, y, text, em, angle), = lv.pad_labels([pad("SPK_L_P", 1.0, 3.0)], "F.Cu")
    assert (x, y, text, angle) == (1.0, 2.0, "SPK_L_P", 90)
    assert em <= 0.5 * 1.0 and lv.ADVANCE * em * len(text) <= 0.9 * 3.0 + 1e-9


def test_pad_label_only_on_the_pads_layers_and_not_when_too_small():
    pads = [pad("A", 1, 1, layers=["B.Cu"]), pad("LONG_NET_NAME", 0.1, 0.1)]
    assert lv.pad_labels(pads, "F.Cu") == []
    assert [t for _, _, t, _, _ in lv.pad_labels(pads, "B.Cu")] == ["A"]


def track(net, x0, y0, x1, y1, width=0.5, layer="F.Cu"):
    return {"layer": layer, "net": net, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "width": width}


def test_track_labels_keep_their_spacing_per_net():
    tracks = [track("OUT", 0, 0, 5, 0), track("OUT", 0, 1, 5, 1),   # 1 mm apart
              track("OUT", 20, 0, 25, 0),                           # far away
              track("IN", 0, 2, 5, 2),                              # other net
              track("OUT", 0, 9, 0.5, 9)]                           # too short
    got = lv.track_labels(tracks, "F.Cu")
    assert sorted((round(x), round(y), t) for x, y, t, _, _ in got) == [
        (2, 0, "OUT"), (2, 2, "IN"), (22, 0, "OUT")]


def test_track_label_angle_is_as_the_reader_sees_it():
    # board y points down: a segment going up and to the right reads at +45
    (_, _, _, _, angle), = lv.track_labels([track("N", 0, 5, 5, 0)], "F.Cu")
    assert angle == pytest.approx(45)
