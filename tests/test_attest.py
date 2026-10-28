"""U5 acceptance tests: release attestation, durable waivers, derived
disposition (codex C1 + H9).

The plan's acceptance criteria, verbatim:
 - order REFUSES lumina-carrier and rf-de-20m in their current recorded
   states even with fresh dfm (here: attest build lists every miss, exit 1;
   order_submit refuses a governed workspace without a valid attestation);
 - a one-coordinate board nudge, a waiver edit, or a copper-weight change
   each invalidate the attestation;
 - the two reference boards attest green end-to-end (pinned in
   test_reference_attestations_verify_valid once the migration commits
   their attestations).

Hermetic: pure venv, synthetic workspaces under tmp_path, real boards read
READ-ONLY (attest verify/disposition/build-refusal never write). The
JLCPCB session is mocked test_jlcapi-style; no network, no kicad-cli.
"""
from __future__ import annotations

import datetime as _dt
import json
import zipfile
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import attest  # noqa: E402
import checklib  # noqa: E402
import gate  # noqa: E402
import order_submit  # noqa: E402
import releaselib  # noqa: E402
import state as state_mod  # noqa: E402
import statelib  # noqa: E402
from _boards import copy_board, real_board  # noqa: E402

BOARD = "attboard"
PCB_TEXT = """(kicad_pcb (version 20260101) (generator "test")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  )
  (net 0 "")
  (segment (start 1 1) (end 2 2) (width 0.25) (layer "F.Cu") (net 0))
)
"""


# --------------------------------------------------------------- fixtures

def _creep(net="V48", refs=("U1",), sev="error", pos=(1.0, 2.0)):
    return {"check": "check_creepage", "kind": "creepage", "severity": sev,
            "net": net, "refs": list(refs), "pos": list(pos),
            "msg": "gap", "source": "check_creepage"}


def _write_report(ws: Path, name: str, script: str, pcb: Path,
                  violations=None, coverage=None) -> Path:
    vios = violations or []
    rep = {"script": script, "board": pcb.name,
           "status": "violations" if vios else "pass",
           "counts": checklib.summarize(vios),
           "checks": {}, "violations": vios}
    if coverage is not None:
        rep["coverage"] = coverage
    checklib.stamp(rep, pcb)
    out = ws / "reports" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return out


def _durable_waiver(pcb_hash: str, **over) -> dict:
    w = {"check": "check_creepage", "kind": "creepage", "net": "V48",
         "refs": ["U1"], "pos": [1.0, 2.0],
         "artifact": pcb_hash, "checker_version": checklib.CHECKER_VERSION,
         "expires": "2030-01-01",
         "reason": "prototype spacing accepted",
         "approved": "Test Owner 2026-08-14"}
    w.update(over)
    return w


def green_ws(tmp_path: Path, waived_violation=True) -> Path:
    """A synthetic workspace that satisfies every attest build precondition.
    Lives under a boards/ dir so workspace-relative resolution (waiver
    fallback, commit scoping) behaves like the real tree."""
    ws = tmp_path / "boards" / BOARD
    st = state_mod.State.init(ws, BOARD)

    kicad = ws / "kicad"
    pcb = kicad / f"{BOARD}.kicad_pcb"
    pcb.write_text(PCB_TEXT, encoding="utf-8")
    (kicad / f"{BOARD}.kicad_sch").write_text(
        '(kicad_sch (version 20260101))', encoding="utf-8")
    (kicad / f"{BOARD}.net").write_text(
        '(export (version "E"))', encoding="utf-8")
    (kicad / "constraints.json").write_text("{}", encoding="utf-8")
    (kicad / "decoupling.json").write_text("{}", encoding="utf-8")

    (ws / "architecture").mkdir(exist_ok=True)
    (ws / "architecture" / "stackup.md").write_text(
        "# Stackup\n\n## Chosen: `JLC2313_1.6` (2-layer)\n", encoding="utf-8")

    fab = ws / "fab"
    with zipfile.ZipFile(fab / f"{BOARD}_gerbers.zip", "w") as z:
        z.writestr(f"{BOARD}-F_Cu.gbr", "G04 test*\nM02*\n")
    (fab / "BOM.csv").write_text("Comment\n", encoding="utf-8")
    (fab / "CPL.csv").write_text("Designator\n", encoding="utf-8")
    (fab / "BOM-full.csv").write_text("Comment\n", encoding="utf-8")
    (fab / "quote.json").write_text(json.dumps({
        "spec": {"layers": 2, "width_mm": 20.0, "height_mm": 25.0,
                 "thickness_mm": 1.6},
        "estimated": True,
        "matrix": [{"qty": 5, "surface_finish": "HASL",
                    "solder_mask_color": "green", "total": 4.0}],
    }), encoding="utf-8")

    pcb_hash = statelib.hash_artifact(pcb, "sexpr_no_uuid")
    vios = []
    if waived_violation:
        (ws / "reports").mkdir(exist_ok=True)
        (ws / "reports" / "verify-waivers.json").write_text(json.dumps(
            {"waivers": [_durable_waiver(pcb_hash)]}), encoding="utf-8")
        vios = [_creep()]
    _write_report(ws, "verify_release.json", "verify_all", pcb,
                  violations=vios,
                  coverage={"strict": True, "required": ["check_creepage"],
                            "ran": ["check_creepage"],
                            "passed": [], "failed": ["check_creepage"] if vios
                            else [], "waived": [], "not_applicable": {},
                            "skipped_error": {}})
    _write_report(ws, "dfm_release.json", "dfm_check", pcb)

    for g in ("erc", "place", "drc_routed", "verify", "dfm"):
        st.record_gate(g, {"status": "pass", "failing_count": 0,
                           "counts": {"total": 0}})
    st.record_human("4", "approved", "test approval")
    st.save()
    return ws


