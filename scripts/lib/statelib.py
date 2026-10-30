"""statelib.py - normalized artifact hashing + freshness computation (T7).

The library half of state.json v2: state.py owns the schema and CLI, this
module owns everything that turns files into design-content hashes and hashes
into freshness verdicts. Toolchain-free by design (pure venv: sexpdata via
netlist_audit, yaml, fabhash) so freshness questions never need kicad-cli.

Why normalized hashes (LEARNINGS 2026-08-06 [fab_export][ordering][dfm], T1):
raw file hashes are NOT design fingerprints - KiCad restamps gerber/drill
headers on every export and reassigns UUIDs on regeneration, so a byte hash
says "same file", never "same design". Each artifact KIND therefore hashes
through a normalizer that removes exactly the churn that does not change the
design, and nothing else. Stored hashes are prefixed "<norm>:" so a normalizer
change can never false-match a hash computed under the old rules.

The artifact kinds, per-gate input sets and edit-class invalidation map live
in reference/invalidation.yaml (single source; documented there). This module
loads that map, resolves kind paths for a workspace ({board} templates,
registry overrides), and computes the two-layer freshness verdict:
hash-valid AND unmarked = fresh.

Failure direction is always conservative: an unparsable input falls back to a
raw-bytes hash (only an identical file matches), an unknown gate hashes no
inputs (freshness "unknown", never "fresh"), a missing file hashes to None
(match only if it was also missing at record time).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

DEFAULT_MAP = Path(__file__).resolve().parents[2] / "reference" / "invalidation.yaml"

_MAP_CACHE: dict[tuple[str, float], dict] = {}

# (uuid "1c164f86-...") - KiCad 10 quoted form - plus the unquoted and legacy
# (tstamp ...) forms older files can carry. Live-verified on pd-trigger:
# quoted uuid in both .kicad_pcb and .kicad_sch, zero tstamps.
_UUID_TOKEN = re.compile(
    r"\((?:uuid|tstamp)\s+\"?[0-9a-fA-F-]+\"?\s*\)")


# ---------------------------------------------------------------------------
# invalidation map
# ---------------------------------------------------------------------------
def load_map(path: Path | str | None = None) -> dict:
    """Parsed invalidation.yaml with the three sections guaranteed present.
    Cached on (path, mtime) - record-gate and freshness call this per run."""
    p = Path(path) if path else DEFAULT_MAP
    key = (str(p), p.stat().st_mtime)
    if key in _MAP_CACHE:
        return _MAP_CACHE[key]
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    for section in ("artifact_kinds", "gate_inputs", "edit_classes"):
        if not isinstance(data.get(section), dict) or not data[section]:
            raise ValueError(f"{p}: missing/empty section {section!r}")
    for cls, ec in data["edit_classes"].items():
        for field in ("mutates", "stale_artifacts", "gates", "human_hold"):
            if field not in ec:
                raise ValueError(f"{p}: edit class {cls!r} lacks {field!r}")
        unknown = [k for k in ec["mutates"] + ec["stale_artifacts"]
                   if k not in data["artifact_kinds"]]
        if unknown:
            raise ValueError(f"{p}: edit class {cls!r} names unknown "
                             f"artifact kinds {unknown}")
        unknown = [g for g in ec["gates"] if g not in data["gate_inputs"]]
        if unknown:
            raise ValueError(f"{p}: edit class {cls!r} names unknown "
                             f"gates {unknown}")
    for gate, kinds in data["gate_inputs"].items():
        unknown = [k for k in kinds if k not in data["artifact_kinds"]]
        if unknown:
            raise ValueError(f"{p}: gate {gate!r} inputs name unknown "
                             f"artifact kinds {unknown}")
    _MAP_CACHE[key] = data
    return data


# ---------------------------------------------------------------------------
# normalizers (each returns the BYTES to hash; None = use raw file bytes)
# ---------------------------------------------------------------------------
def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _eol(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _bytes_text_eol(data: bytes) -> bytes:
    return _eol(_text(data)).encode("utf-8")


def _bytes_sexpr_no_uuid(data: bytes) -> bytes:
    return _UUID_TOKEN.sub("", _eol(_text(data))).encode("utf-8")


def _norm_text_eol(path: Path) -> bytes:
    return _bytes_text_eol(path.read_bytes())


def _norm_sexpr_no_uuid(path: Path) -> bytes:
    return _bytes_sexpr_no_uuid(path.read_bytes())


def _norm_json_canonical(path: Path) -> bytes:
    doc = json.loads(_text(path.read_bytes()))
    return json.dumps(doc, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _norm_netlist_canonical(path: Path) -> bytes:
    """Semantic netlist content: components (ref -> value+footprint) + nets
    (name -> sorted (ref,pin,pintype) nodes). Reuses netlist_audit's tested
    parser (a sibling SCRIPT - lazy import with a path insert; lib must not
    grow its own netlist parser). Export date, tool banner, block order and
    formatting are invisible; libparts/libsource are deliberately excluded
    (library bookkeeping - pin semantics already travel per-node as pintype)."""
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import netlist_audit  # noqa: E402
    parsed = netlist_audit.parse_netlist(path)
    canon = {
        "components": {
            ref: {"value": c.get("value"), "footprint": c.get("footprint")}
            for ref, c in parsed["components"].items()},
        "nets": {
            name: sorted((m.get("ref") or "", m.get("pin") or "",
                          m.get("pintype") or "")
                         for m in members)
            for name, members in parsed["nets"].items()},
    }
    return json.dumps(canon, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def _norm_dir_text(path: Path) -> bytes:
    """All files under a directory, name-sorted, each EOL-normalized. Only
    defined for directories (the sims kind); a file input raises so the
    caller's raw fallback takes over."""
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory")
    h_parts: list[bytes] = []
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = f.relative_to(path).as_posix()
        h_parts.append(rel.encode("utf-8") + b"\0"
                       + _eol(_text(f.read_bytes())).encode("utf-8") + b"\0")
    return b"dir\0" + b"".join(h_parts)


