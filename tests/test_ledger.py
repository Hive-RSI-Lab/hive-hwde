"""The board's fact ledger: every fact it relies on is verified with a source,
failed, or unknown - and release refuses while any is not verified.

The person, 10-28: "know what we dont know". The hi-fi core board reached the
BOM review with J1 oversold and U8 swapped; its ledger would have listed both
as failed before any order step.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import ledger as ledger_cli  # noqa: E402
import ledgerlib  # noqa: E402
import stock_check  # noqa: E402
from test_stock_check import fake_fetch  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_LEDGER_WRITE", "1")
    ws = tmp_path / "boards" / "core"
    (ws / "parts").mkdir(parents=True)
    (ws / "fab").mkdir()
    (ws / "state.json").write_text("{}", encoding="utf-8")
    (ws / "parts" / "parts.json").write_text(json.dumps({"parts": [
        {"refdes": ["C1", "C2", "C3"], "lcsc": "C1525", "value": "100nF",
         "refdes_class": {"C3": "dnp"}},
        {"refdes": ["U8"], "lcsc": "C84681", "value": "CH340C",
         "mpn": "CH340C"},
        {"refdes": ["J1"], "lcsc": "C5184243", "mpn": "USB4105-GF-A-120",
         "value": "USB4105-GF-A-120"},
        {"refdes": ["J9"], "value": "screw", "assembly_class":
         "customer_supplied"},
    ]}), encoding="utf-8")
    (ws / "fab" / "BOM.csv").write_text(
        "Comment,Designator,Footprint,LCSC Part #\n"
        "100nF,\"C1,C2\",C0402,C1525\n"
        "CH340C,U8,SOP-16,C84681\n"
        "USB4105-GF-A-120,J1,USB-C,C5184243\n", encoding="utf-8")
    return ws


def test_expected_lists_what_the_board_relies_on(ws):
    exp = ledgerlib.expected(ws)
    kinds = lambda s: sorted(e["kind"] for e in exp.values()  # noqa: E731
                             if e["subject"] == s)
    assert kinds("C84681") == ["lifecycle", "part_match", "stock"]
    assert kinds("U8") == ["footprint", "pinout", "rating"]
    assert kinds("C1") == ["footprint", "rating"]       # two pins: no pinout
    assert kinds("C3") == [] and kinds("J9") == []      # dnp, not bought


def test_nothing_recorded_is_all_unknown_and_blocks(ws):
    rep = ledgerlib.report(ws)
    assert rep["ready"] is False and not rep["failed"]
    assert rep["counts"] == {"unknown": len(ledgerlib.expected(ws))}
    assert any("stock" in b and "unknown" in b
               for b in ledgerlib.blockers(ws))


def test_stock_check_writes_stock_match_and_lifecycle(ws):
    res = stock_check.check_rows(stock_check.read_bom(ws / "fab" / "BOM.csv"),
                                 boards=5, fetch=fake_fetch(), jobs=1)
    assert ledgerlib.record_stock(ws, res) == ws / "ledger.json"
    data = json.loads((ws / "ledger.json").read_text(encoding="utf-8"))
    f = data["facts"]
    assert f["C1525/stock"]["status"] == "verified"
    assert f["C1525/part_match"]["status"] == "verified"
    assert f["C5184243/stock"]["status"] == "failed"
    assert "-372" in f["C5184243/stock"]["value"]
    assert f["C84681/lifecycle"]["status"] == "failed"
    assert "C7464026" in f["C84681/lifecycle"]["value"]
    assert f["C1525/stock"]["source"] and f["C1525/stock"]["checked_at"]
    # the file lists the gaps too, not only what was checked
    assert f["U8/pinout"]["status"] == "unknown"
    blk = ledgerlib.blockers(ws)
    assert any(b.startswith("ledger: 1 lifecycle fact(s) failed - C84681")
               for b in blk)
    assert any("stock fact(s) failed - C5184243" in b for b in blk)


def test_stock_goes_stale(ws):
    res = stock_check.check_rows(
        [{"Comment": "100nF", "Designator": "C1,C2", "LCSC": "C1525"}],
        fetch=fake_fetch(), jobs=1)
    ledgerlib.record_stock(ws, res)
    fact = ledgerlib.load(ws)["facts"]["C1525/stock"]
    later = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=73)
    assert ledgerlib.effective(fact, None)["status"] == "verified"
    eff = ledgerlib.effective(fact, None, at=later)
    assert eff["status"] == "unknown" and "stale" in eff["why"]


def test_fact_about_a_replaced_part_reads_unknown(ws):
    ledgerlib.put(ws, [{"subject": "U8", "kind": "pinout",
                        "status": "verified", "lcsc": "C84681",
                        "source": "CH340 datasheet p.4"}], "hand")
    exp = ledgerlib.expected(ws)["U8/pinout"]
    assert ledgerlib.effective(ledgerlib.load(ws)["facts"]["U8/pinout"],
                               exp)["status"] == "verified"
    eff = ledgerlib.effective(ledgerlib.load(ws)["facts"]["U8/pinout"],
                              {**exp, "lcsc": "C7464026"})
    assert eff["status"] == "unknown" and "C7464026" in eff["why"]


def test_cli_set_needs_a_source_and_report_exits_on_gaps(ws, capsys):
    assert ledger_cli.main(["set", "--workspace", str(ws), "--subject", "U8",
                            "--kind", "pinout", "--status", "verified"]) == 2
    assert ledger_cli.main(["set", "--workspace", str(ws), "--subject", "U8",
                            "--kind", "pinout", "--status", "verified",
                            "--source", "CH340 datasheet v3B p.4"]) == 0
    capsys.readouterr()
    assert ledger_cli.main(["report", "--workspace", str(ws)]) == 1
    rep = json.loads(capsys.readouterr().out)
    assert rep["ready"] is False
    assert rep["by_kind"]["pinout"]["verified"] == 1


def test_every_fact_verified_is_ready(ws):
    facts = [{**e, "status": "verified", "source": "test"}
             for e in ledgerlib.expected(ws).values()]
    ledgerlib.put(ws, facts, "test")
    assert ledgerlib.report(ws)["ready"] is True
    assert ledgerlib.blockers(ws) == []


def test_writes_nothing_outside_a_workspace_or_when_off(ws, monkeypatch):
    (ws / "state.json").unlink()
    assert ledgerlib.put(ws, [], "x") is None
    (ws / "state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HWDE_LEDGER_WRITE", "0")
    assert ledgerlib.put(ws, [], "x") is None
    assert not (ws / "ledger.json").exists()


def test_release_refuses_on_ledger_gaps(tmp_path, monkeypatch):
    """attest build refuses while a fact is not verified; once all are, it
    issues, and verify() turns invalid again when stock goes stale."""
    import releaselib
    from test_attest import green_ws
    monkeypatch.setenv("HWDE_LEDGER_WRITE", "1")
    w = green_ws(tmp_path)
    att, problems = releaselib.build(w)
    assert att is not None, problems          # no parts: nothing relied on
    (w / "fab" / "attestation.json").unlink(missing_ok=True)
    (w / "parts").mkdir(exist_ok=True)
    (w / "parts" / "parts.json").write_text(json.dumps({"parts": [
        {"refdes": ["U8"], "lcsc": "C84681", "mpn": "CH340C"}]}))
    (w / "fab" / "BOM.csv").write_text(
        "Comment,Designator,Footprint,LCSC Part #\nCH340C,U8,SOP-16,C84681\n")
    att, problems = releaselib.build(w)
    assert att is None
    assert any(p.startswith("ledger: 1 stock fact(s) unknown - C84681")
               for p in problems)
    facts = [{**e, "status": "verified", "source": "test"}
             for e in ledgerlib.expected(w).values()]
    ledgerlib.put(w, facts, "test")
    att, problems = releaselib.build(w)
    assert att is not None, problems
    releaselib.write_attestation(w, att)
    assert releaselib.verify(w)["valid"] is True
    data = ledgerlib.load(w)
    data["facts"]["C84681/stock"]["checked_at"] = "2020-01-01T00:00:00+00:00"
    (w / "ledger.json").write_text(json.dumps(data))
    v = releaselib.verify(w)
    assert v["valid"] is False
    assert any("stock fact(s) unknown" in p for p in v["problems"])


# ------------------------------------------------- check_ratings' facts

def test_pinout_fact():
    import check_ratings
    ext = {"mpn": "X", "pinout": [{"pin": "1", "name": "VCC"},
                                  {"pin": "2", "name": "GND"}]}
    b = {"ref": "U1", "nums": ["1", "2"], "fns": {"1": "VCC", "2": "GND"}}
    assert check_ratings.pinout_fact(b, ext)[0] == "verified"
    b["fns"]["1"] = "TXD"
    st, why = check_ratings.pinout_fact(b, ext)
    assert st == "unknown" and "TXD" in why
    b["nums"].append("3")
    assert check_ratings.pinout_fact(b, ext)[0] == "failed"
    assert check_ratings.pinout_fact(b, None)[0] == "unknown"


def test_rating_facts_from_findings():
    import check_ratings
    board = [{"ref": "U1", "lcsc": "C1", "pads": {"1": "+5V"}},
             {"ref": "U2", "lcsc": "C2", "pads": {"1": "+5V"}},
             {"ref": "R1", "lcsc": "C3", "pads": {"1": "+5V", "2": "GND"}},
             {"ref": "U3", "lcsc": "C4", "pads": {"1": "X"}}]
    vios = [{"severity": "error", "refs": ["U1"], "kind":
             "rating_over_voltage", "msg": "over"},
            {"severity": "warning", "refs": ["R1"], "kind": "rating_unrated",
             "msg": "unrated"}]
    facts = {f["subject"]: f for f in check_ratings.ledger_facts(
        board, {}, vios, {"U2": 1}, {}) if f["kind"] == "rating"}
    assert facts["U1"]["status"] == "failed"
    assert facts["U2"]["status"] == "verified" and facts["U2"]["source"]
    assert facts["R1"]["status"] == "unknown"
    assert facts["U3"]["status"] == "unknown"


def test_cpl_verify_records_footprints(ws, tmp_path):
    import cpl_verify
    (ws / "kicad").mkdir()
    pcb = ws / "kicad" / "core.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    rows = [{"ref": "U8", "verdict": "ok", "basis": "pad number"},
            {"ref": "J1", "verdict": "no_fit", "why": "pads miss"},
            {"ref": "C1", "verdict": "no_model", "why": "no model"}]
    assert cpl_verify.record_footprints(
        pcb, rows, {"U8": "C84681", "J1": "C5184243", "C1": "C1525"})
    f = ledgerlib.load(ws)["facts"]
    assert f["U8/footprint"]["status"] == "verified"
    assert f["J1/footprint"]["status"] == "failed"
    assert f["C1/footprint"]["status"] == "unknown"
