"""learnlib - workspace learnings, the promotion queue, and root promotion (U6).

v3 design decision 3: every run captures its learnings IN THE WORKSPACE; a
run-close step compiles them into a machine-readable promotion queue; a
separate promotion pass moves each entry to its level on the knowledge ladder
(script check / cost term / template / prompt line / knowledge record / bench
item / root LEARNINGS) or declines it with a reason.

The workspace file format (enforced by parse_entries, the same shape the root
lesson files use so a promotion is a verbatim copy):

    # LEARNINGS - <board> (<what the board is>)
    <preamble sections are allowed until the first dated entry>

    ## YYYY-MM-DD [P7][routing][gotcha] The claim, in one line
    body ...

`## ` headings BEFORE the first dated entry are preamble (an index, a queue
note). After it, a heading that does not parse is malformed - a learning
nobody can key, promote or count.

The queue (`<workspace>/learnings/queue.yaml`, QUEUE_SCHEMA):

    entry            stable id: <date>-<title slug> (line numbers move, ids do not)
    stage            P0-P10 from the entry's own tags, or null
    proposed_level   L0-L3 the promoter proposes, or null while pending
    targets          artifacts the entry names that EXIST (compile scans for them)
    status           pending | promoted | declined
    resolution       {kind, artifacts, reason, date} once ruled

Compile is idempotent: rulings survive re-compilation, source drift (line,
title, tags) is refreshed, new entries arrive `pending`, and an id in the queue
with no entry behind it is reported as an orphan rather than deleted.
"""
from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
REPO = SKILL.parents[2]          # <repo>/.claude/skills/hwde -> <repo>

ROOT_LEARNINGS = REPO / "LEARNINGS.md"
TRIAGE = REPO / "design" / "ladder-triage.md"

ENTRY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) ((?:\[[^\]]+\])+) (.+)$")
TAG_RE = re.compile(r"\[([^\]]+)\]")
STAGE_RE = re.compile(r"^P(?:10|[0-9])$")

LEVELS = ("L0", "L1", "L2", "L3")
STATUSES = ("pending", "promoted", "declined")

# What a promotion WROTE TO - the rungs of the ladder from
# design/routing-knowledge-notes.md s6, plus the two prose-level homes.
PROMOTE_KINDS = ("script_check", "cost_term", "template", "prompt_line",
                 "knowledge_record", "remediation", "bench_item",
                 "root_learnings")
# Why an entry does NOT climb. Every decline needs one of these AND a reason.
DECLINE_KINDS = ("board_local", "duplicate", "superseded", "not_actionable")

QUEUE_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "hwde workspace promotion queue",
    "type": "object",
    "required": ["version", "board", "source", "entries"],
    "additionalProperties": False,
    "properties": {
        "version": {"const": 1},
        "board": {"type": "string", "minLength": 1},
        "source": {"type": "string", "minLength": 1},
        "compiled": {"type": "string"},
        # U21: run close records unverified research here so the promote pass
        # sees it. Absent on a workspace whose research all cleared review.
        "research_drafts": {
            "type": "object", "additionalProperties": False,
            "properties": {k: {"type": "integer", "minimum": 0} for k in
                           ("drafts", "stalled", "unruled", "refuted",
                            "orphaned")},
        },
        "research_draft_records": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "task", "state", "stalled"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "task": {"type": "string", "minLength": 1},
                    "state": {"enum": ["unruled", "refuted", "orphaned"]},
                    "stalled": {"type": "boolean"},
                },
            },
        },
        "entries": {"type": "array", "items": {
            "type": "object",
            "required": ["entry", "line", "title", "tags", "stage",
                         "proposed_level", "targets", "status"],
            "additionalProperties": False,
            "properties": {
                "entry": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+$"},
                "line": {"type": "integer", "minimum": 1},
                "date": {"type": "string"},
                "title": {"type": "string", "minLength": 1},
                "tags": {"type": "array", "items": {"type": "string"}},
                "stage": {"type": ["string", "null"]},
                "proposed_level": {"enum": [*LEVELS, None]},
                "targets": {"type": "array", "items": {"type": "string"}},
                "status": {"enum": list(STATUSES)},
                "resolution": {
                    "type": ["object", "null"],
                    "required": ["kind", "reason", "date"],
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"enum": [*PROMOTE_KINDS, *DECLINE_KINDS]},
                        "artifacts": {"type": "array",
                                      "items": {"type": "string"}},
                        "reason": {"type": "string", "minLength": 8},
                        "date": {"type": "string"},
                    },
                },
            },
        }},
    },
}

