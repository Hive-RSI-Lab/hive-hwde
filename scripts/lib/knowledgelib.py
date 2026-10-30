"""knowledgelib - the class-indexed knowledge library (U4, v3 design decision 2)
+ coverage contracts (U13, design decision 5a).

One RECORD per file under reference/knowledge/records/<id>.yaml. A record is a
CLASS-level engineering fact (power-loop, EMI, thermal-via, ...) with the keys
that make retrieval DETERMINISTIC - the same trigger-not-judgment principle as
reference/remediations/ (T4): at runtime nobody decides they "need EMI
knowledge"; a board DECLARES a buck block / an SO-8-EP package, and the
matching records are injected by exact key lookup.

Record shape (schema-validated; the single source of truth is RECORD_SCHEMA):

  id: buck-input-hot-loop            # == filename stem, unique
  classes: [power-loop, emi]         # controlled vocab: CLASSES
  applies:                           # >= 1 non-empty list, else unreachable
    topologies: [buck]               # keyed by constraints.json blocks[]
    packages: [SO-8-EP]              # keyed by parts.json parts[].package
    interfaces: [usb]                # keyed by diff_pairs[].base / --interfaces
    parts: [AP63203, C5248536]       # keyed by parts.json mpn / lcsc (U13)
  rule:                              # machine-checkable fields, or null
    enforced_by: null                #   script[:--flag] that enforces it (must
    hf_bypass_nf: 100                #   exist), null = not yet enforced
  prose: >                           # the fact + why; <= PROSE_MAX chars -
    ...                              #   this text is PROMPT-INJECTED
  sources:                           # down to the page/section
    - {file: reference/knowledge/sources/x.pdf, page: 4}
  status: active                     # active | draft | superseded (lifecycle)
  origin: migration:topologies/buck  # where the record came from
  # --- schema v2 (U13): the coverage contract -------------------------
  level: topology                    # principle|topology|family|part|instance
  envelope:                          # WHAT THE MECHANISM VARIES WITH, as
    edge_ns: {max: 20}               #   unit-suffixed dims (see ENVELOPE_UNITS)
    switching_kind: {in: [hard]}     #   or *_kind categorical dims; required
                                     #   at topology/family/part, forbidden
                                     #   at principle, optional at instance
  maturity: approved                 # draft|verified|approved|proven
  approval: {by: owner, date: "2026-08-16"}   # REQUIRED when approved
  evidence: [{workspace: boards/x, board: x,  # REQUIRED when proven (bring-up
              event: bringup_passed, date: "2026-09-01"}] # evidence, --prove)
  generalizes: [hot-loop-principle]  # more-general parents (ids must exist)

Authoring an envelope (U14 rulings): an envelope is what BOUNDS the rule -
"where does this stop being TRUE?" - never the numbers the rule happens to
carry (a record quoting both 1 oz and 2 oz widths spans copper weight; its
bound is hard switching). When nothing bounds it, the record is a
`principle`, which is why the schema forbids an envelope there and requires
one above: level and envelope are ONE decision. Prefer one dim, and only
dims P2 can declare - an undeclared dim leaves the record `provisional`.

QUOTE EVERY DATE: unquoted YAML dates load as datetime.date, not str (the
lint names it, LEARNINGS 2026-08-15 [yaml][knowledge]).

Legacy records (pre-U14 backfill) tolerate the missing v2 fields: level None,
maturity `draft`, no envelope. validate(strict=True) is the post-backfill
mode that requires them - the committed library has been strict-green since
the U14 backfill (all 16 records owner-approved, 2026-08-15).

Coverage checklists live under reference/knowledge/checklists/<id>.yaml
(CHECKLIST_SCHEMA): per topology/interface, the classes + minimum levels that
must be populated before designing it. They carry maturity + approval like a
record; select() never injects them.

coverage(ws) is the structural "I know enough to design this" test: per
block/part/interface slot -> covered | provisional | gap, from (1) the
deterministic key query + envelope containment + maturity floor, (2) an
optional agent MAPPING (record->slot edges only - it classifies, it may not
declare sufficiency; validated against MAPPING_SCHEMA), (3) the mechanical
cutoff per class from the checklist. Gap entries are research task specs -
the `research` verb's input (U15, scripts/research.py + lib/researchlib.py):
its outputs land WORKSPACE-FIRST under <ws>/research/records + checklists
(status draft until the second reader verifies; maturity draft/verified),
and coverage + `--select --workspace` fold them in (`_workspace` marker,
library wins on an id clash) so a researched class reads provisional, not
gap, until the owner approves the promoted copy.

Sources PDFs live under reference/knowledge/sources/. Retrieval consumers:
scripts/knowledge.py (CLI), the orchestrator spawn step (docs/playbook.md), and
datasheet_extract.py --app-note (record-shaped extraction template).

Matching is normalized: topology/interface tokens casefold; package and part
tokens additionally drop non-alphanumerics ("SO-8-EP" == "SO8EP" == "so-8 ep")
- vendor package strings are not stable enough for exact string equality.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import yaml

import bringuplib

LIB = Path(__file__).resolve().parent
SCRIPTS = LIB.parent
SKILL = SCRIPTS.parent
REPO = SKILL
RECORDS_DIR = SKILL / "reference" / "knowledge" / "records"
# The source PDFs are the manufacturers' (NOTICE), so a copy of the skill that
# doesn't carry them (the Hive's hwde module) points here at where they are.
SOURCES_DIR = (Path(os.environ["HWDE_KNOWLEDGE_SOURCES"])
               if os.environ.get("HWDE_KNOWLEDGE_SOURCES")
               else SKILL / "reference" / "knowledge" / "sources")
CHECKLISTS_DIR = SKILL / "reference" / "knowledge" / "checklists"

# Controlled class vocabulary (grow it here when a NEW class is real - same
# pattern as cluster_violations.FIXER_HINTS; tests pin committed records to it).
CLASSES = frozenset({
    "power-loop", "emi", "thermal", "thermal-via", "decoupling", "feedback",
    "selection", "sourcing", "inrush", "sequencing", "return-path",
    "constraints-emission", "esd", "creepage",
    "diff-pair",    # U14: controlled-impedance pair rules (Zdiff, skew,
                    # stubs/vias) - what the interface checklists require and
                    # no record holds yet
})
STATUSES = ("active", "draft", "superseded")
PROSE_MAX = 1500  # prompt-injected; a record is a focused fact, not an essay

# --- schema v2 (U13): coverage contract vocab --------------------------------
SCHEMA_VERSION = 2
LEVELS = ("principle", "topology", "family", "part", "instance")
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}
MATURITIES = ("draft", "verified", "approved", "proven")
MATURITY_RANK = {m: i for i, m in enumerate(MATURITIES)}
# Bootstrap mode (owner ruling): only owner-approved (or bench-proven) records
# satisfy coverage. The floor is a coverage() parameter so it can decay later.
DEFAULT_MATURITY_FLOOR = "approved"
ENVELOPE_REQUIRED_AT = ("topology", "family", "part")
ENVELOPE_FORBIDDEN_AT = ("principle",)
# Numeric envelope dims are unit-suffixed keys (`vin_v`, `edge_ns`, `iout_a`);
# categorical dims end in `_kind` (`switching_kind: {in: [hard]}`).
ENVELOPE_UNITS = (
    "v", "mv", "kv", "a", "ma", "ua", "w", "mw", "kw",
    "hz", "khz", "mhz", "ghz", "ns", "us", "ms", "s",
    "c", "k", "mm", "um", "cm", "m",
    "pf", "nf", "uf", "mf", "nh", "uh", "mh",
    "ohm", "mohm", "kohm", "mohms", "pct", "oz",
    "mbps", "gbps", "bps", "layers", "pins",
    "v_per_ns", "v_per_us", "a_per_ns", "a_per_us",
)
CATEGORICAL_SUFFIX = "_kind"
# Part slots: a P3 datasheet extraction (parts/<lcsc>.json) whose layout_notes
# has fewer entries than this is a THIN layout section = a detectable gap.
LAYOUT_NOTES_MIN = 2
IC_REF_HINTS = ("U",)
BRINGUP_EVENT = "bringup_passed"

_APPROVAL_SHAPE = {
    "type": "object",
    "required": ["by", "date"],
    "additionalProperties": False,
    "properties": {
        "by": {"type": "string", "minLength": 1},
        "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "note": {"type": "string"},
    },
}
_EVIDENCE_ITEM = {
    "type": "object",
    "required": ["workspace", "event", "date"],
    "additionalProperties": False,
    "properties": {
        "workspace": {"type": "string", "minLength": 1},
        "board": {"type": "string"},
        "event": {"type": "string", "minLength": 1},
        "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "results": {"type": "string"},  # the bring-up results file it cites
        "note": {"type": "string"},
    },
}

RECORD_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "hwde knowledge record",
    "type": "object",
    "required": ["id", "classes", "applies", "rule", "prose", "sources",
                 "status", "origin"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
        "classes": {"type": "array", "minItems": 1,
                    "items": {"type": "string"}},
        "applies": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "topologies": {"type": "array", "items": {"type": "string"}},
                "packages": {"type": "array", "items": {"type": "string"}},
                "interfaces": {"type": "array", "items": {"type": "string"}},
                "parts": {"type": "array", "items": {"type": "string"}},
            },
        },
        "rule": {
            "oneOf": [
                {"type": "null"},
                {"type": "object", "minProperties": 1,
                 "properties": {"enforced_by": {"type": ["string", "null"]}},
                 "additionalProperties": {
                     "type": ["string", "number", "boolean", "null"]}},
            ],
        },
        "prose": {"type": "string", "minLength": 1, "maxLength": PROSE_MAX},
        "sources": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["file"],
                "additionalProperties": False,
                "properties": {
                    "file": {"type": "string", "minLength": 1},
                    "page": {"type": "integer", "minimum": 1},
                    "section": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
        "status": {"type": "string", "enum": list(STATUSES)},
        "origin": {"type": "string", "minLength": 1},
        # --- schema v2 (U13) - optional in bootstrap, required by strict ---
        "level": {"type": "string", "enum": list(LEVELS)},
        "envelope": {"type": ["object", "null"]},   # dims linted in code
        # U15: why THESE dims bound the rule (what it scales with) - the
        # researcher's justification; research.py validate requires it on
        # every research record that carries an envelope. The owner's
        # approval.note supersedes it at approval.
        "envelope_note": {"type": "string", "minLength": 1,
                          "maxLength": 600},
        "maturity": {"type": "string", "enum": list(MATURITIES)},
        "generalizes": {"type": "array", "items": {"type": "string"}},
        "approval": _APPROVAL_SHAPE,
        "verification": _APPROVAL_SHAPE,
        "evidence": {"type": "array", "items": _EVIDENCE_ITEM},
    },
}

CHECKLIST_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "hwde coverage checklist",
    "type": "object",
    "required": ["id", "kind", "applies", "requires", "maturity", "origin"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
        "kind": {"const": "coverage-checklist"},
        "applies": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "topologies": {"type": "array", "items": {"type": "string"}},
                "interfaces": {"type": "array", "items": {"type": "string"}},
            },
        },
        "requires": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["class", "min_level"],
                "additionalProperties": False,
                "properties": {
                    "class": {"type": "string"},
                    "min_level": {"type": "string", "enum": list(LEVELS)},
                    "note": {"type": "string"},
                },
            },
        },
        "maturity": {"type": "string", "enum": list(MATURITIES)},
        "approval": _APPROVAL_SHAPE,
        "sources": {"type": "array", "items": RECORD_SCHEMA["properties"]
                    ["sources"]["items"]},
        "prose": {"type": "string", "maxLength": PROSE_MAX},
        "origin": {"type": "string", "minLength": 1},
    },
}

# The schema-forced output of the coverage-mapper agent (agents/
# coverage-mapper.md): record -> slot edges for ONE class each. No verdicts,
# no sufficiency, no confidence - the mechanical cutoff decides.
MAPPING_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "hwde coverage mapping (agent output)",
    "type": "object",
    "required": ["mappings"],
    "additionalProperties": False,
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["record", "slot", "class", "why"],
                "additionalProperties": False,
                "properties": {
                    "record": {"type": "string", "minLength": 1},
                    "slot": {"type": "string", "minLength": 1},
                    "class": {"type": "string", "minLength": 1},
                    "why": {"type": "string", "minLength": 1,
                            "maxLength": 300},
                },
            },
        },
        "note": {"type": "string", "maxLength": 600},
    },
}


def blank_record() -> dict:
    """Schema-shaped skeleton (the --app-note extraction template)."""
    return {
        "id": "",
        "classes": [],
        "applies": {"topologies": [], "packages": [], "interfaces": [],
                    "parts": []},
        "rule": None,
        "prose": "",
        "sources": [{"file": "reference/knowledge/sources/<pdf>", "page": 1}],
        "status": "active",
        "origin": "app-note:<pdf>",
        "level": "topology",
        "envelope": {"<dim>_<unit>": {"min": 0, "max": 0}},
        "maturity": "draft",
        "generalizes": [],
    }


# ---------------------------------------------------------------------------
# normalization + loading
# ---------------------------------------------------------------------------
def norm_token(s: str) -> str:
    """Topology / interface / class key normalization."""
    return str(s).strip().casefold()


def norm_pkg(s: str) -> str:
    """Package / part key normalization: casefold + drop non-alphanumerics, so
    'SO-8-EP' == 'SO-8EP' == 'so8 ep' (vendor strings are not stable)."""
    return re.sub(r"[^a-z0-9]", "", str(s).casefold())


def record_files(records_dir: Path | str | None = None) -> list[Path]:
    d = Path(records_dir) if records_dir else RECORDS_DIR
    return sorted(d.glob("*.yaml"))


def load_records(records_dir: Path | str | None = None) -> list[dict]:
    """Parsed records, each with '_path' (skill-relative posix). Unparseable
    YAML raises - validate() reports it instead of raising."""
    out = []
    for p in record_files(records_dir):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            try:
                data["_path"] = p.relative_to(SKILL).as_posix()
            except ValueError:
                data["_path"] = p.as_posix()
            out.append(data)
    return out


def checklist_files(checklists_dir: Path | str | None = None) -> list[Path]:
    d = Path(checklists_dir) if checklists_dir else CHECKLISTS_DIR
    return sorted(d.glob("*.yaml")) if d.is_dir() else []


def load_checklists(checklists_dir: Path | str | None = None) -> list[dict]:
    out = []
    for p in checklist_files(checklists_dir):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            try:
                data["_path"] = p.relative_to(SKILL).as_posix()
            except ValueError:
                data["_path"] = p.as_posix()
            out.append(data)
    return out


def record_level(r: dict) -> str | None:
    lv = r.get("level")
    return lv if lv in LEVEL_RANK else None


def record_maturity(r: dict) -> str:
    """Missing maturity = draft (bootstrap tolerance)."""
    m = r.get("maturity")
    return m if m in MATURITY_RANK else "draft"


# ---------------------------------------------------------------------------
# envelopes + operating points
# ---------------------------------------------------------------------------
def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def dim_kind(key: str) -> str | None:
    """'numeric' for a unit-suffixed key, 'categorical' for *_kind, else
    None (the key can never match an envelope dim)."""
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
        return None
    if key.endswith(CATEGORICAL_SUFFIX) and len(key) > len(CATEGORICAL_SUFFIX):
        return "categorical"
    for u in ENVELOPE_UNITS:
        if key.endswith("_" + u) and len(key) > len(u) + 1:
            return "numeric"
    return None


def envelope_problems(env, where: str = "envelope") -> list[str]:
    """Grammar check for one record's envelope. Empty list = well-formed."""
    if env is None:
        return []
    if not isinstance(env, dict):
        return [f"{where}: must be a mapping of dims"]
    problems = []
    for key, val in env.items():
        kind = dim_kind(key)
        if kind is None:
            problems.append(
                f"{where}.{key}: dim key must be unit-suffixed "
                f"(one of _{', _'.join(ENVELOPE_UNITS[:8])}, ...) or end in "
                f"{CATEGORICAL_SUFFIX}")
            continue
        if not isinstance(val, dict) or not val:
            problems.append(f"{where}.{key}: value must be a mapping "
                            "({min/max} or {in: [...]})")
            continue
        if kind == "categorical":
            if set(val) != {"in"} or not isinstance(val["in"], list) \
                    or not val["in"] or not all(
                        isinstance(x, str) and x for x in val["in"]):
                problems.append(f"{where}.{key}: categorical dim needs "
                                "exactly {in: [non-empty str, ...]}")
        else:
            if not set(val) <= {"min", "max"}:
                problems.append(f"{where}.{key}: numeric dim allows only "
                                "min/max")
            for b in ("min", "max"):
                if b in val and not _is_num(val[b]):
                    problems.append(f"{where}.{key}.{b}: must be a number")
            if _is_num(val.get("min")) and _is_num(val.get("max")) \
                    and val["min"] > val["max"]:
                problems.append(f"{where}.{key}: min > max")
    return problems


