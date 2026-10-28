"""task_router - match a task to a verb and plan its recipe (T10).

The front door of `/hwde <task>`. The skill is an EE picking up a project in
any state, so the interesting question is not "which phase are we in" but
"what am I being asked to do". This script answers it DETERMINISTICALLY where
it can:

  1. reference/tasks.yaml is matched first (regex table, ~13 verbs). A single
     clear winner -> a bound, executable plan (exit 0).
  2. A tie, or nothing at all -> exit 1 with `candidates` + a question. THAT is
     where the LLM classifies, and it lands back here as `--verb <name>`, so
     the recipe machinery is the same on both paths.
  3. A verb whose required arguments are missing -> exit 1 with `needs`, each
     carrying the question to ask. One batch, per SKILL.md rule 6.

The plan is data, never prose: every step is a concrete command (bound to the
workspace's real paths through statelib.kind_path), a gate name, an agent role,
a human hold, or a nested recipe. Gates and ceremony come from
reference/invalidation.yaml via the verb's edit_class - this script never
restates them, so the map stays the single source of truth (T7).

The full pipeline is the `full-run` verb: same table, same step vocabulary.

  task_router.py --task "swap R5 for a 10k" --workspace ~/dev/boards/pd-trigger
  task_router.py --verb move --workspace ~/dev/boards/x --arg ref=C12
  task_router.py --list            # the verb table (summaries + args)
  task_router.py --validate        # registry self-check (scripts/flags/gates)

exit 0 planned / 1 needs a decision (ambiguous, unknown, missing args,
blocked precondition) / 2 error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT = "task_router.py"
SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import yaml  # noqa: E402

from checklib import CheckError, utf8_stdout  # noqa: E402
import boardreg  # noqa: E402
import env  # noqa: E402
import statelib  # noqa: E402

TASKS = SKILL / "reference" / "tasks.yaml"
GATES = SKILL / "reference" / "gates.yaml"

STEP_KINDS = ("do", "gate", "agent", "human", "recipe", "note")
PRECONDITIONS = ("workspace", "board_exists", "sch_exists", "netlist_exists",
                 "no_open_issues")  # plus "gates_fresh:<gate>"

# Workspace slots. Every kicad/ artifact resolves through the invalidation map
# (statelib.kind_path), so a workspace with a registry override plans against
# ITS paths, not the default layout.
KIND_SLOTS = {"pcb": "pcb", "sch": "sch", "netlist": "netlist", "pro": "pro",
              "dru": "dru", "constraints": "constraints",
              "decoupling": "decoupling", "parts": "parts", "sims": "sims"}
DIR_SLOTS = {"fab": "fab", "reports": "reports", "log": "log", "work": "work"}

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def load_tasks(path: Path | str | None = None) -> dict:
    p = Path(path) if path else TASKS
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("verbs"), dict):
        raise CheckError(f"{p}: no verbs table")
    return data


def load_gate_order(path: Path | str | None = None) -> list[str]:
    """Gate names in PIPELINE order (by the phase each guards, declaration
    order inside a phase) - gates.yaml is written in the order the gates were
    built, which puts the P6 interim drc before erc's successors."""
    p = Path(path) if path else GATES
    gates = yaml.safe_load(p.read_text(encoding="utf-8"))["gates"]
    order = list(gates)
    return sorted(order, key=lambda g: (str(gates[g].get("phase", "P9")),
                                        order.index(g)))


