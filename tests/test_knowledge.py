"""U4 - knowledge library + trigger-keyed retrieval.

Contracts pinned here:
  1. Every committed record in reference/knowledge/records/ lints green
     (schema, controlled classes, real source files, real scripts/flags) -
     the validate_registry discipline applied to knowledge.
  2. Retrieval is TRIGGERED, not judged: a board declaring a buck block gets
     power-loop/EMI records in its spawn prompt_block (the U4 acceptance);
     packages and interfaces key the same way; no declared key -> nothing.
  3. reference/topologies/buck.md is a GENERATED VIEW - byte-pinned to
     render_topology over the committed records.
  4. datasheet_extract --app-note emits a record-shaped grounding payload,
     and the committed app-note records actually cite the committed PDF.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "hwde"
SCRIPTS = SKILL / "scripts"
RECORDS = SKILL / "reference" / "knowledge" / "records"
SOURCES = SKILL / "reference" / "knowledge" / "sources"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import knowledge  # noqa: E402
import knowledgelib  # noqa: E402
from datasheet_extract import main as ds_main  # noqa: E402


def run_knowledge(tmp_path, argv):
    """knowledge.py in-process; returns (payload, exit_code) via --out."""
    out = tmp_path / "out.json"
    code = knowledge.main(argv + ["--out", str(out)])
    return json.loads(out.read_text(encoding="utf-8")), code


def write_record(d: Path, rid: str, **over) -> Path:
    rec = {
        "id": rid, "classes": ["emi"],
        "applies": {"topologies": ["buck"], "packages": [], "interfaces": []},
        "rule": None, "prose": "A fact.",
        "sources": [{"file": "reference/knowledge/records", "note": "dir"}],
        "status": "active", "origin": "test",
    }
    # sources[].file must EXIST; point at a real repo file by default
    rec["sources"] = [{"file": "LEARNINGS.md"}]
    rec.update(over)
    p = d / f"{rid}.yaml"
    p.write_text(yaml.safe_dump(rec), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. committed records lint green
# ---------------------------------------------------------------------------
def test_committed_records_lint_green():
    problems = knowledgelib.validate()
    assert problems == [], "\n".join(problems)


def test_committed_records_exist_at_all():
    assert len(knowledgelib.record_files()) >= 10  # 8 migration + app-note set


def test_validate_cli_contract(tmp_path):
    payload, code = run_knowledge(tmp_path, ["--validate"])
    assert code == 0 and payload["status"] == "pass"


# ---------------------------------------------------------------------------
# 1b. the lint actually rejects broken records
# ---------------------------------------------------------------------------
def test_lint_rejects_unknown_class(tmp_path):
    write_record(tmp_path, "r-a", classes=["not-a-class"])
    assert any("unknown class" in p for p in knowledgelib.validate(tmp_path))


def test_lint_rejects_missing_source_file(tmp_path):
    write_record(tmp_path, "r-a", sources=[{"file": "no/such/file.pdf"}])
    assert any("not found" in p for p in knowledgelib.validate(tmp_path))


def test_lint_accepts_a_not_redistributed_sidecar(tmp_path):
    """A source whose licence forbids redistribution is committed as
    `<file>.not-redistributed.md` (url + sha256) instead of the bytes: the
    citation still resolves, a citation with neither still fails."""
    ws, recs = tmp_path / "ws", tmp_path / "recs"
    (ws / "research" / "sources").mkdir(parents=True)
    recs.mkdir()
    rel = "research/sources/spec.pdf"
    write_record(recs, "r-a", sources=[{"file": rel}])
    assert any("not found" in p
               for p in knowledgelib.validate(recs, source_roots=(ws,)))
    (ws / (rel + knowledgelib.NOT_REDISTRIBUTED_SUFFIX)).write_text(
        "download: https://example.invalid/spec.pdf sha256 deadbeef\n",
        encoding="utf-8")
    assert knowledgelib.validate(recs, source_roots=(ws,)) == []


def test_lint_rejects_id_stem_mismatch(tmp_path):
    p = write_record(tmp_path, "r-a")
    p.rename(tmp_path / "r-b.yaml")
    assert any("filename stem" in p for p in knowledgelib.validate(tmp_path))


def test_lint_rejects_unreachable_record(tmp_path):
    write_record(tmp_path, "r-a",
                 applies={"topologies": [], "packages": [], "interfaces": []})
    assert any("retrieve" in p for p in knowledgelib.validate(tmp_path))


def test_lint_rejects_non_ascii(tmp_path):
    write_record(tmp_path, "r-a", prose="100 µF at the pin")
    assert any("ASCII" in p for p in knowledgelib.validate(tmp_path))


def test_lint_rejects_hallucinated_script_and_flag(tmp_path):
    write_record(tmp_path, "r-a", prose="run scripts/no_such_tool.py please")
    assert any("non-existent script" in p for p in knowledgelib.validate(tmp_path))
    write_record(tmp_path, "r-b",
                 prose="run scripts/knowledge.py --no-such-flag")
    assert any("--no-such-flag" in p for p in knowledgelib.validate(tmp_path))


def test_lint_rejects_bad_enforced_by(tmp_path):
    write_record(tmp_path, "r-a", rule={"enforced_by": "ghost.py", "x": 1})
    assert any("enforced_by" in p for p in knowledgelib.validate(tmp_path))


def test_lint_rejects_overlong_prose(tmp_path):
    write_record(tmp_path, "r-a", prose="x" * (knowledgelib.PROSE_MAX + 1))
    assert any("schema" in p and "prose" in p
               for p in knowledgelib.validate(tmp_path))


def test_validate_cli_exit_1_on_problems(tmp_path):
    d = tmp_path / "recs"
    d.mkdir()
    write_record(d, "r-a", classes=["nope"])
    payload, code = run_knowledge(
        tmp_path, ["--validate", "--records-dir", str(d)])
    assert code == 1 and payload["status"] == "problems"


# ---------------------------------------------------------------------------
# 2. triggered retrieval (the U4 acceptance pins)
# ---------------------------------------------------------------------------
def make_ws(tmp_path, constraints=None, parts=None, parts_at="kicad") -> Path:
    ws = tmp_path / "ws"
    (ws / "kicad").mkdir(parents=True)
    (ws / "state.json").write_text(json.dumps({"board": "synth"}),
                                   encoding="utf-8")
    if constraints is not None:
        (ws / "kicad" / "constraints.json").write_text(
            json.dumps(constraints), encoding="utf-8")
    if parts is not None:
        pdir = ws / parts_at
        pdir.mkdir(exist_ok=True)
        (pdir / "parts.json").write_text(json.dumps(parts), encoding="utf-8")
    return ws


def test_buck_block_gets_powerloop_and_emi_records_in_prompt(tmp_path):
    """THE acceptance: a synthetic board declaring a buck block gets
    power-loop/EMI records in its P6 spawn prompt."""
    ws = make_ws(tmp_path,
                 constraints={"blocks": [{"topology": "buck", "block": "B3"}]})
    payload, code = run_knowledge(
        tmp_path, ["--select", "--workspace", str(ws)])
    assert code == 0 and payload["count"] > 0
    classes = {c for r in payload["records"] for c in r["classes"]}
    assert {"power-loop", "emi"} <= classes
    assert "buck-input-hot-loop" in payload["prompt_block"]
    assert "KNOWLEDGE RECORDS" in payload["prompt_block"]
    assert payload["keys"]["topologies"] == ["buck"]


def test_no_declared_keys_selects_nothing(tmp_path):
    ws = make_ws(tmp_path, constraints={"power": [{"net": "+5V",
                                                   "current_a": 1.0}]})
    payload, code = run_knowledge(
        tmp_path, ["--select", "--workspace", str(ws)])
    assert code == 0 and payload["count"] == 0
    assert payload["prompt_block"] == ""


def test_package_keys_records_with_normalization(tmp_path):
    """P3's packages key package records; 'SO-8-EP' == 'SO8EP' == 'so 8 ep'."""
    ws = make_ws(tmp_path, parts={"parts": [{"package": "so 8 ep"}]})
    payload, code = run_knowledge(
        tmp_path, ["--select", "--workspace", str(ws)])
    assert code == 0
    ids = [r["id"] for r in payload["records"]]
    assert "buck-thermal-via-and-via-current" in ids