def operating_point_problems(op, where: str = "operating_point") -> list[str]:
    """Advisory: dims a workspace declares that can never match a record."""
    if op is None:
        return []
    if not isinstance(op, dict):
        return [f"{where}: must be a mapping"]
    out = []
    for key, val in op.items():
        kind = dim_kind(key)
        if kind is None:
            out.append(f"{where}.{key}: not a unit-suffixed or *_kind dim - "
                       "no envelope can ever test it")
        elif kind == "numeric" and not _is_num(val):
            out.append(f"{where}.{key}: numeric dim needs a number, got "
                       f"{json.dumps(val)[:40]}")
        elif kind == "categorical" and not isinstance(val, str):
            out.append(f"{where}.{key}: categorical dim needs a string token")
    return out


def envelope_contains(env, op) -> dict:
    """Does the operating point sit inside the envelope?

    verdict: 'inside'  every envelope dim is declared in op and satisfied
             'outside' >= 1 declared dim violates its bound / set
             'unknown' no violation, but >= 1 envelope dim is undeclared in op
             'n/a'     the record carries no envelope (principle / legacy)
    Dims in op that the envelope does not mention are ignored - a record's
    envelope lists what its mechanism varies with, nothing else."""
    if not env:
        return {"verdict": "n/a", "unknown_dims": [], "outside_dims": []}
    op = op or {}
    unknown, outside = [], []
    for key, bound in env.items():
        kind = dim_kind(key)
        if key not in op or not isinstance(bound, dict):
            unknown.append(key)
            continue
        v = op[key]
        if kind == "categorical":
            allowed = {norm_token(x) for x in bound.get("in") or []}
            if not isinstance(v, str) or norm_token(v) not in allowed:
                outside.append(key)
        else:
            if not _is_num(v):
                unknown.append(key)
                continue
            lo, hi = bound.get("min"), bound.get("max")
            if (_is_num(lo) and v < lo) or (_is_num(hi) and v > hi):
                outside.append(key)
    if outside:
        verdict = "outside"
    elif unknown:
        verdict = "unknown"
    else:
        verdict = "inside"
    return {"verdict": verdict, "unknown_dims": unknown,
            "outside_dims": outside}


