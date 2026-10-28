#!/usr/bin/env python
"""gen_cost.py - the model cost of generating a board, split by hwde step.

A board's guide and design doc say what it cost to generate: a total, and a
breakdown by hwde step where the records allow one. This script works that
out from the records a run leaves behind and writes it to reports/cost.json,
which guide_facts.py carries into the guide.

It reads, all read-only:
  Claude Code transcripts   ~/.claude/projects/<escaped worktree>/<id>.jsonl
                            plus <id>/subagents/*.jsonl: every model message
                            with its timestamp, model and token usage, and the
                            session's own `cost-state` total at its end. They
                            outlive the worktree and exist for a run that timed
                            out, which the loop log records with no cost.
  reference/model_prices.yaml  USD per million tokens, with its source.
  <ws>/state.json history   when hwde recorded each step (phase, gate and
                            human-checkpoint events; naive local timestamps).
  loop.log (--loop-log)     a headless loop's per-iteration cost, reconciled
                            against the round this call records.

How a message is attributed to a step. hwde records `phase` events when it
enters a phase, and `gate` / `human` events when a phase's exit gate or
checkpoint runs. Spend after a phase event goes to that phase; spend before a
gate or checkpoint goes to the phase that gate closes (erc -> schematic,
place -> placement, drc_routed -> routing, verify -> verification, dfm ->
DFM). Spend before the workspace existed goes to "before the workspace";
spend after the last record goes to the furthest phase reached. A round of
sessions with no hwde record inside its time span is NOT split: it keeps one
line, and `breakdown_reason` says why. Nothing is ever apportioned by guess;
the one estimate, below, only shares out a total that is known exactly.

Output Claude Code never wrote down. A streamed message is written once per
content block, and those records carry the usage of the stream's start (a
handful of output tokens). The final count comes with `stop_reason`, on a
closing record that background subagents mostly never write (PCB-0017-A: 384
of 444 Fable messages). One session's transcript shows 642 output tokens
where cost-state billed 99,849, while its input and cache tokens match to the
token. Cost-state's `modelUsage` holds each model's exact total, so the
missing output is added back to that model's messages with no closing
record, shared by their visible content size (OPEN_FLOOR), and they are then
priced and placed in steps like the rest. A session's `apportioned_output` /
`apportioned_usd` and the document's `apportioned_usd` say how much of the
split was shared out that way.

Rounds. A round is a set of sessions with a label. With no --session, the
round is every session in the worktree's Claude project directory (or each
--project-dir) that ran between the workspace's first and last history event,
or in the hour before the first (the run's lead-in: reading the brief and the
skill) - the design run itself, including the session still running. With
--session, exactly those transcripts. --shared-with marks a round that also
worked on something else (another board, the skill): its cost is shown
beside the board's total, never inside it. Results merge into an existing
--out file by session id, so rounds accumulate across runs, and a session
whose transcript is gone keeps the figures it was recorded with.

A session's cost is Claude Code's own recorded total (`cost-state`) or the
priced sum of its messages, whichever is larger: the recorded total also
bills small side calls the transcript does not show, and a session with no
`cost-state` yet (still running, or killed before it wrote one) has only the
priced sum, and says so. The steps split the priced sum; the rest shows as
its own "not in the transcript" line, so the steps always add up to the
total. The loop log's total (--loop-log, reconciled against the round being
recorded) is reported beside it: a timed-out iteration logs no cost. Tokens of a model the price table lacks are reported as
unpriced, never priced by guess.

Exit 0 "pass"       at least one session was found and priced.
Exit 1 "violations" no session found for the workspace - `missing` says where
                    it looked. The existing --out file is left untouched.
Exit 2 "error"      no workspace / unreadable state.json, price table or
                    transcript.

CLI:
  gen_cost.py --workspace ~/dev/boards/<name> [--out reports/cost.json]
              [--project-dir DIR ...] [--session FILE.jsonl ...]
              [--label TEXT] [--shared-with TEXT] [--loop-log FILE ...]
              [--note TEXT ...] [--history-tz +HH:MM] [--prices FILE]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent
PRICES = SCRIPTS.parent / "reference" / "model_prices.yaml"

# Step keys in pipeline order (SPEC 3), with the names the guide prints.
STEPS = [
    ("setup", "Before the workspace (brief and skill read)"),
    ("P0", "Brief and intake"),
    ("P1", "Research"),
    ("P2", "Architecture"),
    ("P3", "Parts and library"),
    ("P4", "Schematic"),
    ("P5", "Board setup"),
    ("P6", "Placement"),
    ("P7", "Routing"),
    ("P8", "Verification"),
    ("P9", "DFM, order files and guide"),
    ("P10", "Ordering"),
    ("done", "After the run closed"),
]
STEP_LABEL = dict(STEPS)
STEP_ORDER = {k: i for i, (k, _) in enumerate(STEPS)}
# The phase each exit gate closes (state.py GATE_ORDER, plus sim in P8) and
# the phase each human checkpoint closes (state.py CHECKPOINTS).
GATE_PHASE = {"erc": "P4", "place": "P6", "drc_routed": "P7",
              "verify": "P8", "sim": "P8", "dfm": "P9"}
CHECKPOINT_PHASE = {"1": "P2", "2": "P4", "3": "P6", "4": "P8", "5": "P10"}
TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write_5m",
                "cache_write_1h")
LOOP_SLACK = timedelta(minutes=2)
# A session that ended this soon before the workspace's first record is the
# run's lead-in (reading the brief and the skill, then creating the workspace).
LEAD_IN = timedelta(hours=1)
# Output a message's transcript never got is shared in proportion to the
# message's visible content plus this fraction of the mean, so a message whose
# content is only (redacted) thinking still gets a share. Measured on the
# PCB-0017-A sessions' closed messages: 0.5 gave the least per-message error
# for two of three models.
OPEN_FLOOR = 0.5


class CostError(RuntimeError):
    """Unusable input (exit 2)."""


# --- inputs -----------------------------------------------------------------

def load_prices(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        models = data["models"]
        for name, rates in models.items():
            for f in TOKEN_FIELDS:
                float(rates[f])
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise CostError(f"price table unreadable: {path}: {exc}") from exc
    return data


def parse_ts(s: str, naive_tz) -> datetime:
    """ISO timestamp -> aware UTC. A naive one is read in `naive_tz` (None =
    this host's local zone, which is what state.json history is written in)."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz) if naive_tz else dt.astimezone()
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def usage_tokens(u: dict) -> dict:
    cc = u.get("cache_creation") or {}
    w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:  # older record: no TTL split, bill as 5m
        w5, w1 = u.get("cache_creation_input_tokens", 0), 0
    return {"input": u.get("input_tokens", 0) or 0,
            "output": u.get("output_tokens", 0) or 0,
            "cache_read": u.get("cache_read_input_tokens", 0) or 0,
            "cache_write_5m": w5 or 0, "cache_write_1h": w1 or 0}


def price(model: str, toks: dict, prices: dict) -> float | None:
    rates = prices["models"].get(model.split("[")[0])
    if rates is None:
        return None
    return sum(toks[f] * float(rates[f]) for f in TOKEN_FIELDS) / 1e6


def _block_chars(c: dict) -> int:
    """Visible size of one content block (thinking is stored redacted)."""
    t = c.get("type")
    if t == "text":
        return len(c.get("text") or "")
    if t == "tool_use":
        return len(json.dumps(c.get("input"), ensure_ascii=True))
    if t == "thinking":
        return len(c.get("thinking") or "")
    return 0


def apportion_output(best: dict, model_usage: dict) -> dict[str, int]:
    """Restore output tokens the transcript never got (module docstring,
    'Output Claude Code never wrote down'). Adds them in place to the open
    messages of `best` and returns {model: tokens added}."""
    added: dict[str, int] = {}
    for model, mu in (model_usage or {}).items():
        mine = [e for e in best.values() if e["model"] == model]
        try:
            missing = int(mu.get("outputTokens") or 0) - sum(
                e["toks"]["output"] for e in mine)
        except (TypeError, ValueError, AttributeError):
            continue
        open_ = [e for e in mine if not e["stopped"]]
        if missing <= 0 or not open_:
            continue
        chars = [sum(e["chars"].values()) for e in open_]
        floor = OPEN_FLOOR * sum(chars) / len(chars)
        w = [c + floor for c in chars] if floor else [1.0] * len(open_)
        tot = sum(w)
        share = [missing * x / tot for x in w]
        whole = [int(s) for s in share]
        # largest remainder, so the tokens added sum exactly to `missing`
        for i in sorted(range(len(open_)), key=lambda i: whole[i] - share[i])[
                :missing - sum(whole)]:
            whole[i] += 1
        for e, n in zip(open_, whole):
            e["toks"]["output"] += n
        added[model] = missing
    return added


def read_session(main: Path, naive_tz=None) -> dict:
    """One session: its messages (main + subagents) and its cost-state.

    A streamed message is written once per content block under one message
    id; the record with the largest output count is the final one when the
    message's closing record (a `stop_reason`) was written. When it was not,
    the count is the stream's opening one, and apportion_output restores the
    rest from cost-state's per-model usage."""
    files = [main] + sorted(Path(p) for p in glob.glob(
        str(main.with_suffix("")) + "/subagents/*.jsonl"))
    best: dict = {}
    recorded = None
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CostError(f"transcript unreadable: {f}: {exc}") from exc
        for ln in lines:
            try:
                d = json.loads(ln)
            except ValueError:
                continue  # a torn last line of a killed session
            if d.get("type") == "cost-state" and f == main:
                recorded = d
            if d.get("type") != "assistant":
                continue
            m = d.get("message") or {}
            u, model, ts = m.get("usage"), m.get("model") or "", d.get("timestamp")
            if not u or not ts or model.startswith("<"):
                continue  # <synthetic> error turns bill nothing
            key = (str(f), m.get("id") or d.get("requestId") or d.get("uuid"))
            out = u.get("output_tokens", 0) or 0
            e = best.get(key)
            if e is None or out > e["toks"]["output"]:
                e = best[key] = {
                    "t": parse_ts(ts, naive_tz), "model": model,
                    "toks": usage_tokens(u),
                    "stopped": bool(e and e["stopped"]),
                    "chars": e["chars"] if e else {}}
            e["stopped"] = e["stopped"] or bool(m.get("stop_reason"))
            blk = d.get("apiBlockIndex", d.get("uuid"))
            e["chars"][blk] = max(e["chars"].get(blk, 0), sum(
                _block_chars(c) for c in m.get("content") or []
                if isinstance(c, dict)))
    added = apportion_output(best, (recorded or {}).get("modelUsage"))
    msgs = sorted(((e["t"], e["model"], e["toks"]) for e in best.values()),
                  key=lambda x: x[0])
    return {"id": main.stem, "transcript": main, "messages": msgs,
            "apportioned_output": added,
            "recorded_usd": (float(recorded["totalCostUSD"])
                             if recorded and "totalCostUSD" in recorded else None)}


def milestones(history: list, naive_tz=None) -> list[tuple]:
    """(when, phase, 'enter'|'exit') from state.json history, sorted."""
    out = []
    for e in history or []:
        if not isinstance(e, dict) or not e.get("ts"):
            continue
        ev = e.get("event")
        if ev == "init":
            ph, kind = "P0", "enter"
        elif ev == "phase":
            ph, kind = e.get("phase"), "enter"
        elif ev == "gate":
            ph, kind = GATE_PHASE.get(str(e.get("gate"))), "exit"
        elif ev == "human":
            ph, kind = CHECKPOINT_PHASE.get(str(e.get("checkpoint"))), "exit"
        else:
            continue
        if ph not in STEP_ORDER:
            continue
        try:
            out.append((parse_ts(e["ts"], naive_tz), ph, kind))
        except ValueError:
            continue
    return sorted(out, key=lambda m: m[0])


def step_of(t: datetime, ms: list[tuple]) -> str:
    """The hwde step a moment belongs to (module docstring, 'attributed')."""
    if not ms or t < ms[0][0]:
        return "setup"
    i = max(k for k, m in enumerate(ms) if m[0] <= t)
    if i == len(ms) - 1:
        return max((m[1] for m in ms), key=STEP_ORDER.__getitem__)
    nxt = ms[i + 1]
    return nxt[1] if nxt[2] == "exit" else ms[i][1]


LOOP_COST = re.compile(r"\$(\d+(?:\.\d+)?)?(?:~(\w+))?$")


def read_loop_log(path: Path, bad: list | None = None) -> list[dict]:
    """Iterations of a cc-loop log: start/end, rc and cost (None when the
    iteration died without reporting one, e.g. a timeout, rc=124). A cost
    field is `$1.23` (exact), `$1.23~transcript` (estimated from that source)
    or `$` (none); an end line whose cost is none of these is appended to
    `bad` (as "path:line: text") and skipped, never fatal."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise CostError(f"loop log unreadable: {path}: {exc}") from exc
    its, start = [], None
    for n, ln in enumerate(lines, 1):
        m = re.match(r"(\S+Z) iter (\d+)/\d+ start", ln)
        if m:
            start = parse_ts(m.group(1), None)
            continue
        m = re.match(r"(\S+Z) iter (\d+) end rc=(\d+)(.*)", ln)
        if not (m and start):
            continue
        c = re.search(r"\bcost=(\S*)", m.group(4))
        cm = LOOP_COST.match(c.group(1)) if c else None
        if not cm:
            if bad is not None:
                bad.append(f"{path}:{n}: {ln.strip()[:120]}")
            start = None
            continue
        its.append({"start": start, "end": parse_ts(m.group(1), None),
                    "iter": int(m.group(2)), "rc": int(m.group(3)),
                    "cost_usd": float(cm.group(1)) if cm.group(1) else None,
                    "cost_source": cm.group(2), "log": str(path)})
        start = None
    return its


# --- discovery --------------------------------------------------------------

def claude_projects_root() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def default_project_dir(ws: Path) -> Path | None:
    """The Claude Code project directory of the worktree holding `ws`."""
    try:
        top = subprocess.run(["git", "-C", str(ws), "rev-parse",
                              "--show-toplevel"], capture_output=True,
                             text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    if not top:
        return None
    return claude_projects_root() / re.sub(r"[^A-Za-z0-9]", "-", top)


def overlapping(sessions: list[dict], ms: list[tuple]) -> list[dict]:
    """Sessions whose time span overlaps the workspace's recorded span (first
    to last history event), widened by LEAD_IN before the first record."""
    keep = []
    for s in sessions:
        if not s["messages"] or not ms:
            continue
        a, b = s["messages"][0][0], s["messages"][-1][0]
        if a <= ms[-1][0] and b >= ms[0][0] - LEAD_IN:
            keep.append(s)
    return keep


# --- assembly ---------------------------------------------------------------

def _home(p: Path) -> str:
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h + os.sep) else s


def session_record(s: dict, ms: list[tuple], prices: dict) -> dict:
    by_step: dict[str, float] = {}
    unpriced: dict[str, int] = {}
    total = 0.0
    for t, model, toks in s["messages"]:
        usd = price(model, toks, prices)
        if usd is None:
            unpriced[model] = unpriced.get(model, 0) + sum(toks.values())
            continue
        total += usd
        k = step_of(t, ms)
        by_step[k] = by_step.get(k, 0.0) + usd
    msgs = s["messages"]
    rec = s["recorded_usd"]
    added = s.get("apportioned_output") or {}
    est = sum(price(m, {**dict.fromkeys(TOKEN_FIELDS, 0), "output": n},
                    prices) or 0.0 for m, n in added.items())
    return {"id": s["id"], "transcript": _home(s["transcript"]),
            "usd": round(max(total, rec or 0.0), 4),
            "start": iso(msgs[0][0]) if msgs else None,
            "end": iso(msgs[-1][0]) if msgs else None,
            "messages": len(msgs),
            "priced_usd": round(total, 4),
            "recorded_usd": (round(s["recorded_usd"], 4)
                             if s["recorded_usd"] is not None else None),
            "complete": s["recorded_usd"] is not None,
            "by_step": {k: round(v, 4) for k, v in sorted(
                by_step.items(), key=lambda kv: STEP_ORDER[kv[0]])},
            "unpriced_tokens": unpriced,
            "apportioned_output": added,
            "apportioned_usd": round(est, 4)}


def summarize_round(rnd: dict, ms: list[tuple]) -> dict:
    ss = rnd["sessions"]
    starts = [parse_ts(s["start"], None) for s in ss if s.get("start")]
    ends = [parse_ts(s["end"], None) for s in ss if s.get("end")]
    a, b = (min(starts), max(ends)) if starts and ends else (None, None)
    inside = sum(1 for m in ms if a and a <= m[0] <= b)
    rnd["start"], rnd["end"] = iso(a), iso(b)
    rnd["usd"] = round(sum(s["usd"] for s in ss), 4)
    rnd["priced_usd"] = round(sum(s["priced_usd"] for s in ss), 4)
    recs = [s["recorded_usd"] for s in ss]
    rnd["recorded_usd"] = (round(sum(recs), 4)
                           if recs and None not in recs else None)
    rnd["hwde_records_inside"] = inside
    if rnd.get("shared_with"):
        rnd["breakdown"] = False
        rnd["breakdown_reason"] = (f"shared with {rnd['shared_with']}: its "
                                   "spend is not split between them")
    elif not inside:
        rnd["breakdown"] = False
        rnd["breakdown_reason"] = ("hwde recorded no step in the workspace "
                                   "while this round ran")
    else:
        rnd["breakdown"] = True
        rnd["breakdown_reason"] = None
    return rnd


def attach_loop(rnd: dict, iters: list[dict]) -> None:
    a, b = parse_ts(rnd["start"], None), parse_ts(rnd["end"], None)
    mine = [i for i in iters
            if i["end"] >= a - LOOP_SLACK and i["start"] <= b + LOOP_SLACK]
    if not mine:
        return
    rnd["loop"] = {
        "logs": sorted({_home(Path(i["log"])) for i in mine}),
        "iterations": [{"iter": i["iter"], "start": iso(i["start"]),
                        "end": iso(i["end"]), "rc": i["rc"],
                        "cost_usd": i["cost_usd"],
                        **({"estimated": i["cost_source"]}
                           if i.get("cost_source") else {})} for i in mine],
        "logged_usd": round(sum(i["cost_usd"] or 0 for i in mine), 4),
        "unlogged_iterations": sum(1 for i in mine if i["cost_usd"] is None),
        "estimated_iterations": sum(1 for i in mine if i.get("cost_source")),
    }


def totals(doc: dict) -> dict:
    own = [r for r in doc["rounds"] if not r.get("shared_with")]
    split = [r for r in own if r["breakdown"]]
    by: dict[str, float] = {}
    for r in split:
        for s in r["sessions"]:
            for k, v in s["by_step"].items():
                by[k] = by.get(k, 0.0) + v
    steps = [{"step": k, "label": STEP_LABEL[k], "usd": round(by[k], 4)}
             for k, _ in STEPS if k in by]
    doc["apportioned_usd"] = round(sum(s.get("apportioned_usd") or 0.0
                                       for r in split
                                       for s in r["sessions"]), 4)
    rest = sum(r["usd"] - r["priced_usd"] for r in split)
    if rest >= 0.005:
        steps.append({"step": "unattributed",
                      "label": "Not in the transcript (side calls)",
                      "usd": round(rest, 4)})
    for r in own:
        if not r["breakdown"]:
            steps.append({"step": "unsplit", "label": r["label"],
                          "usd": r["usd"], "reason": r["breakdown_reason"]})
    doc["total_usd"] = round(sum(r["usd"] for r in own), 4)
    recs = [r["recorded_usd"] for r in own]
    doc["recorded_usd"] = (round(sum(recs), 4)
                           if recs and None not in recs else None)
    doc["loop_logged_usd"] = (round(sum(r["loop"]["logged_usd"] for r in own
                                        if r.get("loop")), 4)
                              if any(r.get("loop") for r in own) else None)
    doc["by_step"] = steps
    if not own:
        doc["breakdown"], doc["breakdown_reason"] = "none", "no own round"
    elif len(split) == len(own):
        doc["breakdown"], doc["breakdown_reason"] = "full", None
    else:
        doc["breakdown"] = "partial" if split else "none"
        doc["breakdown_reason"] = "; ".join(
            f"{r['label']}: {r['breakdown_reason']}" for r in own
            if not r["breakdown"])
    doc["shared"] = [{"label": r["label"], "shared_with": r["shared_with"],
                      "usd": r["usd"]} for r in doc["rounds"]
                     if r.get("shared_with")]
    unp: dict[str, int] = {}
    for r in doc["rounds"]:
        for s in r["sessions"]:
            for m, n in s["unpriced_tokens"].items():
                unp[m] = unp.get(m, 0) + n
    doc["unpriced_tokens"] = unp
    doc["incomplete_sessions"] = [s["id"] for r in doc["rounds"]
                                  for s in r["sessions"] if not s["complete"]]
    return doc


def build(ws: Path, *, sessions: list[Path], project_dirs: list[Path],
          label: str | None, shared_with: str | None, loop_logs: list[Path],
          notes: list[str], prices_path: Path, naive_tz, previous: dict | None
          ) -> dict:
    state_p = ws / "state.json"
    try:
        state = json.loads(state_p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CostError(f"state.json unreadable: {state_p}: {exc}") from exc
    prices = load_prices(prices_path)
    ms = milestones(state.get("history"), naive_tz)
    missing: list[str] = []
    if sessions:
        found = [read_session(p, naive_tz) for p in sessions]
        label = label or "later round"
    else:
        dirs = project_dirs or [d for d in [default_project_dir(ws)] if d]
        if not dirs:
            missing.append(f"no Claude project directory for {ws} "
                           "(not in a git worktree?) - pass --project-dir")
        cands = []
        for d in dirs:
            if not d.is_dir():
                missing.append(f"no transcripts at {_home(d)}")
                continue
            cands += [read_session(p, naive_tz)
                      for p in sorted(d.glob("*.jsonl"))]
        found = overlapping(cands, ms)
        if dirs and not found and not missing:
            missing.append("no session in " + ", ".join(_home(d) for d in dirs)
                           + " ran while the workspace recorded its steps")
        label = label or "design run"
    found = [s for s in found if s["messages"]]
    if not found and not missing:
        missing.append("the given transcripts hold no priced message")

    doc = {"script": "gen_cost", "board": state.get("board"),
           "workspace": ws.as_posix(), "currency": "USD",
           "prices": {"source": prices["meta"].get("source"),
                      "verified": str(prices["meta"].get("verified")),
                      "table": "reference/model_prices.yaml"},
           "rounds": [], "notes": []}
    if previous:
        doc["rounds"] = previous.get("rounds") or []
        doc["notes"] = previous.get("notes") or []
    new_ids = {s["id"] for s in found}
    for r in doc["rounds"]:
        r["sessions"] = [s for s in r["sessions"] if s["id"] not in new_ids]
    rnd = next((r for r in doc["rounds"] if r["label"] == label), None)
    if found:
        if rnd is None:
            rnd = {"label": label, "sessions": []}
            doc["rounds"].append(rnd)
        if shared_with is not None:
            rnd["shared_with"] = shared_with or None
        rnd["sessions"] += [session_record(s, ms, prices) for s in found]
        rnd["sessions"].sort(key=lambda s: s.get("start") or "")
    doc["rounds"] = [summarize_round(r, ms) for r in doc["rounds"]
                     if r["sessions"]]
    doc["rounds"].sort(key=lambda r: r.get("start") or "")
    bad: list[str] = []
    iters = [i for p in loop_logs for i in read_loop_log(p, bad)]
    for b in bad:
        print(f"warning: loop log line skipped, cost unparseable: {b}",
              file=sys.stderr)
    if iters and found:
        attach_loop(rnd, iters)
    if loop_logs:
        doc["loop_unparsed_lines"] = len(bad)
    for n in notes:
        if n not in doc["notes"]:
            doc["notes"].append(n)
    totals(doc)
    doc["missing"] = missing
    doc["status"] = "pass" if found and not missing else "violations"
    doc["generated"] = iso(datetime.now(timezone.utc))
    return doc


def _tz(s: str | None):
    if not s:
        return None
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", s)
    if not m:
        raise CostError(f"--history-tz must look like -04:00, got {s!r}")
    off = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return timezone(-off if m.group(1) == "-" else off)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="~/dev/boards/<name>")
    ap.add_argument("--out", help="write (and merge into) this JSON file, "
                    "normally <ws>/reports/cost.json; default stdout")
    ap.add_argument("--project-dir", action="append", default=[],
                    help="Claude project dir to search (default: the one of "
                    "the worktree holding the workspace)")
    ap.add_argument("--session", action="append", default=[],
                    help="a session transcript .jsonl to count as one round")
    ap.add_argument("--label", help="round label (default 'design run', or "
                    "'later round' with --session)")
    ap.add_argument("--shared-with", help="what else this round worked on; "
                    "its cost is shown beside the total, not in it")
    ap.add_argument("--loop-log", action="append", default=[],
                    help="a cc-loop loop.log to reconcile against")
    ap.add_argument("--note", action="append", default=[],
                    help="a line for `notes`, e.g. what could not be recovered")
    ap.add_argument("--history-tz", help="zone of state.json's naive "
                    "timestamps, e.g. -04:00 (default: this host's)")
    ap.add_argument("--prices", default=str(PRICES))
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    out = Path(args.out) if args.out else None
    try:
        if not ws.is_dir():
            raise CostError(f"no workspace at {ws}")
        previous = None
        if out and out.is_file():
            try:
                previous = json.loads(out.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise CostError(f"{out} unreadable: {exc}") from exc
        payload = build(ws, sessions=[Path(p) for p in args.session],
                        project_dirs=[Path(p) for p in args.project_dir],
                        label=args.label, shared_with=args.shared_with,
                        loop_logs=[Path(p) for p in args.loop_log],
                        notes=args.note, prices_path=Path(args.prices),
                        naive_tz=_tz(args.history_tz), previous=previous)
        code = 0 if payload["status"] == "pass" else 1
    except CostError as exc:
        payload, code = {"script": "gen_cost", "status": "error",
                         "error": str(exc)}, 2
    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if out and code == 0:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="ascii")
    print(text if not out or code else json.dumps(
        {"script": "gen_cost", "status": payload["status"], "out": str(out),
         "total_usd": payload["total_usd"],
         "breakdown": payload["breakdown"]}))
    return code


if __name__ == "__main__":
    sys.exit(main())
