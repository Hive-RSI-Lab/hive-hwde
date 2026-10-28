"""routelib - shared plumbing for the S11 routing pipeline (venv side).

Three concerns, used by route_edit.py / route_auto.py / planes_gen.py:

1. run_worker(): drive lib/route_swig.py in KiCad's BUNDLED python. The job
   and result travel via FILES in a caller-owned staging dir - worker stdout
   is noise by design (wx image-handler chatter, C-level "memory leak of type
   'PCB_TRACK *'" spray on bulk removals) and is never parsed.

2. Freerouting invocation: build_fr_cmd() with the S11-verified flag set
   (LEARNINGS [freerouting]): --gui.enabled=false, -de/-do, -mp, and the
   determinism/safety trio -mt 1 -is sequential -da (multithreaded optimizer
   has a known clearance bug AND is nondeterministic; -da is mandatory or FR
   phones home and can stall) + --logging.file.enabled=false (else a
   DEBUG-heavy freerouting.log lands in cwd). run_freerouting() enforces a
   PROCESS-level timeout - a wedged JVM is only cleared by a kill.

3. parse_fr_log(): the authoritative completion parse. FR's own success
   signal lies; unrouted-count priority is (a) session-completed line's
   "final score: S (N unrouted)", (b) the LAST pass line's "(N unrouted)"
   - a pass line WITHOUT the parenthetical means 0 unrouted at that pass -
   (c) None (unknown -> caller treats as failure). Gate on kicad-cli DRC,
   never on FR's numbers.

4. dsn_apply_net_rules(): KiCad's DSN export writes only netclass rules, so
   a per-net .kicad_dru floor (rules_gen's aiee_pwr_width_* track width and
   aiee_hv_* clearance) never reaches Freerouting, which then lays that net
   at the default 0.2 mm (rf-term's /RF: 4 track_width + 3 HV clearance DRC
   errors). Each such net is moved into its own DSN class carrying the
   floor.

5. dsn_merge_wires(): KiCad exports pre-routed copper as many short wires,
   which overflow Freerouting's stack in PolylineTrace.combine; each chain
   of same-net/layer/width/type wires is joined into one path.
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from checklib import CheckError

WORKER = Path(__file__).resolve().parent / "route_swig.py"

# Deterministic / safe base flags (order: options first, then -de/-do).
FR_BASE_FLAGS = ["--gui.enabled=false", "-mt", "1", "-is", "sequential",
                 "-da", "--logging.file.enabled=false"]

# Escalation ladder for route_auto: each rung re-runs Freerouting on the same
# DSN with more effort. mp = max passes; oit = optimizer improvement threshold
# (percent; lower = keep optimizing longer); us = board update strategy.
DEFAULT_LADDER = [
    {"mp": 20},
    {"mp": 60, "oit": 0.05},
    {"mp": 100, "us": "global"},
]

# Score capture is `\d+(?:\.\d+)?`, NOT `[\d.]+`: when Freerouting finishes with
# nothing unrouted the line has no " (N unrouted)" suffix and ends in a full stop
# ("...score of 997.76."), which `[\d.]+` swallows -> float("997.76.") ValueError.
# The bug fired ONLY on success, because the suffix otherwise terminated the match
# at the space. Found on bb-buck P6 (route probe, completion 1.00).
_PASS_RE = re.compile(
    r"Auto-router pass #(\d+).*?score of (\d+(?:\.\d+)?)(?: \((\d+) unrouted\))?")
_SESSION_RE = re.compile(
    r"Auto-router session completed: started with (\d+) unrouted nets"
    r".*?final score: (\d+(?:\.\d+)?)(?: \((\d+) unrouted\))?")


WORK_MARKER = ".aiee_route_work"


def fresh_work_dir(work: Path) -> Path:
    """(Re)create a work dir SAFELY: only wipe a directory this pipeline
    created (marker file) or an empty one - a user-supplied --work-dir
    pointing at real data must never be rmtree'd (S11 review finding)."""
    import shutil

    work = Path(work)
    if work.exists():
        if not work.is_dir():
            raise CheckError(f"work dir is not a directory: {work}")
        if any(work.iterdir()) and not (work / WORK_MARKER).is_file():
            raise CheckError(
                f"refusing to wipe non-empty work dir {work} (no {WORK_MARKER}"
                " marker; pick a fresh --work-dir)")
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / WORK_MARKER).write_text("", encoding="utf-8")
    return work