# ---------------------------------------------------------------------------
# validation (validate_registry-style: every named artifact must exist)
# ---------------------------------------------------------------------------
# A source whose licence forbids redistribution (a paid or restricted standard)
# is not committed: what ships in its place is a sidecar `<file>.not-redistributed.md`
# naming the public download url and the sha256 of the copy that was read. The
# citation stays checkable (and re-readable by anyone who downloads it), the
# bytes stay out of the repo.
NOT_REDISTRIBUTED_SUFFIX = ".not-redistributed.md"


def _boards_root() -> Path:
    try:
        from lib import env  # noqa: PLC0415
    except ImportError:
        import env  # type: ignore  # noqa: PLC0415
    return env.boards_root()


def _source_exists(file: str, roots=()) -> bool:
    """Source files resolve skill-relative or repo-relative - plus any extra
    `roots` (U15: a workspace, whose research records cite
    `research/sources/<file>` relative to the workspace root). A
    `<file>.not-redistributed.md` sidecar stands in for a source that may not
    be redistributed.

    A `boards/<ws>/...` source cites a board workspace, which lives in the
    boards repo (env.boards_root()), not in the skill: it resolves there, and
    when that repo is not cloned it cannot be checked, so it is not reported
    missing. An empty root counts as not cloned: a check run without the
    boards repo points HWDE_BOARDS_ROOT at an empty dir. <ws> may be a
    board's bare name from before its workspace was renamed <PN>_<name>;
    boardreg.resolve finds it through the register.

    A root with no register.yaml is a project's own boards/ (a Hive module
    holding a board or two), not the boards repo: it answers only for the
    workspaces it holds, and a citation of a board it doesn't hold (one of
    the boards repo's) cannot be checked there either."""
    if not file or file != file.strip():
        return False
    if file.startswith("boards/"):
        broot = _boards_root()
        if not broot.is_dir() or not any(broot.iterdir()):
            return True
        ws, _, rest = file[len("boards/"):].partition("/")
        try:
            from lib import boardreg  # noqa: PLC0415
        except ImportError:
            import boardreg  # type: ignore  # noqa: PLC0415
        ws_dir = boardreg.resolve(ws, broot)
        if ws_dir is None and not (broot / boardreg.REGISTER).is_file():
            return True
        ws_dir = ws_dir or broot / ws
        file_in_boards = ws_dir / rest
        if file_in_boards.is_file() or file_in_boards.with_name(
                file_in_boards.name + NOT_REDISTRIBUTED_SUFFIX).is_file():
            return True
    # a library source, cited skill-relative, may live in SOURCES_DIR instead
    # (HWDE_KNOWLEDGE_SOURCES: a copy of the skill that doesn't carry them)
    lib_prefix = "reference/knowledge/sources/"
    if file.startswith(lib_prefix):
        file_in_sources = SOURCES_DIR / file[len(lib_prefix):]
        if file_in_sources.is_file() or file_in_sources.with_name(
                file_in_sources.name + NOT_REDISTRIBUTED_SUFFIX).is_file():
            return True
    for base in (SKILL, REPO, *(Path(r) for r in roots)):
        p = base / file
        if p.is_file() or p.with_name(p.name + NOT_REDISTRIBUTED_SUFFIX).is_file():
            return True
    return False


def _script_flag_problems(where: str, text: str) -> list[str]:
    """T4 remediation rule, applied to record prose: every *.py named must
    exist in the repo, and every --flag on a line naming one of OUR scripts
    must appear in that script's source."""
    problems = []
    real = {p.name for p in SCRIPTS.rglob("*.py")} | \
           {p.name for p in (REPO / "tests").rglob("*.py")}
    for name in set(re.findall(r"\b([a-z_][a-z0-9_]*\.py)\b", text)):
        if name not in real:
            problems.append(f"{where}: names non-existent script {name}")
    for ln in text.splitlines():
        m = re.search(r"\b([a-z_][a-z0-9_]*)\.py\b", ln)
        if not m:
            continue
        script = SCRIPTS / f"{m.group(1)}.py"
        if not script.is_file():
            continue
        src = script.read_text(encoding="utf-8")
        for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", ln):
            if flag not in src:
                problems.append(
                    f"{where}: {m.group(1)}.py has no {flag}")
    return problems


def _load_yaml_checked(p: Path, problems: list[str]) -> dict | None:
    """Read + parse one hand-authored YAML file with the ASCII + mapping
    checks; appends problems, returns the dict or None."""
    where = p.name
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        problems.append(f"{where}: unreadable ({exc})")
        return None
    try:
        raw.encode("ascii")
    except UnicodeEncodeError as exc:
        problems.append(f"{where}: not ASCII-safe ({exc})")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        problems.append(f"{where}: invalid YAML ({exc})")
        return None
    # YAML escapes can smuggle non-ASCII past the raw check, and record
    # text is prompt-injected verbatim - check the PARSED values too.
    try:
        json.dumps(data, ensure_ascii=False).encode("ascii")
    except UnicodeEncodeError:
        problems.append(f"{where}: not ASCII-safe (parsed values)")
    except TypeError as exc:
        # YAML 1.1 implicit typing: an unquoted 2026-08-15 loads as a
        # datetime.date, which is neither the string the schema wants nor
        # JSON-serializable - report it here instead of dying in --out.
        problems.append(
            f"{where}: YAML implicit typing produced a non-JSON value "
            f"({exc}) - quote it (an unquoted date becomes a date object, "
            "not a string)")
        return None
    if not isinstance(data, dict):
        problems.append(f"{where}: not a mapping")
        return None
    return data


def _governance_problems(where: str, data: dict) -> list[str]:
    """Maturity governance (design decision 5a): approved needs the owner's
    sign-off on file; proven needs bring-up evidence on file."""
    problems = []
    m = data.get("maturity")
    if m == "approved" and not isinstance(data.get("approval"), dict):
        problems.append(f"{where}: maturity approved requires an approval "
                        "block {by, date} (owner sign-off)")
    if m == "proven" and not data.get("evidence"):
        problems.append(f"{where}: maturity proven requires non-empty "
                        f"evidence[] (bring-up events; knowledge.py --prove "
                        "writes them)")
    return problems


