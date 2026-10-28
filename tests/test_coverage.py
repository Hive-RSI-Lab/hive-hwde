"""U13 - coverage contracts: levels, envelopes, maturity, the trigger.

Contracts pinned here (design decision 5a):
  1. Schema v2 lint: level/envelope/maturity/generalizes grammar, maturity
     governance (approved needs an approval block, proven needs evidence),
     generalizes targets exist and are more general, principle records carry
     no envelope, topology/family/part records must; bootstrap tolerance
     (missing v2 fields = draft) vs --strict; committed library lint-green;
     coverage checklists lint (one per topology, classes in vocab).
  2. THE acceptance: a synthetic workspace with a buck block at an operating
     point inside / outside record envelopes flips covered <-> gap
     deterministically; maturity floor enforced (draft never satisfies);
     checklist gating (no checklist = gap; unapproved checklist caps at
     provisional; min_level per class - a principle-only match is provisional).
  3. The mapping step: mapping_request emitted only when unmet classes
     remain; a valid schema-forced mapping folds in (via=mapping, sha
     logged); an invalid one (unknown record/slot, class the record lacks,
     schema violation) is refused whole with exit 2.
  4. Part slots: P3 datasheet extraction thin/empty = gap, present = covered;
     the block's operating point is lent to its parts.
  5. --prove: bring-up evidence upgrades every APPLIED record to proven with
     the evidence entry (idempotent, outside-envelope records untouched);
     refused without a bringup_passed event; post-write library still lints.
  6. Recipe wiring: full-run plans the P2/P3 coverage steps + the mapper;
     constraints_lint accepts operating_point on blocks/diff_pairs.
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

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import constraints_lint  # noqa: E402
import knowledge  # noqa: E402
import knowledgelib  # noqa: E402
import task_router  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def run_knowledge(tmp_path, argv):
    out = tmp_path / f"out{len(list(tmp_path.iterdir()))}.json"
    code = knowledge.main(argv + ["--out", str(out)])
    return json.loads(out.read_text(encoding="utf-8")), code


APPROVAL = {"by": "owner", "date": "2026-08-15"}

# Who may rule a committed record or checklist approved: the owner (the U14
# rulings, 2026-08-15), or perms on the owner's standing redirect (U22,
# 2026-10-04). A perms ruling says so and cites the redirect thread; it never
# claims to be the owner's own.
RULINGS = {"owner": "2026-08-15", "perms": "2026-10-04"}
# Later owner rulings, each pinned to the records it approved. 2026-10-08
# (#ai-ee thread 1791465870.806379): STDC14 for the STLINK-V3MINIE is the
# default debug header on new STM32 boards.
LATER_RULINGS = {"swd-debug-port-stm32-stdc14-default": ("owner", "2026-10-08")}
REDIRECT_THREAD = "1791102051.529859"
# The five U22 second-read records land verified, not approved: the rulings
# approved only the promoted set (design/u22-staged/second-reads.md).
U22_SECOND_READS = {"in-aggressor-separation", "in-leakage-symmetry-and-guarding",
                    "inamp-gain-pin-and-input-node-parasitics",
                    "in-path-symmetry-sets-cmrr", "in-bias-return-sizing"}


def assert_ruled(item: dict) -> None:
    a = item.get("approval") or {}
    rid = item["id"]
    if rid in LATER_RULINGS:
        assert (a.get("by"), a.get("date")) == LATER_RULINGS[rid], rid
        return
    assert a.get("by") in RULINGS, rid
    assert a.get("date") == RULINGS[a["by"]], rid
    if a["by"] == "perms":
        assert REDIRECT_THREAD in a.get("note", ""), rid


def write_record(d: Path, rid: str, **over) -> Path:
    rec = {
        "id": rid, "classes": ["emi"],
        "applies": {"topologies": ["buck"], "packages": [], "interfaces": [],
                    "parts": []},
        "rule": None, "prose": "A fact.",
        "sources": [{"file": "LEARNINGS.md"}],
        "status": "active", "origin": "test",
        "level": "topology", "envelope": {"vin_v": {"min": 3, "max": 8}},
        "maturity": "approved", "approval": dict(APPROVAL),
    }
    rec.update(over)
    for k in [k for k, v in rec.items() if v is ...]:
        del rec[k]                       # `...` = omit the key entirely
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{rid}.yaml"
    p.write_text(yaml.safe_dump(rec), encoding="utf-8")
    return p


def write_checklist(d: Path, cid: str, **over) -> Path:
    cl = {
        "id": cid, "kind": "coverage-checklist",
        "applies": {"topologies": [cid]},
        "requires": [{"class": "power-loop", "min_level": "topology"},
                     {"class": "emi", "min_level": "topology"}],
        "maturity": "approved", "approval": dict(APPROVAL),
        "origin": "test",
    }
    cl.update(over)
    for k in [k for k, v in cl.items() if v is ...]:
        del cl[k]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{cid}.yaml"
    p.write_text(yaml.safe_dump(cl), encoding="utf-8")
    return p


def make_ws(tmp_path, constraints=None, parts=None, extractions=None,
            history=None, name="ws") -> Path:
    """A synthetic workspace: kicad/constraints.json + parts/parts.json (+
    parts/<lcsc>.json extractions) + a minimal state.json."""
    ws = tmp_path / name
    (ws / "kicad").mkdir(parents=True)
    (ws / "log").mkdir()
    (ws / "state.json").write_text(json.dumps(
        {"board": "synth", "history": history or []}), encoding="utf-8")
    if constraints is not None:
        (ws / "kicad" / "constraints.json").write_text(
            json.dumps(constraints), encoding="utf-8")
    if parts is not None:
        (ws / "parts").mkdir()
        (ws / "parts" / "parts.json").write_text(json.dumps(parts),
                                                 encoding="utf-8")
        for lcsc, ext in (extractions or {}).items():
            (ws / "parts" / f"{lcsc}.json").write_text(json.dumps(ext),
                                                       encoding="utf-8")
    return ws


def buck_ws(tmp_path, vin=5, extra_op=None, name="ws", **kw) -> Path:
    op = {"vin_v": vin, "iout_a": 3, "edge_ns": 5, "switching_kind": "hard"}
    op.update(extra_op or {})
    return make_ws(tmp_path, constraints={
        "blocks": [{"topology": "buck", "block": "B3", "operating_point": op}]},
        name=name, **kw)


@pytest.fixture
def lib(tmp_path):
    """A two-record buck library + approved buck checklist."""
    recs = tmp_path / "recs"
    cls = tmp_path / "cls"
    write_record(recs, "r-hot-loop", classes=["power-loop"],
                 envelope={"edge_ns": {"max": 20},
                           "switching_kind": {"in": ["hard"]}})
    write_record(recs, "r-emi", classes=["emi"],
                 envelope={"vin_v": {"min": 3, "max": 8}})
    write_checklist(cls, "buck")
    return recs, cls


def cov(tmp_path, ws, lib, *extra):
    recs, cls = lib
    return run_knowledge(tmp_path, ["--coverage", "--workspace", str(ws),
                                    "--records-dir", str(recs),
                                    "--checklists-dir", str(cls), *extra])


def slot(payload, sid):
    return next(s for s in payload["slots"] if s["id"] == sid)


# ---------------------------------------------------------------------------
# 1. schema v2 lint
# ---------------------------------------------------------------------------
def test_committed_library_lint_green_and_checklists_present():
    assert knowledgelib.validate() == []
    ids = {c["id"] for c in knowledgelib.load_checklists()}
    # U14: buck approved, plus the two in-fleet interface checklists
    assert {"buck", "100base-tx", "usb-fs"} <= ids
    for c in knowledgelib.load_checklists():
        assert knowledgelib.record_maturity(c) == "approved", c["id"]
        assert_ruled(c)


def test_committed_records_are_strict_green_after_the_u14_backfill():
    """U14 flipped this from the bootstrap tolerance test: every committed
    record now carries level + maturity, so --strict must be clean."""
    assert knowledgelib.validate() == []
    assert knowledgelib.validate(strict=True) == []
    records = knowledgelib.load_records()
    assert records
    for r in records:
        rid = r["id"]
        assert knowledgelib.record_level(r) in knowledgelib.LEVELS, rid
        # every record landed approved (owner, U14; or perms on the owner's
        # redirect, U22), except the U22 second reads, which land verified
        # because the rulings approved only the promoted set; proven only
        # ever arrives via --prove, which writes its own evidence
        if rid in U22_SECOND_READS:
            assert knowledgelib.record_maturity(r) == "verified", rid
        else:
            assert knowledgelib.record_maturity(r) in (
                "approved", "proven"), rid
        if r["maturity"] == "approved":
            assert_ruled(r)
            # the ruling itself: what does this rule scale with?
            assert len(r["approval"].get("note", "")) > 80, rid
        if r["level"] == "principle":
            assert not r.get("envelope"), rid
        else:
            assert r.get("envelope"), rid


def test_yaml_implicit_dates_are_a_lint_problem_not_a_crash(tmp_path):
    """An unquoted 2026-08-15 loads as a datetime.date - it must surface as a
    named problem, not a TypeError out of the JSON writer (U14 build)."""
    d = tmp_path / "r"
    write_record(d, "r-dated")
    p = d / "r-dated.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "date: '2026-08-15'", "date: 2026-08-15"), encoding="utf-8")
    problems = knowledgelib.validate(d, tmp_path / "nocl")
    assert any("non-JSON value" in x and "quote it" in x for x in problems)
    payload, code = run_knowledge(tmp_path, ["--validate", "--records-dir",
                                             str(d)])
    assert code == 1 and payload["status"] == "problems"


def test_strict_cli_flag(tmp_path):
    d = tmp_path / "r"
    write_record(d, "r-ok")
    write_record(d, "r-legacy", level=..., envelope=..., maturity=...,
                 approval=...)
    payload, code = run_knowledge(tmp_path, ["--validate", "--records-dir",
                                             str(d)])
    assert code == 0, payload["problems"]
    payload, code = run_knowledge(tmp_path, ["--validate", "--strict",
                                             "--records-dir", str(d)])
    assert code == 1
    assert any("r-legacy" in p and "strict" in p for p in payload["problems"])


@pytest.mark.parametrize("over,needle", [
    ({"level": "flavour"}, "level"),
    ({"maturity": "gold"}, "maturity"),
    ({"envelope": {"vin": {"min": 1}}}, "unit-suffixed"),
    ({"envelope": {"vin_v": {"lo": 1}}}, "min/max"),
    ({"envelope": {"vin_v": {"min": 9, "max": 1}}}, "min > max"),
    ({"envelope": {"vin_v": {"min": "one"}}}, "number"),
    ({"envelope": {"switching_kind": {"in": []}}}, "categorical"),
    ({"envelope": {"switching_kind": {"min": 1}}}, "categorical"),
    ({"level": "principle"}, "carries no envelope"),
    ({"level": "family", "envelope": {}}, "requires a non-empty envelope"),
    ({"level": "part", "envelope": ...}, "requires a non-empty envelope"),
    ({"maturity": "approved", "approval": ...}, "approval block"),
    ({"maturity": "proven"}, "evidence"),
    ({"generalizes": ["nope"]}, "unknown record"),
    ({"generalizes": ["r-a"]}, "generalizes itself"),
    ({"approval": {"by": "owner", "date": "yesterday"}}, "date"),
])
def test_lint_rejects_bad_v2_records(tmp_path, over, needle):
    d = tmp_path / "r"
    write_record(d, "r-a", **over)
    problems = knowledgelib.validate(d, tmp_path / "nocl")
    assert problems and any(needle in p for p in problems), problems


def test_lint_generalizes_must_point_at_a_more_general_record(tmp_path):
    d = tmp_path / "r"
    write_record(d, "r-principle", level="principle", envelope=...)
    write_record(d, "r-topo", generalizes=["r-principle"])
    assert knowledgelib.validate(d, tmp_path / "nocl") == []
    # a topology record "generalizing" a part record is backwards
    write_record(d, "r-part", level="part", generalizes=[])
    write_record(d, "r-topo", generalizes=["r-part"])
    problems = knowledgelib.validate(d, tmp_path / "nocl")
    assert any("not more general" in p for p in problems), problems


def test_instance_level_envelope_optional_and_proven_needs_evidence(tmp_path):
    d = tmp_path / "r"
    write_record(d, "r-inst", level="instance", envelope=..., maturity="proven",
                 evidence=[{"workspace": "boards/x", "board": "x",
                            "event": "bringup_passed", "date": "2026-09-01"}])
    assert knowledgelib.validate(d, tmp_path / "nocl") == []


@pytest.mark.parametrize("over,needle", [
    ({"kind": "checklist"}, "kind"),
    ({"requires": []}, "requires"),
    ({"requires": [{"class": "vibes", "min_level": "topology"}]}, "CLASSES"),
    ({"requires": [{"class": "emi", "min_level": "deep"}]}, "min_level"),
    ({"applies": {}}, "no slot can ever use"),
    ({"maturity": "approved", "approval": ...}, "approval block"),
])
def test_checklist_lint_rejects(tmp_path, over, needle):
    cls = tmp_path / "c"
    write_checklist(cls, "buck", **over)
    problems = knowledgelib.validate(tmp_path / "norecs", cls)
    assert problems and any(needle in p for p in problems), problems


def test_checklist_one_per_topology(tmp_path):
    cls = tmp_path / "c"
    write_checklist(cls, "buck")
    write_checklist(cls, "buck-two", applies={"topologies": ["buck"]})
    problems = knowledgelib.validate(tmp_path / "norecs", cls)
    assert any("already has checklist" in p for p in problems), problems


def test_envelope_contains_semantics():
    ec = knowledgelib.envelope_contains
    env = {"vin_v": {"min": 3, "max": 8}, "switching_kind": {"in": ["hard"]}}
    assert ec(env, {"vin_v": 5, "switching_kind": "hard"})["verdict"] == "inside"
    assert ec(env, {"vin_v": 5, "switching_kind": "HARD"})["verdict"] == "inside"
    r = ec(env, {"vin_v": 12, "switching_kind": "hard"})
    assert r["verdict"] == "outside" and r["outside_dims"] == ["vin_v"]
    r = ec(env, {"vin_v": 5})
    assert r["verdict"] == "unknown" and r["unknown_dims"] == ["switching_kind"]
    # outside beats unknown; a non-numeric value for a numeric dim = unknown
    assert ec(env, {"vin_v": 12})["verdict"] == "outside"
    assert ec(env, {"vin_v": "twelve", "switching_kind": "hard"})["verdict"] \
        == "unknown"
    assert ec(None, {"vin_v": 5})["verdict"] == "n/a"
    assert ec({}, {})["verdict"] == "n/a"
    # dims the envelope does not mention are ignored
    assert ec({"vin_v": {"max": 30}}, {"vin_v": 12, "iout_a": 99})["verdict"] \
        == "inside"


# ---------------------------------------------------------------------------
# 2. THE acceptance: inside/outside flips covered <-> gap; floors; checklists
# ---------------------------------------------------------------------------
def test_operating_point_inside_envelopes_is_covered(tmp_path, lib):
    ws = buck_ws(tmp_path, vin=5)
    payload, code = cov(tmp_path, ws, lib, "--phase", "P2")
    assert code == 0 and payload["status"] == "pass", payload
    b = slot(payload, "block:B3")
    assert b["verdict"] == "covered"
    assert {c["class"]: c["verdict"] for c in b["classes"]} == {
        "power-loop": "covered", "emi": "covered"}
    assert payload["gaps"] == [] and payload["mapping_request"] is None
    assert payload["phase"] == "P2" and payload["maturity_floor"] == "approved"


def test_operating_point_outside_an_envelope_flips_to_gap(tmp_path, lib):
    ws = buck_ws(tmp_path, vin=12)     # r-emi's envelope is 3..8 V
    payload, code = cov(tmp_path, ws, lib)
    assert code == 1 and payload["status"] == "gaps"
    b = slot(payload, "block:B3")
    assert b["verdict"] == "gap"
    emi = next(c for c in b["classes"] if c["class"] == "emi")
    assert emi["verdict"] == "gap"
    assert emi["records"][0]["blocker"] == "outside"
    assert emi["records"][0]["outside_dims"] == ["vin_v"]
    # the gap entry is a research task spec
    g = payload["gaps"][0]
    assert g["slot"] == "block:B3" and g["topology"] == "buck"
    assert g["missing"] == [{"class": "emi", "min_level": "topology"}]
    assert g["operating_point"]["vin_v"] == 12
    assert "r-emi" in g["related_records"]
    assert "populate emi" in g["task"]
    # the same workspace, dims back inside -> covered again (deterministic)
    ws2 = buck_ws(tmp_path, vin=6, name="ws2")
    payload2, code2 = cov(tmp_path, ws2, lib)
    assert code2 == 0 and slot(payload2, "block:B3")["verdict"] == "covered"


def test_undeclared_dim_is_provisional_not_covered(tmp_path, lib):
    ws = make_ws(tmp_path, constraints={"blocks": [
        {"topology": "buck", "block": "B3",
         "operating_point": {"vin_v": 5, "edge_ns": 5}}]})   # no switching_kind
    payload, code = cov(tmp_path, ws, lib)
    assert code == 0                       # provisional is not a gap
    b = slot(payload, "block:B3")
    assert b["verdict"] == "provisional"
    pl = next(c for c in b["classes"] if c["class"] == "power-loop")
    assert pl["records"][0]["blocker"] == "envelope-unknown"
    assert pl["records"][0]["unknown_dims"] == ["switching_kind"]


def test_maturity_floor_draft_never_satisfies(tmp_path):
    recs, cls = tmp_path / "recs", tmp_path / "cls"
    write_record(recs, "r-hot-loop", classes=["power-loop"], maturity="draft",
                 approval=...)
    write_record(recs, "r-emi", classes=["emi"], maturity="verified",
                 approval=...)
    write_checklist(cls, "buck")
    ws = buck_ws(tmp_path, vin=5)
    payload, code = cov(tmp_path, ws, (recs, cls))
    b = slot(payload, "block:B3")
    assert b["verdict"] == "provisional"
    assert all(c["records"][0]["blocker"] == "maturity-below-floor"
               for c in b["classes"])
    # lowering the floor to verified covers emi but not the draft power-loop
    payload, _ = cov(tmp_path, ws, (recs, cls), "--maturity-floor", "verified")
    verdicts = {c["class"]: c["verdict"] for c in slot(payload, "block:B3")["classes"]}
    assert verdicts == {"power-loop": "provisional", "emi": "covered"}
    # floor draft = everything counts (what --prove uses)
    payload, _ = cov(tmp_path, ws, (recs, cls), "--maturity-floor", "draft")
    assert slot(payload, "block:B3")["verdict"] == "covered"
    # a bad floor is a usage error
    _, code = cov(tmp_path, ws, (recs, cls), "--maturity-floor", "gold")
    assert code == 2


def test_legacy_record_without_level_is_provisional(tmp_path):
    """Pre-backfill records (no level/envelope/maturity) can never satisfy -
    they read as level None / draft, and the blocker says why."""
    recs, cls = tmp_path / "recs", tmp_path / "cls"
    write_record(recs, "r-old", classes=["power-loop", "emi"], level=...,
                 envelope=..., maturity=..., approval=...)
    write_checklist(cls, "buck")
    payload, _ = cov(tmp_path, buck_ws(tmp_path), (recs, cls),
                     "--maturity-floor", "draft")
    b = slot(payload, "block:B3")
    assert b["verdict"] == "provisional"
    assert b["classes"][0]["records"][0]["blocker"] == "level-unknown"


def test_no_checklist_is_a_gap_whose_task_is_to_produce_one(tmp_path, lib):
    recs, _ = lib
    ws = buck_ws(tmp_path)
    payload, code = cov(tmp_path, ws, (recs, tmp_path / "nocl"))
    assert code == 1
    b = slot(payload, "block:B3")
    assert b["verdict"] == "gap" and b["checklist"] is None
    assert any("no coverage checklist" in r for r in b["gap_reasons"])
    g = payload["gaps"][0]
    assert g["missing"] == [{"class": "coverage-checklist", "min_level": None}]
    assert "produce its coverage checklist" in g["task"]
    # informational: what we already hold is still listed by class
    assert {c["class"] for c in b["classes"]} == {"power-loop", "emi"}


def test_unapproved_checklist_caps_at_provisional(tmp_path, lib):
    recs, _ = lib
    cls = tmp_path / "cls-draft"
    write_checklist(cls, "buck", maturity="draft", approval=...)
    payload, code = cov(tmp_path, buck_ws(tmp_path), (recs, cls))
    b = slot(payload, "block:B3")
    assert code == 0 and b["verdict"] == "provisional"
    assert b["checklist"] == {"id": "buck", "maturity": "draft",
                              "floor_met": False}
    assert any("< floor" in r for r in b["gap_reasons"])


def test_min_level_makes_a_principle_only_match_provisional(tmp_path):
    """A principle-level record in the right class narrows research to the
    application delta - it never covers a topology-level requirement."""
    recs, cls = tmp_path / "recs", tmp_path / "cls"
    write_record(recs, "loop-principle", classes=["power-loop"],
                 level="principle", envelope=...,
                 applies={"topologies": ["buck", "boost"], "packages": [],
                          "interfaces": [], "parts": []})
    write_record(recs, "r-emi", classes=["emi"])
    write_checklist(cls, "buck")
    payload, code = cov(tmp_path, buck_ws(tmp_path), (recs, cls))
    b = slot(payload, "block:B3")
    pl = next(c for c in b["classes"] if c["class"] == "power-loop")
    assert pl["verdict"] == "provisional"
    assert pl["records"][0]["blocker"] == "level-below-min"
    assert b["verdict"] == "provisional" and code == 0
    # ... but a checklist asking only for principle level is satisfied
    write_checklist(cls, "buck", requires=[
        {"class": "power-loop", "min_level": "principle"},
        {"class": "emi", "min_level": "topology"}])
    payload, code = cov(tmp_path, buck_ws(tmp_path, name="ws2"), (recs, cls))
    assert slot(payload, "block:B3")["verdict"] == "covered" and code == 0


def test_gap_spec_names_principle_parents(tmp_path):
    recs, cls = tmp_path / "recs", tmp_path / "cls"
    write_record(recs, "loop-principle", classes=["power-loop"],
                 level="principle", envelope=...,
                 applies={"topologies": ["boost"], "packages": [],
                          "interfaces": [], "parts": []})
    write_record(recs, "r-emi", classes=["emi"])
    write_checklist(cls, "buck")
    payload, code = cov(tmp_path, buck_ws(tmp_path), (recs, cls))
    assert code == 1
    g = payload["gaps"][0]
    assert g["missing"] == [{"class": "power-loop", "min_level": "topology"}]
    assert g["principle_parents"] == ["loop-principle"]
    assert "application delta" in g["task"]


def test_interface_slot_from_diff_pairs_and_no_slots_warning(tmp_path, lib):
    recs, cls = lib
    write_checklist(cls, "usb", applies={"interfaces": ["usb"]},
                    requires=[{"class": "esd", "min_level": "topology"}])
    write_record(recs, "usb-esd", classes=["esd"],
                 applies={"topologies": [], "packages": [], "interfaces": ["usb"],
                          "parts": []},
                 envelope={"impedance_ohm": {"min": 85, "max": 95}})
    ws = make_ws(tmp_path, constraints={"diff_pairs": [
        {"p": "/USB_DP", "n": "/USB_DM", "base": "USB", "impedance_ohm": 90}]})
    payload, code = cov(tmp_path, ws, (recs, cls))
    s = slot(payload, "interface:usb")
    assert code == 0 and s["verdict"] == "covered"
    assert s["operating_point"] == {"impedance_ohm": 90}
    # a design that declares nothing: zero slots, a warning, exit 0
    ws0 = make_ws(tmp_path, constraints={"power": []}, name="ws0")
    payload, code = cov(tmp_path, ws0, (recs, cls))
    assert code == 0 and payload["summary"]["slots"] == 0
    assert any("no slots" in w for w in payload["warnings"])


def test_bad_operating_point_dims_are_warned(tmp_path, lib):
    ws = make_ws(tmp_path, constraints={"blocks": [
        {"topology": "buck", "block": "B3",
         "operating_point": {"vin": 12, "vin_v": "twelve"}}]})
    payload, _ = cov(tmp_path, ws, lib)
    assert any("vin:" in w and "unit-suffixed" in w for w in payload["warnings"])
    assert any("vin_v" in w and "number" in w for w in payload["warnings"])


def test_coverage_cli_contract(tmp_path, lib):
    recs, cls = lib
    payload, code = run_knowledge(tmp_path, ["--coverage", "--records-dir",
                                             str(recs)])
    assert code == 2 and "requires --workspace" in payload["error"]
    payload, code = run_knowledge(tmp_path, ["--coverage", "--workspace",
                                             str(tmp_path / "nope")])
    assert code == 2


# ---------------------------------------------------------------------------
# 3. the mapping step (schema-forced agent output)
# ---------------------------------------------------------------------------
def test_mapping_request_only_when_classes_unmet(tmp_path, lib):
    recs, cls = lib
    write_record(recs, "loop-generic", classes=["power-loop"],
                 applies={"topologies": ["sync-buck"], "packages": [],
                          "interfaces": [], "parts": []},
                 envelope={"edge_ns": {"max": 50}})
    write_checklist(cls, "buck", requires=[
        {"class": "power-loop", "min_level": "topology"},
        {"class": "emi", "min_level": "topology"},
        {"class": "thermal", "min_level": "topology"}])
    ws = buck_ws(tmp_path)
    payload, code = cov(tmp_path, ws, (recs, cls))
    assert code == 1
    req = payload["mapping_request"]
    assert req is not None
    assert req["schema"]["title"] == knowledgelib.MAPPING_SCHEMA["title"]
    assert req["slots"][0]["id"] == "block:B3"
    assert req["slots"][0]["unmet_classes"] == ["thermal"]
    assert {c["id"] for c in req["candidates"]} >= {"loop-generic", "r-emi"}
    assert "may NOT" in req["instructions"] or "do NOT" in req["instructions"]


def test_valid_mapping_folds_in_and_is_logged(tmp_path, lib):
    recs, cls = lib
    write_record(recs, "thermal-generic", classes=["thermal"],
                 applies={"topologies": ["sync-buck"], "packages": [],
                          "interfaces": [], "parts": []},
                 envelope={"iout_a": {"max": 10}})
    write_checklist(cls, "buck", requires=[
        {"class": "power-loop", "min_level": "topology"},
        {"class": "emi", "min_level": "topology"},
        {"class": "thermal", "min_level": "topology"}])
    ws = buck_ws(tmp_path)
    payload, code = cov(tmp_path, ws, (recs, cls))
    assert code == 1 and slot(payload, "block:B3")["verdict"] == "gap"
    mp = ws / "log" / "coverage-mapping-P2.json"
    mp.write_text(json.dumps({"mappings": [
        {"record": "thermal-generic", "slot": "block:B3", "class": "thermal",
         "why": "sync-buck is a buck; the thermal fact does not depend on it"}],
        "note": "one edge"}), encoding="utf-8")
    payload, code = cov(tmp_path, ws, (recs, cls), "--mapping", str(mp),
                        "--phase", "P2")
    assert code == 0, payload
    b = slot(payload, "block:B3")
    assert b["verdict"] == "covered"
    th = next(c for c in b["classes"] if c["class"] == "thermal")
    assert th["records"][0]["via"] == "mapping" and th["records"][0]["satisfies"]
    assert payload["mapping_applied"]["edges"] == 1
    assert payload["mapping_applied"]["sha256"] == \
        knowledgelib.sha256_file(mp)
    assert payload["mapping_request"] is None


@pytest.mark.parametrize("mapping,needle", [
    ({"mappings": [{"record": "ghost", "slot": "block:B3", "class": "emi",
                    "why": "x"}]}, "unknown record"),
    ({"mappings": [{"record": "r-emi", "slot": "block:B9", "class": "emi",
                    "why": "x"}]}, "unknown slot"),
    ({"mappings": [{"record": "r-emi", "slot": "block:B3",
                    "class": "power-loop", "why": "x"}]}, "does not carry"),
    ({"mappings": [{"record": "r-emi", "slot": "block:B3", "class": "emi",
                    "why": "x", "confidence": 0.9}]}, "schema"),
    ({"mappings": [{"record": "r-emi", "slot": "block:B3", "class": "emi",
                    "why": "x", "covered": True}]}, "schema"),
    ({"edges": []}, "schema"),
])
def test_invalid_mapping_is_refused_whole(tmp_path, lib, mapping, needle):
    ws = buck_ws(tmp_path, vin=12)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps(mapping), encoding="utf-8")
    payload, code = cov(tmp_path, ws, lib, "--mapping", str(mp))
    assert code == 2 and payload["status"] == "error"
    assert needle in payload["error"], payload["error"]


def test_mapping_cannot_map_a_draft_record(tmp_path, lib):
    recs, cls = lib
    write_record(recs, "r-shelved", classes=["emi"], status="draft")
    ws = buck_ws(tmp_path, vin=12)
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"mappings": [
        {"record": "r-shelved", "slot": "block:B3", "class": "emi",
         "why": "x"}]}), encoding="utf-8")
    payload, code = cov(tmp_path, ws, (recs, cls), "--mapping", str(mp))
    assert code == 2 and "not active" in payload["error"]


# ---------------------------------------------------------------------------
# 4. part slots (P3 datasheet layout extraction)
# ---------------------------------------------------------------------------
def test_part_slot_layout_extraction_present_thin_missing(tmp_path, lib):
    parts = {"parts": [
        {"ref_prefix_hint": "U", "lcsc": "C1", "mpn": "REG-A", "block": "B3",
         "package": "SO-8-EP"},
        {"ref_prefix_hint": "U", "lcsc": "C2", "mpn": "REG-B", "block": "B3"},
        {"ref_prefix_hint": "U", "lcsc": "C3", "mpn": "REG-C", "block": "B3"},
        {"ref_prefix_hint": "C", "lcsc": "C4", "mpn": "CAP"},   # not a slot
    ]}
    ext = {"C1": {"layout_notes": ["Keep the hot loop tight.",
                                   "Thermal vias under the EP."]},
           "C2": {"layout_notes": ["one note only"]}}
    ws = buck_ws(tmp_path, parts=parts, extractions=ext)
    payload, code = cov(tmp_path, ws, lib, "--phase", "P3")
    assert code == 1
    ids = {s["id"] for s in payload["slots"]}
    assert {"part:C1", "part:C2", "part:C3"} <= ids and "part:C4" not in ids
    assert slot(payload, "part:C1")["verdict"] == "covered"
    assert slot(payload, "part:C1")["datasheet_layout"] == "present"
    assert slot(payload, "part:C2")["verdict"] == "gap"
    assert slot(payload, "part:C2")["datasheet_layout"] == "thin"
    assert slot(payload, "part:C3")["verdict"] == "gap"
    assert slot(payload, "part:C3")["datasheet_layout"] == "missing"
    # parts inherit their block's operating point
    assert slot(payload, "part:C1")["operating_point"]["vin_v"] == 5
    gap_ids = {g["slot"] for g in payload["gaps"]}
    assert gap_ids == {"part:C2", "part:C3"}
    g = next(g for g in payload["gaps"] if g["slot"] == "part:C2")
    assert g["mpn"] == "REG-B" and "thin" in g["reasons"][0]


def test_part_level_record_covers_a_part_without_extraction(tmp_path, lib):
    recs, cls = lib
    write_record(recs, "reg-c-layout", classes=["thermal-via"], level="part",
                 applies={"topologies": [], "packages": [], "interfaces": [],
                          "parts": ["REG-C"]},
                 envelope={"vin_v": {"max": 30}})
    parts = {"parts": [{"ref_prefix_hint": "U", "lcsc": "C3", "mpn": "REG-C",
                        "block": "B3"}]}
    payload, code = cov(tmp_path, buck_ws(tmp_path, parts=parts), (recs, cls))
    s = slot(payload, "part:C3")
    assert code == 0 and s["verdict"] == "covered"
    assert s["classes"][0]["records"][0]["id"] == "reg-c-layout"
    # select() keys parts too now
    hits = knowledgelib.select(knowledgelib.load_records(recs), parts=["reg-c"])
    assert [r["id"] for r in hits] == ["reg-c-layout"]


# ---------------------------------------------------------------------------
# 5. --prove: bring-up evidence -> proven
# ---------------------------------------------------------------------------
BRINGUP = [{"ts": "2026-09-01T10:00:00", "event": "bringup_passed",
            "msg": "rails in bounds"}]


def test_prove_refuses_without_bringup_evidence(tmp_path, lib):
    recs, cls = lib
    ws = buck_ws(tmp_path)
    payload, code = run_knowledge(tmp_path, [
        "--prove", "--workspace", str(ws), "--records-dir", str(recs),
        "--checklists-dir", str(cls)])
    assert code == 1 and "bringup_passed" in payload["problems"][0]
    assert all(knowledgelib.record_maturity(r) == "approved"
               for r in knowledgelib.load_records(recs))


def test_prove_upgrades_applied_records_only_and_is_idempotent(tmp_path, lib):
    recs, cls = lib
    # r-emi's envelope is 3..8 V: at 12 V it does NOT apply -> stays approved
    ws = buck_ws(tmp_path, vin=12, history=BRINGUP)
    args = ["--prove", "--workspace", str(ws), "--records-dir", str(recs),
            "--checklists-dir", str(cls)]
    payload, code = run_knowledge(tmp_path, args + ["--dry-run"])
    assert code == 0 and payload["dry_run"]
    assert [u["id"] for u in payload["upgraded"]] == ["r-hot-loop"]
    assert all(knowledgelib.record_maturity(r) == "approved"
               for r in knowledgelib.load_records(recs))     # dry = no write
    payload, code = run_knowledge(tmp_path, args)
    assert code == 0
    assert payload["upgraded"] == [{"id": "r-hot-loop", "from": "approved",
                                    "to": "proven"}]
    by_id = {r["id"]: r for r in knowledgelib.load_records(recs)}
    assert by_id["r-hot-loop"]["maturity"] == "proven"
    ev = by_id["r-hot-loop"]["evidence"]
    assert len(ev) == 1 and ev[0]["event"] == "bringup_passed"
    assert ev[0]["date"] == "2026-09-01" and ev[0]["board"] == "synth"
    assert by_id["r-emi"]["maturity"] == "approved" and "evidence" not in by_id["r-emi"]
    # the prose survived the targeted edit, and the library still lints
    text = (recs / "r-hot-loop.yaml").read_text(encoding="utf-8")
    assert "prose: A fact." in text
    assert knowledgelib.validate(recs, cls) == []
    # idempotent
    payload, code = run_knowledge(tmp_path, args)
    assert code == 0 and payload["upgraded"] == [] \
        and payload["unchanged"] == ["r-hot-loop"]
    # a SECOND board adds evidence, no re-upgrade
    ws2 = buck_ws(tmp_path, vin=12, history=BRINGUP, name="ws2")
    payload, code = run_knowledge(tmp_path, [
        "--prove", "--workspace", str(ws2), "--records-dir", str(recs),
        "--checklists-dir", str(cls)])
    assert payload["upgraded"] == [] and payload["evidence_added"] == ["r-hot-loop"]
    ev = {r["id"]: r for r in knowledgelib.load_records(recs)}["r-hot-loop"]["evidence"]
    assert len(ev) == 2 and {e["workspace"] for e in ev} == {
        ws.as_posix(), ws2.as_posix()}
    # proven satisfies coverage at the default floor
    payload, code = cov(tmp_path, buck_ws(tmp_path, vin=5, name="ws3"), (recs, cls))
    assert code == 0 and slot(payload, "block:B3")["verdict"] == "covered"


def test_prove_reaches_below_approved(tmp_path):
    """Reality outranks review: a verified (second-reader) record that
    applied to a bring-up-passed board goes straight to proven."""
    recs, cls = tmp_path / "recs", tmp_path / "cls"
    write_record(recs, "r-v", classes=["emi"], maturity="verified", approval=...)
    write_checklist(cls, "buck")
    ws = buck_ws(tmp_path, history=BRINGUP)
    payload, code = run_knowledge(tmp_path, [
        "--prove", "--workspace", str(ws), "--records-dir", str(recs),
        "--checklists-dir", str(cls)])
    assert code == 0 and payload["upgraded"] == [
        {"id": "r-v", "from": "verified", "to": "proven"}]
    assert knowledgelib.validate(recs, cls) == []


def test_prove_targeted_edit_helpers():
    txt = "id: x\nmaturity: approved\nprose: >\n  a: b\n"
    out = knowledgelib._set_top_key(txt, "maturity", "proven")
    assert out == "id: x\nmaturity: proven\nprose: >\n  a: b\n"
    out = knowledgelib._set_top_key("id: x\n", "maturity", "proven")
    assert out.endswith("maturity: proven\n")
    ev = {"workspace": "boards/b", "board": "b", "event": "bringup_passed",
          "date": "2026-09-01"}
    out = knowledgelib._append_evidence("id: x\n", ev)
    assert yaml.safe_load(out)["evidence"] == [ev]
    out2 = knowledgelib._append_evidence(out + "origin: t\n", dict(ev, board="c"))
    data = yaml.safe_load(out2)
    assert [e["board"] for e in data["evidence"]] == ["b", "c"]
    assert data["origin"] == "t"


# ---------------------------------------------------------------------------
# 6. recipe + lint wiring
# ---------------------------------------------------------------------------
def test_full_run_plans_the_coverage_steps(tmp_path):
    tasks = task_router.load_tasks()
    steps = tasks["verbs"]["full-run"]["steps"]
    dos = [s["do"] for s in steps if "do" in s]
    assert any("knowledge.py --coverage" in d and "--phase P2" in d for d in dos)
    assert any("knowledge.py --coverage" in d and "--phase P3" in d for d in dos)
    agents = [s for s in steps if "agent" in s]
    assert any(s["agent"] == "coverage-mapper" and s.get("optional")
               for s in agents)
    assert (SKILL / "agents" / "coverage-mapper.md").is_file()
    assert task_router.validate_registry() == []
    # the coverage steps precede the first gate
    kinds = [("do" if "do" in s else "gate" if "gate" in s else "x") for s in steps]
    first_gate = kinds.index("gate")
    assert all("coverage" not in (s.get("do") or "")
               for s in steps[first_gate:])


def test_constraints_lint_accepts_operating_point(tmp_path):
    doc = {"blocks": [{"topology": "buck", "block": "B3",
                       "operating_point": {"vin_v": 12, "switching_kind": "hard",
                                           "rectifier_kind": "sync"}}],
           "diff_pairs": [{"p": "/A_P", "n": "/A_N", "base": "eth",
                           "operating_point": {"rate_mbps": 100}}]}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    vs, _ = constraints_lint.lint_file(p)
    errs = [v for v in vs if v["severity"] == "error"]
    assert errs == [], errs
    # a nested map is not an operating point
    doc["blocks"][0]["operating_point"] = {"vin_v": {"min": 1}}
    p.write_text(json.dumps(doc), encoding="utf-8")
    vs, _ = constraints_lint.lint_file(p)
    assert any(v["kind"] == "bad_type" and v["severity"] == "error" for v in vs)


def test_prompt_block_and_view_carry_the_maturity_tag():
    recs = knowledgelib.load_records()
    block = knowledgelib.prompt_block(knowledgelib.select(recs, topologies=["buck"]))
    # post-U14 every committed record is owner-approved and levelled
    assert "(topology/approved)" in block
    assert "(principle/approved)" in block
    assert "/draft)" not in block
    view = knowledgelib.render_topology(recs, "buck")
    assert "level/maturity" in view


# ---------------------------------------------------------------------------
# 7. U14 - the backfilled library against real designs
# ---------------------------------------------------------------------------
# The eight operating-point dims a buck block must declare to reach `covered`
# against the approved checklist (architect.md documents the same list).
BUCK_OP = {"vin_v": 12, "vout_v": 5, "iout_a": 3, "pdiss_w": 0.8,
           "board_layers": 4, "switching_kind": "hard",
           "rectifier_kind": "sync", "integration_kind": "integrated-fet",
           "source_kind": "usb-pd"}


def real_ws(tmp_path, op=None, diff_pairs=None, name="ws") -> Path:
    """A workspace checked against the COMMITTED record library."""
    c = {"blocks": [{"topology": "buck", "block": "B1",
                     "name": "U1 integrated sync buck",
                     "operating_point": dict(op if op is not None else BUCK_OP)}]}
    if diff_pairs is not None:
        c["diff_pairs"] = diff_pairs
    return make_ws(tmp_path, constraints=c, name=name)


def test_committed_library_covers_a_buck_block_at_a_real_operating_point(tmp_path):
    """U14 acceptance: the approved records + approved checklist cover every
    class of a real buck block - the pd-trigger-shaped fixture the plan asks
    for, at sbuck/usb-buck's operating point (pd-trigger itself has no buck)."""
    rep = knowledgelib.coverage(real_ws(tmp_path))
    s = next(x for x in rep["slots"] if x["id"] == "block:B1")
    assert s["verdict"] == "covered", [c for c in s["classes"]
                                       if c["verdict"] != "covered"]
    assert {c["class"] for c in s["classes"]} == {
        "selection", "power-loop", "emi", "feedback", "decoupling",
        "return-path", "thermal-via", "inrush", "sequencing",
        "constraints-emission"}
    assert rep["summary"]["gap"] == 0
    # the two principle records satisfy their own rows, and only those rows
    by_class = {c["class"]: c for c in s["classes"]}
    for cls, rid in (("sequencing", "buck-en-softstart-sequencing"),
                     ("constraints-emission", "buck-constraints-emission")):
        ev = next(e for e in by_class[cls]["records"] if e["id"] == rid)
        assert ev["satisfies"] and ev["level"] == "principle"
    # ... while the same principle level is BELOW the min for power-loop
    hot = next(e for e in by_class["power-loop"]["records"]
               if e["id"] == "buck-input-hot-loop")
    assert hot["blocker"] == "level-below-min"


