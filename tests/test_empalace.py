"""lib/empalace.py: the same deck solved by Palace, and the verdict.

Hermetic: the mesh spec comes from emboard.deck's primitives (a port rectangle
where the deck's lumped end is, a barrel where the deck's post is, a sheet per
copper piece), the probes integrate a known field to the known V and I, the CSV
readers, the lumped-port shift undoes a known line, and the verdict fails what
it should fail.

Live (test-slow, skipped without the IIC-OSIC tree): the thru and the open-stub
known answers against the closed forms, see the tests' own limits.
"""
from __future__ import annotations

import cmath
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import sim_solve  # noqa: E402
from lib import emboard as eb  # noqa: E402
from lib import empalace as ep  # noqa: E402
from lib import solvers  # noqa: E402
from test_emboard import ER, H, W50, coupon, CPWG4  # noqa: E402

PORTS = ["0,10,-x", "30,10,+x"]
live = pytest.mark.skipif(solvers.toolchain_root() is None, reason="no IIC-OSIC-TOOLS tree")


@pytest.fixture
def thru(tmp_path):
    pcb = tmp_path / "t.kicad_pcb"
    return eb.extract(pcb, coupon(pcb))


def test_spec_is_the_decks_own_geometry(thru):
    spec, where, _ = ep.mesh_spec(thru, PORTS, 6e9)
    csx, w2 = eb.deck(thru, PORTS, 6e9, 0)
    assert where["ports"] == w2["ports"] and where["floor"] == "pec"
    assert spec["layers"][0] == [0.0, H] and spec["er"] == [ER, 1.0]
    # the floor is PEC, so B.Cu is not a sheet too; the strip and its feeds are
    assert [s["z"] for s in spec["sheets"]] == [H] and spec["sheets"][0]["signal"]
    xs = [p[0] for p in spec["sheets"][0]["outer"]]
    assert min(xs) == pytest.approx(-where["feed_len"], abs=1e-3) and max(xs) == pytest.approx(30 + where["feed_len"], abs=1e-3)
    # lumped ends where the deck has them: from strip to ground, as wide as the strip
    for q, side in zip(spec["ports"], (-1, 1)):
        assert {round(p[2], 6) for p in q["pts"]} == {0.0, H}
        ys = [p[1] for p in q["pts"]]
        assert max(ys) - min(ys) == pytest.approx(W50, abs=1e-3)
        assert (q["pts"][0][0] - 15) * side > 15


def test_spec_keeps_barrels_and_parts(tmp_path):
    pcb = tmp_path / "c.kicad_pcb"
    pcb.write_text(CPWG4)
    geo = eb.extract(pcb, [0, 0, 20, 10], parts={"R1": 49.9})
    spec, where, _ = ep.mesh_spec(geo, ["0,5,-x", "20,5,+x"], 6e9)
    assert where["floor"] == "pec"
    # two drills and the stitching along both feeds
    assert len(spec["posts"]) > 2
    r = [q for q in spec["ports"] if not q["excitable"]]
    assert len(r) == 1 and r[0]["r"] == 49.9 and r[0]["dir"] == "+X"
    assert {p[2] for p in r[0]["pts"]} == {1.4}


def _field_csv(path, name, rows, n):
    head = ["f (GHz)"] + [f"{p}{{{name}_{a}[{i}]}} (u)" for i in range(1, n + 1) for a in "xyz" for p in ("Re", "Im")]
    lines = [",".join(head)] + [",".join(f"{v:.12e}" for v in r) for r in rows]
    path.write_text("\n".join(lines) + "\n")


def test_probes_integrate_a_known_field(thru, tmp_path):
    """E uniform under the strip and H circling it: 1 V and 1 A come back."""
    _, where, pinfo = ep.mesh_spec(thru, PORTS, 6e9)
    plist, terms = ep.probe_list(pinfo)
    pts = [p["Center"] for p in plist]
    n = len(pts)
    # the voltage: E_z = -1/H below the strip, strip above ground
    e_row, b_row = [1.0], [1.0]
    cy = where["ports"][0]["y"]
    for (x, y, z) in pts:
        ez = -1.0 / (H * 1e-3)
        e_row += [0, 0, 0, 0, ez, 0.0]
        # B of a line current of 1 A along x at (cy, H): B = mu0 / (2 pi r)
        dy, dz = y - cy, z - H
        r2 = (dy * dy + dz * dz) * 1e-6
        k = ep.MU0 / (2 * math.pi * r2)
        b_row += [0, 0, -k * dz * 1e-3, 0, k * dy * 1e-3, 0]
    (tmp_path / "p").mkdir()
    _field_csv(tmp_path / "p" / "probe-E.csv", "E", [e_row], n)
    _field_csv(tmp_path / "p" / "probe-B.csv", "B", [b_row], n)
    reads = ep.feed_reads(tmp_path / "p", terms, [1e9])
    for k in (0, 1):
        us, ii = reads[k]
        for u in us:
            assert u[0] == pytest.approx(1.0, rel=1e-6)
    # the loop around the strip encloses the line current: Ampere, whichever way round
    assert abs(reads[0][1][0][0]) == pytest.approx(1.0, rel=2e-3)
    assert abs(reads[0][1][1][0]) == pytest.approx(1.0, rel=2e-3)


