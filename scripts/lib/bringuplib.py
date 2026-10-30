"""bringuplib.py - what a board's bring-up measured, and what that makes it.

A board in hand is brought up by measuring it: rails, currents, clocks, the
board's own self-test, its outputs into a load, its temperature at a stated
power. The close of bring-up records those numbers, not a bare word, so
`bring-up-passed` (attest's disposition) and `proven` (knowledge.py --prove)
always cite a measurement. This module reads a results file, checks it, and
says which bring-up event it supports.

A results file (JSON, kept in the workspace, e.g. `bringup/results.json`):

    {"schema": "hwde-bringup/1",
     "board": "lite-main",              # the workspace's board
     "unit": "240ac4123456",            # optional: which board in hand
     "date": "2026-11-05",
     "how": "remote",                   # optional: remote | bench | mixed
     "items": [
       {"name": "+3V3 at idle", "measured": 3.31, "unit": "V",
        "limit": {"min": 3.2, "max": 3.4}, "verdict": "pass",
        "source": "self",               # optional: self | bench | by-hand
        "evidence": "bringup/2026-11-05/session.json"},
       {"name": "THD+N at 1 W, 4 ohm", "measured": 0.02, "unit": "%",
        "limit": {"max": 0.05}, "verdict": "pass"},
       {"name": "power into 4 ohm at 1% THD+N", "measured": 38, "unit": "W",
        "limit": {"min": 45}, "verdict": "derated",
        "note": "governed to 38 W: the charger's budget, not the amp"},
       {"name": "Ethernet iperf", "verdict": "skip", "optional": true,
        "reason": "no cable at the person's home"}]}

Each item:
  name      what was measured (unique in the file)
  verdict   pass | fail | derated | skip
  measured  a number (with `unit`) or, for a by-hand check, a short text
            ("plays", "y"); required unless the verdict is skip
  limit     {"min", "max", "nom"} (numbers, any of them) or a text the
            reading was held to; required unless the verdict is skip
  note      required for derated: what the board is derated to
  reason    required for skip; `optional: true` says the board can pass
            without it
  evidence  optional path to the raw data (relative to the workspace)

A numeric reading held to numeric limits must agree with its verdict: pass
lies inside, fail lies outside (derated may lie either side; the note says
why). The file's outcome, worst first:

  failed      any fail                       -> event bringup_failed
  derated     any derated                    -> event bringup_derated
  incomplete  a skip not marked optional, or no measured item at all
              (supports no event: measure it, or say it is optional)
  passed      every required item measured and inside -> bringup_passed

`from_session()` reads a bench session in the shape hifi-receiver-2's
bench/ writes (`selftests[].items`, a `verdicts` map, a by-hand
`checklist`), so a project's own bench can feed the close unchanged; a mock
session is refused (it measured no board).

Toolchain-free (stdlib only).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "hwde-bringup/1"
VERDICTS = ("pass", "fail", "derated", "skip")
EVENTS = {"passed": "bringup_passed", "derated": "bringup_derated",
          "failed": "bringup_failed"}
BRINGUP_EVENTS = tuple(EVENTS.values())
DEFAULT_REL = "bringup/results.json"


class BringupError(ValueError):
    """The results file cannot be read or is not a results file."""


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def inside(measured, limit) -> bool | None:
    """True/False when a number is held to numeric limits; None when either
    side is not numeric (a text limit is the reviewer's to read)."""
    if not _num(measured) or not isinstance(limit, dict):
        return None
    lo, hi = limit.get("min"), limit.get("max")
    if not _num(lo) and not _num(hi):
        return None
    return (not _num(lo) or measured >= lo) and (not _num(hi) or measured <= hi)


def check(results: dict) -> list[str]:
    """Every problem with a results document; [] when it is well formed."""
    probs: list[str] = []
    if not isinstance(results, dict):
        return ["results must be a JSON object"]
    if results.get("schema") != SCHEMA:
        probs.append(f"schema must be {SCHEMA!r}, not {results.get('schema')!r}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(results.get("date") or "")):
        probs.append("date must be YYYY-MM-DD (the day it was measured)")
    items = results.get("items")
    if not isinstance(items, list) or not items:
        return probs + ["items must be a non-empty list"]
    seen: set[str] = set()
    for i, it in enumerate(items):
        where = f"items[{i}]"
        if not isinstance(it, dict):
            probs.append(f"{where} is not an object")
            continue
        name = it.get("name")
        if not isinstance(name, str) or not name.strip():
            probs.append(f"{where} has no name")
        else:
            where = f"item {name!r}"
            if name in seen:
                probs.append(f"{where} is listed twice")
            seen.add(name)
        v = it.get("verdict")
        if v not in VERDICTS:
            probs.append(f"{where}: verdict {v!r} is not one of {', '.join(VERDICTS)}")
            continue
        if v == "skip":
            if not str(it.get("reason") or "").strip():
                probs.append(f"{where}: a skip needs a reason")
            continue
        m, lim = it.get("measured"), it.get("limit")
        if m is None or (isinstance(m, str) and not m.strip()):
            probs.append(f"{where}: {v} without a measured value")
        elif not (_num(m) or isinstance(m, str)):
            probs.append(f"{where}: measured must be a number or a short text")
        if lim is None or lim == {} or (isinstance(lim, str) and not lim.strip()):
            probs.append(f"{where}: {v} without a limit it was held to")
        elif isinstance(lim, dict):
            bad = [k for k in lim if k not in ("min", "max", "nom")]
            if bad or not all(_num(x) for x in lim.values()):
                probs.append(f"{where}: limit takes numeric min/max/nom only")
        elif not isinstance(lim, str):
            probs.append(f"{where}: limit must be {{min,max,nom}} or a text")
        if v == "derated" and not str(it.get("note") or "").strip():
            probs.append(f"{where}: derated needs a note saying to what")
        ok = inside(m, lim)
        if v == "pass" and ok is False:
            probs.append(f"{where}: verdict pass, but {m} lies outside {lim}")
        if v == "fail" and ok is True:
            probs.append(f"{where}: verdict fail, but {m} lies inside {lim} - "
                         "say what failed, or mark it pass")
    return probs


def outcome(results: dict) -> dict:
    """{outcome, event, counts, failed, derated, unmeasured} for a
    well-formed document (run check() first)."""
    items = results.get("items") or []
    by = {v: [it["name"] for it in items if it.get("verdict") == v]
          for v in VERDICTS}
    unmeasured = [it["name"] for it in items if it.get("verdict") == "skip"
                  and not it.get("optional")]
    measured = len(items) - len(by["skip"])
    if by["fail"]:
        out = "failed"
    elif by["derated"]:
        out = "derated"
    elif unmeasured or not measured:
        out = "incomplete"
    else:
        out = "passed"
    return {"outcome": out, "event": EVENTS.get(out),
            "counts": {v: len(by[v]) for v in VERDICTS},
            "failed": by["fail"], "derated": by["derated"],
            "unmeasured": unmeasured}


def load(path: Path | str) -> dict:
    """A results document from a file: the hwde schema as is, or a bench
    session converted by from_session()."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BringupError(f"{p}: cannot read results: {e}") from e
    if isinstance(data, dict) and data.get("schema") is None \
            and ("selftests" in data or "verdicts" in data):
        return from_session(data, evidence=p.as_posix())
    return data


def sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_VERDICT_WORD = re.compile(r"^\s*(pass|fail|derated|skip)\b\s*:?\s*(.*)$", re.I)


def from_session(s: dict, evidence: str = "") -> dict:
    """A bench session (hifi-receiver-2 bench/ session.json shape) as a
    results document. Self-test items keep their value and limit; each
    `verdicts` entry not already a self-test summary becomes an item whose
    limit is its own name (the bench writes the limit there, e.g. "power
    into 4 ohm (1% THD+N >= 90% of 50 W)"); by-hand checklist answers
    y/n/skip are pass/fail/skip."""
    if s.get("mock"):
        raise BringupError("a mock session measured no board - run it on the "
                           "board in hand")
    date = str(s.get("started") or s.get("date") or "")[:10]
    items: list[dict] = []
    names: set[str] = set()

    def add(it: dict) -> None:
        base, n = it["name"], 2
        while it["name"] in names:
            it["name"] = f"{base} ({n})"
            n += 1
        names.add(it["name"])
        if evidence:
            it.setdefault("evidence", evidence)
        items.append(it)

    for st in s.get("selftests") or []:
        stage = st.get("stage") or "self-test"
        for i in st.get("items") or []:
            it = {"name": f"{stage}: {i.get('name')}", "source": "self",
                  "measured": i.get("value") if i.get("value") not in (None, "")
                  else ("ok" if i.get("pass") else "not ok"),
                  "limit": i.get("limit") if i.get("limit") not in (None, "")
                  else "self-test pass"}
            if i.get("pass"):
                it["verdict"] = "pass"
            elif i.get("expected"):
                it.update(verdict="skip", optional=True,
                          reason=f"expected not to pass at stage {stage!r}")
            else:
                it["verdict"] = "fail"
            if isinstance(it["limit"], (int, float)):
                it["limit"] = str(it["limit"])
            add(it)
    for name, text in (s.get("verdicts") or {}).items():
        if name.startswith("self-test"):
            continue            # summarised the self-test items above
        m = _VERDICT_WORD.match(str(text))
        word, rest = (m.group(1).lower(), m.group(2)) if m else ("skip", str(text))
        it = {"name": name, "source": "bench", "verdict": word}
        if word == "skip":
            it["reason"] = rest or "no verdict recorded"
        else:
            it.update(measured=rest or word, limit=name)
        add(it)
    for name, ans in (s.get("checklist") or {}).items():
        a = str(ans).strip().lower()
        it = {"name": name, "source": "by-hand"}
        if a in ("y", "yes", "pass", "true"):
            it.update(verdict="pass", measured="y", limit="works")
        elif a in ("n", "no", "fail", "false"):
            it.update(verdict="fail", measured="n", limit="works")
        else:
            it.update(verdict="skip", reason=f"answered {ans!r}")
        add(it)
    return {"schema": SCHEMA, "board": s.get("board") or "",
            "unit": (s.get("info") or {}).get("mac") or "",
            "date": date, "how": "bench", "items": items,
            "converted_from": "bench session"}


def summary(path: Path | str, ws: Path | str | None = None) -> dict:
    """check + outcome for a file, with the path (workspace-relative when
    it lies inside ws) and its sha256 - what the history event records."""
    p = Path(path)
    if ws is not None and not p.is_absolute():
        p = Path(ws) / p
    if not p.is_file():
        raise BringupError(f"no results file at {p}")
    doc = load(p)
    probs = check(doc)
    rel = p.as_posix()
    if ws is not None:
        try:
            rel = p.resolve().relative_to(Path(ws).resolve()).as_posix()
        except ValueError:
            pass
    out = {"results": rel, "results_sha256": sha256(p), "problems": probs,
           "board": doc.get("board") if isinstance(doc, dict) else None,
           "date": doc.get("date") if isinstance(doc, dict) else None}
    if not probs:
        out.update(outcome(doc))
    return out


def latest_event(history: list) -> dict | None:
    """The last bring-up event (passed, derated or failed) in a state
    history; a later one supersedes an earlier (a reworked board)."""
    last = None
    for h in history or []:
        if isinstance(h, dict) and h.get("event") in BRINGUP_EVENTS:
            last = h
    return last


def evidence_problems(ws: Path | str, ev: dict) -> list[str]:
    """Why a recorded bring-up event is not evidence: it names no results
    file, the file is gone, or its bytes changed since it was recorded."""
    rel = ev.get("results")
    if not rel:
        return [f"{ev.get('event')} at {ev.get('ts')} names no results file "
                "(a bare event: log it again with state.py bringup --results)"]
    p = Path(ws) / rel
    if not p.is_file():
        return [f"{ev.get('event')} names {rel}, which is not in the workspace"]
    if ev.get("results_sha256") and sha256(p) != ev["results_sha256"]:
        return [f"{rel} changed since {ev.get('event')} was recorded"]
    return []
