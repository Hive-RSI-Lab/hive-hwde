"""KiCad 4/5 legacy schematics (#1023): kicad-cli opens a .sch, exits 0 and
writes a netlist with no components, so every schematic check passed on
nothing. lib/legacy_net reads the nets from the shipped eeschema .net or the
board, and an empty export from a non-empty schematic is an error."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import legacy_net  # noqa: E402
import netlist_audit  # noqa: E402
import verify_all  # noqa: E402

SCH = """EESchema Schematic File Version 4
EELAYER 30 0
EELAYER END
$Descr A4 11693 8268
$EndDescr
$Comp
L Device:R R1
U 1 1 5C000001
P 1000 1000
F 0 "R1" H 1050 1000 50  0000 L CNN
F 1 "10k" H 1050 900 50  0000 L CNN
	1    1000 1000
$EndComp
$Comp
L MCU:MCU1 U1
U 1 1 5C000002
P 2000 1000
F 0 "U1" H 2000 1200 50  0000 C CNN
F 1 "MCU1" H 2000 800 50  0000 C CNN
	1    2000 1000
$EndComp
$Comp
L power:GND #PWR01
U 1 1 5C000003
P 2000 1500
F 0 "#PWR01" H 2000 1250 50  0001 C CNN
	1    2000 1500
$EndComp
$Sheet
S 3000 1000 500  500
U 5C000010
F0 "Sub" 50
F1 "sub.sch" 50
$EndSheet
$EndSCHEMATC
"""

SUB = """EESchema Schematic File Version 4
$Comp
L Device:C C1
U 1 1 5C000004
P 1000 1000
F 0 "C1" H 1050 1000 50  0000 L CNN
	1    1000 1000
$EndComp
$EndSCHEMATC
"""

LIB = """EESchema-LIBRARY Version 2.4
DEF Device_R R 0 0 N Y 1 F N
DRAW
X ~ 1 0 100 30 D 50 50 1 1 P
X ~ 2 0 -100 30 U 50 50 1 1 P
ENDDRAW
ENDDEF
DEF Device_C C 0 0 N Y 1 F N
DRAW
X ~ 1 0 100 30 D 50 50 1 1 P
X ~ 2 0 -100 30 U 50 50 1 1 P
ENDDRAW
ENDDEF
DEF MCU_MCU1 U 0 0 Y Y 1 F N
DRAW
X VDD 1 -200 100 100 R 50 50 1 1 W
X GND 2 -200 0 100 R 50 50 1 1 W
X PA0 3 200 100 100 L 50 50 1 1 B
X NC 4 200 0 100 L 50 50 1 1 N
ENDDRAW
ENDDEF
"""


def _mod(ref, value, pads):
    p = " ".join(f'(pad {n} smd rect (at 0 0) (size 1 1) (layers F.Cu) '
                 f'(net {i} "{net}"))' for i, (n, net) in enumerate(pads, 1))
    return (f"(module Lib:FP (layer F.Cu) (fp_text reference {ref} (at 0 0) "
            f"(layer F.SilkS)) (fp_text value {value} (at 0 0) "
            f"(layer F.Fab)) {p})")


PCB = ("(kicad_pcb (version 20171130) (host pcbnew 5.1.9) "
       + _mod("R1", "10k", [("1", "+3V3"), ("2", "/PA0")])
       + _mod("C1", "100n", [("1", "+3V3"), ("2", "GND")])
       + _mod("U1", "MCU1", [("1", "+3V3"), ("2", "GND"), ("3", "/PA0"),
                             ("4", "Net-(U1-Pad4)")])
       + ")")

NET_D = """(export (version D)
  (design (source x.sch) (tool "Eeschema 5.1.9"))
  (components
    (comp (ref R1) (value 10k) (footprint Lib:FP)
      (libsource (lib Device) (part R)))
    (comp (ref U1) (value MCU1) (footprint Lib:FP)
      (libsource (lib MCU) (part MCU1_ALT))))
  (libparts
    (libpart (lib Device) (part R)
      (pins (pin (num 1) (name ~) (type passive))
            (pin (num 2) (name ~) (type passive))))
    (libpart (lib MCU) (part MCU1)
      (aliases (alias MCU1_ALT))
      (pins (pin (num 1) (name VDD) (type power_in))
            (pin (num 3) (name PA0) (type BiDi))
            (pin (num 4) (name NC) (type NotConnected)))))
  (nets
    (net (code 1) (name +3V3)
      (node (ref R1) (pin 1)) (node (ref U1) (pin 1)))
    (net (code 2) (name /PA0)
      (node (ref R1) (pin 2)) (node (ref U1) (pin 3)))
    (net (code 3) (name "Net-(U1-Pad4)")
      (node (ref U1) (pin 4)))))