def test_parts_json_pre_p5_fallback_location(tmp_path):
    ws = make_ws(tmp_path, parts={"parts": [{"package": "HTSOP-J8"}]},
                 parts_at="parts")
    payload, _ = run_knowledge(tmp_path, ["--select", "--workspace", str(ws)])
    assert "buck-thermal-via-and-via-current" in \
        [r["id"] for r in payload["records"]]


def test_interface_keys_from_diff_pairs_base(tmp_path):
    ws = make_ws(tmp_path, constraints={
        "diff_pairs": [{"p": "/USB_DP", "n": "/USB_DM", "base": "USB"}]})
    payload, _ = run_knowledge(tmp_path, ["--select", "--workspace", str(ws)])
    assert "buck-upstream-inrush-limit" in \
        [r["id"] for r in payload["records"]]
    assert payload["keys"]["interfaces"] == ["usb"]


def test_explicit_blocks_flag_without_workspace(tmp_path):
    payload, code = run_knowledge(tmp_path, ["--select", "--blocks", "Buck"])
    assert code == 0 and payload["count"] >= 8


def test_draft_and_superseded_records_are_never_injected(tmp_path):
    d = tmp_path / "recs"
    d.mkdir()
    write_record(d, "r-live")
    write_record(d, "r-draft", status="draft")
    write_record(d, "r-old", status="superseded")
    payload, _ = run_knowledge(
        tmp_path, ["--select", "--blocks", "buck", "--records-dir", str(d)])
    assert [r["id"] for r in payload["records"]] == ["r-live"]


