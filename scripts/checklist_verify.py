#!/usr/bin/env python
"""checklist_verify.py - a coverage checklist counts on an independent
agent's ruling, graded against planted faults, not only on the person's
sign-off (lib/checklistverify.py owns the logic).

  packet  --checklist ID|PATH --packet DIR --key FILE [--planted N]
          [--seed S] [--workspace WS]
          The blinded packet for the verifier: DIR/items/item-*.yaml (the
          checklist and N >= 3 planted mutants of it, shuffled) and
          DIR/manifest.json (the verdicts template, the content digest, the
          key's sha256). The answer key goes to FILE, outside DIR: hand the
          verifier (agents/checklist-verifier.md) DIR only.
  rule    --checklist ID|PATH --packet DIR --key FILE --verdicts FILE
          [--write] [--workspace WS]
          Grades the verifier's verdicts: not the checklist's author, every
          planted mutant called faulty for the planted reason, the real item
          called sound, a basis (record id or file#page) for every required
          class. --write then sets the checklist to maturity verified with
          the `verification` block, which coverage counts as it counts an
          approved checklist while the content digest holds. exit 0
          verified / 1 not verified / 2 error.
  status  [--workspace WS] [--maturity-floor F]
          Every checklist, whether it counts at the floor, and why not.

An ID names a library checklist (reference/knowledge/checklists/<id>.yaml),
or with --workspace one under <ws>/research/checklists first.

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

import checklistverify  # noqa: E402
import knowledgelib  # noqa: E402
from checklib import utf8_stdout  # noqa: E402

import yaml  # noqa: E402

SCRIPT = "checklist_verify"


def _find(ref: str, ws: str | None) -> Path:
    p = Path(ref)
    if p.suffix == ".yaml" and p.is_file():
        return p
    cands = []
    if ws:
        cands.append(Path(ws) / knowledgelib.WS_CHECKLISTS / f"{ref}.yaml")
    cands.append(knowledgelib.CHECKLISTS_DIR / f"{ref}.yaml")
    for c in cands:
        if c.is_file():
            return c
    raise FileNotFoundError(f"no checklist {ref!r} (looked at "
                            f"{', '.join(c.as_posix() for c in cands)})")


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a checklist mapping")
    data["_path"] = path.as_posix()
    return data


def _records(ws: str | None) -> list[dict]:
    recs = knowledgelib.load_records()
    if ws:
        recs = knowledgelib.merge_workspace(
            recs, knowledgelib.workspace_records(Path(ws)))
    return recs


def do_packet(args) -> tuple[dict, int]:
    path = _find(args.checklist, args.workspace)
    cl = _load(path)
    manifest = checklistverify.make_packet(
        cl, Path(args.packet), Path(args.key), args.planted, args.seed)
    return {"script": SCRIPT, "status": "pass", "packet": args.packet,
            "key": args.key, "manifest": manifest,
            "next": ("hand the verifier (agents/checklist-verifier.md) "
                     f"{args.packet} only - never the key - then "
                     "checklist_verify.py rule --verdicts <its JSON>")}, 0


def do_rule(args) -> tuple[dict, int]:
    path = _find(args.checklist, args.workspace)
    cl = _load(path)
    packet = Path(args.packet)
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    key_bytes = Path(args.key).read_bytes()
    key = json.loads(key_bytes)
    verdicts = json.loads(Path(args.verdicts).read_text(encoding="utf-8"))
    roots = (Path(args.workspace),) if args.workspace else ()
    graded = checklistverify.grade(cl, manifest, key, key_bytes, verdicts,
                                   _records(args.workspace), roots)
    out = {"script": SCRIPT, "checklist_path": path.as_posix(), **graded}
    if graded["status"] != "verified":
        return out, 1
    if args.write:
        checklistverify.write_verification(path, graded["verification"])
        out["written"] = path.as_posix()
    return out, 0


def do_status(args) -> tuple[dict, int]:
    cls = knowledgelib.load_checklists()
    if args.workspace:
        cls = knowledgelib.merge_workspace(
            cls, knowledgelib.workspace_checklists(Path(args.workspace)))
    rows = checklistverify.status(cls, args.maturity_floor)
    return {"script": SCRIPT, "status": "pass",
            "maturity_floor": args.maturity_floor, "checklists": rows,
            "not_counting": [r["id"] for r in rows if not r["counts"]]}, 0


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--out", help="write the JSON here instead of stdout")
        p.add_argument("--workspace", help="a board workspace whose "
                       "research/checklists and records count too")
        return p

    p = common(sub.add_parser("packet", help="blinded packet + planted mutants"))
    p.add_argument("--checklist", required=True)
    p.add_argument("--packet", required=True, help="dir the verifier reads")
    p.add_argument("--key", required=True, help="answer key, outside --packet")
    p.add_argument("--planted", type=int,
                   default=knowledgelib.CHECKLIST_MIN_PLANTED)
    p.add_argument("--seed", type=int, help="fixed shuffle/mutation seed "
                   "(tests); default random")

    p = common(sub.add_parser("rule", help="grade the verifier's verdicts"))
    p.add_argument("--checklist", required=True)
    p.add_argument("--packet", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--verdicts", required=True)
    p.add_argument("--write", action="store_true",
                   help="on a verified ruling, write it into the checklist")

    p = common(sub.add_parser("status", help="which checklists count"))
    p.add_argument("--maturity-floor", default=knowledgelib.DEFAULT_MATURITY_FLOOR,
                   choices=knowledgelib.MATURITIES)

    args = ap.parse_args(argv)
    try:
        payload, code = {"packet": do_packet, "rule": do_rule,
                         "status": do_status}[args.cmd](args)
    except (OSError, ValueError) as exc:
        payload, code = {"script": SCRIPT, "status": "error",
                         "error": str(exc)}, 2
    text = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
