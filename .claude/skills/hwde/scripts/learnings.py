#!/usr/bin/env python
"""learnings.py - workspace learnings -> promotion queue -> the ladder (U6).

The run-close and promotion halves of v3 design decision 3. Subcommands:

  init      write the standard workspace LEARNINGS.md skeleton (never
            overwrites an existing file).
  compile   parse <workspace>/LEARNINGS.md and merge its entries into
            <workspace>/learnings/queue.yaml. Idempotent: rulings survive,
            source drift is refreshed, new entries land `pending`. THIS is the
            run-close step every recipe ends with. exit 1 on a malformed
            heading or an orphaned queue id.
  queue     read the queue back: counts plus the rows, filterable with
            --status. exit 0 (a pending backlog is a fact, not a failure).
  validate  lint the queue: schema, ids that still exist, targets/artifacts
            that exist, rulings that carry a kind + reason. exit 1 on problems.
  resolve   rule on entries - one with --entry/--status/--kind/--reason, or a
            whole pass with --batch FILE. A `root_learnings` promotion also
            performs the move: the entry is written verbatim to a new lesson
            file, learnings.d/<id>.md, whose last line is its triage row.
  sweep     every workspace's queue state (the general-agent operator mode).
  triage    the knowledge-ladder counts, computed from design/ladder-triage.md's
            archive table plus every learnings.d/ lesson's triage line (the
            register keeps no count by hand). exit 1 when a lesson file is
            malformed.

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2,
ASCII, no interactivity.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import yaml  # noqa: E402

import env  # noqa: E402
import learnlib  # noqa: E402
from checklib import utf8_stdout  # noqa: E402

SCRIPT = "learnings"


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def do_init(args) -> tuple[dict, int]:
    ws = Path(args.workspace)
    p = learnlib.learnings_path(ws)
    if p.exists():
        return {"script": SCRIPT, "status": "pass", "written": None,
                "note": f"{p.as_posix()} already exists - left alone"}, 0
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(learnlib.SKELETON.format(
        board=args.board or ws.name, what=args.what or "one line on the board",
        ws=ws.as_posix(), today=learnlib.today()),
        encoding="utf-8", newline="\n")
    return {"script": SCRIPT, "status": "pass", "written": p.as_posix()}, 0


def do_compile(args) -> tuple[dict, int]:
    ws = Path(args.workspace)
    queue, report = learnlib.compile_queue(ws, args.board)
    # U21: run close is the last moment anyone looks. Research that never
    # cleared the second reader never injects, so a board can close having
    # been designed WITHOUT its hardest knowledge (bb-amp: six input-stage
    # rules) and nothing says so. Sweep here, name it, and rule on it.
    drafts = _research_drafts(ws)
    if drafts["counts"]["drafts"]:
        queue["research_drafts"] = drafts["counts"]
        queue["research_draft_records"] = [
            {"id": r["id"], "task": r["task"], "state": r["state"],
             "stalled": r["stalled"]} for r in drafts["drafts"]]
    path = learnlib.save_queue(ws, queue)
    bad = bool(report["malformed"] or report["orphans"])
    stalled = drafts["stalled"]
    if stalled:
        _record_draft_decision(ws, stalled)
    payload = {"script": SCRIPT,
               "status": "problems" if (bad or stalled) else "pass",
               "queue": path.as_posix(), **report,
               "research_drafts": drafts["counts"],
               "draft_unverified": drafts["drafts"]}
    if stalled:
        payload["error"] = (
            f"{len(stalled)} research record(s) never verified - the design "
            "does not hold that knowledge: "
            + ", ".join(r["id"] for r in stalled))
    return payload, 1 if (bad or stalled) else 0


def _research_drafts(ws: Path) -> dict:
    """The workspace's unverified research (empty shape when it has none)."""
    try:
        import researchlib
    except ImportError:
        researchlib = None
    if researchlib is None or not researchlib.root_of(ws).is_dir():
        return {"drafts": [], "stalled": [],
                "counts": {"drafts": 0, "stalled": 0, "unruled": 0,
                           "refuted": 0, "orphaned": 0}}
    return researchlib.draft_sweep(ws)


