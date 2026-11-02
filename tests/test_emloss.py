"""lib/emloss.py (#553): wideband causal loss for board S-parameters.

Known answers, hermetic:
  - Djordjevic-Sarkar from one Dk/Df pair predicts FR408HR's other pairs
    (Isola's table as listed on Knowde: Dk 3.72 3.69 3.68 3.64 3.65, Df .0072
    .0091 .0092 .0098 .0095 at 0.1 1 2 5 10 GHz), and Kramers-Kronig holds;
  - alpha_d against Pozar 3.198, alpha_c against Wheeler's rule on
    Hammerstad-Jensen's closed form (no mesh) and the wide-line limit;
  - the skin effect's reactance equals its resistance, from the KK integral;
  - add_line_loss on a lossless line's S is the lossy line's S, and a planted
    tan d x10 gives the matching extra dB.
Live (test-slow): R(f) of a strip over a ground against FastHenry; dielectric
in the deck (Lorentz poles) against add_line_loss on the same coupon.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sim_solve  # noqa: E402
from lib import emboard as eb  # noqa: E402
from lib import emloss as L  # noqa: E402
from lib import emsolve as em  # noqa: E402
from lib import geom as G  # noqa: E402
from lib import solvers  # noqa: E402

live = pytest.mark.skipif(solvers.toolchain_root() is None, reason="no IIC-OSIC-TOOLS tree")


# ---------------------------------------------------------------- dielectric

def test_ds_is_set_by_its_one_frequency():
    d = L.DS(4.4, 0.02)
    e = complex(d.eps(1e9))
    assert e.real == pytest.approx(4.4)
    assert -e.imag / e.real == pytest.approx(0.02)


def test_ds_predicts_a_datasheets_other_frequencies():
    d = L.DS(3.68, 0.0092, 2e9)     # one pair of FR408HR's table
    for f, dk, df in ((1e8, 3.72, 0.0072), (1e9, 3.69, 0.0091), (5e9, 3.64, 0.0098), (1e10, 3.65, 0.0095)):
        assert float(d.eps(f).real) == pytest.approx(dk, rel=0.01)
        # 100 MHz is the far end: the laminate's Df falls there and DS's does not
        assert float(d.df(f)) == pytest.approx(df, rel=0.30 if f < 1e9 else 0.07)


def test_ds_is_causal_kramers_kronig():
    d = L.DS(4.4, 0.02)
    f = np.array([1e7, 1e8, 1e9, 1e10, 6e10])
    got = L.kk_real(lambda x: -d.eps(x).imag, f, d.einf)
    assert got == pytest.approx(d.eps(f).real, abs=2e-3)


def test_a_conductivity_is_not_causal_like_ds():
    """The trap this module exists for: kappa at 1 GHz has a Df that goes as
    1/f, 10x DS's at 100 MHz."""
    d = L.DS(4.4, 0.02)
    assert 0.02 * 1e9 / 1e8 > 8 * float(d.df(1e8))
    assert float(d.df(1e8)) == pytest.approx(0.02, rel=0.1)


def test_poles_hold_ds_over_the_band():
    d = L.DS(4.4, 0.02)
    f = np.logspace(8, math.log10(6e9), 40)
    fit = d.poles(1e8, 6e9)
    got = L.poles_eps(fit, f)
    assert np.abs(got.real / d.eps(f).real - 1).max() < 1e-3
    assert np.abs(got.imag / d.eps(f).imag - 1).max() < 0.02


def test_lorentz_terms_are_the_debye_terms_they_stand_for():
    csx = em.Csx(1e-3)
    d = L.DS(4.4, 0.02)
    L.dielectric_props(csx, "s", d, 1e8, 6e9)
    kind, attrs, inner, _ = csx.props[0]
    assert kind == "LorentzMaterial" and attrs["Order"] == 9
    f = np.logspace(8, math.log10(6e9), 20)
    w = 2 * np.pi * f
    eps = inner["Epsilon"] * np.ones_like(f, dtype=complex)
    for k in range(1, attrs["Order"] + 1):
        wp = 2 * np.pi * inner[f"EpsilonPlasmaFrequency_{k}"]
        wl = 2 * np.pi * inner[f"EpsilonLorPoleFrequency_{k}"]
        eps -= inner["Epsilon"] * wp ** 2 / (w ** 2 - wl ** 2 - 1j * w / inner[f"EpsilonRelaxTime_{k}"])
    assert np.abs(eps.imag / d.eps(f).imag - 1).max() < 0.08
    assert np.abs(eps.real / d.eps(f).real - 1).max() < 1e-3


