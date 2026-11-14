"""verify_all hands the netlist checks a netlist (#885).

minuet v3 ships a schematic and no .net, so check_straps said no_netlist and
missed the published GPIO6 reset pull-up. verify_all now exports the board's
own <stem>.kicad_sch once into the reports dir and passes it as --netlist.
kicad-cli is mocked here; the end-to-end export runs in the score.
"""
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import checklib  # noqa: E402
import kc  # noqa: E402
import verify_all  # noqa: E402
from lib import env  # noqa: E402

from test_antenna_straps import _netlist  # noqa: E402

NETLIST_CHECKS = ("check_straps", "check_usb_phy", "check_reset_drive",
                  "check_cap_load", "check_transformer")


def _c3_board(tmp_path: Path) -> tuple[Path, Path]:
    """A schematic-only board: s.kicad_pcb + s.kicad_sch, and (aside, where
    the checks can't find it) the netlist the mocked export will write: an
    ESP32-C3 whose GPIO6 (pulled up by the chip from reset) reaches J4.5."""
    src = tmp_path / "src"
    src.mkdir()
    _netlist(src, {"U4": "ESP32-C3-WROOM-02-N4", "R9": "10k",
                   "J4": "Conn_02x08"}, {
        "+3.3V": [("U4", "1", "3V3_1"), ("R9", "2", "")],
        "GND": [("U4", "19", "GND_19")],
        "BOOT": [("U4", "8", "GPIO9_8"), ("R9", "1", "")],
        "GPIO6": [("U4", "5", "GPIO6_5"), ("J4", "5", "Pin_5_5")],
    })
    board = tmp_path / "board"
    board.mkdir()
    pcb = board / "s.kicad_pcb"
    shutil.copy(src / "s.kicad_pcb", pcb)
    (board / "s.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    return pcb, src / "s.net"


def _mock_export(monkeypatch, net: Path | None, calls: list | None = None):
    monkeypatch.setattr(env, "find_kicad_cli", lambda: Path("/x/kicad-cli"))

    def export(cli, sch, out, fmt="kicadsexpr"):
        if calls is not None:
            calls.append((cli, sch, out))
        if net is None:
            return {"status": "error", "error": "kicad-cli exploded"}
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(net, out)
        return {"status": "pass"}
    monkeypatch.setattr(kc, "export_netlist", export)


def test_exports_the_schematic_when_no_net(tmp_path, monkeypatch):
    pcb, net = _c3_board(tmp_path)
    calls = []
    _mock_export(monkeypatch, net, calls)
    rep = tmp_path / "rep"
    got, note = verify_all.board_netlist(pcb, rep)
    assert got == rep / "netlist" / "s.net" and got.is_file()
    assert note == {"source": "exported", "from": "s.kicad_sch",
                    "path": str(got)}
    assert calls[0][1] == pcb.with_suffix(".kicad_sch")


def test_a_net_beside_the_board_is_left_to_the_checks(tmp_path, monkeypatch):
    pcb, net = _c3_board(tmp_path)
    shutil.copy(net, pcb.with_suffix(".net"))
    calls = []
    _mock_export(monkeypatch, net, calls)
    assert verify_all.board_netlist(pcb, tmp_path / "rep") == (None, None)
    assert calls == []


def test_a_given_netlist_wins(tmp_path, monkeypatch):
    pcb, net = _c3_board(tmp_path)
    calls = []
    _mock_export(monkeypatch, net, calls)
    got, note = verify_all.board_netlist(pcb, tmp_path / "rep", str(net))
    assert got == net and note["source"] == "given" and calls == []
    with pytest.raises(checklib.CheckError):
        verify_all.board_netlist(pcb, tmp_path / "rep", str(tmp_path / "no"))


def test_no_schematic_says_why(tmp_path, monkeypatch):
    pcb, net = _c3_board(tmp_path)
    pcb.with_suffix(".kicad_sch").unlink()
    _mock_export(monkeypatch, net)
    got, note = verify_all.board_netlist(pcb, tmp_path / "rep")
    assert got is None and note["source"] is None
    assert "s.kicad_sch" in note["reason"]


def test_no_kicad_cli_says_why(tmp_path, monkeypatch):
    pcb, _ = _c3_board(tmp_path)
    monkeypatch.setattr(env, "find_kicad_cli", lambda: None)
    got, note = verify_all.board_netlist(pcb, tmp_path / "rep")
    assert got is None and "no kicad-cli" in note["reason"]

    def bad_pin():
        raise env.EnvError("HWDE_KICAD_CLI does not exist: /nope")
    monkeypatch.setattr(env, "find_kicad_cli", bad_pin)
    got, note = verify_all.board_netlist(pcb, tmp_path / "rep")
    assert got is None and "/nope" in note["reason"]


def test_a_failed_export_says_why(tmp_path, monkeypatch):
    pcb, _ = _c3_board(tmp_path)
    _mock_export(monkeypatch, None)
    got, note = verify_all.board_netlist(pcb, tmp_path / "rep")
    assert got is None and "kicad-cli exploded" in note["reason"]


def test_the_netlist_checks_take_the_input():
    by = {c["name"]: c for c in verify_all.CHECKS}
    assert "netlist" in verify_all.INPUT_KEYS
    for name in NETLIST_CHECKS:
        args = by[name]["args"]({"pcb": "b.kicad_pcb", "netlist": "x.net"})
        assert args[-2:] == ["--netlist", "x.net"], name
        assert "--netlist" not in by[name]["args"]({"pcb": "b.kicad_pcb"})


def test_planted_gpio6_pull_found_on_a_schematic_only_board(tmp_path,
                                                            monkeypatch):
    """Planted fault: minuet's GPIO6-to-header erratum on a board with only
    a schematic. Before #885 check_straps saw no netlist and passed it."""
    pcb, net = _c3_board(tmp_path)
    _mock_export(monkeypatch, net)
    rep = tmp_path / "rep"
    rep.mkdir()
    got, _ = verify_all.board_netlist(pcb, rep)
    straps = next(c for c in verify_all.CHECKS if c["name"] == "check_straps")
    res = verify_all.run_one(straps, {"pcb": str(pcb), "netlist": str(got)},
                             rep)
    kinds = [v.get("kind") for v in res["violations"]]
    assert "reset_pull_offboard" in kinds, res
    # the same board without the export is blind to it
    res = verify_all.run_one(straps, {"pcb": str(pcb), "netlist": None}, rep)
    assert "reset_pull_offboard" not in [v.get("kind")
                                         for v in res["violations"]]
