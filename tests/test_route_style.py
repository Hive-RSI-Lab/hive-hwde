"""check_route_style + the fixed review render set (competitive-research item 9).

The owner's routing rule (2026-10-08) - straight and 45-degree traces, no
needless arcs - scored from the .kicad_pcb: arcs and off-angle segments are
ERRORS (waivable per net), needless jogs stay warnings:

  - the golden blinky2 is clean (score 1.0), the frozen bb_adc fixture board
    scores exactly 4 off-angle segments + 2 jogs      -> test_frozen_*
  - each style is caught on its own synthetic board, and its look-alike that
    must NOT be caught sits on the same board         -> test_arc_*, test_angle_*,
                                                        test_jog_*
  - verify_all runs it on the board alone; the off-angle errors fail it, a
    per-net waiver on every erroring net passes it  -> test_verify_all_*
  - render.py --views review is the fixed set top,bottom,iso -> test_review_*
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_route_style  # noqa: E402
import cluster_violations  # noqa: E402
import gate  # noqa: E402
import render  # noqa: E402
import verify_all  # noqa: E402

BLINKY = ROOT / "tests" / "golden" / "blinky2" / "blinky2.kicad_pcb"
BB_ADC = ROOT / "tests" / "fixtures" / "bb_adc" / "bb-adc.kicad_pcb"


def _seg(a, b, w=0.25, uid="s"):
    return (f'\t(segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (width {w})'
            f' (layer "B.Cu") (net "+5V") (uuid "{uid}"))\n')


def _board(tmp_path, extra: str) -> Path:
    """blinky2 plus `extra` copper, placed at x>=200 - far off its outline, so
    no pad or via of the golden touches the synthetic tracks."""
    text = BLINKY.read_text(encoding="utf-8").rstrip()
    assert text.endswith(")")
    out = tmp_path / "synth.kicad_pcb"
    out.write_text(text[:-1] + extra + ")\n", encoding="utf-8")
    return out


def _score(pcb: Path):
    payload, _ = check_route_style.run(["--pcb", str(pcb)])
    return payload


def _hits(payload, style):
    return [it for v in payload["violations"] if v["style"] == style
            for it in v["items"]]


def test_frozen_golden_is_clean():
    p = _score(BLINKY)
    assert p["status"] == "pass"
    assert p["style"] == {"segments": 79, "arcs": 0, "off_angle": 0, "jogs": 0,
                          "flagged": 0, "score": 1.0}


def test_frozen_bb_adc_scores_exactly():
    p = _score(BB_ADC)
    assert p["style"] == {"segments": 73, "arcs": 0, "off_angle": 4, "jogs": 2,
                          "flagged": 6, "score": 0.918}
    assert {(v["style"], v["severity"]) for v in p["violations"]} == {
        ("angle", "error"), ("jog", "warning")}
    assert {v["kind"] for v in p["violations"]} == {"route_style"}
    assert sorted((v["net"], v["style"], len(v["items"]))
                  for v in p["violations"]) == [
        ("+3V3", "jog", 1), ("/AGND_SENSE", "angle", 1),
        ("/AIN_ADC", "angle", 2), ("/AIN_DIV", "angle", 1),
        ("VDD_ADC", "jog", 1)]
    assert all(it["uuid"] for v in p["violations"] for it in v["items"])


def test_arc_counted_straight_and_45_kept(tmp_path):
    arc = ('\t(arc (start 200 200) (mid 201.4645 200.6066) (end 202 202)'
           ' (width 0.25) (layer "B.Cu") (net "+5V") (uuid "arc1"))\n')
    p = _score(_board(tmp_path, arc + _seg((200, 210), (205, 210), uid="h")
                      + _seg((205, 210), (208, 213), uid="d45")))
    assert p["style"]["arcs"] == 1
    assert [it["uuid"] for it in _hits(p, "arc")] == ["arc1"]
    assert _hits(p, "angle") == [] and _hits(p, "jog") == []
    assert p["style"]["score"] == pytest.approx(1 - 1 / (79 + 2 + 1), abs=1e-3)


def test_angle_flagged_rounding_kept(tmp_path):
    # 30 degrees: off-angle. The 0.2 mm stub is 2.8 degrees off but its end
    # lands under 0.01 mm off the heading -> coordinate rounding, kept.
    p = _score(_board(tmp_path, _seg((200, 220), (208.66, 225), uid="a30")
                      + _seg((200, 230), (200.2, 230.0099), uid="round")))
    assert [it["uuid"] for it in _hits(p, "angle")] == ["a30"]
    assert p["style"]["off_angle"] == 1


def test_jog_flagged_real_sidestep_and_via_joint_kept(tmp_path):
    small = (_seg((200, 240), (205, 240), uid="j1")
             + _seg((205, 240), (205.1, 240.1), uid="j2")
             + _seg((205.1, 240.1), (210, 240.1), uid="j3"))
    wide = (_seg((200, 250), (205, 250), uid="w1")
            + _seg((205, 250), (206, 251), uid="w2")
            + _seg((206, 251), (211, 251), uid="w3"))
    at_via = (_seg((200, 260), (205, 260), uid="v1")
              + _seg((205, 260), (205.1, 260.1), uid="v2")
              + _seg((205.1, 260.1), (210, 260.1), uid="v3")
              + '\t(via (at 205 260) (size 0.6) (drill 0.3) (layers "F.Cu"'
                ' "B.Cu") (net "+5V") (uuid "via1"))\n')
    # a straight run split in two, the second stored end-to-start
    split = (_seg((200, 270), (205, 270), uid="c1")
             + _seg((207, 270), (205, 270), uid="c2")
             + _seg((207, 270), (210, 270), uid="c3"))
    p = _score(_board(tmp_path, small + wide + at_via + split))
    assert [it["uuid"] for it in _hits(p, "jog")] == ["j2"]
    assert p["style"]["jogs"] == 1


def test_verify_all_fails_it_on_off_angle_until_waived_per_net(tmp_path):
    summary, _ = verify_all.run(["--pcb", str(BB_ADC),
                                 "--reports-dir", str(tmp_path / "rep")])
    entry = summary["checks"]["check_route_style"]
    assert entry["status"] == "violations"
    mine = [v for v in summary["violations"]
            if v["source"] == "check_route_style"]
    assert len(mine) == 5
    assert sorted((v["style"], v["severity"]) for v in mine) == [
        ("angle", "error")] * 3 + [("jog", "warning")] * 2
    assert "check_route_style" in summary["coverage"]["failed"]
    assert cluster_violations.FIXER_HINTS["route_style"] == "router"

    g = {"tool": "verify", "fail_severities": ["error"], "max_count": 0}
    res = gate.evaluate("verify", g, summary, waivers=[])
    assert sorted(v["net"] for v in res["failing"]
                  if v["source"] == "check_route_style") == [
        "/AGND_SENSE", "/AIN_ADC", "/AIN_DIV"]
    # one waiver per net that needs its geometry clears exactly that net
    waivers = [{"check": "check_route_style", "kind": "route_style",
                "net": n, "reason": "analog sense geometry is intent",
                "approved": "test 2026-10-08"}
               for n in ("/AGND_SENSE", "/AIN_ADC")]
    res = gate.evaluate("verify", g, summary, waivers=waivers)
    left = [v for v in res["failing"] if v["source"] == "check_route_style"]
    assert [v["net"] for v in left] == ["/AIN_DIV"]
    assert res["status"] == "fail"
    waivers.append({**waivers[0], "net": "/AIN_DIV"})
    res = gate.evaluate("verify", g, summary, waivers=waivers)
    assert not [v for v in res["failing"]
                if v["source"] == "check_route_style"]
    assert res["waived_count"] == 3
    assert "check_route_style" not in res["coverage"]["failed"]
    assert "check_route_style" in res["coverage"]["waived"]


def test_verify_all_clean_board_passes_it(tmp_path):
    summary, _ = verify_all.run(["--pcb", str(BLINKY),
                                 "--reports-dir", str(tmp_path / "rep")])
    assert summary["checks"]["check_route_style"]["status"] == "pass"


def test_review_view_set_is_fixed():
    assert render.parse_views("review") == ["top", "bottom", "iso"]
    assert render.parse_views("review,top,left") == ["top", "bottom", "iso",
                                                     "left"]
    assert render.parse_views("top") == ["top"]
    with pytest.raises(ValueError):
        render.parse_views("reviews")
