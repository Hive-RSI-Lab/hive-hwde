#!/usr/bin/env python
"""parts_enrich.py - fill parts.json `attributes` from each part's LCSC listing.

sim_board, check_ratings and mlcc_dcbias judge a part by its parts.json
`attributes` (a resistor's Power(Watts), a cap's Voltage Rated and
dielectric, a FET's Vgs/Vgs(th)/RDS(on), an LED's Vf, a current-sense amp's
common-mode range). Nothing else in the pipeline fills them, so without this
step every resistor is judged at its package default and every cap's rating
is a known unknown (#467).

For every parts.json row with an `lcsc` code this asks JLC's parts search
(lib/partslib.py, the endpoint parts_search uses) for that exact code and
merges the listing's attributes into the row: an attribute the row already
names is kept as it is (a hand-set or generator value wins), a missing one is
added. An empty `description` or `datasheet` is filled the same way. Listings
are cached per code under ~/.cache/hwde/lcsc_listing (HWDE_LCSC_CACHE), so a
rerun is offline; --refresh asks again.

CLI:
  parts_enrich.py --workspace boards/<b>       # parts/parts.json in place
  parts_enrich.py --parts parts/ [--dry-run] [--refresh] [--out report.json]
Exit 0 every coded row has its listing / 1 a code JLC does not know (the
row is left as it was) / 2 error (no parts.json, endpoint unreachable with
rows still to fetch).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import partslib  # noqa: E402

SCRIPT = "parts_enrich"


def cache_dir() -> Path:
    return Path(os.environ.get("HWDE_LCSC_CACHE")
                or Path.home() / ".cache" / "hwde" / "lcsc_listing")


def listing(lcsc: str, cache: Path, refresh: bool = False) -> dict | None:
    """The normalized JLC row for exactly this code (cached), or None when
    JLC's search knows no such code. Raises partslib.PartsError when the
    endpoint can't be reached and nothing is cached."""
    f = cache / f"{lcsc}.json"
    if f.is_file() and not refresh:
        got = json.loads(f.read_text(encoding="utf-8"))
        return got or None
    rows, _ = partslib.live_search(lcsc, page_size=10)
    hit = next((partslib.normalize(r) for r in rows
                if str(r.get("lcsc", "")).upper() == lcsc.upper()), None)
    if hit is None and not rows and not partslib.endpoint_reachable():
        raise partslib.PartsError("JLC parts search unreachable")
    cache.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(hit or {}, ensure_ascii=False), encoding="utf-8")
    return hit


def merge(row: dict, hit: dict) -> list[str]:
    """Merge a listing into a parts.json row in place; the names added."""
    have = {str(a.get("name", "")) for a in row.get("attributes") or []}
    new = [a for a in hit.get("attributes") or []
           if a.get("name") and a["name"] not in have]
    if new:
        row["attributes"] = list(row.get("attributes") or []) + new
    added = [a["name"] for a in new]
    for k in ("description", "datasheet"):
        if not row.get(k) and hit.get(k):
            row[k] = hit[k]
            added.append(k)
    return added


def _indent(text: str) -> int:
    lines = text.splitlines()
    if len(lines) > 1:
        n = len(lines[1]) - len(lines[1].lstrip(" "))
        if n:
            return n
    return 2


def run(a) -> dict:
    pdir = Path(a.parts) if a.parts else (Path(a.workspace) / "parts"
                                          if a.workspace else None)
    pj = pdir / "parts.json" if pdir else None
    if pj is None or not pj.is_file():
        raise checklib.CheckError("give --workspace or --parts holding "
                                  "parts.json")
    text = pj.read_text(encoding="utf-8")
    doc = json.loads(text)
    cache = cache_dir()
    rows, vios, unreachable = [], [], []
    for p in doc.get("parts") or []:
        code = str(p.get("lcsc") or "").strip()
        refs = list(p.get("refdes") or [])
        if not code:
            continue
        try:
            hit = listing(code, cache, a.refresh)
        except partslib.PartsError as exc:
            unreachable.append(code)
            rows.append({"lcsc": code, "refdes": refs, "status": "error",
                         "why": str(exc)})
            continue
        if hit is None:
            rows.append({"lcsc": code, "refdes": refs, "status": "unknown"})
            vios.append(checklib.violation(
                SCRIPT, "warning", None, None, None, refs,
                f"{code} ({p.get('mpn') or '?'}) not found in JLC's parts "
                "search: its attributes stay as they are", SCRIPT,
                kind="lcsc_unknown"))
            continue
        added = merge(p, hit)
        rows.append({"lcsc": code, "refdes": refs, "status":
                     "filled" if added else "unchanged", "added": added})
    if unreachable and len(unreachable) == len(rows):
        raise checklib.CheckError(
            f"JLC parts search unreachable; {len(unreachable)} codes not "
            "cached")
    changed = any(r["status"] == "filled" for r in rows)
    if changed and not a.dry_run:
        pj.write_text(json.dumps(doc, indent=_indent(text),
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    return checklib.report(
        SCRIPT, pj, vios, written=bool(changed and not a.dry_run),
        filled=sum(r["status"] == "filled" for r in rows),
        unchanged=sum(r["status"] == "unchanged" for r in rows),
        not_reached=unreachable, rows=rows)


def main(argv: list[str] | None = None) -> int:
    checklib.utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace")
    ap.add_argument("--parts", help="parts dir holding parts.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be added; write nothing")
    ap.add_argument("--refresh", action="store_true",
                    help="ask JLC again even for cached codes")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    return checklib.cli_wrap(SCRIPT, lambda: (run(a), a.out))


if __name__ == "__main__":
    sys.exit(main())