SKELETON = """# LEARNINGS - {board} ({what})

Workspace-local. Every entry gets a date, tags (stage tag first: P0-P10), and a
one-line claim as its title. `learnings.py compile --workspace {ws}` turns new
entries into `learnings/queue.yaml`; the `promote` verb rules on them.

## {today} [P0][example] Replace this with the first real learning

Body: what was measured, on what, and what to do about it next time. Keep the
board-specific numbers - they are the evidence a promotion is judged on.
"""


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
def learnings_path(ws: Path) -> Path:
    return Path(ws) / "LEARNINGS.md"


def queue_path(ws: Path) -> Path:
    return Path(ws) / "learnings" / "queue.yaml"


def today() -> str:
    return _date.today().isoformat()


# ---------------------------------------------------------------------------
# parsing the workspace file
# ---------------------------------------------------------------------------
def slug(title: str, words: int = 7) -> str:
    """A stable, readable id fragment: the first `words` words of the claim."""
    toks = re.findall(r"[A-Za-z0-9]+", title.lower())
    out = "-".join(toks[:words])[:60].strip("-")
    return out or "entry"


def parse_entries(text: str) -> tuple[list[dict], list[str]]:
    """(entries, malformed headings). Entries carry line/date/tags/stage/
    title/body/id; ids are de-duplicated with a numeric suffix."""
    lines = text.splitlines()
    starts: list[tuple[int, re.Match]] = []
    malformed: list[str] = []
    seen_entry = False
    for i, ln in enumerate(lines, 1):
        m = ENTRY_RE.match(ln)
        if m:
            seen_entry = True
            starts.append((i, m))
        elif ln.startswith("## ") and seen_entry:
            malformed.append(f"line {i}: {ln.strip()[:70]}")

    entries: list[dict] = []
    used: dict[str, int] = {}
    for idx, (line, m) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[line:end]).strip("\n")
        tags = TAG_RE.findall(m.group(2))
        stage = next((t for t in tags if STAGE_RE.match(t)), None)
        base = f"{m.group(1)}-{slug(m.group(3))}"
        used[base] = used.get(base, 0) + 1
        eid = base if used[base] == 1 else f"{base}-{used[base]}"
        entries.append({"entry": eid, "line": line, "date": m.group(1),
                        "title": m.group(3).strip(), "tags": tags,
                        "stage": stage, "body": body})
    return entries, malformed


def _exists(rel: str) -> bool:
    return (SKILL / rel).is_file() or (REPO / rel).is_file()


_TARGET_PATTERNS = (
    (re.compile(r"(?<![\w/])scripts/lib/([a-z_0-9]+)\.py"), "scripts/lib/{}.py"),
    (re.compile(r"(?<![\w/])lib/([a-z_0-9]+)\.py"), "scripts/lib/{}.py"),
    (re.compile(r"(?<![\w/])scripts/([a-z_0-9]+)\.py"), "scripts/{}.py"),
    (re.compile(r"(?<![\w/.])([a-z_0-9]+)\.py"), "scripts/{}.py"),
    (re.compile(r"(?<![\w/])agents/([a-z_0-9-]+)\.md"), "agents/{}.md"),
    (re.compile(r"(?<![\w/])reference/([a-z_0-9/-]+\.(?:md|yaml|csv))"),
     "reference/{}"),
    (re.compile(r"(?<![\w/])remediations/([a-z_0-9-]+\.md)"),
     "reference/remediations/{}"),
    (re.compile(r"(?<![\w/])tests/(test_[a-z_0-9]+)\.py"), "tests/{}.py"),
    # U15: workspace-first research outputs named by a research task's close
    # entry (repo-relative when the workspace is in-repo)
    (re.compile(r"(?<![\w/])(boards/[A-Za-z0-9_.-]+/research/(?:records|checklists)/[a-z0-9-]+\.yaml)"),
     "{}"),
)


def scan_targets(text: str, limit: int = 8) -> list[str]:
    """Artifacts the entry NAMES and that exist here. Deterministic: this is a
    compile step, not a judgment - a promoter edits the list when ruling."""
    hits: set[str] = set()
    for rx, shape in _TARGET_PATTERNS:
        for m in rx.finditer(text):
            rel = shape.format(m.group(1))
            if _exists(rel):
                hits.add(rel)
    return sorted(hits)[:limit]


