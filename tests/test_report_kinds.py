"""report_gen.py --kind highlight|full and lib/dochistory.py.

The history helpers run on hand-built state and a throwaway git repo; the two
new document kinds build as --tex-only on test_report's synthetic workspace.
The flow figure renders for real only when the diagram-maker skill and node
are on the machine.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_report import make_workspace, run_main, sections_by_name

import report_gen  # noqa: E402  (on sys.path through test_report)
from lib import dochistory  # noqa: E402


def dec(phase, ts, what, why="because"):
    return {"phase": phase, "ts": f"2026-09-27T{ts}:00", "what": what, "why": why}


RUN = [
    dec("P0", "00:10", "Q1 pads"),
    dec("P1", "00:20", "DEVIATION: amp IC A -> B"),
    dec("P3", "01:00", "C11 accept X5R (C15850)"),
    dec("P2", "01:10", "snubber stays on the speaker side"),       # back to P2
    dec("P5", "02:00", "footprint fix"),
    dec("P4", "02:10", "review W1: swap C13"),                     # back to P4
    dec("P4", "02:11", "review W4: legend only"),                  # same backtrack
    dec("P6", "03:00", "accept a bigger outline"),
    dec("P7", "04:00", "P7->P6: spread L1-L4 for the OUT nets"),   # named backtrack
]


# ------------------------------------------------------------ dochistory

def test_backtracks_named_and_regressions_grouped():
    bts = dochistory.backtracks({"decisions": RUN})
    assert [(b["from"], b["to"], len(b["decisions"])) for b in bts] == [
        ("P3", "P2", 1), ("P5", "P4", 2), ("P7", "P6", 1)]


def test_forward_run_has_no_backtracks():
    fwd = [d for d in RUN if d["what"] not in (
        "snubber stays on the speaker side", "review W1: swap C13",
        "review W4: legend only") and "->" not in d["what"].split(":")[0]]
    assert dochistory.backtracks({"decisions": fwd}) == []
    # a forward "P3 to P4" mention is not a backtrack either
    assert dochistory.backtracks({"decisions": [
        dec("P3", "00:00", "P3 to P4: schematic next")]}) == []


def test_component_decisions_keep_part_picks_only():
    whats = [d["what"] for d in dochistory.component_decisions({"decisions": RUN})]
    assert "DEVIATION: amp IC A -> B" in whats
    assert "C11 accept X5R (C15850)" in whats
    assert "review W1: swap C13" in whats
    assert "Q1 pads" not in whats and "footprint fix" not in whats


def test_phase_spans_count_and_order():
    spans = dochistory.phase_spans({"decisions": RUN})
    assert [s["phase"] for s in spans] == ["P0", "P1", "P2", "P3", "P4", "P5",
                                           "P6", "P7"]
    assert {s["phase"]: s["count"] for s in spans}["P4"] == 2


def test_flow_spec_back_nodes_beside_their_source():
    st = {"phase": "P7", "decisions": RUN,
          "gates": {"place": {"phase": "P6", "attempts": 31}}}
    spec = dochistory.flow_spec(st, dochistory.backtracks(st))
    nodes = {n["id"]: n for n in spec["nodes"]}
    assert [nodes[f"P{i}"]["row"] for i in range(8)] == list(range(8))
    assert nodes["P7"].get("mark") and not nodes["P6"].get("mark")
    assert "gate: 31 attempts" in nodes["P6"]["sub"]
    backs = [n for n in spec["nodes"] if n["id"].startswith("back")]
    # no two returns climb overlapping rows, so all three share column 1
    assert [(n["col"], n["row"], n["title"]) for n in backs] == [
        (1, 3, "Back to P2"), (1, 5, "Back to P4"), (1, 7, "Back to P6")]
    assert "P7->P6" not in backs[2]["sub"][0]
    assert {"from": "back2", "to": "P6", "kind": "dash", "route": "vh"} in spec["edges"]


def test_flow_spec_overlapping_returns_take_separate_columns():
    st = {"phase": "P5", "decisions": [
        dec("P5", "01:00", "setup"), dec("P1", "01:10", "rethink supply"),
        dec("P5", "01:20", "setup again"), dec("P3", "01:30", "repick parts")]}
    spec = dochistory.flow_spec(st, dochistory.backtracks(st))
    backs = [(n["col"], n["row"]) for n in spec["nodes"] if n["id"].startswith("back")]
    assert backs == [(1, 5), (2, 5)]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin", "HOME": str(repo)})


def _parts(**refs) -> str:
    return json.dumps({"parts": [{"refs": [r], "mpn": m, "lcsc": c}
                                 for r, (m, c) in refs.items()]})


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    r = tmp_path / "boards"
    ws = r / "PCB-0001-A_x"
    (ws / "kicad").mkdir(parents=True)
    (ws / "routing").mkdir()
    _git(r, "init", "-q")
    (ws / "kicad" / "parts.json").write_text(_parts(C1=("A", "C1"), C2=("B", "C2")))
    (ws / "routing" / "pre-zeta.kicad_pcb").write_text("x")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "one")
    (ws / "notes.md").write_text("no parts change")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "two")
    (ws / "kicad" / "parts.json").write_text(_parts(C1=("A2", "C9"), D1=("TVS", "C3")))
    (ws / "routing" / "post-alpha.kicad_pcb").write_text("x")
    (r / "elsewhere.txt").write_text("outside the workspace")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "three")
    return ws


def test_git_commits_scoped_to_the_workspace(repo):
    commits = dochistory.git_commits(repo)
    assert [c["subject"] for c in commits] == ["one", "two", "three"]
    assert "elsewhere.txt" not in sum((c["files"] for c in commits), [])
    assert "kicad/parts.json" in commits[2]["files"]
    acts = dochistory.activity(commits)
    assert sum(a["commits"] for a in acts) == 3


def test_git_commits_outside_a_repo_is_empty(tmp_path):
    assert dochistory.git_commits(tmp_path) == []


def test_parts_changes_added_removed_changed(repo):
    changes = dochistory.parts_changes(repo, dochistory.git_commits(repo))
    assert len(changes) == 1       # "two" did not touch parts.json
    c = changes[0]
    assert c["added"] == [("D1", "TVS C3")]
    assert c["removed"] == [("C2", "B C2")]
    assert c["changed"] == [("C1", "A C1", "A2 C9")]


def test_snapshots_in_the_order_the_run_made_them(repo):
    # post-alpha sorts first by name but was committed after pre-zeta
    assert dochistory.snapshots(repo, dochistory.git_commits(repo)) == [
        "routing/pre-zeta.kicad_pcb", "routing/post-alpha.kicad_pcb"]


# ------------------------------------------------------------ report_gen kinds

def _ws_with_run(tmp_path: Path) -> Path:
    ws = make_workspace(tmp_path, phase="P7")
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    st["decisions"] = RUN
    (ws / "state.json").write_text(json.dumps(st), encoding="utf-8")
    return ws


def test_highlight_kind_tex_only(tmp_path, capsys):
    ws = _ws_with_run(tmp_path)
    code, payload = run_main(["--workspace", str(ws), "--kind", "highlight",
                              "--tex-only"], tmp_path, capsys)
    assert code == 0, payload
    assert payload["kind"] == "highlight"
    assert payload["tex"] == "reports/highlight/synth-highlight.tex"
    assert set(sections_by_name(payload)) == {
        "title", "hl_board", "hl_layers", "hl_parts", "hl_decisions", "hl_run",
        "hl_checks"}
    text = (ws / payload["tex"]).read_text(encoding="utf-8")
    assert "Highlights" in text and r"\tableofcontents" not in text
    assert "went back to an earlier phase 3 times" in text
    assert "swap C13" in text and "Q1 pads" not in text   # key decisions only
    assert "design spend & not recorded" in text
    assert not (ws / "reports" / "design_doc").exists()    # the design doc is untouched


def test_full_kind_without_diagram_maker_says_why(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HWDE_DIAGRAM_MAKER", str(tmp_path / "nowhere"))
    ws = _ws_with_run(tmp_path)
    code, payload = run_main(["--workspace", str(ws), "--kind", "full",
                              "--tex-only"], tmp_path, capsys)
    assert code == 0, payload
    secs = sections_by_name(payload)
    assert secs["decisions"] == "included" and secs["history"] == "included"
    assert secs["flow"] == "missing"
    assert any("diagram-maker skill not found" in w for w in payload["warnings"])
    text = (ws / "reports/design_full/synth-design-full.tex").read_text(encoding="utf-8")
    assert "Full Design Document" in text and "Q1 pads" in text   # every decision
    assert "P7 back to P6" in text
    spec = json.loads((ws / "reports/design_full/flow.json").read_text())
    assert spec["type"] == "flowchart"


def test_full_kind_renders_the_flow_figure(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HWDE_DIAGRAM_MAKER", raising=False)
    if report_gen.diagram_maker()[0] is None or shutil.which("node") is None:
        pytest.skip("diagram-maker skill or node not on this machine")
    ws = _ws_with_run(tmp_path)
    code, payload = run_main(["--workspace", str(ws), "--kind", "full",
                              "--tex-only"], tmp_path, capsys)
    assert sections_by_name(payload)["flow"] == "included", payload["warnings"]
    assert (ws / "reports/design_full/flow.pdf").is_file()
    text = (ws / "reports/design_full/synth-design-full.tex").read_text(encoding="utf-8")
    assert "{reports/design_full/flow.pdf}" in text


def test_cc_docs_titles_differ_by_kind(tmp_path):
    pdf = tmp_path / "x.pdf"
    titles = {k: report_gen.cc_docs_args(None, "amp", pdf, kind=k)[5]
              for k in report_gen.KINDS}
    assert titles == {"design": "amp design doc", "highlight": "amp highlight doc",
                      "full": "amp full design doc"}


# ------------------------------------------------------------ layer views

def _with_layers(ws, layers, views=("top", "bottom", "iso")):
    """reports/layers/ as layer_views.py leaves it: layers.json plus the files."""
    ldir = ws / "reports" / "layers"
    ldir.mkdir(parents=True)
    rep = {"layers": [], "views": [], "warnings": []}
    for name in layers:
        f = ldir / f"synth_{name.replace('.', '_')}.pdf"
        f.write_bytes(b"%PDF-1.4")
        rep["layers"].append({"layer": name, "path": str(f), "labels": 3})
    for v in views:
        f = ldir / f"synth_{v}.png"
        f.write_bytes(b"png")
        rep["views"].append({"view": v, "path": str(f), "status": "kept"})
    (ldir / "layers.json").write_text(json.dumps(rep), encoding="utf-8")


def test_full_kind_has_a_page_per_copper_layer(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HWDE_DIAGRAM_MAKER", str(tmp_path / "nowhere"))
    ws = _ws_with_run(tmp_path)
    _with_layers(ws, ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
    code, payload = run_main(["--workspace", str(ws), "--kind", "full",
                              "--tex-only"], tmp_path, capsys)
    assert code == 0, payload
    assert sections_by_name(payload)["layers"] == "included"
    text = (ws / "reports/design_full/synth-design-full.tex").read_text(encoding="utf-8")
    body = text.split(r"\section{Board Layers}")[1].split(r"\section{")[0]
    for name in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu", "top", "bottom", "iso"):
        assert f"reports/layers/synth_{name}." in body
    assert body.index("F_Cu") < body.index("In1_Cu") < body.index("B_Cu")
    assert not any("--render-layers" in w for w in payload["warnings"])


def test_highlight_shows_the_outer_layers_and_iso_only(tmp_path, capsys):
    ws = _ws_with_run(tmp_path)
    _with_layers(ws, ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
    code, payload = run_main(["--workspace", str(ws), "--kind", "highlight",
                              "--tex-only"], tmp_path, capsys)
    assert code == 0, payload
    assert sections_by_name(payload)["hl_layers"] == "included"
    text = (ws / payload["tex"]).read_text(encoding="utf-8")
    body = text.split(r"\section{Copper Layers}")[1].split(r"\section{")[0]
    assert "synth_F_Cu.pdf" in body and "synth_B_Cu.pdf" in body
    assert "synth_In1_Cu" not in body and "synth_top.png" not in body
    assert "synth_iso.png" in body and "all 4" in body


def test_layers_not_drawn_is_a_warning_not_a_failure(tmp_path, capsys):
    ws = _ws_with_run(tmp_path)
    code, payload = run_main(["--workspace", str(ws), "--kind", "highlight",
                              "--tex-only"], tmp_path, capsys)
    assert code == 0, payload
    assert sections_by_name(payload)["hl_layers"] == "missing"
    assert any("--render-layers" in w for w in payload["warnings"])


def _layer_runs(ws, monkeypatch, status):
    """A board, a layers.json newer than it saying `status`, and a record of
    every layer_views.py run report_gen starts under --render-layers."""
    import os
    import time
    stem = report_gen.statelib.project_stem(ws, "synth")
    pcb = ws / "kicad" / f"{stem}.kicad_pcb"
    pcb.parent.mkdir(parents=True, exist_ok=True)
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    _with_layers(ws, ["F.Cu", "B.Cu"])
    index = ws / "reports/layers/layers.json"
    rep = json.loads(index.read_text(encoding="utf-8"))
    rep["status"] = status
    index.write_text(json.dumps(rep), encoding="utf-8")
    later = time.time() + 60
    os.utime(index, (later, later))
    runs = []
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if any(str(c).endswith("layer_views.py") for c in cmd):
            runs.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(report_gen.subprocess, "run", fake)
    return runs


def test_render_layers_keeps_a_passing_layers_json(tmp_path, capsys, monkeypatch):
    ws = _ws_with_run(tmp_path)
    runs = _layer_runs(ws, monkeypatch, "pass")
    code, payload = run_main(["--workspace", str(ws), "--kind", "highlight",
                              "--tex-only", "--render-layers"], tmp_path, capsys)
    assert code == 0, payload
    assert runs == []


@pytest.mark.parametrize("status", ["error", "violations"])
def test_render_layers_redraws_a_failed_layers_json(tmp_path, capsys, monkeypatch,
                                                    status):
    ws = _ws_with_run(tmp_path)
    runs = _layer_runs(ws, monkeypatch, status)
    code, payload = run_main(["--workspace", str(ws), "--kind", "highlight",
                              "--tex-only", "--render-layers"], tmp_path, capsys)
    assert code == 0, payload
    assert len(runs) == 1


# ------------------------------------------------------------ routing snapshots

@pytest.mark.parametrize("sep", ["-", "_"])
def test_snapshots_accept_both_spellings(tmp_path, sep):
    d = tmp_path / "routing"
    d.mkdir()
    for n in (f"pre{sep}route", f"post{sep}route"):
        (d / f"{n}.kicad_pcb").write_text("x")
    (d / "other.kicad_pcb").write_text("x")
    assert dochistory.snapshots(tmp_path, []) == [
        f"routing/post{sep}route.kicad_pcb", f"routing/pre{sep}route.kicad_pcb"]


def _run_full(tmp_path, capsys, phase):
    ws = make_workspace(tmp_path, phase=phase)
    code, payload = run_main(["--workspace", str(ws), "--kind", "full",
                              "--tex-only"], tmp_path, capsys)
    return ws, payload


def test_full_doc_warns_when_a_routed_board_has_no_snapshots(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HWDE_DIAGRAM_MAKER", str(tmp_path / "nowhere"))
    ws, payload = _run_full(tmp_path, capsys, "P8")   # routing is done
    assert any("no routing snapshots" in w for w in payload["warnings"])
    (ws / "routing").mkdir(exist_ok=True)
    (ws / "routing" / "pre_route.kicad_pcb").write_text("x")
    code, payload = run_main(["--workspace", str(ws), "--kind", "full",
                              "--tex-only"], tmp_path, capsys)
    assert not any("no routing snapshots" in w for w in payload["warnings"])
    text = (ws / "reports/design_full/synth-design-full.tex").read_text(encoding="utf-8")
    assert "board snapshots" in text


@pytest.mark.parametrize("phase", ["P6", "P7"])
def test_no_snapshot_warning_before_routing_is_done(tmp_path, capsys, monkeypatch, phase):
    monkeypatch.setenv("HWDE_DIAGRAM_MAKER", str(tmp_path / "nowhere"))
    _ws, payload = _run_full(tmp_path, capsys, phase)
    assert not any("no routing snapshots" in w for w in payload["warnings"])