@pytest.mark.parametrize("mutate, cls, verdict", [
    # off the selection ladder's V/I corner, from a source with no attach
    # rule, voltage-mode: all four selection-class records go outside at once
    # (U22 added the current-mode inductor window)
    ({"vin_v": 400, "iout_a": 40, "source_kind": "dc-input",
      "control_kind": "vmode"}, "selection", "gap"),
    # ... but the V/I corner ALONE is not enough: buck-upstream-inrush-limit
    # carries the selection class and is bounded only by the source
    ({"vin_v": 400, "iout_a": 40}, "selection", "covered"),
    # a sync buck: the free-wheel-diode record does not apply (but power-loop
    # stays covered through the C_IN/C_O separation record)
    ({}, "power-loop", "covered"),
    # 8-layer stack is outside the 4-layer join recipe; external FETs put
    # the integrated-FET EP via-array record (U22) out too
    ({"board_layers": 8, "integration_kind": "controller"}, "return-path",
     "gap"),
    # ... the 8-layer stack ALONE stays covered through that EP record
    ({"board_layers": 8}, "return-path", "covered"),
    # 30 W dissipation is past vias-as-the-heat-path, and with external FETs
    # the EP via-array record is out
    ({"pdiss_w": 30, "integration_kind": "controller"}, "thermal-via", "gap"),
    # a battery is not a source with an attach or hot-plug rule
    ({"source_kind": "battery"}, "inrush", "gap"),
    # ... a bench supply now is: U22's DC-input hot-plug overshoot record
    ({"source_kind": "dc-input"}, "inrush", "covered"),
    # controller + external FETs: the integrated-FET BST/FB record is out
    ({"integration_kind": "controller"}, "decoupling", "gap"),
])
def test_committed_envelopes_bound_each_class(tmp_path, mutate, cls, verdict):
    op = dict(BUCK_OP, **mutate)
    rep = knowledgelib.coverage(real_ws(tmp_path, op=op))
    s = next(x for x in rep["slots"] if x["id"] == "block:B1")
    got = next(c for c in s["classes"] if c["class"] == cls)
    assert got["verdict"] == verdict, got