def validate_registry(tasks: dict | None = None,
                      imap: dict | None = None) -> list[str]:
    """Structural problems in tasks.yaml, as human-readable strings. Empty =
    the table is internally consistent and every artifact it names exists.
    Run by --validate and by the test suite."""
    tasks = tasks or load_tasks()
    imap = imap or statelib.load_map()
    gates = load_gate_order()
    problems: list[str] = []

    for verb, spec in tasks["verbs"].items():
        where = f"verbs.{verb}"
        for field in ("summary", "doc", "workspace", "match"):
            if field not in spec:
                problems.append(f"{where}: missing {field!r}")
        if spec.get("workspace") not in ("required", "optional", "created", None):
            problems.append(f"{where}: bad workspace {spec.get('workspace')!r}")
        doc = spec.get("doc")          # skill-relative, e.g. reference/recipes/x.md
        if doc and not (SKILL / doc).is_file():
            problems.append(f"{where}: doc {doc} not found")

        cls = spec.get("edit_class")
        if cls is not None:
            if cls not in imap["edit_classes"]:
                problems.append(f"{where}: unknown edit_class {cls!r}")
            if "gates" in spec:
                problems.append(
                    f"{where}: has edit_class AND gates - gates come from "
                    "invalidation.yaml (restating them is how they drift)")
            if "human_hold" in spec:
                problems.append(
                    f"{where}: has edit_class AND human_hold - the weight comes "
                    "from invalidation.yaml")
        else:
            for g in spec.get("gates") or []:
                if g not in gates:
                    problems.append(f"{where}: unknown gate {g!r}")

        for name, arg in (spec.get("args") or {}).items():
            if not isinstance(arg, dict) or "kind" not in arg:
                problems.append(f"{where}.args.{name}: needs a kind")
            elif arg["kind"] not in ("refdes", "net", "path", "lcsc", "int", "text"):
                problems.append(f"{where}.args.{name}: bad kind {arg['kind']!r}")
            if arg.get("required") and not arg.get("question"):
                problems.append(f"{where}.args.{name}: required args need a question")
            if arg.get("extract", "only") not in ("only", "first", "none"):
                problems.append(f"{where}.args.{name}: bad extract policy "
                                f"{arg['extract']!r}")

        for pc in spec.get("preconditions") or []:
            base = pc.split(":", 1)[0]
            if base == "gates_fresh":
                if pc.split(":", 1)[1] not in gates:
                    problems.append(f"{where}: gates_fresh names unknown gate {pc!r}")
            elif pc not in PRECONDITIONS:
                problems.append(f"{where}: unknown precondition {pc!r}")

        for pat in (spec["match"].get("any", []) + spec["match"].get("not", [])
                    + spec["match"].get("all", [])):
            try:
                re.compile(pat)
            except re.error as exc:
                problems.append(f"{where}: bad regex {pat!r} ({exc})")
        if not spec["match"].get("any"):
            problems.append(f"{where}: match.any is empty - unreachable verb")

        bodies = [("steps", spec.get("steps"))]
        for vname, var in (spec.get("variants") or {}).items():
            if "when" not in var:
                problems.append(f"{where}.variants.{vname}: missing when")
            elif not _valid_condition(var["when"]):
                problems.append(
                    f"{where}.variants.{vname}: bad when {var['when']!r}")
            if var.get("edit_class") and var["edit_class"] not in imap["edit_classes"]:
                problems.append(f"{where}.variants.{vname}: unknown edit_class")
            if var.get("edit_class") and ("gates" in var or "human_hold" in var):
                problems.append(f"{where}.variants.{vname}: edit_class AND "
                                "gates/human_hold - the map owns both")
            for g in var.get("gates") or []:
                if g not in gates:
                    problems.append(f"{where}.variants.{vname}: unknown gate {g!r}")
            bodies.append((f"variants.{vname}.steps", var.get("steps")))
        if not any(b for _, b in bodies):
            problems.append(f"{where}: no steps and no variants")

        for label, steps in bodies:
            if steps is None:
                continue
            if not isinstance(steps, list) or not steps:
                problems.append(f"{where}.{label}: empty")
                continue
            for i, step in enumerate(steps):
                problems += _validate_step(f"{where}.{label}[{i}]", step,
                                           tasks, gates)
    return problems


def _valid_condition(cond: str) -> bool:
    if cond in ("always", "has_workspace"):
        return True
    head, _, rest = cond.partition(":")
    if head == "has_arg" and rest:
        return True
    if head == "matches" and rest:
        try:
            re.compile(rest)
        except re.error:
            return False
        return True
    return False


_SCRIPT_INFO: dict[str, tuple[set[str], set[str]] | None] = {}


