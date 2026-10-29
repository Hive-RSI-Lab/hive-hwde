"""guide_facts.py acceptance tests.

Criteria -> tests:
  - complete fab package -> exit 0 / pass, BOM rows + paths populated
                                     -> test_complete_package_pass
  - missing zip/BOM/CPL -> exit 1, `missing` names it; a complete sibling
    workspace built in the same test is unaffected -> test_missing_fab_files
  - missing quote/schematic pdf -> `todo`, not a violation
                                     -> test_missing_quote_and_schematic_is_todo
  - fab/prebuy.csv carried into the facts; absent -> `todo` naming bom_cpl
                                     -> test_prebuy_list_reaches_the_guide
  - --render re-renders top/bottom through render.py first and points the
    guide at the fresh top render; a failed render is exit 1; without the
    flag a `todo` asks for it  -> test_render_flag_rerenders_for_the_guide
  - reports/cost.json carried as `generation_cost`; absent -> `todo`
    naming gen_cost          -> test_generation_cost_reaches_the_guide
  - no workspace / unreadable state.json -> exit 2
                                     -> test_no_workspace_exit2,
                                        test_unreadable_state_exit2
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import guide_facts  # noqa: E402

TS = "2026-07-28T10:00:00"

# A gen_cost.py reports/cost.json, trimmed to what guide_facts reads.
COST = {
    "currency": "USD", "total_usd": 7.5, "recorded_usd": 7.5,
    "loop_logged_usd": 2.5, "breakdown": "partial",
    "breakdown_reason": "fix round: hwde recorded no step",
    "by_step": [{"step": "P4", "label": "Schematic", "usd": 5.0},
                {"step": "unsplit", "label": "fix round", "usd": 2.5,
                 "reason": "hwde recorded no step"}],
    "shared": [{"label": "skill round", "shared_with": "the skill",
                "usd": 1.0}],
    "incomplete_sessions": [], "unpriced_tokens": {}, "notes": ["n"],
    "prices": {"source": "t", "verified": "2026-09-24"},
    "rounds": [{"label": "design run", "loop": {"unlogged_iterations": 1}},
               {"label": "skill round", "shared_with": "the skill",
                "loop": {"unlogged_iterations": 5}}],
}

BOM_HEADER = "Designator,Comment,Footprint,LCSC,Qty Per Board,MPN,Assembly Class\n"


def bom_row(designator="R1", comment="10k", footprint="0402", lcsc="C1000",
           qty="1", mpn="", assy="smt_placed") -> str:
    return f"{designator},{comment},{footprint},{lcsc},{qty},{mpn},{assy}\n"


def make_workspace(tmp_path: Path, name: str = "synth", board: str = "synth",
                   with_fab: bool = True, with_quote: bool = True,
                   with_schematic: bool = True, with_cost: bool = True) -> Path:
    """Synthetic workspace covering the state.json + kicad/ + fab/ facts
    guide_facts.collect reads, per the artifact_kinds paths in
    reference/invalidation.yaml (kicad/{board}.kicad_sch,
    kicad/{board}.kicad_pcb, kicad/parts.json, fab/{board}_gerbers.zip,
    fab/BOM.csv, fab/BOM-full.csv, fab/CPL.csv)."""
    ws = tmp_path / name
    state = {
        "version": 1, "board": board, "phase": "P10", "mode": "default",
        "gates": {"dfm": {"status": "pass"}},
        "artifacts": {},
        "decisions": [{"text": "used 2oz copper", "phase": "P2", "ts": TS}],
    }
    ws.mkdir()
    (ws / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")

    kicad = ws / "kicad"
    kicad.mkdir()
    (kicad / f"{board}.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    (kicad / f"{board}.kicad_pcb").write_text("(kicad_pcb)", encoding="utf-8")
    (kicad / "parts.json").write_text(json.dumps({"parts": [
        {"lcsc": "C1000", "mpn": "RC0402", "basic": True, "price": 0.01,
         "description": "10k resistor"}]}), encoding="utf-8")

    if with_fab:
        fab = ws / "fab"
        fab.mkdir()
        (fab / f"{board}_gerbers.zip").write_bytes(b"PK\x03\x04")
        (fab / "BOM.csv").write_text(BOM_HEADER + bom_row(), encoding="utf-8")
        (fab / "BOM-full.csv").write_text(BOM_HEADER + bom_row(),
                                          encoding="utf-8")
        (fab / "prebuy.csv").write_text(
            "LCSC,MPN,Comment,Designator,Qty Per Board,Boards,Qty To Buy,"
            "Note\n", encoding="utf-8")
        (fab / "CPL.csv").write_text(
            "Designator,Mid X,Mid Y,Layer,Rotation\nR1,1,1,top,0\n",
            encoding="utf-8")
        if with_quote:
            (fab / "quote.json").write_text(json.dumps({
                "estimated": True, "currency": "USD",
                "matrix": [{"qty": 10, "surface_finish": "HASL",
                           "total": 12.3, "unit_cost": 1.23,
                           "assembly": {"total": 5.0}}]}), encoding="utf-8")

    if with_schematic:
        reports = ws / "reports"
        reports.mkdir()
        (reports / "schematic.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    if with_cost:
        (ws / "reports").mkdir(exist_ok=True)
        (ws / "reports" / "cost.json").write_text(json.dumps(COST),
                                                  encoding="utf-8")

    return ws


def run_main(ws: Path, tmp_path: Path, capsys, name="out", extra=()):
    out = tmp_path / f"{name}.json"
    code = guide_facts.main(["--workspace", str(ws), "--out", str(out),
                             *extra])
    if out.exists():
        payload = json.loads(out.read_text(encoding="utf-8"))
    else:
        payload = json.loads(capsys.readouterr().out)
    return code, payload


# ------------------------------------------------------------------ pass

def test_complete_package_pass(tmp_path, capsys):
    ws = make_workspace(tmp_path)
    code, payload = run_main(ws, tmp_path, capsys)
    assert code == 0, payload
    assert payload["status"] == "pass"
    assert payload["missing"] == []
    assert payload["board"] == "synth"
    assert len(payload["bom"]) == 1
    row = payload["bom"][0]
    assert row["lcsc"] == "C1000"
    assert row["jlc_status"] == "basic"
    assert row["unit_price"] == 0.01
    assert payload["paths"]["gerber_zip"] == "fab/synth_gerbers.zip"
    assert payload["paths"]["bom"] == "fab/BOM.csv"
    assert payload["paths"]["cpl"] == "fab/CPL.csv"
    assert payload["paths"]["schematic_pdf"] == "reports/schematic.pdf"
    assert payload["paths"]["quote"] == "fab/quote.json"
    assert [t["fact"] for t in payload["todo"]] == ["fresh render"]
    assert payload["parts_cost_per_board"] == 0.01


def test_prebuy_list_reaches_the_guide(tmp_path, capsys):
    """The guide's ordering section lists what to pre-buy (owner,
    2026-09-24): the facts carry bom_cpl's prebuy.csv rows, build qty and
    note; a fab package from before the list exists gets a `todo`."""
    ws = make_workspace(tmp_path)
    (ws / "fab" / "prebuy.csv").write_text(
        "LCSC,MPN,Comment,Designator,Qty Per Board,Boards,Qty To Buy,Note\n"
        "C2798175,TYPE-C-6M-001,USB-C 6P,J1,1,5,5,may show as idle stock\n",
        encoding="utf-8")
    code, payload = run_main(ws, tmp_path, capsys)
    assert code == 0, payload
    pb = payload["prebuy"]
    assert pb["path"] == "fab/prebuy.csv" and pb["build_qty"] == 5
    assert pb["note"] == "may show as idle stock"
    assert pb["rows"] == [{"LCSC": "C2798175", "MPN": "TYPE-C-6M-001",
                           "Comment": "USB-C 6P", "Designator": "J1",
                           "Qty Per Board": "1", "Qty To Buy": "5"}]
    assert [t["fact"] for t in payload["todo"]] == ["fresh render"]

    old = make_workspace(tmp_path, name="old")
    (old / "fab" / "prebuy.csv").unlink()
    code, payload = run_main(old, tmp_path, capsys, name="old")
    assert code == 0 and payload["prebuy"] is None
    assert [t["fact"] for t in payload["todo"]] == ["pre-buy list",
                                                    "fresh render"]
    assert "bom_cpl.py" in payload["todo"][0]["cmd"]


def test_generation_cost_reaches_the_guide(tmp_path, capsys):
    """The guide says what the board cost to generate (owner, 2026-09-24):
    the facts carry gen_cost's total, per-step lines with the reason a round
    was not split, the shared rounds and the timed-out iterations of the
    board's own rounds only; no cost.json -> a `todo` naming gen_cost."""
    ws = make_workspace(tmp_path)
    code, payload = run_main(ws, tmp_path, capsys)
    assert code == 0, payload
    gc = payload["generation_cost"]
    assert gc["total_usd"] == 7.5 and gc["breakdown"] == "partial"
    assert gc["by_step"] == COST["by_step"] and gc["shared"] == COST["shared"]
    assert gc["timed_out_iterations"] == 1  # the shared round's 5 not counted
    assert "rounds" not in gc
    assert payload["paths"]["cost"] == "reports/cost.json"

    old = make_workspace(tmp_path, name="old", with_cost=False)
    code, payload = run_main(old, tmp_path, capsys, name="old")
    assert code == 0 and payload["generation_cost"] is None
    todo = {t["fact"]: t["cmd"] for t in payload["todo"]}
    assert "gen_cost.py" in todo["generation cost"]
    # --out shares --workspace's base, so the command works from one cwd.
    assert (f"--workspace {old.as_posix()} --out "
            f"{(old / 'reports' / 'cost.json').as_posix()}") in todo["generation cost"]


