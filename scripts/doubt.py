#!/usr/bin/env python
"""doubt.py - a board's own questions about how it could fail, each answered
by a check written for it (reference/doubt.md).

The gates ask a fixed list. A doubt is a question an agent forms about THIS
board ("does the amp's RESET reach VIH from a 3.3 V expander?", "can the
PVDD bulk caps see the 28 V EPR contract?"), with a check script that answers
it from the board's files and re-runs on every change. The ledger
`<ws>/doubts/ledger.json` keeps the questions, their checks, their answers
and what was done about each; `<ws>/doubts/doubts.md` is its table.

Questions come from two places:
  - the module's library `scripts/doubts/*.py`: checks an earlier board's
    doubt grew into, asked of every board where they apply (`ask`);
  - the agent holding the board: `add` writes a skeleton check under
    `<ws>/doubts/checks/<id>.py` for it to fill in. A board check general
    enough for any board is promoted into the library (`promote`), so the
    next board is asked it without anyone thinking of it.

Verbs (all take --workspace boards/<b>):
  ask      add every library question that applies and isn't in the ledger;
           print the board's parts and rails as prompts for its own questions
  add      --id ID --question Q --why W --domain D [--check PATH]
           a question of the agent's own; no --check writes the skeleton
  fact     --rail NET=V [--source S]   a rail voltage the net name doesn't
           carry, read by every check (cite where it comes from)
  run      [--id ID ...] answer the questions: each check on the board now
  dispose  --id ID --kind fixed|contingency|firmware|justified|accepted
           --how TEXT   what was done about a fail or an unknown
  status   [--json] what's still open; exit 1 while anything is
  promote  --id ID --name NAME   copy a board check into scripts/doubts/
           (it must define applies()) and point the question at it

A question is open while it has no check, no answer, an answer older than
the board's files or its check (stale), or a fail / unknown / error with no
disposition. A disposition binds the answer it was given against: a re-run
that answers differently opens the question again. Not a gate: the agent
weighs what status says, as with first_spin.py.

Contract (SPEC section 6): exit 0 nothing open, 1 open, 2 error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))

import checklib  # noqa: E402
import doubtlib  # noqa: E402

DISPOSITIONS = ("fixed", "contingency", "firmware", "justified", "accepted")
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")

SKELETON = '''"""{question}

Why it matters: {why}

