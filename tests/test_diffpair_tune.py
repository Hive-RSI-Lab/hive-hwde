"""Diff-pair skew tuning and running skew (hive-hwde #476).

check_diffpair's dynamic-phase check (running skew along the coupled trunk)
and route_tune's pair work: bumps on the short leg next to the bend that
built the skew, and coupled meanders for a pair in a length group."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))
import check_diffpair as cdp  # noqa: E402
import check_length_groups as clg  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import route_tune as rt  # noqa: E402
from test_route_tune import apply_text, fp, seg  # noqa: E402

GOLDEN = REPO / "tests" / "golden"
P, N = "/D_P", "/D_N"


def pair_board(tmp_path, *, pitch=0.3, w=0.15, bump_p=None, bump_n=None,
               other="", extra_nets="") -> Path:
    """A pair from U1 (x=5) to U2 (x=45): P at y=10, N at y=10+pitch.
    bump_p / bump_n = (x, h): one rectangular bump 1 mm wide, AWAY from the
    partner (P up, N down), adding 2 h to that leg."""
    pads = [("1", 0, 0, P), ("2", 0, pitch, N)]
    body = fp("U1", 5, 10, pads) + fp("U2", 45, 10, pads)
    for net, y, bump, sgn in ((P, 10, bump_p, -1), (N, 10 + pitch, bump_n, 1)):
        if bump:
            x, h = bump
            yy = round(y + sgn * h, 4)
            body += (seg(5, y, x, y, net, w=w) + seg(x, y, x, yy, net, w=w)
                     + seg(x, yy, x + 1, yy, net, w=w)
                     + seg(x + 1, yy, x + 1, y, net, w=w)
                     + seg(x + 1, y, 45, y, net, w=w))
        else:
            body += seg(5, y, 45, y, net, w=w)
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 60 30) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body}{extra_nets}{other})
"""
    p = tmp_path / "b.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def cons(tmp_path, pair: dict, groups=None) -> Path:
    c = tmp_path / "constraints.json"
    c.write_text(json.dumps({"diff_pairs": [{"p": P, "n": N, **pair}],
                             **({"length_groups": groups} if groups else {})}),
                 encoding="utf-8")
    return c


def diffpair(pcb, c):
    return cdp.run(["--pcb", str(pcb), "--constraints", str(c)])[0]


def tune_text(pcb, c, passes=4, **kw):
    """route_tune's plan applied on the text (no KiCad), pass by pass."""
    tuned = []
    for _ in range(passes):
        payload, _ = rt.run(["--pcb", str(pcb), "--constraints", str(c),
                             "--dry-run", "--ops-out", str(pcb.with_suffix(".ops.json"))]
                            + [x for k, v in kw.items() for x in (f"--{k}", str(v))])
        ops = json.loads(pcb.with_suffix(".ops.json").read_text())["ops"]
        if not ops:
            break
        tuned += payload["tuned"]
        apply_text(pcb, ops)
    return tuned, payload


# ------------------------------------------------------------ running skew

def test_known_answer_phase_flags_and_end_skew_does_not(tmp_path):
    """+1 mm on P at the first bump, +1 mm on N at the second: end skew 0,
    peak running skew 1 mm between them (the issue's known answer)."""
    pcb = pair_board(tmp_path, bump_p=(15, 0.5), bump_n=(35, 0.5))
    c = cons(tmp_path, {"max_skew_mm": 0.5})
    rep = diffpair(pcb, c)
    row = rep["checked"][0]
    assert row["skew_mm"] == pytest.approx(0.0, abs=1e-6)
    assert row["phase"]["peak_mm"] == pytest.approx(1.0, abs=0.02)
    assert row["phase"]["end_skew_mm"] == pytest.approx(0.0, abs=1e-6)
    kinds = [v["kind"] for v in rep["violations"]]
    assert kinds == ["diffpair_phase"]
    v = rep["violations"][0]
    assert v["peak_mm"] == pytest.approx(1.0, abs=0.02)
    # from past the P bump (x=15..16 is 10..12 mm along) to the N bump (x=35)
    assert v["from_mm"] == pytest.approx(12.0, abs=0.1)
    assert v["to_mm"] == pytest.approx(31.0, abs=0.1)
    # an excursion shorter than the window is not judged
    c2 = cons(tmp_path, {"max_skew_mm": 0.5, "phase_window_mm": 25})
    assert [v["kind"] for v in diffpair(pcb, c2)["violations"]] == []
    # rise time sets the window: 500 ps / 10 on FR4 ~ 7 mm < 20 mm
    c3 = cons(tmp_path, {"max_skew_mm": 0.5, "rise_ps": 500})
    rep3 = diffpair(pcb, c3)
    assert rep3["checked"][0]["phase"]["window_mm"] == pytest.approx(7.1, abs=0.4)
    assert [v["kind"] for v in rep3["violations"]] == ["diffpair_phase"]


