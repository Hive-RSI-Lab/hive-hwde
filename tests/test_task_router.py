"""T10 - the task router: taxonomy, recipes, and the front door.

Four contracts are pinned here:
  1. The registry is REAL: every script, flag, subcommand, agent prompt, gate,
     edit class and recipe doc named in tasks.yaml exists. T4's lesson was a
     hallucinated route_edit envelope that would have failed every widen a
     fixer tried; this test is that lesson applied to the recipes.
  2. Matching is deterministic and honest: a clear task gets one verb, an
     ambiguous one gets candidates (never a coin flip), an unknown one asks.
  3. Every verb plans END TO END on a real workspace fixture - the golden-path
     dry-run the v2 plan asks for - with its gates and ceremony taken from
     invalidation.yaml rather than restated in the recipe.
  4. full-run is a recipe like any other: its gate sequence IS the pipeline's
     GATE_ORDER, so the "special case, not a separate code path" claim is
     mechanically checked rather than asserted in prose.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "hwde"
SCRIPTS = SKILL / "scripts"
RECIPES = SKILL / "reference" / "recipes"
FIXTURES = ROOT / "tests" / "fixtures" / "stages" / "pd_trigger"
PY = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
if not PY.exists():   # a checkout with no .venv (the landing gate): the venv running pytest
    PY = Path(sys.executable)

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import state as state_mod  # noqa: E402
import statelib  # noqa: E402
import task_router as tr  # noqa: E402

TASKS = tr.load_tasks()
VERBS = sorted(TASKS["verbs"])


# ---------------------------------------------------------------------------
# 1. the registry is real
# ---------------------------------------------------------------------------
def test_registry_validates_clean():
    problems = tr.validate_registry()
    assert problems == [], "\n".join(problems)


def test_the_plan_s_verb_list_is_the_registry_s():
    """The v2 plan fixed the taxonomy; drift here is a silent scope change."""
    assert VERBS == sorted([
        "review", "fix-finding", "move", "swap-part", "add-part",
        "remove-part", "reroute-net", "make-footprint", "dfm-check", "order",
        "track", "resume-phase", "promote", "learn", "research", "full-run",
        # U17: the outline became editable after P5
        "resize-board"])


def test_skill_md_lists_exactly_the_registry_verbs():
    """SKILL.md names the vocabulary; tasks.yaml owns the recipes. Names may
    not drift apart - a verb the playbook never mentions is unreachable."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    named = {v for v in VERBS if f"`{v}`" in text}
    assert named == set(VERBS), f"SKILL.md is missing {set(VERBS) - named}"


@pytest.mark.parametrize("verb", VERBS)
def test_every_verb_has_a_loadable_recipe_doc(verb: str):
    doc = SKILL / TASKS["verbs"][verb]["doc"]
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert text.isascii(), f"{doc.name}: non-ASCII content"
    assert len(text.splitlines()) <= 120, f"{doc.name} is too long to stay read"
    assert text.startswith(f"# {verb} "), f"{doc.name}: first line names the verb"


def _declared_flags(name: str) -> set[str] | None:
    script = SCRIPTS / f"{name}.py"
    if not script.is_file():
        return None
    return set(re.findall(r"add_argument\(\s*[\"'](--[a-z][a-z0-9-]*)[\"']",
                          script.read_text(encoding="utf-8")))


@pytest.mark.parametrize("verb", VERBS)
def test_recipe_doc_commands_use_real_flags(verb: str):
    """Same contract as the remediation refs: a --flag shown to the reader must
    be one the script declares. Flags attribute to the NEAREST PRECEDING script
    on the line - one sentence can name two of them."""
    bad = []
    for raw in (SKILL / TASKS["verbs"][verb]["doc"]).read_text(
            encoding="utf-8").splitlines():
        declared: set[str] | None = None
        for tok in re.findall(r"[a-z_][a-z0-9_]*\.py|(?<![\w-])--[a-z][a-z0-9-]*",
                              raw):
            if tok.endswith(".py"):
                declared = _declared_flags(tok[:-3])
            elif declared is not None and tok not in declared:
                bad.append(f"{tok} not declared ({raw.strip()[:70]})")
    assert not bad, bad


def test_gates_and_holds_are_never_restated_in_the_recipe():
    """A verb with an edit class must take BOTH from invalidation.yaml. This
    is the T7 interface note - restating them is how the two files drift."""
    for verb, spec in TASKS["verbs"].items():
        if spec.get("edit_class"):
            assert "gates" not in spec and "human_hold" not in spec, verb