def _script_info(name: str):
    """(declared flags, subcommand names) for scripts/<name>.py, or None.

    Declared = it appears in an `add_argument("--flag")` call. A flag string
    that merely APPEARS in the source is not a flag the script accepts: gate.py
    contains "--pcb" while building kc.py commands, route_auto contains
    "--nets" as the argument it hands to KRT. T10 shipped four such invocations
    before this rule existed. Subcommands come from add_parser literals, plus
    the string literals just above a dynamic `add_parser(var)` (state.py
    registers show/resume/freshness through a loop)."""
    if name in _SCRIPT_INFO:
        return _SCRIPT_INFO[name]
    script = SCRIPTS / f"{name}.py"
    info = None
    if script.is_file():
        src = script.read_text(encoding="utf-8")
        flags = set(re.findall(
            r"add_argument\(\s*[\"'](--[a-z][a-z0-9-]*)[\"']", src))
        subs = set(re.findall(r"add_parser\(\s*[\"']([a-z][a-z0-9_-]*)[\"']", src))
        for dyn in re.finditer(r"add_parser\(\s*[a-z_]+\s*\)", src):
            head = "\n".join(src[:dyn.start()].split("\n")[-3:])
            subs |= set(re.findall(r"[\"']([a-z][a-z0-9_-]*)[\"']", head))
        info = (flags, subs)
    _SCRIPT_INFO[name] = info
    return info


def _check_command_text(where: str, text: str) -> list[str]:
    """Validate flags inside PROSE, but only where the prose is quoting a
    command: after a <script>.py, keep consuming subcommands, paths and
    placeholders, and stop at the first ordinary word. That way
    `kc.py drc --refill-zones` is checked (and caught - kc.py's flag is
    --refill; --refill-zones is kicad-cli's) while a true negative statement
    like "route_auto has no --nets" is left alone."""
    problems: list[str] = []
    tokens = text.split()
    for i, tok in enumerate(tokens):
        raw = tok.strip("`\"'(),.;:")
        if not raw.endswith(".py"):
            continue
        info = _script_info(raw.split("/")[-1][:-3])
        if info is None:
            continue
        flags, subs = info
        for nxt in tokens[i + 1:]:
            t = nxt.strip("`\"'(),.;:")
            if t.startswith("--"):
                if t not in flags:
                    problems.append(f"{where}: {raw} declares no {t}")
                continue
            if t and (t in subs or t[0] in "{<-" or "/" in t):
                continue    # subcommand, placeholder, path or short flag
            break        # an ordinary word: the quoted command ended
    return problems


def _validate_step(where: str, step: dict, tasks: dict,
                   gates: list[str]) -> list[str]:
    problems: list[str] = []
    if not isinstance(step, dict):
        return [f"{where}: not a mapping"]
    kinds = [k for k in STEP_KINDS if k in step]
    if len(kinds) != 1:
        return [f"{where}: needs exactly one of {STEP_KINDS}, has {kinds}"]
    kind = kinds[0]
    if kind == "do":
        cmd = step["do"]
        m = re.match(r"scripts/([a-z_0-9]+\.py)\b", cmd)
        if not m:
            problems.append(f"{where}: `do` must start with scripts/<name>.py")
        else:
            info = _script_info(m.group(1)[:-3])
            if info is None:
                problems.append(f"{where}: no such script {m.group(1)}")
            else:
                flags, subs = info
                for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", cmd):
                    if flag not in flags:
                        problems.append(f"{where}: {m.group(1)} declares no {flag}")
                tokens = cmd.split()[1:]
                if subs and tokens and not tokens[0].startswith("-"):
                    if tokens[0] not in subs:
                        problems.append(f"{where}: {m.group(1)} has no "
                                        f"subcommand {tokens[0]!r}")
    elif kind == "gate":
        g = step["gate"]
        if "{" not in g and g not in gates:
            problems.append(f"{where}: unknown gate {g!r}")
    elif kind == "agent":
        if not (SKILL / "agents" / f"{step['agent']}.md").is_file():
            problems.append(f"{where}: no agent prompt {step['agent']}.md")
        if not step.get("tier"):
            problems.append(f"{where}: agent step needs a tier")
    elif kind == "human":
        if step["human"] not in (0, 1, 2, 3):
            problems.append(f"{where}: human hold must be 0-3")
    elif kind == "recipe":
        if step["recipe"] not in tasks["verbs"]:
            problems.append(f"{where}: unknown recipe {step['recipe']!r}")
    # prose carries commands too - a note telling the operator to run
    # `kc.py drc --refill-zones` is as wrong as a `do` that says it
    for field in ("note", "why", "when"):
        if isinstance(step.get(field), str):
            problems += _check_command_text(f"{where}.{field}", step[field])
    return problems


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------
def match_verbs(text: str, tasks: dict) -> list[dict]:
    """Every verb the task text plausibly names, best first. Score = number of
    distinct `any` patterns that hit, plus the verb's weight."""
    out = []
    for verb, spec in tasks["verbs"].items():
        m = spec["match"]
        if any(re.search(p, text, re.I) for p in m.get("not", [])):
            continue
        if not all(re.search(p, text, re.I) for p in m.get("all", [])):
            continue
        hits = [p for p in m.get("any", []) if re.search(p, text, re.I)]
        if not hits:
            continue
        out.append({"verb": verb, "score": len(hits) + int(m.get("weight", 0)),
                    "matched": hits, "summary": spec["summary"]})
    return sorted(out, key=lambda c: (-c["score"], c["verb"]))