def test_an_undeclared_dim_holds_the_class_at_provisional(tmp_path):
    """The cost of the U14 envelopes: a dim P2 omits keeps its record
    provisional - visible, never silently covered."""
    op = {k: v for k, v in BUCK_OP.items() if k != "integration_kind"}
    rep = knowledgelib.coverage(real_ws(tmp_path, op=op))
    s = next(x for x in rep["slots"] if x["id"] == "block:B1")
    dec = next(c for c in s["classes"] if c["class"] == "decoupling")
    assert dec["verdict"] == "provisional"
    ev = next(e for e in dec["records"] if e["id"] == "buck-bst-fb-output-caps")
    assert ev["blocker"] == "envelope-unknown"
    assert ev["unknown_dims"] == ["integration_kind"]
    assert s["verdict"] == "provisional"


@pytest.mark.parametrize("base, checklist", [
    ("ETH_RX", "100base-tx"),      # lumina-carrier's diff_pairs bases
    ("ETH_TX", "100base-tx"),
    ("USB", "usb-fs"),             # usb-buck's
])
def test_interface_checklists_claim_the_tokens_boards_declare(base, checklist):
    cls = knowledgelib.load_checklists()
    got = knowledgelib.find_checklist(cls, "interface", base)
    assert got is not None and got["id"] == checklist


