"""U15 - research verb: acquisition, synthesis, second reader, auto-trigger.

Contracts pinned here (v3 design decision 5a, the research leg):
  1. Allowlist enforcement: https only, host on reference/knowledge/
     domains.yaml (suffix match, no look-alikes, no userinfo), every redirect
     hop re-checked, refusal BEFORE any transport call and ledgered; vendor
     community hosts / forum domains force tier forum; the domains file
     itself lints (the comma-in-a-flow-value trap cannot hide).
  2. Quarantine + ledger: a fetch lands under research/sources/, sha-pinned
     with tier/pages; --expect pdf refuses HTML shells; local registration
     is still allowlist-checked; depth and attempt caps are VISIBLE
     checkpoints (payload + state decision/event), never silent.
  3. open: one task per gap from a coverage report inside a governed
     workspace; consumes budgets.research.per_run through the state ledger
     (lazily installed on pre-U15 state files); the per-run cap hit names
     the unopened slots; briefs carry gap + existing knowledge + templates.
  4. The research contract (validate): ledger-only citations with page +
     note, no forum-sole records, envelope_note, maturity governance, slot
     keying, class coverage, no library id clash, page within the PDF, a
     draft checklist when the gap had none; generalizes may point at the
     library.
  5. THE acceptance: a seeded gap (topology with an approved checklist and
     no records) round-trips gap -> open -> fetch -> draft records ->
     second reader verified -> close -> pending queue entry, inside the
     caps; coverage then reads the slot provisional (covered at floor
     verified); verified records inject via --select, refuted ones never do.
  6. promote: verified record + sources into the library with rewritten
     citation paths, library re-linted; drafts refused.
  7. Distributor clients: exit 2 naming the exact missing env vars; DigiKey
     v4 / Mouser v1 request shapes pinned through a fake transport.
  8. Wiring: the research verb plans (slot / all variants), full-run carries
     the auto-trigger at P2 and P3 exits, agent contracts pass the flag
     lint, bench stage P1 freezes/scores/compares a research root.
"""
from __future__ import annotations

import json
import shutil
import sys
from io import BytesIO
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "hwde"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bench  # noqa: E402
import distributors  # noqa: E402
import knowledge  # noqa: E402
import knowledgelib  # noqa: E402
import learnings  # noqa: E402
import learnlib  # noqa: E402
import research  # noqa: E402
import researchlib  # noqa: E402
import state as state_mod  # noqa: E402
import task_router as tr  # noqa: E402

APPROVAL = {"by": "owner", "date": "2026-08-15"}
TI = "https://www.ti.com/lit/an/slua123.pdf"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _pdf(pages: int = 5) -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()


PDF = _pdf()


def transport_ok(url):
    return {"status": 200, "final_url": url, "hops": [url],
            "content_type": "application/pdf", "body": PDF}


def transport_html(url):
    return {"status": 200, "final_url": url, "hops": [url],
            "content_type": "text/html", "body": b"<!doctype html><p>x"}


def run(mod, tmp_path, argv):
    out = tmp_path / f"out{len(list(tmp_path.iterdir()))}.json"
    code = mod.main(argv + ["--out", str(out)])
    return json.loads(out.read_text(encoding="utf-8")), code


def write_checklist(d: Path, cid: str, classes=("power-loop", "emi"),
                    field="topologies", **over) -> Path:
    cl = {"id": cid, "kind": "coverage-checklist",
          "applies": {field: [cid]},
          "requires": [{"class": c, "min_level": "topology"} for c in classes],
          "maturity": "approved", "approval": dict(APPROVAL),
          "origin": "test"}
    cl.update(over)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{cid}.yaml"
    p.write_text(yaml.safe_dump(cl), encoding="utf-8")
    return p


def make_lib(tmp_path, checklist="llc"):
    recs = tmp_path / "lib_records"
    cls = tmp_path / "lib_checklists"
    recs.mkdir(exist_ok=True)
    if checklist:
        write_checklist(cls, checklist)
    else:
        cls.mkdir(exist_ok=True)
    return recs, cls


def make_ws(tmp_path, topology="llc", name="ws", governed=True,
            blocks=None) -> Path:
    ws = tmp_path / name
    if governed:
        state_mod.State.init(ws, "synth")
    (ws / "kicad").mkdir(parents=True, exist_ok=True)
    blocks = blocks or [{"topology": topology, "block": "PWR",
                         "operating_point": {"vin_v": 400, "f_khz": 100,
                                             "switching_kind": "soft"}}]
    (ws / "kicad" / "constraints.json").write_text(
        json.dumps({"blocks": blocks}), encoding="utf-8")
    return ws


def coverage_report(tmp_path, ws, lib, phase="P2", label="cov") -> Path:
    recs, cls = lib
    payload, code = run(knowledge, tmp_path, [
        "--coverage", "--workspace", str(ws), "--phase", phase,
        "--records-dir", str(recs), "--checklists-dir", str(cls)])
    p = tmp_path / f"{label}-{len(list(tmp_path.iterdir()))}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p, payload, code


def open_task(tmp_path, ws, report: Path, slot=None):
    argv = ["open", "--workspace", str(ws), "--gaps", str(report)]
    argv += ["--slot", slot] if slot else ["--all"]
    return run(research, tmp_path, argv)


def base_record(tid, rid="llc-resonant-tank-loop", **over) -> dict:
    rec = {
        "id": rid, "classes": ["power-loop"],
        "applies": {"topologies": ["llc"]}, "rule": None,
        "prose": "Keep the resonant tank loop tight; fig 3 (p2) shows it.",
        "sources": [{"file": "research/sources/slua123.pdf", "page": 2,
                     "note": "fig 3: resonant tank loop drawn on the eval board"}],
        "status": "draft", "origin": f"research:{tid}", "level": "topology",
        "envelope": {"switching_kind": {"in": ["soft"]}},
        "envelope_note": "bounded by soft switching - the loop rule is about "
                         "resonant current, not edge rate",
        "maturity": "draft", "generalizes": [],
    }
    rec.update(over)
    for k in [k for k, v in rec.items() if v is ...]:
        del rec[k]
    return rec


def write_record(ws, rec) -> Path:
    d = researchlib.records_dir(researchlib.root_of(ws))
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{rec['id']}.yaml"
    p.write_text(yaml.safe_dump(rec, sort_keys=False), encoding="utf-8")
    return p


def fetch(ws, tid, url=TI, tier="vendor-appnote", transport=transport_ok, **kw):
    root = researchlib.root_of(ws)
    task = researchlib.load_task(root, tid)
    return researchlib.fetch_source(root, task, url, tier,
                                    transport=transport, **kw)


@pytest.fixture
def opened(tmp_path):
    """A governed llc workspace with one open research task + one fetched
    PDF (the researcher's starting point)."""
    lib = make_lib(tmp_path)
    ws = make_ws(tmp_path)
    report, cov, code = coverage_report(tmp_path, ws, lib)
    assert code == 1 and cov["gaps"][0]["slot"] == "block:PWR"
    payload, code = open_task(tmp_path, ws, report)
    assert code == 0, payload
    tid = payload["opened"][0]
    pl, code = fetch(ws, tid)
    assert code == 0, pl
    return {"ws": ws, "tid": tid, "lib": lib, "report": report,
            "open": payload}


# ---------------------------------------------------------------------------
# 1. allowlist
# ---------------------------------------------------------------------------
def test_domains_file_lints_clean_and_flow_values_did_not_split():
    data = researchlib.load_domains()
    assert researchlib.domain_problems(data) == []
    for e in data["domains"]:
        assert set(e) <= {"domain", "kind", "note"}, e   # LEARNINGS 2026-08-14
    kinds = {e["kind"] for e in data["domains"]}
    assert kinds <= set(researchlib.DOMAIN_KINDS)
    assert any(e["domain"] == "rohm.com" for e in data["domains"])


def test_domains_lint_catches_a_split_flow_value(tmp_path):
    bad = {"version": 1, "domains": [{"domain": "ti.com", "kind": "vendor",
                                      "note": "Texas (incl. x", "y)": None}]}
    probs = researchlib.domain_problems(bad)
    assert probs and "additional" in probs[0].lower()


@pytest.mark.parametrize("url,ok,forced,needle", [
    ("https://www.ti.com/lit/an/x.pdf", True, None, None),
    ("https://rohm.com/x.pdf", True, None, None),
    ("https://product.tdk.com/x", True, None, None),
    ("https://e2e.ti.com/thread/1", True, "forum", None),
    ("https://electronics.stackexchange.com/q/1", True, "forum", None),
    ("http://www.ti.com/x.pdf", False, None, "https only"),
    ("https://evil.example/x.pdf", False, None, "off-list fetch refused"),
    ("https://notti.com/x.pdf", False, None, "off-list"),
    ("https://ti.com.evil.example/x.pdf", False, None, "off-list"),
    ("https://user@www.ti.com/x.pdf", False, None, "userinfo"),
    ("ftp://www.ti.com/x.pdf", False, None, "https only"),
])
def test_check_url_matrix(url, ok, forced, needle):
    ck = researchlib.check_url(url)
    assert ck["ok"] is ok, ck
    assert ck["forced_tier"] == forced
    if needle:
        assert needle in ck["reason"]


