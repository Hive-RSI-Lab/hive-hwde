"""U7 - learning mode harness: the `learn` verb, the learner contract, and
graded-fixture capture (bench.py --freeze).

The four acceptance legs:
  1. `learn` plans on a real workspace with every step bound.
  2. Fixture-freeze round-trips: --freeze -> --baseline -> --compare exits 1
     on a seeded regression (and 0 on the frozen original). Frozen copies are
     LF-normalized so their pins equal what a fresh clone checks out.
  3. The learner contract passes registry lint: the agent file resolves from
     the verb, every command it quotes uses declared flags, and its
     classed-edit vocabulary IS learnlib.PROMOTE_KINDS (no second taxonomy).
  4. A dry-run with a scripted stand-in critique produces a classed edit
     through the U6 queue (capture -> compile -> resolve) and the plan
     carries the mechanical exit checklist.
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

import bench  # noqa: E402
import benchlib  # noqa: E402
import learnings as lcli  # noqa: E402
import learnlib  # noqa: E402
import task_router as tr  # noqa: E402
from checklib import CheckError  # noqa: E402
from _boards import board_path  # noqa: E402

RF_DE = board_path("rf-de-20m")

GOLDEN = ROOT / "tests" / "golden" / "blinky2"
S7_NET = ROOT / "tests" / "s7_regen" / "blinky2" / "golden.net"

CHECKLIST_MARKS = ("--freeze", "--baseline", "--compare",
                   "knowledge.py --validate", "learnings.py validate")


def _plan(extra: list[str] | None = None, workspace: Path | None = RF_DE):
    argv = ["--verb", "learn"]
    if workspace:
        argv += ["--workspace", str(workspace)]
    payload, _ = tr.run(argv + (extra or []))
    return payload


def _fresh_manifest(tmp_path: Path) -> Path:
    m = tmp_path / "manifest.yaml"
    m.write_text("# tmp stage-fixture manifest (freeze test)\n"
                 "version: 1\nfixtures:\n", encoding="utf-8", newline="\n")
    return m


def _freeze_p2(m: Path, fid: str = "t_learn_arch",
               extra: list[str] | None = None) -> dict:
    payload, _ = bench.run([
        "--freeze", "--stage", "P2", "--fixture", fid, "--board", "blinky2",
        "--manifest", str(m),
        "--from", f"constraints={GOLDEN / 'constraints.json'}",
        "--from", f"netlist={S7_NET}",
        "--grade", "owner: approved (stand-in)"] + (extra or []))
    return payload


# ---------------------------------------------------------------------------
# 1. the verb plans on a real workspace, every step bound
# ---------------------------------------------------------------------------
def test_learn_plans_on_a_real_workspace_every_step_bound():
    payload = _plan(["--arg", "stage=P7",
                     "--arg", "fixture=golden_usbbuck4_route"])
    assert payload["status"] == "planned", payload.get("question")
    plan = payload["recipe"]
    assert plan["human_hold"] == 3 and plan["edit_class"] is None
    steps = plan["steps"]
    assert steps
    for s in steps:
        if s["kind"] in ("script", "gate"):
            assert "{" not in s["command"], s["command"]
        assert not s.get("free_slots"), s
    agent = next(s for s in steps if s["kind"] == "agent")
    assert agent["role"] == "learner" and agent["tier"] == "fable/max"
    assert (SKILL / "agents" / "learner.md").is_file()
    hold = next(s for s in steps if s["kind"] == "human")
    assert hold["hold"] == 3
    cmds = " ".join(s.get("command", "") for s in steps)
    # pre-load: the U6 queue (stage-scoped) and the U4 knowledge selection
    assert "learnings.py queue" in cmds
    assert "--status pending --stage P7" in cmds
    assert "knowledge.py --select" in cmds
    assert str(RF_DE).replace("\\", "/") in cmds
    # the mechanical exit checklist rides the plan
    for mark in CHECKLIST_MARKS:
        assert mark in cmds, mark
    # scorer-divergence rule is in the plan, not only in the doc
    notes = " ".join(s.get("note", "") for s in steps if s["kind"] == "note")
    assert "missing a term" in notes


def test_learn_without_stage_asks_instead_of_guessing():
    payload = _plan()
    assert payload["status"] == "needs_args"
    asked = {n["arg"] for n in payload["needs"]}
    assert "stage" in asked


def test_learn_teaching_phrasings_do_not_collide_with_promote():
    tasks = tr.load_tasks()
    for text in ("teach the buck placement stage",
                 "run a learning session for P6 with graded fixtures"):
        got = tr.match_verbs(text, tasks)
        assert got and got[0]["verb"] == "learn", (text, got)
        if len(got) > 1:
            assert got[0]["score"] > got[1]["score"], (text, got)
    got = tr.match_verbs("promote the learnings from this run", tasks)
    assert got and got[0]["verb"] == "promote"


# ---------------------------------------------------------------------------
# 2. fixture freeze round-trips (approve -> baseline -> compare)
# ---------------------------------------------------------------------------
def test_freeze_baseline_then_seeded_regression_exits_1(tmp_path, capsys):
    m = _fresh_manifest(tmp_path)
    payload = _freeze_p2(m, extra=["--freeze-args", '{"stackup": null}'])
    assert payload["status"] == "pass"
    frozen = tmp_path / "t_learn_arch" / "constraints.json"
    assert frozen.is_file()
    assert b"\r\n" not in frozen.read_bytes()  # pins match an LF checkout
    # the append preserved the manifest's comment header
    text = m.read_text(encoding="utf-8")
    assert text.startswith("# tmp stage-fixture manifest")
    entry = benchlib.load_manifest(m)["fixtures"]["t_learn_arch"]
    assert entry["provenance"]["grade"] == "owner: approved (stand-in)"
    assert entry["args"] == {"stackup": None}

    assert bench.main(["--stage", "P2", "--fixture", "t_learn_arch",
                       "--manifest", str(m), "--baseline"]) == 0
    capsys.readouterr()
    assert (tmp_path / "baselines" / "t_learn_arch.score.json").is_file()

    # the frozen original still scores clean against its own baseline
    assert bench.main(["--stage", "P2", "--fixture", "t_learn_arch",
                       "--manifest", str(m), "--compare"]) == 0
    capsys.readouterr()

    # seeded regression: a placement ref that does not exist in the netlist
    mutant = tmp_path / "mutant_constraints.json"
    data = json.loads((GOLDEN / "constraints.json").read_text(encoding="utf-8"))
    data.setdefault("placement", {}).setdefault("fixed", []).append("ZZ99")
    mutant.write_text(json.dumps(data), encoding="utf-8")
    code = bench.main(["--stage", "P2", "--fixture", "t_learn_arch",
                       "--manifest", str(m), "--artifact", str(mutant),
                       "--compare"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["baseline"]["regressed"] is True and out["baseline"]["delta"] < 0


def test_freeze_auto_includes_kicad_siblings_and_matches_index_pins(tmp_path,
                                                                    capsys):
    """A .kicad_pcb source brings its stem-matched .kicad_pro along (the T5
    orphaned-board lesson), and LF normalization makes the frozen pins equal
    the repo manifest's pins for the very same golden files - i.e. what a
    fresh clone checks out, not this working tree's bytes."""
    m = _fresh_manifest(tmp_path)
    payload, _ = bench.run([
        "--freeze", "--stage", "P6", "--fixture", "t_learn_place",
        "--board", "blinky2", "--manifest", str(m),
        "--from", f"pcb={GOLDEN / 'blinky2.kicad_pcb'}",
        "--from", f"constraints={GOLDEN / 'constraints.json'}",
        "--from", f"decoupling={GOLDEN / 'decoupling.json'}"])
    assert "pro" in payload["files"], "sibling .kicad_pro not auto-included"
    repo_entry = benchlib.load_manifest()["fixtures"]["golden_blinky2_place"]
    for name in ("pcb", "constraints", "decoupling"):
        assert payload["files"][name]["sha256"] == \
            repo_entry["files"][name]["sha256"], name

    assert bench.main(["--stage", "P6", "--fixture", "t_learn_place",
                       "--manifest", str(m), "--baseline"]) == 0
    capsys.readouterr()
    assert bench.main(["--stage", "P6", "--fixture", "t_learn_place",
                       "--manifest", str(m), "--compare"]) == 0
    capsys.readouterr()