# ---------------------------------------------------------------- conductor

def test_causal_x_of_skin_effect_equals_its_resistance():
    f = np.array([1e8, 1e9, 1e10])
    assert L.causal_x(np.sqrt, f) == pytest.approx(np.sqrt(f), rel=0.003)


def test_series_z_limits():
    rdc, g = 0.161, 380.0
    z = L.series_z(np.array([1.0, 1e10]), rdc, g, "smooth")
    assert z[0].real == pytest.approx(rdc, rel=1e-3)
    hf = L.surface_r(1e10) * g
    assert z[1].real == pytest.approx(hf, rel=0.01)
    assert z[1].imag == pytest.approx(hf, rel=0.01)


def test_roughness():
    # Hammerstad saturates at 2, and is 1 where the skin depth covers the teeth
    assert L.hammerstad_k(1e13, 2.0) == pytest.approx(2.0, rel=0.01)
    assert L.hammerstad_k(1e6, 2.0) == pytest.approx(1.0, abs=0.01)
    assert L.hammerstad_k(6e9, 2.0) > L.hammerstad_k(1e9, 2.0) > L.hammerstad_k(1e8, 2.0)
    assert float(L.huray_k(1e13, 0.5, 3.0)) == pytest.approx(5.5, rel=0.05)
    assert float(L.huray_k(6e9, 0.5, 3.0)) > float(L.huray_k(1e8, 0.5, 3.0))


def test_rough_reactance_tracks_rough_resistance():
    z = L.series_z(np.array([1e9, 6e9]), 0.161, 380.0, "std")
    s = L.series_z(np.array([1e9, 6e9]), 0.161, 380.0, "smooth")
    assert (z.real > 1.2 * s.real).all() and (z.imag > 1.1 * s.imag).all()


@pytest.mark.parametrize("w,h", [(3.06, 1.6), (0.96, 0.5), (0.3, 0.2), (10.0, 0.5)])
def test_alpha_c_of_a_smooth_microstrip_vs_closed_form(w, h):
    s = L.section(w, h, 4.4, 0.02, foil="smooth")
    got = L.alpha_db_per_m(s, [6e9], False, True)[0]
    assert got == pytest.approx(L.alpha_c_wheeler_ms(6e9, w, h, 0.035, s["z0"]), rel=0.10)


def test_alpha_c_approaches_the_wide_line_formula():
    s = L.section(40.0, 0.2, 4.4, 0.02, foil="smooth")
    got = L.alpha_db_per_m(s, [6e9], False, True)[0]
    assert got == pytest.approx(L.alpha_c_wide(6e9, s["z0"], 40.0), rel=0.10)


# ---------------------------------------------------------------- the line

def test_alpha_d_vs_pozar_on_the_kb_microstrip():
    """3.01 dB/m (kb/em-solvers.md). The field solver's q is the true energy
    fraction, about 1% under the secant (eps_eff - 1)/(er - 1) of Pozar 3.198."""
    s = L.section(3.06, 1.6, 4.4, 0.02, t=1e-4, foil="smooth")
    z, ee = L.hammerstad_eps_z(3.06, 1.6, 4.4)
    ref = float(L.alpha_d_closed(1e9, 4.4, ee, 0.02))
    assert ref == pytest.approx(3.01, abs=0.01)
    got = L.alpha_db_per_m(s, [1e9], True, False)[0]
    assert got == pytest.approx(ref, rel=0.02)
    assert got < ref


def test_stripline_alpha_d_is_the_plain_tan_d():
    s = L.section(0.2, 0.2, 4.2, 0.02, h2=0.2, foil="smooth")
    assert s["q"] == pytest.approx(1.0, abs=2e-3)
    d = L.DS(4.2, 0.02)     # all the field is in the dielectric, at its er at this f
    want = L.NP_TO_DB * math.pi * 6e9 / L.C0 * math.sqrt(float(d.eps(6e9).real)) * float(d.df(6e9))
    assert L.alpha_db_per_m(s, [6e9], True, False)[0] == pytest.approx(want, rel=0.01)


def _lossy_line_s(sec, f, length_mm):
    g, z = L.line_gamma_z(sec, f)
    gl = g * length_mm * 1e-3
    a, b, c = np.cosh(gl), z * np.sinh(gl), np.sinh(gl) / z
    den = a + b / 50 + c * 50 + a
    return (a + b / 50 - c * 50 - a) / den, 2 / den