def test_effective_tier_only_ever_lowers():
    assert researchlib.effective_tier("vendor-layout", None) == "vendor-layout"
    assert researchlib.effective_tier("vendor-layout", "forum") == "forum"
    assert researchlib.effective_tier("forum", None) == "forum"
    with pytest.raises(ValueError):
        researchlib.effective_tier("blog", None)


def test_fetch_refuses_off_list_before_any_transport_and_ledgers_it(opened):
    ws, tid = opened["ws"], opened["tid"]

    def boom(url):
        raise AssertionError("transport must not be called for an off-list URL")

    pl, code = fetch(ws, tid, url="https://evil.example/x.pdf", transport=boom)
    assert code == 2 and pl["refused"] == "allowlist"
    task = researchlib.load_task(researchlib.root_of(ws), tid)
    assert task["attempts"][-1]["kind"] == "allowlist"
    assert len(task["sources"]) == 1          # nothing acquired


def test_fetch_refuses_an_off_list_redirect_hop(opened):
    ws, tid = opened["ws"], opened["tid"]

    def redirect(url):
        return {"status": 200, "final_url": "https://cdn.evil.example/x.pdf",
                "hops": [url, "https://cdn.evil.example/x.pdf"],
                "content_type": "application/pdf", "body": PDF}

    pl, code = fetch(ws, tid, url="https://www.ti.com/redir.pdf",
                     transport=redirect)
    assert code == 2 and pl["refused"] == "allowlist-redirect"
    assert not (researchlib.sources_dir(researchlib.root_of(ws))
                / "x.pdf").exists()


def test_fetch_quarantines_sha_pins_and_counts_depth(opened):
    ws, tid = opened["ws"], opened["tid"]
    root = researchlib.root_of(ws)
    task = researchlib.load_task(root, tid)
    src = task["sources"][0]
    assert src["file"] == "research/sources/slua123.pdf"
    assert (ws / src["file"]).read_bytes() == PDF
    assert src["sha256"] == researchlib.sha256_bytes(PDF)
    assert src["pages"] == 5 and src["tier"] == "vendor-appnote"
    assert src["domain"] == "ti.com" and src["via"] == "http"
    assert researchlib.depth_state(task) == {
        "cap": 4, "used": 1, "remaining": 3, "attempts": 0,
        "attempts_cap": 12}


def test_fetch_expect_pdf_refuses_html_shell_with_lcsc_hint(opened):
    ws, tid = opened["ws"], opened["tid"]
    pl, code = fetch(ws, tid,
                     url="https://www.lcsc.com/datasheet/lcsc_datasheet_x_C1.pdf",
                     transport=transport_html)
    assert code == 1 and pl["refused"] == "not_pdf"
    assert "wmsc.lcsc.com" in pl["error"]
    task = researchlib.load_task(researchlib.root_of(ws), tid)
    assert task["attempts"][-1]["kind"] == "not-pdf"
    assert len(task["sources"]) == 1


def test_fetch_forum_host_forces_tier_and_html_is_allowed(opened):
    ws, tid = opened["ws"], opened["tid"]
    pl, code = fetch(ws, tid, url="https://e2e.ti.com/thread/9",
                     tier="vendor-appnote", expect="html",
                     transport=transport_html)
    assert code == 0
    assert pl["source"]["tier"] == "forum"
    assert pl["source"]["declared_tier"] == "vendor-appnote"


def test_fetch_local_file_registers_against_an_allowlisted_origin(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    local = tmp_path / "held.pdf"
    local.write_bytes(_pdf(3))
    pl, code = fetch(ws, tid, url="https://www.rohm.com/an/buck.pdf",
                     transport=None, local_file=local)
    assert code == 0 and pl["source"]["via"] == "local"
    assert pl["source"]["pages"] == 3
    pl, code = fetch(ws, tid, url="https://evil.example/held.pdf",
                     transport=None, local_file=local)
    assert code == 2 and pl["refused"] == "allowlist"


def test_depth_cap_is_a_visible_checkpoint(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    root = researchlib.root_of(ws)
    task = researchlib.load_task(root, tid)
    task["caps"]["depth_per_gap"] = 2
    researchlib.write_task(root, task)
    pl, code = fetch(ws, tid, url="https://www.rohm.com/a.pdf")
    assert code == 0
    # through the CLI so the state decision + event are recorded
    pl, code = run(research, tmp_path, [
        "fetch", "--workspace", str(ws), "--task", tid,
        "--url", "https://www.rohm.com/b.pdf", "--tier", "vendor-appnote",
        "--file", str(ws / "research/sources/slua123.pdf")])
    assert code == 1 and pl["status"] == "checkpoint"
    assert pl["checkpoint"] == "research_depth"
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert any(h["event"] == "research_checkpoint" for h in st["history"])
    assert any("depth cap" in d["what"] for d in st["decisions"])


def test_attempts_cap_is_a_visible_checkpoint(opened):
    ws, tid = opened["ws"], opened["tid"]
    root = researchlib.root_of(ws)
    task = researchlib.load_task(root, tid)
    task["caps"]["depth_per_gap"] = 2     # 1 of 2 used; attempts cap = 6
    task["attempts"] = [{"ts": "t", "url": "u", "kind": "http", "detail": "404"}] * 6
    researchlib.write_task(root, task)
    pl, code = fetch(ws, tid, url="https://www.rohm.com/c.pdf")
    assert code == 1 and pl["checkpoint"] == "research_attempts"


# ---------------------------------------------------------------------------
# 2. open
# ---------------------------------------------------------------------------
def test_default_caps_mirror_state_defaults():
    assert researchlib.DEFAULT_CAPS == state_mod.DEFAULT_BUDGETS["research"]


def test_open_needs_a_governed_workspace_and_a_coverage_report(tmp_path):
    lib = make_lib(tmp_path)
    ws = make_ws(tmp_path, governed=False)
    report, cov, code = coverage_report(tmp_path, ws, lib)
    pl, code = open_task(tmp_path, ws, report)
    assert code == 2 and "state.json" in pl["error"]
    ws2 = make_ws(tmp_path, name="ws2")
    bad = tmp_path / "notareport.json"
    bad.write_text("{}", encoding="utf-8")
    pl, code = open_task(tmp_path, ws2, bad)
    assert code == 2 and "gaps[]" in pl["error"]


def test_open_writes_the_task_brief_and_consumes_per_run(opened):
    ws, tid, payload = opened["ws"], opened["tid"], opened["open"]
    assert tid == "block-llc-1"
    task = researchlib.load_task(researchlib.root_of(ws), tid)
    assert task["slot"] == "block:PWR" and task["status"] == "open"
    assert task["gap"]["missing"][0]["class"] == "power-loop"
    assert task["caps"]["depth_per_gap"] == 4
    brief = payload["tasks"][0]["brief"]
    for key in ("assignment", "operating_point", "missing", "policy",
                "allowlist", "record_template", "record_schema", "commands",
                "caps", "existing_knowledge"):
        assert key in brief, key
    assert brief["record_template"]["origin"] == f"research:{tid}"
    assert brief["record_template"]["applies"]["topologies"] == ["llc"]
    assert brief["record_template"]["status"] == "draft"
    assert brief["checklist_template"] is None       # the slot has one
    assert "research.py fetch" in brief["commands"]["fetch"]
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert st["budgets"]["research"]["per_run"] == 5
    events = [h["event"] for h in st["history"]]
    assert "research_opened" in events and "budget" in events


def test_open_per_run_cap_names_the_unopened_slots(tmp_path):
    lib = make_lib(tmp_path)
    ws = make_ws(tmp_path, blocks=[
        {"topology": "llc", "block": "A", "operating_point": {"vin_v": 1}},
        {"topology": "llc", "block": "B", "operating_point": {"vin_v": 1}}])
    st = state_mod.State.load(ws / "state.json")
    st.data["budgets"]["research"]["per_run"] = 1
    st.save()
    report, cov, code = coverage_report(tmp_path, ws, lib)
    assert len(cov["gaps"]) == 2
    pl, code = open_task(tmp_path, ws, report)
    assert code == 1 and pl["status"] == "checkpoint"
    assert pl["checkpoint"] == "research_cap"
    assert pl["opened"] == ["block-llc-1"]
    assert pl["unopened_slots"] == ["block:B"]
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert any(d["what"].startswith("research cap hit") for d in st["decisions"])
    assert any(h["event"] == "research_checkpoint" for h in st["history"])


def test_open_refuses_a_non_gap_slot_and_a_second_open_task(opened, tmp_path):
    ws, report = opened["ws"], opened["report"]
    pl, code = open_task(tmp_path, ws, report, slot="block:NOPE")
    assert code == 2 and "not a gap" in pl["error"]
    pl, code = open_task(tmp_path, ws, report, slot="block:PWR")
    assert code == 1 and pl["skipped"][0]["reason"].startswith("slot")


def test_open_installs_the_research_budget_on_a_pre_u15_state(tmp_path):
    lib = make_lib(tmp_path)
    ws = make_ws(tmp_path)
    st = state_mod.State.load(ws / "state.json")
    del st.data["budgets"]["research"]
    st.save()
    report, cov, code = coverage_report(tmp_path, ws, lib)
    pl, code = open_task(tmp_path, ws, report)
    assert code == 0, pl
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert st["budgets"]["research"] == {"per_run": 5, "depth_per_gap": 4}
    assert any(h["event"] == "budget_defaulted" for h in st["history"])


def test_open_on_a_checklistless_topology_asks_for_the_checklist(tmp_path):
    lib = make_lib(tmp_path, checklist=None)
    ws = make_ws(tmp_path, topology="flyback")
    report, cov, code = coverage_report(tmp_path, ws, lib)
    assert cov["gaps"][0]["missing"][0]["class"] == "coverage-checklist"
    pl, code = open_task(tmp_path, ws, report)
    assert code == 0
    brief = pl["tasks"][0]["brief"]
    assert brief["checklist_template"]["applies"] == {"topologies": ["flyback"]}
    tid = pl["opened"][0]
    fetch(ws, tid)
    write_record(ws, base_record(tid, "flyback-snubber", classes=["emi"],
                                 applies={"topologies": ["flyback"]}))
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 1
    assert any("no coverage checklist" in p for p in val["problems"])
    cd = researchlib.checklists_dir(researchlib.root_of(ws))
    cd.mkdir(parents=True, exist_ok=True)
    (cd / "flyback.yaml").write_text(yaml.safe_dump({
        "id": "flyback", "kind": "coverage-checklist",
        "applies": {"topologies": ["flyback"]},
        "requires": [{"class": "emi", "min_level": "topology"}],
        "maturity": "draft", "origin": f"research:{tid}",
        "sources": [{"file": "research/sources/slua123.pdf", "page": 1,
                     "note": "table 1: what the note covers"}]}),
        encoding="utf-8")
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 0, val["problems"]
    assert val["checklists"][0]["id"] == "flyback"


# ---------------------------------------------------------------------------
# 3. the research contract (validate)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("over,needle", [
    ({"sources": [{"file": "research/sources/other.pdf", "page": 1,
                   "note": "fig 1: something read"}]}, "not in task"),
    ({"sources": [{"file": "research/sources/slua123.pdf",
                   "note": "fig 1: something read"}]}, "has no page"),
    ({"sources": [{"file": "research/sources/slua123.pdf", "page": 1}]},
     "needs a note"),
    ({"sources": [{"file": "research/sources/slua123.pdf", "page": 9,
                   "note": "fig 9: beyond the end"}]}, "> 5 pages"),
    ({"envelope_note": ...}, "envelope_note"),
    ({"maturity": "approved", "approval": dict(APPROVAL)}, "self-declared"),
    ({"maturity": "verified", "status": "active"}, "verification block"),
    ({"status": "active"}, "status draft"),
    ({"applies": {"topologies": ["buck"]}}, "does not key slot"),
    ({"classes": ["thermal"]}, "cover none of the gap"),
    ({"id": "buck-input-hot-loop"}, "already exists in the library"),
    ({"level": "topology", "envelope": None}, "requires a non-empty envelope"),
])
def test_validate_refuses_contract_breaches(opened, tmp_path, over, needle):
    ws, tid = opened["ws"], opened["tid"]
    rec = base_record(tid, **over)
    if over.get("id") == "buck-input-hot-loop":
        rec["level"], rec["envelope"] = "principle", None
        rec.pop("envelope_note", None)
    write_record(ws, rec)
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 1
    assert any(needle in p for p in val["problems"]), val["problems"]


