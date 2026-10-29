"""Mated connector pins (check_mate_pins.py, reference/connector_pinmap.yaml):
MECH-05 declared mated pairs and MECH-06 identical unkeyed connectors.

Each synthetic case builds its own boards and asserts both the pin or
connector that is flagged and the one that is not. The planted faults
(mech-05-pinout-swap, mech-06-unkeyed-rails) are read from their manifest.d
fragment; the real-board cases (PCB-0011-A's twin 5-pin headers, the lumina
carrier and par stack) skip when the boards repo is absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(GOLDEN / "mutations"))
import check_mate_pins  # noqa: E402
import checklib  # noqa: E402
import mech_05_pinout_swap  # noqa: E402

from _boards import real_board  # noqa: E402

HDR4 = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
SKT4 = "Connector_PinSocket_2.54mm:PinSocket_1x04_P2.54mm_Vertical"
SKT3 = "Connector_PinSocket_2.54mm:PinSocket_1x03_P2.54mm_Vertical"
HDR2 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
JST2 = "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical"


def _fp(ref, fpid, x, y, nets, layer="F.Cu", pitch=2.54):
    """A 1xN row along +x from (x, y); nets[i] is pin i+1's net."""
    pads = "".join(
        f'    (pad "{i + 1}" thru_hole circle (at {pitch * i} 0) (size 1.7 1.7)'
        f' (drill 1) (layers "*.Cu" "*.Mask")'
        + (f' (net "{n}")' if n else "") + ")\n"
        for i, n in enumerate(nets))
    crt = "F.CrtYd" if layer == "F.Cu" else "B.CrtYd"
    return (f'  (footprint "{fpid}" (layer "{layer}") (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 -2 0))\n'
            f'    (attr through_hole)\n'
            f'    (fp_rect (start -1.5 -1.5) (end {pitch * len(nets) + 1} 1.5)'
            f' (stroke (width 0.05)) (fill no) (layer "{crt}"))\n'
            f'{pads}  )\n')


def _board(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 40 30) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    return path


def _run(pcb: Path, pairs=None, tmp=None) -> list[dict]:
    argv = ["--pcb", str(pcb)]
    if pairs is not None:
        cons = (tmp or pcb.parent) / "constraints.json"
        cons.write_text(json.dumps({"mating_pairs": pairs}), encoding="utf-8")
        argv += ["--constraints", str(cons)]
    payload, _ = check_mate_pins.run(argv)
    return payload["violations"]


def _pair(tmp_path, main_nets, mate_nets, mate_fp=SKT4, mate_layer="B.Cu",
          mount="stack", **extra):
    main = _board(tmp_path / "main.kicad_pcb",
                  _fp("J2", HDR4, 10, 10, main_nets))
    _board(tmp_path / "mate" / "d.kicad_pcb",
           _fp("J1", mate_fp, 10, 10, mate_nets, layer=mate_layer))
    return _run(main, [{"ref": "J2", "mate_ref": "J1",
                        "mate_pcb": "mate/d.kicad_pcb", "mount": mount,
                        **extra}])


NETS = ["+3V3", "/SWDIO", "/SWCLK", "GND"]


# ------------------------------------------------------------- MECH-05 pairs

def test_stacked_pair_wired_straight_passes(tmp_path):
    assert _pair(tmp_path, NETS, ["+3V3", "SWDIO", "SWCLK", "GND"]) == []


def test_stacked_pair_with_rails_swapped_is_a_supply_short(tmp_path):
    vs = _pair(tmp_path, NETS, ["GND", "/SWDIO", "/SWCLK", "+3V3"])
    assert {v["pin"] for v in vs} == {"1", "4"}       # 2 and 3 still meet
    assert all(v["kind"] == "mate_pin_mismatch" and v["severity"] == "error"
               and v["supply_short"] for v in vs)
    by_pin = {v["pin"]: v for v in vs}
    assert by_pin["1"]["pos"] == [10.0, 10.0]          # J2 pin 1
    assert by_pin["1"]["mate_net"] == "GND"


def test_signal_swap_is_a_mismatch_but_not_a_short(tmp_path):
    vs = _pair(tmp_path, NETS, ["+3V3", "/SWCLK", "/SWDIO", "GND"])
    assert {v["pin"] for v in vs} == {"2", "3"}
    assert not any(v["supply_short"] for v in vs)


def test_flip_mount_mirrors_the_mate(tmp_path):
    """The same straight socket mated face to face (board turned over) puts
    pin 1 on pin 4: every pin meets the wrong net."""
    assert _pair(tmp_path, NETS, NETS, mount="flip") != []
    # wired mirror-image for a flip, it passes
    assert _pair(tmp_path, NETS, list(reversed(NETS)), mount="flip") == []