def test_add_line_loss_on_a_lossless_line_is_the_lossy_line():
    s = L.section(0.96, 0.5, 4.4, 0.02, length=15.0)
    f = np.linspace(1e8, 6e9, 9)
    got = L.add_line_loss(eb.line_s(f, s["z0"], s["eps_eff"], 30.0), f, [s, s])
    s11, s21 = _lossy_line_s(s, f, 30.0)
    assert np.abs(got[:, 1, 0] - s21).max() < 1e-12
    assert np.abs(got[:, 0, 1] - s21).max() < 1e-12
    assert np.abs(got[:, 0, 0] - s11).max() < 1e-12
    assert np.abs(got[:, 1, 1] - s11).max() < 1e-12


def test_a_mismatched_line_still_gets_its_loss():
    """The EM line need not be the section's Z0: the correction is a small
    delta, so the wrong Z0 moves the answer little and keeps the mismatch."""
    s = L.section(0.96, 0.5, 4.4, 0.02, length=15.0, foil="smooth")
    f = np.array([3e9])
    wrong = eb.line_s(f, 60.0, s["eps_eff"], 30.0)
    got = L.add_line_loss(wrong, f, [s, s])
    extra = 20 * math.log10(abs(wrong[0][1][0])) - 20 * math.log10(abs(got[0, 1, 0]))
    assert extra == pytest.approx(L.alpha_db_per_m(s, f)[0] * 0.03, rel=0.1)


def test_100mm_of_fr4_at_6ghz_is_the_issues_loss():
    s = L.section(0.96, 0.5, 4.4, 0.02, length=50.0)
    f = np.array([6e9])
    got = L.add_line_loss(eb.line_s(f, s["z0"], s["eps_eff"], 100.0), f, [s, s])
    assert 1.0 < -20 * math.log10(abs(got[0, 1, 0])) < 3.0


def test_planted_tan_d_x10_gives_the_matching_extra_db():
    f = np.array([1e9, 6e9])
    one, ten = [L.alpha_db_per_m(L.section(0.96, 0.5, 4.4, t_, foil="smooth"), f, True, False)
                for t_ in (0.002, 0.02)]
    assert ten == pytest.approx(10 * one, rel=0.03)


def test_loss_keeps_a_line_passive_and_reciprocal():
    from lib import sparam
    s = L.section(0.96, 0.5, 4.4, 0.02, length=15.0)
    f = np.linspace(1e8, 6e9, 201)
    got = L.add_line_loss(eb.line_s(f, s["z0"], s["eps_eff"], 30.0), f, [s, s])
    q = sparam.check(f, got)
    assert q["ok"], q


def test_none_and_zero_length_leave_s_alone():
    f = np.array([1e9])
    S = eb.line_s(f, 50.0, 3.0, 30.0)
    s = L.section(0.96, 0.5, 4.4, 0.02)
    assert L.add_line_loss(S, f, [None, s]) == pytest.approx(np.array(S))


# ---------------------------------------------------------------- the stackup

def _pcb(path: Path, stackup=True) -> Path:
    block = '''(setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.0) (loss_tangent 0.02))
      (layer "dielectric 2" (type "core") (thickness 0.6) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.01))
      (layer "B.Cu" (type "copper") (thickness 0.035))))''' if stackup else "(setup)"
    path.write_text(f'''(kicad_pcb (version 20240108) (generator test)
  (general (thickness 0.87))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  {block}
  (net 0 "") (net 1 "SIG")
  (segment (start 0 10) (end 30 10) (width 1) (layer "F.Cu") (net 1))
  (segment (start 15 10) (end 15 18) (width 1) (layer "F.Cu") (net 1)))
''')
    return path


def test_geom_parses_loss_tangent_by_height(tmp_path):
    st = G.load_board(_pcb(tmp_path / "a.kicad_pcb")).stackup
    assert st.dielectrics[0]["loss_tangent"] == pytest.approx((0.2 * 0.02 + 0.6 * 0.01) / 0.8)
    assert st.tand_between("F.Cu", "B.Cu") == (pytest.approx(0.0125), "FR4")


def test_no_stackup_block_has_no_tan_d(tmp_path):
    st = G.load_board(_pcb(tmp_path / "a.kicad_pcb", stackup=False)).stackup
    assert st.tand_between("F.Cu", "B.Cu") == (None, "")


