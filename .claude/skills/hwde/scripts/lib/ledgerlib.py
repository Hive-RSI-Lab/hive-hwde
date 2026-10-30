"""ledgerlib.py - what a board knows about the parts it relies on, and how.

The person, 10-28: "we need to catch stuff like this. know what we dont
know". A board workspace keeps `ledger.json`: one entry per fact the design
relies on, each `verified` (with its source and when), `failed` (a check
found it false) or `unknown` (nothing has checked it - including a check that
ran and could not tell). Checks write to it as they run; nothing is assumed.

Facts and who writes them:
  stock       per LCSC code on BOM.csv: JLC can sell what the build needs
              (stock_check.py, and bom_cpl.py's live run)
  part_match  per LCSC code: the code JLC will place is the BOM's part
              (stock_check.py)
  lifecycle   per LCSC code: JLC sells the code, nothing substitutes it
              (stock_check.py)
  rating      per refdes: every connected pin within its datasheet rating
              (check_ratings.py, run by the verify gate)
  pinout      per refdes with more than two pins: the board's pad numbers
              and names agree with the datasheet extraction's pinout
              (check_ratings.py)
  footprint   per placed refdes: the board footprint's pads land on the
              LCSC part's own model, by number (cpl_verify.py, run by dfm)
  Any fact can also be recorded by hand, with its source:
    ledger.py set --workspace WS --subject U8 --kind pinout --status verified
                  --source "CH340 datasheet v3B p.4, pin table vs symbol"

What the board relies on (`expected`) comes from its own data: every code on
fab/BOM.csv (else every smt_placed parts.json line), and every refdes of every
parts.json line that is bought (not dnp, board_feature or customer_supplied).
A fact expected but never written reads `unknown`. A recorded fact reads
`unknown` again when it went stale: stock older than STOCK_MAX_AGE_H, or a
fact about a refdes whose LCSC code has since changed.

Every kind is critical: release (attest.py build, and order-ready, which
re-verifies) refuses while any expected fact is not `verified`
(`blockers()`); `ledger.py report` lists them.

Writers only write into a real workspace (a state.json beside the board
folder) and never when HWDE_LEDGER_WRITE=0 (the test suite's default).
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import re
from pathlib import Path

LEDGER = "ledger.json"
SCHEMA = 1
KINDS = ("stock", "part_match", "lifecycle", "rating", "pinout", "footprint")
PART_KINDS = ("stock", "part_match", "lifecycle")   # subject: LCSC code
REF_KINDS = ("rating", "pinout", "footprint")       # subject: refdes
STATUSES = ("verified", "failed", "unknown")
STOCK_MAX_AGE_H = 72.0
UNBOUGHT = ("dnp", "board_feature", "customer_supplied")
# the classes the assembler fits from the upload (bom_cpl.ASSEMBLED_CLASSES)
ASSEMBLED = ("smt_placed", "tht")
# refdes prefixes of parts with a pin map worth checking (two-terminal
# passives, LEDs and diodes have no pinout beyond polarity, which
# cpl_verify and dfm's polarity check cover)
TWO_PIN = re.compile(r"^(R|C|L|FB|D|LED|F|TP|H|MH|FID)\d", re.I)
STOCK_SOURCE = ("JLC parts library record (jlcpcb.com smtGood search) and "
                "LCSC product detail (wmsc.lcsc.com)")


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None):
    try:
        t = _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def key(subject: str, kind: str) -> str:
    return f"{subject}/{kind}"


def path(ws: Path) -> Path:
    return Path(ws) / LEDGER


def load(ws: Path) -> dict:
    p = path(ws)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("facts"), dict):
                return data
        except (OSError, ValueError):
            pass
    return {"schema": SCHEMA, "facts": {}}


def writable(ws: Path | None) -> bool:
    return (ws is not None and (Path(ws) / "state.json").is_file()
            and os.environ.get("HWDE_LEDGER_WRITE", "1") != "0")


def workspace_of(pcb: Path) -> Path | None:
    """<ws> for <ws>/kicad/<board>.kicad_pcb when it is a real workspace."""
    ws = Path(pcb).resolve().parent.parent
    return ws if (ws / "state.json").is_file() else None


# ------------------------------------------------------- what is relied on

def _parts(ws: Path) -> list[dict]:
    pj = Path(ws) / "parts" / "parts.json"
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("parts", data) if isinstance(data, dict) else data
    return [p for p in items or [] if isinstance(p, dict)]


def _refs(line: dict) -> list[str]:
    r = line.get("refdes") or []
    return [r] if isinstance(r, str) else [str(x) for x in r]


def _class(line: dict, ref: str) -> str:
    rc = line.get("refdes_class") or {}
    if ref in rc:
        return rc[ref]
    if ref in (line.get("refdes_dnp") or []):
        return "dnp"
    return line.get("assembly_class") or "smt_placed"


def _bom_codes(ws: Path) -> dict[str, list[str]] | None:
    bom = Path(ws) / "fab" / "BOM.csv"
    if not bom.is_file():
        return None
    out: dict[str, list[str]] = {}
    with bom.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            code = (r.get("LCSC Part #") or r.get("LCSC") or "").strip()
            refs = [x.strip() for x in str(r.get("Designator") or "")
                    .split(",") if x.strip()]
            if refs:
                out.setdefault(code or f"(no LCSC: {refs[0]})", []).extend(refs)
    return out


def expected(ws: Path) -> dict[str, dict]:
    """key -> {subject, kind, refs, lcsc}: every fact the board relies on."""
    ws = Path(ws)
    out: dict[str, dict] = {}
    lines = _parts(ws)
    lcsc_of: dict[str, str] = {}
    codes = _bom_codes(ws)
    if codes is None:
        codes = {}
        for ln in lines:
            placed = [r for r in _refs(ln) if _class(ln, r) in ASSEMBLED]
            code = str(ln.get("lcsc") or "").strip()
            if placed:
                codes.setdefault(code or f"(no LCSC: {placed[0]})",
                                 []).extend(placed)
    for code, refs in codes.items():
        for kind in PART_KINDS:
            out[key(code, kind)] = {"subject": code, "kind": kind,
                                    "refs": refs, "lcsc": code}
    for ln in lines:
        code = str(ln.get("lcsc") or "").strip()
        for ref in _refs(ln):
            cls = _class(ln, ref)
            if cls in UNBOUGHT:
                continue
            lcsc_of[ref] = code
            kinds = ["rating"]
            if not TWO_PIN.match(ref):
                kinds.append("pinout")
            if cls in ASSEMBLED:
                kinds.append("footprint")
            for kind in kinds:
                out[key(ref, kind)] = {"subject": ref, "kind": kind,
                                       "refs": [ref], "lcsc": code}
    return out


# ----------------------------------------------------------------- writing

def put(ws: Path, facts: list[dict], by: str) -> Path | None:
    """Merge facts (subject, kind, status, value, source[, lcsc, refs]) into
    the ledger, stamping checked_at and by; also lists every expected fact
    not yet recorded as unknown, so the file itself shows the gaps."""
    if not writable(ws):
        return None
    data = load(ws)
    ts = now()
    for f in facts:
        if f.get("kind") not in KINDS or f.get("status") not in STATUSES:
            raise ValueError(f"bad fact {f!r}: kind one of {KINDS}, status "
                             f"one of {STATUSES}")
        ent = {k: f[k] for k in ("subject", "kind", "status", "value",
                                 "source", "lcsc", "refs") if k in f}
        ent.setdefault("value", "")
        ent.setdefault("source", "")
        ent["checked_at"] = f.get("checked_at") or ts
        ent["by"] = by
        data["facts"][key(f["subject"], f["kind"])] = ent
    for k, e in expected(ws).items():
        data["facts"].setdefault(k, {**e, "status": "unknown", "value": "",
                                     "source": "", "checked_at": None,
                                     "by": None})
    data["schema"] = SCHEMA
    data["updated"] = ts
    p = path(ws)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True,
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


def stock_facts(result: dict) -> list[dict]:
    """stock_check's rows -> stock, part_match and lifecycle facts."""
    out = []
    for r in result.get("rows") or []:
        code = r.get("lcsc") or f"(no LCSC: {(r.get('refs') or ['?'])[0]})"
        base = {"subject": code, "lcsc": code, "refs": r.get("refs") or [],
                "source": STOCK_SOURCE, "checked_at": r.get("checked_at")}
        jlc = r.get("jlc")
        if r["status"] == "unknown" and jlc is None:
            for kind in PART_KINDS:
                out.append({**base, "kind": kind, "status": "unknown",
                            "value": r.get("why", "")})
            continue
        if jlc is None:                                   # not_found
            for kind in PART_KINDS:
                out.append({**base, "kind": kind, "status": "failed",
                            "value": r.get("why", "")})
            continue
        avail, need = r.get("available"), r.get("need")
        out.append({**base, "kind": "stock",
                    "status": ("unknown" if avail is None else
                               "verified" if avail >= need else "failed"),
                    "value": f"JLC can sell {avail}, build needs {need}"
                             f" (JLC lists {jlc.get('stock')}, LCSC "
                             f"{(r.get('lcsc_record') or {}).get('stock')})"})
        m = r.get("match") or {}
        out.append({**base, "kind": "part_match",
                    "status": {True: "verified", False: "failed"}.get(
                        m.get("ok"), "unknown"),
                    "value": m.get("why", "")})
        life = "verified" if jlc.get("for_sale") else "failed"
        out.append({**base, "kind": "lifecycle", "status": life,
                    "value": ("JLC sells it" if life == "verified"
                              else r.get("why", ""))})
    return out