def test_validate_refuses_forum_as_sole_source_but_allows_corroboration(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    pl, code = fetch(ws, tid, url="https://e2e.ti.com/thread/2", expect="html",
                     transport=transport_html)
    forum = pl["source"]["file"]
    write_record(ws, base_record(tid, sources=[
        {"file": forum, "page": 1, "note": "the accepted answer says so"}]))
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 1 and any("SOLE source" in p for p in val["problems"])
    write_record(ws, base_record(tid, sources=[
        {"file": "research/sources/slua123.pdf", "page": 2,
         "note": "fig 3: resonant tank loop on the eval board"},
        {"file": forum, "page": 1, "note": "the accepted answer corroborates"}]))
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 0, val["problems"]


def test_validate_accepts_a_generalizes_link_into_the_library(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid, generalizes=["buck-input-hot-loop"]))
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 0, val["problems"]
    write_record(ws, base_record(tid, generalizes=["no-such-record"]))
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 1 and any("unknown record" in p for p in val["problems"])


def test_validate_and_close_ignore_other_tasks_records(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    write_record(ws, base_record("block-other-9", rid="other-broken",
                                 sources=[{"file": "nope.pdf"}]))
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 0, val["problems"]


# ---------------------------------------------------------------------------
# 4. THE acceptance: seeded gap -> research -> verified -> queue entry
# ---------------------------------------------------------------------------
def test_seeded_gap_round_trips_to_a_verified_queue_entry(opened, tmp_path):
    ws, tid, lib = opened["ws"], opened["tid"], opened["lib"]
    recs, cls = lib
    root = researchlib.root_of(ws)
    # the researcher writes two records (one per missing class)
    write_record(ws, base_record(tid))
    write_record(ws, base_record(
        tid, rid="llc-transformer-shield", classes=["emi"],
        prose="Shield winding between primary and secondary; fig 5 (p4).",
        sources=[{"file": "research/sources/slua123.pdf", "page": 4,
                  "note": "fig 5: shield winding between primary and secondary"}]))
    val, code = run(research, tmp_path, ["validate", "--workspace", str(ws),
                                         "--task", tid])
    assert code == 0, val["problems"]
    # close refuses before the second reader rules
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 1 and sorted(cl["unruled"]) == [
        "llc-resonant-tank-loop", "llc-transformer-shield"]
    # the second reader verifies both
    for rid in ("llc-resonant-tank-loop", "llc-transformer-shield"):
        v, code = run(research, tmp_path, [
            "verify", "--workspace", str(ws), "--task", tid, "--record", rid,
            "--verdict", "verified", "--note", f"re-read the cited page: {rid} holds"])
        assert code == 0, v
    rec = yaml.safe_load((researchlib.records_dir(root)
                          / "llc-resonant-tank-loop.yaml").read_text())
    assert rec["maturity"] == "verified" and rec["status"] == "active"
    assert rec["verification"]["by"] == "second-reader"
    # close -> LEARNINGS entry + pending queue row
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 0, cl
    assert cl["summary"] == {"verified": 2, "refuted": 0, "checklists": 0,
                             "sources": 1}
    q, code = run(learnings, tmp_path, ["queue", "--workspace", str(ws),
                                        "--status", "pending"])
    assert code == 0 and [e["entry"] for e in q["entries"]] == [cl["queue_entry"]]
    row = q["entries"][0]
    assert row["stage"] == "P2" and "research" in row["tags"]
    entries, malformed = learnlib.parse_entries(
        (ws / "LEARNINGS.md").read_text(encoding="utf-8"))
    assert malformed == [] and len(entries) == 1
    assert "llc-transformer-shield" in entries[0]["body"]
    v, code = run(learnings, tmp_path, ["validate", "--workspace", str(ws)])
    assert code == 0, v
    task = researchlib.load_task(root, tid)
    assert task["status"] == "closed" and task["outcome"] == "verified"
    assert task["queue_entry"] == cl["queue_entry"]
    assert researchlib.depth_state(task)["used"] == 1     # inside the caps
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert any(h["event"] == "research_closed" for h in st["history"])
    # coverage now folds the workspace records in: provisional at the
    # approved floor (researched, unapproved), covered at floor verified
    _, cov, code = coverage_report(tmp_path, ws, lib)
    slot = cov["slots"][0]
    assert code == 0 and slot["verdict"] == "provisional"
    assert cov["workspace_records"] == ["llc-resonant-tank-loop",
                                        "llc-transformer-shield"]
    blockers = {e["id"]: e["blocker"] for c in slot["classes"]
                for e in c["records"]}
    assert set(blockers.values()) == {"maturity-below-floor"}
    cov2, code = run(knowledge, tmp_path, [
        "--coverage", "--workspace", str(ws), "--records-dir", str(recs),
        "--checklists-dir", str(cls), "--maturity-floor", "verified"])
    assert code == 0 and cov2["slots"][0]["verdict"] == "covered"
    # and the verified records inject through the normal --select path
    sel, code = run(knowledge, tmp_path, ["--select", "--workspace", str(ws),
                                          "--records-dir", str(recs)])
    assert sel["count"] == 2 and "workspace" in sel["prompt_block"]
    assert all(r["_workspace"] for r in sel["records"])
    # a second open on the same slot is allowed now that the task is closed
    pl, code = open_task(tmp_path, ws, opened["report"], slot="block:PWR")
    assert code == 0 and pl["opened"] == ["block-llc-2"]
    prior = pl["tasks"][0]["brief"]["prior_tasks"]
    assert [t["id"] for t in prior] == [tid]
    assert prior[0]["verdicts"]["llc-transformer-shield"]["verdict"] == "verified"


def test_refuted_record_stays_draft_never_injects_and_can_be_re_read(opened, tmp_path):
    ws, tid, lib = opened["ws"], opened["tid"], opened["lib"]
    recs, _ = lib
    root = researchlib.root_of(ws)
    p = write_record(ws, base_record(tid))
    v, code = run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 as described"])
    assert code == 0
    v, code = run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "refuted",
        "--note", "p2 fig 3 shows a buck, not an LLC tank"])
    assert code == 0
    rec = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert rec["status"] == "draft" and rec["maturity"] == "draft"
    assert "verification" not in rec
    sel, code = run(knowledge, tmp_path, ["--select", "--workspace", str(ws),
                                          "--records-dir", str(recs)])
    assert sel["count"] == 0
    task = researchlib.load_task(root, tid)
    assert task["verdicts"]["llc-resonant-tank-loop"]["verdict"] == "refuted"
    # U21: a verdict is not enough. Refuted leaves the record draft and draft
    # never injects, so closing here would design the board without it.
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 1 and cl["drafts"] == ["llc-resonant-tank-loop"]
    assert cl["refuted"] == ["llc-resonant-tank-loop"] and cl["unruled"] == []
    assert "never injects" in cl["error"]
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid, "--accept-drafts"])
    assert code == 0 and cl["summary"]["refuted"] == 1
    assert cl["outcome"] == "verified_with_drafts"
    assert "Refuted" in (ws / "LEARNINGS.md").read_text(encoding="utf-8")