def _nudge_pcb(ws: Path) -> None:
    pcb = ws / "kicad" / f"{BOARD}.kicad_pcb"
    pcb.write_text(pcb.read_text(encoding="utf-8").replace(
        "(start 1 1)", "(start 1.1 1)"), encoding="utf-8")


# ------------------------------------------- waiver matching (H9 hardening)

def test_empty_ref_subset_match_killed():
    """The rf-de footgun: a refs-scoped waiver must NEVER match a refs-less
    finding (the empty set is a subset of everything)."""
    w = {"kind": "creepage", "net": "V48", "refs": ["U1", "U2"],
         "reason": "r", "approved": "a"}
    assert not gate.waiver_matches(w, _creep(refs=()))
    assert gate.waiver_matches(w, _creep(refs=("U1",)))     # subset survives


def test_pos_scoped_waiver_matching():
    w = {"kind": "creepage", "net": "V48", "pos": [1.0, 2.0],
         "reason": "r", "approved": "a"}
    assert gate.waiver_matches(w, _creep(pos=(1.005, 2.0)))   # within 0.01
    assert not gate.waiver_matches(w, _creep(pos=(1.05, 2.0)))
    assert not gate.waiver_matches(w, {**_creep(), "pos": None})


def test_invalid_durable_waiver_matches_nothing(tmp_path):
    """A waiver whose artifact binding no longer holds is excluded and
    surfaced, never silently applied."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    rep = checklib.stamp({"script": "verify_all", "status": "violations",
                          "checks": {}, "violations": [_creep()],
                          "counts": checklib.summarize([_creep()])}, board)
    g = {"tool": "verify", "fail_severities": ["error"], "max_count": 0}
    stale = _durable_waiver("sexpr_no_uuid:" + "0" * 64)
    res = gate.evaluate("verify", g, rep, waivers=[stale])
    assert res["status"] == "fail" and res["waived_count"] == 0
    assert res["waivers_invalid"][0]["problems"]
    # same waiver bound to the real artifact hash: waives
    good = _durable_waiver(rep["input_digest"])
    res = gate.evaluate("verify", g, rep, waivers=[good])
    assert res["status"] == "pass" and res["waived_count"] == 1


def test_checker_bump_invalidates_waiver(tmp_path, monkeypatch):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    monkeypatch.setattr(checklib, "CHECKER_VERSION", 2)
    rep = checklib.stamp({"script": "verify_all", "status": "violations",
                          "checks": {}, "violations": [_creep()],
                          "counts": checklib.summarize([_creep()])}, board)
    w = _durable_waiver(rep["input_digest"], checker_version=1)
    g = {"tool": "verify", "fail_severities": ["error"], "max_count": 0}
    res = gate.evaluate("verify", g, rep, waivers=[w])
    assert res["status"] == "fail"
    assert "checker_version" in res["waivers_invalid"][0]["problems"][0]


def test_expired_waiver_invalid(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    rep = checklib.stamp({"script": "verify_all", "status": "violations",
                          "checks": {}, "violations": [_creep()],
                          "counts": checklib.summarize([_creep()])}, board)
    w = _durable_waiver(rep["input_digest"], expires="2020-01-01")
    g = {"tool": "verify", "fail_severities": ["error"], "max_count": 0}
    res = gate.evaluate("verify", g, rep, waivers=[w])
    assert res["status"] == "fail"
    assert "expired" in res["waivers_invalid"][0]["problems"][0]


def test_strict_gate_requires_durable_waivers(tmp_path):
    """verify_release refuses (exit 2) a legacy reason+approved-only waiver;
    the lenient verify gate still accepts it (mid-pipeline compat)."""
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    rep = checklib.stamp({"script": "verify_all", "status": "violations",
                          "checks": {}, "violations": [_creep()],
                          "counts": checklib.summarize([_creep()])}, board)
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(rep), encoding="utf-8")
    wv = tmp_path / "w.json"
    wv.write_text(json.dumps({"waivers": [
        {"kind": "creepage", "net": "V48", "refs": ["U1"],
         "reason": "r", "approved": "H4"}]}), encoding="utf-8")
    assert gate.main(["--gate", "verify_release", "--report", str(rp),
                      "--waivers", str(wv)]) == 2
    assert gate.main(["--gate", "verify", "--report", str(rp),
                      "--waivers", str(wv)]) == 0


def test_gate_default_sidecar_falls_back_to_workspace(tmp_path):
    """LEARNINGS 2026-08-08: a waiver file in the workspace reports/ dir was
    silently ignored because the default resolved beside the BOARD. The
    shared resolution now finds it."""
    ws = tmp_path / "boards" / "wb"
    (ws / "kicad").mkdir(parents=True)
    (ws / "reports").mkdir()
    board = ws / "kicad" / "wb.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    rep = checklib.stamp({"script": "verify_all", "status": "violations",
                          "checks": {}, "violations": [_creep()],
                          "counts": checklib.summarize([_creep()])}, board)
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(rep), encoding="utf-8")
    (ws / "reports" / "verify-waivers.json").write_text(json.dumps(
        {"waivers": [{"kind": "creepage", "net": "V48", "refs": ["U1"],
                      "reason": "r", "approved": "H4"}]}), encoding="utf-8")
    assert gate.main(["--gate", "verify", "--report", str(rp)]) == 0


# ------------------------------------------------------- attestation build

def test_build_green_workspace(tmp_path, capsys):
    ws = green_ws(tmp_path)
    assert attest.main(["build", "--workspace", str(ws)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "issued" and out["rev"] == 1
    att = releaselib.load_attestation(ws)
    assert releaselib.check_seal(att)
    assert att["board"] == BOARD
    assert att["manufacturing"]["layers"] == 2
    assert att["manufacturing"]["copper_weight_oz"] == 1.0
    assert att["waivers"]["count"] == 1
    assert len(att["waivers"]["waived_fingerprints"]) == 1
    assert att["inputs"]["pcb"] and att["inputs"]["stackup_md"]
    assert att["fab"]["gerber_zip"]["design_sha256"]
    v = releaselib.verify(ws)
    assert v["valid"], v["problems"]
    assert releaselib.disposition(ws)["disposition"] == "order-ready"


def test_build_lists_every_miss_and_writes_nothing(tmp_path, capsys):
    ws = green_ws(tmp_path)
    st = state_mod.State.load(ws / "state.json")
    st.data["gates"]["verify"]["status"] = "fail"
    st.data["human"].pop("4")
    st.save()
    (ws / "reports" / "dfm_release.json").unlink()
    assert attest.main(["build", "--workspace", str(ws)]) == 1
    out = json.loads(capsys.readouterr().out)
    text = " | ".join(out["problems"])
    assert "gate verify" in text
    assert "checkpoint 4" in text
    assert "dfm_release.json" in text
    assert not (ws / "fab" / "attestation.json").exists()


def test_build_idempotent_then_force_reissues(tmp_path, capsys):
    ws = green_ws(tmp_path)
    assert attest.main(["build", "--workspace", str(ws)]) == 0
    first = json.loads(capsys.readouterr().out)["attestation_sha256"]
    assert attest.main(["build", "--workspace", str(ws)]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["action"] == "unchanged"
    assert again["attestation_sha256"] == first
    assert attest.main(["build", "--workspace", str(ws), "--force"]) == 0
    forced = json.loads(capsys.readouterr().out)
    assert forced["action"] == "reissued" and forced["rev"] == 2
    att = releaselib.load_attestation(ws)
    assert att["supersedes"] == first


def test_sim_gate_required_when_sims_exist(tmp_path, capsys):
    ws = green_ws(tmp_path)
    sims = ws / "kicad" / "sims"
    sims.mkdir()
    (sims / "t.cir").write_text("* tb\n.end\n", encoding="utf-8")
    assert attest.main(["build", "--workspace", str(ws), "--force"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert any("gate sim: never recorded" in p for p in out["problems"])
    # declared NA with reason+approved: excused and recorded. The
    # constraints edit stales place/verify (constraints is their input) -
    # re-record them, exactly what a real operator must do.
    (ws / "kicad" / "constraints.json").write_text(json.dumps({
        "release": {"not_applicable": {"sim": {
            "reason": "no active devices to simulate",
            "approved": "Test Owner 2026-08-14"}}}}), encoding="utf-8")
    st = state_mod.State.load(ws / "state.json")
    for g in ("place", "verify"):
        st.record_gate(g, {"status": "pass", "failing_count": 0,
                           "counts": {"total": 0}})
    st.save()
    assert attest.main(["build", "--workspace", str(ws), "--force"]) == 0
    att = releaselib.load_attestation(ws)
    assert att["gates"]["sim"]["status"] == "not_applicable"


def test_na_only_allowed_for_sim(tmp_path, capsys):
    ws = green_ws(tmp_path)
    (ws / "kicad" / "constraints.json").write_text(json.dumps({
        "release": {"not_applicable": {"verify": {
            "reason": "nah", "approved": "x"}}}}), encoding="utf-8")
    assert attest.main(["build", "--workspace", str(ws), "--force"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert any("not_applicable.verify" in p for p in out["problems"])


# --------------------------------------------------- invalidation (plan)

def test_board_nudge_invalidates(tmp_path):
    """A one-coordinate nudge invalidates: the normalized pcb hash is bound."""
    ws = green_ws(tmp_path)
    att, problems = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    assert releaselib.verify(ws)["valid"]
    _nudge_pcb(ws)
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("input pcb" in p for p in v["problems"])


def test_waiver_edit_invalidates(tmp_path):
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    wf = ws / "reports" / "verify-waivers.json"
    doc = json.loads(wf.read_text(encoding="utf-8"))
    doc["waivers"][0]["reason"] = "edited after attestation"
    wf.write_text(json.dumps(doc), encoding="utf-8")
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("waiver" in p.lower() for p in v["problems"])


def test_copper_weight_change_invalidates(tmp_path):
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    (ws / "architecture" / "stackup.md").write_text(
        "# Stackup\n\n## Chosen: `JLC2313_1.6_2oz` (2-layer, 2 oz)\n",
        encoding="utf-8")
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("copper weight" in p for p in v["problems"])
    assert any("stackup_md" in p for p in v["problems"])


def test_tampered_attestation_refused(tmp_path):
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    path = ws / "fab" / "attestation.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["manufacturing"]["copper_weight_oz"] = 2.0
    path.write_text(json.dumps(doc), encoding="utf-8")
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("seal" in p for p in v["problems"])


def test_null_hash_is_binding(tmp_path):
    """A kind absent at attestation time (recorded null) appearing later is
    a change - the attestation must not survive it."""
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    assert att["inputs"]["parts"] is None
    (ws / "kicad" / "parts.json").write_text("{}", encoding="utf-8")
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("input parts" in p for p in v["problems"])


def test_attestation_predating_place_waivers_kind(tmp_path):
    """place_waivers joined invalidation.yaml after boards were attested
    (2026-10-05). An attestation that does not bind it stays valid while the
    board has no place-waivers.json, and is invalid once one appears."""
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    assert "place_waivers" in att["inputs"]
    att = {k: v for k, v in att.items() if k != "attestation_sha256"}
    att["inputs"] = {k: v for k, v in att["inputs"].items()
                     if k != "place_waivers"}
    att["input_paths"] = {k: v for k, v in att["input_paths"].items()
                          if k != "place_waivers"}
    att["attestation_sha256"] = releaselib.seal(att)
    releaselib.write_attestation(ws, att)
    assert releaselib.verify(ws)["valid"], releaselib.verify(ws)["problems"]
    (ws / "reports" / "place-waivers.json").write_text(
        '{"waivers": []}', encoding="utf-8")
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert "input place_waivers: not bound by the attestation" \
        in v["problems"]


def test_gate_regression_and_revoked_approval_invalidate(tmp_path):
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    st = state_mod.State.load(ws / "state.json")
    st.data["gates"]["dfm"]["status"] = "fail"
    st.data["human"]["4"]["status"] = "rejected"
    st.save()
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("gate dfm" in p for p in v["problems"])
    assert any("checkpoint 4" in p for p in v["problems"])


def test_waiver_expiry_passing_invalidates(tmp_path, monkeypatch):
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    assert releaselib.verify(ws)["valid"]
    future = _dt.datetime(2031, 1, 1, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(releaselib, "_utcnow", lambda: future)
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("expired" in p for p in v["problems"])


# ------------------------------------------------------------ disposition

def test_disposition_ladder(tmp_path):
    ws = green_ws(tmp_path)
    # engineering-validated: gates green+fresh, strict reports removed
    (ws / "reports" / "verify_release.json").unlink()
    (ws / "reports" / "dfm_release.json").unlink()
    assert releaselib.disposition(ws)["disposition"] == "engineering-validated"
    # draft: a gate loses freshness (pcb nudge) - but re-green via re-record
    _nudge_pcb(ws)
    assert releaselib.disposition(ws)["disposition"] == "draft"


def test_disposition_release_candidate_then_order_ready(tmp_path):
    ws = green_ws(tmp_path)
    assert releaselib.disposition(ws)["disposition"] == "release-candidate"
    att, problems = releaselib.build(ws)
    assert not problems, problems
    releaselib.write_attestation(ws, att)
    assert releaselib.disposition(ws)["disposition"] == "order-ready"


def test_disposition_ordered_built_bringup(tmp_path):
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    (ws / "fab" / "order.json").write_text(json.dumps(
        {"order_number": "W123", "api": {}, "human_steps": []}),
        encoding="utf-8")
    assert releaselib.disposition(ws)["disposition"] == "ordered"
    st = state_mod.State.load(ws / "state.json")
    st._log("boards_received", note="arrived")
    st.save()
    assert releaselib.disposition(ws)["disposition"] == "built"
    st._log("bringup_passed", note="bench ok")
    st.save()
    assert releaselib.disposition(ws)["disposition"] == "bring-up-passed"


def test_disposition_derated_blocked_rework(tmp_path):
    ws = green_ws(tmp_path)
    (ws / "fab" / "order.json").write_text(json.dumps(
        {"order_number": "W1", "api": {},
         "human_steps": ["COPPER WAIVER: ordered at 1 oz despite ..."]}),
        encoding="utf-8")
    assert releaselib.disposition(ws)["disposition"] == "derated"
    (ws / "fab" / "restrictions.json").write_text(json.dumps(
        {"restrictions": [{"kind": "hold", "note": "admin hold",
                           "approved": "owner"}]}), encoding="utf-8")
    assert releaselib.disposition(ws)["disposition"] == "blocked"
    (ws / "fab" / "restrictions.json").unlink()
    st = state_mod.State.load(ws / "state.json")
    st.data["gates"]["verify"]["status"] = "fail"
    st.save()
    assert releaselib.disposition(ws)["disposition"] == "rework-required"


def test_resume_surfaces_disposition(tmp_path):
    ws = green_ws(tmp_path)
    st = state_mod.State.load(ws / "state.json")
    assert st.resume_summary()["release_disposition"] == "release-candidate"


def test_record_gate_refuses_stale_result_digest(tmp_path):
    """U5 tooth (hit live in this session's migration): a gate result whose
    stamped input_digest does not match the CURRENT primary input must
    refuse - recording a stale or foreign result file would bless a gate
    that never ran against this board. Legacy results without the field
    keep recording (compat)."""
    ws = green_ws(tmp_path)
    st = state_mod.State.load(ws / "state.json")
    pcb_hash = statelib.hash_artifact(ws / "kicad" / f"{BOARD}.kicad_pcb",
                                      "sexpr_no_uuid")
    ok = {"status": "pass", "failing_count": 0, "counts": {"total": 0},
          "input_digest": pcb_hash}
    st.record_gate("verify", ok)                       # current: records
    stale = {**ok, "input_digest": "sexpr_no_uuid:" + "0" * 64}
    with pytest.raises(Exception, match="input_digest"):
        st.record_gate("verify", stale)
    st.record_gate("verify", {"status": "pass", "failing_count": 0,
                              "counts": {"total": 0}})  # legacy: no field


# ------------------------------------------ real boards (read-only pins)

def test_carrier_and_rfde_refused_in_current_state(capsys, tmp_path):
    """Codex C1 acceptance: order must refuse lumina-carrier and rf-de-20m
    in their CURRENT recorded states even with fresh dfm. attest build is
    the order verb's release step: it must exit 1 with the gaps named, and
    write nothing. (These pins read the committed board records; update
    them only when the boards' recorded states genuinely change. Each board
    is copied first, so a regression that does write lands in tmp_path.)"""
    for board, expect in (("lumina-carrier", "verify"),
                          ("rf-de-20m", "drc_routed")):
        ws = copy_board(board, tmp_path, skip=("work", "state_snapshots"))
        before = (ws / "fab" / "attestation.json").exists()
        assert attest.main(["disposition", "--workspace", str(ws)]) == 0
        d = json.loads(capsys.readouterr().out)
        assert d["disposition"] == "rework-required"
        assert attest.main(["build", "--workspace", str(ws)]) == 1
        out = json.loads(capsys.readouterr().out)
        assert any(f"gate {expect}" in p for p in out["problems"])
        assert (ws / "fab" / "attestation.json").exists() == before


def test_pd_trigger_disposition_derated(capsys):
    """The shipped 1 oz override (codex C2) reads back as derated."""
    ws = real_board("pd-trigger")
    assert attest.main(["disposition", "--workspace", str(ws)]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["disposition"] == "derated"
    assert "COPPER WAIVER" in d["basis"]


# ------------------------------------------------- order_submit (consume)

class FakeSession:
    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def _hit(self, name, arg):
        self.calls.append((name, arg))
        return self.responses[name]

    def upload_gerber(self, p):
        return self._hit("upload_gerber", str(p))

    def audit(self, key, language=0):
        return self._hit("audit", key)

    def calculate(self, payload):
        return self._hit("calculate", payload)

    def create_order(self, payload):
        return self._hit("create_order", payload)

    def names(self):
        return [n for n, _ in self.calls]


def _ok(data):
    return {"ok": True, "code": 200, "message": "SUCCESS", "data": data,
            "http_status": 200, "trace_id": "T-1"}


@pytest.fixture
def api_env(monkeypatch):
    monkeypatch.setenv("HWDE_JLCPCB_APPID", "APP")
    monkeypatch.setenv("HWDE_JLCPCB_KEY", "KEY")
    monkeypatch.setenv("HWDE_JLCPCB_SECRET", "SECRET")


def _submit_argv(ws: Path, *extra):
    return ["--pcb", str(ws / "kicad" / f"{BOARD}.kicad_pcb"),
            "--fab-dir", str(ws / "fab"),
            "--quote", str(ws / "fab" / "quote.json"), "--qty", "5", *extra]


def test_order_submit_governed_without_attestation(tmp_path, capsys):
    """Plain run on a governed workspace with no valid attestation:
    manifest carries the release verdict, status not_order_ready, exit 1."""
    ws = green_ws(tmp_path)                      # green but NOT attested
    code = order_submit.main(_submit_argv(ws))
    assert code == 1
    man = json.loads((ws / "fab" / "order.json").read_text(encoding="utf-8"))
    assert man["status"] == "not_order_ready"
    assert man["release"]["governed"] and not man["release"]["valid"]


def test_order_submit_api_refuses_unattested_before_network(
        tmp_path, monkeypatch, api_env, capsys):
    ws = green_ws(tmp_path)
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(_submit_argv(ws, "--api")) == 2
    assert fake.calls == []                      # zero transport calls
    man = json.loads((ws / "fab" / "order.json").read_text(encoding="utf-8"))
    assert man["api"]["verdict"] == "refused"
    assert "attestation" in man["api"]["note"]


def test_order_submit_attested_api_quote_and_override_refusal(
        tmp_path, monkeypatch, api_env, capsys):
    """Valid attestation: the quote goes out at the ATTESTED copper weight;
    a --copper-oz contradicting it refuses (the pd-trigger case) with zero
    network traffic; a matching one is redundant and allowed."""
    ws = green_ws(tmp_path)
    att, problems = releaselib.build(ws)
    assert not problems, problems
    releaselib.write_attestation(ws, att)
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(_submit_argv(ws, "--api", "--copper-oz", "2")) \
        == 2
    assert fake.calls == []
    capsys.readouterr()
    fake = FakeSession(
        upload_gerber=_ok("FKEY1"),
        audit=_ok({"minLineWidth": 0.2}),
        calculate=_ok({"priceWithoutFreight": 9.9,
                       "achieveDateList": ["2026-09-01"],
                       "pcbCostInfo": {"totalFee": 9.9}}))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(_submit_argv(ws, "--api")) == 0
    assert fake.names() == ["upload_gerber", "audit", "calculate"]
    aq = json.loads((ws / "fab" / "api_quote.json").read_text(
        encoding="utf-8"))
    assert aq["copper_weight_oz"] == 1.0
    assert aq["copper_weight_source"] == "release attestation"
    assert aq["calculate_request"]["pcbParam"]["copperWeight"] == "1"


def test_order_submit_bare_fab_dir_stays_legacy(tmp_path, capsys):
    """No state.json beside the fab dir: ungoverned - the legacy manifest
    flow is preserved (fixture-style dirs, external one-off packages)."""
    fab = tmp_path / "fab"
    fab.mkdir()
    (fab / "b1_gerbers.zip").write_bytes(b"PK\x03\x04zip")
    (fab / "BOM.csv").write_text("Comment\n", encoding="utf-8")
    (fab / "CPL.csv").write_text("Designator\n", encoding="utf-8")
    pcb = tmp_path / "b1.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    code = order_submit.main(["--pcb", str(pcb), "--fab-dir", str(fab)])
    assert code == 0
    man = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert man["status"] == "ready_for_human"
    assert man["release"]["governed"] is False


# ----------------------------- adversarial-review fixes (T8 5-lens, U5)

def test_forged_minimal_attestation_refused(tmp_path):
    """Review CRITICAL: the seal is a checksum, so a forged attestation
    could omit sections and verify() used to skip their checks. verify now
    RE-DERIVES the required set - empty sections are named problems."""
    ws = green_ws(tmp_path)
    forged = {"script": "attest", "kind": "release_attestation",
              "attest_schema": releaselib.ATTEST_SCHEMA, "board": BOARD,
              "rev": 1, "supersedes": None, "created": "2026-08-14T00:00:00",
              "git_head": None,
              "checker_version": checklib.CHECKER_VERSION,
              "inputs": {}, "input_paths": {}, "gates": {}, "reports": {},
              "waivers": {}, "fab": {},
              "manufacturing": {"copper_weight_oz": 1.0, "layers": 2},
              "human_approvals": {}, "known_restrictions": []}
    forged["attestation_sha256"] = releaselib.seal(forged)
    assert releaselib.check_seal(forged)           # the seal is forgeable...
    v = releaselib.verify(ws, forged)
    assert not v["valid"]                          # ...the verify is not
    text = " | ".join(v["problems"])
    assert "input pcb: not bound" in text
    assert "gate erc: required but not bound" in text
    assert "report verify_release: not bound" in text
    assert "fab gerber_zip: not bound" in text
    assert "checkpoint 4: not bound approved" in text


def test_doctored_criteria_cannot_survive_verify(tmp_path):
    """Review CRITICAL: build criteria are canonical-only now, and verify
    RE-EVALUATES the bound reports - an attestation binding a failing
    strict report (however it got sealed) is invalid."""
    ws = green_ws(tmp_path)
    att, problems = releaselib.build(ws)
    assert not problems, problems
    bad = _write_report(
        ws, "verify_release.json", "verify_all",
        ws / "kicad" / f"{BOARD}.kicad_pcb",
        violations=[{"check": "check_current", "kind": "undersized_track",
                     "severity": "error", "net": "V48", "refs": [],
                     "pos": [3.0, 3.0], "msg": "x",
                     "source": "check_current"}])
    att["reports"]["verify_release"]["digest"] = statelib.hash_artifact(
        bad, "json_canonical")
    att["attestation_sha256"] = releaselib.seal(att)
    releaselib.write_attestation(ws, att)
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("re-evaluates to 1 failing" in p for p in v["problems"])


def test_hold_restriction_after_attestation_blocks(tmp_path):
    """Review CRITICAL: a fab/restrictions.json HOLD recorded after the
    attestation must invalidate it and block ordering."""
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    assert releaselib.verify(ws)["valid"]
    (ws / "fab" / "restrictions.json").write_text(json.dumps(
        {"restrictions": [{"kind": "hold", "note": "stop-ship",
                           "approved": "owner"}]}), encoding="utf-8")
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("HOLD" in p for p in v["problems"])
    assert releaselib.disposition(ws)["disposition"] == "blocked"


def test_corrupt_order_json_fails_closed(tmp_path, capsys):
    """Review CRITICAL: a present-but-unparsable fab/order.json used to
    read as 'no order', disarming the created-latch and reading
    order-ready. Now: disposition blocked; order_submit refuses (exit 2)
    WITHOUT rewriting the corrupt record."""
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    (ws / "fab" / "order.json").write_text("{corrupt", encoding="utf-8")
    d = releaselib.disposition(ws)
    assert d["disposition"] == "blocked" and "order.json" in d["basis"]
    code = order_submit.main(_submit_argv(ws))
    assert code == 2
    assert (ws / "fab" / "order.json").read_text(
        encoding="utf-8") == "{corrupt"          # record left untouched


def test_ambiguous_create_attempt_blocks(tmp_path):
    ws = green_ws(tmp_path)
    (ws / "fab" / "order.json").write_text(json.dumps(
        {"api": {"create_attempt": {"state": "in_flight",
                                    "at": "2026-08-14T00:00:00"}},
         "human_steps": []}), encoding="utf-8")
    d = releaselib.disposition(ws)
    assert d["disposition"] == "blocked" and "create attempt" in d["basis"]


def test_artifact_stale_mark_blocks_build_and_verify(tmp_path, capsys):
    """Review CRITICAL: an artifact-level stale mark (declared edit whose
    derived artifact was never regenerated) must block release."""
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    st = state_mod.State.load(ws / "state.json")
    st.set_artifact("gerbers", f"fab/{BOARD}_gerbers.zip")
    st.data["artifacts"]["gerbers"].setdefault("stale", []).append(
        {"ts": "2026-08-14T00:00:00", "edit_class": "reroute_net",
         "refs": [], "human_hold": 1})
    st.save()
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("artifact gerbers" in p for p in v["problems"])
    assert attest.main(["build", "--workspace", str(ws), "--force"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert any("artifact gerbers" in p for p in out["problems"])


def test_rejection_after_attestation_is_veto(tmp_path):
    """Review: a checkpoint recorded REJECTED after attestation (e.g. H5
    veto at order review) must invalidate even though it was never bound."""
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    st = state_mod.State.load(ws / "state.json")
    st.record_human("5", "rejected", "owner said no")
    st.save()
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("REJECTED after" in p for p in v["problems"])


def test_quote_option_edit_invalidates(tmp_path):
    """Review: thickness/finish/mask come from quote.json's spec - editing
    them after attestation must invalidate (quote PRICES may churn; its
    option values may not)."""
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    qp = ws / "fab" / "quote.json"
    doc = json.loads(qp.read_text(encoding="utf-8"))
    doc["spec"]["surface_finish"] = "ENIG"
    qp.write_text(json.dumps(doc), encoding="utf-8")
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("surface_finish" in p for p in v["problems"])


def test_registry_redirect_invalidates(tmp_path):
    """Review: a state.json artifact-registry redirect (same kind, other
    file) must not re-anchor verification - the attested PATH binds."""
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    import shutil
    shutil.copy2(ws / "kicad" / f"{BOARD}.kicad_pcb",
                 ws / "kicad" / "other.kicad_pcb")
    st = state_mod.State.load(ws / "state.json")
    st.data["artifacts"]["pcb"]["path"] = "kicad/other.kicad_pcb"
    st.save()
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("resolution moved" in p for p in v["problems"])


def test_checker_bump_invalidates_attestation(tmp_path, monkeypatch):
    ws = green_ws(tmp_path)
    att, _ = releaselib.build(ws)
    releaselib.write_attestation(ws, att)
    assert releaselib.verify(ws)["valid"]
    monkeypatch.setattr(checklib, "CHECKER_VERSION",
                        checklib.CHECKER_VERSION + 1)
    v = releaselib.verify(ws)
    assert not v["valid"]
    assert any("checker_version" in p for p in v["problems"])


def test_create_payload_manufacturing_binding(tmp_path, monkeypatch,
                                              api_env, capsys):
    """Review CRITICAL: --api-create must check the PAYLOAD's pcbParam (the
    fields JLC receives), not the quote file's advisory top-level copy."""
    ws = green_ws(tmp_path)
    att, problems = releaselib.build(ws)
    assert not problems, problems
    releaselib.write_attestation(ws, att)
    fake = FakeSession(
        upload_gerber=_ok("FKEY1"), audit=_ok({}),
        calculate=_ok({"priceWithoutFreight": 9.9,
                       "pcbCostInfo": {"totalFee": 9.9}}))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(_submit_argv(ws, "--api")) == 0
    capsys.readouterr()
    aq = ws / "fab" / "api_quote.json"
    doc = json.loads(aq.read_text(encoding="utf-8"))
    doc["calculate_request"]["pcbParam"]["copperWeight"] = "2"  # doctored
    aq.write_text(json.dumps(doc), encoding="utf-8")
    confirm = f"{BOARD} {doc['qty']}pcs {doc['grand_total']}"
    fake2 = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    code = order_submit.main(_submit_argv(
        ws, "--api-create", "--api-quote-file", str(aq),
        "--confirm", confirm))
    assert code == 2
    assert fake2.calls == []                     # refused before transport
    man = json.loads((ws / "fab" / "order.json").read_text(encoding="utf-8"))
    assert "pcbParam.copperWeight" in man["api"]["note"]


