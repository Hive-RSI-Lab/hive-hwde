#!/usr/bin/env python
"""research.py - the research verb's mechanical spine (U15, v3 design
decision 5a): a coverage GAP in, page-cited draft records + a promotion-queue
entry out, inside the caps, allowlist-enforced.

The agent halves are agents/researcher.md (acquire + synthesize) and
agents/research-second-reader.md (re-read the cited pages, refute or
verify); every checkable step in between is a subcommand here
(lib/researchlib.py owns the logic):

  open      --workspace WS --gaps <coverage report JSON> (--slot ID | --all)
            [--phase P2]
            One research task per gap: consumes budgets.research.per_run
            through state.py's ledger (cap hit = status "checkpoint", exit
            1, decision + event recorded - never silent), snapshots
            depth_per_gap into research/tasks/<id>.json and emits the
            researcher's BRIEF (gap, what the library already holds,
            policies, templates, exact commands, caps).
  brief     --workspace WS --task ID     re-emit a task's brief.
  fetch     --workspace WS --task ID --url URL --tier T [--about MPN]
            [--file LOCAL] [--expect pdf|html|any] [--note ..] [--part P]
            The ONLY way a source enters the workspace: https + allowlist
            (reference/knowledge/domains.yaml) checked before any bytes and
            on every redirect hop (off-list = exit 2, refused); quarantined
            to research/sources/, sha-pinned in the task ledger with its
            tier (vendor community hosts force tier forum). Depth cap =
            sources acquired per task -> checkpoint (exit 1). --file
            registers a locally held copy against an allowlisted origin
            URL (no network); --expect pdf refuses HTML shells (exit 1).
            A host that refuses (HTTP 401/403/429 or an Akamai/Cloudflare
            challenge page) is ledgered `blocked_by_host` with host + status
            (exit 2) - never evaded; the distributor's hosted datasheet for
            --part (or the part slot's LCSC/MPN) is then tried, and the
            source entry names it under `fallback`.
  verify    --workspace WS --task ID --record RID --verdict verified|refuted
            --note ".." [--by second-reader]
            The second reader's ruling: verified = maturity verified +
            status active + verification block; refuted = back to draft.
  validate  --workspace WS --task ID
            Schema v2 strict + the research contract: sources only from
            the task ledger with page + note, tier policy (forum never
            sole), envelope_note, maturity governance, slot keying, no id
            clash with the library, a draft checklist when the gap had
            none. exit 1 on problems.
  close     --workspace WS --task ID [--abandon --reason ..]
            validate clean + every record ruled -> appends the workspace
            LEARNINGS.md entry and compiles the promotion queue (U6); the
            slot then reads provisional on the next coverage run. A task
            that acquired nothing and was blocked by a host closes as
            `blocked` (a state decision names the hosts; coverage reports
            the slot under research_blocked; open --all skips it).
  promote   --workspace WS --record RID [--dry-run]
            Copies a VERIFIED record (or a draft checklist) + its sources
            into the library, rewrites the citation paths, re-lints the
            library (copy removed on failure). The owner's approval and the
            queue ruling (learnings.py resolve --kind knowledge_record)
            follow.
  status    --workspace WS [--task ID]  tasks, verdicts, caps remaining.
  parts     --mpn MPN [--provider digikey|mouser|all] [--limit N]
            Distributor parametric data + authoritative datasheet links
            (lib/distributors.py). Exits 2 naming the EXACT missing
            credential env vars until the owner registers keys.

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0 ok /
1 violations or checkpoint / 2 error, ASCII, no interactivity.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import knowledgelib  # noqa: E402
import researchlib  # noqa: E402
from checklib import utf8_stdout  # noqa: E402

SCRIPT = "research"


def _ws(args) -> tuple[Path | None, dict | None]:
    ws = Path(args.workspace)
    if not ws.is_dir():
        return None, {"script": SCRIPT, "status": "error",
                      "error": f"workspace not found: {ws}"}
    return ws, None


def _state(ws: Path, required: bool):
    """The workspace State (state.py, v2) or None. `required` -> a payload
    error when it is missing/unreadable."""
    import state as state_mod
    sp = ws / "state.json"
    if not sp.is_file():
        if required:
            raise RuntimeError(f"{sp.as_posix()} missing - research tasks "
                               "live in a governed workspace (state.py init)")
        return None
    try:
        return state_mod.State.load(sp)
    except Exception as exc:  # noqa: BLE001 - surface the state error as-is
        if required:
            raise RuntimeError(f"cannot load {sp.as_posix()}: {exc}") from exc
        return None


def _caps(st) -> dict:
    caps = dict(researchlib.DEFAULT_CAPS)
    if st is not None:
        try:
            caps["depth_per_gap"] = st.budget("research.depth_per_gap")
            caps["per_run"] = st.budget("research.per_run")
        except Exception:  # noqa: BLE001 - malformed budgets: defaults + note
            pass
    return caps


def _load_task(root: Path, tid: str) -> dict:
    return researchlib.load_task(root, tid)


# --------------------------------------------------------------------- open
def do_open(args) -> tuple[dict, int]:
    ws, err = _ws(args)
    if err:
        return err, 2
    if not args.slot and not args.all:
        return {"script": SCRIPT, "status": "error",
                "error": "open needs --slot <id> or --all"}, 2
    gp = Path(args.gaps)
    if not gp.is_file():
        return {"script": SCRIPT, "status": "error",
                "error": f"coverage report not found: {gp}"}, 2
    try:
        report = json.loads(gp.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"script": SCRIPT, "status": "error",
                "error": f"{gp} is not JSON: {exc}"}, 2
    gaps = report.get("gaps") if isinstance(report, dict) else None
    if not isinstance(gaps, list):
        return {"script": SCRIPT, "status": "error",
                "error": f"{gp} carries no gaps[] (is it a knowledge.py "
                         "--coverage report?)"}, 2
    st = _state(ws, required=True)
    phase = args.phase or report.get("phase") or "P1"
    if args.slot:
        wanted = [g for g in gaps if g.get("slot") == args.slot]
        if not wanted:
            return {"script": SCRIPT, "status": "error",
                    "error": f"slot {args.slot!r} is not a gap in {gp.name} "
                             f"(gaps: {[g.get('slot') for g in gaps]})"}, 2
    else:
        wanted = list(gaps)
    root = researchlib.root_of(ws)
    researchlib.ensure_layout(root)
    opened, skipped = [], []
    checkpoint = None
    lib_records = knowledgelib.load_records()
    lib_cls = knowledgelib.load_checklists()
    domains = researchlib.load_domains()
    for gap in wanted:
        if not args.slot and gap.get("research_blocked"):
            # the last pass on this slot was refused by the host: --all does
            # not burn the cap re-hitting it; --slot reopens it on purpose
            b = gap["research_blocked"]
            skipped.append({"slot": gap.get("slot"), "reason": (
                f"last research pass ({b.get('task')}) was blocked by host: "
                + ", ".join(h.get("host", "") for h in b.get("blocked_by")
                            or []) + " - reopen with --slot")})
            continue
        # per-run cap: consume through the state ledger (visible history)
        try:
            remaining = st.budget("research.per_run")
        except Exception as exc:  # noqa: BLE001
            return {"script": SCRIPT, "status": "error",
                    "error": f"budgets.research unreadable: {exc}"}, 2
        if remaining <= 0:
            left = [g.get("slot") for g in wanted
                    if g.get("slot") not in {o["task"]["slot"] for o in opened}
                    and g.get("slot") not in {s["slot"] for s in skipped}]
            checkpoint = {
                "checkpoint": "research_cap", "detail":
                    "budgets.research.per_run exhausted - research launches "
                    "automatically on every gap (owner ruling) and this run "
                    "has used its cap",
                "unopened_slots": left,
                "action": ("present at the next human checkpoint: raise "
                           "budgets.research.per_run (state.json) and re-run "
                           "open, or accept designing under the remaining "
                           "gaps (state.py decision, per slot)")}
            st.add_decision(
                what=f"research cap hit: {len(left)} gap slot(s) unresearched",
                why="budgets.research.per_run exhausted; visible checkpoint, "
                    "not silent truncation", phase=phase)
            st._log("research_checkpoint", checkpoint="research_cap",
                    slots=left)
            st.save()
            break
        try:
            task = researchlib.open_task_for_slot(
                root, gap, phase, _caps(st), coverage_report=gp.as_posix())
        except ValueError as exc:
            skipped.append({"slot": gap.get("slot"), "reason": str(exc)})
            continue
        st.budget("research.per_run", consume=True)
        researchlib.write_task(root, task)
        st._log("research_opened", task=task["id"], slot=task["slot"],
                phase=phase)
        st.save()
        opened.append({"task": task,
                       "brief": researchlib.brief(task, ws, lib_records,
                                                  lib_cls, domains)})
    payload = {"script": SCRIPT, "status": "checkpoint" if checkpoint else
               ("pass" if opened else "violations"),
               "workspace": ws.as_posix(), "phase": phase,
               "opened": [o["task"]["id"] for o in opened],
               "skipped": skipped, "tasks": opened,
               "caps": _caps(st)}
    if checkpoint:
        payload.update(checkpoint)
        return payload, 1
    if not opened:
        payload["error"] = "no task opened (see skipped)"
        return payload, 1
    return payload, 0


def do_brief(args) -> tuple[dict, int]:
    ws, err = _ws(args)
    if err:
        return err, 2
    root = researchlib.root_of(ws)
    task = _load_task(root, args.task)
    return {"script": SCRIPT, "status": "pass", "task": task,
            "brief": researchlib.brief(task, ws)}, 0


# -------------------------------------------------------------------- fetch
def do_fetch(args) -> tuple[dict, int]:
    ws, err = _ws(args)
    if err:
        return err, 2
    root = researchlib.root_of(ws)
    task = _load_task(root, args.task)
    payload, code = researchlib.fetch_source(
        root, task, args.url, args.tier, about=args.about,
        expect=args.expect, local_file=args.file, note=args.note,
        part=args.part)
    payload = {"script": SCRIPT, **payload}
    if payload.get("status") == "checkpoint":
        st = _state(ws, required=False)
        if st is not None:
            st.add_decision(
                what=f"research depth cap hit on {task['id']} ({task['slot']})",
                why=payload.get("detail", ""), phase=task.get("phase"))
            st._log("research_checkpoint", checkpoint=payload["checkpoint"],
                    task=task["id"])
            st.save()
    return payload, code


# ------------------------------------------------------------------- verify
def do_verify(args) -> tuple[dict, int]:
    ws, err = _ws(args)
    if err:
        return err, 2
    root = researchlib.root_of(ws)
    task = _load_task(root, args.task)
    payload, code = researchlib.verify_record(
        root, task, args.record, args.verdict, by=args.by, note=args.note)
    return {"script": SCRIPT, **payload}, code


# ----------------------------------------------------------------- validate
def do_validate(args) -> tuple[dict, int]:
    ws, err = _ws(args)
    if err:
        return err, 2
    root = researchlib.root_of(ws)
    task = _load_task(root, args.task)
    rep = researchlib.validate_task(root, task)
    ok = not rep["problems"]
    return {"script": SCRIPT, "status": "pass" if ok else "problems",
            "task": task["id"], "slot": task["slot"], **rep}, 0 if ok else 1


# -------------------------------------------------------------------- close
def do_close(args) -> tuple[dict, int]:
    ws, err = _ws(args)
    if err:
        return err, 2
    root = researchlib.root_of(ws)
    task = _load_task(root, args.task)
    payload, code = researchlib.close_task(root, task, abandon=args.abandon,
                                          reason=args.reason,
                                          accept_drafts=args.accept_drafts)
    payload = {"script": SCRIPT, **payload}
    if code == 0:
        st = _state(ws, required=False)
        if st is not None:
            st._log("research_closed", task=task["id"], slot=task["slot"],
                    outcome=payload.get("outcome"),
                    queue_entry=payload.get("queue_entry"))
            if payload.get("outcome") == "abandoned":
                st.add_decision(what=f"research task {task['id']} abandoned",
                                why=args.reason or "", phase=task.get("phase"))
            elif payload.get("outcome") == "blocked":
                hosts = ", ".join(b["host"] for b in payload["blocked_by"])
                st.add_decision(
                    what=(f"research on {task['slot']} blocked by host "
                          f"({hosts}) - the coverage gap stands, unresearched"),
                    why=payload.get("reason") or "", phase=task.get("phase"))
                st._log("research_blocked", task=task["id"],
                        slot=task["slot"], blocked_by=payload["blocked_by"])
            drafts = payload.get("accepted_drafts") or []
            if drafts:
                # U21: closing over unverified research is a RULING, never a
                # side effect - it must read back off state.json.
                st.add_decision(
                    what=(f"research task {task['id']} closed over "
                          f"{len(drafts)} unverified draft record(s): "
                          + ", ".join(drafts)),
                    why=(args.reason or "--accept-drafts: the slot ships "
                         "without this knowledge"),
                    phase=task.get("phase"))
                st._log("research_drafts_accepted", task=task["id"],
                        slot=task["slot"], records=drafts)
            st.save()
    return payload, code


# ------------------------------------------------------------------ promote
def do_promote(args) -> tuple[dict, int]:
    ws, err = _ws(args)
    if err:
        return err, 2
    root = researchlib.root_of(ws)
    payload, code = researchlib.promote_record(
        root, args.record, lib_records_dir=args.records_dir,
        lib_sources_dir=args.sources_dir, lib_checklists_dir=args.checklists_dir,
        dry_run=args.dry_run)
    return {"script": SCRIPT, **payload}, code


# ------------------------------------------------------------------- status
def do_status(args) -> tuple[dict, int]:
    ws, err = _ws(args)
    if err:
        return err, 2
    root = researchlib.root_of(ws)
    st = _state(ws, required=False)
    tasks = researchlib.list_tasks(root)
    if args.task:
        tasks = [t for t in tasks if t["id"] == args.task]
        if not tasks:
            return {"script": SCRIPT, "status": "error",
                    "error": f"no task {args.task!r}"}, 2
    rows = []
    for t in tasks:
        rows.append({"id": t["id"], "slot": t["slot"], "status": t["status"],
                     "outcome": t.get("outcome"), "phase": t.get("phase"),
                     "sources": len(t.get("sources") or []),
                     "attempts": len(t.get("attempts") or []),
                     "depth": researchlib.depth_state(t),
                     "counts": researchlib.task_record_counts(root, t),
                     "verdicts": t.get("verdicts") or {},
                     "accepted_drafts": t.get("accepted_drafts"),
                     "blocked_by": (t.get("blocked_by")
                                    or researchlib.blocked_hosts(t) or None),
                     "queue_entry": t.get("queue_entry")})
    # U21: the workspace-wide sweep - a draft hidden behind a CLOSED task is
    # the failure the per-task counts above cannot see.
    sweep = researchlib.draft_sweep(ws)
    payload = {"script": SCRIPT,
               "status": "violations" if sweep["stalled"] else "pass",
               "workspace": ws.as_posix(), "caps": _caps(st), "tasks": rows,
               "draft_unverified": sweep["drafts"],
               "stalled": sweep["stalled"], "draft_counts": sweep["counts"]}
    if sweep["stalled"]:
        payload["error"] = (
            f"{len(sweep['stalled'])} research record(s) are still draft "
            "behind a closed task - unverified research never injects, so "
            "this design does not hold that knowledge: "
            + ", ".join(r["id"] for r in sweep["stalled"]))
    return payload, 1 if sweep["stalled"] else 0


# -------------------------------------------------------------------- parts
def do_parts(args) -> tuple[dict, int]:
    import distributors
    provs = (list(distributors.PROVIDERS) if args.provider in (None, "all")
             else [args.provider])
    missing = {p: distributors.credential_message(p) for p in provs}
    if all(missing.values()):
        return {"script": SCRIPT, "status": "error", "mpn": args.mpn,
                "error": "distributor credentials missing: "
                         + "; ".join(m for m in missing.values() if m),
                "missing": {p: distributors.missing_credentials(p)
                            for p in provs},
                "register": {p: distributors.REGISTER[p] for p in provs}}, 2
    try:
        res = distributors.lookup(args.mpn, providers=provs, limit=args.limit)
    except distributors.DistributorError as exc:
        return {"script": SCRIPT, "status": "error", "mpn": args.mpn,
                "error": str(exc)}, 2
    any_ok = any(r.get("status") == "pass" for r in res["results"].values())
    return {"script": SCRIPT, "status": "pass" if any_ok else "violations",
            **res}, 0 if any_ok else 1


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, ws=True):
        p.add_argument("--out", help="write the JSON here instead of stdout")
        if ws:
            p.add_argument("--workspace", required=True)
        return p

    p = common(sub.add_parser("open", help="open research task(s) from a "
                                           "coverage report's gaps"))
    p.add_argument("--gaps", required=True,
                   help="knowledge.py --coverage report JSON")
    p.add_argument("--slot", help="one gap slot id (block:B3, interface:usb, "
                                  "part:C123)")
    p.add_argument("--all", action="store_true", help="every gap in the report")
    p.add_argument("--phase", help="P1/P2/P3 label (default: the report's)")

    p = common(sub.add_parser("brief", help="re-emit a task's researcher brief"))
    p.add_argument("--task", required=True)

    p = common(sub.add_parser("fetch", help="acquire ONE source into quarantine"))
    p.add_argument("--task", required=True)
    p.add_argument("--url", required=True, help="https URL on the allowlist")
    p.add_argument("--tier", required=True, choices=researchlib.TIERS)
    p.add_argument("--about", help="the MPN/vendor the source is about")
    p.add_argument("--file", help="register this local file instead of "
                                  "downloading (origin URL still checked)")
    p.add_argument("--expect", default="pdf", choices=researchlib.EXPECTS)
    p.add_argument("--note", help="ledger note (why this source)")
    p.add_argument("--part", help="LCSC number or MPN: when the host refuses "
                                  "(blocked_by_host), try the distributor's "
                                  "hosted datasheet for this part")

    p = common(sub.add_parser("verify", help="the second reader's verdict"))
    p.add_argument("--task", required=True)
    p.add_argument("--record", required=True)
    p.add_argument("--verdict", required=True, choices=researchlib.VERDICTS)
    p.add_argument("--note", required=True,
                   help="what was re-read and found (>= 12 chars, ASCII)")
    p.add_argument("--by", default=researchlib.SECOND_READER)

    p = common(sub.add_parser("validate", help="lint a task's outputs"))
    p.add_argument("--task", required=True)

    p = common(sub.add_parser("close", help="close a task -> queue entry"))
    p.add_argument("--accept-drafts", action="store_true",
                   help="close even though records are still draft "
                        "(unverified research never injects) - names them in "
                        "a state decision")
    p.add_argument("--task", required=True)
    p.add_argument("--abandon", action="store_true",
                   help="close without outputs (needs --reason)")
    p.add_argument("--reason")

    p = common(sub.add_parser("promote", help="verified record -> library"))
    p.add_argument("--record", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--records-dir", help="override the library records dir")
    p.add_argument("--sources-dir", help="override the library sources dir")
    p.add_argument("--checklists-dir", help="override the library checklists dir")

    p = common(sub.add_parser("status", help="tasks + caps"))
    p.add_argument("--task")

    p = common(sub.add_parser("parts", help="distributor parametric lookup"),
               ws=False)
    p.add_argument("--mpn", required=True)
    p.add_argument("--provider", choices=["digikey", "mouser", "all"])
    p.add_argument("--limit", type=int, default=5)

    args = ap.parse_args(argv)
    handler = {"open": do_open, "brief": do_brief, "fetch": do_fetch,
               "verify": do_verify, "validate": do_validate,
               "close": do_close, "promote": do_promote, "status": do_status,
               "parts": do_parts}[args.cmd]
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