def test_net_map_names_the_mate_side(tmp_path):
    mate = ["VDD", "/SWDIO", "/SWCLK", "GND"]
    assert {v["pin"] for v in _pair(tmp_path, NETS, mate)} == {"1"}
    assert _pair(tmp_path, NETS, mate, net_map={"+3V3": "VDD"}) == []


def test_unconnected_pin_is_not_compared(tmp_path):
    vs = _pair(tmp_path, NETS, ["+3V3", None, "unconnected-(J1-Pad3)", "/X"])
    assert [v["pin"] for v in vs] == ["4"]


def test_cable_pair_on_one_board_meets_pin_n_to_pin_n(tmp_path):
    pcb = _board(tmp_path / "b.kicad_pcb",
                 _fp("J1", HDR4, 5, 5, NETS)
                 + _fp("J5", HDR4, 5, 20, ["+3V3", "/SWCLK", "/SWDIO", "GND"]))
    vs = _run(pcb, [{"ref": "J1", "mate_ref": "J5", "mount": "cable"}])
    assert {v["pin"] for v in vs} == {"2", "3"}
    assert all(v["kind"] == "mate_pin_mismatch" for v in vs)


def test_patterns_that_do_not_line_up(tmp_path):
    vs = _pair(tmp_path, NETS, ["+3V3", "/SWDIO", "/SWCLK"], mate_fp=SKT3)
    assert [v["kind"] for v in vs] == ["mate_family_mismatch"]
    assert "do not line up" in vs[0]["msg"]


def test_known_families_that_do_not_mate(tmp_path):
    vs = _pair(tmp_path, NETS, NETS, mate_fp=JST2.replace("1x02", "1x04"))
    assert [v["kind"] for v in vs] == ["mate_family_mismatch"]
    assert "does not plug into" in vs[0]["msg"]


def test_unresolved_pair(tmp_path):
    main = _board(tmp_path / "main.kicad_pcb",
                  _fp("J2", HDR4, 10, 10, NETS))
    _board(tmp_path / "d.kicad_pcb", _fp("J1", SKT4, 10, 10, NETS, "B.Cu"))
    ok = {"ref": "J2", "mate_ref": "J1", "mate_pcb": "d.kicad_pcb"}
    assert _run(main, [ok]) == []
    for bad, why in (({"mate_ref": "J9"}, "J9 is not on"),
                     ({"mate_pcb": "gone.kicad_pcb"}, "not found"),
                     ({"ref": "J7"}, "J7 is not on this board")):
        vs = _run(main, [{**ok, **bad}])
        assert [v["kind"] for v in vs] == ["mate_pair_unresolved"], bad
        assert why in vs[0]["msg"]


def test_bad_pair_declarations_stop_the_check(tmp_path):
    main = _board(tmp_path / "main.kicad_pcb", _fp("J2", HDR4, 10, 10, NETS))
    with pytest.raises(checklib.CheckError, match="mount"):
        _run(main, [{"ref": "J2", "mate_ref": "J2", "mount": "sideways"}])
    with pytest.raises(checklib.CheckError, match="mate_ref"):
        _run(main, [{"ref": "J2"}])


# ------------------------------------------------- MECH-06 unkeyed twins

def test_identical_unkeyed_headers_with_different_rails(tmp_path):
    pcb = _board(tmp_path / "b.kicad_pcb",
                 _fp("J1", HDR2, 5, 5, ["+5V", "GND"])
                 + _fp("J3", HDR2, 5, 20, ["+3V3", "GND"]))
    vs = _run(pcb)
    assert [(v["kind"], v["pin"], v["refs"]) for v in vs] == [
        ("mate_unkeyed_rail_swap", "1", ["J1", "J3"])]      # pin 2 is GND both
    assert vs[0]["pos"] == [5.0, 5.0]


def test_twins_that_do_not_trip_the_rule(tmp_path):
    cases = {
        "same rails": (HDR2, ["+5V", "GND"], HDR2, ["+5V", "GND"]),
        "signals only": (HDR2, ["/TX", "GND"], HDR2, ["/RX", "GND"]),
        "keyed family": (JST2, ["+5V", "GND"], JST2, ["+3V3", "GND"]),
        "different footprints": (HDR2, ["+5V", "GND"],
                                 HDR4, ["+3V3", "GND", "/A", "/B"]),
    }
    for name, (fa, na, fb, nb) in cases.items():
        pcb = _board(tmp_path / name / "b.kicad_pcb",
                     _fp("J1", fa, 5, 5, na) + _fp("J3", fb, 5, 20, nb))
        assert _run(pcb) == [], name