# --------------------------------------- reference attestations (migrated)

REFERENCE_BOARDS = ("stm32-blinky", "usb-buck")


def test_reference_attestations_verify_valid():
    """The two reference boards (codex: 'migrate into the new release
    manifest, issue final attestation') must carry attestations that verify
    VALID against the committed tree."""
    for board in REFERENCE_BOARDS:
        ws = real_board(board)
        att = releaselib.load_attestation(ws)
        assert att is not None, f"{board}: no attestation recorded"
        assert releaselib.check_seal(att), f"{board}: seal mismatch"
        v = releaselib.verify(ws)
        assert v["valid"], f"{board}: {v['problems']}"


def test_waivers_found_in_a_workspace_outside_any_boards_dir(tmp_path):
    """A board checked out in a worktree (~/.cc/worktrees/boards/<track>/<b>)
    has no boards dir above it; the workspace is recognised by state.json."""
    ws = tmp_path / "wt" / "track" / "myboard"
    (ws / "kicad").mkdir(parents=True)
    (ws / "reports").mkdir()
    (ws / "state.json").write_text("{}", encoding="utf-8")
    side = ws / "reports" / "verify-waivers.json"
    side.write_text("{}", encoding="utf-8")
    pcb = ws / "kicad" / "myboard.kicad_pcb"
    pcb.write_text("", encoding="utf-8")
    assert releaselib.waivers_for_input(pcb) == side
    # the place gate asks for its own sidecar by name, never verify's
    assert releaselib.waivers_for_input(pcb, "place-waivers.json") is None
    place = ws / "reports" / "place-waivers.json"
    place.write_text("{}", encoding="utf-8")
    assert releaselib.waivers_for_input(pcb, "place-waivers.json") == place
    # no sidecar anywhere -> None, as before
    side.unlink()
    assert releaselib.waivers_for_input(pcb) is None