def test_mapped_gates_land_in_the_plan_even_when_the_recipe_forgets_them():
    imap = statelib.load_map()
    for verb, spec in TASKS["verbs"].items():
        cls = spec.get("edit_class")
        if not cls:
            continue
        payload = _plan(verb=verb, workspace=None)
        planned = {s["gate"] for s in payload["recipe"]["steps"]
                   if s["kind"] == "gate"}
        assert set(imap["edit_classes"][cls]["gates"]) <= planned, verb
        assert payload["recipe"]["human_hold"] == \
            imap["edit_classes"][cls]["human_hold"], verb


def test_flag_check_rejects_a_flag_that_only_appears_in_the_source(tmp_path):
    """gate.py mentions --pcb while building other tools' commands but does not
    declare it; the first draft of tasks.yaml shipped `gate.py --pcb` because
    the weaker check passed. Pin the stronger one."""
    bad = {"version": 1, "verbs": {"x": {
        "summary": "s", "doc": "reference/recipes/track.md", "workspace": "optional",
        "match": {"any": ["x"]},
        "steps": [{"do": "scripts/gate.py --gate erc --pcb {pcb}"}]}}}
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    problems = tr.validate_registry(tr.load_tasks(p))
    assert any("declares no --pcb" in x for x in problems), problems


def test_subcommand_check_catches_a_typo(tmp_path):
    bad = {"version": 1, "verbs": {"x": {
        "summary": "s", "doc": "reference/recipes/track.md", "workspace": "optional",
        "match": {"any": ["x"]},
        "steps": [{"do": "scripts/state.py resumee --workspace {ws}"}]}}}
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    assert any("subcommand" in x for x in tr.validate_registry(tr.load_tasks(p)))


def test_dynamically_registered_subcommands_are_known():
    """state.py adds show/resume/freshness through a loop variable."""
    problems = tr.validate_registry()
    assert not [p for p in problems if "resume" in p]


# ---------------------------------------------------------------------------
# 2. matching
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,verb", [
    ("review this board", "review"),
    ("import the KiCad project in vendor/thing", "review"),
    ("fix the DRC errors", "fix-finding"),
    ("clear the verify violations", "fix-finding"),
    ("move C12 closer to U1", "move"),
    ("rotate J1 90 degrees", "move"),
    ("swap R5 for a 10k", "swap-part"),
    ("replace U2 with a different part", "swap-part"),
    ("add a 100nF decoupling cap on U1", "add-part"),
    ("remove C2", "remove-part"),
    ("re-route +3V3", "reroute-net"),
    ("widen the VBUS trace", "reroute-net"),
    ("make a footprint for C5184243", "make-footprint"),
    ("check this for manufacturability", "dfm-check"),
    ("order 5 boards", "order"),
    ("where's my order?", "track"),
    ("track my order", "track"),
    ("has it shipped?", "track"),
    ("resume the run", "resume-phase"),
    ("keep going", "resume-phase"),
    ("design a USB-C PD trigger board", "full-run"),
    ("build me a blinky board from scratch", "full-run"),
    ("promote the learnings from this run", "promote"),
    ("work the promotion queue", "promote"),
    ("teach the placement stage", "learn"),
    ("run a learning cycle on P6", "learn"),
    ("research the ethernet coverage gap", "research"),
    ("fill the knowledge gap on interface:usb", "research"),
])
def test_canonical_phrasings_route_to_one_verb(text: str, verb: str):
    got = tr.match_verbs(text, TASKS)
    assert got, f"{text!r} matched nothing"
    top = got[0]
    assert top["verb"] == verb, f"{text!r} -> {[c['verb'] for c in got]}"
    if len(got) > 1:
        assert top["score"] > got[1]["score"], (
            f"{text!r} ties {top['verb']} with {got[1]['verb']}")


def test_unknown_task_asks_instead_of_guessing():
    payload, _ = tr.run(["--task", "make the LEDs prettier"])
    assert payload["status"] == "unknown"
    assert payload["candidates"] == []
    assert "question" in payload


def test_ambiguous_task_returns_candidates_not_a_coin_flip():
    """Two verbs, equal evidence: the LLM layer decides, and comes back with
    --verb. Verified through the real table, not a stub."""
    payload, _ = tr.run(["--task", "fix or move C12"])
    assert payload["status"] == "ambiguous"
    assert len(payload["candidates"]) > 1
    assert "--verb" in payload["question"]