def swap_in(staged: Path, pcb: Path) -> None:
    """Atomic same-volume swap; degrade to copy for a cross-volume
    --work-dir (os.replace raises OSError across volumes on Windows)."""
    import os
    import shutil

    try:
        os.replace(staged, pcb)
    except OSError:
        shutil.copy2(staged, pcb)
        Path(staged).unlink(missing_ok=True)


def run_worker(bundled_python: Path, job: dict, stage: Path,
               timeout: int = 300, worker: Path | None = None) -> dict:
    """Run one SWIG-worker verb; job/result via files under `stage`.

    Default worker is route_swig; board_update passes lib/update_swig.py
    (same job/result-file protocol - bulk Remove sprays stdout, results
    must travel by file)."""
    tag = uuid.uuid4().hex[:8]
    job_file = stage / f"job_{tag}.json"
    result_file = stage / f"result_{tag}.json"
    job = dict(job)
    job["result"] = str(result_file)
    job_file.write_text(json.dumps(job), encoding="utf-8")
    try:
        cp = subprocess.run(
            [str(bundled_python), str(worker or WORKER), str(job_file)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(
            f"{(worker or WORKER).stem} {job.get('verb')} timed out after "
            f"{timeout}s (wedged SWIG/wx call - board untouched)") from exc
    if not result_file.is_file():
        tail = (cp.stderr or cp.stdout or "").strip()[-300:]
        raise CheckError(
            f"{(worker or WORKER).stem} {job.get('verb')} wrote no result "
            f"(exit {cp.returncode}): {tail}")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise CheckError(
            f"{(worker or WORKER).stem} {job.get('verb')} failed: "
            f"{result.get('error')}")
    return result


def build_fr_cmd(java: Path, jar: Path, dsn: Path, ses: Path,
                 rung: dict | None = None) -> list[str]:
    rung = rung or {}
    cmd = [str(java), "-jar", str(jar)] + FR_BASE_FLAGS
    cmd += ["-mp", str(rung.get("mp", 20))]
    if rung.get("oit") is not None:
        cmd += ["-oit", str(rung["oit"])]
    if rung.get("us"):
        cmd += ["-us", str(rung["us"])]
    if rung.get("inc"):
        cmd += ["-inc", str(rung["inc"])]
    cmd += ["-de", str(dsn), "-do", str(ses)]
    return cmd


def run_freerouting(java: Path, jar: Path, dsn: Path, ses: Path, *,
                    rung: dict | None = None, timeout: int = 3600,
                    stall_s: int = 900, cmd: list[str] | None = None,
                    log_file: Path | None = None) -> dict:
    """One Freerouting run. Returns parse_fr_log() facts + process info.

    `timeout` is the HARD wall-clock cap; `stall_s` kills a run that has
    produced no output (and no growth of the .ses) for that long. A 4-layer
    board takes ~5 min per pass, so a run that keeps logging passes is let be
    until the hard cap; a silent wedged JVM ends after `stall_s`."""
    import threading
    import time
    cmd = cmd or build_fr_cmd(java, jar, dsn, ses, rung)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, cwd=str(dsn.parent))
    chunks: list[bytes] = []
    last = [time.monotonic()]

    def _pump():
        for line in iter(proc.stdout.readline, b""):
            chunks.append(line)
            last[0] = time.monotonic()

    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    start = time.monotonic()
    ses_size = -1
    timed_out = False
    kill_reason = None
    try:
        while proc.poll() is None:
            time.sleep(min(0.2, max(0.01, stall_s / 10)))
            now = time.monotonic()
            try:
                sz = ses.stat().st_size
            except OSError:
                sz = -1
            if sz != ses_size:
                ses_size = sz
                last[0] = max(last[0], now)
            if now - start > timeout:
                kill_reason = "hard"
            elif now - last[0] > stall_s:
                kill_reason = "stall"
            if kill_reason:
                proc.kill()
                timed_out = True
                break
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        t.join(5)
        proc.stdout.close()
    out = b"".join(chunks).decode("utf-8", "replace")
    rc = 124 if timed_out else proc.returncode
    if log_file is not None:
        log_file.write_text(out, encoding="utf-8")
    facts = parse_fr_log(out)
    facts.update({"rc": rc, "timed_out": timed_out,
                  "ses_written": ses.is_file(), "cmd": cmd,
                  "kill_reason": kill_reason})
    return facts


def parse_fr_log(text: str) -> dict:
    passes = []
    for m in _PASS_RE.finditer(text):
        passes.append({"pass": int(m.group(1)), "score": float(m.group(2)),
                       "unrouted": int(m.group(3)) if m.group(3) else 0})
    session = _SESSION_RE.search(text)
    unrouted = None
    started = None
    score = None
    if session:
        started = int(session.group(1))
        score = float(session.group(2))
        unrouted = int(session.group(3)) if session.group(3) else None
    if unrouted is None and passes:
        unrouted = passes[-1]["unrouted"]
    if unrouted is None and session:
        unrouted = 0  # session completed, no pass lines captured
    return {"passes": passes, "unrouted": unrouted,
            "started_unrouted": started, "final_score": score,
            "session_completed": bool(session)}


def completion_fraction(facts: dict) -> float | None:
    """Fraction of FR's starting workload that got routed (0..1)."""
    started, left = facts.get("started_unrouted"), facts.get("unrouted")
    if started in (None, 0):
        return 1.0 if facts.get("session_completed") and left in (0, None) \
            else None
    if left is None:
        return None
    return max(0.0, min(1.0, 1.0 - left / started))


def _sexp_end(text: str, i: int) -> int:
    """Index just past the s-expression opening at text[i] == '('."""
    depth, quoted = 0, False
    for j in range(i, len(text)):
        ch = text[j]
        if ch == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    raise CheckError("unbalanced s-expression in DSN")


def _quote_mask(text: str) -> str:
    """`text` with every character inside double quotes blanked, so a paren
    in a quoted net name ("Net-(C1-Pad2)") is not read as structure."""
    out, quoted = [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
            out.append(ch)
        else:
            out.append(" " if quoted else ch)
    return "".join(out)


_DSN_TOKEN_RE = re.compile(r'"[^"]*"|[^\s()]+')


def dsn_apply_net_rules(dsn_text: str, widths: dict[str, float],
                        clearances: dict[str, float]) -> tuple[str, list]:
    """Give every net with a width or clearance floor (mm) above its DSN
    class's own its own class carrying max(class, floor). Returns (text,
    [net, ...] moved). Only a `(resolution um N)` DSN is handled - values are
    in um - anything else comes back unchanged."""
    if not re.search(r"\(resolution\s+um\s+\d+\)", dsn_text):
        return dsn_text, []
    nets = set(widths) | set(clearances)
    out, moved, pos = [], [], 0
    for m in re.finditer(r"\(class\s", dsn_text):
        if m.start() < pos:
            continue
        end = _sexp_end(dsn_text, m.start())
        block = dsn_text[m.start():end]
        head_end = next((j for j, t in enumerate(_quote_mask(block))
                         if j and t == "("), -1)
        head = block[:head_end] if head_end > 0 else block[:-1]
        body = block[head_end:-1] if head_end > 0 else ""
        toks = _DSN_TOKEN_RE.findall(head)[1:]     # drop "class"
        name, members = toks[0], toks[1:]
        cw = re.search(r"\(width\s+([\d.]+)\)", body)
        cc = re.search(r"\(clearance\s+([\d.]+)\)", body)
        cw = float(cw.group(1)) if cw else 0.0
        cc = float(cc.group(1)) if cc else 0.0
        keep, extra = [], []
        for tok in members:
            net = tok.strip('"')
            w = widths.get(net, 0.0) * 1000.0
            c = clearances.get(net, 0.0) * 1000.0
            if net not in nets or (w <= cw and c <= cc):
                keep.append(tok)
                continue
            nb = body
            if w > cw:
                nb = re.sub(r"\(width\s+[\d.]+\)", f"(width {w:g})", nb,
                            count=1)
            if c > cc:
                nb = re.sub(r"\(clearance\s+[\d.]+\)", f"(clearance {c:g})",
                            nb, count=1)
            cname = "aiee_" + re.sub(r"[^A-Za-z0-9_]", "_", net)
            extra.append(f"(class {cname} {tok}\n      {nb.strip()}\n    )")
            moved.append(net)
        out.append(dsn_text[pos:m.start()])
        if keep:
            out.append(f"(class {name} {' '.join(keep)}\n      "
                       f"{body.strip()}\n    )")
        for i, e in enumerate(extra):
            out.append(("\n    " if keep or i else "") + e)
        pos = end
    out.append(dsn_text[pos:])
    return "".join(out), moved


_DSN_SEXP_TOKEN_RE = re.compile(r'"[^"]*"|[()]|[^\s()"]+')
_DSN_HEAD_RE = re.compile(r"\(\s*([^\s()]+)")


def _child_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """(start, end) of every direct child of the s-expression at
    text[start:end]."""
    masked = _quote_mask(text[start:end])
    spans, j = [], 1                      # skip the parent's own "("
    while j < len(masked) - 1:
        if masked[j] == "(":
            e = _sexp_end(text, start + j) - start
            spans.append((start + j, start + e))
            j = e
        else:
            j += 1
    return spans


def _sexp_head(text: str, s: int, e: int) -> str:
    m = _DSN_HEAD_RE.match(_quote_mask(text[s:e]))
    return m.group(1) if m else ""


def _parse_dsn_wire(text: str, s: int, e: int) -> dict | None:
    """One `(wire (path LAYER WIDTH x y x y ...) attrs...)`; None for any
    other shape (polygon, qarc, a nested paren in the path, an odd or
    non-numeric coordinate list)."""
    kids = _child_spans(text, s, e)
    if not kids or _quote_mask(text[s:kids[0][0]]).split() != ["(wire"]:
        return None
    ps, pe = kids[0]
    inner = text[ps + 1:pe - 1]
    if "(" in _quote_mask(inner):
        return None
    toks = _DSN_TOKEN_RE.findall(inner)
    if len(toks) < 7 or toks[0] != "path" or (len(toks) - 3) % 2:
        return None
    pts = [(toks[k], toks[k + 1]) for k in range(3, len(toks), 2)]
    try:
        fpts = [(float(x), float(y)) for x, y in pts]
    except ValueError:
        return None
    # attrs = everything after the path ((net ..)(type protect)...), as a
    # token tuple: two wires join only when they agree on all of it
    attrs = tuple(_DSN_SEXP_TOKEN_RE.findall(text[pe:e - 1]))
    return {"span": (s, e), "path": (ps, pe), "layer": toks[1],
            "width": toks[2], "key": (toks[1], toks[2], attrs),
            "pts": pts, "fpts": fpts}


def _cut_span(text: str, s: int, e: int) -> tuple[int, int]:
    """Widen a span to its whole line when nothing else shares that line."""
    ls, le = s, e
    while ls > 0 and text[ls - 1] in " \t":
        ls -= 1
    while le < len(text) and text[le] in " \t\r":
        le += 1
    if (ls == 0 or text[ls - 1] == "\n") and le < len(text) \
            and text[le] == "\n":
        return ls, le + 1
    return s, e


def dsn_merge_wires(dsn_text: str) -> tuple[str, int]:
    """Join each net's pre-routed wire chains into one `(path ...)` per chain.

    KiCad's DSN export writes hand-routed copper as many short wires, and
    Freerouting 2.2.4 overflows its stack in PolylineTrace.combine reading
    them (PCB-0019 USB D+). Two wires join at an endpoint only when exactly
    those two wires on that layer end there, no via sits on it, and they
    agree on layer, width and every attribute (net, type protect/fix, ...).
    A branch point, a via, a cycle or a chain that closes on itself stays
    as exported; coordinates keep their original strings. Returns (text,
    input wires folded into a longer one); unparseable or unbalanced input
    comes back unchanged with 0."""
    # KiCad's parser header declares `(string_quote ")`: that lone quote
    # would flip every quote-aware scan after it. Parse a same-length copy
    # with it blanked; edits go back onto the original by index.
    scan = re.sub(r'\(string_quote\s+"\s*\)',
                  lambda q: q.group(0).replace('"', " "), dsn_text)
    m = re.search(r"\(wiring[\s)]", _quote_mask(scan))
    if not m:
        return dsn_text, 0
    wires, vias = [], set()
    try:
        top = scan.index("(")
        if scan[_sexp_end(scan, top):].strip():
            return dsn_text, 0                  # trailing junk / 2nd tree
        wend = _sexp_end(scan, m.start())
        for s, e in _child_spans(scan, m.start(), wend):
            head = _sexp_head(scan, s, e)
            if head == "wire":
                w = _parse_dsn_wire(scan, s, e)
                if w:
                    wires.append(w)
            elif head == "via":
                # (via PADSTACK x y (net ..)...) - a via ends any chain
                cut = _quote_mask(scan[s + 1:e]).find("(")
                stop = s + 1 + cut if cut >= 0 else e - 1
                toks = _DSN_TOKEN_RE.findall(scan[s + 1:stop])
                try:
                    vias.add((float(toks[2]), float(toks[3])))
                except (IndexError, ValueError):
                    pass
    except CheckError:
        return dsn_text, 0

    # every parsed endpoint counts toward its node's degree; only wires
    # with distinct ends are chain material
    deg: dict = {}
    at: dict = {}
    for i, w in enumerate(wires):
        for end in (w["fpts"][0], w["fpts"][-1]):
            node = (w["layer"], end)
            deg[node] = deg.get(node, 0) + 1
            if w["fpts"][0] != w["fpts"][-1]:
                at.setdefault(node, []).append(i)

    def through(i: int, pt) -> int | None:
        """The wire a chain continues into from wire i at pt, if any."""
        node = (wires[i]["layer"], pt)
        ws = at.get(node, [])
        if deg[node] != 2 or len(ws) != 2 or pt in vias or ws[0] == ws[1]:
            return None
        j = ws[1] if ws[0] == i else ws[0]
        return j if wires[j]["key"] == wires[i]["key"] else None

    used: set[int] = set()
    chains = []
    for i, w in enumerate(wires):
        if i in used or w["fpts"][0] == w["fpts"][-1]:
            continue
        used.add(i)
        chain = [(i, False)]                    # (wire, reversed?)
        cyclic = False
        for forward in (True, False):
            prev, cur = i, w["fpts"][-1 if forward else 0]
            while not cyclic and (j := through(prev, cur)) is not None:
                if j in used:                   # walked back round to i
                    cyclic = True
                    break
                used.add(j)
                f = wires[j]["fpts"]
                starts_here = f[0] == cur
                # forward: j must start at cur; backward: j must end there
                rev = not starts_here if forward else starts_here
                chain.insert(len(chain) if forward else 0, (j, rev))
                prev, cur = j, f[-1] if starts_here else f[0]
        if cyclic or len(chain) < 2:
            continue
        pts: list = []
        for j, rev in chain:
            seq = wires[j]["pts"][::-1] if rev else wires[j]["pts"]
            pts.extend(seq[1:] if pts else seq)
        if tuple(map(float, pts[0])) == tuple(map(float, pts[-1])):
            continue                            # closes on itself
        chains.append(([j for j, _ in chain], pts))

    edits = []                                  # (start, end, replacement)
    merged = 0
    for members, pts in chains:
        members.sort(key=lambda j: wires[j]["span"][0])
        keep = wires[members[0]]
        path = "(path %s %s  %s)" % (keep["layer"], keep["width"],
                                     "  ".join(f"{x} {y}" for x, y in pts))
        edits.append((*keep["path"], path))
        for j in members[1:]:
            edits.append((*_cut_span(dsn_text, *wires[j]["span"]), ""))
        merged += len(members)
    if not merged:
        return dsn_text, 0
    out, pos = [], 0
    for s, e, rep in sorted(edits):
        out.append(dsn_text[pos:s])
        out.append(rep)
        pos = e
    out.append(dsn_text[pos:])
    return "".join(out), merged
