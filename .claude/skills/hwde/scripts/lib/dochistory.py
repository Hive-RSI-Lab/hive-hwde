"""dochistory.py - a board run's history, read from what the run leaves behind.

report_gen.py's full design doc (--kind full) tells how the design went, not
only where it ended. Every helper here is read-only and returns plain data:

  decisions(st)          state.json decisions, oldest first
  phase_spans(st)        per phase: first/last decision time and the count
  backtracks(st)         where the run went back to an earlier phase: a
                         decision that names "Pn->Pm" (m < n), or one whose
                         phase is lower than the highest phase decided before
                         it; consecutive decisions going back to the same
                         phase are one backtrack
  component_decisions(st) decisions that picked, swapped or replaced a part
                         (an LCSC number, "->", swap, replace, instead)
  git_commits(ws, ref)   commits touching the workspace in its own git repo,
                         oldest first, with the files each touched ([] when
                         the workspace is not in a repo)
  activity(ws, commits)  commits bucketed by hour with the workspace areas
                         (top-level dirs) each hour touched
  parts_changes(ws, commits)  kicad/parts.json across the commits that touched
                         it: the refs whose MPN/LCSC appeared, went or changed
  snapshots(ws, commits) routing/pre-* and post-* board snapshots, ordered by
                         the commit that added them (else by name)
  flow_spec(st, bts)     a diagram-maker flowchart spec: phases down one
                         column, each backtrack a node beside its source phase
                         with a dashed edge back to where the run returned
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

PHASE_NAMES = {
    "P0": "Intake", "P1": "Research", "P2": "Architecture",
    "P3": "Parts and library", "P4": "Schematic", "P5": "Board setup",
    "P6": "Placement", "P7": "Routing", "P8": "Verification", "P9": "DFM",
    "P10": "Ordering", "done": "Done",
}
_ORDER = list(PHASE_NAMES)
_ARROW_RE = re.compile(r"\bP(\d{1,2})\s*(?:->|to)\s*P(\d{1,2})\b")
_PART_RE = re.compile(r"\bC\d{4,}\b|->|\bswap|\breplac|\binstead of\b|\bDEVIATION\b",
                      re.IGNORECASE)
GIT_TIMEOUT = 60


def pidx(phase) -> int:
    return _ORDER.index(phase) if phase in _ORDER else -1


def decisions(st: dict) -> list[dict]:
    out = [d for d in st.get("decisions") or [] if isinstance(d, dict)]
    return sorted(out, key=lambda d: str(d.get("ts", "")))


def phase_spans(st: dict) -> list[dict]:
    spans: dict[str, dict] = {}
    for d in decisions(st):
        ph = str(d.get("phase", "?"))
        s = spans.setdefault(ph, {"phase": ph, "first": d.get("ts", ""),
                                  "last": d.get("ts", ""), "count": 0})
        s["last"] = d.get("ts", "")
        s["count"] += 1
    return sorted(spans.values(), key=lambda s: pidx(s["phase"]))


def backtracks(st: dict) -> list[dict]:
    """[{from, to, ts, decisions: [decision...]}], oldest first."""
    out: list[dict] = []
    high = -1
    for d in decisions(st):
        ph = pidx(d.get("phase"))
        m = _ARROW_RE.search(str(d.get("what", "")))
        src = dst = None
        if m and int(m.group(2)) < int(m.group(1)):
            src, dst = f"P{m.group(1)}", f"P{m.group(2)}"
        elif 0 <= ph < high:
            src, dst = _ORDER[high], _ORDER[ph]
        if src is not None:
            last = out[-1] if out else None
            if last and last["to"] == dst and last["from"] == src:
                last["decisions"].append(d)
            else:
                out.append({"from": src, "to": dst, "ts": d.get("ts", ""),
                            "decisions": [d]})
        high = max(high, ph, pidx(src))
    return out


def component_decisions(st: dict) -> list[dict]:
    return [d for d in decisions(st) if _PART_RE.search(str(d.get("what", "")))]


def _git(ws: Path, *args: str) -> str | None:
    try:
        cp = subprocess.run(["git", "-C", str(ws), *args], capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cp.stdout if cp.returncode == 0 else None


def git_commits(ws: Path, ref: str = "HEAD") -> list[dict]:
    """[{sha, ts, subject, files: [workspace-relative paths]}], oldest first,
    from `ref` (a squash-merged board's run lives on its track branch)."""
    top = (_git(ws, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        return []
    try:
        prefix = ws.resolve().relative_to(Path(top).resolve()).as_posix()
    except ValueError:
        return []
    raw = _git(ws, "log", ref, "--reverse", "--name-only", "--format=\x01%h\x02%aI\x02%s",
               "--", ".")
    commits: list[dict] = []
    for block in (raw or "").split("\x01")[1:]:
        head, _, rest = block.partition("\n")
        sha, ts, subject = (head.split("\x02") + ["", ""])[:3]
        files = []
        for f in rest.splitlines():
            f = f.strip()
            if f and (prefix in ("", ".") or f.startswith(prefix + "/")):
                files.append(f[len(prefix) + 1:] if prefix not in ("", ".") else f)
        commits.append({"sha": sha, "ts": ts, "subject": subject, "files": files})
    return commits


def activity(commits: list[dict]) -> list[dict]:
    """[{hour: 'YYYY-MM-DDTHH', commits, areas: {area: files}}] by hour."""
    buckets: dict[str, dict] = {}
    for c in commits:
        hour = c["ts"][:13]
        b = buckets.setdefault(hour, {"hour": hour, "commits": 0, "areas": {}})
        b["commits"] += 1
        for f in c["files"]:
            area = f.split("/", 1)[0] if "/" in f else "(root)"
            b["areas"][area] = b["areas"].get(area, 0) + 1
    return [buckets[k] for k in sorted(buckets)]


def _parts_map(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    rows = data.get("parts") if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for p in rows if isinstance(rows, list) else []:
        if not isinstance(p, dict):
            continue
        label = " ".join(str(p[k]) for k in ("mpn", "lcsc") if p.get(k))
        for ref in p.get("refs") or []:
            out[str(ref)] = label
    return out


def parts_changes(ws: Path, commits: list[dict]) -> list[dict]:
    """[{sha, ts, added: [(ref, part)], removed: [...], changed: [(ref, old, new)]}]."""
    rel = "kicad/parts.json"
    out: list[dict] = []
    prev: dict[str, str] | None = None
    for c in commits:
        if rel not in c["files"]:
            continue
        text = _git(ws, "show", f"{c['sha']}:./{rel}")
        if text is None:
            continue
        cur = _parts_map(text)
        if prev is not None:
            ch = {"sha": c["sha"], "ts": c["ts"],
                  "added": sorted((r, cur[r]) for r in cur.keys() - prev.keys()),
                  "removed": sorted((r, prev[r]) for r in prev.keys() - cur.keys()),
                  "changed": sorted((r, prev[r], cur[r]) for r in cur.keys() & prev.keys()
                                    if cur[r] != prev[r])}
            if ch["added"] or ch["removed"] or ch["changed"]:
                out.append(ch)
        prev = cur
    return out


SNAP_PREFIXES = ("pre-", "post-", "pre_", "post_")


def snapshots(ws: Path, commits: list[dict]) -> list[str]:
    """Workspace-relative routing/pre-*.kicad_pcb and post-*.kicad_pcb, in
    the order the run made them (first commit that holds each; the rest by name).
    pre_*/post_* are accepted too: the routing step of PCB-0023-A saved those,
    and report_gen must render workspaces that already exist. Canonical is the
    hyphen."""
    d = ws / "routing"
    if not d.is_dir():
        return []
    names = sorted(f"routing/{p.name}" for p in d.iterdir() if p.is_file()
                   and p.suffix == ".kicad_pcb" and p.name.startswith(SNAP_PREFIXES))
    first = {}
    for i, c in enumerate(commits):
        for f in c["files"]:
            first.setdefault(f, i)
    return sorted(names, key=lambda n: (first.get(n, len(commits)), n))


def clean(text: str) -> str:
    """A decision's text without its "Pn->Pm:" lead, on one line."""
    return re.sub(r"\s+", " ", _ARROW_RE.sub("", str(text))).strip(" :;,-")


def _short(text: str, width: int = 30) -> str:
    text = clean(text)
    return text if len(text) <= width else text[:width - 3].rstrip() + "..."


def flow_spec(st: dict, bts: list[dict], title: str = "") -> dict:
    """The run as a diagram-maker flowchart (canvas 553, one A4 text column)."""
    spans = {s["phase"]: s for s in phase_spans(st)}
    gates = st.get("gates") or {}
    cur = str(st.get("phase", "P0"))
    last = max([pidx(cur)] + [pidx(p) for p in spans])
    phases = [p for p in _ORDER[:last + 1]]
    nodes, edges = [], []
    for row, ph in enumerate(phases):
        sub = []
        if ph in spans:
            n = spans[ph]["count"]
            sub.append(f"{n} decision{'s' if n != 1 else ''}")
        for g in gates.values():
            if g.get("phase") == ph and g.get("attempts"):
                sub.append(f"gate: {g['attempts']} attempts")
                break
        node = {"id": ph, "title": f"{ph} {PHASE_NAMES.get(ph, ph)}",
                "col": 0, "row": row}
        if sub:
            node["sub"] = sub[:2]
        if ph == cur:
            node["mark"] = True
        nodes.append(node)
        if row:
            edges.append({"from": phases[row - 1], "to": ph})
    # Each back node's dashed return climbs its own column from the source
    # row to the target row; two climbs never share rows in one column.
    spans_by_col: dict[int, list[tuple[int, int]]] = {}
    for i, bt in enumerate(bts):
        if bt["from"] not in phases or bt["to"] not in phases:
            continue
        row = phases.index(bt["from"])
        lo = phases.index(bt["to"])
        col = 1
        while any(a <= row and lo <= b for a, b in spans_by_col.get(col, [])):
            col += 1
        spans_by_col.setdefault(col, []).append((lo, row))
        n = len(bt["decisions"])
        nid = f"back{i}"
        nodes.append({"id": nid, "title": f"Back to {bt['to']}", "role": "gate",
                      "col": col, "row": row,
                      "sub": [_short(bt["decisions"][0].get("what", "")),
                              f"{n} decision{'s' if n != 1 else ''}, "
                              f"{str(bt['ts'])[11:16]}"]})
        edges.append({"from": bt["from"], "to": nid, "kind": "fail"})
        edges.append({"from": nid, "to": bt["to"], "kind": "dash", "route": "vh"})
    return {"type": "flowchart", "canvas": 553,
            "alt": title or "How the design run went, phase by phase",
            "nodes": nodes, "edges": edges}