def test_rail_and_ground_names():
    assert all(map(check_mate_pins.is_rail,
                   ["+3V3", "+5V", "VBUS", "/VCC", "VDD_IO", "+48V_SW", "V5"]))
    assert all(map(check_mate_pins.is_gnd, ["GND", "AGND", "PGND", "VSS"]))
    assert not any(map(check_mate_pins.is_rail,
                       ["GND", "/SWDIO", "/VSENSE_ADC", "/IO3", None]))
    assert check_mate_pins.canon("/control/ID_ADC") == "ID_ADC"


# ------------------------------------------------- golden corpus

@pytest.mark.parametrize("board", ["blinky2", "usbbuck4", "rf4"])
def test_golden_boards_are_clean(board):
    d = GOLDEN / board
    payload, _ = check_mate_pins.run(
        ["--pcb", str(d / f"{board}.kicad_pcb"),
         "--constraints", str(d / "constraints.json")])
    assert payload["violations"] == []


FRAGMENT = yaml.safe_load(
    (GOLDEN / "manifest.d" / "check_mate_pins.yaml").read_text(
        encoding="utf-8"))["mutants"]


@pytest.mark.parametrize("name", sorted(FRAGMENT))
def test_planted_fault_is_caught_where_it_is(name):
    m = FRAGMENT[name]
    d = GOLDEN / "mutants" / name
    argv = ["--pcb", str(d / f"{m['board']}.kicad_pcb")]
    cons = d / "constraints.json"
    argv += ["--constraints", str(cons if cons.exists()
                                  else GOLDEN / m["board"] / "constraints.json")]
    payload, _ = check_mate_pins.run(argv)
    exp = m["expect"]
    hits = [v for v in payload["violations"] if v["kind"] == exp["kind"]
            and exp["ref"] in v["refs"] and v["severity"] == "error"
            and v["pos"] == exp["pos"]]
    assert len(hits) == 1, payload["violations"]
    assert {v["kind"] for v in payload["violations"]} == {exp["kind"]}


def test_mech05_daughter_wired_straight_is_clean(tmp_path):
    """The planted fault's own fixture with the swap left out passes, so the
    catch is the swap and not the fixture."""
    (tmp_path / "mate").mkdir()
    (tmp_path / mech_05_pinout_swap.MATE).write_text(
        mech_05_pinout_swap.daughter(swap=False), encoding="utf-8")
    pair = {"ref": "J2", "mate_ref": "J1", "mate_pcb": mech_05_pinout_swap.MATE,
            "mount": "stack"}
    pcb = GOLDEN / "usbbuck4" / "usbbuck4.kicad_pcb"
    assert _run(pcb, [pair], tmp=tmp_path) == []
    (tmp_path / mech_05_pinout_swap.MATE).write_text(
        mech_05_pinout_swap.daughter(swap=True), encoding="utf-8")
    assert {v["pin"] for v in _run(pcb, [pair], tmp=tmp_path)} == {"1", "4"}


# ------------------------------------------------- real boards

def _pcb(ws: Path) -> Path:
    return next(iter(sorted((ws / "kicad").glob("*.kicad_pcb"))))


def test_bb_mcu_twin_headers_are_flagged():
    """PCB-0011-A, the MECH-06 hit: J2 (SWD) and J3 (IO) are the same 5-pin
    header and J2's pin 3 is +3V3 where J3's is /IO3."""
    vs = _run(_pcb(real_board("bb-mcu")))
    assert [(v["kind"], v["pin"], v["refs"]) for v in vs] == [
        ("mate_unkeyed_rail_swap", "3", ["J2", "J3"])]


def test_lumina_carrier_and_par_stack(tmp_path):
    """PCB-0004-A carries PCB-0005-A on two headers. Stacked the same way up,
    the power header J3 meets net for net; J4's three expansion lines carry
    different names on each board, which net_map says are the same. Turned
    over (flip), +48V_SW meets GND."""
    carrier = _pcb(real_board("lumina-carrier"))
    par = _pcb(real_board("lumina-par"))
    names = {"ADC0_CONN": "ADC0", "ADC1_CONN": "ADC1",
             "ID_ADC_CONN": "ID_ADC"}
    pairs = [{"ref": r, "mate_ref": r, "mate_pcb": str(par),
              "net_map": names} for r in ("J3", "J4")]
    assert _run(carrier, pairs, tmp=tmp_path) == []
    bare = _run(carrier, [{**p, "net_map": {}} for p in pairs], tmp=tmp_path)
    assert {(v["connector"], v["pin"]) for v in bare} == {
        ("J4", "20"), ("J4", "21"), ("J4", "22")}
    flip = _run(carrier, [{**pairs[0], "mount": "flip"}], tmp=tmp_path)
    assert any(v["supply_short"] and v["net"] == "+48V_SW" for v in flip)
