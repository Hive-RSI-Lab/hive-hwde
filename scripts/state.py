"""state.py - state.json read/write helpers for the /hwde orchestrator (SPEC 4, S13).

state.json is the single source of truth for one design run. The orchestrator
NEVER carries pipeline state in its context: it reads the resume summary, acts,
and records every transition here. Every mutating subcommand appends a
timestamped event to `history`, so the file is both current state AND audit
trail (`a killed-and-resumed session continues from the last gate`).

Schema (version 2 - T7 freshness; v1 files upgrade via state_migrate.py):
    {
      "version": 2, "board": str, "workspace": str, "created": ts, "updated": ts,
      "workspace_schema": int,  # file LAYOUT version, absent = 1; `resume`
                                # migrates it (statelib.WORKSPACE_SCHEMA)
      "phase": "P0".."P10" | "done",
      "gates": {gate_name: {phase, status: pass|fail, attempts: int,
                            last: {ts, status, failing_count, total,
                                   inputs: {kind: "<norm>:<sha>"|null}},
                            history: [same shape as last, oldest first],
                            stale: [mark]?}},        # cleared on record-gate
      "human": {checkpoint_id: {status: approved|rejected|skipped, ts, note}},
      "artifacts": {name: {path, kind|null, sha256|null, hashed: ts,
                           stale: [mark]?}},
      "open_issues": [{id, gate, phase, fixer, net, kinds[], severity, count,
                       region, work_order, status: open|fixing|fixed|escalated|
                       waived, agent, attempts, opened, closed}],
      "next_issue_id": int,
      "budgets": {"fix_loops": {gate_name: remaining},
                  "research": {per_run, depth_per_gap},        # U15 caps
                  "cross_stage_spawns": {P4..P9: remaining}},  # U9
      "mode": {token, target, scope, binding, stage, geometry,        # U18
               geometry_is_output, excludes[], requires[], stated_size,
               board_init_outline, ts} | absent,   # reference/build-modes.md
      "decisions": [{what, why, phase, ts}],
      "edits": [{ts, class, refs, note, human_hold, gates, gates_marked,
                 stale_artifacts}],                   # edit-class ledger
      "spawns": [{ts, role, model, effort?, phase?, tokens?, cost_usd?,
                  note?, cross_stage?, issue?}],      # subagent ledger (XC-8)
      "history": [{ts, event, ...detail}]
    }
    mark = {ts, edit_class, refs, human_hold} - stamped by `edit` from
    reference/invalidation.yaml, cleared by record-gate (gates) / re-register
    (artifacts). A gate is FRESH iff its recorded input hashes all match the
    current normalized hashes AND it carries no mark (lib/statelib.py; the
    two-layer semantics are documented in invalidation.yaml).

CLI (spec 6 contract: argparse, JSON to stdout, exit 0 ok / 2 error; exit 1
only for a cross-spawn checkpoint):
    state.py init --workspace DIR --board NAME [--phase P0] [--force]
    state.py show|resume|freshness [--workspace DIR | --state FILE]
        (show/freshness never write; resume first brings the workspace
        layout up to statelib.WORKSPACE_SCHEMA - moving files and saving
        state.json - and lists what it did under "workspace_migration";
        resume --no-migrate writes nothing and reports "pending" there)
    state.py set-phase --phase P7 [--force] ...
    state.py record-gate --gate NAME --result gate_result.json [--phase PN] ...
    state.py artifact --name pcb --path kicad/b.kicad_pcb ...
    state.py edit --class move_fp [--refs U1 U2] [--note TEXT] ...
    state.py rehash [--names gerbers bom] ...
    state.py spawn --role fixer --model opus [--effort high] [--tokens N] ...
    state.py cross-spawn --stage P6 --role part-sourcer --model sonnet
             --brief TEXT [--kinds K ...] [--effort E] ...
    state.py mode --token "learning stage-placement:" [--stated 35x25] ...
    state.py decision --what W --why Y ...
    state.py human --checkpoint 2 --status approved [--note N] ...
    state.py issue --id 3 --status fixed [--agent fixer-1] [--bump-attempts] ...
    state.py budget --path fix_loops.drc_routed [--consume] ...
    state.py log --event name [--data JSON] ...
    state.py snapshot --label L [--files F ...] / restore --label L ...

`cross-spawn` (U9) is the backward-spawn step: a stage at a dead end opens a
`cross_stage` issue and records a ledger spawn tagged `cross_stage: true` with
the issue id, consuming `budgets.cross_stage_spawns.<stage>`. Only the read-only
roles in CROSS_SPAWN_ROLES may be spawned. With the stage's budget spent it
spawns nothing: the issue opens as `escalated`, a decision and a
`cross_spawn_checkpoint` event are recorded, the payload's status is
`checkpoint` and the exit is 1 - the owner decides at the next checkpoint.

`set-phase` REFUSES to advance past a gate phase whose gate has no recorded
result (U16 - bb-buck reached P9 with six passing gate reports on disk and
`gates: {}` in state). `gate.py --workspace <ws>` records the result itself, so
the normal flow never sees the refusal; `--force` is the escape hatch and logs
`phase_forced` with the missing gates.

CLI `log` event names are machine keys: ^[a-z][a-z0-9_-]{0,31}$ (XC-8: a live
run stored paragraphs as event names; prose belongs in --data {"msg": ...}).
`log --event spawn --data {...}` additionally appends the data to the
first-class `spawns` ledger (the SKILL.md step-5 form keeps working).

Writes are atomic (unique temp + fsync + os.replace, same directory; U12
safelib). Writer safety (U12, codex C5/C6): every CLI mutation holds the
OS-exclusive writer lock <state.json>.lock across load->mutate->save, and
State.save() is a base-digest compare-and-swap - a State loaded from bytes X
refuses to overwrite a file that is no longer X (StaleWriteError, exit 2).
`--if-digest SHA` lets an orchestrator pin its own read (show/resume report
`digest`). Snapshot/restore are CONTAINED (no absolute/traversal/symlink
entries, in either direction) and restore is transactional: stage every file
beside its target, verify every hash, then swap - a crash before the swap
phase leaves the workspace untouched.
Snapshot labels/dirs are STABLE interfaces - bench fixture provenance points
at state_snapshots/<label> paths (T5).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import modeslib  # noqa: E402
import safelib  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "state"
VERSION = 2
# CLI log event names are machine keys (XC-8): short, greppable, no prose.
EVENT_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
PHASES = ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10",
          "done"]
# Machine-gate order along the pipeline (SPEC 3). The interim `drc` gate is a
# tool, not a pipeline edge, so it is not listed. `sim` is conditional (a board
# without testbenches does not owe it) - use applicable_gate_order().
GATE_ORDER = [("P4", "erc"), ("P6", "place"), ("P7", "drc_routed"),
              ("P8", "verify"), ("P9", "dfm")]
# Human checkpoints (SPEC 7). 3 is optional-but-on-by-default.
CHECKPOINTS = {"1": "P2", "2": "P4", "3": "P6", "4": "P8", "5": "P10"}
DEFAULT_BUDGETS = {
    "fix_loops": {g: 3 for _, g in GATE_ORDER},
    "freerouting_retries": 2,
    "place_edit_iterations": 8,
    # U15 research caps (owner ruling: research launches AUTOMATICALLY on a
    # coverage gap, so the caps are what bound it): per_run = research tasks
    # opened per run (research.py open consumes one), depth_per_gap = sources
    # acquired per task (research.py fetch counts against it). Cap-hit =
    # a VISIBLE checkpoint (status checkpoint, decision + event recorded),
    # never silent truncation.
    "research": {"per_run": 6, "depth_per_gap": 4},
    # U9 backward spawns (design decision 4): a stage at a dead end spawns a
    # read-only scout for an upstream answer, at most this many per stage.
    # Exhausted = a visible checkpoint to the owner, never a third spawn.
    # U8/U10 live evidence may tune the default 2 per stage.
    "cross_stage_spawns": {p: 2 for p in ("P4", "P5", "P6", "P7", "P8",
                                          "P9")},
}
# Roles a backward spawn may launch. They RETURN a proposal (a candidate
# part, an extraction, a finding); none of them writes a board artifact -
# the orchestrator applies the answer through board_update + `edit --class`,
# so one writer per artifact stands.
CROSS_SPAWN_ROLES = ("part-sourcer", "research-component-scout",
                     "datasheet-extractor", "researcher")
SNAP_DIR = "state_snapshots"
# Standard workspace layout (T6 state-scaffold): init owns the scaffold so
# the orchestrator playbook does not transcribe a directory list every run.
SUBDIRS = ("brief", "architecture", "research", "parts", "lib", "kicad",
           "routing", "reports", "fab", "log")
# Digest discipline (T6 XC-4): SKILL says 5-10 lines per phase digest; the
# post-v1 runs drifted to 23-82. Warn (never fail) when the digest for the
# phase just left is missing or exceeds this cap (headroom for dense phases).
DIGEST_LINE_CAP = 15


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def applicable_gate_order(ws: Path, board: str,
                          registry: dict | None = None,
                          imap: dict | None = None) -> list[tuple[str, str]]:
    """The gates THIS board owes, in pipeline order: GATE_ORDER plus
    ("P8", "sim") when the workspace ships a testbench directory (a board
    with testbenches must pass them; a board without does not owe the gate).

    Single definition of the owed set: releaselib.required_gates derives the
    release list from here, set_phase gates advancement on it, and
    resume_summary reports gates_passed / next_gate against it (U16).
    """
    try:
        imap = imap or statelib.load_map()
        sims_rel = statelib.kind_path("sims", board, imap, registry, ws)
        has_sims = (Path(ws) / sims_rel).is_dir()
    except Exception:  # noqa: BLE001 - map unreadable: fall back to the spine
        has_sims = False
    order: list[tuple[str, str]] = []
    for ph, g in GATE_ORDER:
        order.append((ph, g))
        if g == "verify" and has_sims:
            order.append(("P8", "sim"))
    return order


def _atomic_write(path: Path, text: str) -> None:
    safelib.atomic_write_text(path, text)


LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


def _check_label(label) -> str:
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label) \
            or ".." in label:
        raise safelib.ContainmentError(
            f"snapshot label {label!r} refused: labels are a single path "
            "component [A-Za-z0-9][A-Za-z0-9._-]*")
    return label


def _check_snapshot_entry(rel_norm: str, what: str) -> None:
    """state.json and writer-lock files are never snapshot content: a
    restore would roll the state file back under its own writer and replace
    a lock file other writers hold open."""
    name = rel_norm.rsplit("/", 1)[-1]
    if name == "state.json" or name.endswith(".lock"):
        raise safelib.ContainmentError(
            f"{what} {rel_norm!r} refused: state.json and *.lock files are "
            "never snapshotted or restored")


def _snapshot_dir(ws: Path, label: str) -> Path:
    """ws/state_snapshots/<label>, proven inside the workspace: a symlinked
    state_snapshots/ or <label> dir is refused, not followed (U12)."""
    return safelib.contained_rel(ws, f"{SNAP_DIR}/{label}",
                                 what="snapshot dir")


class State:
    """In-memory view of one state.json. Mutators record history themselves;
    call save() (atomic) after a batch of mutations."""

    def __init__(self, path: Path, data: dict,
                 base_digest: str | None = None):
        self.path = Path(path)
        self.data = data
        # sha256 of the exact bytes this view was loaded from (None for a
        # fresh init); save() refuses when the file no longer matches (CAS)
        self.base_digest = base_digest

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def init(cls, workspace: Path, board: str, phase: str = "P0",
             force: bool = False) -> "State":
        workspace = Path(workspace)
        path = workspace / "state.json"
        if path.exists() and not force:
            raise CheckError(f"{path} already exists (use --force to recreate)")
        if phase not in PHASES:
            raise CheckError(f"unknown phase {phase!r}")
        workspace.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:  # idempotent scaffold; pre-existing content survives
            (workspace / d).mkdir(exist_ok=True)
        ts = now()
        data = {
            "version": VERSION, "board": board,
            "workspace_schema": statelib.WORKSPACE_SCHEMA,
            "workspace": str(workspace).replace("\\", "/"),
            "created": ts, "updated": ts, "phase": phase,
            "gates": {}, "human": {}, "artifacts": {}, "open_issues": [],
            "next_issue_id": 1,
            "budgets": json.loads(json.dumps(DEFAULT_BUDGETS)),
            "decisions": [], "edits": [], "spawns": [],
            "history": [{"ts": ts, "event": "init", "board": board,
                         "phase": phase}],
        }
        st = cls(path, data)
        st.save()
        return st

    @classmethod
    def load(cls, path: Path) -> "State":
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CheckError(f"cannot read state file {path}: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckError(f"state file {path} is not valid JSON: {exc}") \
                from exc
        if not isinstance(data, dict):
            raise CheckError(f"state file {path} is not a JSON object")
        if data.get("version") != VERSION:
            raise CheckError(
                f"{path}: state version {data.get('version')!r} unsupported "
                f"(this build reads v{VERSION}; upgrade v1 with "
                f"state_migrate.py --workspace {path.parent})")
        return cls(path, data, base_digest=safelib.sha256_bytes(raw))

    def current_digest(self) -> str | None:
        try:
            return safelib.sha256_file(self.path)
        except OSError:
            return None

    def save(self) -> None:
        """Atomic + compare-and-swap: under the writer lock, refuse when the
        on-disk bytes no longer match the bytes this view was loaded from
        (another writer landed in between). Never merges, never retries -
        the caller reloads and redoes its mutation on the fresh state."""
        with safelib.writer_lock(self.path, what="state.json"):
            if self.base_digest is not None:
                cur = self.current_digest()
                if cur != self.base_digest:
                    raise safelib.StaleWriteError(
                        f"{self.path} changed since it was loaded (base "
                        f"{self.base_digest[:12]}, now "
                        f"{(cur or 'missing')[:12]}) - another writer landed; "
                        "reload and redo the mutation, nothing was written")
            self.data["updated"] = now()
            text = json.dumps(self.data, indent=1)
            _atomic_write(self.path, text)
            self.base_digest = safelib.sha256_bytes(text.encode("utf-8"))

    def _log(self, event: str, **detail) -> None:
        self.data["history"].append({"ts": now(), "event": event, **detail})

    # ---- mutators --------------------------------------------------------
    def gate_coverage(self, phase: str) -> tuple[list[str], list[str]]:
        """(gates owed BEFORE `phase` with no recorded result, gates whose
        last recorded result is a fail). A gate at phase P is owed once the
        run moves past P - erc (P4) is owed at P5, not at P4 itself."""
        idx = PHASES.index(phase)
        owed = [(ph, g) for ph, g
                in applicable_gate_order(self.path.parent,
                                         self.data.get("board") or "",
                                         self.data.get("artifacts"))
                if PHASES.index(ph) < idx]
        gates = self.data["gates"]
        missing = [f"{g} ({ph})" for ph, g in owed
                   if not (gates.get(g) or {}).get("status")]
        failed = [g for _, g in owed
                  if (gates.get(g) or {}).get("status") == "fail"]
        return missing, failed

    def set_phase(self, phase: str, require_gates: bool = True) -> list[dict]:
        """Record the phase; return warn-only digest-discipline findings for
        the phase just left (T6 XC-4 - drift becomes recorded fact, exit 0).

        U16: ADVANCING past a gate phase whose gate has no recorded result is
        REFUSED (CheckError). bb-buck walked P4 -> P9 with six passing gate
        reports on disk and `gates: {}` in state, because running the gate and
        recording it were separate steps and only the first was enforced. The
        phase machine is now the second tooth: you cannot leave a gate phase
        without evidence in state.json. `require_gates=False` (CLI --force) is
        the deliberate escape hatch and records itself in history.
        """
        if phase not in PHASES:
            raise CheckError(f"unknown phase {phase!r}")
        prev = self.data["phase"]
        warnings: list[dict] = []
        advancing = (prev in PHASES
                     and PHASES.index(phase) > PHASES.index(prev))
        if advancing:
            missing, failed = self.gate_coverage(phase)
            if missing and require_gates:
                raise CheckError(
                    f"cannot advance {prev} -> {phase}: no recorded gate "
                    f"result for {', '.join(missing)}. Run the gate with "
                    "gate.py --workspace <ws> (it records the result itself) "
                    "or state.py record-gate --gate <g> --result <file>; "
                    "--force advances anyway and says so in history")
            if missing:
                warnings.append({
                    "kind": "gate_coverage",
                    "msg": f"advanced to {phase} with no recorded result for "
                           f"{', '.join(missing)} (--force)"})
                self._log("phase_forced", phase=phase, prev=prev,
                          missing=missing)
            if failed:
                warnings.append({
                    "kind": "gate_coverage",
                    "msg": f"advanced to {phase} with {', '.join(failed)} "
                           "recorded FAIL - the fix loop re-records a pass "
                           "before the run moves on"})
        self.data["phase"] = phase
        self._log("phase", phase=phase, prev=prev)
        if prev != phase and re.fullmatch(r"P\d+", prev or ""):
            digest = self.path.parent / "log" / f"{prev}-digest.md"
            if not digest.is_file():
                warnings.append({
                    "kind": "digest_discipline",
                    "msg": f"log/{prev}-digest.md missing for the phase just "
                           "left (SKILL: write 5-10 lines before set-phase)"})
            else:
                n = len(digest.read_text(encoding="utf-8",
                                         errors="replace").splitlines())
                if n > DIGEST_LINE_CAP:
                    warnings.append({
                        "kind": "digest_discipline",
                        "msg": f"log/{prev}-digest.md is {n} lines "
                               f"(cap {DIGEST_LINE_CAP}; SKILL says 5-10)"})
        return warnings

    def record_gate(self, gate: str, result: dict, phase: str | None = None) -> dict:
        status = result.get("status")
        if status not in ("pass", "fail"):
            raise CheckError(f"gate result status must be pass|fail, "
                             f"got {status!r}")
        # U5 tooth: when the result carries the report's stamped input
        # digest (gate.py emits it since U5), it must match the CURRENT
        # primary input - otherwise the result describes a different or
        # stale artifact and recording it would mark the gate fresh-pass
        # falsely (hit live: a failed gate re-run left the OLD result file
        # behind and record-gate happily blessed it). Legacy results
        # without the field are recorded as before.
        digest = result.get("input_digest")
        if digest:
            imap = self._imap()
            kinds = imap["gate_inputs"].get(gate) or []
            if kinds:
                rel, cur = statelib.hash_kind(
                    self.path.parent, self.data.get("board") or "",
                    kinds[0], imap, self.data["artifacts"])
                if cur != digest:
                    raise CheckError(
                        f"gate result input_digest does not match the "
                        f"current {kinds[0]} ({rel}) - the result describes "
                        "a different or stale artifact; re-run the gate "
                        "against the current file")
        entry = {"ts": now(), "status": status,
                 "failing_count": result.get("failing_count", 0),
                 "total": (result.get("counts") or {}).get("total", 0),
                 "inputs": self._hash_gate_inputs(gate)}
        g = self.data["gates"].setdefault(
            gate, {"phase": phase or result.get("phase"), "status": None,
                   "attempts": 0, "last": None, "history": []})
        if phase:
            g["phase"] = phase
        g["attempts"] += 1
        g["status"] = status
        g["last"] = entry
        g["history"].append(entry)
        # the gate just ran against the CURRENT inputs: whatever edit marks it
        # carried are resolved (pass or fail - the result is current either way)
        g.pop("stale", None)
        self._log("gate", gate=gate, status=status, attempt=g["attempts"],
                  failing_count=entry["failing_count"])
        return g

    def _imap(self) -> dict:
        return statelib.load_map()

    def _hash_gate_inputs(self, gate: str) -> dict:
        """Normalized hashes of every artifact the gate reads (statelib),
        resolved against the state file's own directory - the freshness key
        the result stays valid under. Hashed kinds are silently auto-
        registered in the artifacts registry (XC-8: the registry was dead
        because registration was a separate manual step)."""
        imap = self._imap()
        ws = self.path.parent
        board = self.data.get("board") or ""
        registry = self.data["artifacts"]
        inputs: dict[str, str | None] = {}
        for kind in imap["gate_inputs"].get(gate, []):
            rel, sha = statelib.hash_kind(ws, board, kind, imap, registry)
            inputs[kind] = sha
            entry = registry.get(kind)
            if not isinstance(entry, dict):
                entry = {}
            entry.update({"path": rel, "kind": kind, "sha256": sha,
                          "hashed": now()})
            registry[kind] = entry          # stale marks (if any) survive
        return inputs

    def set_artifact(self, name: str, path: str) -> None:
        """Register/refresh an artifact by name. Explicit registration means
        "this file was (re)produced": the entry is re-hashed and any stale
        marks on it are cleared."""
        rel = str(path).replace("\\", "/")
        imap = self._imap()
        kind = name if name in imap["artifact_kinds"] else None
        norm = (imap["artifact_kinds"][kind]["norm"] if kind
                else statelib.norm_for_path(self.path.parent / rel))
        sha = statelib.hash_artifact(self.path.parent / rel, norm)
        self.data["artifacts"][name] = {"path": rel, "kind": kind,
                                        "sha256": sha, "hashed": now()}
        self._log("artifact", name=name, path=rel)

    def apply_edit(self, edit_class: str, refs: list[str] | None = None,
                   note: str | None = None) -> dict:
        """Record a declared edit: stamp the invalidation map's stale set.
        Marks land on RECORDED gates (an unrun gate has no result to
        distrust) and on the mapped derived artifacts (registry entries are
        created at their current hash when absent, so later regeneration is
        detectable). Returns the full mapped sets + the ceremony weight."""
        imap = self._imap()
        ec = imap["edit_classes"].get(edit_class)
        if ec is None:
            raise CheckError(
                f"unknown edit class {edit_class!r} "
                f"(known: {', '.join(sorted(imap['edit_classes']))})")
        ts = now()
        mark = {"ts": ts, "edit_class": edit_class, "refs": refs or [],
                "human_hold": ec["human_hold"]}
        marked_gates = []
        for gname in ec["gates"]:
            g = self.data["gates"].get(gname)
            if g is not None:
                g.setdefault("stale", []).append(mark)
                marked_gates.append(gname)
        ws = self.path.parent
        board = self.data.get("board") or ""
        registry = self.data["artifacts"]
        for kind in ec["stale_artifacts"]:
            entry = registry.get(kind)
            if not isinstance(entry, dict):
                rel, sha = statelib.hash_kind(ws, board, kind, imap, registry)
                entry = {"path": rel, "kind": kind, "sha256": sha,
                         "hashed": ts}
                registry[kind] = entry
            entry.setdefault("stale", []).append(mark)
        rec = {"ts": ts, "class": edit_class, "refs": refs or [],
               "note": note, "human_hold": ec["human_hold"],
               "gates": list(ec["gates"]), "gates_marked": marked_gates,
               "stale_artifacts": list(ec["stale_artifacts"])}
        self.data["edits"].append(rec)
        self._log("edit", edit_class=edit_class, refs=refs or [],
                  human_hold=ec["human_hold"])
        return rec

    def rehash(self, names: list[str] | None = None) -> dict:
        """Re-hash artifacts. Explicit --names = "I regenerated these": their
        stale marks are force-cleared. A bare rehash refreshes every
        registered entry plus every standard kind whose file exists, clearing
        marks only where the hash actually CHANGED (an untouched derived
        artifact keeps its mark - it still does not derive from the current
        design)."""
        imap = self._imap()
        ws = self.path.parent
        board = self.data.get("board") or ""
        registry = self.data["artifacts"]
        explicit = names is not None
        if names is None:
            names = sorted(set(registry) | {
                k for k in imap["artifact_kinds"]
                if (ws / statelib.kind_path(k, board, imap, registry, ws)).exists()})
        out = {}
        for name in names:
            entry = registry.get(name)
            if not isinstance(entry, dict):
                if name not in imap["artifact_kinds"]:
                    raise CheckError(f"unknown artifact {name!r} (not "
                                     "registered, not a standard kind)")
                entry = {"kind": name}
            # an existing entry's kind is authoritative - a null kind on a
            # kind-named entry is DELIBERATE (migration: same name, different
            # file) and must not be resurrected from the name here
            kind = entry.get("kind")
            if kind not in imap["artifact_kinds"]:
                kind = None
            rel = entry.get("path") or statelib.kind_path(
                kind or name, board, imap, registry, ws)
            norm = (imap["artifact_kinds"][kind]["norm"] if kind
                    else statelib.norm_for_path(ws / rel))
            old = entry.get("sha256")
            sha = statelib.hash_artifact(ws / rel, norm)
            entry.update({"path": rel, "kind": kind, "sha256": sha,
                          "hashed": now()})
            if entry.get("stale") and (explicit or sha != old):
                entry.pop("stale", None)
            registry[name] = entry
            out[name] = {"path": rel, "sha256": sha, "changed": sha != old,
                         "stale_marks": len(entry.get("stale") or [])}
        self._log("rehash", names=sorted(out))
        return out

    def restamp_input(self, kind: str, old: str, new: str,
                      why: str) -> list[str]:
        """Carry PASSED gates recorded against input `kind` at hash `old`
        over to hash `new`, and the registry entry with them. Only for an
        edit the caller has proved changes nothing a gate reads except in a
        direction that can only clear findings (check_model_paths --fix
        rewrites 3D model paths and nothing else). A gate recorded against
        any other hash was already stale and stays so; a failed gate is left
        to re-run; stale marks are untouched. Returns the gates moved."""
        moved = []
        for gname, g in sorted(self.data["gates"].items()):
            inputs = (g.get("last") or {}).get("inputs") or {}
            if g.get("status") == "pass" and inputs.get(kind) == old:
                inputs[kind] = new
                moved.append(gname)
        entry = self.data["artifacts"].get(kind)
        if isinstance(entry, dict) and entry.get("sha256") == old:
            entry.update({"sha256": new, "hashed": now()})
        if moved:
            self._log("restamp", kind=kind, gates=moved, why=why,
                      old=old, new=new)
        return moved

    def record_spawn(self, record: dict) -> dict:
        """Append a subagent spawn to the first-class ledger (XC-8: tier
        choices survived only as digest prose; the SKILL step-5 log form
        routes here too)."""
        rec = {"ts": now(), **{k: v for k, v in record.items()
                               if v is not None}}
        self.data["spawns"].append(rec)
        self._log("spawn", role=rec.get("role"), model=rec.get("model"))
        return rec

    def cross_spawn(self, stage: str, role: str, model: str, brief: str,
                    kinds: list[str] | None = None,
                    effort: str | None = None) -> dict:
        """U9 backward spawn: issue + budget + tagged ledger entry, or a
        visible checkpoint when the stage's budget is spent."""
        if stage not in PHASES:
            raise CheckError(f"unknown stage {stage!r}")
        if role not in CROSS_SPAWN_ROLES:
            raise CheckError(
                f"role {role!r} may not be spawned backward: it would write "
                "an artifact another stage owns (allowed: "
                f"{', '.join(CROSS_SPAWN_ROLES)})")
        path = f"cross_stage_spawns.{stage}"
        remaining = self.budget(path)
        issue = self.open_issue({
            "gate": None, "phase": stage, "fixer": role,
            "kinds": ["cross_stage", *(kinds or [])], "severity": "blocking",
            "work_order": brief, "cross_stage": True})
        if remaining <= 0:
            issue["status"] = "escalated"
            self.add_decision(
                what=f"cross-stage spawn budget spent at {stage}: issue "
                     f"{issue['id']} escalated, no spawn",
                why=f"budgets.{path} exhausted; visible checkpoint, not a "
                    "silent third spawn", phase=stage)
            self._log("cross_spawn_checkpoint", stage=stage,
                      issue=issue["id"])
            return {"status": "checkpoint", "checkpoint": "cross_stage_cap",
                    "issue": issue, "remaining": 0,
                    "action": ("present at the next human checkpoint: raise "
                               f"budgets.{path} in state.json and re-run, "
                               "or rule on the dead end directly (state.py "
                               "decision), then close the issue")}
        remaining = self.budget(path, consume=True)
        issue["status"], issue["agent"] = "fixing", role
        spawn = self.record_spawn({
            "role": role, "model": model, "effort": effort, "phase": stage,
            "note": brief, "cross_stage": True, "issue": issue["id"]})
        return {"status": "spawned", "issue": issue, "spawn": spawn,
                "remaining": remaining}

    def set_mode(self, token: str, stated: str | None = None) -> dict:
        """Record the brief's build mode as FIRST-CLASS state (U18).

        The mode is what later stages read to know whether the stated geometry
        is an input or an output - `board_init` refuses a fixed outline under a
        geometry-relaxing binding - so it cannot live in decision prose. The
        decision is recorded too (relaxation is never silent), but the machine
        answer is here.
        """
        mode = modeslib.resolve(token)
        size = modeslib.parse_size(stated or "") if stated else None
        if stated and size is None:
            raise CheckError(f"--stated {stated!r} is not a W x H size "
                             "(e.g. 35x25)")
        plan = modeslib.geometry_plan(mode, size)
        rec = {"token": mode["token"], "target": mode["target"],
               "scope": mode["scope"], "binding": mode["binding"],
               "stage": mode["stage"], "geometry": mode["geometry"],
               "geometry_is_output": mode["geometry_is_output"],
               "excludes": mode["excludes"], "requires": mode["requires"],
               "stated_size": list(size) if size else None,
               "board_init_outline": plan["board_init_outline"], "ts": now()}
        self.data["mode"] = rec
        self._log("mode", target=rec["target"], scope=rec["scope"],
                  binding=rec["binding"])
        self.add_decision(f"Build mode: {modeslib.summary(mode)}",
                          f"token {mode['token']!r} in the brief; contract in "
                          "reference/build-modes.md")
        if plan["decision"]:
            self.add_decision(plan["decision"]["what"],
                              plan["decision"]["why"])
        return {"mode": rec, "geometry_plan": plan}

    def add_decision(self, what: str, why: str, phase: str | None = None) -> None:
        self.data["decisions"].append(
            {"what": what, "why": why, "phase": phase or self.data["phase"],
             "ts": now()})
        self._log("decision", what=what)

    def record_human(self, checkpoint: str, status: str,
                     note: str | None = None) -> None:
        if checkpoint not in CHECKPOINTS:
            raise CheckError(f"unknown checkpoint {checkpoint!r} "
                             f"(known: {', '.join(CHECKPOINTS)})")
        if status not in ("approved", "rejected", "skipped"):
            raise CheckError("human status must be approved|rejected|skipped")
        self.data["human"][checkpoint] = {"status": status, "ts": now(),
                                          "note": note}
        self._log("human", checkpoint=checkpoint, status=status)

    def open_issue(self, issue: dict) -> dict:
        iid = self.data["next_issue_id"]
        self.data["next_issue_id"] = iid + 1
        rec = {"id": iid, "status": "open", "agent": None, "attempts": 0,
               "opened": now(), "closed": None, **issue}
        self.data["open_issues"].append(rec)
        self._log("issue_open", id=iid, gate=rec.get("gate"),
                  fixer=rec.get("fixer"), kinds=rec.get("kinds"))
        return rec

    def update_issue(self, iid: int, status: str | None = None,
                     agent: str | None = None, bump: bool = False) -> dict:
        for rec in self.data["open_issues"]:
            if rec["id"] == iid:
                if status:
                    if status not in ("open", "fixing", "fixed", "escalated",
                                      "waived"):
                        raise CheckError(f"bad issue status {status!r}")
                    rec["status"] = status
                    if status in ("fixed", "waived"):
                        rec["closed"] = now()
                if agent:
                    rec["agent"] = agent
                if bump:
                    rec["attempts"] += 1
                self._log("issue", id=iid, status=rec["status"],
                          agent=rec["agent"], attempts=rec["attempts"])
                return rec
        raise CheckError(f"no issue with id {iid}")

    def budget(self, dotted: str, consume: bool = False) -> int:
        node = self.data["budgets"]
        keys = dotted.split(".")
        # A budget family added after this state.json was created (U15's
        # `research`) is installed from DEFAULT_BUDGETS on first touch and
        # logged - a run started before the family existed still gets the
        # cap, and the install is visible in the history.
        if keys[0] not in node and keys[0] in DEFAULT_BUDGETS:
            node[keys[0]] = json.loads(json.dumps(DEFAULT_BUDGETS[keys[0]]))
            self._log("budget_defaulted", family=keys[0],
                      values=node[keys[0]])
        for k in keys[:-1]:
            node = node.get(k)
            if not isinstance(node, dict):
                raise CheckError(f"unknown budget path {dotted!r}")
        leaf = keys[-1]
        if leaf not in node:
            raise CheckError(f"unknown budget path {dotted!r}")
        if consume:
            if node[leaf] <= 0:
                raise CheckError(f"budget {dotted} exhausted")
            node[leaf] -= 1
            self._log("budget", path=dotted, remaining=node[leaf])
        return node[leaf]

    # ---- snapshots (fix-loop safety net; SPEC 4 rollback) ----------------
    def _workspace(self) -> Path:
        return Path(self.data["workspace"])

    def snapshot(self, label: str, files: list[str] | None = None) -> dict:
        """Contained copy of workspace files into state_snapshots/<label>.
        Every entry is proven inside the workspace (no absolute/traversal/
        symlink entries, U12) before a byte is copied."""
        ws = self._workspace()
        label = _check_label(label)
        dest = _snapshot_dir(ws, label)
        rels = files or [a["path"] for a in self.data["artifacts"].values()
                         if isinstance(a, dict) and a.get("path")
                         and (ws / a["path"]).is_file()]
        plan = []
        for rel in rels:
            rel_norm = str(rel).replace("\\", "/")
            src = safelib.contained_rel(ws, rel_norm, what="snapshot entry")
            if src.is_symlink() or not src.is_file():
                raise CheckError(
                    f"snapshot source missing or not a regular file: {src}")
            if rel_norm.split("/")[0] == SNAP_DIR:
                raise safelib.ContainmentError(
                    f"snapshot entry {rel_norm!r} lies inside {SNAP_DIR}/")
            _check_snapshot_entry(rel_norm, "snapshot entry")
            plan.append((rel_norm, src))
        if dest.exists():
            shutil.rmtree(dest)
        manifest = []
        for rel_norm, src in plan:
            out = safelib.contained_rel(dest, rel_norm, what="snapshot copy")
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            manifest.append({"path": rel_norm,
                             "sha256": safelib.sha256_file(out)})
        safelib.atomic_write_json(
            dest / "manifest.json",
            {"label": label, "ts": now(), "files": manifest})
        self._log("snapshot", label=label, files=len(manifest))
        return {"label": label, "files": manifest}

    def restore(self, label: str) -> dict:
        """Transactional restore: validate + stage + verify EVERY file
        beside its target, then swap each into place with os.replace. Any
        failure (bad entry, missing file, hash mismatch, crash) before the
        swap phase leaves the workspace byte-for-byte untouched."""
        ws = self._workspace()
        label = _check_label(label)
        dest = _snapshot_dir(ws, label)
        man = checklib.load_json(dest / "manifest.json",
                                 f"snapshot {label} manifest")
        files = man.get("files") if isinstance(man, dict) else None
        if not isinstance(files, list):
            raise CheckError(f"snapshot {label} manifest has no files list")
        plan = []
        for f in files:
            if not isinstance(f, dict) or not isinstance(f.get("path"), str) \
                    or not isinstance(f.get("sha256"), str):
                raise CheckError(f"snapshot {label} manifest entry malformed: "
                                 f"{f!r}")
            rel = f["path"]
            _check_snapshot_entry(rel.replace("\\", "/"), "snapshot file")
            src = safelib.contained_rel(dest, rel, what="snapshot file")
            if src.is_symlink() or not src.is_file():
                raise CheckError(f"snapshot file missing or not a regular "
                                 f"file: {src}")
            target = safelib.contained_rel(ws, rel, what="restore target")
            if target.exists() and (target.is_symlink()
                                    or not target.is_file()):
                raise safelib.ContainmentError(
                    f"restore target {target} is not a regular file")
            safelib.sweep_stale_stage_temps(target)
            plan.append((rel, src, target, f["sha256"]))
        staged: list[tuple[Path, Path, str, str]] = []
        restored = []
        try:
            for rel, src, target, sha in plan:
                tmp = safelib.stage_copy(src, target)
                staged.append((tmp, target, sha, rel))
                safelib.fault("restore.staged", rel=rel, n=len(staged))
            for tmp, target, sha, rel in staged:
                got = safelib.sha256_file(tmp)
                if got != sha:
                    raise CheckError(
                        f"restore hash mismatch for {rel} (snapshot {label} "
                        f"is corrupt: {got[:12]} != {sha[:12]}) - nothing "
                        "restored")
            safelib.fault("restore.verified", label=label)
            for tmp, target, sha, rel in staged:
                if target.is_symlink():
                    raise safelib.ContainmentError(
                        f"restore target {target} became a symlink")
                os.replace(tmp, target)
                restored.append(rel)
        finally:
            for tmp, _t, _s, _r in staged:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
        self._log("restore", label=label, files=len(restored))
        return {"label": label, "restored": restored}

    # ---- freshness -------------------------------------------------------
    def freshness(self) -> dict:
        """Read-only two-layer freshness report (statelib): per recorded gate
        hash validity + stale marks, per artifact registered-vs-current."""
        return statelib.freshness_report(self.data, self.path.parent,
                                         self._imap())

    # ---- resume ----------------------------------------------------------
    def resume_summary(self) -> dict:
        gates = self.data["gates"]
        # U16: the owed set, not the bare spine - a board that ships
        # testbenches owes `sim` too, so resume must count it as passed and
        # name it as the next gate when it is missing.
        order = applicable_gate_order(self.path.parent,
                                      self.data.get("board") or "",
                                      self.data.get("artifacts"))
        passed = [g for _, g in order
                  if gates.get(g, {}).get("status") == "pass"]
        next_gate = None
        for ph, g in order:
            if gates.get(g, {}).get("status") != "pass":
                next_gate = {"phase": ph, "gate": g}
                break
        open_issues = [i for i in self.data["open_issues"]
                       if i["status"] in ("open", "fixing")]
        pending_human = [cp for cp, ph in CHECKPOINTS.items()
                         if PHASES.index(self.data["phase"]) >
                         PHASES.index(ph) and cp not in self.data["human"]]
        last = self.data["history"][-1] if self.data["history"] else None
        fresh = self.freshness()
        # U5 (codex C1): phase is workflow position, never a release
        # certificate - resume surfaces the DERIVED disposition beside it so
        # a P10 phase can no longer read as "order-ready". Advisory here:
        # any releaselib failure degrades to null, never breaks resume.
        disposition_error = None
        try:
            import releaselib  # noqa: E402  (lib dir on sys.path)
            disposition = releaselib.disposition(
                self.path.parent)["disposition"]
        except Exception as exc:  # noqa: BLE001
            # surfaced, not swallowed: a null disposition with no reason
            # would hide the worst rung (U5 review)
            disposition = None
            disposition_error = f"{type(exc).__name__}: {exc}"
        return {
            "script": SCRIPT, "board": self.data["board"],
            "mode": self.data.get("mode"),
            "release_disposition": disposition,
            **({"release_disposition_error": disposition_error}
               if disposition_error else {}),
            "workspace": self.data["workspace"], "phase": self.data["phase"],
            "gates_passed": passed, "next_gate": next_gate,
            "gates_passed_fresh": [g for g in passed
                                   if fresh["gates"][g]["fresh"]],
            "gates_stale": fresh["summary"]["stale"],
            "gates_freshness_unknown": fresh["summary"]["unknown"],
            "human_hold_pending": fresh["summary"]["human_hold_pending"],
            "open_issues": open_issues, "pending_human": sorted(pending_human),
            "budgets": self.data["budgets"],
            "artifacts": self.data["artifacts"], "last_event": last,
        }


