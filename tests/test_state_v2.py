"""T7 tests: state.json v2 - normalized artifact hashes, the invalidation
map, two-layer gate freshness, edit classes, the spawn ledger and v1->v2
migration.

All hermetic (pure venv: sexpdata/yaml/zipfile - no kicad-cli). Real-shape
inputs come from the committed, sha-pinned stage fixtures
(tests/fixtures/stages/pd_trigger) copied READ-ONLY into tmp workspaces.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import uuid as uuid_mod
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import state as state_mod  # noqa: E402
import state_migrate  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402
from _boards import BOARDS, need_shipped_checks  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "stages" / "pd_trigger"
GATES_YAML = REPO / ".claude" / "skills" / "hwde" / "reference" / "gates.yaml"
IMAP = statelib.load_map()


# ---------------------------------------------------------------------------
# normalizers
# ---------------------------------------------------------------------------
def _h(tmp_path: Path, name: str, content: str | bytes, norm: str):
    p = tmp_path / name
    if isinstance(content, str):
        p.write_bytes(content.encode("utf-8"))
    else:
        p.write_bytes(content)
    return statelib.hash_artifact(p, norm)


SEXPR = ('(kicad_pcb (version 20260206) (generator "pcbnew")\n'
         '  (segment (start 1 2) (end 3 4) (width 0.25) (layer "F.Cu")'
         ' (net 2) (uuid "1c164f86-879d-498f-9603-857017bc9f62"))\n)')


def test_sexpr_no_uuid_ignores_uuid_and_eol_churn(tmp_path):
    a = _h(tmp_path, "a.kicad_pcb", SEXPR, "sexpr_no_uuid")
    regen = SEXPR.replace("1c164f86-879d-498f-9603-857017bc9f62",
                          "eeed413b-a38f-4f20-a5f3-69733c50d18b")
    b = _h(tmp_path, "b.kicad_pcb", regen, "sexpr_no_uuid")
    crlf = _h(tmp_path, "c.kicad_pcb", SEXPR.replace("\n", "\r\n"),
              "sexpr_no_uuid")
    assert a == b == crlf
    assert a.startswith("sexpr_no_uuid:")
    moved = _h(tmp_path, "d.kicad_pcb", SEXPR.replace("(start 1 2)",
               "(start 1 2.1)"), "sexpr_no_uuid")
    assert moved != a


def test_sexpr_no_uuid_on_real_board_survives_full_reuuid(tmp_path):
    """Regeneration churn on the REAL routed pd-trigger board: replace every
    uuid with a fresh one -> identical hash; nudge one coordinate -> differs."""
    src = (FIXTURES / "route" / "pd-trigger.kicad_pcb").read_text(
        encoding="utf-8")
    a = _h(tmp_path, "a.kicad_pcb", src, "sexpr_no_uuid")
    reuuid = re.sub(r'\(uuid "[0-9a-fA-F-]+"\)',
                    lambda _: f'(uuid "{uuid_mod.uuid4()}")', src)
    assert reuuid != src
    b = _h(tmp_path, "b.kicad_pcb", reuuid, "sexpr_no_uuid")
    assert a == b
    m = re.search(r"\(start ([0-9.]+) ", src)
    nudged = src[:m.start(1)] + str(float(m.group(1)) + 0.01) + src[m.end(1):]
    c = _h(tmp_path, "c.kicad_pcb", nudged, "sexpr_no_uuid")
    assert c != a


def test_json_canonical_ignores_key_order_and_whitespace(tmp_path):
    a = _h(tmp_path, "a.json", '{"b": 1, "a": {"y": 2, "x": 3}}',
           "json_canonical")
    b = _h(tmp_path, "b.json", '{\n "a": {"x": 3, "y": 2},\n "b": 1\n}\n',
           "json_canonical")
    assert a == b
    c = _h(tmp_path, "c.json", '{"b": 1, "a": {"y": 2, "x": 4}}',
           "json_canonical")
    assert c != a


NETLIST = """(export (version "E")
  (design (source "C:/x/{name}.kicad_sch") (date "{date}") (tool "Eeschema"))
  (components
    (comp (ref "{c1}") (value "{v1}") (footprint "aiee:R0603"))
    (comp (ref "{c2}") (value "100nF") (footprint "aiee:C0603")))
  (nets
    (net (code "1") (name "{n1}")
      (node (ref "{c1}") (pin "1") (pintype "passive"))
      (node (ref "{c2}") (pin "2") (pintype "passive")))
    (net (code "2") (name "{n2}")
      (node (ref "{c1}") (pin "2") (pintype "passive")))))