def validate(records_dir: Path | str | None = None,
             checklists_dir: Path | str | None = None,
             strict: bool = False, source_roots=(),
             extra_records: list[dict] | None = None) -> list[str]:
    """Structural problems across the records dir + checklists dir, as
    human-readable strings. Empty list = the library is internally consistent
    and every named artifact exists. Run by knowledge.py --validate and the
    test suite.

    strict=False (bootstrap): the v2 fields (level / envelope / maturity) may
    be missing - such records read as level None, maturity draft, and never
    satisfy coverage. strict=True: they are required (post-U14 backfill).
    source_roots: extra roots a `sources[].file` may resolve against (U15
    workspace research records cite workspace-relative paths).
    extra_records: already-parsed records (the LIBRARY, when linting a
    workspace research dir) that count as `generalizes` targets - a research
    record points at its principle parent in the library, and that parent is
    not in the dir being linted."""
    import jsonschema
    problems: list[str] = []
    seen_ids: dict[str, str] = {}
    validator = jsonschema.Draft202012Validator(RECORD_SCHEMA)
    parsed: dict[str, dict] = {}

    for p in record_files(records_dir):
        where = p.name
        data = _load_yaml_checked(p, problems)
        if data is None:
            continue

        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
            path = "/".join(str(x) for x in err.path) or "(top)"
            problems.append(f"{where}: schema {path}: {err.message}")
        rid = data.get("id")
        if rid and rid != p.stem:
            problems.append(f"{where}: id {rid!r} != filename stem {p.stem!r}")
        if rid in seen_ids:
            problems.append(f"{where}: duplicate id {rid!r} (also {seen_ids[rid]})")
        elif rid:
            seen_ids[rid] = where
            parsed[rid] = data

        for c in data.get("classes") or []:
            if norm_token(c) not in CLASSES:
                problems.append(
                    f"{where}: unknown class {c!r} (grow knowledgelib.CLASSES "
                    "if the class is real)")
        applies = data.get("applies") or {}
        if isinstance(applies, dict) and not any(
                applies.get(k) for k in ("topologies", "packages",
                                         "interfaces", "parts")):
            problems.append(
                f"{where}: applies has no keys at all - no board can ever "
                "retrieve this record (the T4 unreachable-trigger failure mode)")

        for i, src in enumerate(data.get("sources") or []):
            if isinstance(src, dict) and not _source_exists(
                    src.get("file", ""), source_roots):
                problems.append(
                    f"{where}: sources[{i}].file {src.get('file')!r} not found "
                    "(skill-, repo- or boards-root-relative)")
        rule = data.get("rule")
        if isinstance(rule, dict) and rule.get("enforced_by"):
            ref = str(rule["enforced_by"])
            script = ref.split(":", 1)[0]
            spath = SCRIPTS / script
            if not script.endswith(".py") or not spath.is_file():
                problems.append(
                    f"{where}: rule.enforced_by names non-existent script {ref!r}")
            elif ":" in ref:
                token = ref.split(":", 1)[1]
                if token not in spath.read_text(encoding="utf-8"):
                    problems.append(
                        f"{where}: rule.enforced_by {ref!r}: {token!r} not "
                        f"found in {script}")
        problems += _script_flag_problems(where, data.get("prose") or "")

        # --- schema v2: level / envelope / maturity / generalizes ---------
        level = data.get("level")
        env = data.get("envelope")
        problems += envelope_problems(env, f"{where}: envelope")
        if level in ENVELOPE_FORBIDDEN_AT and env:
            problems.append(f"{where}: level {level} carries no envelope "
                            "(a principle applies wherever its class does)")
        if level in ENVELOPE_REQUIRED_AT and not env:
            problems.append(f"{where}: level {level} requires a non-empty "
                            "envelope (what does this rule scale with?)")
        if strict:
            for f in ("level", "maturity"):
                if f not in data:
                    problems.append(f"{where}: strict: missing {f}")
        problems += _governance_problems(where, data)

    # generalizes targets: must exist, not self, and be MORE general
    targets = dict(parsed)
    for r in extra_records or []:
        if isinstance(r, dict) and r.get("id") and r["id"] not in targets:
            targets[r["id"]] = r
    for rid, data in parsed.items():
        where = seen_ids[rid]
        for tgt in data.get("generalizes") or []:
            if tgt == rid:
                problems.append(f"{where}: generalizes itself")
            elif tgt not in targets:
                problems.append(f"{where}: generalizes unknown record {tgt!r}")
            else:
                mine, theirs = record_level(data), record_level(targets[tgt])
                if mine and theirs and LEVEL_RANK[theirs] >= LEVEL_RANK[mine]:
                    problems.append(
                        f"{where}: generalizes {tgt!r} but that record is not "
                        f"more general ({theirs} vs {mine})")

    # --- checklists ---------------------------------------------------------
    cvalidator = jsonschema.Draft202012Validator(CHECKLIST_SCHEMA)
    claimed: dict[str, str] = {}
    for p in checklist_files(checklists_dir):
        where = f"checklists/{p.name}"
        data = _load_yaml_checked(p, problems)
        if data is None:
            continue
        for err in sorted(cvalidator.iter_errors(data),
                          key=lambda e: list(e.path)):
            path = "/".join(str(x) for x in err.path) or "(top)"
            problems.append(f"{where}: schema {path}: {err.message}")
        cid = data.get("id")
        if cid and cid != p.stem:
            problems.append(f"{where}: id {cid!r} != filename stem {p.stem!r}")
        applies = data.get("applies") or {}
        toks = []
        if isinstance(applies, dict):
            toks = [("topology", norm_token(t)) for t in applies.get("topologies") or []] \
                + [("interface", norm_token(t)) for t in applies.get("interfaces") or []]
        if not toks:
            problems.append(f"{where}: applies has no topologies/interfaces - "
                            "no slot can ever use this checklist")
        for kind, tok in toks:
            key = f"{kind}:{tok}"
            if key in claimed:
                problems.append(f"{where}: {key} already has checklist "
                                f"{claimed[key]} (one checklist per slot type)")
            claimed[key] = p.name
        for i, req in enumerate(data.get("requires") or []):
            if isinstance(req, dict) and norm_token(req.get("class", "")) \
                    not in CLASSES:
                problems.append(f"{where}: requires[{i}].class "
                                f"{req.get('class')!r} not in CLASSES")
        # Alias drift: coverage() selects records by the slot's own token, so
        # when a record covers a required class under one of the checklist's
        # tokens but not another, a slot spelled the other way reads `gap`.
        # (A class no token holds yet is a research gap, not drift.)
        held = {(f, norm_token(t), norm_token(c))
                for r in parsed.values() if r.get("status") == "active"
                for f in ("topologies", "interfaces")
                for t in (r.get("applies") or {}).get(f) or []
                for c in r.get("classes") or []}
        for req in data.get("requires") or []:
            cls = norm_token(req.get("class", "")) \
                if isinstance(req, dict) else ""
            have = [(k, t) for k, t in toks
                    if (("topologies" if k == "topology" else "interfaces"),
                        t, cls) in held]
            if have:
                for kind, tok in toks:
                    if (kind, tok) not in have:
                        problems.append(
                            f"{where}: {kind} {tok!r} has no active record "
                            f"for required class {cls!r}, though "
                            f"{have[0][0]} {have[0][1]!r} does (give the "
                            "record the checklist's tokens)")
        for i, src in enumerate(data.get("sources") or []):
            if isinstance(src, dict) and not _source_exists(
                    src.get("file", ""), source_roots):
                problems.append(f"{where}: sources[{i}].file "
                                f"{src.get('file')!r} not found")
        problems += _script_flag_problems(where, data.get("prose") or "")
        problems += _governance_problems(where, data)
    return problems


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------
def select(records: list[dict], topologies=(), packages=(),
           interfaces=(), parts=()) -> list[dict]:
    """ACTIVE records matching any key, sorted by id. Draft/superseded records
    are never injected."""
    tops = {norm_token(t) for t in topologies}
    pkgs = {norm_pkg(p) for p in packages}
    ifs = {norm_token(i) for i in interfaces}
    prts = {norm_pkg(p) for p in parts}
    out = []
    for r in records:
        if r.get("status") != "active":
            continue
        ap = r.get("applies") or {}
        hit = (
            tops & {norm_token(t) for t in ap.get("topologies") or []}
            or pkgs & {norm_pkg(p) for p in ap.get("packages") or []}
            or ifs & {norm_token(i) for i in ap.get("interfaces") or []}
            or prts & {norm_pkg(p) for p in ap.get("parts") or []}
        )
        if hit:
            out.append(r)
    return sorted(out, key=lambda r: r.get("id") or "")


def _ws_paths(ws: Path) -> dict:
    """The workspace's constraints / parts locations via statelib.kind_path
    with the pre-P5 fallbacks (architecture/constraints.json, parts/parts.json)."""
    import statelib
    state: dict = {}
    sp = ws / "state.json"
    if sp.is_file():
        state = json.loads(sp.read_text(encoding="utf-8"))
    board = state.get("board") or ws.name
    registry = state.get("artifacts") or {}
    imap = statelib.load_map()
    cpath = ws / statelib.kind_path("constraints", board, imap, registry, ws)
    if not cpath.is_file():
        cpath = ws / "architecture" / "constraints.json"
    ppath = ws / statelib.kind_path("parts", board, imap, registry, ws)
    if not ppath.is_file():
        ppath = ws / "parts" / "parts.json"
    return {"state": state, "board": board, "constraints": cpath,
            "parts": ppath}


def workspace_keys(ws: Path | str) -> dict:
    """Deterministic retrieval keys for a workspace - NO agent judgment.

    topologies: constraints.json blocks[].topology (P2's block list; the
      board-side sidecar via statelib.kind_path, falling back to
      architecture/constraints.json before P5).
    packages:   parts.json parts[].package (P3's chosen packages; kind_path,
      falling back to parts/parts.json - the pre-P5 layout).
    interfaces: diff_pairs[].base from the same constraints file.
    parts:      parts.json parts[].mpn + parts[].lcsc (U13 part-level records).
    """
    ws = Path(ws)
    paths = _ws_paths(ws)
    keys = {"topologies": [], "packages": [], "interfaces": [], "parts": [],
            "sources": {}}
    cpath, ppath = paths["constraints"], paths["parts"]
    if cpath.is_file():
        data = json.loads(cpath.read_text(encoding="utf-8"))
        blocks = data.get("blocks") or []
        keys["topologies"] = sorted({
            norm_token(b["topology"]) for b in blocks
            if isinstance(b, dict) and b.get("topology")})
        keys["interfaces"] = sorted({
            norm_token(d["base"]) for d in data.get("diff_pairs") or []
            if isinstance(d, dict) and d.get("base")})
        keys["sources"]["constraints"] = cpath.as_posix()
    if ppath.is_file():
        data = json.loads(ppath.read_text(encoding="utf-8"))
        parts = [p for p in data.get("parts") or [] if isinstance(p, dict)]
        keys["packages"] = sorted({str(p["package"]) for p in parts
                                   if p.get("package")})
        keys["parts"] = sorted({str(p[k]) for p in parts for k in ("mpn", "lcsc")
                                if p.get(k)})
        keys["sources"]["parts"] = ppath.as_posix()
    return keys