def test_forced_verb_is_the_llm_fallback_path():
    payload, _ = tr.run(["--task", "make the LEDs prettier", "--verb", "review"])
    assert payload["match"] == {"verb": "review", "how": "forced",
                                "score": None, "matched": []}
    assert payload["recipe"]["verb"] == "review"


def test_unknown_forced_verb_is_an_error():
    rc = tr.main(["--task", "x", "--verb", "no-such-verb"])
    assert rc == 2


def test_empty_task_asks():
    payload, _ = tr.run([])
    assert payload["status"] == "unknown"


# ---------------------------------------------------------------------------
# 3. argument extraction
# ---------------------------------------------------------------------------
def test_first_refdes_is_the_subject_of_a_move():
    payload, _ = tr.run(["--task", "move C12 closer to U1"])
    assert payload["args"]["ref"] == "C12"


def test_add_part_never_binds_the_ic_it_decouples():
    """"add a cap on U1" is not a request to touch U1."""
    payload, _ = tr.run(["--task", "add a 100nF cap on U1 pin 12"])
    assert "ref" not in payload["args"]


def test_lcsc_and_capacitor_refdes_are_not_confused():
    payload, _ = tr.run(["--task", "make a footprint for C5184243"])
    assert payload["args"]["lcsc"] == "C5184243"
    payload, _ = tr.run(["--task", "remove C2"])
    assert payload["args"]["ref"] == "C2"


def test_missing_required_arg_asks_the_declared_question():
    payload, _ = tr.run(["--task", "re-route the power net"])
    assert payload["status"] == "needs_args"
    asked = {n["arg"]: n["question"] for n in payload["needs"]}
    assert "net" in asked and "e.g." in asked["net"]


def test_explicit_args_win_over_extraction():
    payload, _ = tr.run(["--task", "move C12", "--arg", "ref=R9"])
    assert payload["args"]["ref"] == "R9"


def test_bad_arg_syntax_is_an_error():
    assert tr.main(["--task", "move C12", "--arg", "refR9"]) == 2


# ---------------------------------------------------------------------------
# 4. golden-path dry-run per verb, on a real workspace
# ---------------------------------------------------------------------------
def _plan(verb: str, workspace: Path | None, extra: list[str] | None = None):
    argv = ["--verb", verb]
    if workspace:
        argv += ["--workspace", str(workspace)]
    payload, _ = tr.run(argv + (extra or []))
    return payload


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Path:
    """A real pd-trigger workspace built from the FROZEN stage fixtures: routed
    board + its sidecars + schematic + netlist, state v2 with all five gates
    recorded (which is what establishes the T7 input hashes)."""
    ws = tmp_path_factory.mktemp("ws") / "pd-trigger"
    (ws / "kicad").mkdir(parents=True)
    for name in ("kicad_pcb", "kicad_pro", "kicad_dru"):
        shutil.copy2(FIXTURES / "route" / f"pd-trigger.{name}",
                     ws / "kicad" / f"pd-trigger.{name}")
    shutil.copy2(FIXTURES / "sch" / "pd-trigger.kicad_sch",
                 ws / "kicad" / "pd-trigger.kicad_sch")
    shutil.copy2(FIXTURES / "pd-trigger.net", ws / "kicad" / "pd-trigger.net")
    for side in ("constraints.json", "decoupling.json"):
        shutil.copy2(FIXTURES / side, ws / "kicad" / side)
    (ws / "kicad" / "parts.json").write_text('{"parts": []}', encoding="utf-8")
    for sub in ("fab", "reports", "log", "work"):
        (ws / sub).mkdir()
    st = state_mod.State.init(ws, "pd-trigger")
    for phase, gate in state_mod.GATE_ORDER:
        st.data["phase"] = phase
        st.record_gate(gate, {"status": "pass", "counts": {"total": 0}}, phase)
    st.data["phase"] = "P9"
    st.save()
    return ws