def test_select_missing_workspace_is_error(tmp_path):
    payload, code = run_knowledge(
        tmp_path, ["--select", "--workspace", str(tmp_path / "nope")])
    assert code == 2 and payload["status"] == "error"


# ---------------------------------------------------------------------------
# 3. the topology view is generated, and pinned
# ---------------------------------------------------------------------------
def test_buck_topology_view_is_pinned_to_the_records(tmp_path):
    """reference/topologies/buck.md is a generated view. Hand-edits fail
    here; edit the record and re-render (command in the file header)."""
    committed = (SKILL / "reference" / "topologies" / "buck.md").read_text(
        encoding="utf-8")
    rendered = knowledgelib.render_topology(knowledgelib.load_records(), "buck")
    assert committed == rendered


def test_render_topology_cli(tmp_path):
    out = tmp_path / "view.md"
    code = knowledge.main(["--render-topology", "buck", "--out", str(out)])
    assert code == 0 and out.read_text(encoding="utf-8").startswith("# Topology")
    # unknown topology -> refuse an empty view
    code = knowledge.main(["--render-topology", "flyback9x",
                           "--out", str(tmp_path / "v2.md")])
    assert code == 2


# ---------------------------------------------------------------------------
# 4. app-note ingestion round-trip
# ---------------------------------------------------------------------------
ROHM = SOURCES / "rohm-buck-pcb-layout-an.pdf"