def _record_draft_decision(ws: Path, stalled: list) -> None:
    """One state decision naming every stalled record - so the miss reads
    back off state.json long after the run, not just off a payload."""
    try:
        import state as state_mod
    except ImportError:
        return
    sp = Path(ws) / "state.json"
    if not sp.is_file():
        return
    try:
        st = state_mod.State.load(sp)
    except Exception:
        return
    ids = ", ".join(r["id"] for r in stalled)
    if any(d.get("what", "").startswith("run closed over ") and ids in
           d.get("what", "") for d in (st.data.get("decisions") or [])):
        return          # idempotent: compile runs more than once
    st.add_decision(
        what=(f"run closed over {len(stalled)} unverified research record(s): "
              f"{ids}"),
        why=("the second reader never verified them, so they never injected - "
             "this board was designed without that knowledge"),
        phase=st.data.get("phase"))
    st._log("research_drafts_unverified",
            records=[r["id"] for r in stalled],
            tasks=sorted({r["task"] for r in stalled}))
    st.save()


def do_queue(args) -> tuple[dict, int]:
    ws = Path(args.workspace)
    queue = learnlib.load_queue(ws)
    if queue is None:
        return {"script": SCRIPT, "status": "error",
                "error": f"no queue at {learnlib.queue_path(ws).as_posix()} "
                         "- run `learnings.py compile` first"}, 2
    rows = queue["entries"]
    counts = {s: sum(1 for r in rows if r["status"] == s)
              for s in learnlib.STATUSES}
    if args.status:
        rows = [r for r in rows if r["status"] == args.status]
    if args.stage:
        # the stage-learner operator mode: only the entries this stage owns
        rows = [r for r in rows if r["stage"] == args.stage]
    return {"script": SCRIPT, "status": "pass", "board": queue["board"],
            "counts": counts, "shown": len(rows), "entries": rows}, 0


def _boards_dir(args) -> Path:
    return Path(args.boards_dir) if args.boards_dir else env.boards_root()


def do_validate(args) -> tuple[dict, int]:
    boards = ([Path(args.workspace)] if args.workspace else
              [Path(w["workspace"]) for w in learnlib.sweep(_boards_dir(args))
               if learnlib.queue_path(Path(w["workspace"])).is_file()])
    problems: list[str] = []
    warnings: list[str] = []
    for ws in boards:
        p, w = learnlib.validate_queue(ws)
        problems += [f"{ws.name}: {x}" for x in p]
        warnings += [f"{ws.name}: {x}" for x in w]
    return ({"script": SCRIPT,
             "status": "pass" if not problems else "problems",
             "workspaces": [b.name for b in boards],
             "problems": problems, "warnings": warnings},
            1 if problems else 0)