def test_freeze_refusals(tmp_path):
    m = _fresh_manifest(tmp_path)
    _freeze_p2(m)
    with pytest.raises(CheckError, match="already exists"):
        _freeze_p2(m)  # same id again
    with pytest.raises(CheckError, match="'constraints'"):
        bench.run(["--freeze", "--stage", "P2", "--fixture", "t_no_primary",
                   "--board", "b", "--manifest", str(m),
                   "--from", f"netlist={S7_NET}"])
    with pytest.raises(CheckError, match="--board"):
        bench.run(["--freeze", "--stage", "P2", "--fixture", "t_no_board",
                   "--manifest", str(m),
                   "--from", f"constraints={GOLDEN / 'constraints.json'}"])
    with pytest.raises(CheckError, match="ASCII"):
        _freeze_p2(m, fid="t_bad_grade",
                   extra=["--note", "approuvé"])
    with pytest.raises(CheckError, match="JSON object"):
        _freeze_p2(m, fid="t_bad_args", extra=["--freeze-args", '["x"]'])
    with pytest.raises(CheckError, match="basename"):
        bench.run(["--freeze", "--stage", "P2", "--fixture", "t_basename",
                   "--board", "b", "--manifest", str(m),
                   "--from", f"constraints={GOLDEN / 'constraints.json'}",
                   "--from", f"netlist={GOLDEN / 'constraints.json'}"])
    with pytest.raises(CheckError, match="--freeze"):
        bench.run(["--stage", "P2", "--fixture", "t_learn_arch",
                   "--manifest", str(m), "--board", "b"])  # freeze-only flag
    with pytest.raises(CheckError, match="separate invocation"):
        _freeze_p2(m, fid="t_combo", extra=["--baseline"])


