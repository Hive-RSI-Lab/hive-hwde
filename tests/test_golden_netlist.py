"""The golden netlist fwe's copied parser is tested against
(tests/golden/netlist/README.md). hwde's side of the contract: its reader
still makes the committed JSON of the committed export, and a fresh export of
the golden schematic still reads the same, so the fixture never drifts from
what hwde actually writes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import env  # noqa: E402
import kc  # noqa: E402
import simlib  # noqa: E402

GOLD = REPO / "tests" / "golden" / "netlist"
SCH = REPO / "tests" / "golden" / "usbbuck4" / "usbbuck4.kicad_sch"


def _expected() -> dict:
    return json.loads((GOLD / "usbbuck4.parsed.json").read_text(encoding="utf-8"))


def test_reader_makes_the_committed_json():
    assert simlib.parse_netlist(GOLD / "usbbuck4.net") == _expected()


def test_fixture_covers_what_a_parser_must_handle():
    exp = _expected()
    nodes = [n for ns in exp["nets"].values() for n in ns]
    assert len(exp["components"]) >= 20
    assert {"/USB_DP", "/USB_DM", "GND", "+3V3", "VBUS"} <= set(exp["nets"])
    assert any(n.startswith("unconnected-(") for n in exp["nets"])
    assert any(n["pinfunction"] for n in nodes)
    assert not any("/home/" in line or ":\\" in line for line in
                   (GOLD / "usbbuck4.net").read_text(encoding="utf-8")
                   .splitlines() if "(source" in line)


@pytest.mark.smoke
def test_fresh_export_reads_the_same(tmp_path):
    cli = env.find_kicad_cli()
    if cli is None:
        pytest.skip("kicad-cli not installed")
    out = tmp_path / "usbbuck4.net"
    res = kc.export_netlist(cli, SCH, out)
    assert res["status"] == "pass", res
    assert simlib.parse_netlist(out) == _expected()