@pytest.mark.parametrize("verb", VERBS)
def test_every_verb_plans_on_a_real_workspace(verb: str, workspace: Path):
    """The v2 plan's acceptance: a golden-path dry-run per verb. Every step is
    bound to this workspace's real paths, every command names an existing
    script, and nothing that must be answered is silently assumed."""
    extra = {"review": ["--arg", "source=tests/golden/blinky2"],
             "fix-finding": ["--arg", "findings=reports/gate-verify.json",
                             "--arg", "gate=verify"],
             "learn": ["--arg", "stage=P6", "--arg",
                       "fixture=pd_trigger_place"],
             "move": ["--arg", "ref=C12"],
             "swap-part": ["--arg", "ref=R5"],
             "remove-part": ["--arg", "ref=C2"],
             "reroute-net": ["--arg", "net=+3V3"],
             "make-footprint": ["--arg", "lcsc=C14663"],
             "resize-board": ["--arg", "size=fit"]}.get(verb, [])
    payload = _plan(verb, workspace, extra)
    assert payload["status"] == "planned", (verb, payload.get("question"))
    steps = payload["recipe"]["steps"]
    assert steps, verb
    for s in steps:
        assert s["kind"] in ("script", "gate", "agent", "human", "recipe", "note")
        if s["kind"] in ("script", "gate"):
            assert "{" not in s["command"], (verb, s["command"])
            head = s["command"].split()[0]
            assert (SCRIPTS / Path(head).name).is_file(), (verb, head)
        if s["kind"] == "agent":
            assert (SKILL / "agents" / f"{s['role']}.md").is_file()
            assert s["tier"]


@pytest.mark.parametrize("verb", VERBS)
def test_plan_paths_resolve_through_the_invalidation_map(verb: str,
                                                         workspace: Path):
    """Not string-built: the board path comes from statelib.kind_path, so a
    workspace with a registry override plans against ITS layout."""
    payload = _plan(verb, workspace, ["--arg", "ref=C12", "--arg", "net=GND",
                                      "--arg", "lcsc=C14663", "--arg",
                                      "findings=reports/gate-verify.json"])
    pcb = str(workspace / "kicad" / "pd-trigger.kicad_pcb").replace("\\", "/")
    cmds = " ".join(s.get("command", "") for s in payload["recipe"]["steps"])
    if pcb.split("/")[-1] in cmds:
        assert pcb in cmds, verb


def test_workspace_slots_follow_a_registry_override(workspace: Path, tmp_path):
    """kind_path honors an override only when the entry's own kind matches -
    the T7 name-collision rule. The router inherits that for free."""
    ws2 = tmp_path / "odd-layout"
    shutil.copytree(workspace, ws2)
    data = json.loads((ws2 / "state.json").read_text(encoding="utf-8"))
    data["artifacts"]["pcb"] = {"kind": "pcb", "path": "layout/board.kicad_pcb"}
    (ws2 / "state.json").write_text(json.dumps(data), encoding="utf-8")
    payload = _plan("dfm-check", ws2)
    cmds = " ".join(s.get("command", "") for s in payload["recipe"]["steps"])
    assert "layout/board.kicad_pcb" in cmds


def test_move_variant_switches_the_edit_class_and_the_ceremony(workspace: Path):
    fp, _ = tr.run(["--task", "move C12 to the left", "--workspace",
                    str(workspace)])
    silk, _ = tr.run(["--task", "move the refdes label for C12", "--workspace",
                      str(workspace)])
    assert fp["recipe"]["edit_class"] == "move_fp"
    assert fp["recipe"]["human_hold"] == 1
    assert silk["recipe"]["edit_class"] == "silk_edit"
    assert silk["recipe"]["human_hold"] == 0
    assert set(silk["recipe"]["gates"]) < set(fp["recipe"]["gates"])


def test_review_variant_is_external_only_when_a_source_is_given(workspace: Path):
    ext, _ = tr.run(["--task", "review the project at tests/golden/blinky2",
                     "--workspace", str(workspace)])
    assert ext["recipe"]["variant"] == "external"
    assert "intake.py" in ext["recipe"]["steps"][0]["command"]
    here, _ = tr.run(["--task", "review this board", "--workspace",
                      str(workspace)])
    assert here["recipe"]["variant"] == "workspace"
    assert "intake.py" not in " ".join(
        s.get("command", "") for s in here["recipe"]["steps"])


def test_missing_workspace_blocks_a_verb_that_needs_one():
    payload, _ = tr.run(["--task", "re-route +3V3", "--arg", "net=+3V3"])
    assert payload["status"] == "needs_args"
    assert any(n["arg"] == "workspace" for n in payload["needs"])