# ---------------------------------------------------------------------------
# 3. the learner contract passes registry lint
# ---------------------------------------------------------------------------
def test_learner_contract_passes_registry_lint():
    assert tr.validate_registry() == []
    doc = SKILL / "agents" / "learner.md"
    text = doc.read_text(encoding="utf-8")
    assert text.isascii()
    problems: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        problems += tr._check_command_text(f"learner.md:{i}", line)
    assert problems == []


def test_learner_edit_vocabulary_is_learnlib_promote_kinds():
    """Design decision: the classed-edit vocabulary is the U6 promotion-kind
    set, reused verbatim - a second taxonomy is how the queue and the
    teaching sessions drift apart."""
    text = (SKILL / "agents" / "learner.md").read_text(encoding="utf-8")
    for kind in learnlib.PROMOTE_KINDS:
        assert kind in text, kind


def test_recipe_doc_names_the_checklist_and_the_divergence_rule():
    text = (SKILL / "reference" / "recipes" / "learn.md").read_text(
        encoding="utf-8")
    assert "MISSING A TERM" in text
    for mark in CHECKLIST_MARKS:
        assert mark in text, mark
    for kind in learnlib.PROMOTE_KINDS:
        assert kind in text, kind


# ---------------------------------------------------------------------------
# 4. dry-run: a scripted stand-in critique becomes a classed edit
# ---------------------------------------------------------------------------
def test_scripted_critique_round_trips_to_a_classed_edit(tmp_path, capsys):
    """The loop's bookkeeping without an LLM: the owner's critique is captured
    as a workspace learning, compiled into the U6 queue (the learner's
    pre-load), and resolved as a classed artifact edit whose kind comes from
    PROMOTE_KINDS. This is the exit checklist's 'index the session's edits'
    path, exercised end to end."""
    ws = tmp_path / "teach-p6"
    ws.mkdir()
    (ws / "LEARNINGS.md").write_text(
        "# LEARNINGS - teach-p6 (learning-mode session capture)\n\n"
        "## 2026-08-14 [P6][placement] Owner critique: input cap sits 9.9mm "
        "from VIN\n\nStand-in critique for the dry run; the hot loop must "
        "price input-cap distance, scripts/place_anneal.py owns the term.\n",
        encoding="utf-8", newline="\n")

    assert lcli.main(["compile", "--workspace", str(ws)]) == 0
    capsys.readouterr()
    assert lcli.main(["queue", "--workspace", str(ws), "--status", "pending",
                      "--stage", "P6"]) == 0
    queue_view = json.loads(capsys.readouterr().out)
    rows = queue_view["entries"]
    assert len(rows) == 1 and rows[0]["stage"] == "P6"
    eid = rows[0]["entry"]
    assert rows[0]["targets"] == ["scripts/place_anneal.py"]

    assert lcli.main(["resolve", "--workspace", str(ws), "--entry", eid,
                      "--status", "promoted", "--kind", "cost_term",
                      "--level", "L3",
                      "--targets", "scripts/place_anneal.py",
                      "--reason",
                      "owner critique -> anneal input-cap distance term"]) == 0
    capsys.readouterr()
    assert lcli.main(["validate", "--workspace", str(ws)]) == 0
    capsys.readouterr()

    queue = yaml.safe_load(
        (ws / "learnings" / "queue.yaml").read_text(encoding="utf-8"))
    row = next(r for r in queue["entries"] if r["entry"] == eid)
    assert row["status"] == "promoted"
    assert row["resolution"]["kind"] == "cost_term"
    assert row["resolution"]["kind"] in learnlib.PROMOTE_KINDS
    assert row["resolution"]["artifacts"] == ["scripts/place_anneal.py"]

    # and the plan the session runs under carries the mechanical checklist
    payload = _plan(["--arg", "stage=P6",
                     "--arg", "fixture=pd_trigger_place"])
    cmds = " ".join(s.get("command", "")
                    for s in payload["recipe"]["steps"])
    for mark in CHECKLIST_MARKS:
        assert mark in cmds, mark
