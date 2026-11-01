"""#325: BGA escape (route_escape + lib/bgalib).

Pure tests (grid detection, the escape budget, the DRU rule and its
placement, floors, plane layers, via-in-pad detection, KRT arguments, the
--plan CLI, rules_gen's copy of the rule) need no toolchain. The slow test
fans a synthetic 6 x 6 0.8 mm BGA out live with KRT and grades it with
kicad-cli: every ball escaped, no new DRC error.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import bgalib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import route_escape as re_  # noqa: E402
import rules_gen  # noqa: E402

# JLC 6-layer 1 oz row (reference/jlc_capabilities.yaml)
JLC6 = {"track_width": 0.0889, "clearance": 0.0889, "via_diameter": 0.45,
        "via_drill": 0.2, "annular": 0.15, "hole_clearance": 0.25}
PRO6 = {"board": {"design_settings": {"rules": {
    "min_clearance": 0.0889, "min_track_width": 0.0889,
    "min_via_diameter": 0.45, "min_through_hole_diameter": 0.2,
    "min_via_annular_width": 0.15, "min_hole_to_hole": 0.5,
    "min_copper_edge_clearance": 0.3}}},
    "net_settings": {"classes": [{"name": "Default", "clearance": 0.125,
                                  "track_width": 0.2, "via_diameter": 0.5,
                                  "via_drill": 0.2}]},
    "meta": {"filename": "b.kicad_pro", "version": 3}}


# ---- synthetic boards ----------------------------------------------------

def _pad(num, x, y, net, size, shape="circle", w=None):
    w = w or size
    return (f'    (pad "{num}" smd {shape}\n      (at {x:.4f} {y:.4f})\n'
            f'      (size {w} {size})\n'
            f'      (layers "F.Cu" "F.Mask" "F.Paste")\n'
            f'      (net "{net}")\n      (uuid "u-{num}-{net}")\n    )\n')


def _fp(ref, x, y, pads, half, rot=0):
    hx, hy = half
    return (f'  (footprint "t:{ref}" (layer "F.Cu") (at {x} {y} {rot})\n'
            f'    (property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS"))\n'
            f'    (fp_rect (start {-hx} {-hy}) (end {hx} {hy}) '
            f'(stroke (width 0.05)) (fill no) (layer "F.CrtYd"))\n'
            f'{pads}  )\n')


def _grid(n, pitch, pad, prefix="S", skip=()):
    out = []
    for r in range(n):
        for c in range(n):
            if (r, c) in skip:
                continue
            x, y = (c - (n - 1) / 2) * pitch, (r - (n - 1) / 2) * pitch
            out.append((f"{chr(65 + r)}{c + 1}", x, y, f"/{prefix}{r}_{c}"))
    return out


def board(tmp_path, *, n=6, pitch=0.8, pad=0.35, rot=0, extra="",
          name="b", zone_layer=None, pro=True, connector=True) -> Path:
    balls = _grid(n, pitch, pad)
    u1 = "".join(_pad(num, x, y, net, pad) for num, x, y, net in balls)
    half = (n - 1) / 2 * pitch + 0.8
    body = _fp("U1", 20.0, 15.0, u1, (half, half), rot)
    if connector:
        jp = "".join(_pad(str(k + 1), (k % 18) * 1.0, (k // 18) * 2.0, net,
                          1.2, "rect", 0.6)
                     for k, (_, _, _, net) in enumerate(balls))
        body += _fp("J1", 11.5, 24.0, jp, (18, 2.5))
    if zone_layer:
        body += (f'  (zone (net "GND") (layer "{zone_layer}") (uuid "z1")\n'
                 '    (polygon (pts (xy 1 1) (xy 39 1) (xy 39 39) (xy 1 39)))\n'
                 '  )\n')
    body += extra
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal)
    (2 "B.Cu" signal) (13 "F.Paste" user) (1 "F.Mask" user)
    (5 "F.SilkS" user "F.Silkscreen") (31 "F.CrtYd" user "F.Courtyard")
    (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 40 40) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    if pro:
        p.with_suffix(".kicad_pro").write_text(json.dumps(PRO6),
                                                encoding="utf-8")
    return p


def _qfn(n_side=8, pitch=0.5) -> str:
    """A perimeter-only pad ring (QFN-like): not a ball grid."""
    pads = []
    k = 0
    span = (n_side - 1) / 2 * pitch
    for i in range(n_side):
        o = (i - (n_side - 1) / 2) * pitch
        for x, y in ((o, -span - 0.5), (o, span + 0.5),
                     (-span - 0.5, o), (span + 0.5, o)):
            k += 1
            pads.append(_pad(str(k), x, y, f"/Q{k}", 0.25))
    return _fp("U9", 32.0, 32.0, "".join(pads), (3, 3))


# ============================================================ detection

def test_finds_ball_grid_not_qfn(tmp_path):
    bg = geom.BoardGeom.from_file(board(tmp_path, extra=_qfn()))
    found = bgalib.find_bgas(bg)
    assert [b.ref for b in found] == ["U1"]
    b = found[0]
    assert (b.rows, b.cols, b.balls) == (6, 6, 36)
    assert b.pitch == pytest.approx(0.8)
    assert b.pad_d == pytest.approx(0.35)
    assert b.layer == "F.Cu"


def test_finds_rotated_grid(tmp_path):
    bg = geom.BoardGeom.from_file(board(tmp_path, rot=30))
    (b,) = bgalib.find_bgas(bg)
    assert b.pitch == pytest.approx(0.8, abs=1e-3)
    assert (b.rows, b.cols) == (6, 6)


def test_coarse_grid_is_not_a_bga(tmp_path):
    bg = geom.BoardGeom.from_file(board(tmp_path, pitch=2.54, pad=1.0))
    assert bgalib.find_bgas(bg) == []


def test_refs_filter(tmp_path):
    bg = geom.BoardGeom.from_file(board(tmp_path))
    assert bgalib.find_bgas(bg, ["U7"]) == []
    assert [b.ref for b in bgalib.find_bgas(bg, ["U1"])] == ["U1"]


def _qfn_ep(n_side=12, pitch=0.5, ep=5.0) -> str:
    """A real QFN: oblong leads on all four sides (the lattice comes out at
    0.25 mm) and a big exposed pad in the middle, which used to pass as an
    interior ball (hive-hwde issue: route_escape planned escapes for QFNs)."""
    pads = []
    k = 0
    span = 3.0
    for i in range(n_side):
        o = (i - (n_side - 1) / 2) * pitch
        for x, y, w, h in ((o, -span, 0.25, 0.8), (o, span, 0.25, 0.8),
                           (-span, o, 0.8, 0.25), (span, o, 0.8, 0.25)):
            k += 1
            pads.append(_pad(str(k), x, y, f"/Q{k}", h, shape="roundrect",
                             w=w))
    pads.append(_pad(str(k + 1), 0.0, 0.0, "/GND", ep, shape="rect"))
    return _fp("U8", 32.0, 8.0, "".join(pads), (3.6, 3.6))


def test_qfn_with_exposed_pad_is_not_a_bga(tmp_path):
    bg = geom.BoardGeom.from_file(board(tmp_path, extra=_qfn_ep()))
    assert [b.ref for b in bgalib.find_bgas(bg)] == ["U1"]


def test_depopulated_bga_is_still_found(tmp_path):
    """A ball field with its centre depopulated (a ring two balls deep plus
    a centre island) is still a BGA."""
    skip = ({(r, c) for r in range(2, 6) for c in range(2, 6)}
            - {(3, 3), (3, 4), (4, 3), (4, 4)})
    pads = "".join(_pad(num, x, y, net, 0.35) for num, x, y, net
                   in _grid(8, 0.8, 0.35, prefix="D", skip=skip))
    bg = geom.BoardGeom.from_file(board(tmp_path, extra=_fp(
        "U5", 30.0, 30.0, pads, (3.5, 3.5))))
    assert "U5" in [b.ref for b in bgalib.find_bgas(bg)]


# ============================================================ budget

def _bga(pitch, pad):
    return bgalib.Bga("U1", "F.Cu", pitch, 10, 10, pad, (0, 0, 1, 1), 100)


def test_budget_08mm_on_jlc_6layer_is_dogbone():
    """i.MX6ULL MAPBGA-289: 0.8 mm, 0.32 mm lands. The 6-layer floor via
    (0.45/0.2) breaks the row's own 0.15 annular ring, so 0.5; clearance =
    hole clearance 0.25 less the 0.15 ring = 0.1."""
    ep = bgalib.escape_params(_bga(0.8, 0.32), JLC6)
    assert ep["via_size"] == pytest.approx(0.5)
    assert ep["via_drill"] == pytest.approx(0.2)
    assert ep["clearance"] == pytest.approx(0.1)
    assert ep["track_width"] == pytest.approx(0.0889)
    assert ep["method"] == "dogbone" and ep["dogbone_ok"]
    b = ep["budget"]
    assert b["street"]["have"] == pytest.approx(0.3)
    assert b["street"]["need"] == pytest.approx(0.2889)
    assert b["gap"]["have"] == pytest.approx(0.8 / math.sqrt(2), abs=1e-4)
    assert b["gap"]["need"] == pytest.approx(0.16 + 0.25 + 0.1)


def test_budget_05mm_emmc_cannot_dogbone():
    ep = bgalib.escape_params(_bga(0.5, 0.25), JLC6)
    assert not ep["dogbone_ok"]
    assert ep["method"] == "channel"
    assert not ep["budget"]["gap"]["ok"]
    assert "via-in-pad" in ep["why"]


def test_budget_wide_hole_clearance_raises_clearance():
    ep = bgalib.escape_params(_bga(1.0, 0.45), {**JLC6, "hole_clearance": 0.3})
    assert ep["clearance"] == pytest.approx(0.15)


# ============================================================ DRU rule

def test_dru_rule_matches_rules_gen_render():
    r = rules_gen.Rule(bgalib.rule_name("U100"), "clearance", 0.1,
                       condition=bgalib.rule_condition("U100"))
    assert bgalib.dru_rule("U100", 0.1) == r.render() + "\n"


def test_ensure_rules_idempotent_replace_and_header():
    t, ch = bgalib.ensure_rules("", [("U1", 0.1)])
    assert t.startswith("(version 1)\n") and ch == ["aiee_bga_escape_U1"]
    t2, ch2 = bgalib.ensure_rules(t, [("U1", 0.1)])
    assert t2 == t and ch2 == []
    t3, ch3 = bgalib.ensure_rules(t, [("U1", 0.12)])
    assert ch3 == ["aiee_bga_escape_U1"]
    assert t3.count('(rule "aiee_bga_escape_U1"') == 1
    assert "0.1200mm" in t3 and "0.1000mm" not in t3


def test_ensure_rules_stays_before_hv_rules():
    hv = ('(version 1)\n(rule "aiee_clearance_floor"\n\t(constraint '
          'clearance (min 0.0889mm))\n)\n(rule "aiee_hv_48v_VIN"\n\t'
          '(constraint clearance (min 0.6000mm))\n\t(condition '
          '"A.NetName == \'VIN\'")\n)\n')
    t, _ = bgalib.ensure_rules(hv, [("U1", 0.1)])
    assert t.index("aiee_bga_escape_U1") < t.index("aiee_hv_48v_VIN")
    pkg = hv.replace("aiee_hv_48v_VIN", "aiee_pkg_rating_U3")
    t, _ = bgalib.ensure_rules(pkg, [("U1", 0.1)])
    assert t.index("aiee_bga_escape_U1") < t.index("aiee_pkg_rating_U3")
    assert t.index("aiee_clearance_floor") < t.index("aiee_bga_escape_U1")


def test_rules_gen_writes_the_escape_rule(tmp_path):
    bg = geom.BoardGeom.from_file(board(tmp_path))
    cap = {"min_trace_width_mm": 0.0889, "min_clearance_mm": 0.0889,
           "min_via_diameter_mm": 0.45, "min_via_drill_mm": 0.2,
           "min_annular_ring_mm": 0.15}
    rules, facts = rules_gen.bga_rules(bg, cap)
    assert [r.name for r in rules] == ["aiee_bga_escape_U1"]
    assert facts[0]["clearance_mm"] == pytest.approx(0.1)
    assert facts[0]["method"] == "dogbone"
    # route_escape's writer sees rules_gen's rule as its own
    text = "(version 1)\n" + "\n".join(r.render() for r in rules) + "\n"
    assert bgalib.ensure_rules(text, [("U1", 0.1)])[1] == []


# ============================================================ helpers

def test_escape_floors_from_pro(tmp_path):
    p = board(tmp_path).with_suffix(".kicad_pro")
    fl = re_.escape_floors(p)
    assert fl["clearance"] == pytest.approx(0.0889)   # fab floor, not 0.125
    assert fl["annular"] == pytest.approx(0.15)
    assert fl["hole_to_hole"] == pytest.approx(0.5)
    assert fl["hole_clearance"] == pytest.approx(0.25)  # KiCad default
    assert re_.escape_floors(None)["clearance"] == pytest.approx(0.2)


def test_escape_layers_skip_inner_plane(tmp_path):
    bg = geom.BoardGeom.from_file(board(tmp_path, zone_layer="In1.Cu"))
    (b,) = bgalib.find_bgas(bg)
    layers, planes = re_.escape_layers(bg, b)
    assert planes == ["In1.Cu"]
    assert layers == ["F.Cu", "In2.Cu", "B.Cu"]
    # an outer pour is a fill, not a plane
    bg2 = geom.BoardGeom.from_file(board(tmp_path, zone_layer="B.Cu",
                                         name="c"))
    assert re_.escape_layers(bg2, bgalib.find_bgas(bg2)[0])[1] == []
    assert re_.escape_layers(bg, b, ["F.Cu", "X.Cu"])[0] == ["F.Cu"]


def test_via_in_pad_nets_counts_only_new_vias_in_own_pads(tmp_path):
    vias = ('  (via (at 18 13) (size 0.5) (drill 0.2) (layers "F.Cu" "B.Cu")'
            ' (net "/S0_0") (uuid "v-new"))\n'
            '  (via (at 18.8 13) (size 0.5) (drill 0.2) (layers "F.Cu" "B.Cu")'
            ' (net "/S0_1") (uuid "v-old"))\n'
            '  (via (at 18.4 13.4) (size 0.5) (drill 0.2) (layers "F.Cu" '
            '"B.Cu") (net "/S0_0") (uuid "v-gap"))\n')
    bg = geom.BoardGeom.from_file(board(tmp_path, extra=vias))
    (b,) = bgalib.find_bgas(bg)
    assert re_.via_in_pad_nets(bg, b, {"v-new", "v-gap"}) == {"/S0_0": 1}


def test_fanout_args_pin_floor_and_never_touch_drc_settings(tmp_path):
    b = _bga(0.8, 0.32)
    ep = bgalib.escape_params(b, JLC6)
    a = re_.fanout_args(b, ep, ["F.Cu", "In2.Cu"], tmp_path / "f.txt", 0.05)
    assert "--no-fix-drc-settings" in a and "--fab-overrides" in a
    assert a[a.index("--escape-method") + 1] == "dogbone"
    assert a[a.index("--via-size") + 1] == "0.5"
    i = a.index("--layers")
    assert a[i + 1:i + 3] == ["F.Cu", "In2.Cu"]
    fab = re_.fab_overrides(tmp_path, ep, {"hole_to_hole": 0.5,
                                           "edge_clearance": 0.3})
    txt = fab.read_text()
    assert "via_diameter = 0.5" in txt and "hole_to_hole = 0.5" in txt


def test_plan_cli_reports_without_krt(tmp_path, capsys):
    pcb = board(tmp_path)
    before = pcb.read_bytes()
    rc = re_.main(["--pcb", str(pcb), "--plan"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "pass"
    (b,) = out["facts"]["bgas"]
    assert b["ref"] == "U1" and b["method"] == "dogbone"
    assert b["via_size"] == pytest.approx(0.5)
    assert pcb.read_bytes() == before
    assert not (tmp_path / "route_escape").exists()


def test_no_bga_is_a_pass(tmp_path, capsys):
    pcb = board(tmp_path, pitch=2.54, pad=1.0)
    assert re_.main(["--pcb", str(pcb)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["facts"]["note"] == "no ball grid on this board"


def test_unknown_ref_is_an_error(tmp_path, capsys):
    assert re_.main(["--pcb", str(board(tmp_path)), "--refs", "U7"]) == 2


# ============================================================ live (slow)

def test_live_escape_synthetic_bga(tmp_path):
    if env.find_kicad_cli() is None or env.find_krt() is None:
        pytest.skip("needs kicad-cli and KiCadRoutingTools")
    pcb = board(tmp_path)
    out = tmp_path / "r.json"
    rc = re_.main(["--pcb", str(pcb), "--out-report", str(out)])
    rep = json.loads(out.read_text())
    assert rc == 0, rep
    f = rep["facts"]
    (b,) = f["bgas"]
    assert b["method"] == "dogbone"
    assert b["krt"]["escaped"] == 36
    assert b["kept_nets"] == 36 and b["left_for_routers"] == []
    assert f["drc_delta"]["new"] == {}
    assert f["board_updated"] and f["dru_rules"] == ["aiee_bga_escape_U1"]
    dru = pcb.with_suffix(".kicad_dru").read_text()
    assert dru.startswith("(version 1)") and "aiee_bga_escape_U1" in dru
    bg = geom.BoardGeom.from_file(pcb)
    assert bg.vias_of() and bg.tracks_of()
    # no via sits in a ball (dog-bone, not via-in-pad)
    (g,) = bgalib.find_bgas(bg)
    assert re_.via_in_pad_nets(bg, g, {v.uuid for v in bg.vias_of()}) == {}