# ---------------------------------------------------------------------------
# argument extraction
# ---------------------------------------------------------------------------
# Refdes prefixes KiCad/this pipeline actually emits. "C12" is a capacitor
# refdes AND the shape of an LCSC id - the disambiguator is length + context
# (LCSC ids are C + >=4 digits, and usually announced), so extraction never
# guesses one from the other.
REFDES_RE = re.compile(
    r"\b(C|R|L|D|U|Q|J|K|SW|Y|X|FB|TP|F|MH|JP|LED|RN|CN)(\d{1,3})\b")
LCSC_RE = re.compile(r"\b[Cc](\d{4,})\b")
NET_RE = re.compile(r"(?:^|\s)(/[A-Za-z0-9_./+-]+|[+-]\d+V\d*|\bGND\b|\bVBUS\b"
                    r"|\bVCC\b|\bVDD\b)")
QUOTED_RE = re.compile(r"[\"']([^\"']+)[\"']")


def extract_args(text: str, spec: dict, cwd: Path) -> dict:
    """Deterministic slot filling from the task text. Anything uncertain is
    left unfilled - a question beats a wrong refdes.

    Per-arg `extract` picks the policy: `only` (default) fills just when the
    text names exactly one candidate; `first` takes the leading one (the
    grammatical subject: "move C12 closer to U1" is about C12); `none` never
    extracts (add-part's `ref` NAMES THE NEW PART - the refdes in the text is
    the IC it decouples, and binding that would be worse than asking)."""
    got: dict[str, str] = {}
    for name, arg in (spec.get("args") or {}).items():
        kind = arg["kind"]
        policy = arg.get("extract", "only")
        val = None
        if policy == "none":
            continue
        if kind == "refdes":
            hits = [f"{m.group(1)}{m.group(2)}" for m in REFDES_RE.finditer(text)
                    if not (m.group(1).upper() == "C" and len(m.group(2)) >= 4)]
            if hits and (policy == "first" or len(set(hits)) == 1):
                val = hits[0]
        elif kind == "lcsc":
            hits = {f"C{m.group(1)}" for m in LCSC_RE.finditer(text)}
            if len(hits) == 1:
                val = hits.pop()
        elif kind == "net":
            hits = {m.group(1) for m in NET_RE.finditer(text)}
            if len(hits) == 1:
                val = hits.pop()
        elif kind == "int":
            hits = {m for m in re.findall(r"\b(\d{1,5})\b", text)}
            if len(hits) == 1:
                val = hits.pop()
        elif kind == "path":
            cands = [t.strip(",;()") for t in QUOTED_RE.findall(text)]
            cands += [t.strip(",;()") for t in text.split()
                      if ("/" in t or "\\" in t or t.endswith(
                          (".json", ".kicad_pro", ".kicad_pcb", ".kicad_sch")))]
            existing = [c for c in cands if (cwd / c).exists()]
            pick = existing or cands
            if len(set(pick)) == 1:
                val = pick[0]
        if val is not None:
            got[name] = val
    return got