def test_verify_is_a_targeted_edit_that_keeps_the_prose_bytes(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    p = write_record(ws, base_record(tid))
    text = ("id: llc-resonant-tank-loop\nclasses: [power-loop]\n"
            "applies: {topologies: [llc]}\nrule: null\n"
            "prose: >-\n  Keep the tank loop tight: fig 3 (p2) shows it -\n"
            "  and the second line stays as written.\n"
            "sources:\n  - {file: research/sources/slua123.pdf, page: 2, "
            "note: \"fig 3: tank loop\"}\n"
            "status: draft\norigin: research:" + tid + "\nlevel: topology\n"
            "envelope: {switching_kind: {in: [soft]}}\n"
            "envelope_note: bounded by soft switching only\n"
            "maturity: draft\n")
    p.write_text(text, encoding="utf-8", newline="\n")
    v, code = run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 re-read, holds"])
    assert code == 0, v
    new = p.read_text(encoding="utf-8")
    assert "  Keep the tank loop tight: fig 3 (p2) shows it -\n" in new
    assert "maturity: verified\n" in new and "status: active\n" in new
    assert new.count("verification:") == 1
    assert yaml.safe_load(new)["verification"]["note"] == "p2 fig 3 re-read, holds"


def test_verify_refuses_bad_input(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    v, code = run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "short"])
    assert code == 2 and "note" in v["error"]
    v, code = run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "no-such", "--verdict", "verified",
        "--note", "long enough note here"])
    assert code == 2


def test_close_refuses_dirty_or_empty_and_abandon_needs_a_reason(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 1 and "nothing to close" in cl["error"]
    write_record(ws, base_record(tid, sources=[
        {"file": "research/sources/nope.pdf", "page": 1, "note": "made-up citation"}]))
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 1 and "validate" in cl["error"]
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid, "--abandon"])
    assert code == 2
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid, "--abandon",
                                        "--reason", "slot needs no research after all"])
    assert code == 0 and cl["outcome"] == "abandoned"
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert any("abandoned" in d["what"] for d in st["decisions"])
    assert not (ws / "learnings" / "queue.yaml").exists()