def record_stock(ws: Path, result: dict) -> Path | None:
    return put(ws, stock_facts(result), "stock_check")


# ----------------------------------------------------------------- reading

def effective(fact: dict | None, exp: dict | None, at=None) -> dict:
    """The fact as it reads now: stale and other-part facts read unknown."""
    at = at or _dt.datetime.now(_dt.timezone.utc)
    if fact is None:
        return {**(exp or {}), "status": "unknown", "value": "",
                "source": "", "checked_at": None, "why": "never checked"}
    out = dict(fact)
    if out.get("status") == "unknown":
        out.setdefault("why", out.get("value") or "never checked")
        return out
    if exp and exp.get("lcsc") and fact.get("lcsc") and \
            exp["lcsc"] != fact["lcsc"]:
        out.update(status="unknown",
                   why=f"checked for {fact['lcsc']}; the board now has "
                       f"{exp['lcsc']}")
    elif fact.get("kind") == "stock":
        t = _parse(fact.get("checked_at"))
        age = (at - t).total_seconds() / 3600 if t else None
        if age is None or age > STOCK_MAX_AGE_H:
            out.update(status="unknown",
                       why=f"stale: checked {fact.get('checked_at')}, stock "
                           f"is only good for {STOCK_MAX_AGE_H:.0f} h - "
                           "rerun stock_check.py")
    return out


