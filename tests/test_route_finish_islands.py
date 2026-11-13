"""#939: route_finish's board rip-up must not leave a ripped net in pieces.

Ripping net B's track to free a path for A joins A, and B comes back
unconnected for the next pass to route again. When no path brings B back,
the board-wide unconnected count can still drop (A had more open items than
B gained), and the pass was kept with B cut in two. The island guard
compares each ripped net's DRC unconnected count with its count before the
rip and sends the run back to the board before its first rip when one went
up. These tests script the passes, so no KiCad is needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import route_finish as rf  # noqa: E402

ITEMS = [{"pos": [0, 0]}, {"pos": [1, 1]}]


def _drc(**open_by_net):
    return {"violations": [{"source": "unconnected", "net": n, "items": ITEMS,
                            "severity": "error"}
                           for n, k in open_by_net.items() for _ in range(k)]}


def _script(monkeypatch, passes):
    """passes: [(board text after, jobs, drc after)]; each fake pass writes
    its text to the staged board, as route_edit would."""
    it = iter(passes)

    def one_pass(cli, staged, cur, opts, **kw):
        text, jobs, after = next(it)
        staged.write_text(text)
        return jobs, after, None, ["F.Cu"]
    monkeypatch.setattr(rf, "_one_pass", one_pass)
    monkeypatch.setattr(rf, "_rules_facts", lambda r: {})
    monkeypatch.setattr(rf, "_sweep_dangling",
                        lambda cli, staged, nets, cur, **kw: cur)


def _rip_job(net, ripped):
    return {"net": net, "status": "routed", "rip_uuids": ["u1"],
            "ripped_nets": ripped}


def test_a_ripped_net_left_split_is_undone(tmp_path, monkeypatch):
    staged = tmp_path / "b.kicad_pcb"
    staged.write_text("start")
    # A had 3 open items; ripping B joins all of A, B ends in 2 pieces and
    # the next pass finds no way back for it
    _script(monkeypatch, [
        ("ripped", [_rip_job("A", ["B"])], _drc(B=1)),
        ("ripped+retry", [{"net": "B", "status": "failed"}], _drc(B=1)),
    ])
    facts = rf.finish_staged(Path("kicad-cli"), staged, _drc(A=3),
                             passes=3)
    assert facts["rip_split_undone"] == {"B": [0, 1]}
    assert staged.read_text() == "start"
    assert facts["kept"] is False
    assert facts["unconnected_after"] == 3
    assert not list(tmp_path.glob("preripnets_*"))


def test_a_ripped_net_routed_back_is_kept(tmp_path, monkeypatch):
    staged = tmp_path / "b.kicad_pcb"
    staged.write_text("start")
    _script(monkeypatch, [
        ("ripped", [_rip_job("A", ["B"])], _drc(B=1)),
        ("B back", [{"net": "B", "status": "routed"}], _drc()),
    ])
    facts = rf.finish_staged(Path("kicad-cli"), staged, _drc(A=3),
                             passes=3)
    assert "rip_split_undone" not in facts
    assert staged.read_text() == "B back" and facts["kept"] is True


def test_earlier_passes_survive_the_undo(tmp_path, monkeypatch):
    """Only the copper from the first rip on goes: a plain pass before it
    stays."""
    staged = tmp_path / "b.kicad_pcb"
    staged.write_text("start")
    _script(monkeypatch, [
        ("C routed", [{"net": "C", "status": "routed"}], _drc(A=3)),
        ("ripped", [_rip_job("A", ["B"])], _drc(B=1)),
        ("ripped+retry", [{"net": "B", "status": "failed"}], _drc(B=1)),
    ])
    facts = rf.finish_staged(Path("kicad-cli"), staged, _drc(A=3, C=1),
                             passes=4)
    assert facts["rip_split_undone"] == {"B": [0, 1]}
    assert staged.read_text() == "C routed" and facts["kept"] is True
    assert facts["unconnected_after"] == 3


def test_a_net_already_open_before_its_rip_may_stay_open(tmp_path,
                                                         monkeypatch):
    staged = tmp_path / "b.kicad_pcb"
    staged.write_text("start")
    _script(monkeypatch, [
        ("ripped", [_rip_job("A", ["B"])], _drc(B=1)),
    ])
    facts = rf.finish_staged(Path("kicad-cli"), staged, _drc(A=3, B=1),
                             passes=1)
    assert "rip_split_undone" not in facts and staged.read_text() == "ripped"


def test_at_the_drc_cap_the_split_is_reported_not_undone(tmp_path,
                                                         monkeypatch):
    staged = tmp_path / "b.kicad_pcb"
    staged.write_text("start")
    cap = rf.DRC_UNCONNECTED_CAP
    _script(monkeypatch, [
        ("ripped", [_rip_job("A", ["B"])], _drc(B=1, Z=cap - 1)),
    ])
    facts = rf.finish_staged(Path("kicad-cli"), staged,
                             _drc(A=3, Z=cap), passes=1)
    assert facts["rip_split_capped"] == {"B": [0, 1]}
    assert staged.read_text() == "ripped"