def test_precondition_failure_blocks_with_the_reason(workspace: Path):
    """Ordering is gated on a FRESH dfm gate, not merely a passed one."""
    payload = _plan("order", workspace)
    assert payload["status"] == "planned"          # freshly recorded = fresh
    board = workspace / "kicad" / "pd-trigger.kicad_pcb"
    board.write_text(board.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    after = _plan("order", workspace)
    assert after["status"] == "blocked"
    assert "dfm" in after["question"]


def test_open_issue_blocks_ordering(workspace: Path, tmp_path):
    ws2 = tmp_path / "issued"
    shutil.copytree(workspace, ws2)
    st = state_mod.State.load(ws2 / "state.json")
    st.open_issue({"gate": "verify", "fixer": "router",
                   "kinds": ["undersized_track"]})
    st.save()
    payload = _plan("order", ws2)
    assert payload["status"] == "blocked"
    assert "open issues" in payload["question"]


def test_fix_finding_infers_the_single_failing_gate(workspace: Path, tmp_path):
    ws2 = tmp_path / "failing"
    shutil.copytree(workspace, ws2)
    st = state_mod.State.load(ws2 / "state.json")
    st.record_gate("verify", {"status": "fail", "counts": {"total": 3}})
    st.save()
    report = ws2 / "reports" / "gate-verify.json"
    report.write_text(json.dumps({
        "gate": "verify", "status": "fail",
        "failing": [{"check": "check_current", "kind": "undersized_track",
                     "severity": "error", "pos": [10, 10], "layer": "F.Cu",
                     "net": "+3V3", "refs": [], "msg": "x", "source": "verify"}],
    }), encoding="utf-8")
    payload = _plan("fix-finding", ws2)
    assert payload["status"] == "planned"
    assert payload["args"]["gate"] == "verify"
    assert payload["args"]["findings"].endswith("gate-verify.json")
    assert any("gate-verify.json" in s.get("command", "")
               for s in payload["recipe"]["steps"])


def test_fix_finding_attaches_the_trigger_indexed_remediation(workspace: Path,
                                                              tmp_path):
    """T4's refs reach the plan the same way they reach a work order."""
    report = tmp_path / "checks.json"
    report.write_text(json.dumps({"violations": [
        {"check": "check_current", "kind": "undersized_track", "severity":
         "error", "pos": [1, 1], "layer": "F.Cu", "net": "+3V3", "refs": [],
         "msg": "x", "source": "verify"}]}), encoding="utf-8")
    payload = _plan("fix-finding", workspace,
                    ["--arg", f"findings={report}", "--arg", "gate=verify"])
    assert any(p.endswith("undersized_track.md")
               for p in payload["recipe"]["remediations"])


def test_two_failing_gates_are_not_guessed(workspace: Path, tmp_path):
    ws2 = tmp_path / "two-failing"
    shutil.copytree(workspace, ws2)
    st = state_mod.State.load(ws2 / "state.json")
    st.record_gate("verify", {"status": "fail", "counts": {"total": 1}})
    st.record_gate("dfm", {"status": "fail", "counts": {"total": 1}})
    st.save()
    payload = _plan("fix-finding", ws2)
    assert payload["status"] == "needs_args"


# ---------------------------------------------------------------------------
# 5. full-run is a recipe, not a second pipeline
# ---------------------------------------------------------------------------
def test_full_run_gate_sequence_is_the_pipeline_gate_order():
    payload = _plan("full-run", None)
    planned = [s["gate"] for s in payload["recipe"]["steps"]
               if s["kind"] == "gate"]
    assert planned == [g for _, g in state_mod.GATE_ORDER]


def test_full_run_delegates_ordering_to_the_order_recipe():
    payload = _plan("full-run", None)
    assert any(s["kind"] == "recipe" and s["verb"] == "order"
               for s in payload["recipe"]["steps"])


def test_full_run_starts_with_the_env_check_and_state_init():
    steps = _plan("full-run", None)["recipe"]["steps"]
    assert "check_env.py" in steps[0]["command"]
    assert "state.py init" in steps[1]["command"]


def test_recipe_steps_reference_only_existing_recipes():
    for verb in VERBS:
        for s in _plan(verb, None)["recipe"]["steps"]:
            if s["kind"] == "recipe":
                assert s["verb"] in TASKS["verbs"]


# ---------------------------------------------------------------------------
# 6. CLI contract
# ---------------------------------------------------------------------------
def test_list_and_validate_exit_zero(tmp_path):
    for flag in ("--list", "--validate"):
        out = tmp_path / f"{flag.strip('-')}.json"
        assert tr.main([flag, "--out", str(out)]) == 0
        assert json.loads(out.read_text(encoding="utf-8"))["status"] == "planned"


def test_exit_codes_follow_the_spec_contract(workspace: Path):
    assert tr.main(["--verb", "track", "--workspace", str(workspace)]) == 0
    assert tr.main(["--task", "re-route something"]) == 1        # needs args
    assert tr.main(["--task", "make the LEDs prettier"]) == 1     # unknown
    assert tr.main(["--verb", "move", "--workspace", "/no/such/ws",
                    "--arg", "ref=C1"]) == 1                      # no workspace


def test_payload_is_ascii_and_json(workspace: Path, tmp_path):
    out = tmp_path / "plan.json"
    tr.main(["--task", "move C12", "--workspace", str(workspace),
             "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert text.isascii()
    json.loads(text)


def test_corrupt_state_is_an_error_not_a_wrong_plan(tmp_path):
    ws = tmp_path / "broken"
    ws.mkdir()
    (ws / "state.json").write_text("{not json", encoding="utf-8")
    assert tr.main(["--verb", "track", "--workspace", str(ws)]) == 2


@pytest.mark.smoke
def test_router_runs_as_a_subprocess_under_the_repo_venv(workspace: Path):
    """The orchestrator invokes it as a command, not as an import."""
    proc = subprocess.run(
        [str(PY), str(SCRIPTS / "task_router.py"), "--task",
         "move C12 off the connector", "--workspace", str(workspace)],
        capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["recipe"]["verb"] == "move"
    assert payload["recipe"]["doc"] == "reference/recipes/move.md"


def test_a_verb_whose_variants_all_miss_asks_instead_of_planning_nothing():
    """`review` with neither a source to import nor a workspace to re-review:
    an empty plan is the one answer that must never be silent."""
    payload, _ = tr.run(["--verb", "review"])
    assert payload["recipe"]["steps"] == []
    assert payload["status"] == "needs_args"
    asked = {n["arg"] for n in payload["needs"]}
    assert {"source", "workspace"} <= asked


def test_prose_that_quotes_a_command_is_flag_checked_too(tmp_path):
    """A note telling the operator to run `kc.py drc --refill-zones` is as
    wrong as a `do` that says it (kc.py's flag is --refill; --refill-zones is
    kicad-cli's own). Both live notes carrying it were caught this way."""
    bad = {"version": 1, "verbs": {"x": {
        "summary": "s", "doc": "reference/recipes/track.md", "workspace": "optional",
        "match": {"any": ["x"]},
        "steps": [{"note": "refill first (kc.py drc --refill-zones --save-board)"}]}}}
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    assert any("declares no --refill-zones" in x
               for x in tr.validate_registry(tr.load_tasks(p)))


def test_prose_may_state_a_true_negative_about_a_flag(tmp_path):
    """"route_auto has no --nets" must NOT be flagged - the scan stops at the
    first ordinary word, so only quoted commands are checked."""
    ok = {"version": 1, "verbs": {"x": {
        "summary": "s", "doc": "reference/recipes/track.md", "workspace": "optional",
        "match": {"any": ["x"]},
        "steps": [{"note": "route_auto.py has no --nets: it is whole-board"}]}}}
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump(ok), encoding="utf-8")
    assert not [x for x in tr.validate_registry(tr.load_tasks(p))
                if "--nets" in x]


def test_gate_steps_declare_their_input_and_flag_a_missing_one(workspace: Path):
    """The map lists `sim` for every part-level edit class, but a board with no
    testbenches has no kicad/sims - and gate.py RAISES on a missing input
    instead of reporting a skip. The plan must say so, not let the operator
    discover it through a traceback."""
    payload = _plan("add-part", workspace, ["--arg", "lcsc=C14663"])
    gates = {s["gate"]: s for s in payload["recipe"]["steps"]
             if s["kind"] == "gate"}
    assert gates["dfm"]["input_exists"] is True
    assert gates["sim"]["input_exists"] is False
    assert "skip it" in gates["sim"]["note"]
    assert gates["erc"]["input"].endswith(".kicad_sch")
    assert gates["sim"]["input"].endswith("sims")
