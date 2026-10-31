"""route_tune (hive-hwde #324): trombone meanders bring a length group's short
members to the middle of their allowed range, measured the way
check_length_groups measures."""
from __future__ import annotations

import json
import re
import shutil
import sys
import uuid as uuidlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_length_groups as clg  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import route_tune as rt  # noqa: E402

GOLDEN = REPO / "tests" / "golden" / "blinky2"


def _uid():
    return str(uuidlib.uuid4())


def seg(x1, y1, x2, y2, net, layer="F.Cu", w=0.15) -> str:
    return (f'  (segment (start {x1} {y1}) (end {x2} {y2}) (width {w}) '
            f'(layer "{layer}") (net "{net}") (uuid "{_uid()}"))\n')


def fp(ref, x, y, pads) -> str:
    out = [f'  (footprint "t:{ref}" (at {x} {y}) (layer "F.Cu")\n'
           f'    (property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS"))\n']
    for num, dx, dy, net in pads:
        out.append(f'    (pad "{num}" smd rect (at {dx} {dy}) (size 0.4 0.4) '
                   f'(layers "F.Cu") (net "{net}"))\n')
    return "".join(out) + "  )\n"


def board(tmp_path, nets_extra: dict, other="", pitch=6) -> Path:
    """Nets /D0../D2 and /DQS from U1 (x=5) to U2 (x=45), one row each, `pitch`
    mm apart; nets_extra adds a detour (mm) to a net."""
    nets = ["/D0", "/D1", "/D2", "/DQS"]
    pads = [(str(i + 1), 0, pitch * i, n) for i, n in enumerate(nets)]
    body = fp("U1", 5, 5, pads) + fp("U2", 45, 5, pads)
    for i, n in enumerate(nets):
        y = 5 + pitch * i
        e = nets_extra.get(n, 0.0)
        if e:
            h = e / 2
            body += (seg(5, y, 25, y, n) + seg(25, y, 25, y + h, n)
                     + seg(25, y + h, 26, y + h, n) + seg(26, y + h, 26, y, n)
                     + seg(26, y, 45, y, n))
        else:
            body += seg(5, y, 45, y, n)
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body}{other})
"""
    p = tmp_path / "b.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def cons_file(tmp_path, groups, extra=None) -> Path:
    c = tmp_path / "constraints.json"
    c.write_text(json.dumps({"length_groups": groups, **(extra or {})}),
                 encoding="utf-8")
    return c


def apply_text(pcb: Path, ops: list[dict]) -> None:
    """The ops route_edit would apply, done on the text (no KiCad): remove by
    uuid, add_path as segments."""
    text = pcb.read_text(encoding="utf-8")
    for op in ops:
        if op["op"] == "remove":
            text, n = re.subn(r'  \(segment [^\n]*\(uuid "' + op["uuid"] + r'"\)\)\n',
                              "", text)
            assert n == 1
    adds = ""
    for op in ops:
        if op["op"] == "add_path":
            pts = op["points"]
            for a, b in zip(pts, pts[1:]):
                adds += seg(a[0], a[1], b[0], b[1], op["net"], op["layer"],
                            op["width"])
    text = text.rstrip()[:-1] + adds + ")\n"
    pcb.write_text(text, encoding="utf-8")


def plan(pcb, cons, **kw):
    payload, _ = rt.run(["--pcb", str(pcb), "--constraints", str(cons),
                         "--dry-run", "--ops-out", str(pcb.with_suffix(".ops.json"))]
                        + [x for k, v in kw.items()
                           for x in ((f"--{k}",) if v == "" else (f"--{k}", str(v)))])
    ops = json.loads(pcb.with_suffix(".ops.json").read_text())["ops"]
    return payload, ops


def check(pcb, cons):
    return clg.run(["--pcb", str(pcb), "--constraints", str(cons)])[0]


# ------------------------------------------------------------ geometry

def test_meander_points_add_exactly_2hk():
    pts = rt.meander_points((0, 0), (20, 0), 3, 0.7, 0.45, 0.5, 1)
    from shapely.geometry import LineString
    assert LineString(pts).length == pytest.approx(20 + 2 * 0.7 * 3)
    assert pts[0] == (0, 0) and pts[-1] == (20, 0)
    assert all(abs(y) < 1e-9 or abs(y - 0.7) < 1e-9 for _, y in pts)
    assert rt.meander_points((0, 0), (2, 0), 5, 0.7, 0.45, 0.5, 1) is None
    assert rt.max_bumps(20, 0.45, 0.5) == 21


# ------------------------------------------------------------ tuning

def test_short_members_tuned_to_the_reference(tmp_path):
    pcb = board(tmp_path, {"/DQS": 3.0, "/D1": 2.5})
    cons = cons_file(tmp_path, [{"name": "BYTE0", "nets": ["/D*"],
                                 "ref_net": "/DQS", "tol_mm": 0.635}])
    assert check(pcb, cons)["counts"]["total"] == 2         # D0, D2 short
    payload, ops = plan(pcb, cons)
    tuned = {t["net"]: t for t in payload["tuned"]}
    assert set(tuned) == {"/D0", "/D2"}                      # D1 within tol
    assert all(t["added_mm"] == pytest.approx(3.0, abs=0.011) for t in tuned.values())
    apply_text(pcb, ops)
    after = check(pcb, cons)
    assert after["status"] == "pass", after["violations"]
    lens = {r["net"]: r["length_mm"] for r in after["groups"][0]["nets"]}
    assert lens["/D0"] == pytest.approx(43.0, abs=0.011)


def test_window_below_reference_aims_mid_window(tmp_path):
    pcb = board(tmp_path, {"/DQS": 6.0})
    cons = cons_file(tmp_path, [{"name": "AC", "nets": ["/D0"], "ref_net": "/DQS",
                                 "window_mm": [-4, -2]}])
    payload, ops = plan(pcb, cons)
    assert payload["tuned"][0]["aim_mm"] == pytest.approx(43.0)
    apply_text(pcb, ops)
    assert check(pcb, cons)["status"] == "pass"


def test_obstacle_moves_the_meander_to_the_free_side(tmp_path):
    # a foreign track 0.6 mm above /D0 (y=5): bumps must go below (+y)
    other = seg(5, 4.4, 45, 4.4, "/X")
    pcb = board(tmp_path, {"/DQS": 2.0}, other=other)
    cons = cons_file(tmp_path, [{"name": "B", "nets": ["/D0"], "ref_net": "/DQS",
                                 "tol_mm": 0.3}])
    _, ops = plan(pcb, cons)
    path = next(o for o in ops if o["op"] == "add_path")
    assert all(p[1] >= 5 - 1e-9 for p in path["points"])


def test_no_room_and_too_long_are_reported(tmp_path):
    # /D0 boxed in by foreign tracks 0.45 mm either side: no room
    other = seg(5, 4.55, 45, 4.55, "/X") + seg(5, 5.45, 45, 5.45, "/Y")
    pcb = board(tmp_path, {"/DQS": 2.0, "/D1": 6.0}, other=other)
    cons = cons_file(tmp_path, [{"name": "B", "nets": ["/D0", "/D1"],
                                 "ref_net": "/DQS", "tol_mm": 0.3}])
    payload, ops = plan(pcb, cons)
    kinds = sorted(v["kind"] for v in payload["violations"])
    assert kinds == ["tune_no_room", "tune_too_long"]
    assert ops == []


def _pour(net, x0, y0, x1, y1, layer="F.Cu"):
    pts = f"(xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1}) (xy {x0} {y1})"
    return (f'  (zone (net "{net}") (layer "{layer}")\n'
            f'    (polygon (pts {pts}))\n'
            f'    (filled_polygon (layer "{layer}") (pts {pts})))\n')


def test_other_nets_pours_are_obstacles(tmp_path):
    # GND poured 0.45 mm either side of /D0 (y=5): a bump would cut into
    # it, so no room; --through-pours lets the plan go in (DRC guards it)
    other = _pour("GND", 5, 0.5, 45, 4.55) + _pour("GND", 5, 5.45, 45, 9.5)
    pcb = board(tmp_path, {"/DQS": 2.0}, other=other, pitch=12)
    cons = cons_file(tmp_path, [{"name": "B", "nets": ["/D0"], "ref_net": "/DQS",
                                 "tol_mm": 0.3}])
    payload, ops = plan(pcb, cons)
    assert ops == [] and [v["kind"] for v in payload["violations"]] == ["tune_no_room"]
    payload, ops = plan(pcb, cons, **{"through-pours": ""})
    assert ops and payload["tuned"][0]["added_mm"] == pytest.approx(2.0, abs=0.011)


def test_pair_members_are_not_tuned(tmp_path):
    pcb = board(tmp_path, {"/D1": 2.0})
    # /D0 and /D2 stand in for a declared pair
    cons = cons_file(tmp_path, [{"name": "B", "nets": ["/D0", "/D1", "/D2"],
                                 "tol_mm": 0.3}],
                     {"diff_pairs": [{"p": "/D0", "n": "/D2"}]})
    payload, ops = plan(pcb, cons)
    assert sorted(v["kind"] for v in payload["violations"]) == [
        "tune_pair_member", "tune_pair_member"]
    assert ops == []


def test_reference_groups_go_first():
    nets = ["/D0", "/DQS_P", "/DQS_N", "/CK"]
    groups = [{"nets": ["/D0"], "ref_nets": ["/DQS_*"], "tol_mm": 1},
              {"nets": ["/DQS_P", "/DQS_N"], "tol_mm": 1, "not_longer_than": ["/CK"]},
              {"nets": ["/CK"], "max_mm": 50}]
    assert rt.group_order(groups, nets) == [2, 1, 0]


# ------------------------------------------------------------ live (KiCad)

def test_tune_blinky2_live(tmp_path):
    """The real writer (route_edit's SWIG worker) and DRC on a real board:
    SWCLK lengthened into a window below SWDIO, DRC gains nothing."""
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("KiCad bundled python not installed")
    for f in GOLDEN.iterdir():
        if f.suffix in (".kicad_pcb", ".kicad_pro", ".kicad_sch"):
            shutil.copy2(f, tmp_path / f.name)
    pcb = tmp_path / "blinky2.kicad_pcb"
    cons = cons_file(tmp_path, [{"name": "SWD", "nets": ["/SWCLK"],
                                 "ref_net": "/SWDIO", "window_mm": [-9, -7]}])
    assert check(pcb, cons)["counts"]["total"] == 1
    payload, _ = rt.run(["--pcb", str(pcb), "--constraints", str(cons)])
    assert payload["status"] == "pass", payload["violations"]
    assert payload["drc"]["new"] == {}
    bg = geom.load_board(pcb)
    L = clg.net_length(bg, "/SWCLK")["length_mm"]
    assert L == pytest.approx(25.448 - 8, abs=0.02)