def test_wrong_frequency_list_is_refused(thru, tmp_path):
    _, _, pinfo = ep.mesh_spec(thru, PORTS, 6e9)
    plist, terms = ep.probe_list(pinfo)
    n = len(plist)
    (tmp_path / "p").mkdir()
    _field_csv(tmp_path / "p" / "probe-E.csv", "E", [[1.0] + [0.0] * 6 * n], n)
    _field_csv(tmp_path / "p" / "probe-B.csv", "B", [[1.0] + [0.0] * 6 * n], n)
    with pytest.raises(solvers.SolverError):
        ep.feed_reads(tmp_path / "p", terms, [1e9, 2e9])


def test_read_port_s(tmp_path):
    f = tmp_path / "port-S.csv"
    f.write_text("f (GHz), |S[1][1]| (dB), arg(S[1][1]) (deg.), |S[2][1]| (dB), arg(S[2][1]) (deg.)\n"
                 "1.0, -20.0, 90.0, 0.0, -45.0\n")
    (s11, s21), = ep.read_port_s(f, 0, 2)
    assert s11 == pytest.approx(0.1j, abs=1e-9)
    assert s21 == pytest.approx(cmath.exp(-1j * math.pi / 4))


def test_lumped_shift_removes_a_known_line():
    """A line of Z 60 between the ends is a 10 mm line and 2 x 5 mm of feed:
    shifted 5 mm from each end it reads the 10 mm line in the middle."""
    freqs = [1e9, 2e9, 3e9]
    ee, z = 3.0, 60.0
    whole = eb.line_s(freqs, z, ee, 20.0)
    raw = {e: [[complex(s[k][e]) for k in range(2)] for s in whole] for e in (0, 1)}
    lines = [{"z": [z] * 3, "eps_eff": [ee] * 3}] * 2
    waves = ep._shifted_waves(raw, lines, [5.0, 5.0], freqs, 2)
    got = eb.sparams(waves, 2)
    want = eb.line_s(freqs, z, ee, 10.0)
    for g, w in zip(got, want):
        for i in range(2):
            for j in range(2):
                assert complex(g[i][j]) == pytest.approx(w[i][j], abs=1e-9)


# ---------------------------------------------------------------- verdict

FR = [0.5e9 + 0.05e9 * k for k in range(61)]


def _notched(f0, depth=0.05):
    """S of a stub-like notch: S21 = j x / (1 + j x)-ish, S11 = S21 - 1."""
    out = []
    for f in FR:
        x = (f / f0 - f0 / f) * 8
        s21 = (depth + 1j * x) / (1 + 1j * x)
        out.append([[s21 - 1, s21], [s21, s21 - 1]])
    return out


def test_identical_agree():
    v = ep.compare(FR, _notched(2.5e9), _notched(2.5e9))
    assert v["ok"] and not v["disagree"]
    assert v["terms"]["S21"]["notch"]["shift_pct"] == 0


def test_small_notch_shift_agrees_and_a_large_one_does_not():
    assert ep.compare(FR, _notched(2.5e9), _notched(2.51e9))["ok"]
    v = ep.compare(FR, _notched(2.5e9), _notched(2.6e9))
    assert not v["ok"] and any("notch" in w for w in v["disagree"])


def test_a_notch_only_one_solver_sees_disagrees():
    flat = eb.line_s(FR, 50.0, 3.3, 0.001)
    v = ep.compare(FR, _notched(2.5e9), flat)
    assert not v["ok"] and any("only openEMS" in w for w in v["disagree"])


def test_a_different_line_disagrees():
    a, b = eb.line_s(FR, 50.0, 3.3, 30.0), eb.line_s(FR, 50.0, 3.6, 30.0)
    v = ep.compare(FR, a, b)
    assert not v["ok"] and any("rms" in w for w in v["disagree"])
    assert v["limits"] == {"rms_ds": ep.RMS_DS, "notch_pct": ep.NOTCH_PCT}


