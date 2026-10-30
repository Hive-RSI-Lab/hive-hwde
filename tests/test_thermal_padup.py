"""check_thermal: pad-up packages, heatsinks, the enclosure, the firmware
governor and bulk-cap ripple (hifi-receiver-2 review-2 THERMAL-1/2/5/9).

Pure tests on synthetic boards; no toolchain.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_thermal  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402


def _fp(ref, value, at, *, lcsc=None, pad="GND"):
    lc = (f'    (property "LCSC" "{lcsc}" (at 0 0 0) (layer "F.Fab"))\n'
          if lcsc else "")
    return (f'  (footprint "t:{ref}" (at {at[0]} {at[1]}) (layer "F.Cu")\n'
            f'    (property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS"))\n'
            f'    (property "Value" "{value}" (at 0 0 0) (layer "F.Fab"))\n'
            f'{lc}'
            f'    (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu") '
            f'(net "{pad}"))\n'
            f'    (pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu") '
            f'(net "{pad}")))\n')


def _board(tmp_path, body, name="b"):
    sq = "(xy 0 0) (xy 40 0) (xy 40 40) (xy 0 40)"
    text = f"""(kicad_pcb (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" signal)
    (3 "B.Cu" signal) (25 "Edge.Cuts" user)) (setup)
  (gr_rect (start 0 0) (end 40 40) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body}  (zone (net "GND") (layer "B.Cu")
    (polygon (pts {sq}))
    (filled_polygon (layer "B.Cu") (pts {sq})))
)
"""
    p = tmp_path / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return geom.load_board(p)


AMP = _fp("U15", "TPA3221", (20, 20), lcsc="C475500")
HOLES = _fp("H5", "slot", (10, 20)) + _fp("H6", "slot", (30, 20))
HS = {"theta_sa_c_w": 20, "source": "19x12x30 pin fin, vendor curve",
      "part": "19x12x30 mm aluminium", "interface_mm": 0.1,
      "interface_k_w_mk": 3, "spreading_c_w": 1, "hold": "clip",
      "mount_refs": "H5,H6"}


def errs(vs, kind=None):
    return [v for v in vs if v["severity"] == "error"
            and (kind is None or v["kind"] == kind)]


def test_padup_known_by_lcsc_value_and_mpn(tmp_path):
    bg = _board(tmp_path, AMP + _fp("U2", "TPA3116D2DADR", (5, 5))
                + _fp("U3", "TPA3118D2DAPR", (35, 5))
                + _fp("U4", "TAS5630DKDR", (5, 35)))
    s = check_thermal.slug_top_info(bg, "U15")
    assert s["part"] == "TPA3221" and s["theta_ja_c_w"] == 44.8
    assert check_thermal.slug_top_info(bg, "U2")["part"] == "TPA3116D2"
    assert check_thermal.slug_top_info(bg, "U3") is None      # DAP: pad down
    assert check_thermal.slug_top_info(bg, "U4") is not None  # DKD: pad up
    assert check_thermal.slug_top_info(bg, "U3", {"slug": "top"})
    assert check_thermal.slug_top_info(bg, "U15", {"slug": "bottom"}) is None


def test_padup_without_heatsink_fails_and_asks_for_no_vias(tmp_path):
    """THERMAL-1: 3.5 W on the TPA3221's 44.8 C/W is ~157 C of rise; the
    remedy is a heatsink, not 36 vias."""
    bg = _board(tmp_path, AMP)
    vs, f = check_thermal.check_part(
        bg, {"ref": "U15", "net": "GND", "power_w": 3.5, "dt_c": 65})
    assert f["theta_ja"] == 44.8 and f["slug"] == "top"
    e = errs(vs, "thermal_area")
    assert e and "slug is on top" in e[0]["msg"] and "heatsink" in e[0]["msg"]
    assert not [v for v in vs if v["kind"] == "thermal_vias"]


def test_theta_override_below_datasheet_is_an_error(tmp_path):
    """THERMAL-9: core's 8 C/W 'into a bolted heatsink' with no heatsink."""
    bg = _board(tmp_path, AMP)
    vs, f = check_thermal.check_part(bg, {
        "ref": "U15", "net": "GND", "power_w": 3.0, "dt_c": 60,
        "theta_ja_c_w": 8.0, "theta_source": "SLASEE9C into a heatsink"})
    e = errs(vs, "thermal_constraint")
    assert e and "44.8" in e[0]["msg"]
    assert f["theta_ja"] == 44.8                 # the datasheet's is used
    assert errs(vs, "thermal_area")