def _tag(r: dict) -> str:
    tag = f"{record_level(r) or 'level?'}/{record_maturity(r)}"
    if r.get("_workspace"):
        tag += ", workspace"     # U15: a research draft, not yet promoted
    return tag


def prompt_block(records: list[dict], keys: dict | None = None) -> str:
    """The ASCII block the orchestrator pastes into a spawn prompt. Empty
    string when nothing matched (the caller then injects nothing)."""
    if not records:
        return ""
    keys = keys or {}
    shown = {k: keys.get(k) for k in ("topologies", "packages", "interfaces")
             if keys.get(k)}
    if keys.get("parts"):       # mpn/lcsc list is long - show its size only
        shown["parts"] = len(keys["parts"])
    lines = ["KNOWLEDGE RECORDS (deterministic retrieval"
             + (f"; keys: {json.dumps(shown, sort_keys=True)}" if shown else "")
             + "). Treat rules as constraints; cite the record id when you "
             "apply or overrule one; the tag is level/maturity (draft = "
             "unreviewed):"]
    for r in records:
        lines.append(f"- {r['id']} [{', '.join(r.get('classes') or [])}] "
                     f"({_tag(r)})")
        prose = " ".join((r.get("prose") or "").split())
        lines.append(f"  {prose}")
        rule = r.get("rule")
        if isinstance(rule, dict):
            fields = {k: v for k, v in rule.items() if k != "enforced_by"}
            if fields:
                lines.append("  rule: " + " ".join(
                    f"{k}={v}" for k, v in sorted(fields.items())))
        srcs = []
        for s in r.get("sources") or []:
            loc = s.get("file", "")
            if s.get("page"):
                loc += f" p.{s['page']}"
            if s.get("section"):
                loc += f" {s['section']}"
            srcs.append(loc)
        if srcs:
            lines.append("  source: " + "; ".join(srcs))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# topology view rendering (reference/topologies/<t>.md stays as a view)
# ---------------------------------------------------------------------------
def render_topology(records: list[dict], topology: str) -> str:
    """Markdown view of every record applying to a topology - the generated
    replacement for the hand-written reference/topologies/<t>.md. Deterministic
    (sorted by id); a test pins the committed view to this render."""
    t = norm_token(topology)
    hits = [r for r in sorted(records, key=lambda r: r.get("id") or "")
            if t in {norm_token(x) for x in
                     (r.get("applies") or {}).get("topologies") or []}
            and r.get("status") == "active"]
    lines = [
        f"# Topology reference: {topology} (GENERATED VIEW - do not hand-edit)",
        "",
        "Source of truth: reference/knowledge/records/ (U4). Regenerate with",
        f"`scripts/knowledge.py --render-topology {topology} --out "
        f"reference/topologies/{topology}.md`; a test pins this file to the",
        "records, so hand-edits fail the suite - edit the record, re-render.",
        "",
        "HOW TO USE (research-reference-design agents): read this FIRST, then",
        "research only the part-specific delta (exact external-component",
        "table, errata, the family's FB flavor). Cite deltas against the",
        "record ids. Retrieval into P6/P7 spawn prompts is automatic",
        "(knowledge.py --select) once constraints.json declares the block.",
        "Each heading carries the record's level/maturity tag (U13 coverage",
        "contract): draft = unreviewed; only approved/proven satisfy coverage.",
    ]
    for r in hits:
        lines += ["", f"## {r['id']} [{', '.join(r.get('classes') or [])}] "
                      f"({_tag(r)})", ""]
        lines.append((r.get("prose") or "").rstrip())
        rule = r.get("rule")
        if isinstance(rule, dict):
            fields = {k: v for k, v in rule.items() if k != "enforced_by"}
            if fields:
                lines += ["", "Rule: " + " ".join(
                    f"{k}={v}" for k, v in sorted(fields.items()))]
        env = r.get("envelope")
        if isinstance(env, dict) and env:
            lines += ["", "Envelope: " + " ".join(
                f"{k}={json.dumps(v, sort_keys=True)}"
                for k, v in sorted(env.items()))]
        srcs = []
        for s in r.get("sources") or []:
            loc = s.get("file", "")
            if s.get("page"):
                loc += f" p.{s['page']}"
            if s.get("section"):
                loc += f" {s['section']}"
            srcs.append(loc)
        if srcs:
            lines += ["", "Sources: " + "; ".join(srcs)]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# workspace-first research outputs (U15): <ws>/research/records + checklists
# ---------------------------------------------------------------------------
WS_RESEARCH = "research"
WS_RECORDS = "research/records"
WS_CHECKLISTS = "research/checklists"
WS_TASKS = "research/tasks"


def workspace_research_blocks(ws: Path | str) -> dict[str, dict]:
    """slot -> {task, closed, blocked_by[]} when the slot's LATEST closed
    research task closed `blocked` (every fetch refused by the host, nothing
    acquired). A later task on the slot that closed otherwise clears it.
    Unreadable task files are skipped - research.py status names them."""
    d = Path(ws) / WS_TASKS
    latest: dict[str, dict] = {}
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(t, dict) or t.get("status") != "closed":
            continue
        slot = t.get("slot")
        if slot and str(t.get("closed") or "") >= str(
                (latest.get(slot) or {}).get("closed") or ""):
            latest[slot] = t
    return {s: {"task": t.get("id"), "closed": t.get("closed"),
                "blocked_by": t.get("blocked_by") or []}
            for s, t in latest.items() if t.get("outcome") == "blocked"}


def workspace_records(ws: Path | str) -> list[dict]:
    """Research records the workspace holds (research.py writes them there
    first; the promote pass moves them into the library). Each carries
    `_workspace: True` and a workspace-relative `_path`. Unparseable or
    non-mapping files are skipped - research.py validate names them."""
    d = Path(ws) / WS_RECORDS
    out = []
    for p in sorted(d.glob("*.yaml")) if d.is_dir() else []:
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            data["_path"] = f"{WS_RECORDS}/{p.name}"
            data["_workspace"] = True
            out.append(data)
    return out


def workspace_checklists(ws: Path | str) -> list[dict]:
    d = Path(ws) / WS_CHECKLISTS
    out = []
    for p in sorted(d.glob("*.yaml")) if d.is_dir() else []:
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            data["_path"] = f"{WS_CHECKLISTS}/{p.name}"
            data["_workspace"] = True
            out.append(data)
    return out


def record_draft_unverified(record: dict) -> bool:
    """A WORKSPACE research record that the second reader has not verified
    (U21). Library records are untouched by this - only research output that
    never cleared review, which never injects and must never read as
    coverage."""
    return (bool(record.get("_workspace"))
            and str(record.get("origin") or "").startswith("research:")
            and record_maturity(record) != "verified")


def merge_workspace(items: list[dict], ws_items: list[dict]) -> list[dict]:
    """Library items + the workspace's, deduped by id - the LIBRARY wins (a
    promoted record still sitting in the workspace must not shadow it)."""
    have = {r.get("id") for r in items}
    return list(items) + [r for r in ws_items if r.get("id") not in have]


