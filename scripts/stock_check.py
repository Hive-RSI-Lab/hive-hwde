#!/usr/bin/env python
"""stock_check.py - can JLC place every row of the BOM today? (the fab gate)

Reads the assembler upload (fab/BOM.csv: Comment, Designator, Footprint,
LCSC Part #) and asks JLC's and LCSC's own records, live, for each row
(lib/stocklib.py): is the code JLC's and for sale, is it the part the BOM
means (MPN, else value), and can JLC sell what the build needs (designators
x --boards)? A row JLC would swap (not for sale, with its substitute), short,
unknown to JLC, mismatched, or not checkable (a source down) fails: unknown
is never a pass. bom_cpl.py runs this on every fab run it makes live; run it
again right before the order, since stock moves daily.

The MPN a row is compared with comes from the workspace's parts data (parts/
parts.json, parts/<LCSC>.json - distributor_bom.lcsc_index); a passive with
no MPN is matched by its value in JLC's description.

With --workspace the report goes to <ws>/reports/stock_check.json unless
--out says otherwise, and the verdicts go into the board's fact ledger
(ledger.json, lib/ledgerlib.py) as `stock`, `part_match` and `lifecycle`
facts, each with its source and time.

CLI:
  stock_check.py --bom fab/BOM.csv [--parts parts/] [--boards 5]
                 [--workspace WS] [--jobs 6] [--out report.json]
Exit 0 every row ok / 1 a row blocks the order / 2 error (no BOM, no
LCSC column). HWDE_STOCK_OFFLINE=1 refuses (exit 2): this check is live
or it is nothing.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import stocklib  # noqa: E402

SCRIPT = "stock_check"
DEFAULT_BOARDS = 5   # bom_cpl's DEFAULT_BUILD_QTY: JLC's smallest PCBA order
LCSC_COLS = ("LCSC Part #", "LCSC", "JLCPCB Part #", "LCSC Part")


def read_bom(path: Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        col = next((c for c in LCSC_COLS if c in r), None)
        if col is None:
            raise ValueError(f"{path}: no LCSC column (one of "
                             f"{', '.join(LCSC_COLS)})")
        refs = [x.strip() for x in str(r.get("Designator") or "").split(",")
                if x.strip()]
        out.append({"Comment": r.get("Comment", ""),
                    "Designator": r.get("Designator", ""),
                    "Footprint": r.get("Footprint", ""),
                    "LCSC": (r.get(col) or "").strip(), "refs": refs})
    return out


def mpn_index(parts_dir: Path | None) -> dict[str, dict]:
    import distributor_bom
    return distributor_bom.lcsc_index(parts_dir)


def check_rows(rows: list[dict], boards: int = DEFAULT_BOARDS,
               mpns: dict | None = None, fetch=None,
               jobs: int = 6) -> dict:
    """{status, boards, rows[], blocking[], counts{}} for BOM rows (dicts
    with Comment, LCSC and refs or Designator)."""
    fetch = fetch or stocklib.http_fetch
    mpns = mpns or {}

    def one(r):
        refs = r.get("refs") or [x.strip() for x in
                                 str(r.get("Designator") or "").split(",")
                                 if x.strip()]
        code = str(r.get("LCSC") or "").strip()
        mpn = (mpns.get(code) or {}).get("mpn", "")
        v = stocklib.check_row(code, len(refs) * boards, mpn=mpn,
                               value=r.get("Comment", ""), fetch=fetch)
        v["refs"] = refs
        v["comment"] = r.get("Comment", "")
        return v

    with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        verdicts = list(ex.map(one, rows))
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    blocking = [v for v in verdicts if v["status"] in stocklib.BLOCKING]
    return {"status": "fail" if blocking else "pass", "boards": boards,
            "checked_at": stocklib.now(), "counts": counts,
            "rows": verdicts,
            "blocking": [{"lcsc": v["lcsc"], "refs": v["refs"],
                          "status": v["status"], "why": v["why"]}
                         for v in blocking]}


def violations(result: dict) -> list[dict]:
    """bom_cpl-style violations, one per blocking row."""
    return [{"kind": f"bom_stock_{b['status']}", "refs": b["refs"],
             "lcsc": b["lcsc"], "message": f"{b['lcsc']}: {b['why']}"}
            for b in result.get("blocking") or []]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bom", help="JLC BOM upload (default <ws>/fab/BOM.csv)")
    ap.add_argument("--parts", help="parts dir (default <ws>/parts)")
    ap.add_argument("--workspace", help="board workspace: defaults the paths "
                    "and the report (reports/stock_check.json)")
    ap.add_argument("--boards", type=int, default=DEFAULT_BOARDS,
                    help=f"boards in the build (default {DEFAULT_BOARDS})")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", help="write the JSON report here too")
    args = ap.parse_args(argv)
    ws = Path(args.workspace) if args.workspace else None
    try:
        if stocklib.offline():
            raise RuntimeError("HWDE_STOCK_OFFLINE is set: stock is only "
                               "known by asking")
        bom = Path(args.bom) if args.bom else (ws / "fab" / "BOM.csv"
                                               if ws else None)
        if bom is None or not bom.is_file():
            raise FileNotFoundError(f"no BOM: {bom} (give --bom or "
                                    "--workspace)")
        parts = Path(args.parts) if args.parts else (
            ws / "parts" if ws else bom.resolve().parent.parent / "parts")
        res = check_rows(read_bom(bom), boards=args.boards,
                         mpns=mpn_index(parts), jobs=args.jobs)
        res = {"script": SCRIPT, "bom": str(bom), **res}
        if ws is not None:
            import ledgerlib
            led = ledgerlib.record_stock(ws, res)
            res["ledger"] = str(led) if led else None
        if ws is not None and not args.out:
            args.out = str(ws / "reports" / "stock_check.json")
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        err = {"script": SCRIPT, "status": "error",
               "error": f"{type(exc).__name__}: {exc}"}
        text = json.dumps(err, indent=1)
        (Path(args.out).write_text(text, encoding="utf-8") if args.out
         else print(text))
        return 2
    text = json.dumps(res, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    for b in res["blocking"]:
        print(f"stock_check: BLOCKS {','.join(b['refs'])} {b['lcsc']} "
              f"[{b['status']}]: {b['why']}", file=sys.stderr)
    return 1 if res["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
