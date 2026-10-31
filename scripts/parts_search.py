#!/usr/bin/env python
"""parts_search.py - LCSC/JLCPCB parametric part search (SPEC.md P3, 6.1).

Returns a ranked JSON candidate list (Basic-first, then stock, then price) for a
keyword query, with parametric filters. Primary source is the anonymous JLCPCB
parts search (no credential); a cached jlcparts SQLite DB is an offline fallback
(--db); web search is the agent's last resort (signalled by exit 2 when the live
endpoint is unreachable and no --db is given).

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2.
  0 = query ran (results present OR a genuine no-match against a reachable API)
  2 = error (endpoint unreachable with no usable cache, bad db, internal error)
(There is no exit-1 "violations" notion for a search.)

A part known only by what it is (an RCA jack, a TOSLINK receiver, a 48 V
USB-C receptacle) goes through --kind: JLC's keyword search matches part
numbers, so those words find nothing; a kind (reference/part_kinds.yaml) names
the JLC category it sits in and how to pick it out there, and the search
browses that category, in stock only, instead. --category NAME browses any
JLC category the same way; --attr / --attr-min filter it by attribute.

Examples:
  parts_search.py --kind rca-jack
  parts_search.py --kind usb-c-48v --limit 5
  parts_search.py --kind banana-socket --query red
  parts_search.py --category "USB Connectors" --attr "Connector Type=Type-C" --attr-min "Current Rating-Power=5"
  parts_search.py --list-kinds
  parts_search.py --query "STM32F103C8T6"
  parts_search.py --query "100nF 0603 X7R" --basic-only --min-stock 5000 --limit 5
  parts_search.py --query "10k 0603" --filters package=0603 type=basic --db cache.sqlite3
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import partslib  # noqa: E402

KINDS_FILE = Path(__file__).resolve().parent.parent / "reference" / "part_kinds.yaml"
_BROWSE_PAGE = 1000  # the endpoint serves up to 1000 rows a page

_FILTER_KEYS = {"package", "type", "min_stock", "max_price", "brand", "contains"}

# JLC placeholder rows: generic ~$0.04 "JLCPCB Assembly" entries with no real
# brand/datasheet that masquerade as in-stock hits (S14 finding). Both
# conditions required, so a real-brand row with a missing datasheet URL
# survives (e.g. the Tokmas LM5069 row in the lumina-strobe sweep).
_PLACEHOLDER_BRANDS = {"", "jlcpcb", "jlcpcb assembly"}

_VAL_SUFFIX = re.compile(r"^(\d+(?:\.\d+)?)([kKmMrR])$")
_VAL_INFIX = re.compile(r"^(\d+)([kKmMrR])(\d+)$")
_UNIT_WORD = {"k": "kohm", "m": "Mohm", "r": "ohm"}

_EMPTY_HINT = ("zero rows for value tokens like 10K is a known JLC search "
               "quirk, not proof of a stock-out - retry with an exact MPN or "
               "a spelled-out value")


def _normalize_value_query(query: str) -> str | None:
    """Spell out resistor-style value tokens (10K -> '10 kohm', 4R7 ->
    '4.7 ohm'); None when nothing matched. The JLC search silently returns
    zero rows for the shorthand forms (LEARNINGS 2026-07-28 [parts])."""
    out, changed = [], False
    for tok in query.split():
        m = _VAL_SUFFIX.match(tok)
        if m:
            out.append(f"{m.group(1)} {_UNIT_WORD[m.group(2).lower()]}")
            changed = True
            continue
        m = _VAL_INFIX.match(tok)
        if m:
            out.append(f"{m.group(1)}.{m.group(3)} {_UNIT_WORD[m.group(2).lower()]}")
            changed = True
            continue
        out.append(tok)
    return " ".join(out) if changed else None


def _split_placeholders(parts: list[dict]) -> tuple[list[dict], int]:
    keep, dropped = [], 0
    for p in parts:
        if p["brand"].strip().lower() in _PLACEHOLDER_BRANDS and not p["datasheet"]:
            dropped += 1
        else:
            keep.append(p)
    return keep, dropped


_PICK_PACKAGES = {"0402", "0603", "0805"}


# A passive's value: number, SI prefix, unit (4.7 ohm, 4.7kΩ, 100nF, 2.2 uH).
# The prefix is case-sensitive so mohm/mΩ (milli) and Mohm/MΩ (mega) differ.
_PASSIVE_VAL = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*([pnuµμmkKMG]?)\s*([oO][hH][mM][sS]?|\u2126|\u03a9|F|H)"
    r"(?![A-Za-z])")
_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
           "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
_VALUE_ATTRS = {"ohm": "resistance", "F": "capacitance", "H": "inductance"}


def _passive_values(text: str) -> list[tuple[float, str]]:
    """Every (value, unit) a text states: '4.7Ω ±1% 0603' -> [(4.7, 'ohm')]."""
    out = []
    for num, pre, unit in _PASSIVE_VAL.findall(text):
        u = unit if unit in ("F", "H") else "ohm"
        out.append((float(num) * _PREFIX[pre], u))
    return out


def _query_value(query: str) -> tuple[float, str] | None:
    """The one value a passive query asks for (shorthand 4R7/10K spelled out
    first); None when it names none, or several."""
    vals = set(_passive_values(_normalize_value_query(query) or query))
    return vals.pop() if len(vals) == 1 else None


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b))


def _has_value(p: dict, want: tuple[float, str]) -> bool:
    """The row is of the wanted value: its value attribute when it has one,
    else its description. JLC's keyword search is fuzzy, so '4.7 ohm' also
    returns 47 Ω rows (issue #384)."""
    num, unit = want
    attr = [a for a in p.get("attributes") or []
            if str(a.get("name", "")).lower() == _VALUE_ATTRS[unit]]
    texts = [str(a.get("value", "")) for a in attr] or [p.get("description", "")]
    return any(u == unit and _same(v, num)
               for t in texts for v, u in _passive_values(t))


def _pick_basic_passive(parts: list[dict], qty: int,
                        want: tuple[float, str] | None = None) -> dict | None:
    """part-sourcer rules 1-2, deterministically: JLC Basic (no feeder fee),
    standard package, stock >= 5x build qty, of the value asked for; cheapest
    conforming row wins."""
    conforming = [p for p in parts
                  if p["basic"] and p["package"] in _PICK_PACKAGES
                  and p["stock"] >= 5 * qty
                  and (want is None or _has_value(p, want))]
    conforming.sort(key=lambda p: (p["price"] is None,
                                   p["price"] if p["price"] is not None else 0.0,
                                   -p["stock"]))
    return conforming[0] if conforming else None


def _parse_filters(pairs: list[str]) -> dict:
    """`--filters package=0603 type=basic` -> dict, validated against known keys."""
    out: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise partslib.PartsError(f"bad --filters item '{pair}' (want key=value)")
        key, val = pair.split("=", 1)
        key = key.strip()
        if key not in _FILTER_KEYS:
            raise partslib.PartsError(
                f"unknown filter '{key}'; known: {sorted(_FILTER_KEYS)}")
        out[key] = val.strip()
    return out


def _merge_filter_args(args, extra: dict) -> dict:
    """Combine explicit flags with --filters items (flags win when both set)."""
    f = dict(extra)
    if args.package is not None:
        f["package"] = args.package
    if args.basic_only:
        f["type"] = "basic"
    if args.min_stock is not None:
        f["min_stock"] = str(args.min_stock)
    if args.max_price is not None:
        f["max_price"] = str(args.max_price)
    if args.brand is not None:
        f["brand"] = args.brand
    if args.contains is not None:
        f["contains"] = args.contains
    return f


def load_kinds(path: Path = KINDS_FILE) -> dict:
    try:
        kinds = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise partslib.PartsError(f"cannot read part kinds {path}: {exc}") from exc
    return kinds


def resolve_kind(name: str, kinds: dict) -> tuple[str, dict]:
    """A kind name or alias -> (kind, its spec)."""
    want = name.strip().lower().replace(" ", "-").replace("_", "-")
    for kind, spec in kinds.items():
        if want == kind or want in [a.lower() for a in spec.get("aliases") or []]:
            return kind, spec
    raise partslib.PartsError(
        f"unknown kind '{name}'; known: {sorted(kinds)} (aliases in "
        f"{KINDS_FILE.name}). For anything else browse its JLC category with "
        "--category NAME, or add a kind to the file.")


def _attr_args(items: list[str], numeric: bool) -> dict:
    """['Name=v1|v2'] -> {'Name': ['v1', 'v2']}; numeric: {'Name': float}."""
    out: dict = {}
    for item in items or []:
        name, sep, val = item.partition("=")
        if not sep or not name.strip() or not val.strip():
            raise partslib.PartsError(f"bad attribute filter '{item}' (want Name=value)")
        if numeric:
            try:
                out[name.strip()] = float(val)
            except ValueError as exc:
                raise partslib.PartsError(
                    f"bad --attr-min '{item}': want a plain number in the "
                    "attribute's base unit (48 for 48V, 0.5 for 500mA)") from exc
        else:
            out[name.strip()] = [v.strip() for v in val.split("|") if v.strip()]
    return out


def _attr_text(p: dict) -> str:
    return " ".join([p["mpn"], p["description"]] +
                    [str(a.get("value", "")) for a in p["attributes"]])


def _source_match(p: dict, src: dict, mins: dict, words: list[str]) -> bool:
    if src.get("mpn") and not re.match(src["mpn"], p["mpn"], re.I):
        return False
    text = _attr_text(p)
    if src.get("text") and not re.search(src["text"], text, re.I):
        return False
    if src.get("not_text") and re.search(src["not_text"], text, re.I):
        return False
    for name, lo in mins.items():
        v = partslib.attr_max(p, name)
        if v is None or v < float(lo):
            return False
    low = text.lower()
    return all(w in low for w in words)


def browse(sources: list[dict], *, words: list[str], attrs: dict, mins: dict,
           in_stock: bool, part_type: str | None, max_rows: int
           ) -> tuple[list[dict], list[dict]]:
    """Browse each source's JLC category and keep what matches it ->
    (normalized parts, one summary per source). Network errors propagate."""
    parts: list[dict] = []
    seen: set[str] = set()
    summary = []
    for src in sources:
        server = {**(src.get("attrs") or {}), **attrs}
        smins = {**(src.get("min") or {}), **mins}
        fetched, total, page = 0, 0, 1
        while fetched < max_rows:
            rows, total = partslib.category_search(
                src["category"], attrs=server, in_stock=in_stock,
                part_type=part_type, page=page, page_size=_BROWSE_PAGE)
            for row in rows:
                p = partslib.normalize(row)
                if p["lcsc"] not in seen and _source_match(p, src, smins, words):
                    seen.add(p["lcsc"])
                    parts.append(p)
            fetched += len(rows)
            if not rows or fetched >= total:
                break
            page += 1
        summary.append({"category": src["category"], "total": total,
                        "fetched": fetched, "truncated": fetched < total})
    return parts, summary


def search(args) -> dict:
    filters = _merge_filter_args(args, _parse_filters(args.filters))
    ftype = (filters.get("type") or "").lower()
    basic_only = ftype == "basic"
    part_type = {"basic": "base", "extended": "expand"}.get(ftype)
    min_stock = int(filters["min_stock"]) if "min_stock" in filters else 0
    max_price = float(filters["max_price"]) if "max_price" in filters else None

    if args.kind or args.category:
        return _search_kind(args, filters, basic_only, part_type, min_stock, max_price)

    # Clear error before any network call. With --db the cache still works
    # without the live-search dependencies, so skip live search and say why.
    notes: list[str] = []
    net_errors: tuple = (OSError,)
    try:
        net_errors = (OSError, partslib.require_httpx().HTTPError)
    except partslib.PartsError as exc:
        if not args.db:
            raise
        notes.append(f"live search skipped, using --db: {exc}")

    # Fetch a generous window so filters + ranking have material to work on.
    window = max(args.limit * 4, 25)
    source = "live"
    total = 0
    raw: list[dict] = []

    def _live(q: str) -> tuple[list[dict], int]:
        if notes:  # a dependency is missing; the cache answers instead
            return [], 0
        try:
            return partslib.live_search(
                q, page=args.page, page_size=window, part_type=part_type)
        except partslib.PartsError as exc:
            if not args.db:
                raise
            notes.append(f"live search skipped, using --db: {exc}")
            return [], 0
        except net_errors:  # network failure; anything else is a bug and surfaces
            return [], 0

    raw, total = _live(args.query)

    # Value tokens like 10K silently return zero rows; retry once spelled out
    # so a bad query is not mistaken for a stock-out (ladder row 100).
    query_retried = None
    if not raw:
        norm_q = _normalize_value_query(args.query)
        if norm_q:
            query_retried = norm_q
            raw, total = _live(norm_q)

    if not raw:
        # Empty live result: cached DB > web-search hint. Disambiguate offline.
        if args.db:
            source = "db"
            raw_norm = partslib.db_search(args.db, args.query, limit=window)
            parts = raw_norm  # already normalized
        elif not partslib.endpoint_reachable():
            raise partslib.PartsError(
                "JLCPCB search endpoint unreachable and no --db cache given. "
                "Fix: run with network, pass --db <jlcparts cache.sqlite3>, or "
                "fall back to web search for the MPN.")
        else:
            # Reachable but genuinely no matches - not an error.
            payload = {
                "script": "parts_search", "status": "empty", "source": source,
                "query": args.query, "total": 0, "count": 0, "results": [],
                "filters": filters, "placeholders_filtered": 0,
                "hint": _EMPTY_HINT,
            }
            if query_retried:
                payload["query_retried"] = query_retried
            return payload
    else:
        parts = [partslib.normalize(it) for it in raw]

    dropped = 0
    if not args.include_placeholders:
        parts, dropped = _split_placeholders(parts)
    parts = partslib.apply_filters(
        parts, basic_only=basic_only, package=filters.get("package"),
        min_stock=min_stock, max_price=max_price, brand=filters.get("brand"),
        contains=filters.get("contains"))
    parts.sort(key=partslib.rank_key)
    if args.pick == "basic-passive":
        sel = _pick_basic_passive(parts, args.qty, _query_value(args.query or ""))
        parts = [sel] if sel is not None else []
    top = parts[: args.limit]
    for i, p in enumerate(top):
        p["rank"] = i
    payload = {
        "script": "parts_search",
        "status": ("pass" if top else
                   ("no_candidate" if args.pick else "empty")),
        "source": source,
        "query": args.query,
        "total": total,
        "count": len(top),
        "results": top,
        "filters": filters,
        "placeholders_filtered": dropped,
    }
    if args.pick:
        payload["pick"] = args.pick
        if not top:
            payload["hint"] = (
                "no conforming Basic passive (Basic + 0402/0603/0805 + stock "
                f">= 5x qty {args.qty} + the query's value): a stock-out of the standard form, "
                "not a bad query - pick by judgment")
    if query_retried:
        payload["query_retried"] = query_retried
    if notes:
        payload["note"] = notes[0]
    return payload


def _search_kind(args, filters: dict, basic_only: bool, part_type: str | None,
                 min_stock: int, max_price: float | None) -> dict:
    """--kind / --category: browse JLC categories instead of keyword search."""
    if args.pick:
        raise partslib.PartsError("--pick works on a keyword search, not --kind/--category")
    if args.kind:
        kind, spec = resolve_kind(args.kind, load_kinds())
        sources = [dict(s) for s in spec.get("sources") or []]
        if not sources:
            raise partslib.PartsError(f"kind '{kind}' has no sources in {KINDS_FILE.name}")
    else:
        kind, spec = None, {}
        sources = [{"category": args.category}]
    words = (args.query or "").lower().split()
    attrs, mins = _attr_args(args.attr, False), _attr_args(args.attr_min, True)
    httpx = partslib.require_httpx()
    try:
        parts, summary = browse(
            sources, words=words, attrs=attrs, mins=mins,
            in_stock=not args.any_stock, part_type=part_type,
            max_rows=args.max_rows)
    except (OSError, httpx.HTTPError, ValueError) as exc:
        # ValueError: a body that isn't JSON
        raise partslib.PartsError(
            f"JLC category browse failed ({type(exc).__name__}: {exc}). Fix: "
            "run with network, or web-search the kind for MPNs and look them "
            "up with --query.") from exc

    dropped = 0
    if not args.include_placeholders:
        parts, dropped = _split_placeholders(parts)
    parts = partslib.apply_filters(
        parts, basic_only=basic_only, package=filters.get("package"),
        min_stock=min_stock, max_price=max_price, brand=filters.get("brand"),
        contains=filters.get("contains"))
    parts.sort(key=partslib.rank_key)
    top = parts[: args.limit]
    for i, p in enumerate(top):
        p["rank"] = i
    payload = {
        "script": "parts_search",
        "status": "pass" if top else "empty",
        "source": "live-category",
        "kind": kind,
        "query": args.query,
        "total": len(parts),
        "count": len(top),
        "results": top,
        "filters": filters,
        "attrs": args.attr or [],
        "attr_min": args.attr_min or [],
        "in_stock_only": not args.any_stock,
        "sources": summary,
        "placeholders_filtered": dropped,
    }
    if kind:
        payload["what"] = spec.get("what", "")
    if not top:
        payload["hint"] = (
            "nothing in stock matched in " +
            ", ".join(s["category"] for s in summary) +
            ": try --any-stock, loosen --attr/--attr-min/--query, or check "
            "the kind's sources in reference/part_kinds.yaml (a category "
            "name JLC doesn't know browses as empty)")
    if any(s["truncated"] for s in summary):
        payload["note"] = (f"stopped after --max-rows {args.max_rows} rows of a "
                           "category; narrow it with --attr or raise --max-rows")
    return payload


def main(argv: list[str] | None = None) -> int:
    partslib.utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--query", help="keyword search (MPN, value, package, ...); "
                    "with --kind/--category, words every result must contain")
    ap.add_argument("--kind", help="find by what the part is (rca-jack, "
                    "banana-socket, toslink-receiver, usb-c-48v, ...; "
                    "--list-kinds): browses its JLC category")
    ap.add_argument("--category", help="browse this JLC category (a result "
                    "row's 'category', e.g. 'RCA Connector')")
    ap.add_argument("--attr", action="append", default=[],
                    help="with --kind/--category: 'Name=v1|v2', exact "
                         "attribute values, filtered by JLC (repeatable)")
    ap.add_argument("--attr-min", action="append", default=[],
                    help="with --kind/--category: 'Name=N', the attribute's "
                         "largest stated quantity >= N in base units "
                         "('Voltage Rating=48', 'Current Rating=0.5')")
    ap.add_argument("--any-stock", action="store_true",
                    help="with --kind/--category: include out-of-stock parts")
    ap.add_argument("--max-rows", type=int, default=5000,
                    help="with --kind/--category: rows to read per category "
                         "(default 5000)")
    ap.add_argument("--list-kinds", action="store_true",
                    help="print the known kinds and exit")
    ap.add_argument("--limit", type=int, default=10, help="max ranked results (default 10)")
    ap.add_argument("--page", type=int, default=1, help="live-search page (default 1)")
    ap.add_argument("--basic-only", action="store_true", help="JLC Basic parts only")
    ap.add_argument("--package", help="require this package substring (e.g. 0603)")
    ap.add_argument("--min-stock", type=int, help="require stock >= N")
    ap.add_argument("--max-price", type=float, help="require unit price <= N")
    ap.add_argument("--brand", help="require this brand substring")
    ap.add_argument("--contains", help="require this substring in description/MPN")
    ap.add_argument("--filters", nargs="*", default=[],
                    help="key=value filters: " + " ".join(sorted(_FILTER_KEYS)))
    ap.add_argument("--pick", choices=["basic-passive"],
                    help="deterministic auto-pick: Basic + 0402/0603/0805 + "
                         "stock >= 5x --qty, cheapest conforming row")
    ap.add_argument("--qty", type=int, default=1,
                    help="build quantity for the --pick stock rule (default 1)")
    ap.add_argument("--include-placeholders", action="store_true",
                    help="keep JLC placeholder rows (no-brand/no-datasheet "
                         "~$0.04 entries) that are excluded by default")
    ap.add_argument("--db", help="jlcparts SQLite cache for offline fallback")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)
    if args.list_kinds:
        try:
            kinds = load_kinds()
        except partslib.PartsError as exc:
            return partslib.emit({"script": "parts_search", "status": "error",
                                  "error": str(exc)}, args.out)
        return partslib.emit({
            "script": "parts_search", "status": "pass", "kinds": {
                k: {"what": v.get("what", ""), "aliases": v.get("aliases") or [],
                    "categories": [s["category"] for s in v.get("sources") or []]}
                for k, v in kinds.items()}}, args.out)
    if not (args.query or args.kind or args.category):
        ap.error("one of --query, --kind or --category is required")
    try:
        payload = search(args)
    except partslib.PartsError as exc:
        payload = {"script": "parts_search", "status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - contract: any error -> exit 2
        payload = {"script": "parts_search", "status": "error",
                   "error": f"{type(exc).__name__}: {exc}"}
    return partslib.emit(payload, args.out)


if __name__ == "__main__":
    sys.exit(main())