# ---------------------------------------------------------------------------
# coverage (U13, design decision 5a)
# ---------------------------------------------------------------------------
def workspace_slots(ws: Path | str) -> dict:
    """The design's coverage SLOTS, read deterministically from the workspace:

    block:<name>      one per constraints.json blocks[] entry (topology +
                      optional operating_point {unit-suffixed dims})
    interface:<base>  one per distinct diff_pairs[].base (operating point =
                      the entries' numeric fields, e.g. impedance_ohm, plus
                      an optional operating_point map)
    part:<lcsc|mpn>   one per parts.json IC (ref_prefix_hint U) or any part
                      with a P3 datasheet extraction on disk; carries the
                      extraction's layout_notes count and the operating point
                      of the block it belongs to (parts[].block == blocks[]
                      block/name)
    Returns {slots, keys, warnings, sources}."""
    ws = Path(ws)
    paths = _ws_paths(ws)
    keys = workspace_keys(ws)
    slots: list[dict] = []
    warnings: list[str] = []
    cpath, ppath = paths["constraints"], paths["parts"]
    block_ops: dict[str, dict] = {}

    if cpath.is_file():
        data = json.loads(cpath.read_text(encoding="utf-8"))
        for i, b in enumerate(data.get("blocks") or []):
            if not isinstance(b, dict) or not b.get("topology"):
                continue
            label = str(b.get("block") or b.get("name")
                        or f"{b['topology']}#{i}")
            op = b.get("operating_point") if isinstance(
                b.get("operating_point"), dict) else {}
            for w in operating_point_problems(op, f"blocks[{i}].operating_point"):
                warnings.append(w)
            slot = {"id": f"block:{label}", "kind": "block",
                    "topology": norm_token(b["topology"]),
                    "label": label, "operating_point": op}
            slots.append(slot)
            for k in (b.get("block"), b.get("name")):
                if k:
                    block_ops[norm_token(k)] = op
        by_base: dict[str, dict] = {}
        for i, d in enumerate(data.get("diff_pairs") or []):
            if not isinstance(d, dict) or not d.get("base"):
                continue
            base = norm_token(d["base"])
            op = by_base.setdefault(base, {})
            for k, v in d.items():
                if _is_num(v) and dim_kind(k) == "numeric":
                    op.setdefault(k, v)
            extra = d.get("operating_point")
            if isinstance(extra, dict):
                for w in operating_point_problems(
                        extra, f"diff_pairs[{i}].operating_point"):
                    warnings.append(w)
                op.update(extra)
        for base, op in sorted(by_base.items()):
            slots.append({"id": f"interface:{base}", "kind": "interface",
                          "interface": base, "operating_point": op})

    if ppath.is_file():
        data = json.loads(ppath.read_text(encoding="utf-8"))
        ext_dirs = [ws / "parts", ppath.parent]
        for p in data.get("parts") or []:
            if not isinstance(p, dict):
                continue
            lcsc = str(p.get("lcsc") or "").strip()
            mpn = str(p.get("mpn") or "").strip()
            hint = str(p.get("ref_prefix_hint") or "").strip().upper()
            ext = None
            if lcsc:
                for d in ext_dirs:
                    cand = d / f"{lcsc}.json"
                    if cand.is_file():
                        ext = cand
                        break
            if hint not in IC_REF_HINTS and ext is None:
                continue
            notes: list[str] = []
            ext_error = None
            if ext is not None:
                try:
                    edata = json.loads(ext.read_text(encoding="utf-8"))
                    notes = [str(n) for n in (edata.get("layout_notes") or [])
                             if str(n).strip()]
                except (OSError, ValueError) as exc:
                    ext_error = f"{type(exc).__name__}: {exc}"
            blk = norm_token(p.get("block") or "")
            slots.append({
                "id": f"part:{lcsc or mpn}", "kind": "part",
                "lcsc": lcsc or None, "mpn": mpn or None,
                "package": p.get("package"), "block": p.get("block"),
                "ref_prefix_hint": hint or None,
                "operating_point": block_ops.get(blk, {}),
                "extraction": {
                    "file": ext.as_posix() if ext else None,
                    "layout_notes": len(notes),
                    "layout_chars": sum(len(n) for n in notes),
                    "error": ext_error,
                },
            })

    if not slots:
        warnings.append("no slots: constraints.json declares no blocks / "
                        "diff_pairs and parts.json has no ICs - P2 must "
                        "declare blocks[] (with operating_point) for "
                        "coverage to check anything")
    return {"slots": slots, "keys": keys, "warnings": warnings,
            "sources": {"constraints": cpath.as_posix() if cpath.is_file()
                        else None,
                        "parts": ppath.as_posix() if ppath.is_file()
                        else None},
            "board": paths["board"], "state": paths["state"]}


def find_checklist(checklists: list[dict], kind: str, token: str) -> dict | None:
    """The one checklist claiming this topology/interface token, or None."""
    field = "topologies" if kind == "block" else "interfaces"
    t = norm_token(token)
    for c in checklists:
        ap = c.get("applies") or {}
        if t in {norm_token(x) for x in ap.get(field) or []}:
            return c
    return None


def mapping_problems(mapping, records: list[dict], slot_ids: set[str]) -> list[str]:
    """Validate an agent mapping file's CONTENT against MAPPING_SCHEMA and this
    report's universe: every record must exist and be active, every slot must
    be one of this workspace's slots, and the class must be one the record
    actually carries (the mapper may not invent classes)."""
    import jsonschema
    problems = []
    validator = jsonschema.Draft202012Validator(MAPPING_SCHEMA)
    for err in sorted(validator.iter_errors(mapping), key=lambda e: list(e.path)):
        path = "/".join(str(x) for x in err.path) or "(top)"
        problems.append(f"mapping schema {path}: {err.message}")
    if problems:
        return problems
    by_id = {r.get("id"): r for r in records}
    for i, m in enumerate(mapping["mappings"]):
        r = by_id.get(m["record"])
        if r is None:
            problems.append(f"mappings[{i}]: unknown record {m['record']!r}")
            continue
        if r.get("status") != "active":
            problems.append(f"mappings[{i}]: record {m['record']!r} is "
                            f"{r.get('status')}, not active")
        if m["slot"] not in slot_ids:
            problems.append(f"mappings[{i}]: unknown slot {m['slot']!r}")
        if norm_token(m["class"]) not in {norm_token(c) for c in
                                          r.get("classes") or []}:
            problems.append(f"mappings[{i}]: record {m['record']!r} does not "
                            f"carry class {m['class']!r}")
    return problems


def _evaluate(record: dict, op: dict, min_level: str | None,
              floor: str, via: str) -> dict:
    """One record against one slot's class requirement -> the mechanical
    verdict. satisfies = level known and >= min_level, envelope inside/n-a,
    maturity >= floor. Otherwise `blocker` names the ONE thing that would
    turn it into coverage (outside = it does not apply here at all)."""
    lv = record_level(record)
    mat = record_maturity(record)
    env = envelope_contains(record.get("envelope"), op)
    out = {"id": record.get("id"), "level": lv, "maturity": mat,
           "envelope": env["verdict"], "unknown_dims": env["unknown_dims"],
           "outside_dims": env["outside_dims"], "via": via,
           "satisfies": False, "blocker": None}
    if env["verdict"] == "outside":
        out["blocker"] = "outside"
    elif record_draft_unverified(record):
        # U21: a workspace research record the second reader never verified
        # (never ruled, or ruled refuted). It is NOT knowledge-in-waiting the
        # way an unapproved verified record is - it failed review or stalled,
        # and folding it into `provisional` is what made bb-amp's six lost
        # input-stage rules invisible. Its own blocker, its own bucket.
        out["blocker"] = "draft-unverified"
    elif lv is None:
        out["blocker"] = "level-unknown"
    elif min_level and LEVEL_RANK[lv] < LEVEL_RANK[min_level]:
        out["blocker"] = "level-below-min"      # a principle parent, typically
    elif env["verdict"] == "unknown":
        out["blocker"] = "envelope-unknown"
    elif MATURITY_RANK[mat] < MATURITY_RANK[floor]:
        out["blocker"] = "maturity-below-floor"
    else:
        out["satisfies"] = True
    return out


def _worst(verdicts: list[str]) -> str:
    order = {"covered": 0, "provisional": 1, "gap": 2}
    return max(verdicts, key=lambda v: order[v]) if verdicts else "covered"