def test_research_provisional_escalates_unproven_classes_to_research(tmp_path):
    """Seeding runs (build-modes ultra-bare-bones): `covered` off approved
    records is not good enough on a board whose whole purpose is to seed the
    library. The maturity floor ALONE cannot express that - an under-mature
    class reads `provisional`, which never reaches gaps[] and so never opens
    a research task (U15 made that deliberate, so fresh workspace records do
    not re-fire the trigger). `--research-provisional` is the seeding lever:
    it escalates at CLASS level, so every unproven class lands in `missing`
    and the research task names it."""
    ws = real_ws(tmp_path)
    approved = knowledgelib.coverage(ws)
    assert approved["slots"][0]["verdict"] == "covered"
    assert approved["summary"]["gap"] == 0

    # floor alone: no longer covered, but still nothing to research
    floored = knowledgelib.coverage(ws, floor="proven")
    assert floored["slots"][0]["verdict"] == "provisional"
    assert floored["gaps"] == []

    # floor + escalation: every class becomes a named research target
    seeded = knowledgelib.coverage(ws, floor="proven",
                                   escalate_provisional=True)
    s = seeded["slots"][0]
    assert s["verdict"] == "gap"
    gap = next(g for g in seeded["gaps"] if g["slot"] == s["id"])
    named = {m["class"] for m in gap["missing"]}
    assert {"power-loop", "emi", "thermal-via", "feedback"} <= named
    assert named == {c["class"] for c in s["classes"]}

    # guard: escalation at the DEFAULT floor invents no research - a genuinely
    # covered class stays covered, so production runs are untouched.
    assert knowledgelib.coverage(ws, escalate_provisional=True) == approved