# ---- CLI ----------------------------------------------------------------
def _find_state(args) -> Path:
    if getattr(args, "state", None):
        return Path(args.state)
    ws = getattr(args, "workspace", None)
    if ws:
        return Path(ws) / "state.json"
    p = Path("state.json")
    if p.exists():
        return p
    raise CheckError("give --state FILE or --workspace DIR (no ./state.json)")


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, state_required=True):
        p.add_argument("--state", help="path to state.json")
        p.add_argument("--workspace", help="workspace dir (state.json inside)")
        p.add_argument("--out", help="write result JSON here instead of stdout")
        p.add_argument("--if-digest",
                       help="compare-and-swap pin: refuse unless the current "
                            "state.json sha256 equals this (show/resume "
                            "report `digest`)")

    p = sub.add_parser("init", help="create a new state.json")
    p.add_argument("--workspace", required=True)
    p.add_argument("--board", required=True)
    p.add_argument("--phase", default="P0")
    p.add_argument("--force", action="store_true")
    p.add_argument("--out")

    for name in ("show", "resume", "freshness"):
        p = sub.add_parser(name)
        common(p)
        if name == "resume":
            p.add_argument("--no-migrate", action="store_true",
                           help="resume without migrating the workspace "
                                "layout (a read-only caller); reports "
                                "\"pending\"")

    p = sub.add_parser("set-phase")
    common(p)
    p.add_argument("--phase", required=True)
    p.add_argument("--force", action="store_true",
                   help="advance even when an owed gate has no recorded "
                        "result (logged as phase_forced with the list)")

    p = sub.add_parser("record-gate")
    common(p)
    p.add_argument("--gate", required=True)
    p.add_argument("--result", required=True,
                   help="gate.py result JSON (has status/failing_count)")
    p.add_argument("--phase")

    p = sub.add_parser("artifact")
    common(p)
    p.add_argument("--name", required=True)
    p.add_argument("--path", required=True)

    p = sub.add_parser("edit", help="record a declared edit; stamps the "
                       "invalidation.yaml stale set")
    common(p)
    p.add_argument("--class", dest="edit_class", required=True,
                   help="edit class from reference/invalidation.yaml")
    p.add_argument("--refs", nargs="*", default=None,
                   help="refdes/net names the edit touches (for the record)")
    p.add_argument("--note")

    p = sub.add_parser("rehash", help="re-hash artifacts; --names = "
                       "force-clear their stale marks (regenerated)")
    common(p)
    p.add_argument("--names", nargs="*", default=None)

    p = sub.add_parser("spawn", help="record a subagent spawn in the ledger")
    common(p)
    p.add_argument("--role", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort")
    p.add_argument("--phase")
    p.add_argument("--tokens", type=int)
    p.add_argument("--cost-usd", type=float, dest="cost_usd")
    p.add_argument("--note")

    p = sub.add_parser("cross-spawn", help="U9 backward spawn: issue + "
                       "budgeted, tagged ledger spawn (checkpoint when spent)")
    common(p)
    p.add_argument("--stage", required=True, help="the stage at the dead end")
    p.add_argument("--role", required=True, choices=CROSS_SPAWN_ROLES)
    p.add_argument("--model", required=True)
    p.add_argument("--effort")
    p.add_argument("--brief", required=True,
                   help="the narrow brief the scout gets")
    p.add_argument("--kinds", nargs="*", default=None)

    p = sub.add_parser("mode", help="record the brief's build mode "
                       "(reference/build-modes.md)")
    common(p)
    p.add_argument("--token", required=True,
                   help="the token the brief opens with, e.g. "
                        "'learning stage-placement:'")
    p.add_argument("--stated",
                   help="a size the brief or a checkpoint named, e.g. 35x25 - "
                        "under a geometry-relaxing binding this is recorded "
                        "as relaxed, never as a cap")

    p = sub.add_parser("decision")
    common(p)
    p.add_argument("--what", required=True)
    p.add_argument("--why", required=True)
    p.add_argument("--phase")

    p = sub.add_parser("human")
    common(p)
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--note")

    p = sub.add_parser("issue")
    common(p)
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--status")
    p.add_argument("--agent")
    p.add_argument("--bump-attempts", action="store_true")

    p = sub.add_parser("budget")
    common(p)
    p.add_argument("--path", required=True, dest="bpath",
                   help="dotted path, e.g. fix_loops.drc_routed")
    p.add_argument("--consume", action="store_true")

    p = sub.add_parser("log")
    common(p)
    p.add_argument("--event", required=True)
    p.add_argument("--data", help="extra JSON object merged into the event")

    p = sub.add_parser("snapshot")
    common(p)
    p.add_argument("--label", required=True)
    p.add_argument("--files", nargs="*",
                   help="workspace-relative files (default: file artifacts)")

    p = sub.add_parser("restore")
    common(p)
    p.add_argument("--label", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "init":
        st = State.init(Path(args.workspace), args.board, args.phase,
                        args.force)
        return {"script": SCRIPT, "cmd": "init", "state": str(st.path),
                "board": args.board, "phase": args.phase,
                "subdirs": list(SUBDIRS)}, args.out

    state_path = _find_state(args)
    result: dict = {"script": SCRIPT, "cmd": args.cmd}

    if args.cmd == "resume":
        # the one read that may write: a workspace behind on its layout is
        # migrated first (statelib.migrate_workspace), under the writer lock
        st = State.load(state_path)
        _check_pin(st, args)
        migration: list[dict] | str = []
        behind = st.data.get("workspace_schema", 1) != \
            statelib.WORKSPACE_SCHEMA
        if behind and args.no_migrate:
            migration = "pending"
        elif behind:
            with safelib.writer_lock(state_path, what="state.json"):
                st = State.load(state_path)
                _check_pin(st, args)
                try:
                    migration = statelib.migrate_workspace(state_path.parent,
                                                           st.data)
                except ValueError as exc:
                    raise CheckError(str(exc)) from exc
                st._log("workspace_migrate",
                        to=statelib.WORKSPACE_SCHEMA, actions=migration)
                st.save()
        return {**result, **st.resume_summary(),
                "workspace_migration": migration,
                "digest": st.base_digest}, args.out

    if args.cmd in ("show", "freshness"):  # read-only: never saves
        st = State.load(state_path)
        _check_pin(st, args)
        if args.cmd == "show":
            return {**result, **st.data, "digest": st.base_digest}, args.out
        return {**result, **st.freshness()}, args.out

    # U12: one OS-exclusive hold across load -> mutate -> save, so two CLI
    # writers on one workspace serialize instead of losing an update
    # - and never on a state file that is not there: writer_lock would
    # mkdir a typo'd --workspace and leave a stray .lock behind
    if not state_path.is_file():
        raise CheckError(f"no state file at {state_path} (wrong --workspace/"
                         "--state? run `state.py init` for a new workspace)")
    with safelib.writer_lock(state_path, what="state.json"):
        st = State.load(state_path)
        _check_pin(st, args)
        return _mutate(st, args, result)


def _check_pin(st: "State", args) -> None:
    want = getattr(args, "if_digest", None)
    if want and want != st.base_digest:
        raise safelib.StaleWriteError(
            f"{st.path} digest {(st.base_digest or '')[:12]} != --if-digest "
            f"{want[:12]} - the state changed since that read; nothing "
            "written")


def _mutate(st: "State", args, result: dict):

    if args.cmd == "set-phase":
        warnings = st.set_phase(args.phase, require_gates=not args.force)
        result["phase"] = args.phase
        if warnings:
            result["warnings"] = warnings
    elif args.cmd == "record-gate":
        gres = checklib.load_json(args.result, "gate result")
        g = st.record_gate(args.gate, gres, args.phase)
        result.update(gate=args.gate, status=g["status"],
                      attempts=g["attempts"])
    elif args.cmd == "artifact":
        st.set_artifact(args.name, args.path)
        result.update(name=args.name, path=args.path,
                      sha256=st.data["artifacts"][args.name]["sha256"])
    elif args.cmd == "edit":
        rec = st.apply_edit(args.edit_class, args.refs, args.note)
        result.update(edit=rec)
    elif args.cmd == "rehash":
        result.update(artifacts=st.rehash(args.names))
    elif args.cmd == "spawn":
        rec = st.record_spawn({
            "role": args.role, "model": args.model, "effort": args.effort,
            "phase": args.phase, "tokens": args.tokens,
            "cost_usd": args.cost_usd, "note": args.note})
        result.update(spawn=rec)
    elif args.cmd == "cross-spawn":
        result.update(st.cross_spawn(args.stage, args.role, args.model,
                                     args.brief, args.kinds, args.effort))
    elif args.cmd == "mode":
        try:
            result.update(st.set_mode(args.token, args.stated))
        except modeslib.ModeError as exc:
            raise CheckError(str(exc)) from exc
    elif args.cmd == "decision":
        st.add_decision(args.what, args.why, args.phase)
        result.update(what=args.what)
    elif args.cmd == "human":
        st.record_human(args.checkpoint, args.status, args.note)
        result.update(checkpoint=args.checkpoint, status=args.status)
    elif args.cmd == "issue":
        rec = st.update_issue(args.id, args.status, args.agent,
                              args.bump_attempts)
        result.update(issue=rec)
    elif args.cmd == "budget":
        remaining = st.budget(args.bpath, args.consume)
        result.update(path=args.bpath, remaining=remaining)
    elif args.cmd == "log":
        if not EVENT_RE.fullmatch(args.event or ""):
            raise CheckError(
                f"bad event name {args.event!r}: event names are machine "
                "keys matching [a-z][a-z0-9_-]{0,31} - put prose in "
                '--data {"msg": ...}')
        extra = json.loads(args.data) if args.data else {}
        if not isinstance(extra, dict):
            raise CheckError("--data must be a JSON object")
        if args.event == "spawn":
            # SKILL step-5 form: the spawn ledger is first-class (XC-8);
            # record_spawn writes both the ledger entry and the history event
            result.update(spawn=st.record_spawn(extra))
        else:
            st._log(args.event, **extra)
        result.update(event=args.event)
    elif args.cmd == "snapshot":
        result.update(st.snapshot(args.label, args.files))
    elif args.cmd == "restore":
        result.update(st.restore(args.label))

    st.save()
    result["phase"] = st.data["phase"]
    result["digest"] = st.base_digest
    return result, args.out


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out = run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001  (spec 6: any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if payload.get("status") == "checkpoint" else 0


if __name__ == "__main__":
    raise SystemExit(main())