def test_status_lists_tasks_and_caps(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    stt, code = run(research, tmp_path, ["status", "--workspace", str(ws)])
    assert code == 0 and stt["tasks"][0]["id"] == tid
    assert stt["caps"]["per_run"] == 5
    assert stt["tasks"][0]["depth"]["used"] == 1


# ---------------------------------------------------------------------------
# 5. promote
# ---------------------------------------------------------------------------
def test_promote_copies_verified_record_and_sources_into_the_library(opened, tmp_path):
    ws, tid, lib = opened["ws"], opened["tid"], opened["lib"]
    recs, cls = lib
    libsrc = tmp_path / "lib_sources"
    write_record(ws, base_record(tid))
    pr, code = run(research, tmp_path, [
        "promote", "--workspace", str(ws), "--record", "llc-resonant-tank-loop",
        "--records-dir", str(recs), "--sources-dir", str(libsrc),
        "--checklists-dir", str(cls)])
    assert code == 1 and "VERIFIED" in pr["error"]      # draft refused
    run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 re-read, holds"])
    pr, code = run(research, tmp_path, [
        "promote", "--workspace", str(ws), "--record", "llc-resonant-tank-loop",
        "--records-dir", str(recs), "--sources-dir", str(libsrc),
        "--checklists-dir", str(cls), "--dry-run"])
    assert code == 0 and pr["dry_run"] and not (recs / "llc-resonant-tank-loop.yaml").exists()
    pr, code = run(research, tmp_path, [
        "promote", "--workspace", str(ws), "--record", "llc-resonant-tank-loop",
        "--records-dir", str(recs), "--sources-dir", str(libsrc),
        "--checklists-dir", str(cls)])
    assert code == 0, pr
    copied = yaml.safe_load((recs / "llc-resonant-tank-loop.yaml").read_text())
    assert copied["maturity"] == "verified"
    assert copied["sources"][0]["file"].endswith("lib_sources/slua123.pdf")
    assert (libsrc / "slua123.pdf").read_bytes() == PDF
    assert knowledgelib.validate(recs, cls, strict=True) == []
    assert "learnings.py resolve" in pr["next"]
    pr, code = run(research, tmp_path, [
        "promote", "--workspace", str(ws), "--record", "llc-resonant-tank-loop",
        "--records-dir", str(recs), "--sources-dir", str(libsrc),
        "--checklists-dir", str(cls)])
    assert code == 2 and "already exists" in pr["error"]


def test_promote_carries_a_not_redistributed_sidecar_in_place_of_the_bytes(
        opened, tmp_path):
    """A source whose licence forbids redistribution is kept as its
    `<file>.not-redistributed.md` sidecar (url + sha256). Promotion copies the
    sidecar, the citation still names the source, and the library lints."""
    ws, tid, lib = opened["ws"], opened["tid"], opened["lib"]
    recs, cls = lib
    libsrc = tmp_path / "lib_sources"
    write_record(ws, base_record(tid))
    run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 re-read, holds"])
    pdf = ws / "research" / "sources" / "slua123.pdf"
    pdf.unlink()
    sidecar = pdf.with_name(pdf.name + knowledgelib.NOT_REDISTRIBUTED_SUFFIX)
    sidecar.write_text("download: https://example.invalid/slua123.pdf\n"
                       "sha256: deadbeef\n", encoding="utf-8")
    pr, code = run(research, tmp_path, [
        "promote", "--workspace", str(ws), "--record", "llc-resonant-tank-loop",
        "--records-dir", str(recs), "--sources-dir", str(libsrc),
        "--checklists-dir", str(cls)])
    assert code == 0, pr
    copied = yaml.safe_load((recs / "llc-resonant-tank-loop.yaml").read_text())
    assert copied["sources"][0]["file"].endswith("lib_sources/slua123.pdf")
    assert not (libsrc / "slua123.pdf").exists()          # the bytes never travel
    assert (libsrc / ("slua123.pdf"
                      + knowledgelib.NOT_REDISTRIBUTED_SUFFIX)).is_file()
    assert knowledgelib.validate(recs, cls, strict=True) == []


def test_promote_still_refuses_a_source_that_is_simply_gone(opened, tmp_path):
    ws, tid, lib = opened["ws"], opened["tid"], opened["lib"]
    recs, cls = lib
    write_record(ws, base_record(tid))
    run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 re-read, holds"])
    (ws / "research" / "sources" / "slua123.pdf").unlink()
    pr, code = run(research, tmp_path, [
        "promote", "--workspace", str(ws), "--record", "llc-resonant-tank-loop",
        "--records-dir", str(recs), "--sources-dir", str(tmp_path / "ls2"),
        "--checklists-dir", str(cls)])
    assert code == 2 and "missing from the workspace" in pr["error"]


# ---------------------------------------------------------------------------
# 6. distributor clients
# ---------------------------------------------------------------------------
def test_parts_exits_2_naming_the_exact_missing_credentials(tmp_path, monkeypatch):
    for v in (*distributors.DIGIKEY_ENV, *distributors.MOUSER_ENV):
        monkeypatch.delenv(v, raising=False)
    pl, code = run(research, tmp_path, ["parts", "--mpn", "TPS563201DDCR"])
    assert code == 2 and pl["status"] == "error"
    for v in (*distributors.DIGIKEY_ENV, *distributors.MOUSER_ENV):
        assert v in pl["error"], v
    assert pl["missing"]["digikey"] == list(distributors.DIGIKEY_ENV)
    assert "developer.digikey.com" in pl["register"]["digikey"]
    pl, code = run(research, tmp_path, ["parts", "--mpn", "X",
                                        "--provider", "mouser"])
    assert code == 2 and "HWDE_MOUSER_API_KEY" in pl["error"]
    assert "HWDE_DIGIKEY" not in pl["error"]


def test_digikey_client_request_shapes_and_normalization():
    calls = []

    def fake(method, url, headers=None, data=None, json_body=None):
        calls.append({"method": method, "url": url, "headers": headers,
                      "data": data, "json": json_body})
        if url.endswith("/v1/oauth2/token"):
            return {"status": 200, "json": {"access_token": "tok",
                                            "expires_in": 599}, "text": ""}
        return {"status": 200, "text": "", "json": {
            "ProductsCount": 1, "Products": [{
                "ManufacturerProductNumber": "TPS563201DDCR",
                "Manufacturer": {"Name": "Texas Instruments"},
                "Description": {"ProductDescription": "Buck 3A",
                                "DetailedDescription": "Buck Switching Regulator"},
                "DatasheetUrl": "https://www.ti.com/lit/ds/symlink/tps563201.pdf",
                "ProductUrl": "https://www.digikey.com/x",
                "QuantityAvailable": 1234, "UnitPrice": 0.5,
                "ProductStatus": {"Status": "Active"},
                "Parameters": [{"ParameterText": "Voltage - Input (Max)",
                                "ValueText": "17V"}],
                "ProductVariations": [{"DigiKeyProductNumber": "296-TPS563201DDCRCT-ND",
                                       "StandardPricing": [{"BreakQuantity": 1,
                                                            "UnitPrice": 0.5}]}],
            }]}}

    c = distributors.DigiKeyClient("cid", "sec", transport=fake)
    resp = c.keyword("TPS563201DDCR", limit=3)
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://api.digikey.com/v1/oauth2/token"
    assert calls[0]["data"] == {"client_id": "cid", "client_secret": "sec",
                                "grant_type": "client_credentials"}
    assert calls[1]["url"] == "https://api.digikey.com/products/v4/search/keyword"
    assert calls[1]["headers"]["Authorization"] == "Bearer tok"
    assert calls[1]["headers"]["X-DIGIKEY-Client-Id"] == "cid"
    assert calls[1]["headers"]["X-DIGIKEY-Locale-Site"] == "US"
    assert calls[1]["json"] == {"Keywords": "TPS563201DDCR", "Limit": 3, "Offset": 0}
    hit = distributors.normalize_digikey(resp["json"]["Products"][0])
    assert hit["mpn"] == "TPS563201DDCR" and hit["manufacturer"] == "Texas Instruments"
    assert hit["datasheet_url"].endswith("tps563201.pdf")
    assert hit["parameters"] == {"Voltage - Input (Max)": "17V"}
    assert hit["price_breaks"] == [{"qty": 1, "unit_price": 0.5, "currency": "USD"}]
    assert hit["distributor_pn"] == "296-TPS563201DDCRCT-ND"
    c.details("TPS563201DDCR")
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["url"].endswith("/products/v4/search/TPS563201DDCR/productdetails")
    assert len(calls) == 3       # the token is cached
    sb = distributors.DigiKeyClient("cid", "sec", transport=fake, sandbox=True)
    sb.keyword("x")
    assert calls[-2]["url"].startswith("https://sandbox-api.digikey.com/")


def test_mouser_client_request_shape_and_normalization():
    calls = []

    def fake(method, url, headers=None, data=None, json_body=None):
        calls.append({"method": method, "url": url, "json": json_body})
        return {"status": 200, "text": "", "json": {
            "Errors": [], "SearchResults": {"NumberOfResult": 1, "Parts": [{
                "ManufacturerPartNumber": "TPS563201DDCR",
                "Manufacturer": "Texas Instruments",
                "Description": "Buck", "DataSheetUrl": "https://www.ti.com/x.pdf",
                "ProductDetailUrl": "https://www.mouser.com/x",
                "MouserPartNumber": "595-TPS563201DDCR",
                "AvailabilityInStock": "2,345",
                "ProductAttributes": [{"AttributeName": "Output Current",
                                       "AttributeValue": "3 A"}],
                "PriceBreaks": [{"Quantity": 1, "Price": "$0.55", "Currency": "USD"}],
                "LifecycleStatus": "New Product"}]}}}

    m = distributors.MouserClient("KEY/1", transport=fake)
    resp = m.part_number("TPS563201DDCR")
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == ("https://api.mouser.com/api/v1/search/"
                               "partnumber?apiKey=KEY%2F1")
    assert calls[0]["json"] == {"SearchByPartRequest": {
        "mouserPartNumber": "TPS563201DDCR", "partSearchOptions": "None"}}
    hit = distributors.normalize_mouser(resp["json"]["SearchResults"]["Parts"][0])
    assert hit["stock"] == 2345 and hit["unit_price"] == 0.55
    assert hit["parameters"] == {"Output Current": "3 A"}
    assert hit["distributor_pn"] == "595-TPS563201DDCR"


def test_lookup_reports_partial_credentials(monkeypatch, tmp_path):
    for v in distributors.DIGIKEY_ENV:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HWDE_MOUSER_API_KEY", "k")

    def fake(method, url, headers=None, data=None, json_body=None):
        return {"status": 200, "text": "", "json": {
            "Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}}}

    monkeypatch.setattr(distributors, "http_transport", fake)
    res = distributors.lookup("X")
    assert res["results"]["digikey"]["status"] == "no_credentials"
    assert res["results"]["mouser"]["status"] == "pass"
    assert "HWDE_DIGIKEY_CLIENT_ID" in res["missing"]["digikey"]
    pl, code = run(research, tmp_path, ["parts", "--mpn", "X"])
    assert code == 0 and pl["results"]["mouser"]["count"] == 0


# ---------------------------------------------------------------------------
# 7. wiring: registry, recipes, agents, bench
# ---------------------------------------------------------------------------
def test_research_verb_plans_slot_and_all_variants(opened):
    ws = opened["ws"]
    payload, _ = tr.run(["--verb", "research", "--workspace", str(ws),
                         "--arg", "slot=block:PWR"])
    assert payload["status"] == "planned", payload
    steps = payload["recipe"]["steps"]
    cmds = [s.get("command", "") for s in steps]
    assert payload["recipe"]["variant"] == "slot"
    assert any("research.py open" in c and "--slot block:PWR" in c for c in cmds)
    roles = [(s["role"], s["tier"]) for s in steps if s["kind"] == "agent"]
    assert ("researcher", "fable/high") in roles
    assert ("research-second-reader", "opus/high") in roles
    assert any("research.py close" in c for c in cmds)
    assert payload["recipe"]["human_hold"] == 1 and payload["recipe"]["gates"] == []
    payload, _ = tr.run(["--verb", "research", "--workspace", str(ws)])
    assert payload["recipe"]["variant"] == "all"
    assert any("--all" in s.get("command", "")
               for s in payload["recipe"]["steps"])


def test_full_run_carries_the_auto_trigger_at_p2_and_p3():
    payload, _ = tr.run(["--verb", "full-run"])
    steps = payload["recipe"]["steps"]
    opens = [s["command"] for s in steps
             if s["kind"] == "script" and "research.py open" in s["command"]]
    assert len(opens) == 2
    assert "--all --phase P2" in opens[0] and "--all --phase P3" in opens[1]
    roles = [s["role"] for s in steps if s["kind"] == "agent"]
    assert roles.count("researcher") == 2
    assert roles.count("research-second-reader") == 2
    closes = [s for s in steps if s["kind"] == "script"
              and "research.py close" in s["command"]]
    assert len(closes) == 2
    # the coverage re-run after research precedes the P3 coverage step
    idx = [i for i, s in enumerate(steps) if s["kind"] == "script"
           and "knowledge.py --coverage" in s["command"]]
    assert len(idx) == 3
    assert opens[0] < opens[1]
    assert tr.validate_registry() == []


@pytest.mark.parametrize("doc", ["researcher.md", "research-second-reader.md"])
def test_agent_contracts_pass_the_flag_lint(doc):
    text = (SKILL / "agents" / doc).read_text(encoding="utf-8")
    assert text.isascii()
    problems: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        problems += tr._check_command_text(f"{doc}:{i}", line)
    assert problems == []
    assert "research.py fetch" in text or "research.py verify" in text


def test_researcher_contract_names_the_load_bearing_rules():
    text = (SKILL / "agents" / "researcher.md").read_text(encoding="utf-8")
    for needle in ("VISUAL", "domains.yaml", "envelope_note", "maturity: draft",
                   "research.py validate", "second reader"):
        assert needle.lower() in text.lower(), needle
    reader = (SKILL / "agents" / "research-second-reader.md").read_text(
        encoding="utf-8")
    assert "refute" in reader.lower() and "FRESH" in reader
    assert "No web tools" in reader


def test_learnlib_targets_workspace_research_records():
    body = ("promote boards/demo/research/records/llc-tank.yaml and "
            "boards/demo/research/checklists/llc.yaml")
    hits = [rel for rx, shape in learnlib._TARGET_PATTERNS
            for m in rx.finditer(body) for rel in [shape.format(m.group(1))]]
    assert "boards/demo/research/records/llc-tank.yaml" in hits
    assert "boards/demo/research/checklists/llc.yaml" in hits


def test_bench_p1_freeze_score_baseline_and_compare(opened, tmp_path):
    """The research-quality bench hook: freeze a research root as a graded
    P1 fixture, score it, record the baseline, then a degraded candidate
    (an off-ledger citation) regresses under --compare."""
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 re-read, holds"])
    mdir = tmp_path / "manifest"
    mdir.mkdir()
    man = mdir / "manifest.yaml"
    man.write_text("version: 1\nfixtures:\n  placeholder:\n    stage: P2\n"
                   "    board: x\n    files: {}\n", encoding="utf-8")
    pl, _ = bench.run(["--freeze", "--stage", "P1", "--fixture", "r_seed",
                       "--board", "synth", "--manifest", str(man),
                       "--from", f"task={ws}/research/tasks/{tid}.json",
                       "--from-dir", f"research={ws}/research",
                       "--freeze-args", json.dumps({"task": tid}),
                       "--grade", "test seed"])
    assert pl["status"] == "pass" and pl["froze"] == "r_seed"
    pl, _ = bench.run(["--stage", "P1", "--fixture", "r_seed",
                       "--manifest", str(man), "--baseline"])
    assert pl["status"] == "pass" and pl["composite"] == 100.0
    assert pl["metrics"]["verified"] == 1 and pl["metrics"]["records"] == 1
    # candidate: a research dir whose record cites outside the ledger
    cand = tmp_path / "cand"
    shutil.copytree(ws / "research", cand)
    rec = yaml.safe_load((cand / "records" / "llc-resonant-tank-loop.yaml").read_text())
    rec["sources"][0]["file"] = "research/sources/made-up.pdf"
    (cand / "records" / "llc-resonant-tank-loop.yaml").write_text(
        yaml.safe_dump(rec), encoding="utf-8")
    pl, _ = bench.run(["--stage", "P1", "--fixture", "r_seed",
                       "--manifest", str(man), "--artifact", str(cand),
                       "--compare"])
    assert pl["status"] == "violations" and pl["baseline"]["regressed"]
    assert pl["penalties"]["off_ledger"] == 1


def test_committed_p1_fixture_is_the_mechanism_seed():
    man = yaml.safe_load((ROOT / "tests/fixtures/stages/manifest.yaml")
                         .read_text(encoding="utf-8"))
    p1 = {k: v for k, v in man["fixtures"].items() if v["stage"] == "P1"}
    assert p1, "bench stage P1 needs a committed fixture"
    for fid, e in p1.items():
        assert e["args"]["task"], fid
        payload, _ = bench.run(["--stage", "P1", "--fixture", fid, "--compare"])
        assert payload["status"] == "pass", payload.get("baseline")
        assert payload["baseline"]["delta"] == 0


# --------------------------------------------------------------- U21: drafts
# Unverified research is loud, never silent. bb-amp closed P9 holding six
# draft input-stage records (bias return, CMRR symmetry, guarding) that never
# injected: the board was designed without its hardest knowledge and nothing
# said so anywhere. The per-task close check could not catch it - it is a
# snapshot of one task's own records, blind to anything that lands after that
# task closes.

def test_close_refuses_while_a_record_is_still_draft(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 1
    assert cl["drafts"] == ["llc-resonant-tank-loop"]
    assert cl["unruled"] == ["llc-resonant-tank-loop"]
    assert "still draft" in cl["error"]
    assert "research.py verify" in cl["remedy"]


def test_accept_drafts_closes_but_leaves_an_explicit_state_decision(opened,
                                                                   tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid, "--accept-drafts"])
    assert code == 0, cl
    assert cl["accepted_drafts"] == ["llc-resonant-tank-loop"]
    assert cl["outcome"] == "verified_with_drafts"
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    dec = [d for d in st["decisions"]
           if "unverified draft record" in d["what"]]
    assert len(dec) == 1 and "llc-resonant-tank-loop" in dec[0]["what"]
    assert any(h["event"] == "research_drafts_accepted" for h in st["history"])
    task = researchlib.load_task(researchlib.root_of(ws), tid)
    assert task["accepted_drafts"] == ["llc-resonant-tank-loop"]


def test_draft_sweep_names_a_record_stalled_behind_a_closed_task(opened,
                                                                 tmp_path):
    """bb-amp's mechanism: the task closes clean, THEN a draft record bound to
    it appears. No open task owns it, so no close check can ever see it."""
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    v, code = run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 re-read, holds"])
    assert code == 0, v
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 0, cl
    sweep = researchlib.draft_sweep(ws)
    assert sweep["counts"]["drafts"] == 0
    # ... and now the straggler lands against the closed task
    write_record(ws, base_record(tid, rid="llc-transformer-shield",
                                 classes=["emi"]))
    sweep = researchlib.draft_sweep(ws)
    assert sweep["counts"] == {"drafts": 1, "stalled": 1, "unruled": 1,
                               "refuted": 0, "orphaned": 0}
    row = sweep["stalled"][0]
    assert row["id"] == "llc-transformer-shield" and row["task"] == tid
    assert row["task_status"] == "closed" and row["state"] == "unruled"


def test_draft_sweep_names_an_orphan_no_task_claims(opened, tmp_path):
    ws = opened["ws"]
    write_record(ws, base_record("block-llc-99", rid="llc-orphan-rule"))
    sweep = researchlib.draft_sweep(ws)
    assert sweep["counts"]["orphaned"] == 1
    assert sweep["stalled"][0]["state"] == "orphaned"
    assert sweep["stalled"][0]["task_status"] is None


def test_status_counts_verified_vs_draft_and_flags_the_stall(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    write_record(ws, base_record(tid, rid="llc-transformer-shield",
                                 classes=["emi"]))
    v, code = run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 re-read, holds"])
    assert code == 0, v
    stat, code = run(research, tmp_path, ["status", "--workspace", str(ws)])
    # the task is still OPEN, so nothing is stalled yet
    assert code == 0, stat
    assert stat["tasks"][0]["counts"] == {"records": 2, "verified": 1,
                                          "refuted": 0, "unruled": 1,
                                          "draft": 1}
    assert stat["draft_counts"]["stalled"] == 0
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid, "--accept-drafts"])
    assert code == 0, cl
    stat, code = run(research, tmp_path, ["status", "--workspace", str(ws)])
    assert code == 1 and stat["status"] == "violations"
    assert [r["id"] for r in stat["stalled"]] == ["llc-transformer-shield"]
    assert "never injects" in stat["error"]


def test_run_close_surfaces_unverified_research(opened, tmp_path):
    """learnings.py compile is the run-close step: it must name the loss into
    the payload the digest is written from, a state decision, and the queue."""
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid, "--accept-drafts"])
    assert code == 0, cl
    comp, code = run(learnings, tmp_path, ["compile", "--workspace", str(ws)])
    assert code == 1 and comp["status"] == "problems"
    assert comp["research_drafts"]["stalled"] == 1
    assert "llc-resonant-tank-loop" in comp["error"]
    assert [r["id"] for r in comp["draft_unverified"]] == \
        ["llc-resonant-tank-loop"]
    queue = yaml.safe_load((ws / "learnings" / "queue.yaml")
                           .read_text(encoding="utf-8"))
    assert queue["research_drafts"]["stalled"] == 1
    assert queue["research_draft_records"][0]["id"] == "llc-resonant-tank-loop"
    # the queue it just wrote must still validate (QUEUE_SCHEMA is
    # additionalProperties:false - the sweep's keys have to be declared)
    val, code = run(learnings, tmp_path, ["validate", "--workspace", str(ws)])
    assert code == 0, val
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert any("run closed over 1 unverified research record" in d["what"]
               for d in st["decisions"])
    assert any(h["event"] == "research_drafts_unverified"
               for h in st["history"])
    # idempotent: compiling twice does not stack duplicate decisions
    comp2, code = run(learnings, tmp_path, ["compile", "--workspace", str(ws)])
    assert code == 1
    st2 = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert len([d for d in st2["decisions"]
                if "run closed over" in d["what"]]) == 1


