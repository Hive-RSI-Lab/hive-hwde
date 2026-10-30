"""A coverage checklist counts on an independent ruling graded against
planted faults (checklist_verify.py, lib/checklistverify.py), not only on the
person's approval.

Pinned here:
  1. The packet blinds the checklist among >= 3 distinct planted mutants, and
     the key lives outside it with its sha256 in the manifest.
  2. A ruling counts only when it catches every planted mutant, calls the
     real item sound and grounds every required class; one missed mutant,
     the author ruling, a real item called faulty or an ungrounded class
     refuses it whole.
  3. --write leaves a checklist that lints green and that coverage counts at
     the approved floor; an edit to what it decides afterwards drops it back
     (stale digest) and the lint names it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklist_verify  # noqa: E402
import checklistverify  # noqa: E402
import knowledge  # noqa: E402
import knowledgelib  # noqa: E402

ORIGIN = "hive:261028-141932-9b95b3083b"
VERIFIER = "w-261030-140119-f63d0240de"


def write_record(d: Path, rid: str, classes, topologies=("buck",), **over):
    rec = {"id": rid, "classes": list(classes),
           "applies": {"topologies": list(topologies), "packages": [],
                       "interfaces": [], "parts": []},
           "rule": None, "prose": "A fact.", "sources": [{"file": "README.md"}],
           "status": "active", "origin": "test", "level": "topology",
           "envelope": {"vin_v": {"min": 3, "max": 8}},
           "maturity": "approved", "approval": {"by": "owner",
                                                "date": "2026-08-15"}}
    rec.update(over)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rid}.yaml").write_text(yaml.safe_dump(rec), encoding="utf-8")


def write_checklist(d: Path, cid: str = "buck", **over) -> Path:
    cl = {"id": cid, "kind": "coverage-checklist",
          "applies": {"topologies": [cid]},
          "requires": [
              {"class": "power-loop", "min_level": "topology",
               "note": "input hot loop under 20 mm2"},
              {"class": "emi", "min_level": "topology",
               "note": "switch node kept small"}],
          "sources": [{"file": "README.md", "note": "VIN 5 V at 3 A"}],
          "maturity": "draft", "origin": ORIGIN}
    cl.update(over)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{cid}.yaml"
    p.write_text("# a comment the ruling keeps\n" + yaml.safe_dump(
        cl, sort_keys=False), encoding="utf-8")
    return p


@pytest.fixture
def lib(tmp_path):
    recs, cls = tmp_path / "recs", tmp_path / "cls"
    write_record(recs, "r-hot-loop", ["power-loop"])
    write_record(recs, "r-emi", ["emi"])
    return recs, cls, write_checklist(cls)


def load(p: Path) -> dict:
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    d["_path"] = p.as_posix()
    return d


def packet(tmp_path, cl_path, seed=7, planted=3):
    pk, key = tmp_path / "packet", tmp_path / "keys" / "key.json"
    man = checklistverify.make_packet(load(cl_path), pk, key, planted, seed)
    return pk, key, man


def perfect(key_path: Path, basis=None, by=VERIFIER) -> dict:
    """The verdicts a verifier that sees every fault would give."""
    key = json.loads(key_path.read_text(encoding="utf-8"))
    items = {}
    for label, ans in key["items"].items():
        if ans["real"]:
            items[label] = {"verdict": "sound", "basis": basis or {
                "power-loop": ["r-hot-loop"], "emi": ["r-emi", "README.md#1"]}}
        else:
            want = dict(ans["mutation"]["expect"][0])
            items[label] = {"verdict": "faulty",
                            "faults": [dict(want, why="p2 shows otherwise")]}
    return {"by": by, "note": "read README.md p1", "items": items}


def rule(tmp_path, lib, verdicts, write=False):
    recs, cls, clp = lib
    vp = tmp_path / "verdicts.json"
    vp.write_text(json.dumps(verdicts), encoding="utf-8")
    pk, key = tmp_path / "packet", tmp_path / "keys" / "key.json"
    out = tmp_path / "rule.json"
    argv = ["rule", "--checklist", str(clp), "--packet", str(pk),
            "--key", str(key), "--verdicts", str(vp), "--out", str(out)]
    orig = knowledgelib.load_records
    knowledgelib.load_records = lambda d=None: orig(d or recs)
    try:
        code = checklist_verify.main(argv + (["--write"] if write else []))
    finally:
        knowledgelib.load_records = orig
    return json.loads(out.read_text(encoding="utf-8")), code


# ---------------------------------------------------------------------------
def test_digest_pins_what_the_checklist_decides():
    cl = {"id": "x", "applies": {"topologies": ["x"]},
          "requires": [{"class": "emi", "min_level": "topology"}],
          "sources": [], "prose": "a", "maturity": "draft"}
    d = knowledgelib.checklist_digest(cl)
    assert knowledgelib.checklist_digest(
        dict(cl, prose="b", maturity="verified", verification={})) == d
    assert knowledgelib.checklist_digest(
        dict(cl, requires=[{"class": "emi", "min_level": "principle"}])) != d


def test_same_author_by_shared_task_id():
    assert knowledgelib.same_author("w-261028-141932-9b95b3083b", ORIGIN)
    assert knowledgelib.same_author(ORIGIN, ORIGIN)
    assert knowledgelib.same_author("", ORIGIN)
    assert not knowledgelib.same_author(VERIFIER, ORIGIN)


@pytest.mark.parametrize("rows", [1, 2, 5])
def test_plan_gives_distinct_mutants_each_with_an_expected_fault(rows):
    import random
    cl = {"id": "t", "kind": "coverage-checklist",
          "applies": {"topologies": ["t"]},
          "requires": [{"class": c, "min_level": "topology",
                        "note": f"{c} row, 12 mm"}
                       for c in ["selection", "emi", "thermal", "esd",
                                 "decoupling"][:rows]],
          "sources": [{"file": "README.md", "note": "VF 2.2 V at 20 mA"}]}
    muts = checklistverify.plan(cl, 4, random.Random(1))
    assert len(muts) == 4
    blobs = {json.dumps(m, sort_keys=True) for m, _ in muts}
    assert len(blobs) == 4
    assert json.dumps(checklistverify.blind(cl), sort_keys=True) not in blobs
    kinds = [i["kind"] for _, i in muts]
    assert kinds[0] == ("drop_row" if rows >= 2 else "swap_class")
    assert "add_foreign" in kinds and "corrupt_number" in kinds
    assert all(i["expect"] for _, i in muts)


def test_packet_is_blinded_and_the_key_stays_out(tmp_path, lib):
    pk, key, man = packet(tmp_path, lib[2])
    items = sorted((pk / "items").glob("*.yaml"))
    assert len(items) == 4 and man["items"] == [p.stem for p in items]
    for p in items:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert not {"maturity", "origin", "approval", "verification"} & set(d)
    import hashlib
    assert man["key_sha256"] == hashlib.sha256(key.read_bytes()).hexdigest()
    assert not list(pk.rglob("key*"))
    with pytest.raises(ValueError, match="outside"):
        checklistverify.make_packet(load(lib[2]), pk, pk / "key.json", 3, 1)
    with pytest.raises(ValueError, match="at least"):
        checklistverify.make_packet(load(lib[2]), pk, key, 2, 1)


def test_a_ruling_that_catches_every_fault_verifies_and_coverage_counts_it(
        tmp_path, lib):
    recs, cls, clp = lib
    _, key, _ = packet(tmp_path, clp)
    out, code = rule(tmp_path, lib, perfect(key), write=True)
    assert code == 0 and out["status"] == "verified", out
    assert out["planted"] == {"total": 3, "caught": 3}
    text = clp.read_text(encoding="utf-8")
    assert text.startswith("# a comment the ruling keeps")
    cl = load(clp)
    assert cl["maturity"] == "verified" and cl["verification"]["by"] == VERIFIER
    assert knowledgelib.checklist_verification_problem(cl) is None
    assert knowledgelib.checklist_meets_floor(cl, "approved")
    assert not knowledgelib.checklist_meets_floor(cl, "proven")
    assert knowledgelib.validate(recs, cls) == []
    # coverage counts it as it counts an approved checklist
    ws = tmp_path / "ws"
    (ws / "kicad").mkdir(parents=True)
    (ws / "kicad" / "constraints.json").write_text(json.dumps({"blocks": [
        {"topology": "buck", "block": "B3",
         "operating_point": {"vin_v": 5}}]}), encoding="utf-8")
    cov = tmp_path / "cov.json"
    knowledge.main(["--coverage", "--workspace", str(ws), "--records-dir",
                    str(recs), "--checklists-dir", str(cls), "--out", str(cov)])
    s = json.loads(cov.read_text(encoding="utf-8"))["slots"][0]
    assert s["verdict"] == "covered", s
    assert s["checklist"] == {"id": "buck", "maturity": "verified",
                              "floor_met": True}
    # an edit to what it decides drops it back, and the lint says why
    clp.write_text(text.replace("min_level: topology", "min_level: principle",
                                1), encoding="utf-8")
    cl = load(clp)
    assert "stale" in knowledgelib.checklist_verification_problem(cl)
    assert not knowledgelib.checklist_meets_floor(cl, "approved")
    assert any("stale" in p for p in knowledgelib.validate(recs, cls))
    knowledge.main(["--coverage", "--workspace", str(ws), "--records-dir",
                    str(recs), "--checklists-dir", str(cls), "--out", str(cov)])
    s = json.loads(cov.read_text(encoding="utf-8"))["slots"][0]
    assert s["verdict"] == "provisional"
    assert any("stale" in r for r in s["gap_reasons"])


def test_one_missed_planted_fault_refuses_the_whole_ruling(tmp_path, lib):
    _, key, _ = packet(tmp_path, lib[2])
    v = perfect(key)
    k = json.loads(key.read_text(encoding="utf-8"))
    miss = next(lb for lb, a in k["items"].items() if not a["real"])
    v["items"][miss] = {"verdict": "sound", "basis": {}}
    out, code = rule(tmp_path, lib, v, write=True)
    assert code == 1 and out["status"] == "not_verified"
    assert out["planted"] == {"total": 3, "caught": 2}
    assert [m["item"] for m in out["missed"]] == [miss]
    assert load(lib[2])["maturity"] == "draft"


def test_a_fault_named_for_the_wrong_reason_is_a_miss(tmp_path, lib):
    _, key, _ = packet(tmp_path, lib[2])
    v = perfect(key)
    for lb, it in v["items"].items():
        if it["verdict"] == "faulty":
            it["faults"] = [{"kind": "wrong-fact", "class": "thermal",
                             "why": "guess"}]
    out, code = rule(tmp_path, lib, v)
    assert code == 1 and out["planted"]["caught"] < 3


@pytest.mark.parametrize("by,needle", [
    ("w-261028-141932-9b95b3083b", "author"),
    ("owner", "approval path"),
    ("", "no verifier")])
def test_the_author_or_no_one_cannot_rule(tmp_path, lib, by, needle):
    _, key, _ = packet(tmp_path, lib[2])
    out, code = rule(tmp_path, lib, perfect(key, by=by))
    assert code == 1 and any(needle in p for p in out["problems"]), out


def test_the_real_item_called_faulty_is_reported_not_verified(tmp_path, lib):
    _, key, _ = packet(tmp_path, lib[2])
    v = perfect(key)
    k = json.loads(key.read_text(encoding="utf-8"))
    real = next(lb for lb, a in k["items"].items() if a["real"])
    v["items"][real] = {"verdict": "faulty", "faults": [
        {"kind": "missing", "class": "thermal", "why": "p3 derating"}]}
    out, code = rule(tmp_path, lib, v)
    assert code == 1 and out["real_faults"][0]["class"] == "thermal"


@pytest.mark.parametrize("basis,needle", [
    ({"power-loop": ["r-hot-loop"]}, "no basis given for required class 'emi'"),
    ({"power-loop": ["r-emi"], "emi": ["r-emi"]}, "does not hold class"),
    ({"power-loop": ["r-hot-loop"], "emi": ["nope.pdf#3"]}, "neither")])
def test_every_required_class_must_rest_on_something_real(tmp_path, lib,
                                                          basis, needle):
    _, key, _ = packet(tmp_path, lib[2])
    out, code = rule(tmp_path, lib, perfect(key, basis=basis))
    assert code == 1 and any(needle in p for p in out["problems"]), out


def test_a_record_for_another_topology_is_no_basis(tmp_path, lib):
    write_record(lib[0], "r-boost-emi", ["emi"], topologies=("boost",))
    _, key, _ = packet(tmp_path, lib[2])
    out, code = rule(tmp_path, lib, perfect(key, basis={
        "power-loop": ["r-hot-loop"], "emi": ["r-boost-emi"]}))
    assert code == 1 and any("applies to none" in p for p in out["problems"])


def test_a_packet_from_before_an_edit_cannot_be_ruled(tmp_path, lib):
    _, key, _ = packet(tmp_path, lib[2])
    clp = lib[2]
    clp.write_text(clp.read_text(encoding="utf-8").replace("20 mm2", "30 mm2"),
                   encoding="utf-8")
    out, code = rule(tmp_path, lib, perfect(key))
    assert code == 1 and any("changed after" in p for p in out["problems"])


def test_hand_written_verification_without_a_ruling_does_not_count(tmp_path):
    recs, cls = tmp_path / "recs", tmp_path / "cls"
    write_record(recs, "r-hot-loop", ["power-loop"])
    write_record(recs, "r-emi", ["emi"])
    p = write_checklist(cls, maturity="verified")
    cl = load(p)
    assert knowledgelib.checklist_verification_problem(cl) == \
        "no verification block"
    assert not knowledgelib.checklist_meets_floor(cl, "verified")
    assert any("maturity verified but" in x
               for x in knowledgelib.validate(recs, cls))


def test_status_lists_which_committed_checklists_count():
    rows = checklistverify.status(knowledgelib.load_checklists(), "approved")
    by = {r["id"]: r for r in rows}
    assert by["buck"]["counts"]
    for r in rows:
        assert r["counts"] or r["why_not"]