# ---------------------------------------------------------------------------
# workspace context
# ---------------------------------------------------------------------------
def workspace_context(ws: Path | None, board_hint: str | None,
                      imap: dict) -> dict:
    """Slot bindings + live state for a workspace (which may not exist yet)."""
    ctx: dict = {"workspace": None, "board": board_hint, "state": None,
                 "exists": False, "slots": {}}
    if ws is None:
        return ctx
    ctx["workspace"] = str(ws).replace("\\", "/")
    state_path = ws / "state.json"
    registry = None
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckError(f"unreadable state.json: {exc}") from exc
        ctx["exists"] = True
        ctx["board"] = data.get("board") or board_hint
        registry = data.get("artifacts") or {}
        ctx["state"] = data
    if not ctx["board"]:
        ctx["board"] = boardreg.split_dir(ws.name)[1]
    slots = {"ws": ctx["workspace"], "board": ctx["board"],
             "project": statelib.project_stem(ws, ctx["board"]),
             "state": f"{ctx['workspace']}/state.json"}
    for slot, kind in KIND_SLOTS.items():
        rel = statelib.kind_path(kind, ctx["board"], imap, registry, ws)
        slots[slot] = f"{ctx['workspace']}/{rel}"
    for slot, sub in DIR_SLOTS.items():
        slots[slot] = f"{ctx['workspace']}/{sub}"
    # verify check reports: reports/checks, or kicad/reports/checks on a
    # workspace not yet resumed to workspace_schema 2 (statelib)
    slots["checks"] = str(statelib.check_reports_dir(ws)).replace("\\", "/")
    ctx["slots"] = slots
    return ctx


def infer_failing_gate(ctx: dict) -> tuple[str | None, str | None]:
    """(gate, report path) when the workspace has EXACTLY ONE failing gate and
    its report is on disk. Deterministic convenience only: two failing gates,
    or a missing report, means the operator says which - the router never picks
    a fix target by guessing."""
    data = ctx.get("state")
    if not data:
        return None, None
    failing = [g for g, e in (data.get("gates") or {}).items()
               if e.get("status") == "fail"]
    if len(failing) != 1:
        return None, None
    gate = failing[0]
    rep = Path(ctx["slots"]["reports"]) / f"gate-{gate}.json"
    return gate, (str(rep).replace("\\", "/") if rep.is_file() else None)


def resume_view(ctx: dict) -> dict | None:
    """The freshness half of state.py resume, read-only and import-free."""
    data = ctx.get("state")
    if not data:
        return None
    ws = Path(ctx["workspace"])
    fresh = statelib.freshness_report(data, ws, statelib.load_map())
    gates = data.get("gates", {})
    return {
        "phase": data.get("phase"),
        "gates_passed": sorted(g for g, e in gates.items()
                               if e.get("status") == "pass"),
        "gates_stale": fresh["summary"]["stale"],
        "gates_freshness_unknown": fresh["summary"]["unknown"],
        "human_hold_pending": fresh["summary"]["human_hold_pending"],
        "open_issues": [i["id"] for i in data.get("open_issues", [])
                        if i.get("status") in ("open", "fixing")],
    }


def check_preconditions(spec: dict, ctx: dict, view: dict | None) -> list[dict]:
    out = []
    for pc in spec.get("preconditions") or []:
        base, _, arg = pc.partition(":")
        ok, detail = True, "ok"
        if not ctx["exists"]:
            ok, detail = False, "no workspace (state.json not found)"
        elif base == "workspace":
            pass
        elif base in ("board_exists", "sch_exists", "netlist_exists"):
            slot = {"board_exists": "pcb", "sch_exists": "sch",
                    "netlist_exists": "netlist"}[base]
            p = Path(ctx["slots"][slot])
            ok = p.is_file()
            detail = f"{ctx['slots'][slot]} {'found' if ok else 'MISSING'}"
        elif base == "gates_fresh":
            view = view or {}
            if arg in (view.get("gates_stale") or []):
                ok, detail = False, f"gate {arg} is marked stale - re-run it"
            elif arg in (view.get("gates_freshness_unknown") or []):
                ok, detail = False, (f"gate {arg} freshness is unknown - re-run "
                                     "it to establish input hashes")
            elif arg not in (view.get("gates_passed") or []):
                ok, detail = False, f"gate {arg} has not passed"
            else:
                detail = f"gate {arg} passed and hash-fresh"
        elif base == "no_open_issues":
            issues = (view or {}).get("open_issues") or []
            ok = not issues
            detail = "no open issues" if ok else f"open issues {issues}"
        out.append({"name": pc, "ok": ok, "detail": detail})
    return out


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------
def _choose_variant(spec: dict, text: str, args: dict, ctx: dict):
    for name, var in (spec.get("variants") or {}).items():
        cond = var["when"]
        if cond == "always":
            return name, var
        if cond == "has_workspace" and ctx.get("exists"):
            return name, var
        head, _, rest = cond.partition(":")
        if head == "has_arg" and args.get(rest):
            return name, var
        if head == "matches" and re.search(rest, text, re.I):
            return name, var
    return None, None