def _norm_gerber_design(path: Path) -> bytes | str:
    import fabhash  # sibling lib module
    return fabhash.design_hash(path)  # already a full-content digest string


NORMALIZERS = {
    "text_eol": _norm_text_eol,
    "sexpr_no_uuid": _norm_sexpr_no_uuid,
    "json_canonical": _norm_json_canonical,
    "netlist_canonical": _norm_netlist_canonical,
    "dir_text": _norm_dir_text,
    "gerber_design": _norm_gerber_design,
}

# Suffix -> normalizer for artifacts registered under non-standard names
# (the registry accepts any name; hashing still wants the right churn model).
_SUFFIX_NORM = {
    ".kicad_pcb": "sexpr_no_uuid",
    ".kicad_sch": "sexpr_no_uuid",
    ".kicad_sym": "sexpr_no_uuid",
    ".kicad_mod": "sexpr_no_uuid",
    ".kicad_pro": "json_canonical",
    ".net": "netlist_canonical",
    ".json": "json_canonical",
    ".zip": "gerber_design",
}


def norm_for_path(path: Path | str) -> str:
    p = Path(path)
    if p.is_dir():
        return "dir_text"
    return _SUFFIX_NORM.get(p.suffix.lower(), "text_eol")


def hash_artifact(path: Path | str, norm: str) -> str | None:
    """'<norm>:<sha256>' of the artifact's normalized content, None when the
    file/dir does not exist. Any normalizer failure (unparsable JSON/netlist,
    binary content) falls back to raw bytes under the 'raw' prefix - fail-safe:
    a raw hash only ever matches an identical file, so staleness can be
    over-reported but never missed."""
    p = Path(path)
    if not p.exists():
        return None
    fn = NORMALIZERS.get(norm)
    if fn is None:
        raise ValueError(f"unknown normalizer {norm!r}")
    try:
        out = fn(p)
    except Exception:
        if p.is_dir():  # unreadable dir member: hash the name list at least
            names = "\n".join(sorted(
                f.relative_to(p).as_posix() for f in p.rglob("*")))
            return "rawdir:" + hashlib.sha256(names.encode()).hexdigest()
        return "raw:" + hashlib.sha256(p.read_bytes()).hexdigest()
    if isinstance(out, str):        # gerber_design returns a finished digest
        return f"{norm}:{out}"
    return f"{norm}:{hashlib.sha256(out).hexdigest()}"