# ---------------------------------------------------------------------------
# the queue
# ---------------------------------------------------------------------------
def load_queue(ws: Path) -> dict | None:
    p = queue_path(ws)
    if not p.is_file():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{p}: not a mapping")
    return data


def save_queue(ws: Path, queue: dict) -> Path:
    p = queue_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(queue, sort_keys=False, allow_unicode=False,
                          width=100, default_flow_style=False)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def compile_queue(ws: Path, board: str | None = None) -> tuple[dict, dict]:
    """Merge the workspace's LEARNINGS entries into its queue.

    Rulings are never overwritten; source-side fields (line, title, tags,
    stage) are refreshed because the file is edited between runs. Returns
    (queue, report)."""
    ws = Path(ws)
    src = learnings_path(ws)
    if not src.is_file():
        raise FileNotFoundError(f"no workspace learnings at {src}")
    entries, malformed = parse_entries(src.read_text(encoding="utf-8"))

    old = load_queue(ws) or {}
    by_id = {e["entry"]: e for e in (old.get("entries") or [])
             if isinstance(e, dict) and e.get("entry")}

    out: list[dict] = []
    added: list[str] = []
    for e in entries:
        prev = by_id.pop(e["entry"], None)
        row = {
            "entry": e["entry"], "line": e["line"], "date": e["date"],
            "title": e["title"], "tags": e["tags"], "stage": e["stage"],
            "proposed_level": (prev or {}).get("proposed_level"),
            "targets": (prev or {}).get("targets")
                       or scan_targets(f"{e['title']}\n{e['body']}"),
            "status": (prev or {}).get("status", "pending"),
        }
        if (prev or {}).get("resolution"):
            row["resolution"] = prev["resolution"]
        if prev is None:
            added.append(e["entry"])
        out.append(row)

    orphans = sorted(by_id)
    queue = {"version": 1, "board": board or old.get("board") or ws.name,
             "source": "LEARNINGS.md", "compiled": today(), "entries": out}
    counts = {s: sum(1 for r in out if r["status"] == s) for s in STATUSES}
    report = {"entries": len(out), "added": added, "orphans": orphans,
              "malformed": malformed,
              "no_stage": [r["entry"] for r in out if not r["stage"]],
              "counts": counts}
    return queue, report


def validate_queue(ws: Path) -> tuple[list[str], list[str]]:
    """(problems, warnings). Same discipline as validate_registry: a queue
    that names an artifact must name one that exists, and a ruling that
    cannot be read back is not a ruling."""
    import jsonschema

    ws = Path(ws)
    problems: list[str] = []
    warnings: list[str] = []
    queue = load_queue(ws)
    if queue is None:
        return [f"{queue_path(ws)}: no queue (run `learnings.py compile`)"], []
    try:
        jsonschema.validate(queue, QUEUE_SCHEMA)
    except jsonschema.ValidationError as exc:
        loc = "/".join(str(p) for p in exc.absolute_path)
        return [f"schema: {loc or '<root>'}: {exc.message}"], []

    src = learnings_path(ws)
    live: dict[str, dict] = {}
    if src.is_file():
        entries, malformed = parse_entries(src.read_text(encoding="utf-8"))
        live = {e["entry"]: e for e in entries}
        problems += [f"malformed heading in {src.name}: {m}" for m in malformed]
    else:
        problems.append(f"{src}: workspace learnings file is missing")

    for row in queue["entries"]:
        eid = row["entry"]
        where = f"entry {eid}"
        if not str(row).isascii():
            problems.append(f"{where}: non-ASCII in the queue record")
        e = live.get(eid)
        if e is None:
            problems.append(f"{where}: no such entry in {src.name} (orphan)")
        elif e["line"] != row["line"]:
            warnings.append(f"{where}: line {row['line']} moved to {e['line']}"
                            " - re-compile")
        for t in row["targets"]:
            if not _exists(t):
                problems.append(f"{where}: target {t} does not exist")
        res = row.get("resolution")
        if row["status"] == "pending":
            if res:
                problems.append(f"{where}: pending but carries a resolution")
            continue
        if not res:
            problems.append(f"{where}: {row['status']} with no resolution")
            continue
        kinds = PROMOTE_KINDS if row["status"] == "promoted" else DECLINE_KINDS
        if res["kind"] not in kinds:
            problems.append(f"{where}: kind {res['kind']!r} is not a "
                            f"{row['status']} kind")
        if row["status"] == "promoted":
            if not res.get("artifacts"):
                problems.append(f"{where}: promoted with no artifacts - a "
                                "promotion writes somewhere")
            for a in res.get("artifacts") or []:
                if not _exists(a.split("#")[0]):
                    problems.append(f"{where}: artifact {a} does not exist")
            if not row["proposed_level"]:
                problems.append(f"{where}: promoted with no level")
    return problems, warnings


