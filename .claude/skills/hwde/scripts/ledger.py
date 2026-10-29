#!/usr/bin/env python
"""ledger.py - the board's fact ledger: what it relies on, and what is unknown.

lib/ledgerlib.py says what the ledger holds and who writes it. This shows it
and takes facts recorded by hand.

  ledger.py report --workspace WS [--all] [--out FILE]
      every fact the board relies on as it reads now (stale stock and facts
      about a since-changed part read unknown), with counts per kind and the
      failed and unknown ones listed. Exit 0 ready (every fact verified) /
      1 not ready / 2 error. Release (attest.py) refuses on the same list.
  ledger.py set --workspace WS --subject U8 --kind pinout
                --status verified|failed|unknown --source "..." [--value ".."]
      record one fact you checked yourself; --source is required for
      verified and failed (where it came from: a datasheet page, a
      calculation, a distributor page and its date).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import ledgerlib  # noqa: E402

SCRIPT = "ledger"


def run(argv=None) -> tuple[dict, str | None, int]:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("report", help="what the board knows and does not")
    p.add_argument("--workspace", required=True)
    p.add_argument("--all", action="store_true",
                   help="list every fact, not only the gaps")
    p.add_argument("--out")
    p = sub.add_parser("set", help="record a fact by hand")
    p.add_argument("--workspace", required=True)
    p.add_argument("--subject", required=True,
                   help="refdes (rating/pinout/footprint) or LCSC code")
    p.add_argument("--kind", required=True, choices=ledgerlib.KINDS)
    p.add_argument("--status", required=True, choices=ledgerlib.STATUSES)
    p.add_argument("--source", default="")
    p.add_argument("--value", default="")
    p.add_argument("--out")
    a = ap.parse_args(argv)
    ws = Path(a.workspace)
    if not (ws / "state.json").is_file():
        raise SystemExit(f"{ws}: no state.json - not a board workspace")
    if a.cmd == "set":
        if a.status != "unknown" and not a.source.strip():
            raise SystemExit("--source is required for a verified or failed "
                             "fact: where does it come from?")
        exp = ledgerlib.expected(ws).get(ledgerlib.key(a.subject, a.kind))
        fact = {"subject": a.subject, "kind": a.kind, "status": a.status,
                "source": a.source, "value": a.value}
        if exp:
            fact.update(lcsc=exp.get("lcsc"), refs=exp.get("refs"))
        ledgerlib.put(ws, [fact], "hand")
        rep = {"script": SCRIPT, "status": "pass", "recorded": fact,
               "expected": exp is not None}
        return rep, a.out, 0
    rep = ledgerlib.report(ws)
    if not a.all:
        rep.pop("facts")
    rep = {"script": SCRIPT, "status": "pass" if rep["ready"] else "gaps",
           **rep}
    return rep, a.out, 0 if rep["ready"] else 1


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        rep, out, code = run(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int) or exc.code is None:
            raise
        rep, out, code = ({"script": SCRIPT, "status": "error",
                           "error": str(exc.code)}, None, 2)
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        rep, out, code = ({"script": SCRIPT, "status": "error",
                           "error": f"{type(exc).__name__}: {exc}"}, None, 2)
    text = json.dumps(rep, indent=1, ensure_ascii=False)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