def report(ws: Path, at=None) -> dict:
    """Every expected fact as it reads now, the gaps, and `ready`."""
    ws = Path(ws)
    data = load(ws)
    exp = expected(ws)
    facts = data["facts"]
    rows = []
    for k in sorted(set(exp) | set(facts)):
        if k not in exp:
            continue             # a fact about something no longer on board
        rows.append({"key": k, **effective(facts.get(k), exp[k], at)})
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    by_kind: dict[str, dict[str, int]] = {}
    for r in rows:
        d = by_kind.setdefault(r["kind"], {})
        d[r["status"]] = d.get(r["status"], 0) + 1
    failed = [r for r in rows if r["status"] == "failed"]
    unknown = [r for r in rows if r["status"] == "unknown"]
    return {"ledger": str(path(ws)), "exists": path(ws).is_file(),
            "ready": not failed and not unknown, "counts": counts,
            "by_kind": by_kind, "failed": failed, "unknown": unknown,
            "facts": rows}


def blockers(ws: Path, at=None, limit: int = 12) -> list[str]:
    """Release problems: every critical fact not verified, summarised."""
    rep = report(ws, at)
    out = []
    for status in ("failed", "unknown"):
        rows = rep[status]
        if not rows:
            continue
        by_kind: dict[str, list[str]] = {}
        for r in rows:
            by_kind.setdefault(r["kind"], []).append(r["subject"])
        for kind, subs in sorted(by_kind.items()):
            more = f" +{len(subs) - limit} more" if len(subs) > limit else ""
            out.append(f"ledger: {len(subs)} {kind} fact(s) {status} - "
                       f"{', '.join(subs[:limit])}{more} (ledger.py report "
                       "--workspace <ws>)")
    return out
