"""S7 acceptance tests: schematic generation (schlib.py, the generator
pattern, netlist_audit.py, decoupling metadata).

Plan S7 accept criteria:
  - regenerate golden board 1's schematic from a generator script: ERC
    clean, netlist ELECTRICALLY IDENTICAL (same nets/pin memberships),
    audit passes
        -> hermetic: test_compare_regen_vs_golden / test_audit_regen_passes
           on COMMITTED artifacts; live: test_smoke_regen_blinky2 (rebuild,
           kicad-cli ERC, re-export, re-compare, committed-file freshness)
  - decoupler<->pin association metadata emitted for S4/S9
        -> test_decoupling_* (incl. check_decoupling on the golden BOARD
           driven by the S7-EMITTED metadata)
  - hierarchical sheet stitching (hier_pin + Project; hierdemo fixture)
        -> test_hierdemo_* / test_smoke_hierdemo

Hermetic tests use committed artifacts + pure venv; `smoke` tests rebuild
schematics live (kicad-sch-api symbol resolution needs the installed KiCad
libraries) and drive kicad-cli 10.0.3.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden"
REGEN = REPO / "tests" / "s7_regen" / "blinky2"
HIER = REPO / "tests" / "s7_regen" / "hierdemo"
PYTHON = sys.executable
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_decoupling  # noqa: E402
import checklib  # noqa: E402
import netlist_audit as na  # noqa: E402
import schlib  # noqa: E402

GOLDEN_NET = REGEN / "golden.net"
REGEN_NET = REGEN / "kicad" / "blinky2.net"
REGEN_SCH = REGEN / "kicad" / "blinky2.kicad_sch"
REGEN_META = REGEN / "kicad" / "decoupling.json"
HIER_NET = HIER / "kicad" / "hierdemo.net"


# ------------------------------------------------------------ helpers

def netlist_text(nets: dict, comps=()) -> str:
    """Minimal kicadsexpr netlist for synthetic audit tests."""
    comp_s = "".join(
        f'(comp (ref "{r}") (value "{v}") (footprint "{f}"))'
        for r, v, f in comps)
    net_s = ""
    for i, (name, nodes) in enumerate(nets.items(), 1):
        node_s = "".join(
            f'(node (ref "{r}") (pin "{p}") (pintype "{t}"))'
            for r, p, t in nodes)
        net_s += f'(net (code "{i}") (name "{name}") {node_s})'
    return f'(export (version "E") (components {comp_s}) (nets {net_s}))'


def write_netlist(tmp_path: Path, name: str, nets: dict, comps=()) -> Path:
    p = tmp_path / name
    p.write_text(netlist_text(nets, comps), encoding="utf-8")
    return p


def audit_violations(netlist: Path, constraints: dict, tmp_path: Path,
                     decoupling: dict | None = None):
    cpath = tmp_path / "constraints.json"
    cpath.write_text(json.dumps(constraints), encoding="utf-8")
    argv = ["--netlist", str(netlist), "--constraints", str(cpath)]
    if decoupling is not None:
        dpath = tmp_path / "decoupling.json"
        dpath.write_text(json.dumps(decoupling), encoding="utf-8")
        argv += ["--decoupling", str(dpath)]
    payload, _ = na.run(argv)
    return payload


def kinds(payload) -> list[tuple[str, str]]:
    return [(v["kind"], v["severity"]) for v in payload["violations"]]


# ------------------------------------------------------------ netlist parse

def test_parse_golden_netlist():
    p = na.parse_netlist(GOLDEN_NET)
    assert len(p["nets"]) == 43
    assert len(p["components"]) == 17
    v33 = {(m["ref"], m["pin"]) for m in p["nets"]["+3V3"]}
    assert ("U1", "48") in v33 and ("C1", "1") in v33 and len(v33) == 12
    types = {(m["ref"], m["pin"]): m["pintype"] for m in p["nets"]["+5V"]}
    assert types[("U2", "3")] == "power_in"
    assert p["components"]["U1"]["value"] == "STM32F103C8T6"
    assert sum(1 for n in p["nets"] if n.startswith("unconnected-")) == 32


def test_parse_rejects_bad_input(tmp_path):
    bad = tmp_path / "x.net"
    bad.write_text("(pcb (not a netlist))", encoding="utf-8")
    with pytest.raises(checklib.CheckError):
        na.parse_netlist(bad)
    with pytest.raises(checklib.CheckError):
        na.parse_netlist(tmp_path / "missing.net")


# ------------------------------------------------------------ compare mode

def test_compare_regen_vs_golden():
    """THE S7 acceptance: committed regenerated netlist is electrically
    identical to the committed golden export (same nets, same members)."""
    a = na.parse_netlist(REGEN_NET)
    b = na.parse_netlist(GOLDEN_NET)
    r = na.compare_netlists(a, b)
    assert r["identical"] is True
    assert r["net_counts"] == {"a": 43, "b": 43}


def test_compare_cli_identical_exit0(capsys):
    rc = na.main(["--netlist", str(GOLDEN_NET), "--compare", str(GOLDEN_NET)])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["identical"] is True


def test_compare_detects_differences(tmp_path):
    base = {"/A": [("R1", "1", "passive"), ("R2", "1", "passive")],
            "GND": [("R1", "2", "passive"), ("R2", "2", "passive")]}
    a = write_netlist(tmp_path, "a.net", base)
    # membership diff + missing net + extra net
    b = write_netlist(tmp_path, "b.net", {
        "/A": [("R1", "1", "passive")],
        "/B": [("R9", "1", "passive")],
        "GND": [("R1", "2", "passive"), ("R2", "2", "passive")]})
    payload, _ = na.run(["--netlist", str(a), "--compare", str(b)])
    assert payload["status"] == "violations"
    assert payload["identical"] is False
    diffs = {d["diff"] for d in payload["violations"]}
    assert diffs == {"membership", "missing_in_a"}
    md = next(d for d in payload["membership_diffs"] if d["net"] == "/A")
    assert md["only_in_a"] == [["R2", "1"]] or md["only_in_a"] == [("R2", "1")]


def test_compare_classifies_rename(tmp_path):
    members = [("R1", "1", "passive"), ("R2", "1", "passive")]
    a = write_netlist(tmp_path, "a.net", {"/OLD": members})
    b = write_netlist(tmp_path, "b.net", {"/NEW": members})
    payload, _ = na.run(["--netlist", str(a), "--compare", str(b)])
    assert payload["renamed"] == [{"a": "/OLD", "b": "/NEW", "members": 2}]
    assert [v["diff"] for v in payload["violations"]] == ["renamed"]
    assert payload["status"] == "violations"  # renames are NOT identity


# ------------------------------------------------------------ audit mode

def test_audit_regen_passes():
    """S7 acceptance: the audit passes on the regenerated design."""
    payload, _ = na.run([
        "--netlist", str(REGEN_NET),
        "--constraints", str(GOLDEN / "blinky2" / "constraints.json"),
        "--decoupling", str(REGEN_META)])
    assert payload["status"] == "pass"
    assert payload["violations"] == []
    assert payload["decoupling_associations"] == 6
    power = {p["net"]: p for p in payload["power"]}
    assert power["+3V3"]["power_in"] == 5 and power["+3V3"]["power_out"] == 1


def test_audit_hierdemo_passes():
    payload, _ = na.run([
        "--netlist", str(HIER_NET),
        "--constraints", str(HIER / "constraints.json"),
        "--decoupling", str(HIER / "kicad" / "decoupling.json")])
    assert payload["status"] == "pass"
    assert payload["violations"] == []


def test_audit_missing_net_error(tmp_path):
    payload = audit_violations(GOLDEN_NET, {"power": [{"net": "+9V"}]},
                               tmp_path)
    assert ("missing_net", "error") in kinds(payload)
    assert payload["status"] == "violations"
    v = payload["violations"][0]
    assert v["net"] == "+9V" and "power[0].net" in v["msg"]


def test_audit_reference_nets_checked(tmp_path):
    payload = audit_violations(GOLDEN_NET, {
        "high_speed": [{"net": "/OSC_IN",
                        "reference": {"In1.Cu": "/NOPE"}}]}, tmp_path)
    missing = [v for v in payload["violations"]
               if v["kind"] == "missing_net"]
    assert [v["net"] for v in missing] == ["/NOPE"]
    assert missing[0]["severity"] == "error"
    # undeclared +3V3/+5V power rails correctly warn with these constraints
    assert {v["kind"] for v in payload["violations"]} == {
        "missing_net", "power_undeclared"}


def test_audit_warnings_do_not_fail(tmp_path):
    net = write_netlist(tmp_path, "d.net", {
        "/TYPO": [("R1", "1", "passive")],
        "GND": [("R1", "2", "passive"), ("R2", "2", "passive")],
        "/OK": [("R2", "1", "passive"), ("R3", "1", "passive")],
        "unconnected-(R3-Pad2)": [("R3", "2", "passive+no_connect")]})
    payload = audit_violations(net, {}, tmp_path)
    assert kinds(payload) == [("dangling_net", "warning")]
    assert payload["status"] == "pass"          # warnings alone don't fail
    assert payload["violations"][0]["net"] == "/TYPO"


def test_audit_diffpair_checks(tmp_path):
    net = write_netlist(tmp_path, "d.net", {
        "/USB_DP": [("U1", "1", "bidirectional"), ("J1", "2", "passive")],
        "/X_P": [("U1", "2", "passive"), ("J1", "3", "passive")],
        "/X_N": [("U1", "3", "passive"), ("J1", "4", "passive")]})
    payload = audit_violations(net, {
        "diff_pairs": [{"p": "/X_P", "n": "/X_N"},
                       {"p": "/CANH", "n": "/CANL"}]}, tmp_path)
    ks = kinds(payload)
    # /USB_DP has no /USB_DM partner; /CANH//CANL nets missing + naming
    assert ("diffpair_unpaired", "warning") in ks
    assert ("diffpair_naming", "warning") in ks
    assert ks.count(("missing_net", "error")) == 2
    unpaired = [v for v in payload["violations"]
                if v["kind"] == "diffpair_unpaired"]
    assert [v["net"] for v in unpaired] == ["/USB_DP"]  # _P/_N pair is fine


def test_audit_power_tree(tmp_path):
    net = write_netlist(tmp_path, "p.net", {
        "/VCORE": [("U1", "9", "power_in"), ("C1", "1", "passive")],
        "GND": [("U1", "8", "power_in"), ("C1", "2", "passive")],
        "VSS3": [("U2", "1", "power_in"), ("C2", "1", "passive")],
        "+3V3": [("U9", "1", "passive"), ("C9", "1", "passive")]})
    payload = audit_violations(
        net, {"power": [{"net": "+3V3", "current_a": 0.1}]}, tmp_path)
    ks = kinds(payload)
    # /VCORE feeds power_in but is undeclared; grounds are exempt
    assert ("power_undeclared", "warning") in ks
    undeclared = [v["net"] for v in payload["violations"]
                  if v["kind"] == "power_undeclared"]
    assert undeclared == ["/VCORE"]             # GND + VSS3 exempt
    # +3V3 is declared but has no power_in consumer
    assert ("power_no_consumers", "warning") in ks
    assert payload["status"] == "pass"


def test_audit_power_series_escape_suppresses_passive_load_rails(tmp_path):
    """T6 P2-1: a declared rail whose loads are all passive series elements
    (LED resistors, droppers, fuses) is passive_fed, NOT a warning; a rail
    with only caps-to-GND (typo'd dead rail signature) still warns."""
    net = write_netlist(tmp_path, "p.net", {
        # /VLED feeds two LEDs through series resistors - real consumers
        "/VLED": [("J1", "1", "passive"), ("R1", "1", "passive"),
                  ("R2", "1", "passive"), ("C3", "1", "passive")],
        "/LED_A": [("R1", "2", "passive"), ("D1", "1", "passive")],
        "/LED_B": [("R2", "2", "passive"), ("D2", "1", "passive")],
        # /DEAD has only a connector pin and a cap to GND - nothing escapes
        "/DEAD": [("J2", "1", "passive"), ("C4", "1", "passive")],
        "GND": [("C3", "2", "passive"), ("C4", "2", "passive"),
                ("D1", "2", "passive"), ("D2", "2", "passive")]})
    payload = audit_violations(net, {
        "power": [{"net": "/VLED", "current_a": 0.1},
                  {"net": "/DEAD", "current_a": 0.1}]}, tmp_path)
    warned = [x["net"] for x in payload["violations"]
              if x["kind"] == "power_no_consumers"]
    assert warned == ["/DEAD"]
    facts = {p["net"]: p for p in payload["power"]}
    assert facts["/VLED"]["passive_fed_via"] == ["R1", "R2"]
    assert "passive_fed_via" not in facts["/DEAD"]
    # a 2-pin part whose far pin is NC is not an escape either
    net2 = write_netlist(tmp_path, "p2.net", {
        "/X": [("F9", "1", "passive"), ("C5", "1", "passive")],
        "unconnected-(F9-Pad2)": [("F9", "2", "passive+no_connect")],
        "GND": [("C5", "2", "passive")]})
    payload2 = audit_violations(
        net2, {"power": [{"net": "/X", "current_a": 0.1}]}, tmp_path)
    assert ("power_no_consumers", "warning") in kinds(payload2)


def test_audit_missing_ref_error(tmp_path):
    """T6 P4-4/P2-3 (ladder row 86): the pd-trigger near-miss verbatim -
    a separation ref 'R2' left behind after an R2A/R2B split is an ERROR."""
    net = write_netlist(tmp_path, "r.net", {
        "/A": [("R2A", "1", "passive"), ("R2B", "1", "passive"),
               ("U1", "1", "passive"), ("J1", "1", "passive")],
        "GND": [("R2A", "2", "passive"), ("R2B", "2", "passive"),
                ("U1", "2", "passive"), ("J1", "2", "passive")]},
        comps=[("R2A", "510", "R1206"), ("R2B", "510", "R1206"),
               ("U1", "MCU", "LQFP"), ("J1", "USB", "USBC")])
    cons = {"placement": {
        "edges": [{"ref": "J1", "edge": "left"}],
        "groups": [{"name": "g", "anchor": "U1", "members": ["R2A", "NOPE"]}],
        "fixed": ["H9"],
        "separation": [{"a": ["R2"], "b": ["U1"], "min_mm": 8}]},
        "thermal": [{"ref": "U7", "power_w": 1.0}]}
    payload = audit_violations(net, cons, tmp_path)
    missing = {x["refs"][0]: x for x in payload["violations"]
               if x["kind"] == "missing_ref"}
    assert set(missing) == {"R2", "NOPE", "H9", "U7"}
    assert all(x["severity"] == "error" for x in missing.values())
    assert "placement.separation[0].a[0]" in missing["R2"]["msg"]
    assert "thermal[0].ref" in missing["U7"]["msg"]
    assert payload["status"] == "violations"
    # clean constraints: every ref resolves, nothing fires
    ok = audit_violations(net, {"placement": {
        "separation": [{"a": ["R2A", "R2B"], "b": ["U1"], "min_mm": 8}]}},
        tmp_path)
    assert "missing_ref" not in {x["kind"] for x in ok["violations"]}
    assert ok["constraint_refs_checked"] == 3


def _netlist_with_libparts(tmp_path, name, nets, drop_pin3=True):
    """Synthetic netlist with libparts + libsource: U1 -> (Lib, Part) whose
    libpart declares pins 1-3."""
    node_s = ""
    for i, (nname, nodes) in enumerate(nets.items(), 1):
        ns = "".join(f'(node (ref "{r}") (pin "{p}") (pintype "{t}"))'
                     for r, p, t in nodes)
        node_s += f'(net (code "{i}") (name "{nname}") {ns})'
    text = ('(export (version "E") (components '
            '(comp (ref "U1") (value "X") (footprint "F") '
            '(libsource (lib "Lib") (part "Part")))) '
            '(libparts (libpart (lib "Lib") (part "Part") (pins '
            '(pin (num "1") (name "A") (type "passive")) '
            '(pin (num "2") (name "B") (type "passive")) '
            '(pin (num "3") (name "C") (type "passive"))))) '
            f'(nets {node_s}))')
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_audit_pin_no_net_via_libparts(tmp_path):
    """T6 P4-5/P2-7 (ladder row 68): a pin in the symbol inventory that
    reaches NO net warns (hierarchical exports drop NC singletons)."""
    net = _netlist_with_libparts(tmp_path, "h.net", {
        "/A": [("U1", "1", "passive")],
        "/B": [("U1", "2", "passive")]})
    cpath = tmp_path / "c.json"
    cpath.write_text("{}", encoding="utf-8")
    payload, _ = na.run(["--netlist", str(net), "--constraints", str(cpath)])
    hits = [x for x in payload["violations"] if x["kind"] == "pin_no_net"]
    assert len(hits) == 1 and hits[0]["severity"] == "warning"
    assert hits[0]["refs"] == ["U1"] and "U1.3" in hits[0]["msg"]
    assert payload["status"] == "pass"          # warning does not fail
    assert (payload["pins_expected"], payload["pins_connected"]) == (3, 2)
    # flat-style export: the NC pin is represented as an unconnected-* net
    net2 = _netlist_with_libparts(tmp_path, "f.net", {
        "/A": [("U1", "1", "passive")],
        "/B": [("U1", "2", "passive")],
        "unconnected-(U1-C-Pad3)": [("U1", "3", "passive+no_connect")]})
    payload2, _ = na.run(["--netlist", str(net2), "--constraints",
                          str(cpath)])
    assert "pin_no_net" not in {x["kind"] for x in payload2["violations"]}
    assert (payload2["pins_expected"], payload2["pins_connected"]) == (3, 3)


def test_audit_pin_no_net_prefers_comp_units_block(tmp_path):
    """A kicad-10 per-comp units block is the placed instance's own pin
    inventory and beats the all-units libpart union (multi-unit symbols
    must not fire on the unplaced unit's pins)."""
    text = ('(export (version "E") (components '
            '(comp (ref "U1") (value "X") (footprint "F") '
            '(libsource (lib "Lib") (part "Part")) '
            '(units (unit (name "A") (pins (pin (num "1")) (pin (num "2")))))'
            ')) '
            '(libparts (libpart (lib "Lib") (part "Part") (pins '
            '(pin (num "1") (name "A") (type "passive")) '
            '(pin (num "2") (name "B") (type "passive")) '
            '(pin (num "3") (name "C") (type "passive"))))) '
            '(nets (net (code "1") (name "/A") '
            '(node (ref "U1") (pin "1") (pintype "passive")) '
            '(node (ref "U1") (pin "2") (pintype "passive")))))')
    p = tmp_path / "u.net"
    p.write_text(text, encoding="utf-8")
    cpath = tmp_path / "c.json"
    cpath.write_text("{}", encoding="utf-8")
    payload, _ = na.run(["--netlist", str(p), "--constraints", str(cpath)])
    assert "pin_no_net" not in {x["kind"] for x in payload["violations"]}
    assert (payload["pins_expected"], payload["pins_connected"]) == (2, 2)


def test_audit_metadata_mismatch_kinds(tmp_path):
    comps = [("C1", "100nF", "C_0603"), ("C2", "10uF", "C_0805"),
             ("U1", "MCU", "LQFP")]
    net = write_netlist(tmp_path, "m.net", {
        "+3V3": [("C1", "1", "passive"), ("U1", "48", "power_in")],
        "GND": [("C1", "2", "passive"), ("U1", "8", "power_in"),
                ("C2", "2", "passive")],
        "/ELSE": [("C2", "1", "passive"), ("U1", "9", "power_in")]},
        comps)
    assocs = [
        {"cap": "C9", "ic": "U1", "pin": "48", "rail": "+3V3"},    # no cap
        {"cap": "C1", "ic": "U9", "pin": "48", "rail": "+3V3"},    # no ic
        {"cap": "C1", "ic": "U1", "pin": "48", "rail": "+9V"},     # no rail
        {"cap": "C1", "ic": "U1", "pin": "9", "rail": "+3V3"},     # pin off
        {"cap": "C2", "ic": "U1", "pin": "48", "rail": "+3V3"},    # cap off
        {"cap": "C1", "ic": "U1", "pin": "48", "rail": "+3V3",
         "value": "999nF"},                                        # drift
    ]
    payload = audit_violations(net, {}, tmp_path,
                               {"associations": assocs})
    ks = kinds(payload)
    assert ks.count(("metadata_mismatch", "error")) == 5
    assert ks.count(("metadata_mismatch", "warning")) == 1
    assert payload["status"] == "violations"


def test_audit_bad_inputs_exit2(tmp_path, capsys):
    assert na.main(["--netlist", str(tmp_path / "no.net"),
                    "--constraints", str(tmp_path / "no.json")]) == 2
    assert na.main([]) == 2
    assert na.main(["--netlist", str(GOLDEN_NET)]) == 2  # no constraints
    capsys.readouterr()


def test_audit_out_writes_report(tmp_path):
    out = tmp_path / "r.json"
    rc = na.main(["--netlist", str(GOLDEN_NET),
                  "--constraints",
                  str(GOLDEN / "blinky2" / "constraints.json"),
                  "--out", str(out)])
    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "pass"


# ------------------------------------------------------------ decoupling

def test_regen_decoupling_matches_golden_fixture():
    """The S7-emitted metadata equals the hand-written S4 fixture."""
    emitted = json.loads(REGEN_META.read_text(encoding="utf-8"))
    golden = json.loads((GOLDEN / "blinky2" / "decoupling.json")
                        .read_text(encoding="utf-8"))
    key = ("cap", "ic", "pin", "rail", "value")

    def norm(assocs):
        return sorted(tuple(a.get(k) for k in key) for a in assocs)
    assert norm(emitted["associations"]) == norm(golden["associations"])


def test_check_decoupling_accepts_emitted_metadata():
    """S4's check runs clean on the golden BOARD with S7's metadata."""
    payload, _ = check_decoupling.run([
        "--pcb", str(GOLDEN / "blinky2" / "blinky2.kicad_pcb"),
        "--metadata", str(REGEN_META)])
    assert payload["status"] == "pass"
    assert len(payload["checked"]) == 6


def test_hierdemo_decoupling_rail_net_override():
    meta = json.loads((HIER / "kicad" / "decoupling.json")
                      .read_text(encoding="utf-8"))
    rails = {a["cap"]: a["rail"] for a in meta["associations"]}
    assert rails == {"C10": "/VIN", "C11": "+3V3"}


# ------------------------------------------------------------ schlib pure

def test_stub_dir():
    assert schlib.stub_dir(0, 0) == (-1, 0)     # pin points right -> stub left
    assert schlib.stub_dir(180, 0) == (1, 0)
    assert schlib.stub_dir(90, 0) == (0, 1)     # pin points up -> stub down
    assert schlib.stub_dir(270, 0) == (0, -1)
    assert schlib.stub_dir(0, 90) == (0, 1)     # component rotation composes
    # Device:R pins (lib rot 270/90) on a rot-90 part: outward is horizontal
    assert schlib.stub_dir(270, 90) == (-1, 0)  # left pin -> stub left
    assert schlib.stub_dir(90, 90) == (1, 0)    # right pin -> stub right


def test_point_on_segment():
    seg = ((127.0, 63.5), (127.0, 68.58))
    assert schlib._point_on_segment((127.0, 65.0), *seg)      # interior
    assert schlib._point_on_segment((127.0, 63.5), *seg)      # endpoint
    assert not schlib._point_on_segment((127.01, 65.0), *seg)  # off-axis
    assert not schlib._point_on_segment((127.0, 70.0), *seg)   # past the end


def test_snap_and_grid_assert():
    assert schlib.snap(1.28) == 1.27
    assert schlib.snap(-1.9) == -1.27          # -1.9/1.27 = -1.496 -> -1
    assert schlib.snap(-1.27) == -1.27
    schlib.assert_on_grid((2.54, -1.27), "ok")
    with pytest.raises(ValueError, match="off the 1.27"):
        schlib.assert_on_grid((1.0, 1.27), "bad")


def test_apply_pin_number_fixups(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(
        '(kicad_sch (symbol (pin (name "Shield") (number "6")))'
        ' (symbol (lib_id "Connector:USB_B_Micro") (pin "6" (uuid "u1")))'
        ' (symbol (lib_id "Other:Part") (pin "6" (uuid "u2"))))',
        encoding="utf-8")
    schlib.apply_pin_number_fixups(
        [{"lib_id": "Connector:USB_B_Micro", "pin_name": "Shield",
          "wrong": "6", "right": "SH"}], sch)
    text = sch.read_text(encoding="utf-8")
    assert '(number "SH")' in text
    assert '(pin "SH" (uuid "u1"))' in text
    assert '(pin "6" (uuid "u2"))' in text      # other lib_id untouched
    with pytest.raises(ValueError, match="not found"):
        schlib.apply_pin_number_fixups(
            [{"lib_id": "X:Y", "pin_name": "Nope", "wrong": "1",
              "right": "2"}], sch)


def test_write_project_minimal(tmp_path):
    p = schlib.write_project("demo", tmp_path)
    pro = json.loads(p.read_text(encoding="utf-8"))
    assert pro["erc"]["rule_severities"]["lib_symbol_issues"] == "ignore"
    assert pro["board"]["design_settings"]["rules"]["min_track_width"] == 0.127
    assert pro["meta"]["filename"] == "demo.kicad_pro"
    assert set(pro) == {"board", "erc", "meta", "schematic"}


# ------------------------------------------------------------ smoke (live)

def _run_gen(gen: Path, out_dir: Path) -> dict:
    cp = subprocess.run([PYTHON, str(gen), str(out_dir)],
                        capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return json.loads(cp.stdout)


def _erc_total(sch: Path) -> int:
    import kc
    from lib import env
    r = kc.run_erc(env.find_kicad_cli(), sch)
    return r["counts"]["total"]


def _export_net(sch: Path, out: Path) -> Path:
    import kc
    from lib import env
    r = kc.export_netlist(env.find_kicad_cli(), sch, out)
    assert r["status"] == "pass", r
    return out


@pytest.mark.smoke
def test_smoke_regen_blinky2_full_acceptance(tmp_path):
    """Rebuild from the generator, ERC, export, compare - live."""
    summary = _run_gen(REGEN / "kicad" / "gen" / "root.py", tmp_path)
    assert summary["decoupling_associations"] == 6
    sch = tmp_path / "blinky2.kicad_sch"
    assert _erc_total(sch) == 0                     # ERC clean
    import schem_refdes as sr
    assert sr.audit_sheet(sr.Sheet(sch)) == []      # P4-1: fields born clean
    fresh = _export_net(sch, tmp_path / "fresh.net")
    r = na.compare_netlists(na.parse_netlist(fresh),
                            na.parse_netlist(GOLDEN_NET))
    assert r["identical"], r                        # identical to golden
    r2 = na.compare_netlists(na.parse_netlist(fresh),
                             na.parse_netlist(REGEN_NET))
    assert r2["identical"], r2                      # committed net current
    emitted = json.loads((tmp_path / "decoupling.json")
                         .read_text(encoding="utf-8"))
    committed = json.loads(REGEN_META.read_text(encoding="utf-8"))
    assert emitted == committed                     # committed meta current


@pytest.mark.smoke
def test_smoke_golden_net_current(tmp_path):
    """The committed golden.net matches a fresh export of the golden sch."""
    fresh = _export_net(GOLDEN / "blinky2" / "blinky2.kicad_sch",
                        tmp_path / "g.net")
    r = na.compare_netlists(na.parse_netlist(fresh),
                            na.parse_netlist(GOLDEN_NET))
    assert r["identical"], r


@pytest.mark.smoke
def test_smoke_hierdemo_full(tmp_path):
    summary = _run_gen(HIER / "kicad" / "gen" / "root.py", tmp_path)
    assert summary["decoupling_associations"] == 2
    root = tmp_path / "hierdemo.kicad_sch"
    assert (tmp_path / "power.kicad_sch").exists()
    assert (tmp_path / "load.kicad_sch").exists()
    assert _erc_total(root) == 0
    import schem_refdes as sr
    for name in ("hierdemo", "power", "load"):
        sch_p = tmp_path / f"{name}.kicad_sch"
        assert sr.audit_sheet(sr.Sheet(sch_p)) == [], name  # P4-1
    fresh = _export_net(root, tmp_path / "h.net")
    p = na.parse_netlist(fresh)
    members = {net: sorted((m["ref"], m["pin"]) for m in ms)
               for net, ms in p["nets"].items()}
    assert members["+3V3"] == [("C11", "1"), ("R3", "1"), ("U3", "2")]
    assert members["/CTL"] == [("D2", "1"), ("J2", "1")]
    assert members["/VIN"] == [("C10", "1"), ("J1", "1"), ("U3", "3")]
    assert members["/load/LED_K"] == [("D2", "2"), ("R3", "2")]
    assert members["GND"] == [("C10", "2"), ("C11", "2"), ("J1", "2"),
                              ("J2", "2"), ("U3", "1")]
    r = na.compare_netlists(p, na.parse_netlist(HIER_NET))
    assert r["identical"], r                        # committed net current


@pytest.mark.smoke
def test_smoke_audit_sch_export_path():
    payload, _ = na.run([
        "--sch", str(REGEN_SCH),
        "--constraints", str(GOLDEN / "blinky2" / "constraints.json")])
    assert payload["status"] == "pass" and payload["nets"] == 43


@pytest.mark.smoke
def test_smoke_schlib_pins_cli():
    cp = subprocess.run([PYTHON, str(SCRIPTS / "schlib.py"),
                         "--pins", "Device:C"],
                        capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    payload = json.loads(cp.stdout)
    assert [p["number"] for p in payload["pins"]] == ["1", "2"]
    cp = subprocess.run([PYTHON, str(SCRIPTS / "schlib.py"),
                         "--pins", "NoSuch:Symbol"],
                        capture_output=True, text=True, timeout=120)
    assert cp.returncode == 2
    assert json.loads(cp.stdout)["status"] == "error"  # stdout stays JSON


@pytest.mark.smoke
def test_smoke_rotated_parts_wire_outward_and_erc_clean(tmp_path):
    """V19 regression: ksa mirrors pin positions at 90/270 (schlib.pin_pos
    corrects it) - rotated 2-pin parts must get outward stubs on the TRUE
    pins.  ERC 0 proves the wires land on real pin positions."""
    sh = schlib.Sheet("rot")
    for i, rot in enumerate([0, 90, 180, 270]):
        ref = f"R{i + 1}"
        at = (127.0 + i * 25.4, 63.5)
        sh.add_component("Device:R", ref, "10k", at=at, rotation=rot)
        sh.wire_pins(ref, {"1": f"N{i + 1}", "2": f"N{i if i else 4}"})
        for pad in ("1", "2"):
            p = sh.pin_pos(ref, pad)
            assert abs(p[0] - at[0]) + abs(p[1] - at[1]) == pytest.approx(
                3.81), f"{ref}.{pad} not on the pin ring: {p}"
    # rot 90: pins swing onto the x axis - the pre-fix ksa answer put pin 1
    # at anchor+3.81 (mirrored); the ERC-measured truth is anchor-3.81
    assert sh.pin_pos("R2", "1") == (152.4 - 3.81, 63.5)
    assert sh.pin_pos("R4", "1") == (203.2 + 3.81, 63.5)
    sh.save(tmp_path)
    assert _erc_total(tmp_path / "rot.kicad_sch") == 0


@pytest.mark.smoke
def test_smoke_power_symbol_value_is_rail_net(tmp_path):
    """Ladder row 94: power_flag(net='+3V3_MCU', sym='power:+3V3') must
    export net '+3V3_MCU' - the symbol VALUE names the global net and WINS
    over a coincident label, so schlib now sets VALUE = net."""
    sh = schlib.Sheet("pwrval")
    sh.add_component("Device:R", "R1", "10k", at=(127.0, 63.5))
    sh.wire_pins("R1", {"1": "+3V3_MCU", "2": "GND"})
    sh.power_flag("+3V3_MCU", at=(101.6, 63.5), sym="power:+3V3", flag=True)
    sh.power_flag("GND", at=(101.6, 76.2), sym="power:GND", flag=False)
    sh.save(tmp_path)
    net = _export_net(tmp_path / "pwrval.kicad_sch", tmp_path / "p.net")
    parsed = na.parse_netlist(net)
    assert "+3V3_MCU" in parsed["nets"]
    assert "+3V3" not in parsed["nets"], sorted(parsed["nets"])
    members = {(m["ref"], m["pin"]) for m in parsed["nets"]["+3V3_MCU"]}
    assert ("R1", "1") in members
    # power_symbol_at_pin derives the VALUE from the wired net - and
    # refuses an unwired pin outright
    sh2 = schlib.Sheet("pwrval2")
    sh2.add_component("Device:R", "R1", "10k", at=(127.0, 63.5))
    sh2.wire_pins("R1", {"1": "+1V8_SNS", "2": "GND"})
    sh2.power_symbol_at_pin("R1", "1", "power:+1V8")
    with pytest.raises(ValueError, match="not wired yet"):
        sh2.power_symbol_at_pin("R1", "3", "power:+1V8")
    sh2.power_flag("GND", at=(101.6, 76.2), sym="power:GND", flag=False)
    sh2.save(tmp_path / "b")
    net2 = _export_net(tmp_path / "b" / "pwrval2.kicad_sch",
                       tmp_path / "b" / "p.net")
    parsed2 = na.parse_netlist(net2)
    assert "+1V8_SNS" in parsed2["nets"] and "+1V8" not in parsed2["nets"]


@pytest.mark.smoke
def test_smoke_label_on_foreign_wire_rejected(tmp_path):
    """Ladder row 39: a label anchor landing on another wire's RUN merges
    two nets silently - schlib must raise at generation time."""
    sh = schlib.Sheet("guard")
    # R1's pin-2 stub runs straight down from (127, 67.31) to (127, 69.85)
    sh.add_component("Device:R", "R1", "10k", at=(127.0, 63.5))
    sh.wire_pins("R1", {"1": "A", "2": "B"})
    # R2 placed so its pin-1 stub END (label anchor) hits that run mid-span
    sh.add_component("Device:R", "R2", "10k", at=(127.0, 74.93),
                     rotation=0)
    with pytest.raises(ValueError, match="would merge nets"):
        sh.wire_pin("R2", "1", "C")


@pytest.mark.smoke
def test_smoke_save_place_fields_flag(tmp_path):
    """P4-1: save() runs schem_refdes by default (clean fields, report
    recorded); place_fields=False leaves the raw ksa output untouched."""
    def build():
        sh = schlib.Sheet("pf")
        sh.add_component("Device:R", "R1", "10k", at=(127.0, 63.5))
        sh.wire_pins("R1", {"1": "A", "2": "B"})
        return sh
    a = build()
    a.save(tmp_path / "on")
    assert a.place_report is not None and "error" not in a.place_report
    assert a.place_report["moved"] > 0 and a.place_report["residue"] == []
    import schem_refdes as sr
    assert sr.audit_sheet(sr.Sheet(tmp_path / "on" / "pf.kicad_sch")) == []
    b = build()
    b.save(tmp_path / "off", place_fields=False)
    assert b.place_report is None
    on_text = (tmp_path / "on" / "pf.kicad_sch").read_text(encoding="utf-8")
    off_text = (tmp_path / "off" / "pf.kicad_sch").read_text(encoding="utf-8")
    assert on_text != off_text          # the placement pass really ran


@pytest.mark.smoke
def test_smoke_schlib_validation_errors():
    sh = schlib.Sheet("v")
    with pytest.raises(ValueError, match="off the 1.27"):
        sh.add_component("Device:R", "R1", "1k", at=(100.05, 60.96))
    with pytest.raises(ValueError, match="expected name"):
        sh.add_component("Device:R", "R2", "1k", at=(101.6, 60.96),
                         expect={"1": "NOPE"})
    with pytest.raises(ValueError, match="need a power symbol or PWR_FLAG"):
        sh.power_flag("X", at=(127.0, 63.5), sym=None, flag=False)
    with pytest.raises(ValueError, match="give ref/pad or at"):
        sh.hier_pin("X")
    with pytest.raises(ValueError, match="caps_at"):
        sh.place_ic_with_decoupling(
            "U8", "Device:R", "x", at=(127.0, 88.9), pins={"1": "A", "2": "B"},
            decoupling=[{"cap": "C1", "pin": "1", "rail": "A", "value": "1n"}])
    with pytest.raises(ValueError, match="not in pins"):
        sh.place_ic_with_decoupling(
            "U9", "Device:R", "x", at=(152.4, 88.9), pins={"1": "A", "2": "B"},
            decoupling=[{"cap": "C1", "pin": "9", "rail": "A", "value": "1n"}],
            caps_at=(127.0, 127.0))
    with pytest.raises(ValueError, match="decoupling entry says rail"):
        sh.place_ic_with_decoupling(
            "U10", "Device:R", "x", at=(177.8, 88.9), pins={"1": "A", "2": "B"},
            decoupling=[{"cap": "C1", "pin": "1", "rail": "B", "value": "1n"}],
            caps_at=(127.0, 127.0))
    proj = schlib.Project(schlib.Sheet("vroot"))
    child = schlib.Sheet("vchild")
    with pytest.raises(ValueError, match="no hier_pin"):
        proj.add_sheet(child, at=(127.0, 63.5), size=(25.4, 12.7),
                       nets=["MISSING"])
