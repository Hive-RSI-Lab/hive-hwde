#!/usr/bin/env python
"""knowledge.py - class-indexed knowledge library: validate / select / render /
coverage / prove (U4 + U13).

The retrieval half of v3 design decision 2 and the coverage contract of
decision 5a. Records live in reference/knowledge/records/*.yaml (shape:
knowledgelib.RECORD_SCHEMA), coverage checklists in
reference/knowledge/checklists/*.yaml (CHECKLIST_SCHEMA); retrieval and
coverage are TRIGGERED / structural, never self-assessed:

  --validate            lint every record + checklist (schema, id/filename,
                        controlled classes, source files exist, named
                        scripts/flags exist, schema-v2 level/envelope grammar,
                        generalizes targets, maturity governance: approved
                        needs an approval block, proven needs evidence).
                        exit 0 clean / 1 problems.
      --strict          post-backfill mode: level + maturity REQUIRED on
                        every record (bootstrap tolerates their absence as
                        level None / maturity draft).
  --select              records matching a workspace's declared keys and/or
                        explicit --blocks/--packages/--interfaces/--parts.
                        Emits {keys, count, records, prompt_block}; prompt_block
                        is what the orchestrator pastes into a P3/P6/P7 spawn
                        prompt (empty = inject nothing). exit 0 even when
                        empty - no match is a fact, not a failure.
      --workspace WS    derive keys: constraints.json blocks[].topology
                        (P2's block list) + diff_pairs[].base, parts.json
                        parts[].package + mpn/lcsc (P3's parts). The
                        workspace's own research records (<ws>/research/
                        records, U15) join the pool - only VERIFIED ones
                        (status active) can inject; drafts never do.
      --blocks a,b      explicit topology keys (union with workspace keys)
      --packages a,b    explicit package keys
      --interfaces a,b  explicit interface keys
      --parts a,b       explicit part keys (mpn / lcsc)
  --coverage            the "I know enough to design this" report for a
                        workspace (--workspace required): per block /
                        interface / part slot -> covered | provisional | gap,
                        per required class the records considered and the
                        ONE blocker each; `gaps` = research task specs;
                        `mapping_request` = the coverage-mapper agent's input
                        when unmet classes remain. exit 0 = no gap slots,
                        1 = gaps present (the research trigger: research.py
                        open --gaps <this report>), 2 = cannot run (bad
                        mapping / no workspace). Workspace research records
                        + checklists (U15) are folded in, so a researched
                        but unapproved class reads provisional, not gap.
      --maturity-floor  draft|verified|approved|proven (default approved =
                        bootstrap mode: only owner-approved or bench-proven
                        records satisfy coverage)
      --phase P2        label recorded in the report (P2 exit / P3 exit)
      --mapping FILE    the coverage-mapper's schema-forced output
                        (MAPPING_SCHEMA); validated against this run's
                        records + slots + record classes, refused whole on
                        any problem (exit 2). Its sha256 lands in the report.
  --prove               T11 wiring: after `state.py log --event
                        bringup_passed`, upgrade every record that APPLIED to
                        the workspace (deterministic keys + envelope) to
                        maturity `proven` and append the bring-up evidence
                        entry - reality outranks review. --dry-run reports
                        the plan. exit 0 = done (or nothing to do), 1 = no
                        bring-up evidence in the workspace / write refused.
  --list                every record: id, status, level, maturity, classes,
                        applies; plus the checklists.
  --render-topology T   regenerate reference/topologies/<T>.md from the
                        records (--out required); prints a JSON summary.
                        The committed view is test-pinned to this render.

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2,
ASCII, no interactivity. --records-dir / --checklists-dir override the
library roots (tests).
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
from checklib import utf8_stdout  # noqa: E402

SCRIPT = "knowledge"


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def do_validate(args) -> tuple[dict, int]:
    problems = knowledgelib.validate(args.records_dir, args.checklists_dir,
                                     strict=args.strict)
    n = len(knowledgelib.record_files(args.records_dir))
    nc = len(knowledgelib.checklist_files(args.checklists_dir))
    payload = {"script": SCRIPT, "status": "pass" if not problems else "problems",
               "records": n, "checklists": nc, "strict": bool(args.strict),
               "schema_version": knowledgelib.SCHEMA_VERSION,
               "problems": problems}
    return payload, 0 if not problems else 1


def do_select(args) -> tuple[dict, int]:
    keys = {"topologies": [], "packages": [], "interfaces": [], "parts": [],
            "sources": {}}
    if args.workspace:
        ws = Path(args.workspace)
        if not ws.is_dir():
            return {"script": SCRIPT, "status": "error",
                    "error": f"workspace not found: {ws}"}, 2
        keys = knowledgelib.workspace_keys(ws)
    keys["topologies"] = sorted(set(keys["topologies"]) | {
        knowledgelib.norm_token(t) for t in _csv(args.blocks)})
    keys["packages"] = sorted(set(keys["packages"]) | set(_csv(args.packages)))
    keys["interfaces"] = sorted(set(keys["interfaces"]) | {
        knowledgelib.norm_token(i) for i in _csv(args.interfaces)})
    keys["parts"] = sorted(set(keys.get("parts") or []) | set(_csv(args.parts)))

    records = knowledgelib.load_records(args.records_dir)
    ws_recs: list[dict] = []
    if args.workspace:
        # U15: the workspace's VERIFIED research records inject too (select
        # keeps its status==active rule, so unverified drafts never do)
        ws_recs = knowledgelib.workspace_records(Path(args.workspace))
        records = knowledgelib.merge_workspace(records, ws_recs)
    hits = knowledgelib.select(records, keys["topologies"], keys["packages"],
                               keys["interfaces"], keys["parts"])
    payload = {
        "script": SCRIPT, "status": "pass", "keys": keys, "count": len(hits),
        "records": [{k: r.get(k) for k in
                     ("id", "classes", "applies", "rule", "prose", "sources",
                      "origin", "level", "maturity", "envelope", "_path",
                      "_workspace")}
                    for r in hits],
        "workspace_records": sorted(r.get("id") or "" for r in ws_recs),
        "prompt_block": knowledgelib.prompt_block(hits, keys),
    }
    return payload, 0


def do_coverage(args) -> tuple[dict, int]:
    if not args.workspace:
        return {"script": SCRIPT, "status": "error",
                "error": "--coverage requires --workspace"}, 2
    ws = Path(args.workspace)
    if not ws.is_dir():
        return {"script": SCRIPT, "status": "error",
                "error": f"workspace not found: {ws}"}, 2
    floor = args.maturity_floor or knowledgelib.DEFAULT_MATURITY_FLOOR
    if floor not in knowledgelib.MATURITY_RANK:
        return {"script": SCRIPT, "status": "error",
                "error": f"bad --maturity-floor {floor!r} (one of "
                         f"{', '.join(knowledgelib.MATURITIES)})"}, 2
    mapping = None
    mapping_file = None
    if args.mapping:
        mp = Path(args.mapping)
        if not mp.is_file():
            return {"script": SCRIPT, "status": "error",
                    "error": f"mapping file not found: {mp}"}, 2
        try:
            mapping = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError as exc:
            return {"script": SCRIPT, "status": "error",
                    "error": f"mapping file is not JSON: {exc}"}, 2
        mapping_file = mp.as_posix()
    records = knowledgelib.load_records(args.records_dir)
    checklists = knowledgelib.load_checklists(args.checklists_dir)
    try:
        rep = knowledgelib.coverage(ws, records, checklists, mapping=mapping,
                                    floor=floor, phase=args.phase,
                                    mapping_file=mapping_file,
                                    escalate_provisional=args.research_provisional)
    except ValueError as exc:      # invalid mapping content = cannot run
        return {"script": SCRIPT, "status": "error", "error": str(exc)}, 2
    if mapping_file:
        rep["mapping_applied"]["sha256"] = knowledgelib.sha256_file(mapping_file)
    gaps = rep["summary"]["gap"]
    payload = {"script": SCRIPT, "status": "gaps" if gaps else "pass", **rep}
    return payload, 1 if gaps else 0


def do_prove(args) -> tuple[dict, int]:
    if not args.workspace:
        return {"script": SCRIPT, "status": "error",
                "error": "--prove requires --workspace"}, 2
    ws = Path(args.workspace)
    if not ws.is_dir():
        return {"script": SCRIPT, "status": "error",
                "error": f"workspace not found: {ws}"}, 2
    res = knowledgelib.prove(ws, args.records_dir, args.checklists_dir,
                             dry_run=args.dry_run)
    ok = not res["problems"]
    payload = {"script": SCRIPT,
               "status": "pass" if ok else "problems", **res}
    return payload, 0 if ok else 1


def do_list(args) -> tuple[dict, int]:
    records = knowledgelib.load_records(args.records_dir)
    checklists = knowledgelib.load_checklists(args.checklists_dir)
    payload = {"script": SCRIPT, "status": "pass", "count": len(records),
               "records": [{"id": r.get("id"), "status": r.get("status"),
                            "level": knowledgelib.record_level(r),
                            "maturity": knowledgelib.record_maturity(r),
                            "classes": r.get("classes"),
                            "applies": r.get("applies")} for r in records],
               "checklists": [{"id": c.get("id"),
                               "maturity": knowledgelib.record_maturity(c),
                               "applies": c.get("applies"),
                               "requires": c.get("requires")}
                              for c in checklists]}
    return payload, 0


def do_render(args) -> tuple[dict, int]:
    if not args.out:
        return {"script": SCRIPT, "status": "error",
                "error": "--render-topology requires --out (the view path)"}, 2
    records = knowledgelib.load_records(args.records_dir)
    text = knowledgelib.render_topology(records, args.render_topology)
    used = text.count("\n## ")
    if not used:
        return {"script": SCRIPT, "status": "error",
                "error": f"no active records apply to topology "
                         f"{args.render_topology!r} - refusing an empty view"}, 2
    Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    return {"script": SCRIPT, "status": "pass", "written": args.out,
            "records_used": used}, 0


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", action="store_true")
    mode.add_argument("--select", action="store_true")
    mode.add_argument("--coverage", action="store_true")
    mode.add_argument("--prove", action="store_true")
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--render-topology", metavar="TOPOLOGY")
    ap.add_argument("--workspace", help="--select/--coverage/--prove: the "
                                        "workspace")
    ap.add_argument("--blocks", help="--select: explicit topology keys, comma-sep")
    ap.add_argument("--packages", help="--select: explicit package keys, comma-sep")
    ap.add_argument("--interfaces", help="--select: explicit interface keys, comma-sep")
    ap.add_argument("--parts", help="--select: explicit part keys (mpn/lcsc), "
                                    "comma-sep")
    ap.add_argument("--strict", action="store_true",
                    help="--validate: require level + maturity on every record")
    ap.add_argument("--maturity-floor", metavar="M",
                    help="--coverage: lowest maturity that satisfies "
                         f"(default {knowledgelib.DEFAULT_MATURITY_FLOOR})")
    ap.add_argument("--research-provisional", action="store_true",
                    help="--coverage: count provisional slots as gaps so they "
                         "open research tasks (seeding runs - any build-modes "
                         "learning target; use on a phase's FIRST coverage "
                         "call only, never on the post-research re-run)")
    ap.add_argument("--phase", help="--coverage: label (P2 / P3) recorded in "
                                    "the report")
    ap.add_argument("--mapping", help="--coverage: coverage-mapper output JSON "
                                      "(MAPPING_SCHEMA) to fold in")
    ap.add_argument("--dry-run", action="store_true",
                    help="--prove: report the upgrades without writing")
    ap.add_argument("--records-dir", help="override the records dir (tests)")
    ap.add_argument("--checklists-dir", help="override the checklists dir (tests)")
    ap.add_argument("--out", help="write JSON here (or the view for "
                                  "--render-topology; then the JSON summary "
                                  "goes to stdout)")
    args = ap.parse_args(argv)

    try:
        if args.validate:
            payload, code = do_validate(args)
        elif args.select:
            payload, code = do_select(args)
        elif args.coverage:
            payload, code = do_coverage(args)
        elif args.prove:
            payload, code = do_prove(args)
        elif args.list:
            payload, code = do_list(args)
        else:
            payload, code = do_render(args)
    except Exception as exc:  # noqa: BLE001 - contract: any error -> exit 2
        payload, code = {"script": SCRIPT, "status": "error",
                         "error": f"{type(exc).__name__}: {exc}"}, 2

    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if args.out and not args.render_topology:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