def _bind(template: str, slots: dict, args: dict, required: set,
          needs: set) -> tuple[str, list[str]]:
    """Fill {slots}; report what stayed free. A missing REQUIRED arg blocks the
    plan (it lands in `needs`); anything else renders as <name> - a value the
    operator supplies at run time (op-list paths, the fix's edit class)."""
    free: list[str] = []

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in args and args[key] not in (None, ""):
            return str(args[key])
        if key in slots and slots[key]:
            return slots[key]
        if key in required:
            needs.add(key)
        else:
            free.append(key)
        return f"<{key}>"

    return _PLACEHOLDER.sub(sub, template), free


def build_plan(verb: str, spec: dict, tasks: dict, text: str, args: dict,
               ctx: dict, imap: dict, gate_order: list[str],
               needs: set) -> dict:
    required = {n for n, a in (spec.get("args") or {}).items()
                if a.get("required")}
    slots = dict(ctx["slots"])
    vname, var = _choose_variant(spec, text, args, ctx)
    body = (var or spec).get("steps") or spec.get("steps") or []

    edit_class = (var or {}).get("edit_class", spec.get("edit_class"))
    if edit_class:
        ec = imap["edit_classes"][edit_class]
        gates = [g for g in gate_order if g in ec["gates"]]
        stale = list(ec["stale_artifacts"])
        hold = ec["human_hold"]
        source = f"invalidation.yaml edit_classes.{edit_class}"
    else:
        gates = [g for g in gate_order
                 if g in ((var or {}).get("gates", spec.get("gates")) or [])]
        stale = []
        hold = (var or {}).get("human_hold", spec.get("human_hold", 0))
        source = "tasks.yaml (no edit class - nothing on the board changes)"
    slots.setdefault("gate", None)
    slots["edit_class"] = edit_class

    steps: list[dict] = []
    scheduled: set[str] = set()
    for raw in body:
        step: dict = {"n": len(steps) + 1}
        for k in ("why", "when", "optional"):
            if k in raw:
                step[k] = raw[k]
        if "do" in raw:
            cmd, free = _bind(raw["do"], slots, args, required, needs)
            step.update(kind="script", command=cmd,
                        script=cmd.split()[0].split("/")[-1])
            if free:
                step["free_slots"] = sorted(set(free))
        elif "gate" in raw:
            name, free = _bind(raw["gate"], slots, args, required, needs)
            step.update(kind="gate", **_gate_step(name, slots))
            scheduled.add(name)
            if free:
                step["free_slots"] = sorted(set(free))
        elif "agent" in raw:
            step.update(kind="agent", role=raw["agent"], tier=raw.get("tier"),
                        prompt=f"agents/{raw['agent']}.md")
        elif "human" in raw:
            step.update(kind="human", hold=raw["human"])
        elif "recipe" in raw:
            step.update(kind="recipe", verb=raw["recipe"],
                        summary=tasks["verbs"][raw["recipe"]]["summary"])
        else:
            # notes name real paths ("read {reports}/intake-digest.md") - bind
            # them too, but never let a note create a blocking `need`.
            step.update(kind="note",
                        note=_bind(raw.get("note", ""), slots, args, set(),
                                   set())[0])
        if "note" in raw and step.get("kind") != "note":
            step["note"] = _bind(raw["note"], slots, args, set(), set())[0]
        steps.append(step)

    # Gates the map requires but the recipe did not place itself: they run at
    # the end, in gates.yaml order. This is why a recipe never lists them.
    for g in gates:
        if g in scheduled:
            continue
        steps.append({"n": len(steps) + 1, "kind": "gate",
                      "why": f"required by {source}", **_gate_step(g, slots)})

    return {
        "verb": verb, "summary": spec["summary"], "doc": spec["doc"],
        "variant": vname, "edit_class": edit_class,
        "human_hold": hold, "human_hold_source": source,
        "gates": gates, "gates_source": source,
        "stale_artifacts": stale, "steps": steps,
    }