def test_run_close_is_quiet_when_every_record_verified(opened, tmp_path):
    ws, tid = opened["ws"], opened["tid"]
    write_record(ws, base_record(tid))
    v, code = run(research, tmp_path, [
        "verify", "--workspace", str(ws), "--task", tid,
        "--record", "llc-resonant-tank-loop", "--verdict", "verified",
        "--note", "p2 fig 3 re-read, holds"])
    assert code == 0, v
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 0, cl
    comp, code = run(learnings, tmp_path, ["compile", "--workspace", str(ws)])
    assert code == 0 and comp["status"] == "pass"
    assert comp["research_drafts"]["drafts"] == 0
    queue = yaml.safe_load((ws / "learnings" / "queue.yaml")
                           .read_text(encoding="utf-8"))
    assert "research_drafts" not in queue


def test_run_close_on_a_workspace_without_research_is_unchanged(tmp_path):
    ws = make_ws(tmp_path, name="noresearch")
    (ws / "LEARNINGS.md").write_text(
        "# LEARNINGS - noresearch\n\n## 2026-08-20 [P6][place] A thing\n"
        "Body text.\n", encoding="utf-8", newline="\n")
    comp, code = run(learnings, tmp_path, ["compile", "--workspace", str(ws)])
    assert code == 0 and comp["research_drafts"]["drafts"] == 0