def test_upload_bom_with_jlc_header_is_read(tmp_path, capsys):
    """No BOM-full.csv: the fallback reads bom_cpl's JLC upload header,
    whose part column is "LCSC Part #", not "LCSC"."""
    ws = make_workspace(tmp_path)
    (ws / "fab" / "BOM-full.csv").unlink()
    (ws / "fab" / "BOM.csv").write_text(
        "Comment,Designator,Footprint,LCSC Part #\n10k,R1,0402,C1000\n",
        encoding="utf-8")
    code, payload = run_main(ws, tmp_path, capsys)
    assert code == 0, payload
    assert payload["bom"][0]["lcsc"] == "C1000"
    assert payload["bom"][0]["jlc_status"] == "basic"


# ------------------------------------------------------------------ missing fab files

def test_missing_fab_files(tmp_path, capsys):
    broken = make_workspace(tmp_path, name="broken", board="broken",
                            with_fab=False)
    code, payload = run_main(broken, tmp_path, capsys, name="broken_out")
    assert code == 1
    assert payload["status"] == "violations"
    assert any("gerber zip" in m for m in payload["missing"])
    assert any("BOM.csv" in m for m in payload["missing"])
    assert any("CPL.csv" in m for m in payload["missing"])

    # a complete sibling workspace built in the same test is unaffected
    complete = make_workspace(tmp_path, name="complete")
    code2, payload2 = run_main(complete, tmp_path, capsys, name="complete_out")
    assert code2 == 0
    assert payload2["status"] == "pass"
    assert payload2["missing"] == []