def _rulings(args) -> list[dict]:
    if args.batch:
        data = yaml.safe_load(Path(args.batch).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("rulings")
        if not isinstance(data, list):
            raise ValueError(f"{args.batch}: no `rulings` list")
        return data
    if not (args.entry and args.status and args.kind):
        raise ValueError("resolve needs --batch, or --entry/--status/--kind")
    # --targets feeds resolution.artifacts (apply_ruling's key); shipping it
    # under "targets" silently dropped the list and every single-entry
    # promotion failed validate with "no artifacts" (U7 dry-run find).
    ruling = {"entry": args.entry, "status": args.status, "kind": args.kind,
              "reason": args.reason, "level": args.level,
              "artifacts": _csv(args.targets) or None}
    if args.kind == "root_learnings":
        ruling["triage"] = {"now": args.now_level, "target": args.target_level,
                            "owner": args.owner, "status": args.triage_status,
                            "note": args.note or ""}
    return [ruling]


def do_resolve(args) -> tuple[dict, int]:
    ws = Path(args.workspace)
    queue = learnlib.load_queue(ws)
    if queue is None:
        return {"script": SCRIPT, "status": "error",
                "error": f"no queue at {learnlib.queue_path(ws).as_posix()}"}, 2
    results = [learnlib.apply_ruling(queue, r, ws) for r in _rulings(args)]
    applied = [r for r in results if r["applied"]]
    if applied:
        learnlib.save_queue(ws, queue)
    counts = {s: sum(1 for r in queue["entries"] if r["status"] == s)
              for s in learnlib.STATUSES}
    return ({"script": SCRIPT,
             "status": "pass" if len(applied) == len(results) else "problems",
             "applied": len(applied), "requested": len(results),
             "counts": counts, "results": results},
            0 if applied and len(applied) == len(results) else 1)


def do_sweep(args) -> tuple[dict, int]:
    rows = learnlib.sweep(_boards_dir(args))
    return {"script": SCRIPT, "status": "pass", "workspaces": len(rows),
            "pending_total": sum(r["pending"] for r in rows),
            "uncompiled_total": sum(r["uncompiled"] for r in rows),
            "boards": rows}, 0


def do_triage(args) -> tuple[dict, int]:
    summary = learnlib.triage_summary()
    bad = bool(summary["problems"])
    return {"script": SCRIPT, "status": "fail" if bad else "pass",
            **summary}, 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        """--out belongs to every subcommand (state.py's shape) - a top-level
        one would be clobbered by the subparser's default."""
        p.add_argument("--out", help="write the JSON here instead of stdout")
        return p

    p = common(sub.add_parser("init",
                              help="write the workspace LEARNINGS skeleton"))
    p.add_argument("--workspace", required=True)
    p.add_argument("--board")
    p.add_argument("--what", help="one line on what the board is")

    p = common(sub.add_parser("compile", help="entries -> learnings/queue.yaml"))
    p.add_argument("--workspace", required=True)
    p.add_argument("--board")

    p = common(sub.add_parser("queue", help="read the queue back"))
    p.add_argument("--workspace", required=True)
    p.add_argument("--status", choices=learnlib.STATUSES)
    p.add_argument("--stage", help="only entries tagged with this stage (P0-P10)")

    p = common(sub.add_parser("validate", help="lint the queue"))
    p.add_argument("--workspace")
    p.add_argument("--boards-dir", default=None,
                   help="dir of board workspaces (default: the boards "
                        "root, HWDE_BOARDS_ROOT or ~/dev/boards)")

    p = common(sub.add_parser("resolve", help="promote or decline entries"))
    p.add_argument("--workspace", required=True)
    p.add_argument("--batch", help="YAML/JSON file of rulings")
    p.add_argument("--entry")
    p.add_argument("--status", choices=learnlib.STATUSES[1:])
    p.add_argument("--kind", choices=[*learnlib.PROMOTE_KINDS,
                                      *learnlib.DECLINE_KINDS])
    p.add_argument("--reason", default="")
    p.add_argument("--level", choices=learnlib.LEVELS)
    p.add_argument("--targets", help="comma-separated artifact paths")
    p.add_argument("--owner", help="root_learnings: triage owner artifact")
    p.add_argument("--now-level", choices=learnlib.LEVELS)
    p.add_argument("--target-level", choices=learnlib.LEVELS)
    p.add_argument("--triage-status", default="open")
    p.add_argument("--note", help="root_learnings: triage note")

    p = common(sub.add_parser("sweep", help="every workspace's queue state"))
    p.add_argument("--boards-dir", default=None,
                   help="dir of board workspaces (default: the boards "
                        "root, HWDE_BOARDS_ROOT or ~/dev/boards)")

    common(sub.add_parser("triage",
                          help="print the knowledge-ladder counts"))

    args = ap.parse_args(argv)
    handler = {"init": do_init, "compile": do_compile, "queue": do_queue,
               "validate": do_validate, "resolve": do_resolve,
               "sweep": do_sweep, "triage": do_triage}[args.cmd]
    try:
        payload, code = handler(args)
    except Exception as exc:  # noqa: BLE001 - contract: any error -> exit 2
        payload, code = {"script": SCRIPT, "status": "error",
                         "error": f"{type(exc).__name__}: {exc}"}, 2

    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
