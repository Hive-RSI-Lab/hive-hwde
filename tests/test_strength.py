"""bin/strength.py and the score's use of it: hwde's planted faults are all
caught on this tree, a check loosened lets one through, and the score
charges the step that fault's gate decides on every board.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))

import score  # noqa: E402
import strength  # noqa: E402


def _missed(fs):
    return [name for _, name, ok in fs if not ok]


def test_every_planted_fault_is_caught():
    fs = strength.faults()
    assert _missed(fs) == []
    steps = {s for s, _, _ in fs}
    assert steps == set(strength.FAILS_ON), steps     # each gate the score counts
    assert sum(s == "verify" for s, _, _ in fs) >= 16
    assert sum(s == "dfm" for s, _, _ in fs) >= 1 + len(strength.DRILL_FAULTS)


def test_a_gate_that_stops_failing_warnings_lets_one_through(monkeypatch):
    import gate
    real = gate.load_gates

    def loose(path):
        g = real(path)
        g["erc"]["fail_severities"] = ["error"]
        g["dfm"]["max_count"] = 5
        return g
    monkeypatch.setattr(gate, "load_gates", loose)
    assert set(_missed(strength.gate_faults())) == {
        "gate erc: one warning", "gate dfm: one error"}


def test_a_lowered_jlc_floor_lets_the_drill_faults_through(tmp_path, monkeypatch):
    import dfm_check
    caps = yaml.safe_load(dfm_check.CAPABILITIES.read_text(encoding="utf-8"))

    def lower(d):
        for k, v in d.items():
            if isinstance(v, dict):
                lower(v)
            elif k == "min_hole_to_hole_mm":
                d[k] = 0.4
            elif k == "min_hole_diameter_mm":
                d[k] = 0.2
    lower(caps)
    p = tmp_path / "caps.yaml"
    p.write_text(yaml.safe_dump(caps), encoding="utf-8")
    monkeypatch.setattr(dfm_check, "CAPABILITIES", p)
    assert set(_missed(strength.dfm_faults())) == {
        "dfm hole-to-hole-0.45 (dfm_hole_to_hole)", "dfm drill-0.25 (dfm_hole_size)"}


def test_a_check_with_no_planted_fault_is_a_miss(monkeypatch):
    import score_checks
    real = score_checks.load_manifest

    def drop(*a, **k):
        m = real(*a, **k)
        m["mutants"] = {n: r for n, r in m["mutants"].items()
                        if r["check"] != "check_courtyard"}
        return m
    monkeypatch.setattr(score_checks, "load_manifest", drop)
    assert _missed(strength.verify_faults()) == ["verify check_courtyard: no planted fault"]


def test_planted_drill_hole_sits_clear_of_the_others():
    drl = "M48\nMETRIC\nT1C0.300\n%\nG90\nT1\nX0.0Y0.0\nX10.0Y0.0\nM30\n"
    out = strength._plant(drl, {"pair": 0.45}).splitlines()
    assert "T2C0.300" in out[:out.index("%")]
    assert out[-3:] == ["T2", "X0.750Y0.000", "M30"]


def test_score_charges_a_let_through_fault_on_every_board(monkeypatch, capsys):
    assert score.main() == 0
    base = float(capsys.readouterr().out.splitlines()[-1])
    monkeypatch.setattr(strength, "faults",
                        lambda: [("verify", "verify x (check_x)", False)])
    assert score.main() == 0
    out = capsys.readouterr().out.splitlines()
    assert "planted fault let through: verify x (check_x)" in out
    boards = [ln for ln in out if " clean: " in ln]
    assert boards and all("verify (planted" in ln for ln in boards)
    assert float(out[-1]) < base


def test_score_cannot_be_read_when_the_faults_cannot_run(monkeypatch, capsys):
    def boom():
        raise FileNotFoundError("manifest.yaml")
    monkeypatch.setattr(strength, "faults", boom)
    assert score.main() == 1
    assert "could not run" in capsys.readouterr().err