def coverage(ws: Path | str, records: list[dict] | None = None,
             checklists: list[dict] | None = None,
             mapping: dict | None = None,
             floor: str = DEFAULT_MATURITY_FLOOR,
             phase: str | None = None,
             mapping_file: str | None = None,
             escalate_provisional: bool = False) -> dict:
    """The coverage report for a workspace (see module docstring).

    Per slot: verdict covered | provisional | gap, per required class the
    records considered and why each does or does not count, `gaps` = research
    task specs (slot, missing classes + min levels, operating point, related
    records incl. principle parents). `mapping_request` = what the
    coverage-mapper agent gets when unmet classes remain (None otherwise).
    Never raises on an empty design; `warnings` says what could not be
    checked."""
    if floor not in MATURITY_RANK:
        raise ValueError(f"unknown maturity floor {floor!r}")
    records = load_records() if records is None else records
    checklists = load_checklists() if checklists is None else checklists
    ws = Path(ws)
    # U15: the workspace's own research outputs count too - a researched-but-
    # unapproved class reads `provisional` (maturity below the floor) instead
    # of re-firing the research trigger on the next coverage run.
    ws_recs = workspace_records(ws)
    ws_cls = workspace_checklists(ws)
    records = merge_workspace(records, ws_recs)
    checklists = merge_workspace(checklists, ws_cls)
    ws_info = workspace_slots(ws)
    slots = ws_info["slots"]
    slot_ids = {s["id"] for s in slots}
    warnings = list(ws_info["warnings"])

    draft_unverified: list[dict] = []      # U21: stalled research, by class
    edges: dict[str, list[tuple[dict, str]]] = {}   # slot -> [(record, class)]
    if mapping is not None:
        probs = mapping_problems(mapping, records, slot_ids)
        if probs:
            raise ValueError("invalid mapping: " + "; ".join(probs))
        by_id = {r.get("id"): r for r in records}
        for m in mapping["mappings"]:
            edges.setdefault(m["slot"], []).append(
                (by_id[m["record"]], norm_token(m["class"])))

    active = [r for r in records if r.get("status") == "active"]
    # U21: unverified workspace research is status `draft`, so the filter
    # above dropped it before any slot ever saw it - that is exactly why
    # bb-amp's six refuted input-stage rules showed up nowhere. Evaluate them
    # alongside the active set (they can never SATISFY - _evaluate blocks them
    # as `draft-unverified`), so the report can name the stall. They stay out
    # of `active`, so they are never offered as principle parents or mapping
    # candidates.
    stalled_drafts = [dict(r, status="active") for r in records
                      if record_draft_unverified(r)]
    evaluable = active + stalled_drafts
    principle_by_class: dict[str, list[str]] = {}
    for r in active:
        if record_level(r) == "principle":
            for c in r.get("classes") or []:
                principle_by_class.setdefault(norm_token(c), []).append(r["id"])

    out_slots: list[dict] = []
    gaps: list[dict] = []
    request_slots: list[dict] = []

    for s in slots:
        op = s.get("operating_point") or {}
        entry = dict(s)
        entry["classes"] = []
        entry["gap_reasons"] = []
        if s["kind"] in ("block", "interface"):
            token = s["topology"] if s["kind"] == "block" else s["interface"]
            if s["kind"] == "block":
                matched = select(evaluable, topologies=[token])
            else:
                matched = select(evaluable, interfaces=[token])
            cl = find_checklist(checklists, s["kind"], token)
            entry["checklist"] = None
            required: list[tuple[str, str | None]] = []
            if cl is not None:
                cmat = record_maturity(cl)
                entry["checklist"] = {
                    "id": cl.get("id"), "maturity": cmat,
                    "floor_met": MATURITY_RANK[cmat] >= MATURITY_RANK[floor]}
                required = [(norm_token(q["class"]), q["min_level"])
                            for q in cl.get("requires") or []]
            else:
                entry["gap_reasons"].append(
                    f"no coverage checklist for {s['kind']} {token!r} - the "
                    "first research pass on a new topology/interface produces "
                    "one (reference/knowledge/checklists/)")
                # informational: what we already hold, by class, no min level
                required = sorted({(norm_token(c), None)
                                   for r in matched for c in r.get("classes") or []}
                                  | {(c, None) for r, c in edges.get(s["id"], [])})
            class_verdicts = []
            for cls, min_level in required:
                cands = [(r, "keys") for r in matched
                         if cls in {norm_token(c) for c in r.get("classes") or []}]
                seen = {r["id"] for r, _ in cands}
                cands += [(r, "mapping") for r, c in edges.get(s["id"], [])
                          if c == cls and r["id"] not in seen]
                evals = [_evaluate(r, op, min_level, floor, via)
                         for r, via in cands]
                stalled = [e for e in evals
                           if e["blocker"] == "draft-unverified"]
                if any(e["satisfies"] for e in evals):
                    v = "covered"
                elif any(e["blocker"] in ("level-unknown", "level-below-min",
                                          "envelope-unknown",
                                          "maturity-below-floor")
                         for e in evals):
                    v = "provisional"
                else:
                    # U21: unverified drafts are the only candidates -> the
                    # class is a GAP (nothing usable is held), and it lands in
                    # the draft_unverified bucket so the stall is named, not
                    # inferred from a silent `provisional`.
                    v = "gap"
                if stalled:
                    # Reported even when the class reads `covered` off a
                    # VERIFIED sibling - that is bb-amp exactly: six refuted
                    # input-stage rules whose classes were covered by easier
                    # records, so the loss of the hard knowledge showed
                    # nowhere. `verdict` says which case this is.
                    draft_unverified.append(
                        {"slot": s["id"], "class": cls, "verdict": v,
                         "records": [e["id"] for e in stalled]})
                # Seeding runs (any build-modes learning target): under-mature
                # or unproven class is a research TARGET, not an acceptable
                # pass, and it must reach `missing` so the task names it. Use
                # on a phase's FIRST coverage call only - the post-research
                # re-run keeps normal semantics, so fresh workspace records
                # (which read provisional) never re-fire the trigger.
                if escalate_provisional and v == "provisional":
                    v = "gap"
                class_verdicts.append(v)
                entry["classes"].append({
                    "class": cls, "min_level": min_level, "verdict": v,
                    "records": evals})
            verdict = _worst(class_verdicts) if required else "gap"
            if cl is None:
                verdict = "gap"
            elif entry["checklist"] and not entry["checklist"]["floor_met"] \
                    and verdict == "covered":
                verdict = "provisional"
                entry["gap_reasons"].append(
                    f"checklist {cl.get('id')} maturity "
                    f"{entry['checklist']['maturity']} < floor {floor}")
            entry["verdict"] = verdict
            missing = [{"class": c["class"], "min_level": c["min_level"]}
                       for c in entry["classes"] if c["verdict"] == "gap"]
            unmet = [c["class"] for c in entry["classes"]
                     if c["verdict"] != "covered"]
            if verdict == "gap":
                related = sorted({e["id"] for c in entry["classes"]
                                  for e in c["records"] if not e["satisfies"]})
                parents = sorted({pid for m in missing
                                  for pid in principle_by_class.get(m["class"], [])})
                gaps.append({
                    "slot": s["id"], "kind": s["kind"],
                    "topology" if s["kind"] == "block" else "interface": token,
                    "operating_point": op,
                    "missing": missing if cl is not None else
                    [{"class": "coverage-checklist", "min_level": None}],
                    "reasons": entry["gap_reasons"] or [
                        "no record satisfies the class at this operating "
                        "point and maturity floor"],
                    "related_records": related,
                    "principle_parents": parents,
                    "task": (f"research {s['kind']} {token!r}: "
                             + (f"populate {', '.join(m['class'] for m in missing)}"
                                if missing and cl is not None else
                                "produce its coverage checklist, then populate it")
                             + (" (application delta only - principle parents "
                                "exist)" if parents else "")),
                })
            if unmet and cl is not None:
                request_slots.append({
                    "id": s["id"], "kind": s["kind"],
                    "topology" if s["kind"] == "block" else "interface": token,
                    "operating_point": op,
                    "required_classes": [c["class"] for c in entry["classes"]],
                    "unmet_classes": unmet})
        else:   # part slot
            ext = s["extraction"]
            keys_pkg = [s["package"]] if s.get("package") else []
            keys_parts = [k for k in (s.get("mpn"), s.get("lcsc")) if k]
            matched = select(evaluable, packages=keys_pkg,
                             parts=keys_parts)
            cands = [(r, "keys") for r in matched]
            seen = {r["id"] for r, _ in cands}
            cands += [(r, "mapping") for r, _c in edges.get(s["id"], [])
                      if r["id"] not in seen]
            evals = [_evaluate(r, op, "part", floor, via) for r, via in cands]
            layout_ok = ext["file"] is not None and ext["error"] is None \
                and ext["layout_notes"] >= LAYOUT_NOTES_MIN
            entry["datasheet_layout"] = (
                "present" if layout_ok else
                "thin" if ext["file"] and not ext["error"] and ext["layout_notes"]
                else "missing")
            entry["classes"].append({
                "class": "datasheet-layout", "min_level": "part",
                "verdict": "covered" if layout_ok else "gap",
                "records": evals})
            if layout_ok or any(e["satisfies"] for e in evals):
                verdict = "covered"
            elif any(e["blocker"] in ("maturity-below-floor", "envelope-unknown",
                                      "level-unknown") for e in evals):
                verdict = "provisional"
            else:
                verdict = "gap"
            entry["classes"][0]["verdict"] = verdict
            entry["verdict"] = verdict
            stalled = [e for e in evals if e["blocker"] == "draft-unverified"]
            if stalled:                       # U21: same bucket for part slots
                draft_unverified.append(
                    {"slot": s["id"], "class": "datasheet-layout",
                     "verdict": verdict,
                     "records": [e["id"] for e in stalled]})
            if verdict == "gap":
                why = ("no P3 datasheet extraction on disk"
                       if ext["file"] is None else
                       f"extraction unreadable ({ext['error']})" if ext["error"]
                       else f"layout section thin ({ext['layout_notes']} "
                            f"note(s) < {LAYOUT_NOTES_MIN})")
                entry["gap_reasons"].append(why)
                gaps.append({
                    "slot": s["id"], "kind": "part", "mpn": s.get("mpn"),
                    "lcsc": s.get("lcsc"), "package": s.get("package"),
                    "block": s.get("block"), "operating_point": op,
                    "missing": [{"class": "datasheet-layout",
                                 "min_level": "part"}],
                    "reasons": [why],
                    "related_records": sorted({e["id"] for e in evals}),
                    "principle_parents": [],
                    "task": (f"research part {s.get('mpn') or s.get('lcsc')}: "
                             "vendor layout section / app note -> part-level "
                             "record(s) or a fuller datasheet-extractor pass"),
                })
        out_slots.append(entry)

    # a slot whose research was refused by the host is a NAMED gap, not an
    # empty research pass (PCB-0028: Torex/Littelfuse behind Akamai 403s)
    blocks = workspace_research_blocks(ws)
    blocked_slots = []
    for entry in out_slots:
        b = blocks.get(entry["id"])
        if b and entry.get("verdict") != "covered":
            entry["research_blocked"] = b
            blocked_slots.append(entry["id"])
    for g in gaps:
        b = blocks.get(g["slot"])
        if b:
            g["research_blocked"] = b
            g["reasons"] = list(g["reasons"]) + [
                "research blocked by host: " + ", ".join(
                    f"{h.get('host')} HTTP "
                    + "/".join(str(x) for x in h.get("http_status") or [])
                    for h in b["blocked_by"])]
    if blocked_slots:
        warnings.append(
            f"{len(blocked_slots)} slot(s) left uncovered because the host "
            "refused every research fetch (research_blocked): "
            + ", ".join(blocked_slots))

    counts = {"slots": len(out_slots)}
    for v in ("covered", "provisional", "gap"):
        counts[v] = sum(1 for s in out_slots if s.get("verdict") == v)
    counts["research_blocked"] = len(blocked_slots)
    counts["draft_unverified"] = len({d["slot"] for d in draft_unverified})
    if draft_unverified:
        n = len({r for d in draft_unverified for r in d["records"]})
        warnings.append(
            f"{n} research record(s) never verified - unverified research "
            "never injects, so these classes are NOT covered by them: "
            + ", ".join(sorted({r for d in draft_unverified
                                for r in d["records"]})))

    mapping_request = None
    if request_slots:
        mapping_request = {
            "schema": MAPPING_SCHEMA,
            "instructions": (
                "Map records to slots for ONE class each - only edges you can "
                "justify from the record's prose. You classify; you do NOT "
                "decide sufficiency, coverage or maturity. Output the JSON "
                "shape in `schema`; the coverage run validates it and refuses "
                "unknown records/slots or classes the record does not carry."),
            "slots": request_slots,
            "candidates": [{
                "id": r["id"], "level": record_level(r),
                "maturity": record_maturity(r),
                "classes": r.get("classes") or [],
                "applies": r.get("applies") or {},
                "prose_head": " ".join((r.get("prose") or "").split())[:240],
            } for r in sorted(active, key=lambda r: r.get("id") or "")],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": ws.as_posix(), "board": ws_info["board"],
        "phase": phase, "maturity_floor": floor,
        "keys": ws_info["keys"], "sources": ws_info["sources"],
        "summary": counts, "slots": out_slots, "gaps": gaps,
        "warnings": warnings,
        "workspace_records": sorted(r.get("id") or "" for r in ws_recs),
        "workspace_checklists": sorted(c.get("id") or "" for c in ws_cls),
        "draft_unverified": draft_unverified,
        "mapping_applied": ({"file": mapping_file,
                             "edges": sum(len(v) for v in edges.values())}
                            if mapping is not None else None),
        "mapping_request": mapping_request,
    }


