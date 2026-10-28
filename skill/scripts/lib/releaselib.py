"""releaselib.py - release attestation, durable waivers, derived disposition (U5).

Codex C1: a workflow phase is not a release certificate. This module supplies
the certificate: an immutable ATTESTATION manifest (fab/attestation.json) that
binds the exact normalized design hashes, the strict release-gate reports with
their U2 coverage matrices, the durable waivers, the fab package hashes, the
manufacturing options and the recorded human approvals. Anything order-shaped
(order_submit.py, the `order` verb) consumes ONLY this attestation; any
bound-input change invalidates it.

Codex H9: waivers become durable evidence. A DURABLE waiver carries, beyond
the T6 reason+approved: the artifact hash it was approved against, the checker
version that produced the finding, an expiry date, and a precise fingerprint
(check/kind/net/refs/rounded pos). A changed artifact or checker invalidates
it unless re-approved; a waiver that lists refs never matches a refs-less
finding (the empty-ref subset poison, rf-de-20m LEARNINGS).

The release DISPOSITION is DERIVED, never hand-set (first match wins):

    blocked            escalated issue or a fab/restrictions.json hold entry
    rework-required    any recorded gate fail, or open/fixing issues
    derated            a derate restriction or an order carrying a
                       manufacturing-option waiver (the pd-trigger 1 oz case)
    bring-up-passed    state history event `bringup_passed` (U10/T11 writes it)
    built              state history event `boards_received` (T11 writes it)
    ordered            fab/order.json records an order (web or API)
    order-ready        fab/attestation.json exists and verifies VALID
    release-candidate  strict release reports current + passing, not attested
    engineering-validated  all applicable pipeline gates pass + hash-fresh
    draft              everything else

Toolchain-free (venv only). Heavy validation of the strict reports reuses
gate.py (validate_report/evaluate) via the statelib-style lazy script import -
this lib must not grow a second report validator.

Failure direction is conservative throughout: an unreadable input is a
problem, an absent bound file whose hash was recorded non-null is a problem,
and a recorded-null hash is BINDING (the file appearing later invalidates).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# Dual-context import (checklib._statelib's pattern): this module is loaded
# both as bare `releaselib` (lib on sys.path) and as `lib.releaselib`
# (scripts dir on sys.path) - make the sibling imports work in both.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import checklib  # noqa: E402
import env  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402

ATTEST_SCHEMA = 1
ATTEST_REL_PATH = "fab/attestation.json"
RESTRICTIONS_REL_PATH = "fab/restrictions.json"

DISPOSITIONS = ("draft", "engineering-validated", "release-candidate",
                "order-ready", "ordered", "built", "bring-up-passed",
                "derated", "rework-required", "blocked")

# Strict release gates (gates.yaml, U2) and the report files attest consumes.
RELEASE_REPORTS = (("verify_release", "reports/verify_release.json"),
                   ("dfm_release", "reports/dfm_release.json"))

# The only gate a board may declare not-applicable for release (sbuck-class
# "simulation intentionally skipped" rulings). erc/place/drc_routed/verify/dfm
# are never excusable - a release without them is not a release.
NA_ALLOWED_GATES = ("sim",)

# Durable-waiver required fields beyond T6's reason+approved (H9).
DURABLE_FIELDS = ("artifact", "checker_version", "expires")

POS_TOL_MM = 0.01

# Artifact kinds added to invalidation.yaml after attestations were already
# being sealed. An attestation that predates one does not bind it, and that
# is accepted only while the file is absent (the state it was attested in);
# once the file exists the board must be re-attested. place_waivers:
# 2026-10-05, when the place gate began applying reports/place-waivers.json.
LATE_KINDS = frozenset({"place_waivers"})

_LAYER_DEF_RE = re.compile(
    r'\(\s*\d+\s+"([A-Za-z0-9_.]+\.Cu)"\s+(?:signal|power|mixed|jumper)\b')


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


# ---------------------------------------------------------------------------
# lazy sibling-script imports (statelib's netlist_audit pattern)
# ---------------------------------------------------------------------------
def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _import_script(name: str):
    scripts = _scripts_dir()
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    return __import__(name)


# ---------------------------------------------------------------------------
# seal
# ---------------------------------------------------------------------------
def seal(att: dict) -> str:
    """Content hash over the canonical JSON minus the seal field itself."""
    body = {k: v for k, v in att.items() if k != "attestation_sha256"}
    blob = json.dumps(body, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def check_seal(att: dict) -> bool:
    return bool(att.get("attestation_sha256")) \
        and att["attestation_sha256"] == seal(att)


# ---------------------------------------------------------------------------
# durable waivers (H9)
# ---------------------------------------------------------------------------
def _rounded_pos(pos) -> list[float] | None:
    if not isinstance(pos, (list, tuple)) or len(pos) < 2:
        return None
    try:
        return [round(float(pos[0]), 2), round(float(pos[1]), 2)]
    except (TypeError, ValueError):
        return None


def waiver_fingerprint(v: dict) -> str:
    """Stable fingerprint of a finding/waiver: check + kind + net + refs +
    rounded pos. Used to record exactly WHICH findings an attestation waived."""
    body = {
        "check": v.get("check") or v.get("source"),
        "kind": v.get("kind"),
        "net": v.get("net"),
        "refs": sorted(set(v.get("refs") or [])),
        "pos": _rounded_pos(v.get("pos")),
    }
    blob = json.dumps(body, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _parse_expiry(value) -> _dt.datetime | None:
    """Expiry as an aware UTC datetime; None when unparsable."""
    try:
        d = _dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def waiver_validity(w: dict, report: dict) -> list[str]:
    """Durability problems for one waiver entry against the report that
    carries the findings. Only fields the entry CARRIES are checked (lenient
    mid-pipeline waivers may omit them); durable_problems() additionally
    REQUIRES them for strict/release contexts.

    - artifact: must equal the report's input_digest (the normalized hash of
      the artifact the findings were produced from). A changed artifact
      invalidates the waiver unless re-approved against the new hash.
    - checker_version: must equal the report's stamped checker_version. A
      bumped checker invalidates every waiver approved under the old one.
    - expires: must parse and lie in the future.
    """
    problems: list[str] = []
    label = w.get("check") or w.get("kind") or "?"
    if "artifact" in w:
        digest = report.get("input_digest")
        if not digest:
            problems.append(f"waiver [{label}]: report carries no "
                            "input_digest to validate the artifact binding")
        elif w["artifact"] != digest:
            problems.append(
                f"waiver [{label}]: approved against artifact "
                f"{str(w['artifact'])[:24]}.. but the report's input is "
                f"{str(digest)[:24]}.. - re-approve against the current "
                "artifact")
    if "checker_version" in w:
        cv = report.get("checker_version")
        if w["checker_version"] != cv:
            problems.append(
                f"waiver [{label}]: approved under checker_version "
                f"{w['checker_version']!r} but the report was produced by "
                f"checker_version {cv!r} - re-approve under the current "
                "checkers")
    if "expires" in w:
        exp = _parse_expiry(w["expires"])
        if exp is None:
            problems.append(f"waiver [{label}]: unparsable expires "
                            f"{w['expires']!r}")
        elif exp <= _utcnow():
            problems.append(f"waiver [{label}]: expired {w['expires']}")
    return problems


def durable_problems(waivers: list[dict], report: dict) -> list[str]:
    """Strict/release contexts: every waiver must be a DURABLE waiver -
    it must carry artifact + checker_version + expires and all must
    validate against the report."""
    problems: list[str] = []
    for i, w in enumerate(waivers or []):
        label = w.get("check") or w.get("kind") or f"#{i}"
        missing = [f for f in DURABLE_FIELDS if f not in w]
        if missing:
            problems.append(
                f"waiver [{label}]: not durable - missing "
                f"{'/'.join(missing)} (release waivers must bind the "
                "artifact hash, checker version and an expiry; H9)")
        problems.extend(waiver_validity(w, report))
    return problems


def _ws_rel(path: Path, ws: Path) -> str:
    """Workspace-relative POSIX string, robust to either side being relative.

    `waivers_for_input` returns an ABSOLUTE path when it resolves through the
    boards/<b> ancestor (the normal governed-workspace case) but a path shaped
    like its argument otherwise, while `ws` is whatever the caller passed on the
    command line. Comparing the two directly raised
    "ValueError: '<abs waiver>' is not in the subpath of '<rel ws>'" for every
    board carrying a waiver sidecar, which broke `attest.py build` and silently
    degraded `state.py resume`'s release_disposition to null (2026-08-27,
    g0-sense - the first attested board with a waiver). Resolve both sides
    before relativising; the emitted string is unchanged, so attestations
    written before this fix still verify."""
    return str(Path(path).resolve()
               .relative_to(Path(ws).resolve())).replace("\\", "/")


def waivers_for_input(input_file: Path,
                      name: str = "verify-waivers.json") -> Path | None:
    """Default waiver-sidecar resolution shared with gate.py: the board
    WORKSPACE reports/ dir first (where every real waiver file lives, and
    where invalidation.yaml's `waivers` kind now points, so gate freshness
    and gate application bind the SAME file), then the input file's own
    reports/ dir (the T6-documented location, kept as fallback). LEARNINGS
    2026-08-08: the old input-dir-only default silently ignored every
    workspace waiver file. `name` picks the sidecar: verify-waivers.json for
    the verify gates, place-waivers.json for the place gate (gate.py
    WAIVER_SIDECARS)."""
    p = Path(input_file)
    candidates = []
    parents = list(p.resolve().parents)
    # The workspace is identified by its own marker (state.json), not by where
    # it sits: a board checked out in a worktree (~/.cc/worktrees/boards/
    # <track>/<board>) has no boards dir above it. The boards-dir rule stays
    # as the fallback for a workspace that has no state.json yet.
    for parent in parents:
        if (parent / "state.json").is_file():
            candidates.append(parent / "reports" / name)
            break
    for parent in parents:
        if env.is_boards_dir(parent.parent):
            candidates.append(parent / "reports" / name)
            break
    candidates.append(p.parent / "reports" / name)
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---------------------------------------------------------------------------
# workspace facts
# ---------------------------------------------------------------------------
def load_state(ws: Path) -> dict:
    data = checklib.load_json(Path(ws) / "state.json", "state file")
    if data.get("version") != 2:
        raise CheckError(f"{ws}/state.json: version "
                         f"{data.get('version')!r} unsupported (need v2)")
    return data


def pcb_layer_count(pcb: Path) -> int | None:
    """Copper layer count from the board's (layers ...) definition block -
    only definitions carry the signal/power/... type word, so pad layer
    lists never match."""
    try:
        text = Path(pcb).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    layers = set(_LAYER_DEF_RE.findall(text))
    return len(layers) or None


def derive_inner_copper_oz(ws: Path) -> float | None:
    """Inner copper weight from the stackup the Chosen section names - the
    same source order_submit.derive_copper_oz uses for OUTER copper. JLC's
    inner copper selects a different lamination family (LEARNINGS
    2026-08-06 [stackup][jlcapi][ordering]), so a 4-layer release must bind
    it. None: 2-layer stackup, no yaml-known stackup named, or not
    derivable - callers treat None as 'no inner layers to bind'."""
    order_submit = _import_script("order_submit")
    _oz, source = order_submit.derive_copper_oz(Path(ws))
    m = re.search(r"stackups\.yaml\[([^\]]+)\]", source or "")
    if not m:
        return None
    try:
        import yaml
        doc = yaml.safe_load(
            (_scripts_dir().parent / "reference" / "stackups.yaml")
            .read_text(encoding="utf-8"))
        stack = (((doc or {}).get("stackups") or {}).get(m.group(1))
                 or {}).get("stack") or []
    except Exception:  # noqa: BLE001 - reference data unreadable
        return None
    coppers = [ly for ly in stack if ly.get("type") == "copper"]
    if len(coppers) < 4:
        return None
    return float(coppers[1].get("copper_oz", 0.5))


def load_restrictions(ws: Path) -> tuple[list[dict], list[str]]:
    """fab/restrictions.json: optional, owner-authored. A list (or
    {restrictions: [...]}) of {kind: hold|derate|note, note, approved}."""
    path = Path(ws) / RESTRICTIONS_REL_PATH
    if not path.is_file():
        return [], []
    problems: list[str] = []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"{RESTRICTIONS_REL_PATH}: unreadable ({exc})"]
    entries = doc.get("restrictions") if isinstance(doc, dict) else doc
    if not isinstance(entries, list):
        return [], [f"{RESTRICTIONS_REL_PATH}: no restrictions list"]
    out = []
    for i, r in enumerate(entries):
        if not isinstance(r, dict) or r.get("kind") not in ("hold", "derate",
                                                            "note"):
            problems.append(f"{RESTRICTIONS_REL_PATH}: entry {i} needs kind "
                            "hold|derate|note")
            continue
        if not str(r.get("note") or "").strip():
            problems.append(f"{RESTRICTIONS_REL_PATH}: entry {i} needs a "
                            "non-empty note")
            continue
        out.append(r)
    return out, problems


def _release_na(ws: Path, board: str, imap: dict,
                registry: dict | None) -> tuple[dict, list[str]]:
    """constraints.json release.not_applicable.<gate> declarations (U2's
    verification.not_applicable pattern, lifted to whole gates). Only
    NA_ALLOWED_GATES may be excused; every entry needs reason + approved."""
    rel = statelib.kind_path("constraints", board, imap, registry, ws)
    path = Path(ws) / rel
    if not path.is_file():
        return {}, []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"{rel}: unreadable ({exc})"]
    na = ((doc.get("release") or {}).get("not_applicable")
          if isinstance(doc, dict) else None) or {}
    problems: list[str] = []
    out: dict = {}
    for gate_name, entry in na.items():
        if gate_name not in NA_ALLOWED_GATES:
            problems.append(
                f"release.not_applicable.{gate_name}: only "
                f"{'/'.join(NA_ALLOWED_GATES)} may be declared not "
                "applicable for release")
            continue
        if not isinstance(entry, dict) \
                or not str(entry.get("reason") or "").strip() \
                or not str(entry.get("approved") or "").strip():
            problems.append(f"release.not_applicable.{gate_name}: needs "
                            "non-empty reason and approved")
            continue
        out[gate_name] = {"reason": entry["reason"],
                          "approved": entry["approved"]}
    return out, problems


def required_gates(ws: Path, board: str, imap: dict,
                   registry: dict | None) -> list[str]:
    """The pipeline gates a release must show: state.py's GATE_ORDER plus
    sim when the workspace has a sims directory (declared by existence -
    a board that ships testbenches must pass them).

    U16: the owed set has ONE definition - state.applicable_gate_order, which
    set_phase and resume_summary read too. A release requiring a gate the
    phase machine does not is exactly how evidence goes missing."""
    state_mod = _import_script("state")
    return [g for _, g in state_mod.applicable_gate_order(
        Path(ws), board, registry, imap)]


def _git_head(ws: Path) -> str | None:
    try:
        cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ws),
                            capture_output=True, text=True, timeout=20)
        return cp.stdout.strip() or None if cp.returncode == 0 else None
    except Exception:  # noqa: BLE001 - advisory field only
        return None


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def build(ws: Path,
          max_report_age_h: float = 24.0) -> tuple[dict | None, list[str]]:
    """Assemble the attestation for a workspace, or (None, problems).

    Refusal lists EVERY miss, not the first - the codex acceptance is
    "refuses ... even with fresh dfm", which only reads honestly when the
    operator sees the full gap list at once.

    Pass criteria always come from the CANONICAL gates.yaml - build takes
    no override (U5 adversarial review: a doctored criteria file could
    attest a failing strict report and leave no trace), and verify()
    re-evaluates the bound reports against the canonical file anyway."""
    ws = Path(ws)
    problems: list[str] = []
    data = load_state(ws)
    board = data.get("board") or ""
    imap = statelib.load_map()
    registry = data.get("artifacts") or {}

    # ---- pipeline gates: recorded pass AND hash-fresh -------------------
    na, na_problems = _release_na(ws, board, imap, registry)
    problems += na_problems
    fresh = statelib.freshness_report(data, ws, imap)
    # ANY recorded gate failing - required or not - blocks release: the
    # disposition ladder calls that state rework-required, and an
    # attestation must never disagree with it (review: 'non-required gate
    # FAIL -> rework-required yet attestation valid').
    for g, entry in sorted((data.get("gates") or {}).items()):
        if entry.get("status") == "fail":
            problems.append(
                f"gate {g}: recorded 'fail' "
                f"({(entry.get('last') or {}).get('failing_count', '?')} "
                "failing)")
    # Artifact-level stale marks (a declared edit whose derived artifact
    # was never regenerated) and pending human holds block release too -
    # gate freshness alone does not see them (review: 'attestation binds
    # stale fab package').
    for name, art in sorted((fresh.get("artifacts") or {}).items()):
        if art.get("stale_marks"):
            problems.append(
                f"artifact {name}: carries {len(art['stale_marks'])} stale "
                "mark(s) - regenerate it (state.py rehash --names) first")
    gate_snap: dict = {}
    for g in required_gates(ws, board, imap, registry):
        if g in na:
            gate_snap[g] = {"status": "not_applicable", **na[g]}
            continue
        entry = data.get("gates", {}).get(g)
        if not entry or not entry.get("last"):
            problems.append(f"gate {g}: never recorded")
            continue
        if entry.get("status") != "pass":
            problems.append(
                f"gate {g}: recorded {entry.get('status')!r} "
                f"({(entry.get('last') or {}).get('failing_count', '?')} "
                "failing)")
            continue
        verdict = fresh["gates"].get(g) or {}
        if not verdict.get("fresh"):
            why = []
            if verdict.get("hash_valid") is None:
                why.append("no recorded input hashes (pre-v2 record - "
                           "re-run the gate)")
            elif verdict.get("changed_inputs"):
                why.append("inputs changed: "
                           + ", ".join(verdict["changed_inputs"]))
            if verdict.get("stale_marks"):
                why.append(f"{len(verdict['stale_marks'])} stale mark(s)")
            problems.append(f"gate {g}: passed but not fresh "
                            f"({'; '.join(why) or 'unknown'})")
            continue
        gate_snap[g] = {"status": "pass",
                        "ts": entry["last"].get("ts"),
                        "failing_count": entry["last"].get("failing_count", 0),
                        "inputs": entry["last"].get("inputs")}

    # ---- issues + approvals ---------------------------------------------
    live = [i for i in data.get("open_issues", [])
            if i.get("status") in ("open", "fixing", "escalated")]
    if live:
        problems.append(
            "open issues: "
            + ", ".join(f"#{i.get('id')}({i.get('status')})" for i in live))
    h4 = (data.get("human") or {}).get("4") or {}
    if h4.get("status") != "approved":
        problems.append(
            f"human checkpoint 4 (P8 review) is {h4.get('status') or 'missing'!r}"
            " - release needs an explicit approval, never a skip")

    # ---- strict release reports (U2's coverage matrices) ----------------
    gate_mod = _import_script("gate")
    gates_def = gate_mod.load_gates(gate_mod.DEFAULT_GATES)
    pcb_rel = statelib.kind_path("pcb", board, imap, registry, ws)
    pcb_path = ws / pcb_rel
    waiver_path = waivers_for_input(pcb_path)
    waivers: list[dict] = []
    if waiver_path is not None:
        try:
            waivers = gate_mod.load_waivers(waiver_path)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"waivers {waiver_path}: {exc}")
            waivers = []

    reports_snap: dict = {}
    waived_fps: list[str] = []
    for gate_name, rel in RELEASE_REPORTS:
        gdef = gates_def.get(gate_name)
        if gdef is None:
            problems.append(f"gates.yaml has no {gate_name} gate")
            continue
        rpath = ws / rel
        if not rpath.is_file():
            problems.append(f"{rel}: missing - run the strict {gate_name} "
                            "tool and save its stamped report there")
            continue
        try:
            report = json.loads(rpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{rel}: unreadable ({exc})")
            continue
        try:
            gate_mod.validate_report(gate_name, gdef, report, pcb_path,
                                     max_age_h=max_report_age_h)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{rel}: {exc}")
            continue
        applied = waivers if gdef.get("tool") == "verify" else None
        if applied:
            dp = durable_problems(applied, report)
            if dp:
                problems += dp
                continue
        result = gate_mod.evaluate(gate_name, gdef, report, waivers=applied)
        if result.get("status") != "pass":
            problems.append(f"{gate_name}: {result.get('failing_count')} "
                            "failing after waivers")
            continue
        fps = sorted(waiver_fingerprint(v) for v in result.get("waived") or [])
        waived_fps += fps
        reports_snap[gate_name] = {
            "path": rel,
            "digest": statelib.hash_artifact(rpath, "json_canonical"),
            "coverage": result.get("coverage"),
            "waived_fingerprints": fps,
        }

    # ---- inputs: every artifact kind + the stackup doc ------------------
    # input_paths records the RESOLVED path per kind: verify() compares the
    # current resolution against it, so a state.json registry redirect
    # (pointing a kind at a different file) invalidates instead of quietly
    # re-anchoring the hash check (review: 'registry redirect').
    inputs: dict = {}
    input_paths: dict = {}
    for kind in sorted(imap["artifact_kinds"]):
        rel, sha = statelib.hash_kind(ws, board, kind, imap, registry)
        inputs[kind] = sha
        input_paths[kind] = rel.replace("\\", "/")
    for kind in ("pcb", "sch", "netlist"):
        if inputs.get(kind) is None:
            problems.append(f"required input {kind} missing "
                            f"({statelib.kind_path(kind, board, imap, registry, ws)})")
    stackup_rel = "architecture/stackup.md"
    stackup_hash = statelib.hash_artifact(ws / stackup_rel, "text_eol")
    inputs["stackup_md"] = stackup_hash
    if stackup_hash is None:
        problems.append(f"{stackup_rel} missing - manufacturing options "
                        "cannot be attested without it")

    # ---- fab package ----------------------------------------------------
    fab: dict = {}
    gerber_rel = statelib.kind_path("gerbers", board, imap, registry, ws)
    gerber_path = ws / gerber_rel
    if not gerber_path.is_file():
        problems.append(f"fab package incomplete: {gerber_rel} missing")
    else:
        import fabhash  # sibling lib module (statelib's pattern)
        fab["gerber_zip"] = {
            "path": gerber_rel,
            "sha256": hashlib.sha256(gerber_path.read_bytes()).hexdigest(),
            "design_sha256": fabhash.design_hash(gerber_path),
        }
    for kind in ("bom", "cpl", "bom_full"):
        rel = statelib.kind_path(kind, board, imap, registry, ws)
        if inputs.get(kind) is None:
            problems.append(f"fab package incomplete: {rel} missing "
                            "(U3: BOM-full.csv is the BOM of record)")
        else:
            fab[kind] = {"path": rel, "hash": inputs[kind]}

    # ---- manufacturing options (values, not file refs) ------------------
    order_submit = _import_script("order_submit")
    oz, oz_source = order_submit.derive_copper_oz(ws)
    if oz is None:
        problems.append(f"copper weight cannot be derived: {oz_source}")
    layers = pcb_layer_count(pcb_path)
    if layers is None:
        problems.append(f"cannot count copper layers in {pcb_rel}")
    quote_spec = {}
    quote_path = ws / "fab" / "quote.json"
    if quote_path.is_file():
        try:
            quote_spec = (json.loads(
                quote_path.read_text(encoding="utf-8")) or {}).get("spec") or {}
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"fab/quote.json unreadable ({exc})")
    if quote_spec.get("layers") and layers \
            and int(quote_spec["layers"]) != int(layers):
        problems.append(f"quote spec says {quote_spec['layers']} layers but "
                        f"the board has {layers}")
    thickness = quote_spec.get("thickness_mm")
    if thickness is None:
        problems.append("fab/quote.json spec.thickness_mm missing - board "
                        "thickness is a manufacturing option the "
                        "attestation must bind (run order_quote first)")
    inner = quote_spec.get("inner_copper_weight_oz")
    if inner is None:
        inner = derive_inner_copper_oz(ws)
    if layers and int(layers) >= 4 and inner is None:
        problems.append(
            "inner copper weight underivable for a 4+ layer board: the "
            "Chosen stackup must be a reference/stackups.yaml entry (inner "
            "copper selects a different JLC lamination family)")
    # surface_finish / solder_mask_color: bound when the workspace DECLARES
    # them (quote spec); None means 'order-time choice' - the quote matrix
    # deliberately enumerates both HASL and ENIG rows, so there is nothing
    # design-derived to bind. A board that REQUIRES a finish declares it in
    # the quote spec and it binds like every other option.
    manufacturing = {
        "layers": layers,
        "copper_weight_oz": oz,
        "copper_weight_source": oz_source,
        "inner_copper_weight_oz": inner,
        "thickness_mm": thickness,
        "surface_finish": quote_spec.get("surface_finish"),
        "solder_mask_color": quote_spec.get("solder_mask_color"),
    }

    # ---- restrictions + approvals snapshot ------------------------------
    restrictions, r_problems = load_restrictions(ws)
    problems += r_problems

    if problems:
        return None, sorted(set(problems))

    prior = load_attestation(ws)
    att = {
        "script": "attest",
        "kind": "release_attestation",
        "attest_schema": ATTEST_SCHEMA,
        "board": board,
        "rev": (prior.get("rev", 0) + 1) if prior else 1,
        "supersedes": prior.get("attestation_sha256") if prior else None,
        "created": _utcnow().isoformat(timespec="seconds"),
        "git_head": _git_head(ws),
        "checker_version": checklib.CHECKER_VERSION,
        "inputs": inputs,
        "input_paths": input_paths,
        "gates": gate_snap,
        "reports": reports_snap,
        "waivers": {
            "path": (_ws_rel(waiver_path, ws)
                     if waiver_path is not None else None),
            "hash": (statelib.hash_artifact(waiver_path, "json_canonical")
                     if waiver_path is not None else None),
            "count": len(waivers),
            "waived_fingerprints": sorted(set(waived_fps)),
        },
        "fab": fab,
        "manufacturing": manufacturing,
        "human_approvals": data.get("human") or {},
        "known_restrictions": restrictions,
    }
    att["attestation_sha256"] = seal(att)
    return att, []


def load_attestation(ws: Path) -> dict | None:
    path = Path(ws) / ATTEST_REL_PATH
    if not path.is_file():
        return None
    try:
        att = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return att if isinstance(att, dict) else None


def write_attestation(ws: Path, att: dict) -> Path:
    path = Path(ws) / ATTEST_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(att, indent=1), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
def verify(ws: Path, att: dict | None = None) -> dict:
    """Re-verify an attestation against the CURRENT tree + state. Read-only.

    The required-check set is RE-DERIVED here, never read off the
    attestation (U5 adversarial review: the seal is a content checksum, so
    a forged attestation could otherwise omit sections and skip their
    checks). verify() independently requires: full input coverage at the
    attested paths, every required gate bound + currently passing, both
    strict reports bound AND re-evaluated against the canonical gates.yaml
    under durable waivers, the fab package bound, checkpoint 4 approved,
    checker currency, restrictions unchanged with no hold, no artifact
    stale marks, no live issues, no recorded rejection anywhere.

    NOT invalidated by: report age (bounded at build), history appends, or
    a gate re-run that still passes on identical inputs."""
    ws = Path(ws)
    if att is None:
        att = load_attestation(ws)
    if att is None:
        return {"valid": False, "attested": False,
                "problems": [f"{ATTEST_REL_PATH}: no attestation"]}
    problems: list[str] = []
    if att.get("attest_schema") != ATTEST_SCHEMA:
        problems.append(f"attest_schema {att.get('attest_schema')!r} "
                        f"(this build reads {ATTEST_SCHEMA})")
    if not check_seal(att):
        problems.append("seal mismatch - the attestation file was modified "
                        "after issue")
    if problems:
        return {"valid": False, "attested": True, "problems": problems}
    if att.get("checker_version") != checklib.CHECKER_VERSION:
        problems.append(
            f"attested under checker_version {att.get('checker_version')!r} "
            f"but the current checkers are {checklib.CHECKER_VERSION} - "
            "re-run the strict tools and re-attest")

    imap = statelib.load_map()
    board = att.get("board") or ""

    # state may legitimately evolve (history appends); reload leniently
    try:
        data = load_state(ws)
    except CheckError as exc:
        data = None
        problems.append(str(exc))
    registry = (data or {}).get("artifacts") or {}

    if data is not None and data.get("board") != board:
        problems.append(f"state board {data.get('board')!r} != attested "
                        f"{board!r}")

    # ---- bound inputs: REQUIRED coverage at the attested paths ----------
    att_inputs = att.get("inputs") or {}
    att_paths = att.get("input_paths") or {}
    for kind in sorted(set(imap["artifact_kinds"]) | {"stackup_md"}):
        if kind not in att_inputs:
            if kind in LATE_KINDS and statelib.hash_kind(
                    ws, board, kind, imap, registry)[1] is None:
                continue
            problems.append(f"input {kind}: not bound by the attestation")
            continue
        recorded = att_inputs[kind]
        if kind == "stackup_md":
            cur = statelib.hash_artifact(ws / "architecture" / "stackup.md",
                                         "text_eol")
        else:
            rel, cur = statelib.hash_kind(ws, board, kind, imap, registry)
            bound_rel = att_paths.get(kind)
            if bound_rel and rel.replace("\\", "/") != bound_rel:
                problems.append(
                    f"input {kind}: resolution moved ({bound_rel} -> "
                    f"{rel.replace(chr(92), '/')}) - a registry redirect "
                    "invalidates the attestation")
        if cur != recorded:
            problems.append(f"input {kind}: changed since attestation "
                            f"(recorded {str(recorded)[:24]!r}, "
                            f"current {str(cur)[:24]!r})")

    # ---- waiver file + re-validation ------------------------------------
    gate_mod = _import_script("gate")
    pcb_rel = statelib.kind_path("pcb", board, imap, registry, ws)
    wv = att.get("waivers") or {}
    waivers: list[dict] = []
    if wv.get("path"):
        cur = statelib.hash_artifact(ws / wv["path"], "json_canonical")
        if cur != wv.get("hash"):
            problems.append(f"waivers {wv['path']}: changed or missing - a "
                            "waiver edit invalidates the attestation")
        try:
            waivers = gate_mod.load_waivers(ws / wv["path"])
        except Exception as exc:  # noqa: BLE001
            problems.append(f"waivers {wv['path']}: {exc}")
            waivers = []
        for w in waivers:
            if "expires" in w:
                exp = _parse_expiry(w["expires"])
                if exp is None or exp <= _utcnow():
                    problems.append(
                        f"waiver [{w.get('check') or w.get('kind')}]: "
                        f"expired {w.get('expires')}")
    resolved = waivers_for_input(ws / pcb_rel)
    resolved_rel = (_ws_rel(resolved, ws)
                    if resolved is not None else None)
    if resolved_rel != wv.get("path"):
        problems.append(f"waiver resolution changed: attested "
                        f"{wv.get('path')!r}, now {resolved_rel!r}")

    # ---- strict reports: REQUIRED, bound, and RE-EVALUATED --------------
    # Re-evaluation always uses the canonical gates.yaml criteria: an
    # attestation built against doctored criteria (or a doctored sealed
    # report) fails here regardless of what build() accepted.
    gates_def = gate_mod.load_gates(gate_mod.DEFAULT_GATES)
    att_reports = att.get("reports") or {}
    pcb_hash = statelib.hash_kind(ws, board, "pcb", imap, registry)[1]
    for gate_name, rel in RELEASE_REPORTS:
        rec = att_reports.get(gate_name)
        if not rec:
            problems.append(f"report {gate_name}: not bound by the "
                            "attestation")
            continue
        rpath = ws / rec.get("path", "")
        cur = statelib.hash_artifact(rpath, "json_canonical")
        if cur != rec.get("digest"):
            problems.append(f"report {rec.get('path')}: changed or missing")
            continue
        gdef = gates_def.get(gate_name)
        if gdef is None:
            problems.append(f"gates.yaml has no {gate_name} gate")
            continue
        try:
            report = json.loads(rpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"report {rec.get('path')}: unreadable ({exc})")
            continue
        expected = gate_mod.EXPECTED_SCRIPT.get(gdef.get("tool"))
        if report.get("script") != expected:
            problems.append(f"report {rec.get('path')}: produced by "
                            f"{report.get('script')!r}, expected "
                            f"{expected!r}")
        if report.get("report_schema") != checklib.REPORT_SCHEMA:
            problems.append(f"report {rec.get('path')}: report_schema "
                            f"{report.get('report_schema')!r}")
        if report.get("checker_version") != checklib.CHECKER_VERSION:
            problems.append(
                f"report {rec.get('path')}: checker_version "
                f"{report.get('checker_version')!r} != current "
                f"{checklib.CHECKER_VERSION} - re-run the tool")
        if report.get("input_digest") != pcb_hash:
            problems.append(f"report {rec.get('path')}: input_digest does "
                            "not match the current pcb")
        applied = waivers if gdef.get("tool") == "verify" else None
        if applied:
            problems.extend(durable_problems(applied, report))
        try:
            result = gate_mod.evaluate(gate_name, gdef, report,
                                       waivers=applied)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{gate_name}: re-evaluation refused ({exc})")
            continue
        if result.get("status") != "pass":
            problems.append(f"{gate_name}: re-evaluates to "
                            f"{result.get('failing_count')} failing under "
                            "the canonical criteria")

    # ---- fab package: REQUIRED ------------------------------------------
    fab = att.get("fab") or {}
    gz = fab.get("gerber_zip")
    if not gz:
        problems.append("fab gerber_zip: not bound by the attestation")
    else:
        p = ws / gz.get("path", "")
        if not p.is_file():
            problems.append(f"fab {gz.get('path')}: missing")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != gz.get("sha256"):
            problems.append(f"fab {gz.get('path')}: bytes changed since "
                            "attestation (re-attest the new package)")
    for kind in ("bom", "cpl", "bom_full"):
        rec = fab.get(kind)
        if not rec:
            problems.append(f"fab {kind}: not bound by the attestation")
            continue
        cur = statelib.hash_kind(ws, board, kind, imap, registry)[1]
        if cur != rec.get("hash"):
            problems.append(f"fab {rec.get('path')}: changed or missing")

    # ---- manufacturing options ------------------------------------------
    man = att.get("manufacturing") or {}
    order_submit = _import_script("order_submit")
    oz, _src = order_submit.derive_copper_oz(ws)
    if oz != man.get("copper_weight_oz"):
        problems.append(
            f"copper weight now derives to {oz!r} but the attestation "
            f"bound {man.get('copper_weight_oz')!r} - a manufacturing-"
            "option change invalidates the release")
    inner = derive_inner_copper_oz(ws)
    if man.get("inner_copper_weight_oz") is not None \
            and inner != man.get("inner_copper_weight_oz"):
        problems.append(
            f"inner copper now derives to {inner!r}, attested "
            f"{man.get('inner_copper_weight_oz')!r}")
    layers = pcb_layer_count(ws / pcb_rel)
    if layers != man.get("layers"):
        problems.append(f"layer count now {layers!r}, attested "
                        f"{man.get('layers')!r}")
    quote_spec = {}
    quote_path = ws / "fab" / "quote.json"
    if quote_path.is_file():
        try:
            quote_spec = (json.loads(quote_path.read_text(encoding="utf-8"))
                          or {}).get("spec") or {}
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"fab/quote.json unreadable ({exc})")
    for key in ("thickness_mm", "surface_finish", "solder_mask_color"):
        av = man.get(key)
        cv = quote_spec.get(key)
        if key == "thickness_mm":
            if cv is not None and av is not None and float(cv) != float(av):
                problems.append(f"quote spec {key} now {cv!r}, attested "
                                f"{av!r}")
        elif (av or cv) and str(av) != str(cv):
            problems.append(f"quote spec {key} now {cv!r}, attested {av!r} "
                            "- manufacturing options changed after "
                            "attestation")

    # ---- restrictions: re-read, holds block, drift invalidates ----------
    restrictions, r_problems = load_restrictions(ws)
    problems += [f"restrictions unreadable: {p}" for p in r_problems]
    if restrictions != (att.get("known_restrictions") or []):
        problems.append("fab/restrictions.json changed since attestation - "
                        "re-approve by re-attesting")
    if any(r.get("kind") == "hold" for r in restrictions):
        problems.append("a HOLD restriction is recorded - release is "
                        "administratively blocked")

    # ---- current state: gates, issues, approvals ------------------------
    if data is not None:
        req = required_gates(ws, board, imap, registry)
        att_gates = att.get("gates") or {}
        na, na_problems = _release_na(ws, board, imap, registry)
        problems += na_problems
        for g in req:
            rec = att_gates.get(g)
            if rec is None:
                problems.append(f"gate {g}: required but not bound by the "
                                "attestation")
                continue
            if rec.get("status") == "not_applicable":
                if g not in na:
                    problems.append(f"gate {g}: attested not-applicable but "
                                    "constraints.json no longer declares it")
                continue
            cur = data.get("gates", {}).get(g) or {}
            if cur.get("status") != "pass":
                problems.append(f"gate {g}: now records "
                                f"{cur.get('status')!r}")
            if cur.get("stale"):
                problems.append(f"gate {g}: carries "
                                f"{len(cur['stale'])} stale mark(s)")
        # ANY recorded gate failing contradicts release, bound or not
        for g, entry in sorted((data.get("gates") or {}).items()):
            if entry.get("status") == "fail" and g not in att_gates:
                problems.append(f"gate {g}: records 'fail'")
        fresh = statelib.freshness_report(data, ws, imap)
        for name, art in sorted((fresh.get("artifacts") or {}).items()):
            if art.get("stale_marks"):
                problems.append(f"artifact {name}: carries "
                                f"{len(art['stale_marks'])} stale mark(s)")
        live = [i for i in data.get("open_issues", [])
                if i.get("status") in ("open", "fixing", "escalated")]
        if live:
            problems.append(
                "open issues: "
                + ", ".join(f"#{i.get('id')}({i.get('status')})"
                            for i in live))
        h4 = (att.get("human_approvals") or {}).get("4") or {}
        if h4.get("status") != "approved":
            problems.append("human checkpoint 4: not bound approved in the "
                            "attestation")
        for cp, rec in (att.get("human_approvals") or {}).items():
            cur = (data.get("human") or {}).get(cp)
            if cur != rec:
                problems.append(f"human checkpoint {cp}: changed since "
                                "attestation")
        # a rejection recorded ANYWHERE after attestation is a human veto
        for cp, cur in sorted((data.get("human") or {}).items()):
            if isinstance(cur, dict) and cur.get("status") == "rejected" \
                    and (att.get("human_approvals") or {}).get(cp) != cur:
                problems.append(f"human checkpoint {cp}: REJECTED after "
                                "attestation - human veto")

    return {"valid": not problems, "attested": True,
            "board": board, "rev": att.get("rev"),
            "attestation_sha256": att.get("attestation_sha256"),
            "problems": problems}


# ---------------------------------------------------------------------------
# disposition
# ---------------------------------------------------------------------------
def _order_record(ws: Path) -> tuple[dict | None, str | None]:
    """(record, corruption). A PRESENT-but-unparsable fab/order.json is a
    corruption fact, never 'no order' - treating it as absent disarmed the
    double-buy latch and read back order-ready (U5 adversarial review)."""
    path = Path(ws) / "fab" / "order.json"
    if not path.is_file():
        return None, None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"fab/order.json unreadable ({exc})"
    if not isinstance(doc, dict):
        return None, "fab/order.json is not a JSON object"
    return doc, None


def _has_order(order: dict | None) -> bool:
    if not order:
        return False
    api = order.get("api") or {}
    rec = api.get("order") or {}
    return bool(order.get("order_number") or rec.get("orderId")
                or rec.get("batchNum") or api.get("verdict") == "created")


def _order_derated(ws: Path, order: dict | None) -> bool:
    """An order carrying a manufacturing-option waiver (the pd-trigger
    1 oz case) is a derated build by record. Two signals: the COPPER
    WAIVER human_steps note, and the api quote's recorded
    copper_weight_source being a human override (the structured record -
    prose can be edited, the quote source is machine-written)."""
    if order and any("COPPER WAIVER" in str(s)
                     for s in order.get("human_steps") or []):
        return True
    aq = Path(ws) / "fab" / "api_quote.json"
    if aq.is_file():
        try:
            doc = json.loads(aq.read_text(encoding="utf-8"))
            if "override" in str(doc.get("copper_weight_source") or ""):
                return True
        except (OSError, json.JSONDecodeError):
            pass
    return False


def _ambiguous_create(order: dict | None) -> str | None:
    """An in-flight or ambiguously-failed pcb/create attempt: an order MAY
    exist server-side and no API surface can say - release questions are
    BLOCKED until a human clears it (order_submit documents the ritual)."""
    attempt = ((order or {}).get("api") or {}).get("create_attempt") or {}
    state = str(attempt.get("state") or "")
    if state == "in_flight" or state in ("failed:unknown_error",
                                         "failed:error"):
        return state
    return None


def _history_has(data: dict, event: str) -> bool:
    return any(h.get("event") == event for h in data.get("history") or [])


def _strict_reports_current(ws: Path, data: dict) -> bool:
    """release-candidate test: both strict reports exist, their recorded
    input digest matches the CURRENT pcb, and they evaluate to pass under
    the current (durable-validated) waivers. Age is not re-checked here -
    candidacy is about content currency, not clock."""
    imap = statelib.load_map()
    board = data.get("board") or ""
    registry = data.get("artifacts") or {}
    pcb_rel = statelib.kind_path("pcb", board, imap, registry, ws)
    pcb_hash = statelib.hash_kind(ws, board, "pcb", imap, registry)[1]
    if pcb_hash is None:
        return False
    gate_mod = _import_script("gate")
    gates_def = gate_mod.load_gates(gate_mod.DEFAULT_GATES)
    waiver_path = waivers_for_input(ws / pcb_rel)
    try:
        waivers = (gate_mod.load_waivers(waiver_path)
                   if waiver_path is not None else [])
    except Exception:  # noqa: BLE001
        return False
    for gate_name, rel in RELEASE_REPORTS:
        rpath = Path(ws) / rel
        gdef = gates_def.get(gate_name)
        if gdef is None or not rpath.is_file():
            return False
        try:
            report = json.loads(rpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if report.get("report_schema") != checklib.REPORT_SCHEMA \
                or report.get("input_digest") != pcb_hash \
                or report.get("checker_version") != checklib.CHECKER_VERSION \
                or report.get("script") != gate_mod.EXPECTED_SCRIPT.get(
                    gdef.get("tool")):
            return False
        applied = waivers if gdef.get("tool") == "verify" else None
        if applied and durable_problems(applied, report):
            return False
        try:
            result = gate_mod.evaluate(gate_name, gdef, report,
                                       waivers=applied)
        except Exception:  # noqa: BLE001
            return False
        if result.get("status") != "pass":
            return False
    return True


def disposition(ws: Path) -> dict:
    """Derived release disposition (codex C1). Never hand-set: every rung is
    computed from recorded facts. First match wins, worst first."""
    ws = Path(ws)
    data = load_state(ws)
    imap = statelib.load_map()
    board = data.get("board") or ""
    registry = data.get("artifacts") or {}

    restrictions, r_problems = load_restrictions(ws)
    order, order_corrupt = _order_record(ws)
    issues = data.get("open_issues") or []

    def out(d: str, basis: str, **extra) -> dict:
        return {"board": board, "disposition": d, "basis": basis, **extra}

    # fail closed: corrupt release-relevant records mean the release truth
    # is UNKNOWN - that is blocked, never a lower (more optimistic) rung
    if order_corrupt:
        return out("blocked", order_corrupt + " - fail closed; repair the "
                   "record before any release question")
    if r_problems:
        return out("blocked",
                   "restrictions unreadable/malformed: " + "; ".join(
                       r_problems) + " - fail closed")
    ambiguous = _ambiguous_create(order)
    if ambiguous:
        return out("blocked",
                   f"a pcb/create attempt is recorded {ambiguous!r} - an "
                   "order may exist server-side; verify the JLCPCB portal "
                   "and clear api.create_attempt first")
    escalated = [i for i in issues if i.get("status") == "escalated"]
    holds = [r for r in restrictions if r.get("kind") == "hold"]
    if escalated or holds:
        return out("blocked",
                   f"{len(escalated)} escalated issue(s), "
                   f"{len(holds)} hold restriction(s)")

    failing = sorted(g for g, e in (data.get("gates") or {}).items()
                     if e.get("status") == "fail")
    live = [i for i in issues if i.get("status") in ("open", "fixing")]
    if failing or live:
        return out("rework-required",
                   f"failing gates: {', '.join(failing) or 'none'}; "
                   f"open issues: {len(live)}",
                   failing_gates=failing)

    derates = [r for r in restrictions if r.get("kind") == "derate"]
    if derates or _order_derated(ws, order):
        return out("derated",
                   "derate restriction recorded" if derates else
                   "order carries a manufacturing-option waiver "
                   "(COPPER WAIVER)")

    if _history_has(data, "bringup_passed"):
        return out("bring-up-passed", "state history event bringup_passed")
    if _history_has(data, "boards_received"):
        return out("built", "state history event boards_received")
    if _has_order(order):
        return out("ordered",
                   f"order recorded ({order.get('order_number') or 'api'})")

    v = verify(ws)
    if v["valid"]:
        return out("order-ready",
                   f"attestation rev {v.get('rev')} verifies valid",
                   attestation_sha256=v.get("attestation_sha256"))

    if _strict_reports_current(ws, data):
        return out("release-candidate",
                   "strict release reports current and passing; "
                   "no valid attestation yet",
                   attestation_problems=v.get("problems"))

    fresh = statelib.freshness_report(data, ws, imap)
    req = required_gates(ws, board, imap, registry)
    na, _ = _release_na(ws, board, imap, registry)
    ok = all(
        g in na
        or ((data.get("gates", {}).get(g) or {}).get("status") == "pass"
            and (fresh["gates"].get(g) or {}).get("fresh"))
        for g in req)
    if ok:
        return out("engineering-validated",
                   "all applicable pipeline gates pass and are hash-fresh")

    return out("draft", "gates incomplete or not fresh")
