"""schem_layout: the redraw a person can read, and the gate that keeps it honest."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import schem_layout as sl  # noqa: E402
import schlib  # noqa: E402


def _board(tmp_path: Path) -> Path:
    """A regulator-and-divider sheet in the stub-and-label form the generators
    write: U1 with two decouplers, a divider on FB, a series part to a connector."""
    s = schlib.Sheet("demo")
    s.add_component("Regulator_Linear:AMS1117-3.3", "U1", "AMS1117-3.3", (101.6, 76.2))
    s.wire_pins("U1", {"1": "GND", "2": "+3V3", "3": "+5V"})
    s.add_component("Device:C", "C1", "100n", (50.8, 50.8))
    s.wire_pins("C1", {"1": "+5V", "2": "GND"})
    s.add_component("Device:C", "C2", "10u", (63.5, 50.8))
    s.wire_pins("C2", {"1": "+3V3", "2": "GND"})
    s.add_component("Connector:Conn_01x03_Pin", "J1", "OUT", (152.4, 50.8))
    s.wire_pins("J1", {"1": "SIG", "2": "TAP", "3": "GND"})
    s.add_component("Device:R", "R1", "10k", (50.8, 101.6))
    s.wire_pins("R1", {"1": "SIG", "2": "TAP"})
    s.add_component("Device:R", "R2", "4k7", (63.5, 101.6))
    s.wire_pins("R2", {"1": "TAP", "2": "GND"})
    s.power_flag("GND", (25.4, 127.0), sym="power:GND")
    s.power_flag("+5V", (50.8, 127.0), sym="power:+5V")
    s.power_flag("+3V3", (76.2, 127.0), sym="power:+3V3", flag=False)
    return s.save(tmp_path, readable=False)


def _uuids(path: Path) -> dict[str, str]:
    sheet = sl.Sheet(path)
    return {s.ref: s.uuid for s in sheet.syms if not s.is_power}


def test_redraw_keeps_connectivity_and_draws_wires(tmp_path):
    path = _board(tmp_path)
    old = sl.Sheet(path)
    assert not old.hand_drawn()
    text, report, _ = sl.redraw(old)      # redraw self-checks the partition
    assert report["wired_nets"] >= 1
    out = tmp_path / "out.kicad_sch"
    out.write_text(text, encoding="utf-8")
    new = sl.Sheet(out)
    assert sl._partition(old).keys() == sl._partition(new).keys()
    assert {s.ref: s.uuid for s in old.syms if not s.is_power} == \
        {s.ref: s.uuid for s in new.syms if not s.is_power}
    # R1 and R2 share TAP through a real wire now, not two labels
    assert new.hand_drawn() or len(new.wires) > len(sl.sr._kids(new.root, "label"))


def test_only_reference_and_value_show(tmp_path):
    path = _board(tmp_path)
    s = schlib.Sheet("x")   # the same symbols, with a loud extra field
    s.add_component("Device:R", "R1", "1k", (50.8, 50.8), fields={"MPN": "LOUD-PART"})
    s.wire_pins("R1", {"1": "A", "2": "B"})
    s.add_component("Device:R", "R2", "1k", (76.2, 50.8))
    s.wire_pins("R2", {"1": "B", "2": "A"})
    p = s.save(tmp_path / "x", readable=False)
    text, _r, _b = sl.redraw(sl.Sheet(p))
    m = re.search(r'\(property "MPN" "LOUD-PART"[^\n]*', text)
    assert m and "(hide yes)" in m.group(0)
    assert path.exists()


def test_two_nets_never_share_a_wire_end(tmp_path):
    """Every wire end, pin and label of one net is off every wire of another:
    re-reading the redraw gives each net back alone."""
    old = sl.Sheet(_board(tmp_path))
    text, _r, _b = sl.redraw(old)
    out = tmp_path / "out.kicad_sch"
    out.write_text(text, encoding="utf-8")
    nets = sl.Sheet(out).nets
    ground = [n for n in nets if n.ground]
    assert len(ground) == 1
    assert not any(n.ground and {"SIG", "TAP"} & set(n.names()) for n in nets)


def test_a_changed_netlist_leaves_the_sheet_alone(tmp_path, monkeypatch):
    path = _board(tmp_path)
    original = path.read_text(encoding="utf-8")
    calls = iter([{"A": [("R1", "1")]}, {"A": [("R1", "2")]}])
    monkeypatch.setattr(sl, "netlist", lambda _p: next(calls))
    monkeypatch.setattr(sl, "_fit", lambda _p: None)
    monkeypatch.setattr(sl, "erc_errors", lambda _p: 0)
    report, code = sl.run(path)
    assert code == 1
    assert report["sheets"][path.name]["status"] == "failed"
    assert path.read_text(encoding="utf-8") == original


def test_a_short_is_caught_before_the_gate(tmp_path):
    old = sl.Sheet(_board(tmp_path))
    text, _r, _b = sl.redraw(old)
    # plant a fault: name one of SIG's wires GND
    bad = text.replace('(label "SIG"', '(label "GND"')
    if bad == text:
        bad = text.replace('(label "TAP"', '(label "GND"')
    assert bad != text
    with pytest.raises(sl.LayoutError):
        sl._self_check(old, bad)


def test_already_wired_sheets_are_skipped(tmp_path, monkeypatch):
    path = _board(tmp_path)
    text, _r, _b = sl.redraw(sl.Sheet(path))
    path.write_text(text, encoding="utf-8")
    assert sl.Sheet(path).hand_drawn()
    monkeypatch.setattr(sl, "netlist", lambda _p: {})
    monkeypatch.setattr(sl, "erc_errors", lambda _p: 0)
    report, code = sl.run(path)
    assert code == 0
    assert report["sheets"][path.name]["status"] == "skipped"


def test_redraw_is_the_same_every_time(tmp_path):
    path = _board(tmp_path)
    a = sl.redraw(sl.Sheet(path))[0]
    b = sl.redraw(sl.Sheet(path))[0]
    assert a == b


def test_worse_erc_restores_every_sheet(tmp_path, monkeypatch):
    path = _board(tmp_path)
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(sl, "netlist", lambda _p: {})
    monkeypatch.setattr(sl, "_fit", lambda _p: None)
    counts = iter([0, 2])
    monkeypatch.setattr(sl, "erc_errors", lambda _p: next(counts))
    report, code = sl.run(path)
    assert code == 1 and "restored" in report["erc"]
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.skipif(sl.env.find_kicad_cli() is None, reason="no kicad-cli")
def test_kicad_agrees_the_netlist_is_unchanged(tmp_path):
    path = _board(tmp_path)
    before = sl.netlist(path)
    report, code = sl.run(path)
    assert code == 0, report
    assert report["sheets"][path.name]["status"] == "redrawn"
    assert sl.netlist(path) == before


@pytest.mark.skipif(sl.env.find_kicad_cli() is None, reason="no kicad-cli")
def test_hier_fixture_redraws_clean(tmp_path):
    src = ROOT / "tests" / "s7_regen" / "hierdemo"
    tops = list(src.rglob("*.kicad_pro"))
    if not tops:
        pytest.skip("no built hierdemo")
    d = tmp_path / "h"
    shutil.copytree(tops[0].parent, d)
    top = d / (tops[0].stem + ".kicad_sch")
    before = sl.netlist(top)
    _report, code = sl.run(top)
    assert code == 0
    assert sl.netlist(top) == before


def test_stubs_and_wires_keep_a_pin_pitch_apart():
    import schem_lint as sl_
    # 1.27 apart, running side by side: flagged; a pin pitch apart, or only
    # stepping past an end, isn't
    a = (0.0, 0.0, 10.0, 0.0)
    assert sl_.close_wires([a, (0.0, 1.27, 10.0, 1.27)])
    assert not sl_.close_wires([a, (0.0, 2.54, 10.0, 2.54)])
    assert not sl_.close_wires([a, (10.0, 1.27, 20.0, 1.27)])


def test_redrawn_board_has_no_close_wires_or_text_on_text(tmp_path):
    import schem_lint as sl_
    top = _board(tmp_path)
    assert sl.run(top)[1] == 0
    res = sl_.measure(top, sl._cli())
    assert res["close_wires"] == 0, res["examples"]["close_wires"]
    assert res["text_text"] == 0, res["examples"]["text_text"]


def test_a_part_text_goes_round_what_is_already_there():
    box = sl.Boxes()
    box.add((0.0, 0.0, 10.0, 2.0))
    assert box.hits((1.0, 0.5, 2.0, 1.5)) > 0
    assert box.hits((1.0, 5.0, 2.0, 6.0)) == 0