@pytest.mark.parametrize("theta,src,match", [
    (6.0, "vendor table", "lower than any SMD"),
    (25.0, "on a 40x40 heat sink", "assumes a heatsink"),
])
def test_theta_override_sanity_for_unknown_parts(tmp_path, theta, src, match):
    bg = _board(tmp_path, _fp("U9", "LM1234", (20, 20)))
    vs, _ = check_thermal.check_part(bg, {
        "ref": "U9", "net": "GND", "power_w": 0.1, "theta_ja_c_w": theta,
        "theta_source": src})
    e = errs(vs, "thermal_constraint")
    assert e and match in e[0]["msg"]


def test_heatsink_path_in_parallel(tmp_path):
    """Review 1.4: 1.1 + 1.1 + 1 + 20 = 23.2 C/W through the heatsink, in
    parallel with 44.8 on the board, ~15.3 C/W: 3.5 W -> ~54 K."""
    bg = _board(tmp_path, AMP + HOLES)
    vs, f = check_thermal.check_part(bg, {
        "ref": "U15", "net": "GND", "power_w": 3.5, "dt_c": 65,
        "heatsink": HS})
    assert f["heatsink_interface_cw"] == pytest.approx(
        0.1e-3 / (3 * 29e-6), rel=1e-3)
    assert f["heatsink_r_cw"] == pytest.approx(23.25, abs=0.05)
    assert f["theta_ja"] == pytest.approx(1 / (1 / 44.8 + 1 / 23.25), rel=1e-3)
    assert errs(vs) == [], json.dumps(vs)


def test_adhesive_tape_interface_fails(tmp_path):
    """0.25 mm, 1 W/mK tape over 29 mm2 is 8.6 C/W: over budget at 4.5 W."""
    bg = _board(tmp_path, AMP)
    hs = dict(HS, interface_mm=0.25, interface_k_w_mk=1.0, hold="adhesive",
              theta_sa_c_w=25)
    vs, f = check_thermal.check_part(bg, {
        "ref": "U15", "net": "GND", "power_w": 4.5, "dt_c": 65,
        "heatsink": hs})
    assert f["heatsink_interface_cw"] == pytest.approx(8.62, abs=0.05)
    e = errs(vs, "thermal_area")
    assert e and "not tape" in e[0]["msg"]


def test_clip_without_board_provision_is_an_error(tmp_path):
    bg = _board(tmp_path, AMP)
    vs, _ = check_thermal.check_part(bg, {
        "ref": "U15", "net": "GND", "power_w": 1.0, "dt_c": 65,
        "heatsink": HS})
    e = errs(vs, "thermal_heatsink")
    assert e and "H5, H6 not on the board" in e[0]["msg"]
    vs, _ = check_thermal.check_part(bg, {
        "ref": "U15", "net": "GND", "power_w": 1.0, "dt_c": 65,
        "heatsink": {k: v for k, v in HS.items() if k != "mount_refs"}})
    assert errs(vs, "thermal_heatsink")


@pytest.mark.parametrize("drop,match", [
    ("source", "source"), ("theta_sa_c_w", "theta_sa_c_w"),
    ("part", "part"), ("hold", "hold")])
def test_heatsink_must_be_complete(tmp_path, drop, match):
    bg = _board(tmp_path, AMP + HOLES)
    with pytest.raises(checklib.CheckError, match=match):
        check_thermal.check_part(bg, {
            "ref": "U15", "net": "GND", "power_w": 1.0,
            "heatsink": {k: v for k, v in HS.items() if k != drop}})


def test_padup_unknown_needs_its_theta(tmp_path):
    bg = _board(tmp_path, _fp("U9", "XYZ", (20, 20)))
    vs, _ = check_thermal.check_part(bg, {
        "ref": "U9", "net": "GND", "power_w": 0.1, "slug": "top"})
    assert errs(vs, "thermal_constraint")


def test_governor_warning_and_limit(tmp_path):
    """THERMAL-2: full power (22 W) can't be held; name the limit."""
    bg = _board(tmp_path, AMP + HOLES)
    e = {"ref": "U15", "net": "GND", "power_w": 3.5, "dt_c": 65,
         "heatsink": HS, "power_max_w": 22}
    vs, f = check_thermal.check_part(bg, e)
    g = [v for v in vs if v["kind"] == "thermal_governor"]
    assert g and g[0]["power_limit_w"] == pytest.approx(65 / f["theta_ja"],
                                                        rel=1e-2)
    vs, f = check_thermal.check_part(bg, dict(e, governor="fw: 30 W in, 60 s"))
    assert not [v for v in vs if v["kind"] == "thermal_governor"]
    assert f["governor"].startswith("fw")