def test_end_skew_is_not_also_a_phase_finding(tmp_path):
    pcb = pair_board(tmp_path, pitch=0.65, w=0.25, bump_p=(20, 1.0))
    rep = diffpair(pcb, cons(tmp_path, {"max_skew_mm": 0.5}))
    assert [v["kind"] for v in rep["violations"]] == ["diffpair_skew"]
    assert rep["checked"][0]["phase"]["peak_mm"] == pytest.approx(2.0, abs=0.02)


def test_profile_steps_at_corners_on_usbbuck4():
    """usbbuck4's USB pair turns four corners: DM is outside the first (-1.2),
    DP outside the next three; a sample beside a one-sided detour is not
    matched to the detour's leg (the mutant reads -6.2, not a ramp)."""
    for board, want in (("usbbuck4", [-1.2, 0.0, 1.3, 2.45]),
                        ("mutants/diffpair-skew", [-1.2, 0.0, -6.2, -4.9, -3.75])):
        bg = geom.load_board(GOLDEN / board / "usbbuck4.kicad_pcb")
        term = cdp.matched_terminals(bg, "/USB_DP", "/USB_DM")
        ph = cdp.pair_phase(bg, {"p": "/USB_DP", "n": "/USB_DM"}, term, 1.95, 4.5)
        steps = []
        for _, _, r, _ in ph["samples"]:
            if not steps or abs(r - steps[-1]) > 0.05:
                steps.append(r)
        assert steps[1:] == pytest.approx(want, abs=0.06)


# ------------------------------------------------------------ geometry

def test_coupled_points_add_the_same_to_both_legs():
    a, b, c, d = (0, 0), (20, 0), (20, 0.3), (0, 0.3)     # inner reversed
    outer, inner = rt.coupled_points(a, b, c, d, 3, 0.8, 0.45, 0.5)
    assert LineString(outer).length == pytest.approx(20 + 2 * 0.8 * 3)
    assert LineString(inner).length == pytest.approx(20 + 2 * 0.8 * 3)
    assert inner[0] == c and inner[-1] == d
    assert all(y <= 1e-9 for _, y in outer)              # away from inner (+y)
    # the two legs never come closer than the pitch
    assert LineString(outer).distance(LineString(inner)) == pytest.approx(0.3)
    assert rt.coupled_points(a, b, c, d, 30, 0.8, 0.45, 0.5) is None


def test_tapped_run_is_not_a_candidate(tmp_path):
    stub = seg(25, 10, 25, 7, P) + fp("R1", 25, 7, [("1", 0, 0, P)])
    pcb = pair_board(tmp_path, other=stub)
    bg = geom.load_board(pcb)
    run = next(t for t in bg.tracks_of(P) if t.length > 30)
    assert rt.tapped(bg, run)
    assert not rt.tapped(bg, next(t for t in bg.tracks_of(N) if t.length > 30))


# ------------------------------------------------------------ tuning

def test_end_skew_tuned_on_the_short_leg_next_to_its_cause(tmp_path):
    """A 2 mm one-sided bump on P at x=20: N gets 2 mm of bumps on the run
    beside it, facing away from P, low enough to stay coupled."""
    pcb = pair_board(tmp_path, pitch=0.65, w=0.25, bump_p=(20, 1.0))
    c = cons(tmp_path, {"max_skew_mm": 0.5})
    before = diffpair(pcb, c)["checked"][0]
    tuned, payload = tune_text(pcb, c)
    assert payload["violations"] == []
    t = tuned[0]
    assert (t["net"], t["kind"]) == (N, "skew")
    assert t["added_mm"] == pytest.approx(2.0, abs=0.011)
    after = diffpair(pcb, c)
    row = after["checked"][0]
    assert row["skew_mm"] <= 0.5 and after["violations"] == []
    assert row["uncoupled_n_mm"] <= before["uncoupled_n_mm"] + 1e-6
    assert row["uncoupled_p_mm"] <= before["uncoupled_p_mm"] + 1e-6
    assert row["impedance"]["zdiff_ohm"] == pytest.approx(
        before["impedance"]["zdiff_ohm"], rel=0.02)
    bg = geom.load_board(pcb)
    bumps = [tr for tr in bg.tracks_of(N) if abs(tr.shape.coords[0][1] - 10.65) > 1e-6
             or abs(tr.shape.coords[-1][1] - 10.65) > 1e-6]
    assert bumps and all(min(q[1] for q in tr.shape.coords) >= 10.65 - 1e-6
                         for tr in bumps)                   # away from P
    # beside the planted bump (x=20..21), not at the far end
    xs = [q[0] for tr in bumps for q in tr.shape.coords]
    assert 5 < min(xs) and max(xs) < 36