def apply_ruling(queue: dict, ruling: dict, ws: Path) -> dict:
    """Apply ONE ruling to the queue (in place). Returns a per-entry result.

    A `root_learnings` promotion also performs the move: the entry is written
    verbatim to a new lesson file in the repo's learnings.d/, its triage row
    as the file's last line, because an entry and its row are checked against
    each other by the suite and hand-copying is how they drift apart."""
    eid = ruling["entry"]
    row = next((r for r in queue["entries"] if r["entry"] == eid), None)
    if row is None:
        return {"entry": eid, "applied": False, "why": "not in the queue"}
    if row["status"] != "pending":
        return {"entry": eid, "applied": False,
                "why": f"already {row['status']}"}

    status = ruling["status"]
    kind = ruling["kind"]
    kinds = PROMOTE_KINDS if status == "promoted" else DECLINE_KINDS
    if status not in STATUSES[1:] or kind not in kinds:
        return {"entry": eid, "applied": False,
                "why": f"bad ruling {status}/{kind}"}
    reason = (ruling.get("reason") or "").strip()
    if len(reason) < 8:
        return {"entry": eid, "applied": False, "why": "reason too short"}

    artifacts = list(ruling.get("artifacts") or [])
    extra: dict = {}
    if kind == "root_learnings":
        # A batch is a whole promotion pass: one bad ruling must not strand
        # the rulings that already wrote lesson files, so the failure is
        # reported per entry and the rest of the pass continues.
        try:
            moved = promote_to_root(ws, eid, ruling.get("triage") or {})
        except (ValueError, OSError) as exc:
            return {"entry": eid, "applied": False, "why": str(exc)}
        artifacts = [moved["file"]] + artifacts
        extra = {"root": moved}

    if ruling.get("targets"):
        row["targets"] = list(ruling["targets"])
    row["proposed_level"] = ruling.get("level") or row["proposed_level"]
    row["status"] = status
    row["resolution"] = {"kind": kind, "artifacts": artifacts,
                         "reason": reason,
                         "date": ruling.get("date") or today()}
    return {"entry": eid, "applied": True, "status": status, "kind": kind,
            **extra}


# ---------------------------------------------------------------------------
# root promotion: one lesson file in learnings.d/, carrying its triage row
# ---------------------------------------------------------------------------
# LEARNINGS.md is a closed archive (entries 1-425): its line numbers are cited
# by the remediation refs and by the archive's own Register rows, so it never
# grows again. Every new lesson is its own file, `learnings.d/<id>.md`, whose
# last line is its triage row. Two PRs that each add a lesson add two
# different files and touch nothing else, so they merge either way round
# without a conflict - on GitHub's squash merge too, which ignores any
# .gitattributes merge driver. No count is kept by hand anywhere:
# `learnings.py triage` prints them.
LESSONS = REPO / "learnings.d"
LESSONS_README = "README.md"
TRIAGE_RE = re.compile(r"^Triage: now (\S+) \| target (\S+) \| owner (.+?) "
                       r"\| status (\S+)(?: \| note (.*))?$")


def _root_entries(text: str) -> list[tuple[int, str]]:
    return [(i, m.group(3)) for i, ln in
            enumerate(text.splitlines(), 1) if (m := ENTRY_RE.match(ln))]


def triage_line(now: str, target: str, owner: str, status: str,
                note: str = "") -> str:
    """A lesson file's last line - the row `parse_lesson` reads back."""
    cells = [f"now {now}", f"target {target}", f"owner {owner}",
             f"status {status}"]
    if note.strip():
        cells.append(f"note {note}")
    return "Triage: " + " | ".join(c.replace("|", "/").strip() for c in cells)


def triage_problems(triage: dict) -> list[str]:
    """What is wrong with one triage row, archive or lesson alike."""
    out = []
    for field in ("now", "target", "owner", "status"):
        if not (triage.get(field) or "").strip():
            out.append(f"triage.{field} is empty")
    if out:
        return out
    if triage["now"] not in LEVELS or triage["target"] not in LEVELS:
        out.append(f"triage levels must be one of {LEVELS}")
    elif triage["status"] == "done" and triage["now"] != triage["target"]:
        out.append("status 'done' but not at target")
    if triage["status"] not in ("done", "open", "n/a") and not re.fullmatch(
            r"planned-[TU]\d+", triage["status"]):
        out.append(f"bad triage status {triage['status']!r}")
    return out