# ---------------------------------------------------------------------------
# 8. a host that refuses the fetch (PCB-0028: Torex + Littelfuse behind Akamai)
# ---------------------------------------------------------------------------
TOREX = "https://www.torexsemi.com/file/xc6206/XC6206.pdf"
LITTELFUSE = "https://www.littelfuse.com/assetdocs/smbj-datasheet.pdf"
LCSC_PDF = "https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/XC6206.pdf"
AKAMAI_403 = (b"<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n"
              b"<H1>Access Denied</H1>\nReference&#32;&#35;18&#46;1\n<P>"
              b"https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;18</P>")


def transport_vendor_blocked(url):
    """Vendor hosts 403 (Akamai); every other host serves the PDF."""
    if "torexsemi.com" in url or "littelfuse.com" in url:
        return {"status": 403, "final_url": url, "hops": [url],
                "content_type": "text/html", "server": "AkamaiGHost",
                "body": AKAMAI_403}
    return transport_ok(url)


def no_resolver(parts, vendor=None):
    raise AssertionError("the fallback must not run without a part number")


def blank_task(tmp_path):
    """An open task that has acquired nothing yet."""
    lib = make_lib(tmp_path)
    ws = make_ws(tmp_path)
    report, _cov, _code = coverage_report(tmp_path, ws, lib)
    payload, code = open_task(tmp_path, ws, report)
    assert code == 0, payload
    return ws, payload["opened"][0], lib


@pytest.mark.parametrize("resp,challenge", [
    ({"status": 403, "server": "AkamaiGHost", "body": AKAMAI_403}, "akamai"),
    ({"status": 401, "body": b""}, None),
    ({"status": 429, "body": b"slow down"}, None),
    ({"status": 503, "body": b"<html><script src='/cdn-cgi/challenge-platform/"
                             b"h/b/orchestrate'></script>"}, "cloudflare"),
    ({"status": 200, "body": b"<!DOCTYPE html><html><head><title>Just a "
                             b"moment...</title>"}, "cloudflare"),
    ({"status": 200, "cf_mitigated": "challenge", "body": b"<html></html>"},
     "cloudflare"),
])
def test_a_refusing_host_is_blocked_by_host_not_an_empty_result(
        tmp_path, resp, challenge):
    ws, tid, _lib = blank_task(tmp_path)

    def t(url):
        return {"final_url": url, "hops": [url], "content_type": "text/html",
                **resp}
    pl, code = fetch(ws, tid, url=TOREX, transport=t, resolver=no_resolver)
    assert code == 2 and pl["refused"] == "blocked_by_host", pl
    assert pl["host"] == "www.torexsemi.com"
    assert pl["http_status"] == resp["status"] and pl["challenge"] == challenge
    assert "not 'no records found'" in pl["error"]
    assert "--part" in pl["fallback"]["notes"][0]
    task = researchlib.load_task(researchlib.root_of(ws), tid)
    att = task["attempts"][-1]
    assert att["kind"] == "blocked_by_host" and att["host"] == "www.torexsemi.com"
    assert not task["sources"]


def test_a_404_or_a_real_pdf_is_not_a_block(tmp_path):
    ws, tid, _lib = blank_task(tmp_path)

    def t404(url):
        return {"status": 404, "final_url": url, "hops": [url],
                "content_type": "text/html", "body": b"<html>not found"}
    pl, code = fetch(ws, tid, url=TOREX, transport=t404, resolver=no_resolver)
    assert code == 2 and "refused" not in pl and "HTTP 404" in pl["error"]
    assert researchlib.host_block({"status": 200, "body": PDF + b"cf-chl"},
                                  TOREX) is None


def test_a_real_akamai_fronted_page_is_not_a_block():
    page = (b"<html><head><link href='https://a.edgesuite.net/site.css'>"
            + b"<p>app note</p>" * 600)
    assert researchlib.host_block({"status": 200, "body": page}, TOREX) is None
    short = b"<html><script src='/bm-verify.js'></script></html>"
    assert researchlib.host_block({"status": 200, "body": short},
                                  TOREX)["challenge"] == "akamai"


def test_blocked_fetch_falls_back_to_the_distributor_copy(tmp_path):
    ws, tid, _lib = blank_task(tmp_path)
    asked = []

    def resolver(parts, vendor=None):
        asked.append((parts, vendor))
        return ([{"source": "lcsc", "part": "C5446", "mpn": "XC6206P332MR",
                  "url": LCSC_PDF}], ["digikey: skipped - no credentials"])
    pl, code = fetch(ws, tid, url=TOREX, tier="vendor-layout",
                     transport=transport_vendor_blocked, part="C5446",
                     resolver=resolver)
    assert code == 0, pl
    assert asked == [(["C5446"], "torex")]
    assert pl["source_used"] == "lcsc"
    assert pl["blocked"]["host"] == "www.torexsemi.com"
    assert pl["fallback"]["notes"] == ["digikey: skipped - no credentials"]
    src = pl["source"]
    assert src["url"] == LCSC_PDF and src["domain"] == "lcsc.com"
    assert src["tier"] == "vendor-layout"
    assert src["fallback"]["source"] == "lcsc"
    assert src["fallback"]["for_url"] == TOREX
    assert src["fallback"]["blocked"]["http_status"] == 403
    task = researchlib.load_task(researchlib.root_of(ws), tid)
    assert [a["kind"] for a in task["attempts"]] == ["blocked_by_host"]
    assert len(task["sources"]) == 1


