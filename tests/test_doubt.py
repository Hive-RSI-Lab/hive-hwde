"""doubt.py and the doubt library (reference/doubt.md): a board's own
questions, each answered by a check, re-run and kept in a ledger."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import doubt  # noqa: E402
import doubtlib  # noqa: E402


def netlist(comps: dict, nets: dict) -> str:
    """comps {ref: (value, footprint)}, nets {name: [(ref, pin, name)]}."""
    out = ['(export (version "E")', "(components"]
    for ref, (value, fp) in comps.items():
        out.append(f'(comp (ref "{ref}") (value "{value}") (footprint "{fp}"))')
    out.append(")")
    out.append("(nets")
    for i, (name, nodes) in enumerate(nets.items(), 1):
        out.append(f'(net (code "{i}") (name "{name}")')
        for ref, pin, fn in nodes:
            out.append(f'(node (ref "{ref}") (pin "{pin}") '
                       f'(pinfunction "{fn}_{pin}") (pintype "passive"))')
        out.append(")")
    out.append("))")
    return "\n".join(out)


def workspace(tmp_path, comps, nets, name="b"):
    ws = tmp_path / name
    (ws / "kicad").mkdir(parents=True)
    (ws / "kicad" / f"{name}.net").write_text(netlist(comps, nets))
    return ws


def lib_answer(name, ws, rails=None):
    board = doubtlib.Board.from_workspace(ws, rails)
    return doubtlib.run_check(doubtlib.library()[name], board), board


# A 3.3 V MCU driving a 5 V analog switch's select, as hifi-receiver-2's
# core first had it (review-2), and a 5 V part's output back into the MCU.
SWITCH = {"U1": ("ESP32-WROVER-E", "m"), "U2": ("TS5A23157", "s"),
          "U3": ("PARTX", "x")}
SWITCH_NETS = {
    "+3V3": [("U1", "1", "3V3")],
    "+5V": [("U2", "1", "V+"), ("U3", "1", "VCC")],
    "GND": [("U1", "2", "GND"), ("U2", "2", "GND"), ("U3", "2", "GND")],
    "/SEL": [("U1", "3", "IO5"), ("U2", "3", "IN1")],
    "/BACK": [("U1", "4", "IO6"), ("U3", "3", "OUT")],
}


def test_cross_rail_select_below_cmos_vih_is_an_open_question(tmp_path):
    ws = workspace(tmp_path, SWITCH, SWITCH_NETS)
    ans, _ = lib_answer("cross_rail_logic", ws)
    assert ans["verdict"] == "unknown"
    text = " ".join(ans["unknowns"])
    assert "U2 (TS5A23157) IN1 on 5.0 V is driven to 3.3 V" in text
    assert "U1 (ESP32-WROVER-E) IO5 on 3.3 V can see 5.0 V" in text


def test_cross_rail_cited_row_fails_or_passes(tmp_path, monkeypatch):
    ws = workspace(tmp_path, SWITCH, {k: v for k, v in SWITCH_NETS.items()
                                      if k != "/BACK"})
    mod = doubtlib.load_check(doubtlib.library()["cross_rail_logic"])
    table = tmp_path / "levels.yaml"
    table.write_text("parts:\n- match: TS5A23157\n  vih: 0.7*V\n"
                     "  vin_max: 5.5\n  inputs: [IN1]\n  source: test\n"
                     "- match: ESP32\n  outputs: [IO5]\n  source: test\n")
    monkeypatch.setattr(mod, "TABLE", table)
    ans = mod.answer(doubtlib.Board.from_workspace(ws))
    assert ans["verdict"] == "fail"
    assert "VIH 3.50 V (test)" in ans["findings"][0]["msg"]
    table.write_text("parts:\n- match: TS5A23157\n  vih: 2.4\n  vin_max: 5.5\n"
                     "  inputs: [IN1]\n  source: test\n"
                     "- match: ESP32\n  outputs: [IO5]\n  source: test\n")
    ans = mod.answer(doubtlib.Board.from_workspace(ws))
    assert ans["verdict"] == "pass", ans
    assert any("VIH 2.40 V (cited)" in e for e in ans["evidence"])


def test_reversed_polarised_cap_fails(tmp_path):
    comps = {"C1": ("100uF", "Capacitor_THT:CP_Radial_D8.0mm"),
             "C2": ("100uF", "Capacitor_THT:CP_Radial_D8.0mm")}
    nets = {"+12V": [("C1", "1", "+"), ("C2", "2", "-")],
            "GND": [("C1", "2", "-"), ("C2", "1", "+")]}
    ans, _ = lib_answer("polarized_caps", workspace(tmp_path, comps, nets))
    assert ans["verdict"] == "fail"
    assert [f["refs"] for f in ans["findings"]] == [["C2"]]


def test_polarised_cap_on_unnamed_rail_asks_for_its_voltage(tmp_path):
    comps = {"C1": ("470uF", "aiee:CAP-SMD_BD10.0-L10.3")}
    nets = {"PVDD": [("C1", "1", "+")], "GND": [("C1", "2", "-")]}
    ws = workspace(tmp_path, comps, nets)
    ans, _ = lib_answer("polarized_caps", ws)
    assert ans["verdict"] == "unknown" and "PVDD" in ans["unknowns"][0]
    ans, _ = lib_answer("polarized_caps", ws, rails={"PVDD": 20})
    assert ans["verdict"] == "pass"


def test_untied_ground_fails_and_a_zero_ohm_tie_passes(tmp_path):
    comps = {"U1": ("X", "x"), "U2": ("Y", "y"), "R1": ("0R", "r")}
    nets = {"GND": [("U1", "1", "GND"), ("U2", "1", "GND")],
            "AGND": [("U1", "2", "AGND"), ("U2", "2", "AGND")]}
    ans, _ = lib_answer("ground_ties", workspace(tmp_path, comps, nets))
    assert ans["verdict"] == "fail" and "AGND" in ans["findings"][0]["msg"]
    nets["GND"].append(("R1", "1", "1"))
    nets["AGND"].append(("R1", "2", "2"))
    ans, _ = lib_answer("ground_ties", workspace(tmp_path, comps, nets, "c"))
    assert ans["verdict"] == "pass" and "ground ties: R1" in ans["evidence"]


def test_open_enable_is_an_open_question(tmp_path):
    comps = {"U1": ("TPS54302", "x")}
    nets = {"VIN": [("U1", "1", "VIN")],
            "unconnected-(U1-EN-Pad5)": [("U1", "5", "EN")]}
    ans, _ = lib_answer("floating_controls", workspace(tmp_path, comps, nets))
    assert ans["verdict"] == "unknown"
    assert "U1 (TPS54302) pin 5 EN is open" in ans["unknowns"][0]


def run(*argv):
    payload, _ = doubt.run(list(argv) + ["--json"])
    return payload


def test_ledger_ask_run_dispose_stale(tmp_path):
    ws = workspace(tmp_path, SWITCH, SWITCH_NETS)
    w = ["--workspace", str(ws)]
    asked = run("ask", *w)
    assert "cross_rail_logic" in asked["added"]
    assert "ground_ties" in asked["not_applicable"]
    assert run("ask", *w)["added"] == []          # asked once
    out = run("run", *w)
    assert out["ran"]["lib.cross_rail_logic"] == "unknown"
    assert "lib.cross_rail_logic" in out["open"]
    with pytest.raises(doubt.checklib.CheckError, match="--how"):
        run("dispose", *w, "--id", "lib.cross_rail_logic",
            "--kind", "justified")
    run("dispose", *w, "--id", "lib.cross_rail_logic", "--kind",
        "contingency", "--how", "DNP level shifter footprint on SEL")
    st = run("status", *w)
    assert {r["id"]: r["state"] for r in st["states"]}[
        "lib.cross_rail_logic"] == "disposed"
    # the board changes: the answer is stale until re-run, and the re-run
    # answer opens the question again
    net = ws / "kicad" / "b.net"
    net.write_text(net.read_text().replace("TS5A23157", "TS5A23159"))
    st = run("status", *w)
    assert {r["id"]: r["state"] for r in st["states"]}[
        "lib.cross_rail_logic"] == "open-stale"
    run("run", *w)
    st = run("status", *w)
    assert {r["id"]: r["state"] for r in st["states"]}[
        "lib.cross_rail_logic"] == "open-unknown"
    md = (ws / "doubts" / "doubts.md").read_text()
    assert "| lib.cross_rail_logic | digital |" in md


def test_own_question_skeleton_error_and_promote(tmp_path, monkeypatch):
    ws = workspace(tmp_path, SWITCH, SWITCH_NETS)
    w = ["--workspace", str(ws)]
    with pytest.raises(doubt.checklib.CheckError, match="--why"):
        run("add", *w, "--id", "q1", "--question", "Q?", "--domain", "power")
    run("add", *w, "--id", "q1", "--question", "Is +5V's bulk enough?",
        "--why", "the switch browns out", "--domain", "power")
    check = ws / "doubts" / "checks" / "q1.py"
    assert "QUESTION = \"Is +5V's bulk enough?\"" in check.read_text()
    assert run("run", *w)["ran"]["q1"] == "unknown"   # skeleton: not written
    check.write_text("QUESTION='q'\ndef answer(board):\n    return 1/0\n")
    assert run("run", *w)["ran"]["q1"] == "error"
    with pytest.raises(doubt.checklib.CheckError, match="broken check"):
        run("dispose", *w, "--id", "q1", "--kind", "accepted", "--how", "x")
    check.write_text("import doubtlib\nQUESTION='q'\n"
                     "def answer(board):\n    return doubtlib.passed([])\n")
    assert run("run", *w)["ran"]["q1"] == "error"     # a pass needs evidence
    check.write_text("import doubtlib\nQUESTION='q'\ndef answer(board):\n"
                     "    return doubtlib.passed(['+5V has 3 pins'], '3x')\n")
    assert run("run", *w)["ran"]["q1"] == "pass"
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setattr(doubtlib, "library_dir", lambda: lib)
    with pytest.raises(doubt.checklib.CheckError, match="applies"):
        run("promote", *w, "--id", "q1", "--name", "bulk_enough")
    check.write_text(check.read_text() + "def applies(board):\n    return True\n")
    out = run("promote", *w, "--id", "q1", "--name", "bulk_enough")
    assert (lib / "bulk_enough.py").is_file() and not check.exists()
    led = json.loads((ws / "doubts" / "ledger.json").read_text())
    assert led["questions"][0]["check"] == "lib:bulk_enough"
    assert out["status"] == "pass"


def test_fact_rail_feeds_every_check(tmp_path):
    comps = {"C1": ("470uF", "CP_Elec_8x10")}
    nets = {"PVDD": [("C1", "1", "+")], "GND": [("C1", "2", "-")]}
    ws = workspace(tmp_path, comps, nets)
    w = ["--workspace", str(ws)]
    run("ask", *w)
    assert run("run", *w)["ran"]["lib.polarized_caps"] == "unknown"
    run("fact", *w, "--rail", "PVDD=21", "--source", "PD 20 V + 5 %")
    assert run("run", *w)["ran"]["lib.polarized_caps"] == "pass"
    assert "PVDD = 21.0 V (PD 20 V + 5 %)" in \
        (ws / "doubts" / "doubts.md").read_text()


def test_library_checks_keep_the_contract():
    for name, path in doubtlib.library().items():
        mod = doubtlib.load_check(path)
        assert mod.QUESTION.endswith("?"), name
        assert mod.DOMAIN in doubtlib.DOMAINS, name
        assert getattr(mod, "WHY", ""), name
        assert callable(getattr(mod, "applies", None)), name
