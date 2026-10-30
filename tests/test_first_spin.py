"""check_observability.py (what a board can read of itself) and
first_spin.py (is every first-spin finding answered).

A synthetic board: an MCU reading VBUS through a divider and +3V3 directly
through a series resistor, a buck whose own FB divider must not count as
reading +5V, a test point on +5V, a blind +12V, an amp whose FAULT reaches
a GPIO and whose OTW goes to a pull-up only, and a CH340 console. Then
first_spin.py over two domain reviews in the hifi-receiver-2 shape.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import check_observability as co  # noqa: E402
import first_spin  # noqa: E402

MCU_PINS = {"1": ("+3V3", "VDD"), "2": ("GND", "GND"),
            "3": ("/VBUS_SENSE", "IO34"), "4": ("/V33_SENSE", "IO35"),
            "5": ("/AMP_FAULT", "IO4"), "6": ("/D0", "IO5"), "7": ("/D1", "IO12"),
            "8": ("/D2", "IO13"), "9": ("/D3", "IO14"), "10": ("/D4", "IO15")}
BOARD = [
    ("U1", "ESP32-WROVER-E", "Module", MCU_PINS),
    ("R1", "100k", "R_0402", {"1": ("VBUS", ""), "2": ("/VBUS_SENSE", "")}),
    ("R2", "10k", "R_0402", {"1": ("/VBUS_SENSE", ""), "2": ("GND", "")}),
    ("R3", "10k", "R_0402", {"1": ("+3V3", ""), "2": ("/V33_SENSE", "")}),
    ("R7", "10k", "R_0402", {"1": ("/V33_SENSE", ""), "2": ("GND", "")}),
    ("R8", "10k", "R_0402", {"1": ("+3V3", ""), "2": ("/D0", "")}),
    ("U2", "TPS54302", "SOT-23-6", {"1": ("GND", "GND"), "3": ("VBUS", "VIN"),
                                    "4": ("/FB5", "FB")}),
    ("R4", "100k", "R_0402", {"1": ("+5V", ""), "2": ("/FB5", "")}),
    ("R5", "15k", "R_0402", {"1": ("/FB5", ""), "2": ("GND", "")}),
    ("TP1", "TP", "TestPoint_Pad_D1.0mm", {"1": ("+5V", "1")}),
    ("C1", "10uF", "C_0805", {"1": ("+12V", ""), "2": ("GND", "")}),
    ("U3", "TPA3118", "HTSSOP-32", {"1": ("+12V", "PVCC"), "2": ("GND", "GND"),
                                    "3": ("/AMP_FAULT", "~{FAULT}"),
                                    "4": ("/AMP_OTW", "OTW")}),
    ("R6", "10k", "R_0402", {"1": ("+3V3", ""), "2": ("/AMP_OTW", "")}),
    ("U4", "CH340C", "SOIC-16", {"1": ("+3V3", "VCC"), "2": ("GND", "GND"),
                                 "3": ("/TXD", "TXD")}),
]


def write_net(path: Path, board=BOARD) -> Path:
    comps, nets = [], {}
    for ref, value, fp, pins in board:
        comps.append(f'(comp (ref "{ref}") (value "{value}") '
                     f'(footprint "{fp}"))')
        for pin, (net, fn) in pins.items():
            nets.setdefault(net, []).append(
                f'(node (ref "{ref}") (pin "{pin}") (pinfunction "{fn}") '
                f'(pintype "passive"))')
    body = "\n".join(f'(net (code "{i}") (name "{n}") {" ".join(nd)})'
                     for i, (n, nd) in enumerate(sorted(nets.items()), 1))
    path.write_text(f'(export (version "E") (components {" ".join(comps)}) '
                    f'(nets {body}))\n', encoding="utf-8")
    return path


def run_obs(tmp_path, board=BOARD, *extra):
    net = write_net(tmp_path / "b.net", board)
    out = tmp_path / "obs.json"
    code = co.main(["--net", str(net), "--rail", "VBUS=20", "--out", str(out),
                    *extra])
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_rails_status_and_console(tmp_path):
    code, rep = run_obs(tmp_path)
    assert code == 1                       # +12V blind, OTW unwatched
    rails = {r["net"]: r for r in rep["rails"]}
    assert rails["VBUS"]["status"] == "self"
    assert rails["VBUS"]["paths"][0]["via"] == ["R1"]
    assert rails["VBUS"]["paths"][0]["pin"] == "U1.3(IO34)"
    assert rails["+3V3"]["status"] == "self"
    # the buck's FB divider regulates +5V, it does not report it
    assert rails["+5V"]["status"] == "test_point"
    assert rails["+5V"]["test_points"] == ["TP1"]
    assert rails["+12V"]["status"] == "blind"
    st = {s["fn"]: s for s in rep["status_pins"]}
    assert st["FAULT"]["status"] == "watched"
    assert st["FAULT"]["watchers"] == ["U1.5(IO4)"]
    assert st["OTW"]["status"] == "unwatched"
    assert rep["mcus"] == ["U1"]
    assert rep["console"]["usb_uart"] == ["U4"]
    kinds = sorted(v["check"] for v in rep["violations"])
    assert kinds == ["rail_blind", "status_unwatched"]
    assert rep["summary"]["rails"] == {"self": 2, "test_point": 1, "blind": 1}


def test_a_pull_up_is_not_a_reading(tmp_path):
    """R8 pulls IO5 up to +3V3: without the R7 divider bottom, +3V3 is not
    read by anything (a pull-up reads logic, not the rail)."""
    _, rep = run_obs(tmp_path, [b for b in BOARD if b[0] != "R7"])
    r33 = next(r for r in rep["rails"] if r["net"] == "+3V3")
    assert r33["status"] == "blind", r33["paths"]


def test_an_expander_watches_status_but_reads_no_rail(tmp_path):
    exp = ("U6", "TCA9539PWR", "TSSOP-24",
           {**{str(i): (f"/E{i}", f"P{i:02d}") for i in range(10, 18)},
            "1": ("/AMP_OTW", "P00"), "2": ("/VBUS_SENSE", "P01")})
    board = [b for b in BOARD if b[0] not in ("U1", "U4")] + [exp]
    _, rep = run_obs(tmp_path, board)
    assert rep["mcus"] == [] and rep["expanders"] == ["U6"]
    st = {s["fn"]: s for s in rep["status_pins"]}
    assert st["OTW"]["status"] == "watched"
    vbus = next(r for r in rep["rails"] if r["net"] == "VBUS")
    assert vbus["status"] == "blind"


def test_no_console_and_no_mcu(tmp_path):
    no_bridge = [b for b in BOARD if b[0] != "U4"]
    _, rep = run_obs(tmp_path, no_bridge)
    assert "no_console" in {v["check"] for v in rep["violations"]}
    no_mcu = [b for b in BOARD if b[0] not in ("U1", "U4")]
    _, rep = run_obs(tmp_path, no_mcu)
    assert rep["mcus"] == [] and rep["summary"]["has_mcu"] is False
    assert "no_console" not in {v["check"] for v in rep["violations"]}
    msg = next(v["msg"] for v in rep["violations"] if v["net"] == "VBUS")
    assert "no MCU" in msg


def test_monitor_ic_counts(tmp_path):
    board = BOARD + [("U5", "INA186A1", "SOT-23-6", {
        "1": ("+12V", "IN+"), "2": ("/AMP12", "IN-"), "3": ("+3V3", "VS"),
        "4": ("GND", "GND")})]
    _, rep = run_obs(tmp_path, board)
    r12 = next(r for r in rep["rails"] if r["net"] == "+12V")
    assert r12["status"] == "self" and r12["paths"][0]["pin"] == "U5.1(IN+)"


# ---------------------------------------------------------------- first_spin

POWER = {"domain": "power",
         "findings": [
             {"id": "POWER-1", "board": "core", "severity": "blocker",
              "finding": "5 A in 0.255 mm tracks", "fix": "patches + vias"},
             {"id": "POWER-2", "board": "core", "severity": "major",
              "finding": "inrush past SOA"},
             {"id": "POWER-3", "board": "core", "severity": "minor",
              "finding": "TVS clamp | above abs-max"}],
         "known_unknowns": [
             {"fact": "CH224 abs max 32 V", "status": "verified",
              "could_stop_board": True},
             {"fact": "CH224 VBUS pin tolerant to 32 V", "status": "unverified",
              "could_stop_board": True},
             {"fact": "fallback PDO", "status": "unverified",
              "could_stop_board": False}],
         "tools_wanted": [{"tool": "soa_check", "checks": "Vds*Id vs SOA"}]}
THERMAL = {"domain": "thermal",
           "findings": [{"id": "THERMAL-1", "board": "core",
                         "severity": "blocker", "finding": "pad-up, no heatsink"}],
           "known_unknowns": [{"fact": "theta 8 C/W", "status": "wrong",
                               "could_stop_board": True}],
           "tools_wanted": [{"tool": "soa_check", "checks": "same"},
                            {"tool": "top-slug mode", "checks": "heatsink path"}]}


def review_dir(tmp_path, disp=None, res=None) -> Path:
    d = tmp_path / "ws" / "reports" / "first-spin"
    d.mkdir(parents=True)
    (d / "power.json").write_text(json.dumps(POWER), encoding="utf-8")
    (d / "thermal.json").write_text(json.dumps(THERMAL), encoding="utf-8")
    (d / "eth_clk.report.json").write_text("{}", encoding="utf-8")  # not a review
    if disp is not None:
        (d / "dispositions.json").write_text(json.dumps(disp), encoding="utf-8")
    if res is not None:
        (d / "resolutions.json").write_text(json.dumps(res), encoding="utf-8")
    return d


def fs(tmp_path, *extra):
    out = tmp_path / "fs.json"
    code = first_spin.main(["--workspace", str(tmp_path / "ws"), "--out",
                            str(out), *extra])
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_first_spin_open_items(tmp_path):
    review_dir(tmp_path, disp={"POWER-1": ["fixed", "8 vias, 9.6 A vs 5 A"],
                               "POWER-2": {"disposition": "fixed", "how": ""},
                               "GONE-1": ["fixed", "x"]})
    code, rep = fs(tmp_path)
    assert code == 1 and rep["status"] == "open"
    got = [(o["kind"], o["id"]) for o in rep["open"]]
    assert ("undisposed", "THERMAL-1") in got
    assert ("no_how", "POWER-2") in got
    assert ("unknown_open", "power:1") in got
    assert ("unknown_open", "thermal:0") in got
    assert ("dangling", "GONE-1") in got
    assert all(o["id"] != "POWER-3" for o in rep["open"])   # minor may wait
    assert got[0] == ("undisposed", "THERMAL-1")             # worst first
    assert rep["counts"]["power"] == {"blocker": 1, "major": 1, "minor": 1,
                                      "note": 0}
    tools = {t["tool"]: t["domains"] for t in rep["tools_wanted"]}
    assert tools == {"soa_check": ["power", "thermal"],
                     "top-slug mode": ["thermal"]}


def test_first_spin_all_answered_writes_the_table(tmp_path):
    review_dir(tmp_path,
               disp={"POWER-1": ["fixed", "8 vias"],
                     "POWER-2": ["contingency", "DNP C; bring-up reads inrush"],
                     "THERMAL-1": ["fixed", "lid heatsink, Tj 101-108 C"]},
               res={"power:1": {"status": "verified", "how": "V2.0 t.8.1.1"},
                    "thermal:0": {"status": "resolved", "how": "datasheet 44.8"}})
    md = tmp_path / "d.md"
    code, rep = fs(tmp_path, "--md", str(md))
    assert code == 0 and rep["open"] == [], rep["open"]
    text = md.read_text(encoding="utf-8")
    assert "| POWER-1 | core | blocker |" in text
    assert "TVS clamp / above abs-max" in text      # pipes escaped
    assert "## Tools wanted" in text


def test_first_spin_bad_disposition_and_errors(tmp_path):
    review_dir(tmp_path, disp={"POWER-1": ["ignored", "meh"]})
    code, rep = fs(tmp_path)
    assert ("bad_disposition", "POWER-1") in [(o["kind"], o["id"])
                                              for o in rep["open"]]
    assert first_spin.main(["--dir", str(tmp_path / "nope")]) == 2