def test_phase_tuned_at_each_bend(tmp_path):
    """The known-answer pair: N gets +1 near x=15, then P +1 near x=35; the
    end skew stays 0 and the running skew falls under the limit."""
    pcb = pair_board(tmp_path, bump_p=(15, 0.5), bump_n=(35, 0.5))
    c = cons(tmp_path, {"max_skew_mm": 0.5})
    tuned, _ = tune_text(pcb, c)
    assert [(t["net"], t["added_mm"]) for t in tuned] == [
        (N, pytest.approx(1.0, abs=0.011)), (P, pytest.approx(1.0, abs=0.011))]
    rep = diffpair(pcb, c)
    assert rep["violations"] == []
    row = rep["checked"][0]
    # each bend's step is answered within a millimetre or so (not beside
    # the partner's bump: the leg would run uncoupled there), so what is
    # left is shorter than the 5 mm window
    assert row["phase"]["excursions"] == []
    assert row["skew_mm"] == pytest.approx(0.0, abs=0.011)
    assert row["uncoupled_p_mm"] == 0 and row["uncoupled_n_mm"] == 0


def test_no_room_for_skew_is_reported(tmp_path):
    # a foreign track hugging N's outside: no bump fits
    other = seg(5, 10.95, 45, 10.95, "/X", w=0.15)
    pcb = pair_board(tmp_path, pitch=0.65, w=0.25, bump_p=(20, 1.0), other=other)
    _, payload = tune_text(pcb, cons(tmp_path, {"max_skew_mm": 0.5}))
    assert [v["kind"] for v in payload["violations"]] == ["tune_skew_no_room"]


def test_pair_in_a_length_group_is_tuned_as_a_pair(tmp_path):
    """/D0 (single-ended) is 4 mm longer; the pair /D_P,/D_N gets coupled
    bumps on both legs: both +4 mm, skew unchanged, the group passes."""
    ref = (fp("U3", 5, 20, [("1", 0, 0, "/D0")]) + fp("U4", 45, 20, [("1", 0, 0, "/D0")])
           + seg(5, 20, 25, 20, "/D0") + seg(25, 20, 25, 22, "/D0")
           + seg(25, 22, 26, 22, "/D0") + seg(26, 22, 26, 20, "/D0")
           + seg(26, 20, 45, 20, "/D0"))
    pcb = pair_board(tmp_path, other=ref)
    c = cons(tmp_path, {"max_skew_mm": 0.5},
             groups=[{"name": "G", "nets": ["/D_*"], "ref_net": "/D0", "tol_mm": 0.3}])
    tuned, payload = tune_text(pcb, c)
    rows = [t for t in tuned if t["kind"] == "pair_length"]
    assert {t["net"] for t in rows} == {P, N}
    assert all(t["coupled_mm"] == pytest.approx(4.0, abs=0.011) for t in rows)
    assert payload["violations"] == []
    grp = clg.run(["--pcb", str(pcb), "--constraints", str(c)])[0]
    assert grp["status"] == "pass", grp["violations"]
    row = diffpair(pcb, c)["checked"][0]
    assert row["skew_mm"] == pytest.approx(0.0, abs=0.011)
    assert row["uncoupled_p_mm"] == 0 and row["uncoupled_n_mm"] == 0


# ------------------------------------------------------------ live (KiCad)

def _copy(src: Path, dst: Path) -> Path:
    for f in src.iterdir():
        if f.suffix in (".kicad_pcb", ".kicad_pro"):
            shutil.copy2(f, dst / f.name)
    return dst / "usbbuck4.kicad_pcb"


@pytest.mark.parametrize("board", ["usbbuck4", "mutants/diffpair-skew"])
def test_tune_usbbuck4_live(tmp_path, board):
    """The real writer and DRC: usbbuck4's USB pair (golden: DP 2.45 mm long
    from its outer corners; the diffpair-skew mutant: DM 3.75 mm long from a
    one-sided meander) tuned under 0.3 mm, uncoupled length not grown, zdiff
    within 2 %, kicad-cli DRC gains nothing."""
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("KiCad bundled python not installed")
    pcb = _copy(GOLDEN / board, tmp_path)
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"diff_pairs": [
        {"p": "/USB_DP", "n": "/USB_DM", "max_skew_mm": 0.3,
         "phase_window_mm": 1000}]}), encoding="utf-8")
    before = diffpair(pcb, c)["checked"][0]
    payload, _ = rt.run(["--pcb", str(pcb), "--constraints", str(c)])
    assert payload["status"] == "pass", payload["violations"]
    assert payload["drc"]["new"] == {}
    row = diffpair(pcb, c)["checked"][0]
    assert row["skew_mm"] <= 0.3
    assert row["uncoupled_p_mm"] <= before["uncoupled_p_mm"] + 1e-6
    assert row["uncoupled_n_mm"] <= before["uncoupled_n_mm"] + 1e-6
    assert row["impedance"]["zdiff_ohm"] == pytest.approx(
        before["impedance"]["zdiff_ohm"], rel=0.02)