_BYTE_NORMALIZERS = {
    "text_eol": _bytes_text_eol,
    "sexpr_no_uuid": _bytes_sexpr_no_uuid,
}


def hash_bytes(data: bytes, norm: str) -> str:
    """hash_artifact() of a file holding `data`, for a caller that must hash
    exactly the bytes it read (one read, no second look at the file). Only
    the text normalizers, which never fail, have a bytes form."""
    fn = _BYTE_NORMALIZERS.get(norm)
    if fn is None:
        raise ValueError(f"normalizer {norm!r} has no bytes form")
    return f"{norm}:{hashlib.sha256(fn(data)).hexdigest()}"


# ---------------------------------------------------------------------------
# workspace resolution + freshness
# ---------------------------------------------------------------------------
def find_workspace(input_file: Path | None,
                   explicit: str | None = None) -> Path | None:
    """The workspace whose state.json a result/edit on `input_file` belongs in.

    An explicit --workspace wins and MUST hold a state.json (a typo that
    silently records nowhere is the bug U16 exists to kill). Otherwise the
    input's own parents are walked - the pipeline always works on a file
    inside its workspace, so an orchestrator that forgets the flag still
    records. A corpus input (tests/golden/..., a mutant, a scratch export)
    has no state.json above it and is left alone.

    Used by gate.py (gate results, U16) and board_edit.py (outline edits,
    U17): a writer that records its own consequence never forgets to.
    """
    if explicit:
        ws = Path(explicit)
        if not (ws / "state.json").is_file():
            raise RuntimeError(f"--workspace {ws}: no state.json there")
        return ws
    if input_file is None:
        return None
    p = Path(input_file).resolve()
    for parent in list(p.parents)[:4]:
        if (parent / "state.json").is_file():
            return parent
    return None


# ---------------------------------------------------------------------------
# workspace layout schema (state.json "workspace_schema")
# ---------------------------------------------------------------------------
# The LAYOUT of a workspace, versioned apart from the state.json schema
# ("version"): a layout change moves files, a state change rewrites JSON.
# Absent = 1. Each step below names what it moves; `state.py resume` runs the
# steps a workspace is behind on, so a board picks up the layout the first
# time anyone resumes it.
#   2: verify check reports live in ONE place, <ws>/reports/checks/. Before
#      it verify_all defaulted to <pcb dir>/reports/checks, so boards carry
#      kicad/reports/checks/ (some carry both).
WORKSPACE_SCHEMA = 2
CHECKS_REL = "reports/checks"
LEGACY_CHECKS_REL = "kicad/reports/checks"


def check_reports_dir(ws: Path) -> Path:
    """Where workspace `ws` keeps its verify check reports. Writers always
    use <ws>/reports/checks; readers come here so a workspace nobody has
    resumed since schema 2 still reads from kicad/reports/checks when that is
    the only place holding reports."""
    ws = Path(ws)
    new, old = ws / CHECKS_REL, ws / LEGACY_CHECKS_REL
    if not any(new.glob("*.json")) and any(old.glob("*.json")):
        return old
    return new


def reports_dir_for(pcb: Path) -> Path:
    """Default check-report dir for a board file: its workspace's
    reports/checks when the board sits inside one (state.json above it),
    else <pcb dir>/reports/checks (a corpus board, a scratch export)."""
    ws = find_workspace(pcb)
    return ws / CHECKS_REL if ws else Path(pcb).parent / "reports" / "checks"