def test_interface_slots_are_honest_gaps_with_research_specs(tmp_path):
    """U14 ruling: approve the interface checklists with zero records behind
    them, so the slot reports gap + a research task spec (U15's input)."""
    ws = real_ws(tmp_path, diff_pairs=[{"p": "/USB_P", "n": "/USB_N",
                                        "base": "USB", "impedance_ohm": 90}])
    rep = knowledgelib.coverage(ws)
    s = next(x for x in rep["slots"] if x["id"] == "interface:usb")
    assert s["verdict"] == "gap"
    assert s["checklist"]["id"] == "usb-fs" and s["checklist"]["floor_met"]
    gap = next(g for g in rep["gaps"] if g["slot"] == "interface:usb")
    assert "diff-pair" in {m["class"] for m in gap["missing"]}
    # the buck inrush record keys on interface `usb` and so is not a gap -
    # it is provisional, waiting on a source_kind dim at the pair
    inrush = next(c for c in s["classes"] if c["class"] == "inrush")
    assert inrush["verdict"] == "provisional"
    assert [e["id"] for e in inrush["records"]] == ["buck-upstream-inrush-limit"]


# ------------------------------------------------- U21: draft_unverified
# Unverified workspace research must never read as coverage, and must not
# hide inside `provisional` either. bb-amp is the case: its six refuted
# input-stage records sat on classes that VERIFIED siblings already covered,
# so the loss of the hard knowledge showed up in no report at all.