def test_every_stackups_row_has_a_numeric_tan_d():
    stacks = yaml.safe_load((ROOT / "reference" / "stackups.yaml").read_text())["stackups"]
    seen = 0
    for name, entry in stacks.items():
        for ly in entry.get("stack") or []:
            if ly.get("type") != "copper":
                seen += 1
                assert isinstance(ly.get("loss_tangent"), float) and 0 < ly["loss_tangent"] < 0.05, (name, ly)
    assert seen > 100


def test_df_reference_frequency():
    assert L.df_ref_hz("Rogers RO4350B") == 1e10
    assert L.df_ref_hz("FR4 7628") == 1e9
    assert L.df_ref_hz("") == 1e9


# ---------------------------------------------------------------- from a board

def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_port_length_splits_the_through_path_and_warns_about_a_stub():
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    poly = unary_union([Polygon(_rect(0, 9.5, 30, 10.5)), Polygon(_rect(14.5, 10, 15.5, 22))])
    a = {"x": 0, "y": 10, "w": 1.0}
    b = {"x": 30, "y": 10, "w": 1.0}
    warn: list = []
    assert L.port_length(poly, a, [b], warn, 0) == pytest.approx(15.0, abs=0.5)
    assert warn and "lossless Q" in warn[0]


def test_port_length_follows_a_bend():
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    poly = unary_union([Polygon(_rect(0, -0.5, 20.5, 0.5)), Polygon(_rect(19.5, 0, 20.5, 20))])
    length = L.port_length(poly, {"x": 0, "y": 0, "w": 1.0}, [{"x": 20, "y": 20, "w": 1.0}], [], 0)
    assert length == pytest.approx(20.0, rel=0.05)


def test_port_alone_on_its_copper_takes_its_longest_run():
    from shapely.geometry import Polygon
    poly = Polygon(_rect(0, -0.5, 12, 0.5))
    assert L.port_length(poly, {"x": 0, "y": 0, "w": 1.0}, [{"x": 40, "y": 0, "w": 1.0}], [], 0) \
        == pytest.approx(12.0, abs=0.5)


def _geo_port(**kw):
    return {"x": 0, "y": 10, "dir": "-x", "layer": "F.Cu", "ref": "B.Cu", "net": "SIG", "w": 1.0, **kw}


def test_sections_for_ports_reads_the_stackup(tmp_path):
    st = G.load_board(_pcb(tmp_path / "a.kicad_pcb")).stackup
    geo = {"copper": [{"layer": "F.Cu", "net": "SIG", "pts": _rect(0, 9.5, 30, 10.5)}]}
    ports = [_geo_port(), _geo_port(x=30, dir="+x")]
    got = L.sections_for_ports(geo, ports, st, foil="rtf")
    a, b = got["sections"]
    assert a["kind"] == "microstrip" and a["h"] == pytest.approx(0.8)
    assert a["tand"] == pytest.approx(0.0125) and a["foil"] == "rtf"
    assert a["length"] + b["length"] == pytest.approx(30.0, abs=0.7)
    assert got["warnings"] == []


def test_sections_for_ports_sees_a_coplanar_ground(tmp_path):
    st = G.load_board(_pcb(tmp_path / "a.kicad_pcb")).stackup
    geo = {"copper": [{"layer": "F.Cu", "net": "SIG", "pts": _rect(0, 9.5, 30, 10.5)},
                      {"layer": "F.Cu", "net": "GND", "pts": _rect(0, 10.7, 30, 15)},
                      {"layer": "F.Cu", "net": "GND", "pts": _rect(0, 5, 30, 9.3)}]}
    s = L.sections_for_ports(geo, [_geo_port()], st, lengths=[10.0])["sections"][0]
    assert s["kind"] == "cpwg" and s["g"] == pytest.approx(0.2, abs=1e-6) and s["length"] == 10.0


def test_planar_geom_carries_the_board_loss(tmp_path):
    g = sim_solve.planar_geom(str(_pcb(tmp_path / "a.kicad_pcb")), [0, 0, 30, 20],
                              [{"x": 0, "y": 10, "dir": "-x", "w": 1}])
    assert g["tand"] == pytest.approx(0.0125) and g["f_ref"] == 1e9 and g["t"] == 0.035


def test_planar_geom_without_a_stackup_says_it_does_not_know(tmp_path):
    g = sim_solve.planar_geom(str(_pcb(tmp_path / "a.kicad_pcb", stackup=False)), [0, 0, 30, 20],
                              [{"x": 0, "y": 10, "dir": "-x", "w": 1}])
    assert g["tand"] is None
    got = L.sections_for_planar({**g, "polys": [_rect(0, 9.5, 30, 10.5)]})
    assert got["sections"][0]["tand"] == 0.02 and "assumed" in got["warnings"][0]