def migrate_workspace(ws: Path, data: dict) -> list[dict]:
    """Bring workspace `ws` (state dict `data`) up to WORKSPACE_SCHEMA.
    Mutates `data` (the caller saves it) and returns one record per file
    action. Idempotent: a current workspace returns [] and is untouched.
    Never overwrites: a report already at the new path wins and its legacy
    twin stays where it is, so nothing is lost when a board has both."""
    ws = Path(ws)
    have = data.get("workspace_schema", 1)
    if not isinstance(have, int) or have > WORKSPACE_SCHEMA:
        raise ValueError(f"{ws}: workspace_schema {have!r} is newer than "
                         f"this build ({WORKSPACE_SCHEMA}); update hwde")
    actions: list[dict] = []
    if have < 2:
        old, new = ws / LEGACY_CHECKS_REL, ws / CHECKS_REL
        if old.is_dir():
            for f in sorted(p for p in old.iterdir() if p.is_file()):
                dst = new / f.name
                if dst.exists():
                    actions.append({"action": "kept", "from": f"{LEGACY_CHECKS_REL}/{f.name}",
                                    "to": f"{CHECKS_REL}/{f.name}"})
                    continue
                new.mkdir(parents=True, exist_ok=True)
                f.replace(dst)
                actions.append({"action": "moved", "from": f"{LEGACY_CHECKS_REL}/{f.name}",
                                "to": f"{CHECKS_REL}/{f.name}"})
            for d in (old, old.parent):  # kicad/reports/checks, kicad/reports
                try:
                    d.rmdir()  # only when now empty
                except OSError:
                    break
    data["workspace_schema"] = WORKSPACE_SCHEMA
    return actions


_PN_DIR = re.compile(r"^PCB-\d{4}-[A-HJ-NP-Z]_")  # boardreg's <PN>_<name>


def project_stem(ws: Path | None, board: str) -> str:
    """The KiCad project basename in workspace `ws`: `board` when
    kicad/<board>.kicad_pro is there (or ws is None), else the stem of the one
    .kicad_pro in kicad/ - a numbered board's project is <PN>_<name> while
    state.json's board stays <name>. With none there yet, a workspace named
    <PN>_<board> gives its own name (the project a new numbered board gets);
    otherwise, and with several, `board`."""
    kdir = Path(ws) / "kicad" if ws is not None else None
    if kdir is None or (kdir / f"{board}.kicad_pro").is_file():
        return board
    pros = sorted(kdir.glob("*.kicad_pro")) if kdir.is_dir() else []
    if len(pros) == 1:
        return pros[0].stem
    if not pros and _PN_DIR.match(Path(ws).name) \
            and Path(ws).name.endswith("_" + board):
        return Path(ws).name
    return board


def kind_path(kind: str, board: str, imap: dict,
              registry: dict | None = None, ws: Path | None = None) -> str:
    """Workspace-relative path for a standard artifact kind; {board} in the
    template is the KiCad project basename (project_stem(ws, board)) when the
    workspace is given. A registry entry
    registered under the kind's name overrides the default template (how a
    non-standard layout opts in) - but ONLY when the entry's own `kind` field
    matches. v1 registries reused kind names for different pipeline artifacts
    (lumina-strobe's "constraints" was architecture/constraints.json, NOT the
    kicad/ sidecar the verify gate reads); an unqualified name-match would
    silently hash the wrong file and MISS real staleness."""
    reg = (registry or {}).get(kind)
    if isinstance(reg, dict) and reg.get("path") and reg.get("kind") == kind:
        return reg["path"]
    template = imap["artifact_kinds"][kind]["path"]
    return template.replace("{board}", project_stem(ws, board))


def hash_kind(ws: Path, board: str, kind: str, imap: dict,
              registry: dict | None = None) -> tuple[str, str | None]:
    """(workspace-relative path, hash-or-None) for a standard kind."""
    rel = kind_path(kind, board, imap, registry, ws)
    norm = imap["artifact_kinds"][kind]["norm"]
    return rel, hash_artifact(ws / rel, norm)


def gate_input_hashes(ws: Path, board: str, gate: str, imap: dict,
                      registry: dict | None = None) -> dict[str, str | None]:
    """{kind: hash|None} for every input the gate reads; {} for a gate the
    map does not know (freshness then reports it 'unknown', never 'fresh')."""
    kinds = imap["gate_inputs"].get(gate)
    if not kinds:
        return {}
    return {k: hash_kind(ws, board, k, imap, registry)[1] for k in kinds}