def ws_draft_record(ws: Path, rid: str, **over) -> Path:
    """A workspace research record (research/records/), draft by default."""
    d = ws / "research" / "records"
    over.setdefault("origin", "research:block-buck-1")
    over.setdefault("status", "draft")
    over.setdefault("maturity", "draft")
    over.setdefault("applies", {"topologies": ["buck"], "packages": [],
                                "interfaces": [], "parts": []})
    for k in ("approval",):
        over.setdefault(k, ...)
    return write_record(d, rid, **over)


def test_a_draft_research_record_never_satisfies_and_gets_its_own_bucket(
        tmp_path, lib):
    ws = buck_ws(tmp_path)
    ws_draft_record(ws, "ws-draft-rule", classes=["power-loop"],
                    envelope={"edge_ns": {"max": 20},
                              "switching_kind": {"in": ["hard"]}})
    payload, code = cov(tmp_path, ws, lib)
    assert code == 0, payload
    bucket = payload["draft_unverified"]
    assert [r for d in bucket for r in d["records"]] == ["ws-draft-rule"]
    assert payload["summary"]["draft_unverified"] == 1
    assert any("never verified" in w for w in payload["warnings"])
    evals = [e for s in payload["slots"] for c in s["classes"]
             for e in c["records"] if e["id"] == "ws-draft-rule"]
    assert evals and all(e["blocker"] == "draft-unverified"
                         and not e["satisfies"] for e in evals)