def _stl(path, dims):
    x, y, z = dims
    tris = [((0, 0, 0), (x, 0, 0), (x, y, z)), ((0, 0, 0), (0, y, 0), (x, y, z))]
    data = b"\0" * 80 + struct.pack("<I", len(tris))
    for t in tris:
        data += struct.pack("<12f", 0, 0, 0, *t[0], *t[1], *t[2]) + b"\0\0"
    path.write_bytes(data)


def test_enclosure_lite_box_air(tmp_path):
    """Review 6.1: 3.5 W in Lite's 106 x 96 x 46.8 box, 0.6 mm edge gap:
    ~22 K over the room by hand; the screen says ~20-30 K."""
    enc = {"box_mm": [106, 96, 46.8], "wall_mm": 2.4, "material": "PETG",
           "room_c": 30, "riser_mm2": 228, "inlet_mm2": 3328,
           "outlet_mm2": 3328, "stack_mm": 40}
    air, f, vs = check_thermal.enclosure_air(enc, 3.5, tmp_path)
    assert 20 < f["air_rise_c"] < 30
    assert f["g_vents_w_k"] == pytest.approx(0.033, abs=0.01)
    assert vs == []                             # PETG: > 20 K under Tg
    air2, _, vs = check_thermal.enclosure_air(dict(enc, material="PLA"),
                                              3.5, tmp_path)
    assert air2 == air and errs(vs, "thermal_enclosure")
    _stl(tmp_path / "box.stl", (106, 96, 46.8))
    stl = {k: v for k, v in enc.items() if k != "box_mm"}
    air3, f3, _ = check_thermal.enclosure_air(dict(stl, stl="box.stl"), 3.5,
                                              tmp_path)
    assert f3["box_mm"] == pytest.approx([106, 96, 46.8], abs=0.01)
    assert air3 == pytest.approx(air)
    # more vent area, cooler air
    air4, _, _ = check_thermal.enclosure_air(dict(enc, riser_mm2=1500),
                                             3.5, tmp_path)
    assert air4 < air - 3


def test_tj_max_against_box_air_and_run(tmp_path):
    bg_path = tmp_path / "b.kicad_pcb"
    _board(tmp_path, AMP + HOLES + _fp("U16", "TPA3221", (30, 30),
                                       lcsc="C475500")
           + _fp("C5", "470u", (5, 35)))
    cons = {"enclosure": {"box_mm": [200, 160, 60], "material": "PETG",
                          "riser_mm2": 1500, "room_c": 30},
            "thermal": [{"ref": "U15", "net": "GND", "power_w": 3.5,
                         "tj_max_c": 110, "heatsink": HS}],
            "ripple": [{"ref": "C5", "irms_a": 0.55, "rated_irms_a": 0.6,
                        "freq_multiplier": 1.2, "source": "Rubycon PX",
                        "irms_max_a": 1.25, "life_h": 2000, "rated_c": 105,
                        "life_min_h": 1e6}]}
    cp = tmp_path / "constraints.json"
    cp.write_text(json.dumps(cons), encoding="utf-8")
    payload, _ = check_thermal.run(["--pcb", str(bg_path),
                                    "--constraints", str(cp)])
    enc = payload["enclosure"]
    c = payload["checked"][0]
    assert c["air_c"] == enc["air_c"]
    assert c["dt_allowed_c"] == pytest.approx(110 - enc["air_c"])
    assert c["tj_c"] == pytest.approx(enc["air_c"] + c["rise_c"], abs=0.01)
    kinds = [v["kind"] for v in payload["violations"]]
    assert "thermal_unlisted" in kinds          # U16 has no entry
    rip = [v for v in payload["violations"] if v["kind"] == "thermal_ripple"]
    assert len(rip) == 3                        # 76 %, 174 % max, life
    assert payload["ripple"][0]["ripple_share"] == pytest.approx(0.764,
                                                                 abs=0.01)


def test_tj_max_needs_an_air_temperature(tmp_path):
    bg = _board(tmp_path, AMP)
    with pytest.raises(checklib.CheckError, match="air"):
        check_thermal.check_part(bg, {"ref": "U15", "net": "GND",
                                      "power_w": 1, "tj_max_c": 110,
                                      "theta_ja_c_w": 44.8,
                                      "theta_source": "SLASEE9C"})