def gate_freshness(gate_entry: dict, current: dict[str, str | None]) -> dict:
    """Two-layer verdict for one recorded gate entry against current hashes.

    hash_valid: True (all recorded inputs match), False (>=1 differs),
                None (no recorded inputs - pre-v2 result or unknown gate).
    fresh:      hash_valid is True AND no stale marks.
    """
    recorded = (gate_entry.get("last") or {}).get("inputs")
    marks = gate_entry.get("stale") or []
    if not isinstance(recorded, dict) or not recorded:
        return {"hash_valid": None, "changed_inputs": [],
                "stale_marks": marks, "fresh": False}
    changed = sorted(k for k, v in recorded.items() if current.get(k) != v)
    hash_valid = not changed
    return {"hash_valid": hash_valid, "changed_inputs": changed,
            "stale_marks": marks, "fresh": hash_valid and not marks}


def freshness_report(data: dict, ws: Path,
                     imap: dict | None = None) -> dict:
    """Full freshness view of a state.json v2 payload.

    artifacts: every registered entry + every standard kind mapped for a
    recorded gate - {path, kind, exists, registered, current, changed, marks}.
    gates: per recorded gate the gate_freshness verdict + recorded/current
    hashes. summary.human_hold_pending = max human_hold over all outstanding
    marks (0 when everything is clean).
    """
    imap = imap or load_map()
    board = data.get("board") or ""
    registry = data.get("artifacts") or {}

    gates_out: dict[str, dict] = {}
    for gname, gentry in (data.get("gates") or {}).items():
        current = gate_input_hashes(ws, board, gname, imap, registry)
        verdict = gate_freshness(gentry, current)
        gates_out[gname] = {
            "status": gentry.get("status"),
            "recorded_inputs": (gentry.get("last") or {}).get("inputs"),
            "current_inputs": current,
            **verdict,
        }

    # artifacts: registered entries first, then any standard kind a recorded
    # gate reads that is not registered (visibility without registration)
    art_out: dict[str, dict] = {}
    for name, entry in registry.items():
        if not isinstance(entry, dict):        # pre-migration payload
            entry = {"path": str(entry)}
        rel = entry.get("path")
        kind = entry.get("kind")
        norm = (imap["artifact_kinds"][kind]["norm"]
                if kind in imap["artifact_kinds"]
                else norm_for_path(ws / rel) if rel else "text_eol")
        cur = hash_artifact(ws / rel, norm) if rel else None
        reg_sha = entry.get("sha256")
        art_out[name] = {
            "path": rel, "kind": kind,
            "exists": bool(rel) and (ws / rel).exists(),
            "registered": reg_sha, "current": cur,
            "changed": (reg_sha is not None or cur is not None)
                       and reg_sha != cur,
            "stale_marks": entry.get("stale") or [],
        }
    seen_kinds = {e.get("kind") for e in art_out.values()}
    for gname in gates_out:
        for kind in imap["gate_inputs"].get(gname, []):
            if kind in seen_kinds or kind in art_out:
                continue
            rel, cur = hash_kind(ws, board, kind, imap, registry)
            art_out[kind] = {
                "path": rel, "kind": kind, "exists": (ws / rel).exists(),
                "registered": None, "current": cur, "changed": False,
                "stale_marks": [],
            }
            seen_kinds.add(kind)

    holds = [m.get("human_hold", 0)
             for g in gates_out.values() for m in g["stale_marks"]]
    holds += [m.get("human_hold", 0)
              for a in art_out.values() for m in a["stale_marks"]]
    fresh = sorted(g for g, v in gates_out.items() if v["fresh"])
    stale = sorted(g for g, v in gates_out.items()
                   if not v["fresh"] and v["hash_valid"] is not None)
    unknown = sorted(g for g, v in gates_out.items()
                     if v["hash_valid"] is None)
    return {
        "board": board,
        "gates": gates_out,
        "artifacts": art_out,
        "summary": {"fresh": fresh, "stale": stale, "unknown": unknown,
                    "human_hold_pending": max(holds, default=0)},
    }