def test_the_bucket_reports_even_when_a_verified_sibling_covers_the_class(
        tmp_path, lib):
    """bb-amp exactly: the class reads covered off the easier record, and the
    refuted one still has to be named."""
    ws = buck_ws(tmp_path)
    ws_draft_record(ws, "ws-draft-rule", classes=["power-loop"],
                    envelope={"edge_ns": {"max": 20},
                              "switching_kind": {"in": ["hard"]}})
    payload, code = cov(tmp_path, ws, lib)
    assert code == 0, payload
    s = slot(payload, "block:B3")
    pl = next(c for c in s["classes"] if c["class"] == "power-loop")
    assert pl["verdict"] == "covered"                 # r-hot-loop covers it
    assert [r for d in payload["draft_unverified"] for r in d["records"]] \
        == ["ws-draft-rule"]


def test_a_verified_workspace_record_is_not_in_the_draft_bucket(tmp_path, lib):
    ws = buck_ws(tmp_path)
    ws_draft_record(ws, "ws-verified-rule", classes=["power-loop"],
                    status="active", maturity="verified",
                    verification={"by": "second-reader", "date": "2026-08-20",
                                  "note": "re-read p4, holds"},
                    envelope={"edge_ns": {"max": 20},
                              "switching_kind": {"in": ["hard"]}})
    payload, code = cov(tmp_path, ws, lib)
    assert code == 0, payload
    assert payload["draft_unverified"] == []
    assert payload["summary"]["draft_unverified"] == 0


def test_a_draft_LIBRARY_record_keeps_maturity_below_floor(tmp_path, lib):
    """The new blocker is scoped to workspace research output - a library
    record that is merely under-mature still reads `maturity-below-floor`."""
    recs, cls = lib
    write_record(recs, "r-draft-lib", classes=["power-loop"],
                 maturity="draft", approval=...,
                 envelope={"edge_ns": {"max": 20},
                           "switching_kind": {"in": ["hard"]}})
    ws = buck_ws(tmp_path)
    payload, code = cov(tmp_path, ws, (recs, cls))
    assert code == 0, payload
    assert payload["draft_unverified"] == []
    ev = [e for s in payload["slots"] for c in s["classes"]
          for e in c["records"] if e["id"] == "r-draft-lib"]
    assert ev and all(e["blocker"] == "maturity-below-floor" for e in ev)
