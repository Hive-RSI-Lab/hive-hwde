#!/usr/bin/env python
"""distributor_bom.py - DigiKey and Mouser BOMs for a self-assembled build.

Owner, 2026-10-01: "equivalent digikey and or mouser boms ... in case the
assembly fee is too high". bom_cpl.py calls write() right after it writes the
BOM of record, so every fab step leaves, beside the JLC pair in fab/:

  <ws>_BOM_digikey.csv  DigiKey myLists / BOM Manager upload
  <ws>_BOM_mouser.csv   Mouser BOM Tool upload
  <ws>_BOM_cost.json    the two totals side by side - only when at least one
                        distributor's API keys are set

<ws> is the board workspace dir name (PCB-0022-A_nfc-card). Rows come from
BOM-full.csv, so hand-installed, off-board, customer-supplied and
select-on-test parts are in too; each row's assembly class is in the Notes
column. `dnp` and `board_feature` lines are left out: nothing is bought for
them. Lines that share an MPN merge into one row, so the distributor does not
split one part's quantity. Quantity = per board x --boards (default 1).

Column headers. DigiKey: its own upload template ("Digi-Key Part Number",
"Manufacturer Name", "Manufacturer Part Number", "Customer Reference",
"Quantity 1"), taken from DigiKey's BOM Manager upload page as shown in a
Georgia Tech capstone handout; today's myLists upload was not checked live.
Mouser: the BOM Tool maps columns by hand at upload (and remembers the map);
its own field names could not be read (mouser.com refuses scripted fetches), so
these are plain names chosen to map on sight. Both are UNCONFIRMED against a
live upload - LEARNINGS 2026-10-01 [bom][fab].

A line with no MPN takes one from the LCSC data the workspace already holds
(parts/parts.json, then parts/<LCSC>.json); the manufacturer comes the same
way. A line still without an MPN stays in the file with the MPN blank, and its
designators are listed in the warnings (bom_cpl prints them on stderr).

Prices: with HWDE_DIGIKEY_CLIENT_ID + _SECRET and/or HWDE_MOUSER_API_KEY set
(lib/distributors.py), each MPN is looked up and the distributor part number,
stock and unit price at the order quantity are filled in. Without keys the
by-MPN files are still written and a warning says prices were not looked up.
Missing keys, a failed lookup or a transport error never fail the fab step.

CLI (read-only on its input; writes only into --out-dir):
  distributor_bom.py --bom-full fab/BOM-full.csv --out-dir DIR
                     [--ws-name NAME] [--parts-dir parts/] [--boards N]
                     [--no-price-lookup] [--out report.json]
Exit 0 written (warnings on stderr) / 2 error.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

import distributors  # noqa: E402

DIGIKEY_FIELDS = ["Digi-Key Part Number", "Manufacturer Name",
                  "Manufacturer Part Number", "Customer Reference",
                  "Quantity 1", "Notes", "Description", "LCSC",
                  "Stock", "Unit Price (USD)", "Extended Price (USD)"]
MOUSER_FIELDS = ["Mouser Part Number", "Manufacturer",
                 "Manufacturer Part Number", "Customer Part Number",
                 "Quantity", "Notes", "Description", "LCSC",
                 "Stock", "Unit Price", "Extended Price"]
# BOM-full classes nothing is bought for.
SKIP_CLASSES = ("dnp", "board_feature")


# ------------------------------------------------------------- input rows
def read_bom_full(path: Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def lcsc_index(parts_dir: Path | None) -> dict[str, dict]:
    """LCSC number -> {mpn, manufacturer} from the workspace's own part data:
    parts/<LCSC>.json first, parts/parts.json lines over it (the sourced line
    carries JLC's `brand`; an extraction file usually does not)."""
    out: dict[str, dict] = {}
    if parts_dir is None or not Path(parts_dir).is_dir():
        return out

    def put(lcsc, ent):
        lcsc = str(lcsc or "").strip()
        if not lcsc or not isinstance(ent, dict):
            return
        cur = out.setdefault(lcsc, {"mpn": "", "manufacturer": ""})
        mpn = ent.get("mpn") or ent.get("mfr_part") or ""
        man = ent.get("manufacturer") or ent.get("brand") or ""
        if mpn:
            cur["mpn"] = str(mpn)
        if man:
            cur["manufacturer"] = str(man)

    for f in sorted(Path(parts_dir).glob("C*.json")):
        try:
            ent = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        put(ent.get("lcsc") or f.stem if isinstance(ent, dict) else "", ent)
    pj = Path(parts_dir) / "parts.json"
    if pj.is_file():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        items = data.get("parts", data) if isinstance(data, dict) else data
        if isinstance(items, list):
            for ent in items:
                if isinstance(ent, dict):
                    put(ent.get("lcsc") or ent.get("LCSC"), ent)
    return out


def _refs(designator: str) -> list[str]:
    return [r.strip() for r in str(designator or "").split(",") if r.strip()]


def build_lines(bom_full: list[dict], lcsc_data: dict[str, dict],
                boards: int = 1) -> list[dict]:
    """BOM-full rows -> one buy line per MPN (a line with no MPN stands alone).

    {mpn, manufacturer, refs[], qty_per_board, qty, classes[], comment,
     footprint, lcsc, mpn_source (bom|lcsc|"")}"""
    lines: list[dict] = []
    by_mpn: dict[str, dict] = {}
    for row in bom_full:
        cls = (row.get("Assembly Class") or "").strip()
        if cls in SKIP_CLASSES:
            continue
        lcsc = (row.get("LCSC") or "").strip()
        mpn = (row.get("MPN") or "").strip()
        known = lcsc_data.get(lcsc, {})
        src = "bom" if mpn else ""
        if not mpn and known.get("mpn"):
            mpn, src = known["mpn"], "lcsc"
        refs = _refs(row.get("Designator"))
        try:
            per_board = int(row.get("Qty Per Board") or len(refs) or 1)
        except ValueError:
            per_board = len(refs) or 1
        key = mpn.upper()
        line = by_mpn.get(key) if key else None
        if line is None:
            line = {"mpn": mpn, "manufacturer": known.get("manufacturer", ""),
                    "refs": [], "qty_per_board": 0, "classes": [],
                    "comment": (row.get("Comment") or "").strip(),
                    "footprint": (row.get("Footprint") or "").strip(),
                    "lcsc": lcsc, "mpn_source": src}
            lines.append(line)
            if key:
                by_mpn[key] = line
        line["refs"] += refs
        line["qty_per_board"] += per_board
        if cls and cls not in line["classes"]:
            line["classes"].append(cls)
        line["lcsc"] = line["lcsc"] or lcsc
        line["manufacturer"] = line["manufacturer"] or known.get(
            "manufacturer", "")
    for line in lines:
        line["qty"] = line["qty_per_board"] * boards
    return lines


# ---------------------------------------------------------------- pricing
def price_at(breaks: list[dict], qty: int) -> tuple[float | None, int]:
    """(unit price, quantity actually bought). The break with the largest
    quantity not above `qty`; below the smallest break, that break's price at
    its own quantity (the minimum order)."""
    good = sorted((int(b["qty"]), float(b["unit_price"])) for b in breaks or []
                  if b.get("qty") is not None and b.get("unit_price") is not None)
    if not good:
        return None, qty
    if qty < good[0][0]:
        return good[0][1], good[0][0]
    unit = good[0][1]
    for q, p in good:
        if q <= qty:
            unit = p
    return unit, qty


def _pick(hits: list[dict], mpn: str) -> dict | None:
    exact = [h for h in hits if str(h.get("mpn") or "").upper() == mpn.upper()]
    return (exact or hits or [None])[0]


def _search(provider: str, client, mpn: str) -> tuple[dict | None, str]:
    """(normalized best hit | None, error text)."""
    if provider == "digikey":
        resp = client.keyword(mpn, limit=5)
        body = resp.get("json")
        if resp.get("status") != 200 or not isinstance(body, dict):
            return None, f"HTTP {resp.get('status')}"
        hits = [distributors.normalize_digikey(p)
                for p in body.get("Products") or []]
    else:
        resp = client.part_number(mpn)
        body = resp.get("json")
        errs = body.get("Errors") if isinstance(body, dict) else None
        if resp.get("status") != 200 or not isinstance(body, dict) or errs:
            return None, (str(errs)[:200] if errs
                          else f"HTTP {resp.get('status')}")
        hits = [distributors.normalize_mouser(p) for p in
                (body.get("SearchResults") or {}).get("Parts") or []]
    hit = _pick(hits, mpn)
    return hit, ("" if hit else "no match")


def price_lines(provider: str, lines: list[dict], transport=None
                ) -> tuple[dict[int, dict] | None, list[str]]:
    """index -> {pn, stock, unit, buy_qty, ext} for one provider, or None when
    its keys are missing or the lookup fails. Never raises."""
    missing = distributors.missing_credentials(provider)
    if missing:
        return None, [f"{provider}: prices not looked up (no {', '.join(missing)})"]
    warns: list[str] = []
    out: dict[int, dict] = {}
    try:
        client = (distributors.DigiKeyClient if provider == "digikey"
                  else distributors.MouserClient).from_env(transport=transport)
        for i, line in enumerate(lines):
            if not line["mpn"]:
                continue
            hit, err = _search(provider, client, line["mpn"])
            if hit is None:
                warns.append(f"{provider}: {line['mpn']} ({','.join(line['refs'])})"
                             f" not priced: {err}")
                continue
            unit, buy = price_at(hit.get("price_breaks"), line["qty"])
            out[i] = {"pn": hit.get("distributor_pn") or "",
                      "manufacturer": hit.get("manufacturer") or "",
                      "stock": hit.get("stock"), "unit": unit, "buy_qty": buy,
                      "ext": round(unit * buy, 4) if unit is not None else None}
    except Exception as exc:  # noqa: BLE001 - a lookup never fails the fab step
        return None, [f"{provider}: prices not looked up "
                      f"({type(exc).__name__}: {exc})"]
    return out, warns


# ----------------------------------------------------------------- output
def _notes(line: dict) -> str:
    txt = "; ".join(line["classes"])
    if line["mpn_source"] == "lcsc":
        txt += "; MPN from LCSC data"
    return txt


def _num(v) -> str:
    return "" if v is None else (f"{v:.4f}".rstrip("0").rstrip(".")
                                 if isinstance(v, float) else str(v))


def _rows(provider: str, lines: list[dict], priced: dict | None) -> list[dict]:
    f = DIGIKEY_FIELDS if provider == "digikey" else MOUSER_FIELDS
    rows = []
    for i, line in enumerate(lines):
        p = (priced or {}).get(i, {})
        vals = [p.get("pn", ""), line["manufacturer"] or p.get("manufacturer", ""),
                line["mpn"], ",".join(line["refs"]), str(line["qty"]),
                _notes(line), line["comment"], line["lcsc"],
                _num(p.get("stock")), _num(p.get("unit")), _num(p.get("ext"))]
        rows.append(dict(zip(f, vals)))
    return rows


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _total(lines: list[dict], priced: dict | None) -> dict:
    if priced is None:
        return {"looked_up": False}
    unpriced = [",".join(lines[i]["refs"]) or lines[i]["mpn"]
                for i in range(len(lines))
                if lines[i]["mpn"] and priced.get(i, {}).get("ext") is None]
    short = [lines[i]["mpn"] for i, p in priced.items()
             if isinstance(p.get("stock"), int) and p["stock"] < p["buy_qty"]]
    return {"looked_up": True,
            "total_usd": round(sum(p["ext"] for p in priced.values()
                                   if p.get("ext") is not None), 2),
            "lines_priced": sum(1 for p in priced.values()
                                if p.get("ext") is not None),
            "lines_unpriced": unpriced, "short_stock": short}


def write(bom_full: list[dict], out_dir: Path, ws_name: str,
          parts_dir: Path | None = None, boards: int = 1,
          lookup_prices: bool = True, transport=None) -> dict:
    """Write the two distributor BOMs (and the cost file when priced).
    Returns a report dict whose `warnings` the caller prints on stderr."""
    if boards < 1:
        raise ValueError(f"boards must be >= 1, got {boards}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = build_lines(bom_full, lcsc_index(parts_dir), boards)
    warnings: list[str] = []
    no_mpn = [",".join(ln["refs"]) or ln["comment"] for ln in lines
              if not ln["mpn"]]
    if no_mpn:
        warnings.append("no MPN (left blank in the distributor BOMs): "
                        + "; ".join(no_mpn))

    priced: dict[str, dict | None] = {}
    for prov in distributors.PROVIDERS:
        if lookup_prices:
            priced[prov], w = price_lines(prov, lines, transport)
            warnings += w
        else:
            priced[prov] = None
            warnings.append(f"{prov}: prices not looked up (lookup off)")

    paths = {}
    for prov in distributors.PROVIDERS:
        p = out_dir / f"{ws_name}_BOM_{prov}.csv"
        _write_csv(p, DIGIKEY_FIELDS if prov == "digikey" else MOUSER_FIELDS,
                   _rows(prov, lines, priced[prov]))
        paths[prov] = str(p)

    cost_path = None
    if any(v is not None for v in priced.values()):
        totals = {prov: _total(lines, priced[prov])
                  for prov in distributors.PROVIDERS}
        done = {k: v["total_usd"] for k, v in totals.items() if v["looked_up"]}
        cost = {"board": ws_name, "boards": boards, "currency": "USD",
                "lines": len(lines), **totals,
                "cheaper": (min(done, key=done.get) if len(done) == 2
                            else None)}
        cost_path = out_dir / f"{ws_name}_BOM_cost.json"
        cost_path.write_text(json.dumps(cost, indent=1) + "\n",
                             encoding="utf-8")

    return {"digikey": paths["digikey"], "mouser": paths["mouser"],
            "cost": str(cost_path) if cost_path else None,
            "boards": boards, "lines": len(lines),
            "priced": {k: v is not None for k, v in priced.items()},
            "missing_mpn": no_mpn, "warnings": warnings}


def default_ws_name(out_dir: Path, fallback: str) -> str:
    """The workspace dir name when out_dir is a workspace's fab/."""
    d = Path(out_dir).resolve()
    return d.parent.name if d.name == "fab" else fallback


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bom-full", required=True, help="BOM-full.csv to read")
    ap.add_argument("--out-dir", required=True, help="where the files go")
    ap.add_argument("--ws-name", help="file prefix (default: the workspace "
                    "dir that holds BOM-full.csv's fab/)")
    ap.add_argument("--parts-dir", help="LCSC data for blank MPNs (default: "
                    "the workspace's parts/)")
    ap.add_argument("--boards", type=int, default=1,
                    help="multiply per-board quantities (default 1)")
    ap.add_argument("--no-price-lookup", action="store_true",
                    help="skip the distributor APIs even with keys set")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)
    src = Path(args.bom_full)
    try:
        parts_dir = (Path(args.parts_dir) if args.parts_dir
                     else src.resolve().parent.parent / "parts")
        rep = write(read_bom_full(src), Path(args.out_dir),
                    args.ws_name or default_ws_name(src.parent, src.stem),
                    parts_dir=parts_dir, boards=args.boards,
                    lookup_prices=not args.no_price_lookup)
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        rep = {"script": "distributor_bom", "status": "error",
               "error": f"{type(exc).__name__}: {exc}"}
        code = 2
    else:
        rep = {"script": "distributor_bom", "status": "pass", **rep}
        for w in rep["warnings"]:
            print(f"distributor_bom: {w}", file=sys.stderr)
        code = 0
    text = json.dumps(rep, indent=1)
    (Path(args.out).write_text(text, encoding="utf-8") if args.out
     else print(text))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