def test_render_flag_rerenders_for_the_guide(tmp_path, capsys, monkeypatch):
    """A guide built from an old render showed a bare board (owner,
    2026-09-24): --render renders again through render.py before gathering,
    and the guide's picture is that render."""
    calls, result = [], {"status": "pass"}

    def fake(pcb, reports):
        calls.append((pcb, reports))
        outs = []
        for view in ("top", "bottom"):
            png = reports / f"synth_{view}.png"
            png.write_bytes(b"png")
            outs.append({"view": view, "path": str(png),
                         "status": result["status"],
                         "stderr_tail": "no 3D model loaders"})
        return {"status": result["status"], "outputs": outs,
                "models_missing": ["${KICAD10_3DMODEL_DIR}/H.step"],
                "models_relinked": ["/gone/R.wrl"]}
    monkeypatch.setattr(guide_facts, "fresh_render", fake)

    ws = make_workspace(tmp_path)
    code, payload = run_main(ws, tmp_path, capsys, extra=["--render"])
    assert code == 0, payload
    assert calls == [(ws / "kicad" / "synth.kicad_pcb", ws / "reports")]
    assert payload["paths"]["top_render"] == "reports/synth_top.png"
    assert payload["render"]["views"] == {"top": "reports/synth_top.png",
                                          "bottom": "reports/synth_bottom.png"}
    assert payload["render"]["models_missing"] == [
        "${KICAD10_3DMODEL_DIR}/H.step"]
    assert payload["todo"] == []

    result["status"] = "error"
    code, payload = run_main(ws, tmp_path, capsys, name="bad",
                             extra=["--render"])
    assert code == 1 and payload["paths"]["top_render"] is None
    assert any(m.startswith("fresh render") and "loaders" in m
               for m in payload["missing"])

    def boom(pcb, reports):
        raise RuntimeError("kicad-cli not found")
    monkeypatch.setattr(guide_facts, "fresh_render", boom)
    code, payload = run_main(ws, tmp_path, capsys, name="raised",
                             extra=["--render"])
    assert code == 1 and payload["paths"]["top_render"] is None
    assert any(m.startswith("fresh render") and "kicad-cli not found" in m
               for m in payload["missing"])

    code, payload = run_main(ws, tmp_path, capsys, name="plain")
    assert payload["render"] is None
    todo = {t["fact"]: t["cmd"] for t in payload["todo"]}
    assert "--render" in todo["fresh render"]