def test_app_note_grounding_payload(tmp_path):
    out = tmp_path / "g.json"
    code = ds_main(["--app-note", str(ROHM), "--out", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["mode"] == "app_note"
    assert payload["record_schema"]["title"] == "hwde knowledge record"
    assert payload["record_template"]["applies"] == {
        "topologies": [], "packages": [], "interfaces": [], "parts": []}
    # U13: the template carries the schema-v2 coverage fields too
    assert payload["record_template"]["maturity"] == "draft"
    assert "level" in payload["record_template"]
    assert "envelope" in payload["record_template"]
    assert payload["n_pages"] >= 10 and payload["text_chars"] > 10000
    assert "emi" in payload["classes"]


def test_app_note_refuses_non_pdf(tmp_path):
    fake = tmp_path / "x.pdf"
    fake.write_text("<html>nope</html>", encoding="utf-8")
    out = tmp_path / "g.json"
    code = ds_main(["--app-note", str(fake), "--out", str(out)])
    assert code == 2


def test_app_note_records_committed_and_cite_the_pdf():
    """The U4 acceptance's round-trip: real app note -> records citing it
    by page, lint green (green-ness is test 1's job)."""
    assert ROHM.is_file()
    recs = [r for r in knowledgelib.load_records()
            if str(r.get("origin", "")).startswith("app-note:")]
    assert len(recs) >= 3
    for r in recs:
        cites = [s for s in r["sources"]
                 if "rohm-buck-pcb-layout-an.pdf" in s["file"]]
        assert cites, f"{r['id']}: app-note record must cite the PDF"
        assert any(s.get("page") for s in cites), \
            f"{r['id']}: cite the PDF down to the page"


# ---------------------------------------------------------------------------
# 5. list mode
# ---------------------------------------------------------------------------
def test_list_mode(tmp_path):
    payload, code = run_knowledge(tmp_path, ["--list"])
    assert code == 0 and payload["count"] == len(knowledgelib.record_files())
    assert all(set(r) == {"id", "status", "level", "maturity", "classes",
                          "applies"}
               for r in payload["records"])
    assert "checklists" in payload


def test_boards_sources_resolve_in_the_boards_repo(tmp_path, monkeypatch):
    """A `boards/<ws>/...` source resolves under HWDE_BOARDS_ROOT (the boards
    repo); a file missing there is reported; with no boards repo cloned the
    citation cannot be checked and is not reported."""
    root = tmp_path / "boards-repo"
    (root / "ws" / "parts").mkdir(parents=True)
    (root / "ws" / "parts" / "d.pdf").write_bytes(b"%PDF")
    monkeypatch.setenv("HWDE_BOARDS_ROOT", str(root))
    assert knowledgelib._source_exists("boards/ws/parts/d.pdf")
    assert not knowledgelib._source_exists("boards/ws/parts/gone.pdf")
    monkeypatch.setenv("HWDE_BOARDS_ROOT", str(tmp_path / "not-cloned"))
    assert knowledgelib._source_exists("boards/ws/parts/gone.pdf")
    # an empty root (a check run pointed at a blank dir) is not cloned either
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("HWDE_BOARDS_ROOT", str(tmp_path / "empty"))
    assert knowledgelib._source_exists("boards/ws/parts/gone.pdf")


# ---------------------------------------------------------------------------
# STDC14 is the default STM32 debug header; TC2030 / 0.1 in are brief opt-outs
# ---------------------------------------------------------------------------
STDC14_ID = "swd-debug-port-stm32-stdc14-default"


def _stdc14_record():
    return yaml.safe_load((SKILL / "reference" / "knowledge" / "records"
                           / f"{STDC14_ID}.yaml").read_text(encoding="utf-8"))


def test_swd_debug_port_block_selects_the_stdc14_default(tmp_path):
    payload, code = run_knowledge(
        tmp_path, ["--select", "--blocks", "swd-debug-port"])
    assert code == 0
    rec = next(r for r in payload["records"] if r["id"] == STDC14_ID)
    assert rec["rule"]["lcsc"] == "C5307809"
    assert rec["rule"]["mpn"] == "FTSH-107-01-L-DV-K"
    assert "T_VCC" in rec["rule"]["pin_3"]
    assert "VCP" in rec["rule"]["pin_13"] and "VCP" in rec["rule"]["pin_14"]


def test_stdc14_envelope_holds_for_stm32_and_opt_outs_fall_outside():
    env = _stdc14_record()["envelope"]
    verdict = lambda **op: knowledgelib.envelope_contains(env, op)["verdict"]
    assert verdict(mcu_family_kind="stm32", debug_connector_kind="stdc14") \
        == "inside"
    assert verdict(mcu_family_kind="stm32", debug_connector_kind="tc2030") \
        == "outside"
    assert verdict(mcu_family_kind="stm32",
                   debug_connector_kind="pin-header-2.54") == "outside"


def test_agents_and_checklist_default_to_stdc14_with_named_opt_outs():
    arch = (SKILL / "agents" / "architect.md").read_text(encoding="utf-8")
    assert "STDC14" in arch and "tc2030" in arch and "pin-header-2.54" in arch
    ra = (SKILL / "agents" / "requirements-analyst.md").read_text(
        encoding="utf-8")
    assert "STDC14" in ra and "TC2030" in ra and "0.1 in" in ra
    mcu = (SKILL / "reference" / "checklists" / "mcu.md").read_text(
        encoding="utf-8")
    assert "STDC14" in mcu and "TC2030" in mcu