"""


def _board(tmp_path, with_net=False):
    (tmp_path / "x.sch").write_text(SCH)
    (tmp_path / "sub.sch").write_text(SUB)
    (tmp_path / "x-cache.lib").write_text(LIB)
    (tmp_path / "x.kicad_pcb").write_text(PCB)
    if with_net:
        (tmp_path / "x.net").write_text(NET_D)
    return tmp_path / "x.sch"


def _parse(net):
    return netlist_audit.parse_netlist(net)


def test_detects_legacy_files(tmp_path):
    sch = _board(tmp_path, with_net=True)
    assert legacy_net.is_legacy(sch)
    assert legacy_net.is_legacy_net(tmp_path / "x.net")
    modern = tmp_path / "m.kicad_sch"
    modern.write_text("(kicad_sch (version 20231120))")
    assert not legacy_net.is_legacy(modern)
    assert legacy_net.count_sch_components(sch) == 3  # R1 U1 + C1 on sub.sch


def test_from_board_names_pins_from_the_lib(tmp_path):
    sch = _board(tmp_path)
    r = legacy_net.netlist(sch, tmp_path / "out.net")
    assert r["status"] == "pass" and r["source"] == "board"
    assert r["components"] == 3
    text = (tmp_path / "out.net").read_text()
    assert '(ref "U1") (pin "1") (pintype "power_in") (pinfunction "VDD")' in text
    assert '(pintype "bidirectional") (pinfunction "PA0")' in text
    # KiCad 5's per-pin singleton net reads as KiCad 6's unconnected-*
    assert '"unconnected-(U1-Pad4)"' in text and "Net-(U1-Pad4)" not in text
    p = _parse(tmp_path / "out.net")
    assert set(p["components"]) == {"R1", "C1", "U1"}


def test_shipped_net_wins_and_fills_pins_via_alias(tmp_path):
    sch = _board(tmp_path, with_net=True)
    r = legacy_net.netlist(sch, tmp_path / "out.net")
    assert r["status"] == "pass" and r["source"] == "shipped"
    text = (tmp_path / "out.net").read_text()
    assert '(ref "U1") (pin "3") (pintype "bidirectional") (pinfunction "PA0")' in text
    assert '(ref "R1") (pin "1") (pintype "passive") (pinfunction "")' in text
    assert '"unconnected-(U1-Pad4)"' in text


def test_no_net_and_no_board_fails_loudly(tmp_path):
    sch = _board(tmp_path)
    (tmp_path / "x.kicad_pcb").unlink()
    r = legacy_net.netlist(sch, tmp_path / "out.net")
    assert r["status"] == "fail" and "empty netlist" in r["error"]


def test_planted_empty_export_is_an_error(tmp_path):
    """The planted fault: kicad-cli's empty netlist from a non-empty .sch."""
    sch = _board(tmp_path)
    empty = tmp_path / "empty.net"
    empty.write_text('(export (version "E") (components) (nets))\n')
    assert legacy_net.empty_export(sch, empty) == 3
    legacy_net.netlist(sch, tmp_path / "ok.net")
    assert legacy_net.empty_export(sch, tmp_path / "ok.net") == 0


def test_kc_export_netlist_flags_empty_export(tmp_path, monkeypatch):
    import kc
    sch = _board(tmp_path)
    out = tmp_path / "k.net"

    def fake(cli, args, **kw):
        out.write_text('(export (version "E") (components) (nets))\n')
        return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(kc, "run_cli", fake)
    r = kc.export_netlist(Path("kicad-cli"), sch, out)
    assert r["status"] == "error"
    assert "0 components" in r["error"] and "which has 3" in r["error"]


def test_audit_of_empty_netlist_judges_nothing(tmp_path):
    empty = tmp_path / "empty.net"
    empty.write_text('(export (version "E") (components) (nets))\n')
    payload, _ = netlist_audit.run(["--netlist", str(empty)])
    assert payload["violations"] == []
    assert "0 components" in (payload.get("nothing_to_judge") or "")


def test_audit_reads_a_legacy_sch(tmp_path):
    sch = _board(tmp_path)
    payload, _ = netlist_audit.run(["--sch", str(sch)])
    assert not payload.get("nothing_to_judge")
    kinds = {v["kind"] for v in payload["violations"]}
    assert "dangling_net" not in kinds  # the U1.4 singleton is unconnected-*
    payload, _ = netlist_audit.run(["--pcb", str(tmp_path / "x.kicad_pcb")])
    assert not payload.get("nothing_to_judge")


def test_audit_converts_a_given_legacy_net(tmp_path):
    _board(tmp_path, with_net=True)
    payload, _ = netlist_audit.run(["--netlist", str(tmp_path / "x.net")])
    assert not payload.get("nothing_to_judge")
    assert "dangling_net" not in {v["kind"] for v in payload["violations"]}


@pytest.mark.parametrize("with_net", [False, True])
def test_verify_all_board_netlist_on_legacy(tmp_path, with_net):
    _board(tmp_path, with_net=with_net)
    pcb = tmp_path / "x.kicad_pcb"
    net, note = verify_all.board_netlist(pcb, tmp_path / "reports")
    assert net is not None and net.is_file(), note
    assert note["source"] == "legacy"
    assert "pinfunction" in net.read_text()
    assert json.dumps(note)