def _gate_step(gate: str, slots: dict) -> dict:
    """A gate step: its command plus whether its INPUT is actually there.

    gate.py's input depends on the gate's tool (gates.yaml): erc reads the
    schematic, sim reads the sims DIRECTORY, everything else the board. The
    invalidation map lists `sim` for every part-level edit class, but a board
    with no testbenches has no kicad/sims - and gate.py raises on a missing
    input rather than reporting a skip. So the plan says so up front instead of
    letting the operator find out through a traceback."""
    if "<" in gate:
        return {"gate": gate,
                "command": (f"scripts/gate.py --gate {gate} <input> "
                            f"--workspace {slots.get('ws', '<ws>')}")}
    try:
        spec = yaml.safe_load(GATES.read_text(encoding="utf-8"))["gates"][gate]
    except (OSError, KeyError):
        spec = {}
    tool = spec.get("tool", "drc")
    inp = {"erc": slots.get("sch"), "sim": slots.get("sims")}.get(
        tool, slots.get("pcb")) or "<board>"
    # --workspace is what RECORDS the result in state.json (U16): a gate step
    # that does not record leaves the run with reports on disk and no evidence
    # in state, which is exactly how bb-buck reached P9 with `gates: {}`.
    step = {"gate": gate, "input": inp,
            "command": (f"scripts/gate.py --gate {gate} {inp} "
                        f"--workspace {slots.get('ws', '<ws>')} "
                        f"--out {slots.get('reports', '<reports>')}/"
                        f"gate-{gate}.json")}
    if "<" not in inp:
        step["input_exists"] = Path(inp).exists()
        if not step["input_exists"]:
            step["note"] = (f"input {inp} does not exist - this gate has "
                            "nothing to judge; skip it (gate.py errors on a "
                            "missing input, it does not report a skip)")
    return step


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _remediations(findings: str | None) -> list[str]:
    """Trigger-indexed refs for the kinds a findings file actually carries
    (T4). Same lookup fix_dispatch uses, so the plan and the work orders point
    at the same knowledge."""
    if not findings or not Path(findings).is_file():
        return []
    try:
        import cluster_violations
        import fix_dispatch
        data = json.loads(Path(findings).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  - a bad findings file is not fatal here
        return []
    viols = data.get("failing") or data.get("violations") or []
    if isinstance(data.get("clusters"), list):
        viols = [v for c in data["clusters"] for v in c.get("violations", [])]
    kinds = {cluster_violations.kind_of(v) for v in viols if isinstance(v, dict)}
    return fix_dispatch.remediation_paths(sorted(k for k in kinds if k))


def run(argv: list[str] | None = None) -> tuple[dict, str | None]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--task", help="the request, in the user's own words")
    ap.add_argument("--verb", help="force a verb (the LLM-classification path)")
    ap.add_argument("--workspace", help="<boards root>/<name>, the boards root being "
                         "HWDE_BOARDS_ROOT or ~/dev/boards (may not exist yet); a "
                         "board name or part number resolves through register.yaml")
    ap.add_argument("--arg", action="append", default=[], metavar="K=V",
                    help="fill a recipe argument explicitly (repeatable)")
    ap.add_argument("--findings", help="gate result / check report for "
                                       "fix-finding (also fills its arg)")
    ap.add_argument("--list", action="store_true", help="the verb table")
    ap.add_argument("--validate", action="store_true",
                    help="registry self-check (scripts, flags, gates, classes)")
    ap.add_argument("--tasks", help="alternate tasks.yaml (tests)")
    ap.add_argument("--out", help="write the plan JSON here instead of stdout")
    args = ap.parse_args(argv)

    tasks = load_tasks(args.tasks)
    imap = statelib.load_map()
    gate_order = load_gate_order()

    if args.validate:
        problems = validate_registry(tasks, imap)
        return ({"script": SCRIPT, "status": "planned" if not problems
                 else "error", "verbs": sorted(tasks["verbs"]),
                 "problems": problems}, args.out)

    if args.list:
        return ({"script": SCRIPT, "status": "planned",
                 "verbs": [{"verb": v, "summary": s["summary"],
                            "workspace": s["workspace"], "doc": s["doc"],
                            "edit_class": s.get("edit_class"),
                            "args": sorted((s.get("args") or {}))}
                           for v, s in tasks["verbs"].items()]}, args.out)

    text = (args.task or "").strip()
    if not text and not args.verb:
        return ({"script": SCRIPT, "status": "unknown", "task": text,
                 "candidates": [],
                 "question": "What would you like done? (see --list for the "
                             "task types this skill routes)"}, args.out)

    candidates = match_verbs(text, tasks) if text else []
    if args.verb:
        if args.verb not in tasks["verbs"]:
            raise CheckError(f"unknown verb {args.verb!r}; --list shows them all")
        verb, how = args.verb, "forced"
    else:
        if not candidates:
            return ({"script": SCRIPT, "status": "unknown", "task": text,
                     "candidates": [],
                     "question": "No task type matched. Classify it yourself "
                                 "against --list and re-run with --verb <name>, "
                                 "or ask the user what they want done."},
                    args.out)
        if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
            tied = [c for c in candidates if c["score"] == candidates[0]["score"]]
            return ({"script": SCRIPT, "status": "ambiguous", "task": text,
                     "candidates": tied,
                     "question": "More than one task type fits. Pick one and "
                                 "re-run with --verb <name> (or ask the user): "
                                 + ", ".join(c["verb"] for c in tied)}, args.out)
        verb, how = candidates[0]["verb"], "table"

    spec = tasks["verbs"][verb]
    cwd = Path.cwd()
    ws = (boardreg.locate(args.workspace, env.boards_root())
          if args.workspace else None)
    ctx = workspace_context(ws, None, imap)

    extracted = extract_args(text, spec, cwd) if text else {}
    explicit = {}
    for kv in args.arg:
        k, _, v = kv.partition("=")
        if not k or not v:
            raise CheckError(f"--arg must be K=V, got {kv!r}")
        explicit[k] = v
    if args.findings:
        explicit.setdefault("findings", args.findings)
    argvals = {**extracted, **explicit}
    if ctx["exists"] and (spec.get("args") or {}).keys() & {"findings", "gate"}:
        gate, report = infer_failing_gate(ctx)
        if gate:
            argvals.setdefault("gate", gate)
            if report:
                argvals.setdefault("findings", report)

    needs: set[str] = set()
    plan = build_plan(verb, spec, tasks, text, argvals, ctx, imap, gate_order,
                      needs)
    view = resume_view(ctx)
    pres = check_preconditions(spec, ctx, view)
    plan["remediations"] = _remediations(argvals.get("findings"))

    if spec["workspace"] == "required" and not ctx["exists"]:
        needs.add("workspace")
    if not plan["steps"]:
        # Every variant's condition missed (e.g. `review` with neither a source
        # to import nor a workspace to re-review). An empty plan is the one
        # answer that must never be silent: ask what the verb is FOR.
        needs.update(n for n, a in (spec.get("args") or {}).items()
                     if a.get("extract", "only") != "none")
        needs.add("workspace")

    need_list = [{"arg": n,
                  "question": ((spec.get("args") or {}).get(n) or {}).get(
                      "question", f"Which {n}?")}
                 for n in sorted(needs)]
    if "workspace" in needs:
        for item in need_list:
            if item["arg"] == "workspace":
                item["question"] = ("Which workspace? (~/dev/boards/<name> with a "
                                    "state.json)")

    blocked = [p for p in pres if not p["ok"]]
    if need_list:
        status = "needs_args"
        question = " ".join(i["question"] for i in need_list)
    elif blocked:
        status = "blocked"
        question = ("Preconditions failed: "
                    + "; ".join(f"{p['name']}: {p['detail']}" for p in blocked))
    else:
        status, question = "planned", None

    payload = {
        "script": SCRIPT, "status": status, "task": text,
        "match": {"verb": verb, "how": how,
                  "score": next((c["score"] for c in candidates
                                 if c["verb"] == verb), None),
                  "matched": next((c["matched"] for c in candidates
                                   if c["verb"] == verb), [])},
        "candidates": candidates,
        "workspace": ctx["workspace"], "board": ctx["board"],
        "workspace_exists": ctx["exists"],
        "args": argvals, "needs": need_list,
        "preconditions": pres, "recipe": plan, "state": view,
    }
    if question:
        payload["question"] = question
    return payload, args.out


EXIT = {"planned": 0, "ambiguous": 1, "unknown": 1, "needs_args": 1,
        "blocked": 1, "error": 2}


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    try:
        payload, out = run(argv)
    except Exception as exc:  # noqa: BLE001  (SPEC 6: any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}, indent=1))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return EXIT.get(payload.get("status"), 2)


if __name__ == "__main__":
    raise SystemExit(main())
