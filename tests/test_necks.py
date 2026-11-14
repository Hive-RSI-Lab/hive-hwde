"""Neck zones bind every routing stage (#937).

rules_gen's aiee_pwr_neck lets a power net narrow inside the courtyard of a
fine-pitch part. route_finish read it first (#924); route_escape (KRT's
ball fanout, the --pads stubs) and route_auto (KRT's finish) read the same
zones from necklib. Each test below is a planted fault: the same board or
field without the neck rule boxes the 0.5 mm-pitch power pad in.
"""
from __future__ import annotations

import sys
import types

import pytest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
from shapely.geometry import Point  # noqa: E402

import bgalib  # noqa: E402
import geom  # noqa: E402
import necklib  # noqa: E402
import padfanout  # noqa: E402
import route_auto  # noqa: E402
import route_escape as re_  # noqa: E402
from test_route_finish import (  # noqa: E402
    NECK_DRU, _board, _fine_pitch, _pad, _seg)

NO_NECK = NECK_DRU.split('(rule "aiee_pwr_neck')[0]


def _sub(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def _necks(pcb):
    return necklib.read_necks(pcb)


def test_read_necks_cuts_the_courtyard_to_the_pads_reach(tmp_path):
    pcb = _board(tmp_path, _fine_pitch("U1", 10, 15), dru=NECK_DRU)
    necks = _necks(pcb)
    assert set(necks) == {"+3V3"} and set(necks["+3V3"]) == {"F.Cu"}
    ((zone, w),) = necks["+3V3"]["F.Cu"]
    assert w == 0.25
    x0, y0, x1, y1 = zone.bounds
    assert y0 > 13.8 and y1 < 16.2       # the pad's reach, not the courtyard
    assert necklib.neck_width(necks, "+3V3", "F.Cu", Point(10, 15.5)) \
        == 0.25
    assert necklib.neck_width(necks, "+3V3", "F.Cu", Point(10, 17)) \
        is None
    assert necklib.neck_width(necks, "+3V3", "B.Cu", Point(10, 15)) \
        is None
    assert _necks(_board(_sub(tmp_path, "n"), _fine_pitch("U1", 10, 15),
                         dru=NO_NECK)) == {}


# ------------------------------------------------------- route_escape (KRT)

def _field():
    """A 0.5 mm-pitch field whose one +3V3 ball the .kicad_dru holds at
    1 mm outside the part, 0.08 mm inside it."""
    return bgalib.Bga(ref="U1", layer="F.Cu", pitch=0.5, rows=3, cols=3,
                      pad_d=0.25, bbox=(0, 0, 1.0, 1.0), balls=9,
                      nets={"A1": "/S", "A2": "+3V3", "A3": "GND",
                            "B1": "GND", "B2": "GND", "B3": "GND",
                            "C1": "GND", "C2": "GND", "C3": "/T"})


def test_escape_fans_a_necked_power_ball_out(tmp_path):
    b = _field()
    wide = {"+3V3": 1.0}
    ok, held = re_.fanout_nets(b, wide, 0.0889, neck={"+3V3": 0.08})
    assert "+3V3" in ok and held == []
    # the planted fault: the net floor alone holds the ball back
    ok, held = re_.fanout_nets(b, wide, 0.0889)
    assert held == ["+3V3"]
    # a neck wider than KRT's one fan width still holds it
    assert re_.fanout_nets(b, wide, 0.0889, neck={"+3V3": 0.25})[1] \
        == ["+3V3"]


def test_field_necks_needs_every_ball_in_its_zone(tmp_path):
    body = _fine_pitch("U1", 10, 15)
    pcb = _board(tmp_path, body, dru=NECK_DRU)
    bg = geom.BoardGeom.from_file(pcb)
    u1 = types.SimpleNamespace(ref="U1")
    assert re_.field_necks(bg, u1, _necks(pcb)) == {"+3V3": 0.25}
    assert re_.field_necks(bg, u1, {}) == {}
    # a +3V3 ball of U1 past its zone (an outer pad) drops the net
    far = body.replace('(pad "1" smd rect (at -1.0 0) (size 0.25 0.8)'
                       ' (layers "F.Cu") (net "GND"))',
                       '(pad "1" smd rect (at -1.0 0) (size 0.25 0.8)'
                       ' (layers "F.Cu") (net "+3V3"))')
    assert far != body
    bg2 = geom.BoardGeom.from_file(_board(_sub(tmp_path, "f"), far,
                                          dru=NECK_DRU))
    nk = {"+3V3": {"F.Cu": [(Point(10, 15).buffer(0.6), 0.25)]}}
    assert re_.field_necks(bg2, u1, nk) == {}
    assert re_.field_necks(bg2, u1, nk) == {}


def _neck_board(tmp_path, dru):
    body = _fine_pitch("U1", 10, 15) + _pad("U2", 10, 5, "+3V3", size=1.5)
    return _board(tmp_path, body, dru=dru)


def test_pad_fanout_lays_the_stub_at_the_neck_width(tmp_path):
    bg = geom.BoardGeom.from_file(_neck_board(tmp_path, NECK_DRU))
    ops, rep = padfanout.Fanout(bg).fan(["U1.3"])
    assert rep[0]["result"] == "via", rep
    (trk,) = [o for o in ops if o["op"] == "add_track"]
    assert trk["width"] == 0.25
    # the planted fault: at the 1 mm floor the pad is walled in
    bg = geom.BoardGeom.from_file(_neck_board(_sub(tmp_path, "n"), NO_NECK))
    ops, rep = padfanout.Fanout(bg).fan(["U1.3"])
    assert rep[0]["result"] == "no spot" and ops == []


# ------------------------------------------------------- route_auto (KRT)

def test_krt_widths_route_a_necked_net_at_its_neck(tmp_path):
    pcb = _neck_board(tmp_path, NECK_DRU)
    wide = {"+3V3": 1.0, "SIG": 0.3}
    assert route_auto.krt_widths(wide, _necks(pcb)) == {"+3V3": 0.25,
                                                        "SIG": 0.3}
    assert route_auto.krt_widths(wide, {}) == wide


def test_narrow_tracks_keep_a_neck_inside_its_zone(tmp_path):
    body = (_fine_pitch("U1", 10, 15) + _pad("U2", 10, 5, "+3V3", size=1.5)
            + _seg(10, 15, 10, 15.6, 0.25, "F.Cu", "+3V3", "in")
            + _seg(10, 15, 10, 15.6, 0.2, "F.Cu", "+3V3", "thin")
            + _seg(10, 17, 10, 19, 0.25, "F.Cu", "+3V3", "out")
            + _seg(12, 17, 12, 19, 1.0, "F.Cu", "+3V3", "wide"))
    pcb = _board(tmp_path, body, dru=NECK_DRU)
    necks = _necks(pcb)
    widths = route_auto.krt_widths({"+3V3": 1.0}, necks)
    floors = {"+3V3": 1.0}
    assert sorted(route_auto._narrow_new_tracks(pcb, floors, set(), necks)) \
        == ["out", "thin"]
    # the planted fault: without the zones the neck is stripped too
    assert sorted(route_auto._narrow_new_tracks(pcb, floors, set())) \
        == ["in", "out", "thin"]
    assert widths["+3V3"] == 0.25


# ------------------------------------- route_auto (Freerouting's neckdown, #998)

def test_snap_neckdowns_widens_a_hair_thin_stub_in_the_zone(tmp_path):
    body = (_fine_pitch("U1", 10, 15) + _pad("U2", 10, 5, "+3V3", size=1.5)
            + _seg(10, 15, 10, 14.0, 0.2498, "F.Cu", "+3V3", "f1")
            + _seg(10, 14.0, 10, 5, 1.0, "F.Cu", "+3V3", "a2")
            + _seg(10, 15, 10, 15.6, 0.2, "F.Cu", "+3V3", "b3")
            + _seg(10, 17, 10, 19, 0.2498, "F.Cu", "+3V3", "c4")
            + _seg(10, 15, 10, 14.0, 0.2498, "F.Cu", "+3V3", "d5"))
    pcb = _board(tmp_path, body, dru=NECK_DRU)
    # only the new near-neck stub inside the zone: not a real under-width
    # track, not one outside the zone, not one on the board before
    assert route_auto._snap_neckdowns(pcb, {"d5"}) == ["f1"]
    widths = {t.uuid: t.width
              for t in route_auto.geom.BoardGeom.from_file(pcb).tracks_of()}
    assert widths == {"f1": 0.25, "a2": 1.0, "b3": 0.2, "c4": 0.2498,
                      "d5": 0.2498}
    # the planted fault: without the neck rule nothing is widened
    plain = _board(_sub(tmp_path, "n"), body, dru=NO_NECK)
    assert route_auto._snap_neckdowns(plain, set()) == []


@pytest.mark.parametrize("snap", [True, False], ids=["snap", "without"])
def test_live_freerouting_neckdown_passes_the_neck_rule(tmp_path, snap,
                                                        monkeypatch):
    """Freerouting gets +3V3 at its 1 mm floor and necks it down at the
    0.5 mm-pitch pin by itself, 0.2 um under the pad: the neck rule fails it
    until the stub is snapped to the neck width (#998)."""
    import env
    import kc
    cli = env.find_kicad_cli()
    if cli is None or env.find_freerouting_jar() is None \
            or env.find_java() is None:
        pytest.skip("needs kicad-cli, java and Freerouting")
    if not snap:   # the planted fault: the neckdown stays as Freerouting laid it
        monkeypatch.setattr(route_auto, "_snap_neckdowns", lambda s, b: [])
    pcb = _neck_board(tmp_path, NECK_DRU)
    try:
        payload, _ = route_auto.run([
            "--pcb", str(pcb), "--work-dir", str(tmp_path / "work"),
            "--max-rungs", "1", "--timeout-s", "300",
            "--no-krt-finish", "--no-maze-finish"])
    except Exception as e:  # noqa: BLE001
        if any(k in str(e) for k in ("wxEntryStart", "X Display")):
            pytest.skip("route_swig couldn't start wx on this machine's Xvfb")
        raise
    assert payload["facts"]["unrouted_nets"] == [], payload["facts"]
    drc = kc.run_drc(cli, pcb, all_track_errors=True)
    thin = [v for v in drc["violations"] if v.get("check") == "track_width"]
    if snap:
        assert payload["facts"]["neckdowns_snapped"] >= 1
        assert thin == [], thin
    else:
        assert thin, "Freerouting's neckdown no longer runs under the pad"