def lesson_files(lessons_dir: Path | None = None) -> list[Path]:
    d = Path(lessons_dir) if lessons_dir else LESSONS
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if p.name != LESSONS_README)


def parse_lesson(path: Path) -> tuple[dict, list[str]]:
    """(lesson, problems). A lesson file is ONE dated `## ` heading on its
    first line, a body, and a last non-blank line `Triage: now Lx | target Lx
    | owner P | status S [| note N]`. Its name is `<date>-<slug>.md` (a `-N`
    suffix allowed), so the name is the stable key a citation uses."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    problems: list[str] = []
    heads = [ln for ln in lines if ln.startswith("## ")]
    m = ENTRY_RE.match(lines[0]) if lines else None
    if not m:
        return {"file": path.name}, [f"{path.name}: first line is not a "
                                     "`## YYYY-MM-DD [tag] title` heading"]
    if len(heads) != 1:
        problems.append(f"{path.name}: {len(heads)} `## ` headings - one "
                        "lesson per file")
    date, tags, title = m.group(1), TAG_RE.findall(m.group(2)), m.group(3).strip()
    base = f"{date}-{slug(title)}"
    if not re.fullmatch(re.escape(base) + r"(?:-\d+)?", path.stem):
        problems.append(f"{path.name}: name it {base}.md (from its heading)")
    body_lines = list(lines[1:])
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    tm = TRIAGE_RE.match(body_lines[-1]) if body_lines else None
    triage = None
    if not tm:
        problems.append(f"{path.name}: last line is not `Triage: now Lx | "
                        "target Lx | owner P | status S | note N`")
    else:
        triage = {"now": tm.group(1), "target": tm.group(2),
                  "owner": tm.group(3).strip(), "status": tm.group(4),
                  "note": (tm.group(5) or "").strip()}
        problems += [f"{path.name}: {p}" for p in triage_problems(triage)]
        body_lines.pop()
    if not path.read_text(encoding="utf-8").isascii():
        problems.append(f"{path.name}: non-ASCII text")
    return {"file": path.name, "date": date, "tags": tags, "title": title,
            "body": "\n".join(body_lines).strip("\n"),
            "triage": triage}, problems


def archive_rows(triage_file: Path | None = None) -> list[dict]:
    """The Register table of design/ladder-triage.md: one row per archive
    entry, `| n | learnings_line | entry | tags | now | target | owner |
    status | note |`. n and line are frozen with the archive."""
    tri = Path(triage_file) if triage_file else TRIAGE
    rows = []
    for ln in tri.read_text(encoding="utf-8").splitlines():
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 8 or not cells[0].isdigit():
            continue
        rows.append({"n": int(cells[0]), "line": int(cells[1]),
                     "entry": cells[2], "tags": cells[3], "now": cells[4],
                     "target": cells[5], "owner": cells[6],
                     "status": cells[7],
                     "note": cells[8] if len(cells) > 8 else ""})
    return rows


def _need_root_files(root: Path, tri: Path) -> None:
    """A copy of the skill vendored outside the hwde repo (the boards repo)
    has no root LEARNINGS.md or triage register: say so as a ValueError, which
    a promotion pass reports per entry, instead of a bare FileNotFoundError."""
    for f in (root, tri):
        if not f.is_file():
            raise ValueError(f"{f} not found - root promotion runs from the "
                             "hwde checkout, not a vendored copy of the skill")


def promote_to_root(ws: Path, entry_id: str, triage: dict,
                    root: Path | None = None,
                    triage_file: Path | None = None,
                    lessons_dir: Path | None = None) -> dict:
    """Write the workspace entry verbatim, with its triage row as the last
    line, to a new lesson file `learnings.d/<id>.md`. Refuses a lesson the
    archive or another lesson file already carries, and non-ASCII."""
    ws = Path(ws)
    root = Path(root) if root else ROOT_LEARNINGS
    tri = Path(triage_file) if triage_file else TRIAGE
    lessons = Path(lessons_dir) if lessons_dir else LESSONS

    entries, _ = parse_entries(learnings_path(ws).read_text(encoding="utf-8"))
    e = next((x for x in entries if x["entry"] == entry_id), None)
    if e is None:
        raise ValueError(f"{entry_id}: not in {learnings_path(ws)}")

    bad = triage_problems(triage)
    if bad:
        raise ValueError(f"{entry_id}: {bad[0]}")
    if not _exists(triage["owner"]) and "/" in triage["owner"]:
        raise ValueError(f"{entry_id}: triage owner {triage['owner']} "
                         "does not exist")

    header = f"## {e['date']} {''.join(f'[{t}]' for t in e['tags'])} {e['title']}"
    body = e["body"].strip("\n")
    provenance = (f"Promoted from {ws.as_posix()}/LEARNINGS.md "
                  f"(promotion pass {today()}).")
    row = triage_line(triage["now"], triage["target"], triage["owner"],
                      triage["status"], triage.get("note", ""))
    block = f"{header}\n{provenance}\n{body}\n\n{row}\n"
    if not block.isascii():
        raise ValueError(f"{entry_id}: non-ASCII text cannot be promoted")

    _need_root_files(root, tri)
    carried = [root.read_text(encoding="utf-8")] + [
        p.read_text(encoding="utf-8") for p in lesson_files(lessons)]
    if any(header in t.splitlines() for t in carried):
        raise ValueError(f"{entry_id}: the root learnings already carry "
                         "this entry")
    base = f"{e['date']}-{slug(e['title'])}"
    path, k = lessons / f"{base}.md", 1
    while path.exists():
        k += 1
        path = lessons / f"{base}-{k}.md"
    lessons.mkdir(parents=True, exist_ok=True)
    path.write_text(block, encoding="utf-8", newline="\n")
    return {"file": f"{lessons.name}/{path.name}", "row": row}


def triage_summary(triage_file: Path | None = None,
                   root: Path | None = None,
                   lessons_dir: Path | None = None) -> dict:
    """Every count the register used to keep by hand, computed from the
    archive's table plus the lesson files' own triage lines. Nothing in
    design/ladder-triage.md restates these numbers, so nothing goes stale."""
    tri = Path(triage_file) if triage_file else TRIAGE
    root = Path(root) if root else ROOT_LEARNINGS
    if not (tri.is_file() and root.is_file()):
        return {"rows": 0, "learnings_entries": 0, "archive_entries": 0,
                "lessons": 0, "levels": {}, "by_status": {}, "climbing": 0,
                "open_by_owner": {}, "problems": [],
                "absent": "no root LEARNINGS.md / triage register here "
                          "(vendored copy of the skill)"}
    rows = archive_rows(tri)
    archive = _root_entries(root.read_text(encoding="utf-8"))
    problems: list[str] = []
    files = lesson_files(lessons_dir)
    for p in files:
        lesson, bad = parse_lesson(p)
        problems += bad
        if lesson.get("triage"):
            rows.append(lesson["triage"])
    levels = {lv: {"now": sum(1 for r in rows if r["now"] == lv),
                   "target": sum(1 for r in rows if r["target"] == lv)}
              for lv in LEVELS}
    status: dict[str, int] = {}
    owners: dict[str, int] = {}
    for r in rows:
        key = r["status"] if r["status"] in ("done", "open", "n/a") else "planned"
        status[key] = status.get(key, 0) + 1
        if r["status"] == "open":
            owners[r["owner"]] = owners.get(r["owner"], 0) + 1
    return {"rows": len(rows), "learnings_entries": len(archive) + len(files),
            "archive_entries": len(archive), "lessons": len(files),
            "levels": levels, "by_status": status,
            "climbing": sum(1 for r in rows
                            if r["now"] in LEVELS and r["target"] in LEVELS
                            and LEVELS.index(r["target"]) > LEVELS.index(r["now"])),
            "open_by_owner": dict(sorted(owners.items(),
                                         key=lambda kv: (-kv[1], kv[0]))),
            "problems": problems}


def sweep(boards_dir: Path) -> list[dict]:
    """Every workspace that captures learnings, with its queue state. The
    general-agent operator mode reads this to pick what to work on."""
    out = []
    for ws in sorted(Path(boards_dir).iterdir()):
        if not (ws.is_dir() and learnings_path(ws).is_file()):
            continue
        entries, malformed = parse_entries(
            learnings_path(ws).read_text(encoding="utf-8"))
        queue = load_queue(ws)
        rows = (queue or {}).get("entries") or []
        counts = {s: sum(1 for r in rows if r.get("status") == s)
                  for s in STATUSES}
        out.append({"board": ws.name, "workspace": ws.as_posix(),
                    "entries": len(entries), "queued": len(rows),
                    "uncompiled": max(0, len(entries) - len(rows)),
                    "malformed": len(malformed), **counts})
    return out