# ------------------------------------------------------------------ todo, not a violation

def test_missing_quote_and_schematic_is_todo(tmp_path, capsys):
    ws = make_workspace(tmp_path, with_quote=False, with_schematic=False)
    code, payload = run_main(ws, tmp_path, capsys)
    assert code == 0, payload
    assert payload["status"] == "pass"
    assert payload["missing"] == []
    facts = {t["fact"] for t in payload["todo"]}
    assert "cost estimate" in facts
    assert "schematic export" in facts
    assert payload["paths"]["quote"] is None
    assert payload["paths"]["schematic_pdf"] is None


# ------------------------------------------------------------------ error (exit 2)

def test_no_workspace_exit2(tmp_path, capsys):
    code, payload = run_main(tmp_path / "nope", tmp_path, capsys)
    assert code == 2
    assert payload["status"] == "error"
    assert "state.json" in payload["error"]


def test_unreadable_state_exit2(tmp_path, capsys):
    ws = tmp_path / "corrupt"
    ws.mkdir()
    (ws / "state.json").write_text("{not json", encoding="utf-8")
    code, payload = run_main(ws, tmp_path, capsys)
    assert code == 2
    assert payload["status"] == "error"


def test_part_number_reaches_the_guide(tmp_path, capsys):
    """A board the register lists carries its PCB-NNNN-R for the title page;
    one it does not list carries none and says why."""
    ws = make_workspace(tmp_path)
    code, payload = run_main(ws, tmp_path, capsys, name="nopn")
    assert payload["part_number"] is None
    assert "no register.yaml" in payload["part_number_reason"]
    (ws.parent / "register.yaml").write_text(
        "products:\n  PCB-0001:\n    title: t\n    revs:\n"
        f"      B: {{dir: {ws.name}}}\n", encoding="utf-8")
    code, payload = run_main(ws, tmp_path, capsys, name="pn")
    assert payload["part_number"]["pn"] == "PCB-0001-B"
    assert payload["part_number"]["rev"] == "B"
    assert payload["part_number_reason"] is None