Answer it from the board's files: board.nets / board.pins[ref][pin]
({{"net", "name"}}), board.rail(net), board.supply(ref), board.mpn(ref),
board.parts (parts.json rows by refdes), board.pcb (the .kicad_pcb path)
for geometry. Return doubtlib.passed(evidence, margin), failed(findings)
or unknown(the facts that would settle it). A pass names what it checked
and by how much it passes.
"""
import doubtlib

QUESTION = {question!r}
DOMAIN = {domain!r}
WHY = {why!r}


def answer(board):
    return doubtlib.unknown(["this check is not written yet"])
'''


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    def __init__(self, ws: Path):
        self.ws = Path(ws).resolve()
        if not self.ws.is_dir():
            raise checklib.CheckError(f"no workspace {ws}")
        self.dir = self.ws / "doubts"
        self.path = self.dir / "ledger.json"
        if self.path.is_file():
            self.data = checklib.load_json(self.path, "doubt ledger")
        else:
            self.data = {"board": self.ws.name, "facts": {"rails": {}},
                         "questions": []}

    @property
    def questions(self) -> list[dict]:
        return self.data["questions"]

    def get(self, qid: str) -> dict:
        for q in self.questions:
            if q["id"] == qid:
                return q
        raise checklib.CheckError(f"no question {qid} in {self.path}")

    def rails(self) -> dict:
        return {k: v["volts"] for k, v in
                self.data.setdefault("facts", {}).get("rails", {}).items()}

    def board(self) -> doubtlib.Board:
        return doubtlib.Board.from_workspace(self.ws, self.rails())

    def check_path(self, q: dict) -> Path | None:
        c = q.get("check")
        if not c:
            return None
        if c.startswith("lib:"):
            return doubtlib.library().get(c[4:])
        return self.dir / c

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")
        (self.dir / "doubts.md").write_text(render_md(self))


def board_digest(ws: Path, board: doubtlib.Board) -> dict:
    out = {"net": doubtlib.sha256(board.netlist)[:16]}
    if board.pcb:
        out["pcb"] = doubtlib.sha256(board.pcb)[:16]
    return out


def state(led: Ledger, q: dict, digest: dict | None = None) -> str:
    """open-no-check | open-unanswered | open-stale | open-<verdict> |
    answered (pass) | disposed"""
    path = led.check_path(q)
    if path is None or not path.is_file():
        return "open-no-check"
    a = q.get("answer")
    if not a:
        return "open-unanswered"
    if digest is not None and (a.get("board") != digest
                               or a.get("check") != doubtlib.sha256(path)[:16]):
        return "open-stale"
    if a["verdict"] == "pass":
        return "answered"
    d = q.get("disposition")
    if d and d.get("verdict") == a["verdict"] \
            and d.get("answer_at") == a.get("at"):
        return "disposed"
    return f"open-{a['verdict']}"


def _digest_or_none(led: Ledger):
    try:
        b = led.board()
    except checklib.CheckError:
        return None
    return board_digest(led.ws, b)


def render_md(led: Ledger) -> str:
    digest = _digest_or_none(led)
    lines = [f"# Doubts: {led.data.get('board', led.ws.name)}", "",
             "What could make this board fail, each question answered by a "
             "check that re-runs (`hwde doubt run`; reference/doubt.md). "
             "Written by doubt.py: edit the ledger through it.", "",
             "| Id | Domain | Question | State | Answer |",
             "|---|---|---|---|---|"]
    for q in led.questions:
        a = q.get("answer") or {}
        bits = [*(f if isinstance(f, str) else f.get("msg", "")
                  for f in a.get("findings", [])),
                *a.get("unknowns", [])]
        summ = a.get("margin") or ""
        if bits:
            summ = "; ".join(bits[:3]) + (f" (+{len(bits) - 3})"
                                          if len(bits) > 3 else "")
        elif a.get("evidence"):
            summ = summ or a["evidence"][0]
        d = q.get("disposition")
        st = state(led, q, digest)
        if st == "disposed":
            summ += f" -- {d['kind']}: {d['how']}"
        cell = summ.replace("|", "/").replace("\n", " ")
        lines.append(f"| {q['id']} | {q.get('domain', '')} | "
                     f"{q['question'].replace('|', '/')} | {st} | {cell} |")
    facts = led.data.get("facts", {}).get("rails", {})
    if facts:
        lines += ["", "## Rail facts", ""]
        lines += [f"- {k} = {v['volts']} V ({v.get('source') or 'no source'})"
                  for k, v in sorted(facts.items())]
    return "\n".join(lines) + "\n"


# -- verbs

def cmd_ask(led: Ledger, args) -> dict:
    board = led.board()
    have = {q.get("check") for q in led.questions}
    added, skipped = [], []
    for name, path in doubtlib.library().items():
        mod = doubtlib.load_check(path)
        ok = mod.applies(board) if hasattr(mod, "applies") else True
        if not ok:
            skipped.append(name)
            continue
        if f"lib:{name}" in have:
            continue
        led.questions.append({"id": f"lib.{name}", "question": mod.QUESTION,
                              "why": getattr(mod, "WHY", ""),
                              "domain": getattr(mod, "DOMAIN", ""),
                              "check": f"lib:{name}", "origin": "library",
                              "added": now()})
        added.append(name)
    led.save()
    prompts = []
    for ref in board.ics() + board.refs("J", "Q", "D", "F", "Y", "X"):
        prompts.append(f"{ref} {board.mpn(ref)}")
    rails = sorted({board.label(n): board.rail(n) for n in board.nets
                    if board.rail(n) is not None
                    and not board.is_ground(n)}.items())
    unknown_rails = sorted({board.label(n) for n in board.nets
                            if re.search(r"V(DD|CC|IN|BUS|BAT|OUT)|PVDD|VS$|"
                                         r"^\+|RAIL|PWR", n, re.I)
                            and board.rail(n) is None})
    return {"status": "pass", "added": added, "not_applicable": skipped,
            "prompts": {"parts": prompts, "rails": rails,
                        "rails_without_voltage": unknown_rails}}


def cmd_add(led: Ledger, args) -> dict:
    if not ID_RE.match(args.id) or args.id.startswith("lib."):
        raise checklib.CheckError(f"bad id {args.id!r}: letters, digits, "
                                  "._- ; lib. is the library's")
    if any(q["id"] == args.id for q in led.questions):
        raise checklib.CheckError(f"{args.id} is already in the ledger")
    if args.domain not in doubtlib.DOMAINS:
        raise checklib.CheckError(f"domain {args.domain!r}: one of "
                                  + ", ".join(doubtlib.DOMAINS))
    if not args.why.strip():
        raise checklib.CheckError("--why: how the board fails if the answer "
                                  "is bad; a question with no failure in "
                                  "mind is not a doubt")
    if args.check:
        src = Path(args.check)
        if args.check.startswith("lib:"):
            if args.check[4:] not in doubtlib.library():
                raise checklib.CheckError(f"no library check {args.check}")
            check = args.check
        else:
            src = src.resolve()
            try:
                check = str(src.relative_to(led.dir))
            except ValueError:
                raise checklib.CheckError(
                    f"{src}: a board check lives under {led.dir}") from None
    else:
        check = f"checks/{re.sub(r'[^A-Za-z0-9_]', '_', args.id)}.py"
        path = led.dir / check
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(SKELETON.format(question=args.question,
                                            why=args.why, domain=args.domain))
    led.questions.append({"id": args.id, "question": args.question,
                          "why": args.why, "domain": args.domain,
                          "check": check, "origin": "board", "added": now()})
    led.save()
    return {"status": "pass", "id": args.id, "check": check}


def cmd_fact(led: Ledger, args) -> dict:
    rails = led.data.setdefault("facts", {}).setdefault("rails", {})
    for item in args.rail:
        net, _, v = item.partition("=")
        try:
            volts = float(v)
        except ValueError:
            raise checklib.CheckError(f"--rail {item}: NET=VOLTS") from None
        rails[net.strip()] = {"volts": volts, "source": args.source,
                              "at": now()}
    led.save()
    return {"status": "pass", "rails": rails}


def cmd_run(led: Ledger, args) -> dict:
    board = led.board()
    digest = board_digest(led.ws, board)
    want = set(args.id or [])
    ran = {}
    for q in led.questions:
        if want and q["id"] not in want:
            continue
        path = led.check_path(q)
        if path is None or not path.is_file():
            ran[q["id"]] = "no check"
            continue
        ans = doubtlib.run_check(path, board)
        ans.update(at=now(), board=digest, check=doubtlib.sha256(path)[:16])
        q["answer"] = ans
        ran[q["id"]] = ans["verdict"]
    led.save()
    out = cmd_status(led, args)
    out["ran"] = ran
    return out


def cmd_dispose(led: Ledger, args) -> dict:
    q = led.get(args.id)
    a = q.get("answer")
    if not a:
        raise checklib.CheckError(f"{args.id} has no answer to dispose of: "
                                  "run it first")
    if a["verdict"] == "pass":
        raise checklib.CheckError(f"{args.id} passes; nothing to dispose of")
    if a["verdict"] == "error":
        raise checklib.CheckError(f"{args.id}'s check errors: fix the check, "
                                  "a broken check can't be accepted")
    if not args.how.strip():
        raise checklib.CheckError("--how: what was done, with its number")
    q["disposition"] = {"kind": args.kind, "how": args.how,
                        "verdict": a["verdict"], "answer_at": a.get("at"),
                        "at": now()}
    led.save()
    return {"status": "pass", "id": args.id, "disposition": q["disposition"]}


def cmd_status(led: Ledger, args) -> dict:
    digest = _digest_or_none(led)
    rows, open_ = [], []
    for q in led.questions:
        st = state(led, q, digest)
        rows.append({"id": q["id"], "state": st})
        if st.startswith("open"):
            open_.append(q["id"])
    return {"status": "violations" if open_ else "pass",
            "board": led.data.get("board"), "questions": len(rows),
            "open": open_, "states": rows,
            "ledger": str(led.path)}


def cmd_promote(led: Ledger, args) -> dict:
    q = led.get(args.id)
    if not re.match(r"^[a-z][a-z0-9_]*$", args.name):
        raise checklib.CheckError(f"--name {args.name!r}: a python module "
                                  "name (lower_snake)")
    src = led.check_path(q)
    if q.get("check", "").startswith("lib:") or not src or not src.is_file():
        raise checklib.CheckError(f"{args.id} has no board check to promote")
    dest = doubtlib.library_dir() / f"{args.name}.py"
    if dest.exists():
        raise checklib.CheckError(f"{dest} exists: pick another name")
    mod = doubtlib.load_check(src)
    if not hasattr(mod, "applies"):
        raise checklib.CheckError(
            f"{src} has no applies(board): a library check says on which "
            "boards its question arises, so it is generic before it moves")
    shutil.copyfile(src, dest)
    q["check"] = f"lib:{args.name}"
    q["promoted_from"] = str(src.relative_to(led.dir))
    src.unlink()
    led.save()
    return {"status": "pass", "id": args.id, "library": str(dest),
            "next": "add a test with a board it fails on (tests/test_doubt.py)"}


VERBS = {"ask": cmd_ask, "add": cmd_add, "fact": cmd_fact, "run": cmd_run,
         "dispose": cmd_dispose, "status": cmd_status, "promote": cmd_promote}


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("verb", choices=sorted(VERBS))
    ap.add_argument("--workspace", required=True, help="boards/<board>")
    ap.add_argument("--id", action="append")
    ap.add_argument("--question")
    ap.add_argument("--why", default="")
    ap.add_argument("--domain")
    ap.add_argument("--check")
    ap.add_argument("--rail", action="append", default=[])
    ap.add_argument("--source")
    ap.add_argument("--kind", choices=DISPOSITIONS)
    ap.add_argument("--how", default="")
    ap.add_argument("--name")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    one = {"add", "dispose", "promote"}
    if args.verb in one:
        if not args.id or len(args.id) != 1:
            ap.error(f"{args.verb} takes one --id")
        args.id = args.id[0]
    need = {"add": ("question", "domain"), "dispose": ("kind",),
            "promote": ("name",), "fact": ("rail",)}
    for attr in need.get(args.verb, ()):
        if not getattr(args, attr):
            ap.error(f"{args.verb} needs --{attr}")
    return args


def run(argv):
    args = parse(argv)
    led = Ledger(Path(args.workspace))
    payload = VERBS[args.verb](led, args)
    payload.setdefault("script", "doubt")
    if args.verb in ("status", "run") and not args.json and not args.out:
        lines = [f"{r['id']:<28} {r['state']}" for r in payload["states"]]
        lines.append(f"{len(payload['open'])} open of {payload['questions']}"
                     f" ({led.dir / 'doubts.md'})")
        return payload, "\n".join(lines)
    return payload, None


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, text = run(sys.argv[1:] if argv is None else argv)
    except checklib.CheckError as e:
        print(json.dumps({"script": "doubt", "status": "error",
                          "error": str(e)}))
        return 2
    if text is not None:
        print(text)
        return 0 if payload["status"] == "pass" else 1
    return checklib.emit(payload, None)


if __name__ == "__main__":
    sys.exit(main())