def test_a_blocked_fallback_does_not_recurse_and_names_what_it_tried(tmp_path):
    ws, tid, _lib = blank_task(tmp_path)
    calls = []

    def resolver(parts, vendor=None):
        calls.append(parts)
        return ([{"source": "digikey", "part": "SMBJ5.0A", "mpn": "SMBJ5.0A",
                  "url": LITTELFUSE}], [])
    pl, code = fetch(ws, tid, url=TOREX, transport=transport_vendor_blocked,
                     part="SMBJ5.0A", resolver=resolver)
    assert code == 2 and pl["refused"] == "blocked_by_host"
    assert calls == [["SMBJ5.0A"]]
    assert pl["fallback"]["tried"][0]["source"] == "digikey"
    assert "blocked by host" in pl["fallback"]["tried"][0]["error"]
    task = researchlib.load_task(researchlib.root_of(ws), tid)
    assert [a["kind"] for a in task["attempts"]] == ["blocked_by_host"] * 2


def test_vendor_token_names_the_blocked_vendor_only():
    assert researchlib.vendor_token(TOREX) == "torex"
    assert researchlib.vendor_token(LITTELFUSE) == "littelfuse"
    assert researchlib.vendor_token(LCSC_PDF) is None
    assert researchlib.vendor_token("https://evil.example/x.pdf") is None


def test_fallback_tries_at_most_a_few_copies(tmp_path):
    ws, tid, _lib = blank_task(tmp_path)

    def resolver(parts, vendor=None):
        return ([{"source": "lcsc", "part": "C1", "url":
                  f"https://www.littelfuse.com/{i}.pdf"} for i in range(6)], [])
    pl, code = fetch(ws, tid, url=TOREX, transport=transport_vendor_blocked,
                     part="C1", resolver=resolver)
    assert code == 2 and len(pl["fallback"]["tried"]) == \
        researchlib.FALLBACK_TRIES


def test_fallback_parts_take_part_then_the_part_slot_keys():
    task = {"gap": {"kind": "part", "lcsc": "C5446", "mpn": "XC6206P332MR"}}
    assert researchlib.fallback_parts(task) == ["C5446", "XC6206P332MR"]
    assert researchlib.fallback_parts(task, "C5446") == ["C5446", "XC6206P332MR"]
    assert researchlib.fallback_parts({"gap": {"kind": "block"}}) == []


def test_distributor_datasheets_lcsc_exact_match_and_quiet_credential_skip(
        monkeypatch):
    for var in distributors.DIGIKEY_ENV + distributors.MOUSER_ENV:
        monkeypatch.delenv(var, raising=False)

    def search(p):
        return [{"lcsc": "C5446", "mpn": "XC6206P332MR-G", "brand": "Torex Semicon",
                 "datasheet": "https://www.lcsc.com/datasheet/"
                              "lcsc_datasheet_XC6206.pdf"},
                {"lcsc": "C9999", "mpn": "XC6206P302MR", "brand": "Torex Semicon",
                 "datasheet": "https://wmsc.lcsc.com/other.pdf"},
                {"lcsc": "C347376", "mpn": "XC6206P332MR", "brand": "UMW",
                 "datasheet": "https://wmsc.lcsc.com/umw.pdf"}]
    cands, notes = researchlib.distributor_datasheets(["C5446"], search=search)
    assert cands == [{"source": "lcsc", "part": "C5446", "lcsc": "C5446",
                      "mpn": "XC6206P332MR-G", "brand": "Torex Semicon",
                      "url": LCSC_PDF}]
    # by MPN: the second-source clone is dropped when the vendor is known
    cands, notes = researchlib.distributor_datasheets(
        ["XC6206P332MR"], search=search, vendor="torex")
    assert [c["lcsc"] for c in cands] == ["C5446"]
    assert "skipped 1 other-brand copy of XC6206P332MR (UMW)" in notes[0]
    cands, _n = researchlib.distributor_datasheets(["XC6206P332MR"],
                                                   search=search)
    assert [c["lcsc"] for c in cands] == ["C5446", "C347376"]
    # DigiKey/Mouser were asked by the MPN LCSC returned, and skipped quietly
    assert any(n.startswith("digikey: skipped") for n in notes)
    assert any(n.startswith("mouser: skipped") for n in notes)

    def boom(p):
        raise OSError("offline")
    cands, notes = researchlib.distributor_datasheets(["C5446"], search=boom)
    assert cands == [] and "lookup of C5446 failed" in notes[0]
    assert "no MPN known" in notes[1]

    def lookup(m):
        return {"results": {"mouser": {"hits": [
            {"mpn": "smbj5.0a", "manufacturer": "Littelfuse",
             "datasheet_url": "//www.littelfuse.com/x.pdf"},
            {"mpn": "SMBJ5.0A", "manufacturer": "Brightking",
             "datasheet_url": "https://bk/x.pdf"},
            {"mpn": "SMBJ5.0CA", "manufacturer": "Littelfuse",
             "datasheet_url": "https://other/x.pdf"}]}},
            "missing": {"digikey": "no creds"}}
    cands, notes = researchlib.distributor_datasheets(
        ["SMBJ5.0A"], search=lambda p: [], lookup=lookup, vendor="littelfuse")
    assert cands == [{"source": "mouser", "part": "SMBJ5.0A",
                      "mpn": "smbj5.0a", "brand": "Littelfuse",
                      "url": "https://www.littelfuse.com/x.pdf"}]
    assert "digikey: skipped - no creds" in notes


def test_an_all_blocked_slot_closes_blocked_and_coverage_names_it(tmp_path):
    ws, tid, lib = blank_task(tmp_path)
    for url in (TOREX, LITTELFUSE, TOREX):
        pl, code = fetch(ws, tid, url=url, transport=transport_vendor_blocked,
                         resolver=no_resolver)
        assert code == 2 and pl["refused"] == "blocked_by_host"
    stt, code = run(research, tmp_path, ["status", "--workspace", str(ws)])
    assert [b["host"] for b in stt["tasks"][0]["blocked_by"]] == [
        "www.littelfuse.com", "www.torexsemi.com"]
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid])
    assert code == 0 and cl["outcome"] == "blocked", cl
    torex = next(b for b in cl["blocked_by"] if "torex" in b["host"])
    assert torex["urls"] == 2 and torex["http_status"] == [403]
    assert torex["challenge"] == "akamai"
    assert "www.torexsemi.com (HTTP 403, akamai challenge)" in cl["reason"]
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert any("blocked by host" in d["what"] and "block:PWR" in d["what"]
               for d in st["decisions"])
    assert not (ws / "learnings" / "queue.yaml").exists()
    # the coverage report names the gap with its reason
    report, cov, code = coverage_report(tmp_path, ws, lib, label="cov2")
    assert code == 1 and cov["summary"]["research_blocked"] == 1
    gap = cov["gaps"][0]
    assert gap["research_blocked"]["task"] == tid
    assert any("research blocked by host: www.littelfuse.com HTTP 403" in r
               for r in gap["reasons"])
    assert next(s for s in cov["slots"] if s["id"] == "block:PWR")[
        "research_blocked"]["blocked_by"]
    assert any("refused every research fetch" in w for w in cov["warnings"])
    # open --all does not re-burn the cap on it; --slot reopens on purpose
    op, code = open_task(tmp_path, ws, report)
    assert code == 1 and op["opened"] == []
    assert "blocked by host" in op["skipped"][0]["reason"]
    op, code = open_task(tmp_path, ws, report, slot="block:PWR")
    assert code == 0 and len(op["opened"]) == 1
    # a later pass that closes otherwise clears the mark
    blocks = knowledgelib.workspace_research_blocks(ws)
    assert list(blocks) == ["block:PWR"]
    t2 = researchlib.load_task(researchlib.root_of(ws), op["opened"][0])
    t2.update({"status": "closed", "outcome": "abandoned",
               "closed": "9999-01-01T00:00:00"})
    researchlib.write_task(researchlib.root_of(ws), t2)
    assert knowledgelib.workspace_research_blocks(ws) == {}


def test_abandon_after_blocks_closes_blocked_but_a_source_keeps_it_abandoned(
        tmp_path, opened):
    (tmp_path / "b").mkdir()
    ws, tid, _lib = blank_task(tmp_path / "b")
    fetch(ws, tid, url=TOREX, transport=transport_vendor_blocked,
          resolver=no_resolver)
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws),
                                        "--task", tid, "--abandon", "--reason",
                                        "Torex refuses every fetch"])
    assert code == 0 and cl["outcome"] == "blocked"
    assert cl["reason"] == "Torex refuses every fetch"
    # the fixture task acquired a source, so a block alongside it is no
    # reason to call the slot blocked
    ws2, tid2 = opened["ws"], opened["tid"]
    fetch(ws2, tid2, url=TOREX, transport=transport_vendor_blocked,
          resolver=no_resolver)
    cl, code = run(research, tmp_path, ["close", "--workspace", str(ws2),
                                        "--task", tid2, "--abandon", "--reason",
                                        "slot needs no research after all"])
    assert code == 0 and cl["outcome"] == "abandoned"