def test_planar_with_loss_on_one_column_is_the_lossy_line():
    geo = {"h": 0.5, "er": 4.4, "tand": 0.02, "polys": [_rect(0, -0.48, 30, 0.48)],
           "ports": [{"x": 0, "y": 0, "dir": "-x", "w": 0.96}, {"x": 30, "y": 0, "dir": "+x", "w": 0.96}]}
    f = np.linspace(1e9, 6e9, 5)
    sec = L.section(0.96, 0.5, 4.4, 0.02)
    S = eb.line_s(f, sec["z0"], sec["eps_eff"], 30.0)
    cols = {0: [[S[m][0][0], S[m][1][0]] for m in range(5)]}
    out, note = sim_solve.planar_with_loss(geo, f, cols, "std", "hammerstad", [15.0, 15.0])
    assert list(out) == [0] and note["ports"][0]["foil_assumed"] is True
    _, s21 = _lossy_line_s(sec, f, 30.0)
    assert np.abs(np.array([c[1] for c in out[0]]) - s21).max() < 1e-3


def test_touchstone_comments_say_what_went_on():
    s = L.section(0.96, 0.5, 4.4, 0.02, length=15.0)
    note = sim_solve._loss_note([s], ["careful"], [1e9, 6e9])
    json.dumps(note)
    text = "\n".join(sim_solve._loss_comments(note))
    assert "Djordjevic-Sarkar" in text and "assumed" in text and "careful" in text


def test_deck_takes_a_dielectric_hook():
    """board_dielectric's poles reach the XML."""
    csx = em.Csx(1e-3)
    name = L.dielectric_props(csx, "slab0", L.DS(4.4, 0.02), 3e8, 6e9)
    csx.box(name, (0, 0, 0), (1, 1, 1))
    xml = csx.xml()
    assert '<LorentzMaterial' in xml and 'EpsilonRelaxTime_1' in xml


# ---------------------------------------------------------------- live

@live
def test_live_r_of_a_strip_over_ground_vs_fasthenry(tmp_path):
    """Second solver: series R of 3.06 x 0.035 mm over a wide plane 1.6 mm away,
    FastHenry's filaments against series_z with Wheeler's G. At 1 kHz it is the two
    Rdc's. At 10 and 100 MHz, the skin side of the knee, within 15%. Between
    (0.1 to 1 MHz) the model reads up to 40% low: the return crowds in the plane,
    which it leaves out (about 0.1 ohm/m, nothing at RF). At 1 GHz these 7 um
    filaments are thicker than the 2 um skin depth, so FastHenry reads low there."""
    import re
    w, h, t, l, gw = 3.06, 1.6, 0.035, 20.0, 40.0
    s = L.section(w, h, 4.4, 0.0, t=t, foil="smooth")
    sig = L.SIGMA_CU * 1e-3
    (tmp_path / "s.inp").write_text(
        "* strip over plane\n.units mm\n"
        f"N1 x=0 y=0 z={h}\nN2 x={l} y=0 z={h}\nN3 x=0 y=0 z=0\nN4 x={l} y=0 z=0\n"
        f"E1 N1 N2 w={w} h={t} sigma={sig} nwinc=41 nhinc=5 rw=1.3 rh=1.0\n"
        f"E2 N3 N4 w={gw} h={t} sigma={sig} nwinc=81 nhinc=5 rw=1.15 rh=1.0\n"
        ".equiv N2 N4\n.external N1 N3\n.freq fmin=1e3 fmax=1e8 ndec=1\n.end\n")
    r = solvers.run("fasthenry", ["s.inp"], cwd=tmp_path, timeout=1200)
    assert (tmp_path / "Zc.mat").exists(), r.stdout[-400:]
    pts = re.findall(r"frequency\s*=\s*([0-9.eE+\-]+)[^\n]*\n\s*([0-9.eE+\-]+)",
                     (tmp_path / "Zc.mat").read_text())
    got = {round(float(a)): float(b) / (l * 1e-3) for a, b in pts}
    rg = 1 / (L.SIGMA_CU * gw * t * 1e-6)
    assert got[1000] == pytest.approx(s["rdc"] + rg, rel=0.01)
    for f in (10 ** 7, 10 ** 8):
        want = L.series_z(np.array([float(f)]), s["rdc"], s["g_per_m"], "smooth")[0].real
        assert got[f] == pytest.approx(want, rel=0.15)