# ---------------------------------------------------------------------------
# proven upgrade path (T11 wiring): reality outranks review
# ---------------------------------------------------------------------------
def bringup_evidence(ws: Path | str) -> list[dict]:
    """The workspace's bring-up events (state.json history `bringup_passed`),
    oldest first; [] when the board has not passed bring-up. prove() takes
    only those whose results file still holds (bringuplib)."""
    ws = Path(ws)
    sp = ws / "state.json"
    if not sp.is_file():
        return []
    data = json.loads(sp.read_text(encoding="utf-8"))
    return [h for h in data.get("history") or []
            if isinstance(h, dict) and h.get("event") == BRINGUP_EVENT]


def _set_top_key(text: str, key: str, value_line: str) -> str:
    """Replace (or append) a top-level `key: ...` scalar line in a record's
    text - a TARGETED edit that leaves the hand-authored prose untouched
    (yaml.safe_dump would reflow the whole file)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(rf"^{re.escape(key)}:", ln):
            lines[i] = f"{key}: {value_line}"
            return "\n".join(lines) + "\n"
    lines.append(f"{key}: {value_line}")
    return "\n".join(lines) + "\n"


def _append_evidence(text: str, item: dict) -> str:
    """Append one evidence entry (flow mapping, values quoted) to the
    top-level `evidence:` block, creating the block at EOF if absent."""
    def q(v) -> str:
        return json.dumps(str(v))
    flow = "  - {" + ", ".join(f"{k}: {q(v)}" for k, v in item.items()) + "}"
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^evidence:\s*(\[\s*\])?\s*$", ln):
            end = i + 1
            while end < len(lines) and (lines[end].startswith(" ")
                                        or not lines[end].strip()):
                end += 1
            # trim trailing blank lines inside the block
            while end > i + 1 and not lines[end - 1].strip():
                end -= 1
            lines[i] = "evidence:"
            lines.insert(end, flow)
            return "\n".join(lines) + "\n"
    lines += ["evidence:", flow]
    return "\n".join(lines) + "\n"


def prove(ws: Path | str, records_dir: Path | str | None = None,
          checklists_dir: Path | str | None = None,
          dry_run: bool = False) -> dict:
    """Upgrade to `proven` every record that APPLIED to a board that passed
    bring-up: deterministic key match + envelope inside/n-a at the board's
    operating points (mapping edges do not count - reality evidence attaches
    to deterministic applicability only). Idempotent per workspace; a record
    already proven elsewhere gains one more evidence entry. Writes are
    targeted line edits, verified by re-validating the library (a failed
    re-validation restores every touched file)."""
    ws = Path(ws)
    events = bringup_evidence(ws)
    result = {"workspace": ws.as_posix(), "evidence_events": len(events),
              "upgraded": [], "evidence_added": [], "unchanged": [],
              "dry_run": dry_run, "problems": []}
    if not events:
        result["problems"].append(
            f"no {BRINGUP_EVENT} event in {ws.as_posix()}/state.json history "
            "- close bring-up with its measurements first (state.py bringup "
            "--results <file>) once the board has passed")
        return result
    sp = ws / "state.json"
    last = bringuplib.latest_event(
        json.loads(sp.read_text(encoding="utf-8")).get("history"))
    if last is not None and last.get("event") != BRINGUP_EVENT:
        result["problems"].append(
            f"the latest bring-up is {last.get('event')} ({last.get('ts')}): "
            "a board that has since failed or derated proves nothing")
        return result
    latest = events[-1]
    bad = bringuplib.evidence_problems(ws, latest)
    if bad:
        result["problems"].extend(bad)
        return result
    date = str(latest.get("ts") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        result["problems"].append(f"{BRINGUP_EVENT} event carries no ISO ts")
        return result
    records = load_records(records_dir)
    checklists = load_checklists(checklists_dir)
    # floor 'draft' = every applicable record, whatever its review state
    rep = coverage(ws, records, checklists, floor="draft")
    try:
        ws_rel = ws.resolve().relative_to(REPO).as_posix()
    except ValueError:
        ws_rel = ws.as_posix()
    item = {"workspace": ws_rel, "board": rep["board"],
            "event": BRINGUP_EVENT, "date": date,
            "results": latest["results"]}
    applied: dict[str, dict] = {}
    for s in rep["slots"]:
        for c in s.get("classes") or []:
            for e in c.get("records") or []:
                if e["via"] == "keys" and e["envelope"] in ("inside", "n/a"):
                    applied[e["id"]] = e
    by_id = {r["id"]: r for r in records}
    rdir = Path(records_dir) if records_dir else RECORDS_DIR
    backups: dict[Path, str] = {}
    for rid in sorted(applied):
        r = by_id.get(rid)
        if r is None:
            continue
        already = [ev for ev in r.get("evidence") or []
                   if ev.get("workspace") == ws_rel
                   and ev.get("event") == BRINGUP_EVENT]
        if already and record_maturity(r) == "proven":
            result["unchanged"].append(rid)
            continue
        p = rdir / f"{rid}.yaml"
        text = p.read_text(encoding="utf-8")
        new = text
        if record_maturity(r) != "proven":
            new = _set_top_key(new, "maturity", "proven")
            result["upgraded"].append(
                {"id": rid, "from": record_maturity(r), "to": "proven"})
        if not already:
            new = _append_evidence(new, item)
            result["evidence_added"].append(rid)
        if not dry_run and new != text:
            backups[p] = text
            p.write_text(new, encoding="utf-8", newline="\n")
    if not dry_run and backups:
        probs = validate(rdir, checklists_dir)
        if probs:
            for p, text in backups.items():
                p.write_text(text, encoding="utf-8", newline="\n")
            result["problems"] += ["post-write validation failed; restored "
                                   "every touched record"] + probs
            result["upgraded"], result["evidence_added"] = [], []
    return result


def sha256_file(p: Path | str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()