def test_compare_reads_the_lossless_s_and_deck_loss_is_refused(tmp_path, monkeypatch):
    pcb = tmp_path / "t.kicad_pcb"
    reg = coupon(pcb)
    freqs = [1e9, 2e9, 3e9]
    S = eb.line_s(freqs, 50.0, 3.3, 30.0)
    monkeypatch.setattr(eb, "study", lambda *a, **k: {
        "freqs": freqs, "S": S, "ports": [{"x": 0, "y": 10, "dir": "-x", "layer": "F.Cu", "ref": "B.Cu",
                                          "net": "RF", "w": W50, "h": H, "er": ER}] * 2,
        "runs": [{"excite": 0, "cells": 1, "seconds": 1, "z_feed": [50, 50]}], "region": reg,
        "fine": 0.1, "floor": "pec", "feeds": []})
    monkeypatch.setattr(ep, "study", lambda *a, **k: {
        "S": S, "S_lumped": S, "runs": [{"excite": 0}], "z_feed": [50, 50], "eps_eff_feed": [3.3, 3.3], "seconds": 1})
    seen = []
    real = ep.compare
    monkeypatch.setattr(ep, "compare", lambda fr, a, b, **k: (seen.append(a), real(fr, a, b, **k))[1])
    from lib import emloss
    monkeypatch.setattr(emloss, "sections_for_ports", lambda *a, **k: {"sections": [], "warnings": []})
    monkeypatch.setattr(emloss, "add_line_loss", lambda s, *a, **k: __import__("numpy").asarray(s) * 0.5)
    monkeypatch.setattr(sim_solve, "_loss_note", lambda *a, **k: {"model": "fake"})
    monkeypatch.setattr(sim_solve, "_loss_comments", lambda *a, **k: [])
    ports = PORTS
    res = sim_solve.board_study(pcb, reg, ports, f_max=3e9, points=3, symmetric=True, converge=False,
                                cross_check="palace")
    assert seen and all(a == S for a in seen)
    assert res["cross_check"]["ok"]
    assert res["s"]["S21"][0][0] == pytest.approx(round(S[0][1][0].real * 0.5, 6))   # what's reported keeps the loss
    with pytest.raises(ValueError, match="loss deck"):
        sim_solve.board_study(pcb, reg, ports, loss="deck", cross_check="palace", converge=False)


def test_cli_exits_1_when_the_solvers_disagree(tmp_path, monkeypatch):
    pcb = tmp_path / "t.kicad_pcb"
    reg = coupon(pcb)
    freqs = [1e9, 2e9, 3e9]
    S = eb.line_s(freqs, 50.0, 3.3, 30.0)
    far = eb.line_s(freqs, 50.0, 4.5, 30.0)

    def fake_eb(geo, ports, f_max, f_min=None, points=201, **kw):
        return {"freqs": freqs, "S": S, "ports": [{"x": 0, "y": 10, "dir": "-x", "layer": "F.Cu", "ref": "B.Cu",
                                                  "net": "RF", "w": W50, "h": H, "er": ER}] * 2,
                "runs": [{"excite": 0, "cells": 1, "seconds": 1, "z_feed": [50, 50]}], "region": reg,
                "fine": 0.1, "floor": "pec", "feeds": []}

    def fake_pal(answer):
        def f(geo, ports, f_max, f_min=None, points=201, **kw):
            return {"S": answer, "S_lumped": answer, "runs": [{"excite": 0}], "z_feed": [50, 50],
                    "eps_eff_feed": [3.3, 3.3], "seconds": 1}
        return f
    args = ["board", "--pcb", str(pcb), "--region", ",".join(map(str, reg)), "--port", PORTS[0], "--port", PORTS[1],
            "--symmetric", "--points", "3", "--no-converge", "--cross-check", "palace"]
    monkeypatch.setattr(eb, "study", fake_eb)
    monkeypatch.setattr(ep, "study", fake_pal(S))
    assert sim_solve.main(args) == 0
    monkeypatch.setattr(ep, "study", fake_pal(far))
    assert sim_solve.main(args) == 1


# ---------------------------------------------------------------- mesh, live

@live
def test_live_mesh_attributes(tmp_path, thru):
    spec, _, _ = ep.mesh_spec(thru, PORTS, 6e9, refine=0.5)
    a = ep.mesh(spec, tmp_path)
    assert a["tets"] > 500 and set(a["ports"]) == {"1", "2"}
    assert [m["er"] for m in a["materials"]] == [1.0, ER]
    assert (tmp_path / "board.msh").stat().st_size > 1000
    json.dumps(a)


# ---------------------------------------------------------------- known answers, live
# Refine 1.5 at order 2 is the coarsest thru that passes: refine 1 reads z0 7.6 %
# off at order 2 and 4.2 % off at order 3, against the 3 % limit.

@pytest.mark.slow
@live
def test_live_thru_known_answer(thru):
    import test_emboard as te
    res = ep.study(thru, PORTS, te.F_MAX, te.FREQS[0], len(te.FREQS), symmetric=True,
                   refine=1.5, order=2, timeout=3000)
    c = te._thru_check(res)
    assert c["ok"], c
    from lib import sparam
    q = sparam.check(res["freqs"], res["S"])
    assert q["ok"], sparam.summary(q)


@pytest.mark.slow
@live
def test_live_open_stub_notch(tmp_path):
    import test_emboard as te
    L = 13.0
    pcb = tmp_path / "t.kicad_pcb"
    geo = eb.extract(pcb, te.coupon(pcb, stub=L))
    res = ep.study(geo, PORTS, te.F_MAX, None, 271, symmetric=True, refine=1.0, order=3, timeout=4800)
    qs = te.imp.field_microstrip(te.W50, te.H, 1e-4, te.ER, mask=None)
    want = eb.notch_f(L + eb.open_end_dl(te.W50, te.H, qs["eps_eff"]), qs["eps_eff"])
    n = ep._notch(res["freqs"], [s[1][0] for s in res["S"]])
    assert n["db"] < -20
    assert n["f"] == pytest.approx(want, rel=0.02), (n, want)