"""


def netlist_text(date="A", v1="10k", swap=False):
    # swap reorders the component/net BLOCKS only, never the connectivity
    t = NETLIST.format(name="b", date=date, c1="R1", c2="C1", v1=v1,
                       n1="GND", n2="/SIG")
    if swap:
        comps = re.findall(r"\(comp .*?\)\)", t, re.S)
        nets = re.findall(r"\(net .*?\)\)\)", t, re.S)
        t = t.replace(comps[0], "@@0@@").replace(comps[1], "@@1@@")
        t = t.replace("@@0@@", comps[1]).replace("@@1@@", comps[0])
        t = t.replace(nets[0], "##0##").replace(nets[1], "##1##")
        t = t.replace("##0##", nets[1]).replace("##1##", nets[0])
    return t


def test_netlist_canonical_ignores_date_and_order(tmp_path):
    a = _h(tmp_path, "a.net", netlist_text(), "netlist_canonical")
    b = _h(tmp_path, "b.net", netlist_text(date="B", swap=True),
           "netlist_canonical")
    assert a == b and a.startswith("netlist_canonical:")
    c = _h(tmp_path, "c.net", netlist_text(v1="22k"), "netlist_canonical")
    assert c != a


def test_netlist_canonical_real_fixture_stable_under_date(tmp_path):
    src = (FIXTURES / "pd-trigger.net").read_text(encoding="utf-8")
    a = _h(tmp_path, "a.net", src, "netlist_canonical")
    redated = re.sub(r'\(date "[^"]*"\)', '(date "2099-01-01")', src)
    b = _h(tmp_path, "b.net", redated, "netlist_canonical")
    assert a == b


def test_text_eol_and_dir_text(tmp_path):
    a = _h(tmp_path, "a.kicad_dru", "(rule x)\n(rule y)\n", "text_eol")
    b = _h(tmp_path, "b.kicad_dru", "(rule x)\r\n(rule y)\r\n", "text_eol")
    assert a == b
    d1 = tmp_path / "sims1"
    d2 = tmp_path / "sims2"
    for d in (d1, d2):
        d.mkdir()
        (d / "tb1.cir").write_text("* tb\n.end\n", encoding="utf-8")
        (d / "tb1.bounds.json").write_text("{}", encoding="utf-8")
    h1 = statelib.hash_artifact(d1, "dir_text")
    h2 = statelib.hash_artifact(d2, "dir_text")
    assert h1 == h2
    (d2 / "tb2.cir").write_text("* other\n.end\n", encoding="utf-8")
    assert statelib.hash_artifact(d2, "dir_text") != h1


def test_gerber_design_routes_to_fabhash(tmp_path):
    import fabhash
    z = tmp_path / "x_gerbers.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("b-F_Cu.gtl", "%TF.CreationDate,2026*%\nG04 x*\nD10*\n")
    assert statelib.hash_artifact(z, "gerber_design") \
        == "gerber_design:" + fabhash.design_hash(z)


def test_hash_fallbacks(tmp_path):
    bad = _h(tmp_path, "bad.json", "{not json", "json_canonical")
    assert bad.startswith("raw:")
    assert statelib.hash_artifact(tmp_path / "absent.json",
                                  "json_canonical") is None
    with pytest.raises(ValueError):
        statelib.hash_artifact(tmp_path / "bad.json", "no_such_norm")


# ---------------------------------------------------------------------------
# invalidation map consistency
# ---------------------------------------------------------------------------
def test_map_covers_every_defined_gate():
    gates = yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]
    missing = [g for g in gates if g not in IMAP["gate_inputs"]]
    assert not missing, f"invalidation.yaml gate_inputs lacks: {missing}"


def test_map_has_the_ten_plan_edit_classes():
    assert set(IMAP["edit_classes"]) == {
        "move_fp", "swap_part_same_fp", "swap_part_new_fp", "add_part",
        "del_part", "reroute_net", "silk_edit", "plane_edit", "rule_change",
        "stackup_change",
        # U17: the board outline became editable (board_edit.py)
        "outline_change"}
    for ec in IMAP["edit_classes"].values():
        assert ec["human_hold"] in (0, 1, 2, 3)
    # the plan's calibration anchors: a one-footprint move carries no
    # checkpoint ceremony; a stackup change is the fab-truth class
    assert IMAP["edit_classes"]["move_fp"]["human_hold"] <= 1
    assert IMAP["edit_classes"]["silk_edit"]["human_hold"] == 0
    assert IMAP["edit_classes"]["stackup_change"]["human_hold"] == 3


def test_map_loader_rejects_unknown_names(tmp_path):
    bad = {"version": 1,
           "artifact_kinds": {"pcb": {"path": "k/{board}.kicad_pcb",
                                      "norm": "sexpr_no_uuid"}},
           "gate_inputs": {"drc": ["pcb"]},
           "edit_classes": {"move_fp": {"mutates": ["pcb"],
                                        "stale_artifacts": ["nope"],
                                        "gates": ["drc"], "human_hold": 1}}}
    p = tmp_path / "inv.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown artifact kinds"):
        statelib.load_map(p)


# ---------------------------------------------------------------------------
# record-gate input capture + hash-layer freshness
# ---------------------------------------------------------------------------
def ws_with_files(tmp_path: Path, board="b") -> state_mod.State:
    ws = tmp_path / "ws"
    st = state_mod.State.init(ws, board, "P4")
    (ws / "kicad" / f"{board}.kicad_sch").write_text(SEXPR, encoding="utf-8")
    (ws / "kicad" / f"{board}.kicad_pro").write_text('{"a": 1}',
                                                     encoding="utf-8")
    return st


PASS = {"status": "pass", "failing_count": 0, "counts": {"total": 0}}


def test_record_gate_captures_input_hashes_and_autoregisters(tmp_path):
    st = ws_with_files(tmp_path)
    g = st.record_gate("erc", PASS, phase="P4")
    inputs = g["last"]["inputs"]
    assert set(inputs) == {"sch", "pro"}
    assert inputs["sch"].startswith("sexpr_no_uuid:")
    assert inputs["pro"].startswith("json_canonical:")
    # XC-8: hashed kinds auto-register (the registry is no longer manual-only)
    reg = st.data["artifacts"]
    assert reg["sch"]["path"] == "kicad/b.kicad_sch"
    assert reg["sch"]["sha256"] == inputs["sch"]
    fr = st.freshness()
    assert fr["gates"]["erc"]["fresh"] is True
    assert fr["summary"]["stale"] == []


def test_hash_layer_catches_undeclared_edits(tmp_path):
    st = ws_with_files(tmp_path)
    st.record_gate("erc", PASS, phase="P4")
    sch = tmp_path / "ws" / "kicad" / "b.kicad_sch"
    sch.write_text(SEXPR.replace("(start 1 2)", "(start 9 9)"),
                   encoding="utf-8")
    fr = st.freshness()
    assert fr["gates"]["erc"]["hash_valid"] is False
    assert fr["gates"]["erc"]["changed_inputs"] == ["sch"]
    assert fr["summary"]["stale"] == ["erc"]
    # re-running the gate re-records against the new content -> fresh again
    st.record_gate("erc", PASS)
    assert st.freshness()["gates"]["erc"]["fresh"] is True


def test_uuid_regeneration_does_not_invalidate(tmp_path):
    st = ws_with_files(tmp_path)
    st.record_gate("erc", PASS, phase="P4")
    sch = tmp_path / "ws" / "kicad" / "b.kicad_sch"
    sch.write_text(SEXPR.replace("1c164f86-879d-498f-9603-857017bc9f62",
                                 str(uuid_mod.uuid4())), encoding="utf-8")
    assert st.freshness()["gates"]["erc"]["fresh"] is True


def test_pre_v2_gate_reports_unknown_not_fresh():
    entry = {"status": "pass", "last": {"ts": "t", "status": "pass"}}
    v = statelib.gate_freshness(entry, {"sch": "x"})
    assert v["hash_valid"] is None and v["fresh"] is False


# ---------------------------------------------------------------------------
# edit classes (unit: every class marks exactly its mapped set)
# ---------------------------------------------------------------------------
ALL_GATES = sorted(IMAP["gate_inputs"])


def state_with_all_gates(tmp_path: Path, name="ws") -> state_mod.State:
    st = state_mod.State.init(tmp_path / name, "b", "P8")
    for g in ALL_GATES:
        st.record_gate(g, PASS)
    return st


@pytest.mark.parametrize("cls", sorted(IMAP["edit_classes"]))
def test_edit_class_marks_exactly_the_mapped_set(tmp_path, cls):
    ec = IMAP["edit_classes"][cls]
    st = state_with_all_gates(tmp_path)
    rec = st.apply_edit(cls, refs=["X1"])
    assert rec["human_hold"] == ec["human_hold"]
    assert rec["gates_marked"] == list(ec["gates"])
    fr = st.freshness()
    assert set(fr["summary"]["stale"]) == set(ec["gates"])
    for g in ALL_GATES:
        expect_marked = g in ec["gates"]
        marks = fr["gates"][g]["stale_marks"]
        assert bool(marks) == expect_marked, (cls, g)
        assert fr["gates"][g]["fresh"] == (not expect_marked), (cls, g)
        # the hash layer is untouched by a simulated (declared-only) edit
        assert fr["gates"][g]["hash_valid"] is True
    marked_arts = {n for n, a in fr["artifacts"].items()
                   if a["stale_marks"]}
    assert marked_arts == set(ec["stale_artifacts"]), cls
    assert fr["summary"]["human_hold_pending"] == ec["human_hold"]
    assert st.data["edits"][-1]["class"] == cls


def test_edit_unknown_class_refused(tmp_path):
    st = state_with_all_gates(tmp_path)
    with pytest.raises(CheckError, match="unknown edit class"):
        st.apply_edit("repaint_soldermask")


def test_edit_marks_only_recorded_gates(tmp_path):
    st = state_mod.State.init(tmp_path / "ws", "b", "P6")
    st.record_gate("erc", PASS)          # only erc has a result
    rec = st.apply_edit("add_part")
    assert rec["gates_marked"] == ["erc"]           # nothing else to distrust
    assert set(rec["gates"]) == set(IMAP["edit_classes"]["add_part"]["gates"])


# ---------------------------------------------------------------------------
# THE acceptance case: simulated move_fp on the pd-trigger fixture
# ---------------------------------------------------------------------------
def build_pd_trigger_ws(tmp_path: Path) -> state_mod.State:
    """Assemble a real-content workspace from the frozen pd-trigger stage
    fixtures (copied read-only): routed board + rules, schematic, sidecars,
    netlist, shipped gerber zip."""
    ws = tmp_path / "ws"
    st = state_mod.State.init(ws, "pd-trigger", "P9")
    k = ws / "kicad"
    for src, dst in [
            ("route/pd-trigger.kicad_pcb", "pd-trigger.kicad_pcb"),
            ("route/pd-trigger.kicad_pro", "pd-trigger.kicad_pro"),
            ("route/pd-trigger.kicad_dru", "pd-trigger.kicad_dru"),
            ("sch/pd-trigger.kicad_sch", "pd-trigger.kicad_sch"),
            ("constraints.json", "constraints.json"),
            ("decoupling.json", "decoupling.json"),
            ("pd-trigger.net", "pd-trigger.net")]:
        shutil.copy2(FIXTURES / src, k / dst)
    (ws / "fab").mkdir(exist_ok=True)
    shutil.copy2(FIXTURES / "fab" / "pd-trigger_gerbers.zip",
                 ws / "fab" / "pd-trigger_gerbers.zip")
    st.set_artifact("gerbers", "fab/pd-trigger_gerbers.zip")
    for g in ALL_GATES:
        st.record_gate(g, PASS)
    return st


def test_simulated_move_fp_marks_exactly_the_mapped_set(tmp_path):
    st = build_pd_trigger_ws(tmp_path)
    fr = st.freshness()
    assert fr["summary"]["stale"] == [] and fr["summary"]["unknown"] == []
    assert all(v["fresh"] for v in fr["gates"].values())
    # real (non-null) hashes on the real files
    drc = st.data["gates"]["drc_routed"]["last"]["inputs"]
    assert all(drc[k] for k in ("pcb", "sch", "pro", "dru"))

    st.apply_edit("move_fp", refs=["J1"], note="connector nudge")
    fr = st.freshness()
    assert fr["summary"]["stale"] == sorted(
        ["place", "drc", "drc_routed", "verify", "dfm"])
    assert fr["gates"]["erc"]["fresh"] and fr["gates"]["sim"]["fresh"]
    marked = {n for n, a in fr["artifacts"].items() if a["stale_marks"]}
    assert marked == {"gerbers"}
    assert fr["summary"]["human_hold_pending"] == 1

    # layer semantics: the pcb file is untouched, so marks are the ONLY
    # staleness source; a re-run gate is current again...
    assert fr["gates"]["drc_routed"]["hash_valid"] is True
    st.record_gate("drc_routed", PASS)
    fr = st.freshness()
    assert fr["gates"]["drc_routed"]["fresh"] is True
    # ...but the gerber package still does not derive from the edited board:
    # a bare rehash (unchanged zip) keeps its mark, explicit re-export clears
    st.rehash()
    assert st.freshness()["artifacts"]["gerbers"]["stale_marks"]
    st.rehash(["gerbers"])
    fr = st.freshness()
    assert fr["artifacts"]["gerbers"]["stale_marks"] == []
    assert fr["summary"]["human_hold_pending"] == 1  # gate marks remain


def test_real_pcb_edit_hash_invalidates_every_pcb_gate(tmp_path):
    st = build_pd_trigger_ws(tmp_path)
    pcb = tmp_path / "ws" / "kicad" / "pd-trigger.kicad_pcb"
    text = pcb.read_text(encoding="utf-8")
    m = re.search(r"\(start ([0-9.]+) ", text)
    pcb.write_text(text[:m.start(1)] + str(float(m.group(1)) + 0.1)
                   + text[m.end(1):], encoding="utf-8")
    fr = st.freshness()
    pcb_gates = {g for g, kinds in IMAP["gate_inputs"].items()
                 if "pcb" in kinds and g in fr["gates"]}
    for g in pcb_gates:
        assert fr["gates"][g]["hash_valid"] is False, g
        assert fr["gates"][g]["changed_inputs"] == ["pcb"], g
    assert fr["gates"]["erc"]["fresh"] and fr["gates"]["sim"]["fresh"]


# ---------------------------------------------------------------------------
# spawn ledger + log hygiene
# ---------------------------------------------------------------------------
def test_spawn_ledger_first_class(tmp_path):
    st = ws_with_files(tmp_path)
    st.record_spawn({"role": "fixer", "model": "opus", "effort": "high",
                     "phase": "P7", "tokens": 41000, "note": None})
    assert st.data["spawns"][0]["role"] == "fixer"
    assert "note" not in st.data["spawns"][0]        # None keys dropped
    assert st.data["history"][-1]["event"] == "spawn"


def test_cli_log_spawn_routes_to_ledger_and_bad_names_refused(tmp_path, capsys):
    ws = tmp_path / "ws"
    assert state_mod.main(["init", "--workspace", str(ws),
                           "--board", "b"]) == 0
    capsys.readouterr()
    rc = state_mod.main(["log", "--workspace", str(ws), "--event", "spawn",
                         "--data",
                         '{"role": "router", "model": "fable", "phase": "P7"}'])
    assert rc == 0
    capsys.readouterr()
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["spawns"][0]["model"] == "fable"
    # XC-8: prose event names refused (a live run stored paragraphs as keys)
    rc = state_mod.main(["log", "--workspace", str(ws), "--event",
                         "The router failed so I respawned it"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert "machine keys" in err["error"]
    rc = state_mod.main(["spawn", "--workspace", str(ws), "--role", "fixer",
                         "--model", "opus", "--tokens", "12000"])
    assert rc == 0
    capsys.readouterr()
    data = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert data["spawns"][1]["tokens"] == 12000


def test_cli_edit_and_freshness(tmp_path, capsys):
    ws = tmp_path / "ws"
    state_mod.main(["init", "--workspace", str(ws), "--board", "b"])
    capsys.readouterr()
    rc = state_mod.main(["edit", "--workspace", str(ws), "--class",
                         "silk_edit", "--refs", "U1", "U2"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["edit"]["human_hold"] == 0
    rc = state_mod.main(["edit", "--workspace", str(ws), "--class", "nope"])
    assert rc == 2
    capsys.readouterr()
    rc = state_mod.main(["freshness", "--workspace", str(ws)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cmd"] == "freshness" and "summary" in out
    # freshness is read-only: updated stamp untouched
    before = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    state_mod.main(["freshness", "--workspace", str(ws)])
    capsys.readouterr()
    after = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert before == after


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------
def make_v1_state(ws: Path, with_spawn_event=True) -> Path:
    """A v1 payload mirroring the real pd-trigger shape (gates with history,
    artifacts as path strings, log events)."""
    (ws / "kicad").mkdir(parents=True)
    (ws / "kicad" / "m.kicad_pcb").write_text(SEXPR, encoding="utf-8")
    history = [
        {"ts": "2026-07-28T05:57:45", "event": "init", "board": "m",
         "phase": "P0"},
        {"ts": "2026-07-28T10:14:30", "event": "gate", "gate": "place",
         "status": "pass", "attempt": 1, "failing_count": 0},
    ]
    if with_spawn_event:
        history.append({"ts": "2026-07-28T10:20:00", "event": "spawn",
                        "role": "router", "model": "fable", "phase": "P7"})
    data = {
        "version": 1, "board": "m", "workspace": str(ws).replace("\\", "/"),
        "created": "2026-07-28T05:57:45", "updated": "2026-07-28T11:56:34",
        "phase": "P10",
        "gates": {"place": {"phase": "P6", "status": "pass", "attempts": 1,
                            "last": {"ts": "t", "status": "pass",
                                     "failing_count": 0, "total": 0},
                            "history": [{"ts": "t", "status": "pass",
                                         "failing_count": 0, "total": 0}]}},
        "human": {"1": {"status": "approved", "ts": "t", "note": "ok"}},
        "artifacts": {"pcb": "kicad/m.kicad_pcb", "order": "fab/order.json"},
        "open_issues": [], "next_issue_id": 1,
        "budgets": {"fix_loops": {"erc": 3}},
        "decisions": [{"what": "w", "why": "y", "phase": "P0", "ts": "t"}],
        "history": history,
    }
    p = ws / "state.json"
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return p


def test_migration_v1_to_v2_and_idempotency(tmp_path):
    ws = tmp_path / "ws"
    p = make_v1_state(ws)
    with pytest.raises(CheckError, match="state_migrate"):
        state_mod.State.load(p)          # v2-only loader names the fix
    payload, _ = state_migrate.run(["--workspace", str(ws)])
    assert payload["migrated"] == 1
    rec = payload["workspaces"][0]
    assert rec["artifacts_hashed"] == 1          # pcb exists, order.json not
    assert rec["spawns_lifted"] == 1
    st = state_mod.State.load(p)                 # loads clean now
    arts = st.data["artifacts"]
    assert arts["pcb"]["kind"] == "pcb"
    assert arts["pcb"]["sha256"].startswith("sexpr_no_uuid:")
    assert arts["order"]["sha256"] is None
    assert st.data["spawns"][0]["role"] == "router"
    assert st.data["edits"] == []
    assert st.data["history"][-1]["event"] == "migrated"
    # pre-v2 gate results are honestly unverifiable, not assumed fresh
    fr = st.freshness()
    assert fr["gates"]["place"]["hash_valid"] is None
    assert fr["summary"]["unknown"] == ["place"]
    # idempotent: second run is a byte-level no-op
    before = p.read_bytes()
    payload, _ = state_migrate.run(["--workspace", str(ws)])
    assert payload["migrated"] == 0 and payload["already_v2"] == 1
    assert p.read_bytes() == before


def test_migration_does_not_bless_name_collisions_as_kind_overrides(tmp_path):
    """Live defect caught at T7 migration: lumina-strobe's v1 registry named
    an entry "constraints" pointing at architecture/constraints.json - NOT the
    kicad/ sidecar the verify gate reads. Blessing it as a kind override would
    make gate-input hashing track the wrong file and MISS real staleness."""
    ws = tmp_path / "ws"
    p = make_v1_state(ws)
    data = json.loads(p.read_text(encoding="utf-8"))
    (ws / "architecture").mkdir()
    (ws / "architecture" / "constraints.json").write_text('{"p2": true}',
                                                          encoding="utf-8")
    data["artifacts"]["constraints"] = "architecture/constraints.json"
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    state_migrate.run(["--workspace", str(ws)])
    st = state_mod.State.load(p)
    entry = st.data["artifacts"]["constraints"]
    assert entry["kind"] is None                 # name kept, kind refused
    assert entry["sha256"].startswith("json_canonical:")
    # rehash must not resurrect the kind from the entry's name
    st.rehash()
    assert st.data["artifacts"]["constraints"]["kind"] is None
    # gate-input resolution ignores the mismatched-kind name collision and
    # hashes the kind's DEFAULT location, not the architecture file
    (ws / "kicad" / "constraints.json").write_text('{"gate": true}',
                                                   encoding="utf-8")
    g = st.record_gate("verify", PASS)
    expected = statelib.hash_artifact(ws / "kicad" / "constraints.json",
                                      "json_canonical")
    assert g["last"]["inputs"]["constraints"] == expected
    # ...and the auto-register then reclaims the kind-named slot with the
    # typed entry (the migrated pointer defended only the pre-regate window)
    entry = st.data["artifacts"]["constraints"]
    assert entry["kind"] == "constraints"
    assert entry["path"] == "kicad/constraints.json"


def test_migration_sweep_and_bad_version(tmp_path):
    root = tmp_path / "boards"
    for name in ("b1", "b2"):
        make_v1_state(root / name)
    payload, _ = state_migrate.run(["--boards-dir", str(root)])
    assert payload["migrated"] == 2
    bad = root / "b3"
    bad.mkdir()
    (bad / "state.json").write_text('{"version": 7}', encoding="utf-8")
    rc = state_migrate.main(["--boards-dir", str(root)])
    assert rc == 2


def test_all_live_workspaces_are_v2():
    """Standing invariant: the six committed board workspaces stay migrated
    (the T7 session ran state_migrate over boards/)."""
    need_shipped_checks()
    states = sorted(BOARDS.glob("*/state.json"))
    if not states:
        pytest.skip(f"needs the boards repo ({BOARDS}; set HWDE_BOARDS_ROOT)")
    assert len(states) >= 6
    for p in states:
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data.get("version") == 2, p


# ---------------------------------------------------------------------------
# resume summary freshness surface
# ---------------------------------------------------------------------------
def test_resume_summary_reports_freshness(tmp_path):
    st = build_pd_trigger_ws(tmp_path)
    s = st.resume_summary()
    assert s["gates_stale"] == [] and s["human_hold_pending"] == 0
    assert set(s["gates_passed_fresh"]) == set(s["gates_passed"])
    st.apply_edit("plane_edit", refs=["GND"])
    s = st.resume_summary()
    assert "verify" in s["gates_stale"] and "drc_routed" in s["gates_stale"]
    assert "verify" not in s["gates_passed_fresh"]
    assert s["human_hold_pending"] == 2
