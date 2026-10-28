#!/usr/bin/env python
"""order_submit.py - order manifest, API quote, and SAFETY-GATED create (P10).

SPEC P10 is explicit: "Payment/final submission is always human." The default
flow therefore never spends money. It:

  1. verifies the fab package is present and internally consistent (gerber zip,
     BOM, CPL) and hashes every artefact,
  2. snapshots the board spec + the chosen quote row,
  3. writes fab/order.json - the traceability record (SPEC P10: order number,
     spec snapshot, gerber hash). The workspace's fab/order.json is ALWAYS
     the record of truth: it is read for prior state and written on every
     run regardless of --out (--out merely adds a copy of the payload
     elsewhere). A rebuild DEEP-MERGES the existing api block (esp.
     api.order) and preserves prior non-generated human_steps (the
     "BOARD-SPECIFIC ..." class survives verbatim forever), so a re-run
     never clobbers a placed-order record or hand-written order notes,
  4. emits the manual-submission deep links, including the JLCDFM upload step
     (V6: JLCDFM has no public API - that second opinion is semi-manual by
     design), and the exact human actions left.

JLCPCB Open API legs (lib/jlcapi.py; contract researched in
ai-library/jlcpcb-openapi-2026/contract.md; pcbParam per the hendley
api-reference PcbOrderCraftData table, the contract's [SDK/PDF] source):

  --api         QUOTE-ONLY: uploadGerber -> audit/get -> calculate, with
                pcbParam built from the spec snapshot + quote spec + the
                stackup-derived copper weight (architecture/stackup.md
                `## Chosen:` id, e.g. JLC2313_1.6_2oz -> "2"), and
                castellatedHoles 1 exactly when the board has castellated
                pads (spec_snapshot.castellated_pads, lib/castellation.py;
                a castellated board also gets a human step naming the
                option). Writes
                fab/api_quote.json (REAL price vs our estimate, shipList,
                achieveDateList, audit findings, the quoted gerber sha256)
                and records the verdict in order.json's api block. NEVER
                calls create. HTTP 403 means the service scope is still under
                review - that is a REPORTED outcome (verdict scope_pending,
                exit 0), not an error: it proves the signing works.

  --api-create  !!! REAL MONEY !!! The ONLY code path that calls pcb/create.
                JLCPCB has NO sandbox or test mode: a successful create places
                a real factory order, and payment mechanics are UNVERIFIED -
                the platform may auto-deduct the prepaid JLC Balance. Guards:
                  * LAYER GUARD: 4+ layer boards refuse outright - JLC's own
                    create returns an unclassifiable code 2 for them and
                    offers no way to ask whether an ambiguous create landed;
                    4L ordering is the web-cart path (see agents/ordering.md),
                  * created-latch: refuses when the CANONICAL fab/order.json
                    already records ANY order - api.order ids, a "created"
                    verdict, or a WEB order_number recorded via
                    --order-number - --out cannot sidestep it (reordering
                    requires manually clearing the record),
                  * ambiguous-attempt block: an api.create_attempt record is
                    pre-armed ON DISK immediately before the transport call;
                    a record left "in_flight" (crash / transport loss
                    mid-create) or "failed:unknown_error"/"failed:error"
                    (JLC answered ambiguously) refuses every later create
                    until a human verifies the portal and manually clears
                    api.create_attempt. Unambiguous rejections
                    (bad_signature/scope_pending/ip_blocked/rate_limited)
                    do not block a retry; any OTHER state, or none,
                    refuses too (a whitelist, not a blacklist),
                  * --api-quote-file: a FRESH (<24 h, not future-dated)
                    api_quote.json written by a --api run,
                  * design binding: the current package's NORMALIZED design
                    hash (lib/fabhash.py - export timestamps stripped) must
                    equal the one recorded at quote time. The order still goes
                    out against the quoted fileKey, i.e. the bytes JLC audited;
                    a harmless re-export no longer invalidates the quote, a
                    real copper change still does,
                  * freight attestation: shipping_method must be one of the
                    QUOTED shipList options and its recorded cost must match;
                    the grand total must equal real_price + freight,
                  * qty cross-check: the attested qty must equal the qty the
                    calculate request actually priced (pcbParam.qty),
                  * --confirm "<board> <qty>pcs <grand_total>": grand_total =
                    real_price + selected freight, exactly as recorded in
                    api_quote.json (typo-proof human token - the human proves
                    they read the real all-in quote).
                Optional --ship-json may contain ONLY shippingAddress /
                billingAddress / taxOrVATNumber / billingAddressFlag (any
                other key refuses - quote-binding fields are not overridable).
                shippingMethod comes from the api quote's recorded
                shipping_method (first shipList option - review it in
                api_quote.json before confirming). Tests must NEVER point
                this path at a live transport - mock the session.

Release attestation (U5, codex C1): a fab dir inside a GOVERNED workspace
(state.json beside it) orders through its release attestation or not at
all. The manifest carries a `release` block (release_governance ->
releaselib.verify); a governed workspace whose fab/attestation.json is
missing or invalid gets manifest status `not_order_ready` (exit 1) and the
API legs refuse BEFORE any network call (exit 2). When the attestation is
valid, its manufacturing options are LAW: --copper-oz that contradicts the
attested copper weight refuses instead of waiving (the pd-trigger 1 oz
case), quote-spec mismatches refuse, and --api-create additionally binds
the on-disk package to the ATTESTED design hash. Bare fab dirs with no
state.json stay on the legacy manifest flow (nothing recorded to attest).

CLI:
  order_submit.py --pcb board.kicad_pcb --fab-dir fab/ [--quote quote.json]
                  [--qty 5] [--api] [--api-create --api-quote-file q.json
                   --confirm "<board> <qty>pcs <total>" [--ship-json s.json]]
                  [--order-number N] [--out fab/order.json]
Exit 0 ready-for-human (incl. API verdicts ok / scope_pending / created)
/ 1 package incomplete or not order-ready (governed, attestation
missing/invalid) / 2 error (missing creds, bad signature, IP block, rate
limit, refused confirm/latch/whitelist/binding/attestation, transport
failure).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import castellation  # noqa: E402
import fabhash  # noqa: E402
import jlcapi  # noqa: E402
import releaselib  # noqa: E402
import safelib  # noqa: E402

# JLCPCB Open API pcb/create refuses 4+ layer boards: three live attempts on
# a 4L board all returned HTTP 200 {"code": 2, "message": "unknown_error"}
# while a 2L board created successfully from the same account, API, ship
# method and payload shape - and `calculate` prices 4L correctly, so only
# `create` is affected (LEARNINGS 2026-07-30 [ordering]). Until JLC support
# explains code 2, 4+ layer ordering is the WEB path.
API_CREATE_MAX_LAYERS = 2
JOURNAL_NAME = "order_attempts.jsonl"   # U12 append-only attempt journal

# pcb/audit is asynchronous on JLC's side: right after upload it returns
# business code 2501 (no_audit_result_error) until their DFM run finishes
# (live-observed 2026-07-29).
AUDIT_PENDING_CODE = 2501
AUDIT_POLL_DELAYS_S = (5.0, 10.0, 15.0)
_sleep = time.sleep  # test seam

JLCDFM_URL = "https://jlcdfm.com/"
JLC_QUOTE_URL = "https://cart.jlcpcb.com/quote"
API_ENV = ("HWDE_JLCPCB_APPID", "HWDE_JLCPCB_KEY", "HWDE_JLCPCB_SECRET")
# Same vars as jlcapi.PROBE_ENV; read through jlcapi.cred_env so the
# pre-rename AIEE_* spelling still counts as set.
API_QUOTE_MAX_AGE_H = 24.0
API_QUOTE_MAX_SKEW_S = 300.0    # future fetched_at beyond this = broken clock

# pcbParam enum maps (hendley PcbOrderCraftData table). surface finish:
# 0 HASL-lead, 1 LF-HASL, 2 ENIG - we never choose leaded, HASL -> 1.
SURFACE_FINISH_CODE = {"HASL": 1, "LF-HASL": 1, "HASL-LF": 1, "ENIG": 2}
PCB_COLOR_CODE = {"green": 0, "red": 1, "yellow": 2, "blue": 3, "white": 4,
                  "black": 5, "purple": 6}
# --ship-json may carry ONLY these create-payload keys (hendley create table:
# address/tax fields). Everything else - fileKey, pcbParam, orderType,
# shippingMethod, achieveDate, batchNum - is bound by the quote/flow and must
# not be overridable from a side file.
SHIP_JSON_KEYS = {"shippingAddress", "billingAddress", "taxOrVATNumber",
                  "billingAddressFlag"}

_OZ_ID_RE = re.compile(r"_(\d+(?:\.\d+)?)\s*oz\b", re.IGNORECASE)
_OZ_MENTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*oz\b", re.IGNORECASE)

# Prefixes of every human_steps line THIS script generates (run() boilerplate
# + API-leg notes). Anything else in a prior order.json - hand-written
# board-specific notes like "BOARD-SPECIFIC: 2oz copper MUST..." - is
# preserved verbatim across rewrites (they are order-safety evidence: the
# copper-mismatch guard greps them).
_GENERATED_STEP_PREFIXES = (
    "Upload ",
    "If assembling:",
    "Review, then pay.",
    "JLCPCB API scope approval pending",
    "API quote in ",
    "API order created:",
)


class ApiRefused(RuntimeError):
    """The API leg refused to proceed (mismatched confirm token, created-
    latch, stale/future quote, whitelist or gerber-binding violation, create
    rejected). CLI exit 2; the manifest is still written. `recorded` marks
    that a classified failure verdict is already in the api block."""

    def __init__(self, msg: str, recorded: bool = False):
        super().__init__(msg)
        self.recorded = recorded


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _find(fab_dir: Path, *patterns: str) -> Path | None:
    for pat in patterns:
        hits = sorted(fab_dir.glob(pat))
        if hits:
            return hits[0]
    return None


def collect_package(fab_dir: Path) -> tuple[dict, list[str]]:
    """Locate + hash the deliverables. -> (artifacts, missing)."""
    artifacts: dict = {}
    missing: list[str] = []

    zip_path = _find(fab_dir, "*_gerbers.zip", "*.zip")
    if zip_path is None:
        missing.append("gerber zip")
    else:
        # sha256 = "is this the exact file I quoted"; design_sha256 =
        # "is this the same DESIGN" (headers/timestamps stripped). Only the
        # second survives a re-export (LEARNINGS [fab_export][jlcapi]).
        artifacts["gerber_zip"] = {"path": str(zip_path),
                                   "sha256": sha256(zip_path),
                                   "design_sha256": fabhash.design_hash(zip_path),
                                   "bytes": zip_path.stat().st_size}
    for label, pats in (("bom", ("BOM.csv", "*BOM*.csv")),
                        ("cpl", ("CPL.csv", "*CPL*.csv", "*-pos.csv"))):
        p = _find(fab_dir, *pats)
        if p is None:
            missing.append(f"{label.upper()}.csv")
        else:
            artifacts[label] = {"path": str(p), "sha256": sha256(p),
                                "bytes": p.stat().st_size}
    return artifacts, missing


def _api_available() -> tuple[bool, str]:
    missing = [v for v in API_ENV if not jlcapi.cred_env(v)]
    if not missing:
        return True, "credentials present"
    return False, ("JLCPCB Open API credentials incomplete - missing "
                   f"{' and '.join(missing)}. HWDE_JLCPCB_KEY/"
                   "HWDE_JLCPCB_SECRET come from the api.jlcpcb.com console "
                   "API key page (may already be set as user env vars); "
                   "HWDE_JLCPCB_APPID is the console application's App ID. "
                   "Service scopes also need portal approval (Permission "
                   "Setting)")


# ------------------------------------------------------------------ API legs

def _make_session():
    """Split out so tests can monkeypatch a mock session in."""
    return jlcapi.session_from_env()


def _num_str(v) -> str:
    return v if isinstance(v, str) else str(v)


def _to_num(v):
    """float(v) or None - shipList costs arrive as strings ("20.00")."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _known_stackups() -> dict:
    """{name: outer copper oz} from reference/stackups.yaml (retired entries
    included - a shipped board may have been built on one)."""
    try:
        import yaml
        doc = yaml.safe_load(
            (SCRIPTS.parent / "reference" / "stackups.yaml")
            .read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - reference data is advisory here
        return {}
    out = {}
    for name, s in ((doc or {}).get("stackups") or {}).items():
        coppers = [ly for ly in s.get("stack", []) if ly.get("type") == "copper"]
        if coppers:
            out[name] = float(coppers[0].get("copper_oz", 1.0))
    return out


def derive_copper_oz(workspace: Path) -> tuple[float | None, str]:
    """Outer copper weight (oz) from architecture/stackup.md's `Chosen`
    section. -> (oz, source_note); **oz is None when it cannot be derived**
    and the caller must REFUSE rather than guess.

    Copper weight has no producer in spec_snapshot/quote.json, so this doc is
    the single source of truth for it - and it is a board-killer: quoting a
    2 oz design at 1 oz builds the wrong board. The old version defaulted to
    1 oz on any parse miss (and blamed a missing file for it), which defeats
    _check_oz_mentions silently and by default (LEARNINGS 2026-07-30
    [order_submit][stackup]).

    Resolution order, over the Chosen heading AND the lines under it (real
    docs put the id on its own line below a numbered heading):
      1. an explicit oz marker in a stackup id (JLC2313_1.6_2oz -> 2)
      2. a stackup id known to reference/stackups.yaml -> its outer copper
      3. nothing -> (None, why) so the caller refuses
    """
    sm = workspace / "architecture" / "stackup.md"
    if not sm.exists():
        return None, ("no architecture/stackup.md - copper weight cannot be "
                      "derived")
    lines = sm.read_text(encoding="utf-8", errors="replace").splitlines()
    start = None
    for i, line in enumerate(lines):
        # "Chosen" ANYWHERE in a heading, not just "## Chosen:" - real docs
        # number their headings ("## 1. Chosen stackup").
        s = line.lstrip()
        if s.startswith("#") and "chosen" in s.lower():
            start = i
            break
    if start is None:
        return None, ("architecture/stackup.md is present but has NO 'Chosen' "
                      "heading (this is not the same as a missing file)")
    window = [lines[start]]
    for line in lines[start + 1:start + 21]:
        if line.lstrip().startswith("#"):
            break
        window.append(line)
    text = "\n".join(window)
    m = _OZ_ID_RE.search(text)
    if m:
        return float(m.group(1)), f"stackup.md: {lines[start].strip()} ({m.group(0).strip()})"
    for name, oz in sorted(_known_stackups().items(), key=lambda kv: -len(kv[0])):
        if name in text:
            return oz, f"stackups.yaml[{name}].stack[0].copper_oz"
    return None, ("architecture/stackup.md 'Chosen' section names no stackup "
                  "known to reference/stackups.yaml and carries no oz marker")


def _check_oz_mentions(man: dict, prior_steps, oz: float,
                       waived: bool = False) -> None:
    """Refuse the quote when order notes demand a heavier copper than the
    derived value (the pd-trigger board-killer: human_steps say '2oz copper
    MUST be selected' while the API quote would go out at 1 oz). With
    waived=True (explicit --copper-oz human override) the mismatch is
    recorded as a permanent waiver note instead of refusing - the guard's
    job is to prevent SILENT downgrades, not human decisions."""
    steps = list(man.get("human_steps") or []) + list(prior_steps or [])
    mentioned = [float(m.group(1)) for s in steps
                 for m in _OZ_MENTION_RE.finditer(str(s))]
    if mentioned and max(mentioned) > float(oz):
        if waived:
            note = (f"COPPER WAIVER: ordered at {oz:g} oz despite the "
                    f"{max(mentioned):g} oz design note (explicit "
                    "--copper-oz human decision) - current paths sized for "
                    f"{max(mentioned):g} oz are derated on this rev")
            if not any("COPPER WAIVER:" in str(s)
                       for s in man.get("human_steps") or []):
                man["human_steps"].insert(0, note)
            return
        raise ApiRefused(
            f"copper-weight mismatch: order notes mention {max(mentioned):g} "
            f"oz but the derived copperWeight is {oz:g} - quoting at the "
            "wrong copper weight is a board-killer. Fix architecture/"
            "stackup.md (`## Chosen:` id) or the order note so they agree, "
            "or pass an explicit --copper-oz to waive")


def build_pcb_param(spec: dict) -> dict:
    """calculate/create pcbParam per the hendley api-reference
    PcbOrderCraftData table (the contract's [SDK/PDF] source). Key names
    layer/width/length/qty/thickness come from that table - the earlier
    stencil* guess belonged to the impedance-template request only, and is
    gone. Every Required=yes field is present; each non-obvious default
    cites its source row/example in that doc."""
    layers, qty = spec.get("layers"), spec.get("qty")
    if not layers or not qty:
        raise ApiRefused("spec snapshot lacks layers/qty - pass --quote "
                         "(order_quote.py output) and --qty")
    width, height = spec.get("width_mm"), spec.get("height_mm")
    if not width or not height:
        # refuse LOCALLY instead of sending "width": null and burning the
        # round-trip on a guaranteed param rejection
        raise ApiRefused(
            "spec snapshot lacks width_mm/height_mm - board dimensions are "
            "required by calculate/create; pass --quote (order_quote.py "
            "output measures the outline)")
    oz = spec.get("copper_weight_oz") or 1
    finish = str(spec.get("surface_finish") or "HASL").upper()
    color = str(spec.get("solder_mask_color") or "green").lower()
    surface = SURFACE_FINISH_CODE.get(finish, 1)
    param = {
        "layer": int(layers),                 # table: "layer - PCB Layer"
        "width": width,                       # table: width (mm)
        "length": height,                     # table: length (mm)
        "qty": int(qty),
        "thickness": spec.get("thickness_mm") or 1.6,
        "pcbColor": PCB_COLOR_CODE.get(color, 0),
        "surfaceFinish": surface,
        # string form per the create example ("copperWeight": "2")
        "copperWeight": "%g" % float(oz),
        "goldFinger": 0,                      # enum: 0 = not required
        "materialDetails": 0,                 # enum: 0 = FR4 Standard Tg140
        "panelFlag": 0,                       # enum: 0 = single PCB
        "panelByJLCPCB_X": 0,                 # calculate example: 0 when
        "panelByJLCPCB_Y": 0,                 #   panelFlag = 0
        "differentDesign": 1,                 # table: "default value is 1"
        "flyingProbeTest": 2,                 # create example: 2 = 100% test
        # enum: 0 = none, 1 = castellated process. Set from the BOARD's own
        # castellated pads (spec_snapshot.castellated_pads, counted by
        # lib/castellation.py), never from a brief: a board without them
        # must not buy the process, and one with them must not ship as
        # plain holes the fab may leave unplated. The 1 has not yet been
        # through a live calculate (no castellated board has been quoted).
        "castellatedHoles": 1 if spec.get("castellated_pads") else 0,
        "orderDetailsRemark": "",             # required String; no remarks
        # calculate-example value; the 0/1/2 enum is undescribed in the doc
        "cascadeStructure": 1,
        "impedanceFlag": "no",                # calculate example
        # isAddCustomerCode/markOnPcb/autoConfirmProductionFile deliberately
        # OMITTED: the doc's calculate example omits them and the live
        # endpoint rejects the "Yes"+2 pairing with code 2708 "The Remove
        # Order Number error" (2026-07-29). They are create-side options;
        # decide them at the first gated live create.
        "plateType": 1,                       # enum: 1 = FR-4
        "viaCovering": 1,                     # enum: 1 = tented (JLC default)
        "needTechnics": 0,                    # table: "default value is 0"
        "edgeRounding": False,                # no edge rounding
        # calculate-example pair, verbatim
        "serviceConfigVos": [
            {"serviceConfigCode": "PPBP",
             "serviceConfigShow": "Paper between PCBs",
             "configOptionShow": "No"},
            {"serviceConfigCode": "CPF",
             "serviceConfigShow": "Confirm Production file",
             "configOptionShow": "No"},
        ],
    }
    if surface == 2:
        # required when surfaceFinish is 2; create-example ENIG thickness
        param["goldThickness"] = 2
    if int(layers) >= 4:
        # inner copper weight is a 4+ layer selection ONLY - live calculate
        # rejects it on 2L with code 2129 "Only boards with four layers or
        # more support the selection of inner copper weight" (2026-07-29).
        #
        # This was previously hardcoded to the create-example's "1", i.e. 1 oz
        # inner copper, on EVERY 4-layer board. That is wrong twice over: JLC's
        # standard 4-layer inner copper is 0.5 oz, so it silently bought a
        # premium (measured: insideCuprumThicknessFee $17.07, 48% of the PCB
        # cost on lumina-carrier) - and, far worse, it fabricates a different
        # stackup than the one the impedance was solved against. lumina-carrier
        # targets 100 ohm differential MDI on JLC04161H-3313, whose inner layers
        # are 17.5 um / 0.5 oz; ordering 35 um inner copper moves the reference
        # spacing and the controlled impedance with it.
        inner = spec.get("inner_copper_weight_oz")
        if inner is None:
            inner = 0.5          # JLC standard 4-layer inner copper
        param["insideCuprumThickness"] = "%g" % float(inner)
    return param


def _record_api_failure(man: dict, cls: str, resp: dict) -> None:
    note = f"{cls}: {resp.get('message')} (http {resp.get('http_status')}, " \
           f"code {resp.get('code')}, trace {resp.get('trace_id')})"
    remedy = jlcapi.REMEDIATION.get(cls)
    if remedy:
        note += f" - {remedy}"
    man["api"].update({"verdict": cls, "note": note})


def _scope_note(man: dict) -> None:
    note = ("JLCPCB API scope approval pending (signing verified) - "
            "quote via the web flow meanwhile; re-run --api once the "
            "portal shows the PCB service approved.")
    if note not in man["human_steps"]:
        man["human_steps"].insert(0, note)


def _api_quote(session, man: dict, fab_dir: Path, prior_steps=None,
               country: str | None = None,
               ship_method: str | None = None,
               copper_oz: float | None = None) -> str:
    """uploadGerber -> audit -> calculate -> fab/api_quote.json. NEVER calls
    create. Returns the verdict recorded in man['api']."""
    zip_info = man["artifacts"].get("gerber_zip")
    if not zip_info:
        man["api"].update({"verdict": "skipped",
                           "note": "gerber zip missing - run fab_export "
                                   "first; API quote skipped"})
        return "skipped"

    # copper weight: on a governed workspace the ATTESTED manufacturing
    # options are law (U5/C1 - the pd-trigger 1 oz case: a human override
    # that contradicts the attested value REFUSES instead of waiving);
    # ungoverned: explicit human override > spec > stackup.md; a note
    # mismatch refuses BEFORE any network traffic unless the override
    # waives it
    spec = _merged_spec(man)
    attested = ((man.get("release") or {}).get("manufacturing")
                if (man.get("release") or {}).get("valid") else None)
    if attested is not None:
        att_oz = attested.get("copper_weight_oz")
        if att_oz is None:
            raise ApiRefused("attestation carries no copper_weight_oz - "
                             "re-run attest.py build")
        if copper_oz is not None and float(copper_oz) != float(att_oz):
            raise ApiRefused(
                f"--copper-oz {float(copper_oz):g} contradicts the ATTESTED "
                f"copper weight {float(att_oz):g} - a manufacturing-option "
                "override invalidates the release (the pd-trigger 1 oz "
                "case). Change architecture/stackup.md and re-attest "
                "instead of overriding at order time")
        if spec.get("copper_weight_oz") \
                and float(spec["copper_weight_oz"]) != float(att_oz):
            raise ApiRefused(
                f"quote spec copper_weight_oz {spec['copper_weight_oz']!r} "
                f"contradicts the attested {float(att_oz):g} - regenerate "
                "the quote or re-attest so they agree")
        # attested options are FORCED into the priced spec, not merely
        # cross-checked: a spec that simply OMITS a value must not fall
        # back to a build_pcb_param default different from the attested
        # one (U5 review: 'None-attested disables guard' - and its dual,
        # None-spec dodges the check)
        for key in ("layers", "thickness_mm", "surface_finish",
                    "solder_mask_color", "inner_copper_weight_oz"):
            av, sv = attested.get(key), spec.get(key)
            if av is None:
                continue
            if sv is not None and str(sv) != str(av):
                raise ApiRefused(
                    f"quote spec {key} {sv!r} contradicts the attested "
                    f"{av!r} - manufacturing options come from the "
                    "attestation only")
            spec[key] = av
        oz, oz_source = float(att_oz), "release attestation"
        spec["copper_weight_oz"] = oz
        _check_oz_mentions(man, prior_steps, oz, waived=False)
    else:
        if copper_oz is not None:
            oz, oz_source = float(copper_oz), "human override (--copper-oz)"
            spec["copper_weight_oz"] = oz
        elif spec.get("copper_weight_oz"):
            oz, oz_source = float(spec["copper_weight_oz"]), "spec snapshot"
        else:
            oz, oz_source = derive_copper_oz(fab_dir.parent)
            if oz is None:
                # never guess a board-killer parameter
                raise ApiRefused(
                    f"copper weight cannot be derived: {oz_source}. Fix the "
                    "`Chosen` section of architecture/stackup.md (name a "
                    "stackup from reference/stackups.yaml, or carry an "
                    "explicit id marker like JLC2313_1.6_2oz) or pass "
                    "--copper-oz")
            spec["copper_weight_oz"] = oz
        _check_oz_mentions(man, prior_steps, oz, waived=copper_oz is not None)
    pcb_param = build_pcb_param(spec)

    if (man["api"].get("file_key")
            and man["api"].get("file_key_sha256") == zip_info["sha256"]):
        # same bytes already uploaded: reuse the key - also keeps JLC's
        # async DFM-audit clock running instead of restarting it every run
        file_key = man["api"]["file_key"]
    else:
        up = session.upload_gerber(zip_info["path"])
        cls = jlcapi.classify(up)
        if cls != "ok":
            _record_api_failure(man, cls, up)
            if cls == "scope_pending":
                _scope_note(man)
            return cls
        file_key = jlcapi.extract_file_key(up)
        if not file_key:
            man["api"].update({"verdict": "error",
                               "note": "uploadGerber succeeded but returned "
                                       f"no fileKey (data={up.get('data')!r})"
                               })
            return "error"
        # record immediately: a later run can reuse the key even if the
        # next calls fail on scope
        man["api"]["file_key"] = file_key
        man["api"]["file_key_sha256"] = zip_info["sha256"]

    aud = session.audit(file_key)
    aud_cls = jlcapi.classify(aud)
    attempts = 1
    for delay in AUDIT_POLL_DELAYS_S:
        if aud_cls == "scope_pending" \
                or aud.get("code") != AUDIT_PENDING_CODE:
            break
        _sleep(delay)
        aud = session.audit(file_key)
        aud_cls = jlcapi.classify(aud)
        attempts += 1
    if aud_cls == "scope_pending":
        _record_api_failure(man, aud_cls, aud)
        _scope_note(man)
        return aud_cls
    audit_summary = {"ok": aud.get("ok"), "code": aud.get("code"),
                     "message": aud.get("message"), "data": aud.get("data"),
                     "attempts": attempts}
    if aud.get("code") == AUDIT_PENDING_CODE:
        # JLC's DFM is still running; the quote proceeds and a later --api
        # run re-polls (the fileKey is reused while the zip is unchanged)
        audit_summary["pending"] = True

    calc_request = {"orderType": 1, "fileKey": file_key,
                    # required by the calculate table; value verbatim from
                    # the documented request example
                    "achieveDate": 120,
                    "pcbParam": pcb_param}
    if country:
        # country code per the doc table (e.g. "US", "NL") - without it
        # calculate returns no shipList, so the grand-total token could
        # not attest freight
        calc_request["country"] = country
    calc = session.calculate(calc_request)
    cls = jlcapi.classify(calc)
    if cls != "ok":
        _record_api_failure(man, cls, calc)
        if cls == "scope_pending":
            _scope_note(man)
        return cls
    data = calc.get("data") if isinstance(calc.get("data"), dict) else {}
    real_price = data.get("priceWithoutFreight")
    # like-for-like H5 comparison: the Open API prices bare PCBs ONLY (no
    # assembly surface exists), so an assembly-inclusive quote row must
    # contribute its PCB sub-total, never PCB+assembly - comparing the
    # combined total against the API's PCB price told the human the wrong
    # DIRECTION of error on both credentialed runs.
    sel = man["quote"].get("selected") or {}
    if "assembly" in sel:
        estimate = (sel.get("pcb") or {}).get("total")
        estimate_scope = "pcb_only"
        estimate_assembly_excluded = (sel.get("assembly") or {}).get("total")
    else:
        estimate = sel.get("total")
        estimate_scope = "total"
        estimate_assembly_excluded = None
    ship_list = data.get("shipList")
    shipping_method = None
    shipping_cost = None
    if isinstance(ship_list, list) and ship_list \
            and isinstance(ship_list[0], dict):
        chosen = ship_list[0]
        if ship_method:
            want = ship_method.strip().lower()
            chosen = next(
                (s for s in ship_list if isinstance(s, dict)
                 and (str(s.get("options", "")).lower() == want
                      or str(s.get("showOptions", "")).lower() == want)),
                None)
            if chosen is None:
                names = ", ".join(
                    f"{s.get('showOptions')} ({s.get('options')})"
                    for s in ship_list if isinstance(s, dict))
                raise ApiRefused(
                    f"--ship-method {ship_method!r} is not in the quoted "
                    f"shipList; available: {names}")
        shipping_method = chosen.get("options")
        shipping_cost = _to_num(chosen.get("cost"))
    # the confirm token attests the GRAND total (pcb + selected freight) so
    # freight cannot ride outside the human's attestation
    rp = _to_num(real_price)
    grand_total = (round(rp + (shipping_cost or 0.0), 2)
                   if rp is not None else None)

    api_quote = {
        "script": "order_submit",
        "kind": "api_quote",
        "status": "pass",
        "board": Path(man["board"]).stem,
        "qty": man["spec_snapshot"].get("qty"),
        "fetched_at": _dt.datetime.now().astimezone()
        .isoformat(timespec="seconds"),
        "file_key": file_key,
        "gerber_sha256": zip_info["sha256"],   # exact file identity (advisory)
        # binds any create to this DESIGN: a re-export of the same board
        # changes the file sha and must not invalidate an approved quote
        "design_sha256": zip_info["design_sha256"],
        "country": country,
        "real_price": real_price,
        "estimate": estimate,
        "estimate_scope": estimate_scope,
        "estimate_assembly_excluded": estimate_assembly_excluded,
        "estimate_note": man["quote"].get("estimate_note"),
        "estimate_source": man["quote"].get("source"),
        "copper_weight_oz": oz,
        "copper_weight_source": oz_source,
        "audit": audit_summary,
        "pcb_cost_info": data.get("pcbCostInfo"),
        "ship_list": ship_list,
        "shipping_method": shipping_method,    # first shipList option; REVIEW
        "shipping_cost": shipping_cost,
        "grand_total": grand_total,            # real_price + freight
        "achieve_date_list": data.get("achieveDateList"),
        "order_total_weight": data.get("orderTotalWeight"),
        "calculate_request": calc_request,
        "trace_id": calc.get("trace_id"),
        "confirm_format": "<board> <qty>pcs <grand_total>",
    }
    out = fab_dir / "api_quote.json"
    safelib.atomic_write_json(out, api_quote)

    if estimate_scope == "pcb_only":
        est_txt = (f"our PCB-only estimate {estimate}; assembly estimated "
                   f"separately at {estimate_assembly_excluded} - the Open "
                   "API prices bare PCBs only")
    else:
        est_txt = f"our estimate {estimate}"
    man["api"].update({
        "verdict": "ok",
        "quote_real": real_price,
        "api_quote_json": str(out),
        "note": f"API quote fetched: grand total {grand_total} "
                f"({real_price} pcb + {shipping_cost} freight) vs {est_txt}",
    })
    man["api"].pop("quote_stale", None)        # this quote is fresh
    man["human_steps"].insert(-1, (
        f"API quote in {out}: REAL grand total {grand_total} ({real_price} "
        f"pcb + {shipping_cost} freight via {shipping_method}; {est_txt}) "
        "- review shipList/achieveDateList/audit findings "
        "before any create."))
    return "ok"


def _load_fresh_quote(path: Path) -> dict:
    try:
        q = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiRefused(f"cannot read api quote {path}: {exc}") from exc
    for key in ("board", "qty", "real_price", "grand_total", "file_key",
                "gerber_sha256", "design_sha256", "calculate_request",
                "fetched_at"):
        if q.get(key) in (None, ""):
            raise ApiRefused(f"api quote {path} lacks {key} - re-run --api")
    try:
        fetched = _dt.datetime.fromisoformat(q["fetched_at"])
    except (TypeError, ValueError) as exc:
        raise ApiRefused(f"api quote fetched_at unparseable "
                         f"({q['fetched_at']!r}): {exc}") from exc
    now = _dt.datetime.now(fetched.tzinfo)
    age_s = (now - fetched).total_seconds()
    if age_s < -API_QUOTE_MAX_SKEW_S:
        raise ApiRefused(f"api quote fetched_at is {-age_s:.0f} s in the "
                         "FUTURE - clock skew; fix the clock and re-run "
                         "--api")
    if age_s > API_QUOTE_MAX_AGE_H * 3600.0:
        raise ApiRefused(f"api quote is {age_s / 3600.0:.1f} h old (max "
                         f"{API_QUOTE_MAX_AGE_H:.0f} h) - prices move; "
                         "re-run --api for a fresh quote")
    return q


# create_attempt states: only these prove no order is in doubt (created is
# additionally caught by the created-latch); every other value refuses.
UNAMBIGUOUS_ATTEMPT_STATES = frozenset((
    "created", "failed:bad_signature", "failed:scope_pending",
    "failed:ip_blocked", "failed:rate_limited"))
AMBIGUOUS_ATTEMPT_STATES = frozenset((
    "in_flight", "failed:unknown_error", "failed:error"))


def _api_create(session, man: dict, quote_file: Path, confirm: str,
                ship_json: Path | None,
                canonical: Path | None = None,
                journal: Path | None = None) -> str:
    """REAL MONEY. The only caller of session.create_order, reachable only
    past the created-latch, the ambiguous-attempt block, a fresh quote file,
    the gerber sha binding, the freight attestation, the qty cross-check and
    an exactly-matching grand-total confirm token."""
    # created-latch: one recorded order per workspace, period. Armed by
    # recorded API ids, a prior "created" verdict (a create that returned ok
    # without ids must still latch, round-3 S-2 defense-in-depth), OR a
    # recorded order_number - a WEB order registered via --order-number is an
    # order too, and API-buying the same board again is the double-buy.
    prior_order = man["api"].get("order") or {}
    if (prior_order.get("batchNum") or prior_order.get("orderId")
            or man["api"].get("verdict") == "created"
            or man.get("order_number")):
        raise ApiRefused(
            f"an order is already recorded for this workspace (api batchNum "
            f"{prior_order.get('batchNum')}, orderId "
            f"{prior_order.get('orderId')}, order_number "
            f"{man.get('order_number')}) - refusing to create again. To "
            "intentionally reorder, manually clear the api.order block AND "
            "the top-level order_number from fab/order.json first")

    # ambiguous-attempt block: a prior create that is still in_flight (crash
    # or transport failure mid-create) or failed ambiguously (unknown_error /
    # error: JLC answered but nothing says whether an order landed, and no
    # order list/search endpoint exists to ask) MUST NOT be retried
    # mechanically - that is the exact case that buys a board twice.
    # Clean pre-transport refusals never write an attempt record, and
    # unambiguous rejections (bad_signature/scope_pending/ip_blocked/
    # rate_limited: the order definitely did not land) do not block a retry.
    # A WHITELIST, not a blacklist: a record with a missing or unrecognised
    # state (hand-edited, truncated, written by a newer/older script) says
    # nothing about whether an order landed, so it refuses like in_flight.
    has_attempt = man["api"].get("create_attempt") is not None
    attempt = man["api"].get("create_attempt") or {}
    raw_state = attempt.get("state")
    astate = raw_state if isinstance(raw_state, str) else ""
    if has_attempt and astate not in UNAMBIGUOUS_ATTEMPT_STATES \
            and astate not in AMBIGUOUS_ATTEMPT_STATES:
        raise ApiRefused(
            f"a prior create attempt (at {attempt.get('at')}, grand_total "
            f"{attempt.get('grand_total')}) has a missing or unrecognised "
            f"state {raw_state!r} - refusing, because only "
            f"{sorted(UNAMBIGUOUS_ATTEMPT_STATES)} prove no order is in "
            "doubt. Verify orders + balance in the JLCPCB web portal FIRST; "
            "only after confirming no order landed, manually remove the "
            "api.create_attempt block from fab/order.json")
    if has_attempt and astate in AMBIGUOUS_ATTEMPT_STATES:
        raise ApiRefused(
            f"a prior create attempt (at {attempt.get('at')}, grand_total "
            f"{attempt.get('grand_total')}) ended ambiguously (state "
            f"{astate!r}) - an order MAY exist server-side and the API has "
            "no order list/search endpoint to check. Verify orders + balance "
            "in the JLCPCB web portal FIRST; only after confirming no order "
            "landed, manually remove the api.create_attempt block from "
            "fab/order.json to clear this refusal")

    q = _load_fresh_quote(quote_file)

    # layer guard: 4+ layer create is refused by JLC itself with an
    # unclassifiable code 2, and an ambiguous create is unobservable (no
    # order list/search endpoint exists) - so never send one.
    param_layers = (q["calculate_request"].get("pcbParam") or {}).get("layer")
    layers = param_layers or _merged_spec(man).get("layers")
    if layers and int(layers) > API_CREATE_MAX_LAYERS:
        raise ApiRefused(
            f"{int(layers)}-layer boards cannot be ordered through the Open "
            "API: pcb/create returns HTTP 200 {code 2, unknown_error} for "
            "every 4-layer payload (three live attempts, 2026-07-30) while "
            "the identical 2-layer payload succeeds, and there is no "
            "order list/search endpoint to tell you afterwards whether an "
            "ambiguous create landed. Order this board through the web cart "
            f"({JLC_QUOTE_URL}) with the same gerber zip - the API quote in "
            "this workspace is still the price reference - and record the "
            "web order number with --order-number")

    # design binding: the board on disk must be the BOARD that was quoted.
    # Bound to the normalized design hash, NOT the file sha: KiCad stamps a
    # creation timestamp into every gerber, so a re-export of an unchanged
    # board changes the sha and used to self-invalidate an approved quote
    # (LEARNINGS 2026-07-30 [fab_export][order_submit][jlcapi]).
    zip_info = man["artifacts"].get("gerber_zip")
    if not zip_info:
        raise ApiRefused("gerber zip missing from the fab dir - cannot bind "
                         "the create to the quoted gerbers")
    if zip_info["design_sha256"] != q["design_sha256"]:
        raise ApiRefused(
            "the design changed since the quote (design hash mismatch: quote "
            f"{q['design_sha256'][:12]}.. vs current "
            f"{zip_info['design_sha256'][:12]}..; this ignores export "
            "timestamps, so the copper really differs) - re-run --api to "
            "re-quote the new gerbers")

    # U5 (codex C1): on a governed workspace the create must also match the
    # ATTESTED package + manufacturing options - the quote binding alone
    # would let an attestation-invalidating re-quote slip through.
    release = man.get("release") or {}
    if release.get("governed"):
        if not release.get("valid"):
            raise ApiRefused(
                "release attestation missing or invalid - run attest.py "
                "build (problems: "
                + "; ".join((release.get("problems") or ["unknown"])[:6])
                + ")")
        att_design = release.get("fab_design_sha256")
        if att_design and zip_info["design_sha256"] != att_design:
            raise ApiRefused(
                "the package on disk is not the ATTESTED design (design "
                f"hash {zip_info['design_sha256'][:12]}.. vs attested "
                f"{att_design[:12]}..) - re-run attest.py build after "
                "regenerating the fab package")
        # check the PAYLOAD's pcbParam - the fields JLC actually receives -
        # not the quote file's advisory top-level copy (U5 review: 'only
        # copper checked, wrong field'). Every attested option must match
        # what is about to be fabricated.
        att_man = release.get("manufacturing") or {}
        pparam = q["calculate_request"].get("pcbParam") or {}
        checks = []
        if att_man.get("copper_weight_oz") is not None:
            checks.append(("copperWeight",
                           "%g" % float(att_man["copper_weight_oz"]),
                           str(pparam.get("copperWeight"))))
        if att_man.get("inner_copper_weight_oz") is not None \
                and "insideCuprumThickness" in pparam:
            checks.append(("insideCuprumThickness",
                           "%g" % float(att_man["inner_copper_weight_oz"]),
                           str(pparam.get("insideCuprumThickness"))))
        if att_man.get("layers") is not None:
            checks.append(("layer", int(att_man["layers"]),
                           pparam.get("layer")))
        if att_man.get("thickness_mm") is not None:
            checks.append(("thickness", float(att_man["thickness_mm"]),
                           _to_num(pparam.get("thickness"))))
        if att_man.get("surface_finish") is not None:
            checks.append((
                "surfaceFinish",
                SURFACE_FINISH_CODE.get(
                    str(att_man["surface_finish"]).upper(), 1),
                pparam.get("surfaceFinish")))
        if att_man.get("solder_mask_color") is not None:
            checks.append((
                "pcbColor",
                PCB_COLOR_CODE.get(
                    str(att_man["solder_mask_color"]).lower(), 0),
                pparam.get("pcbColor")))
        for field, want, got in checks:
            if got != want:
                raise ApiRefused(
                    f"create payload pcbParam.{field} is {got!r} but the "
                    f"attestation binds {want!r} - a manufacturing-option "
                    "override invalidates the release; re-quote after "
                    "re-attesting")

    # freight attestation: the shipping method must be one of the QUOTED
    # options, its recorded cost must match that option, and the grand
    # total the token attests must equal real_price + that freight
    method = q.get("shipping_method")
    freight = _to_num(q.get("shipping_cost")) or 0.0
    if method is not None:
        ship_list = q.get("ship_list") if isinstance(q.get("ship_list"),
                                                     list) else []
        quoted = {s.get("options"): _to_num(s.get("cost"))
                  for s in ship_list if isinstance(s, dict)}
        if method not in quoted:
            raise ApiRefused(
                f"shipping_method {method!r} is not among the quoted "
                f"shipList options {sorted(k for k in quoted if k)} - "
                "re-run --api and choose a quoted option")
        if quoted[method] is None or abs(quoted[method] - freight) > 0.005:
            raise ApiRefused(
                f"shipList cost for {method!r} ({quoted[method]!r}) does "
                f"not match the attested freight ({q.get('shipping_cost')!r})"
                " - the quote record drifted; re-run --api")
    rp = _to_num(q.get("real_price"))
    gt = _to_num(q.get("grand_total"))
    if rp is None or gt is None or abs(rp + freight - gt) > 0.005:
        raise ApiRefused(
            "grand_total does not equal real_price + freight in the api "
            "quote - inconsistent record; re-run --api")

    # the attested qty must equal the qty the calculate request priced
    param_qty = (q["calculate_request"].get("pcbParam") or {}).get("qty")
    if param_qty is None or int(param_qty) != int(q["qty"]):
        raise ApiRefused(
            f"attested qty {q['qty']} does not match the calculated "
            f"pcbParam.qty {param_qty!r} - inconsistent quote record; "
            "re-run --api")

    expected = f"{q['board']} {q['qty']}pcs {_num_str(q['grand_total'])}"
    if (confirm or "").strip() != expected:
        raise ApiRefused(
            'confirm token does not match the quote - pass --confirm '
            '"<board> <qty>pcs <grand_total>" using the exact board, qty '
            f"and grand_total (pcb + freight) recorded in {quote_file}")

    payload = {"orderType": 1, "fileKey": q["file_key"],
               "pcbParam": q["calculate_request"].get("pcbParam")}
    if q.get("shipping_method"):
        payload["shippingMethod"] = q["shipping_method"]
    if ship_json:
        try:
            extra = json.loads(Path(ship_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiRefused(f"cannot read --ship-json: {exc}") from exc
        if not isinstance(extra, dict):
            raise ApiRefused("--ship-json must contain a JSON object")
        offenders = sorted(set(extra) - SHIP_JSON_KEYS)
        if offenders:
            raise ApiRefused(
                f"--ship-json contains non-shipping keys {offenders} - "
                f"allowed keys: {sorted(SHIP_JSON_KEYS)}; quote-bound "
                "fields (fileKey/pcbParam/orderType/...) are not "
                "overridable")
        payload.update(extra)

    # PRE-ARM the attempt record ON DISK before any money can move: if the
    # process dies (or the transport fails) mid-create, the next --api-create
    # finds state "in_flight" and refuses until a human checks the portal.
    # This is the fail-safe direction - a stale in_flight record after a
    # success also refuses, which a portal check clears.
    attempt = {"at": _dt.datetime.now().astimezone()
               .isoformat(timespec="seconds"),
               "state": "in_flight", "grand_total": q["grand_total"],
               "pid": os.getpid()}
    man["api"]["create_attempt"] = attempt
    if canonical is None:
        # fallback: derive the canonical fab/order.json from the gerber zip
        canonical = Path(zip_info["path"]).parent / "order.json"
    if journal is None:
        journal = canonical.parent / JOURNAL_NAME
    safelib.append_journal(journal, {
        "event": "in_flight", "grand_total": q["grand_total"],
        "board": man.get("board"), "qty": q.get("qty")})
    safelib.atomic_write_json(canonical, man)   # fsync'd, unique temp
    safelib.fault("order.pre_armed", canonical=canonical)

    resp = session.create_order(payload)
    cls = jlcapi.classify(resp)
    attempt["state"] = "created" if cls == "ok" else f"failed:{cls}"
    if cls != "ok":
        _record_api_failure(man, cls, resp)
        safelib.append_journal(journal, {
            "event": f"failed:{cls}", "message": resp.get("message"),
            "trace_id": resp.get("trace_id")})
        raise ApiRefused(f"create rejected ({cls}): {resp.get('message')}",
                         recorded=True)
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    order = {"orderId": data.get("orderId"), "batchNum": data.get("batchNum"),
             "orderType": data.get("orderType"),
             "orderDate": data.get("orderDate")}
    man["api"].update({"verdict": "created", "order": order,
                       "note": "REAL ORDER PLACED via pcb/create - payment/"
                               "balance mechanics unverified, check the "
                               "JLCPCB portal"})
    if order.get("batchNum"):
        man["order_number"] = str(order["batchNum"])
    safelib.append_journal(journal, {
        "event": "created", "orderId": order.get("orderId"),
        "batchNum": order.get("batchNum"), "trace_id": resp.get("trace_id")})
    if not (order.get("orderId") or order.get("batchNum")):
        man["human_steps"].insert(0, (
            "WARNING: pcb/create returned ok WITHOUT orderId/batchNum - an "
            "order may exist server-side. Check the JLCPCB portal before ANY "
            "retry; the created-latch stays armed."))
    man["human_steps"].insert(0, (
        f"API order created: orderId {order.get('orderId')}, batchNum "
        f"{order.get('batchNum')}. Poll with order_track.py --workspace "
        "<ws>; verify payment state in the JLCPCB portal (may auto-deduct "
        "JLC Balance)."))
    return "created"


def _merged_spec(man: dict) -> dict:
    return {k: v for k, v in man["spec_snapshot"].items() if v is not None}


# --------------------------------------------------------------- release (U5)

def release_governance(fab_dir: Path) -> dict:
    """Codex C1: order-shaped code consumes ONLY the release attestation.

    A fab dir sitting in a stateful workspace (state.json beside it) is
    GOVERNED: ordering requires <ws>/fab/attestation.json to verify VALID
    (releaselib), and the attested manufacturing options become law for the
    API legs. A bare fab dir (no state.json) is ungoverned - the legacy
    manifest flow still works, flagged in the manifest, because there is no
    recorded state to attest against."""
    ws = Path(fab_dir).resolve().parent
    if not (ws / "state.json").is_file():
        # deliberate escape hatch for EXTERNAL one-off packages only (there
        # is no recorded state to attest against). A pipeline board escaped
        # here would show release.governed=false + the human_steps warning -
        # the human's tell that provenance was severed.
        return {"governed": False, "attested": False, "valid": False,
                "note": "no state.json beside the fab dir - ungoverned "
                        "package; pipeline workspaces are always governed"}
    try:
        v = releaselib.verify(ws)
    except Exception as exc:  # noqa: BLE001 - conservative: unverifiable
        return {"governed": True, "attested": False, "valid": False,
                "problems": [f"attestation unverifiable: "
                             f"{type(exc).__name__}: {exc}"]}
    out = {"governed": True, "attested": v.get("attested", False),
           "valid": bool(v.get("valid")),
           "problems": v.get("problems") or []}
    att = releaselib.load_attestation(ws)
    if att:
        out["attestation_sha256"] = att.get("attestation_sha256")
        out["manufacturing"] = att.get("manufacturing")
        out["fab_design_sha256"] = ((att.get("fab") or {})
                                    .get("gerber_zip") or {}).get(
                                        "design_sha256")
    try:
        out["disposition"] = releaselib.disposition(ws)["disposition"]
    except Exception:  # noqa: BLE001 - advisory field
        pass
    return out


def _load_prior_manifest(out_path: Path) -> dict | None:
    """Prior canonical manifest, or None when the file does NOT exist.

    A PRESENT-but-unparsable order.json raises: the created-latch and the
    ambiguous-attempt block live in this file, so treating corruption as
    'no prior order' would disarm the double-buy protections exactly when
    the record is least trustworthy (U5 adversarial review). Fail closed -
    a human repairs or intentionally removes the file first."""
    if not out_path.exists():
        return None
    try:
        prior = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiRefused(
            f"canonical {out_path} exists but is unreadable ({exc}) - it "
            "carries the created-latch, so refusing to proceed; repair or "
            "deliberately remove the file first") from exc
    if not isinstance(prior, dict):
        raise ApiRefused(
            f"canonical {out_path} is not a JSON object - it carries the "
            "created-latch, so refusing to proceed; repair the file first")
    # U12: a parsable file with the wrong SHAPE is just as untrustworthy -
    # a truncated/hand-edited latch is refused, never coerced or rewritten
    api = prior.get("api")
    problems = []
    if api is not None and not isinstance(api, dict):
        problems.append("api is not an object")
    else:
        for key in ("order", "create_attempt"):
            if api and key in api and api[key] is not None \
                    and not isinstance(api[key], dict):
                problems.append(f"api.{key} is not an object")
    if "human_steps" in prior and prior["human_steps"] is not None \
            and not isinstance(prior["human_steps"], list):
        problems.append("human_steps is not a list")
    if "order_number" in prior and prior["order_number"] is not None \
            and not isinstance(prior["order_number"], (str, int)):
        problems.append("order_number is not a string")
    if problems:
        raise ApiRefused(
            f"canonical {out_path} is structurally corrupt ("
            + "; ".join(problems) + ") - it carries the created-latch, so "
            "refusing to proceed; a human repairs or deliberately removes "
            "the file first (it is never auto-repaired)")
    return prior


def _merge_prior_api(man: dict, prior: dict | None) -> None:
    """Deep-merge the existing CANONICAL manifest into the fresh one: a
    re-run must never clobber a placed-order record, and hand-written
    human_steps (order-safety evidence like "BOARD-SPECIFIC: 2oz copper
    MUST be selected...") survive every rewrite - only lines this script
    itself generates are rebuilt. Preserved api keys lose only to values
    the CURRENT run's api legs set afterwards (.update())."""
    if not prior:
        return
    prior_api = prior.get("api")
    if isinstance(prior_api, dict):
        for key in ("verdict", "quote_real", "api_quote_json", "order",
                    "file_key", "file_key_sha256", "last_quote_verdict",
                    "last_create_verdict", "quote_stale", "create_attempt"):
            if key in prior_api and key not in man["api"]:
                man["api"][key] = prior_api[key]
    if man.get("order_number") is None and prior.get("order_number"):
        man["order_number"] = prior["order_number"]
    prior_steps = prior.get("human_steps")
    if isinstance(prior_steps, list):
        keep = [s for s in prior_steps if isinstance(s, str)
                and not s.startswith(_GENERATED_STEP_PREFIXES)]
        present = set(man["human_steps"])
        man["human_steps"][:0] = [s for s in keep if s not in present]


# ------------------------------------------------------------------ manifest

def run(pcb: Path, fab_dir: Path, quote: Path | None = None,
        qty: int | None = None, use_api: bool = False,
        order_number: str | None = None) -> dict:
    if not fab_dir.is_dir():
        raise FileNotFoundError(f"fab directory not found: {fab_dir}")
    artifacts, missing = collect_package(fab_dir)

    quote_row = None
    quote_data = None
    estimate_note = None
    if quote is not None and Path(quote).exists():
        quote_data = json.loads(Path(quote).read_text(encoding="utf-8"))
        rows = quote_data.get("matrix") or []
        if qty is not None:
            matched = [r for r in rows if r.get("qty") == qty]
            if matched:
                rows = matched
            elif rows:
                # never silently substitute another qty's estimate
                estimate_note = f"no matching estimate row (qty {qty})"
                rows = []
        if rows:
            quote_row = rows[0]
        elif estimate_note is None:
            quote_row = quote_data.get("cheapest")

    api_ok, api_note = _api_available()
    release = release_governance(fab_dir)
    if missing:
        status = "incomplete"
    elif release["governed"] and (not release["valid"]
                                  or release.get("disposition") == "blocked"):
        # U5 (codex C1): a governed workspace without a VALID attestation -
        # or with a blocked disposition (hold, ambiguous create, corrupt
        # record) - is not order-ready, no matter how fresh any gate is
        status = "not_order_ready"
    else:
        status = "ready_for_human"
    quote_spec = (quote_data or {}).get("spec", {})
    # the board decides; the quote's count only stands in when the board
    # file is not at hand
    n_cast = (castellation.count(pcb) if Path(pcb).is_file()
              else int(quote_spec.get("castellated_pads") or 0))

    manifest = {
        "script": "order_submit",
        "status": status,
        "board": pcb.name,
        "generated": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "order_number": order_number,
        "payment": "HUMAN - this script never submits payment (SPEC P10)",
        "artifacts": artifacts,
        "missing": missing,
        "quote": {
            "selected": quote_row,
            "estimate_note": estimate_note,
            "estimated": bool((quote_data or {}).get("estimated", True)),
            "source": str(quote) if quote else None,
            "authoritative_quote_url": JLC_QUOTE_URL,
        },
        "spec_snapshot": {
            "layers": quote_spec.get("layers"),
            "width_mm": quote_spec.get("width_mm"),
            "height_mm": quote_spec.get("height_mm"),
            "thickness_mm": quote_spec.get("thickness_mm"),
            "copper_weight_oz": quote_spec.get("copper_weight_oz"),
            "qty": qty or (quote_row or {}).get("qty"),
            "surface_finish": (quote_row or {}).get("surface_finish"),
            "solder_mask_color": (quote_row or {}).get("solder_mask_color"),
            "assembly": quote_spec.get("assembly"),
            "castellated_pads": n_cast,
        },
        "release": release,
        "api": {"attempted": bool(use_api), "available": api_ok,
                "note": api_note},
        "human_steps": [
            f"Upload {artifacts.get('gerber_zip', {}).get('path', '<gerber zip>')}"
            f" to {JLCDFM_URL} and review the DFM report (V6: no public API).",
            f"Upload the same zip at {JLC_QUOTE_URL} and confirm the real price"
            " against the estimate above.",
            "If assembling: upload BOM.csv and CPL.csv, then CHECK THE RENDERED"
            " PART PREVIEW for every polarized part (LED/diode/electrolytic) -"
            " rotation corrections are the classic failure mode.",
            "Review, then pay. Payment is always the human's action.",
        ],
    }
    if n_cast:
        manifest["human_steps"].insert(-1, (
            f"CASTELLATED: the board has {n_cast} castellated pad(s) - set "
            "'Castellated Holes: Yes' on the quote page (the API leg sends "
            "castellatedHoles 1) and confirm the surcharge."))
    if not release["governed"]:
        manifest["human_steps"].insert(0, (
            "UNGOVERNED package (no state.json beside the fab dir): no "
            "release attestation applies. External one-off boards only - a "
            "pipeline board ordered this way has severed its provenance."))
    return manifest


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--fab-dir", required=True)
    ap.add_argument("--quote", help="order_quote.py JSON")
    ap.add_argument("--qty", type=int, help="quantity row to select")
    ap.add_argument("--country",
                    help="ship-to country code for the freight quote "
                         "(e.g. US); without it calculate returns no "
                         "shipList and the grand-total token cannot carry "
                         "freight")
    ap.add_argument("--ship-method",
                    help="pick a quoted shipList option by its options or "
                         "showOptions name (case-insensitive; default: the "
                         "first quoted option); refuses if not quoted")
    ap.add_argument("--copper-oz", type=float,
                    help="EXPLICIT copper-weight override (e.g. 1); waives "
                         "the note-mismatch guard with a permanent COPPER "
                         "WAIVER human_steps note - current paths sized "
                         "for heavier copper are derated")
    ap.add_argument("--api", action="store_true",
                    help="live QUOTE-ONLY API flow (upload -> audit -> "
                         "calculate -> fab/api_quote.json); never creates "
                         "an order")
    ap.add_argument("--api-create", action="store_true",
                    help="REAL MONEY: place the order via pcb/create (no "
                         "sandbox exists); 2-layer boards only (JLC refuses "
                         "4+ with an unclassifiable code 2 - use the web "
                         "cart); requires --api-quote-file and a matching "
                         "--confirm token; refuses when order.json already "
                         "records ANY order (api or web) or an ambiguous "
                         "prior create attempt")
    ap.add_argument("--api-quote-file",
                    help="fresh api_quote.json from a --api run")
    ap.add_argument("--confirm",
                    help='exact "<board> <qty>pcs <total>" token matching '
                         "the api quote")
    ap.add_argument("--ship-json",
                    help="JSON object with ONLY shippingAddress / "
                         "billingAddress / taxOrVATNumber / "
                         "billingAddressFlag for the create payload")
    ap.add_argument("--order-number", help="record a placed order number")
    ap.add_argument("--lock-timeout", type=float, default=None,
                    help="seconds to wait for the order latch lock "
                         f"(<fab-dir>/order.json.lock; default "
                         f"{safelib.DEFAULT_LOCK_TIMEOUT:g}); a busy lock "
                         "refuses (exit 2) and is journaled")
    ap.add_argument("--out",
                    help="write an ADDITIONAL copy of the payload here; the "
                         "canonical <fab-dir>/order.json is always written "
                         "(and is the only record consulted for the "
                         "created-latch)")
    args = ap.parse_args(argv)

    fab_dir = Path(args.fab_dir)
    if not fab_dir.is_dir():
        print(json.dumps({"script": "order_submit", "status": "error",
                          "error": f"FileNotFoundError: fab directory not "
                                   f"found: {fab_dir}"}, indent=1))
        return 2
    # U12 order latch: ONE OS-exclusive hold across load -> check -> create
    # -> finalize. A second creator serializes behind it and then meets the
    # armed latch; a stuck holder makes this run refuse (journaled), never
    # proceed unlocked.
    canonical = fab_dir / "order.json"
    journal = fab_dir / JOURNAL_NAME
    mode = ("api_create" if args.api_create else "api_quote" if args.api
            else "manifest")
    try:
        with safelib.writer_lock(canonical, timeout=args.lock_timeout,
                                 what="order latch"):
            return _main_locked(args, fab_dir, canonical, journal, mode)
    except safelib.LockBusy as exc:
        safelib.append_journal(journal, {"event": "refused", "stage": "lock",
                                         "mode": mode, "reason": str(exc)})
        print(json.dumps({"script": "order_submit", "status": "error",
                          "error": str(exc)}, indent=1))
        return 2


def _main_locked(args, fab_dir: Path, canonical: Path, journal: Path,
                 mode: str) -> int:
    if mode == "api_create":
        safelib.append_journal(journal, {"event": "begin", "mode": mode,
                                         "argv_confirm": args.confirm})
    try:
        man = run(Path(args.pcb), fab_dir,
                  quote=Path(args.quote) if args.quote else None,
                  qty=args.qty, use_api=args.api or args.api_create,
                  order_number=args.order_number)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": "order_submit", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}, indent=1))
        return 2

    # The CANONICAL fab/order.json is always the record of truth: prior
    # state (created-latch, api merge, preserved notes) is read from it and
    # every run writes it back, regardless of --out. --out only adds a copy
    # of the payload elsewhere - it can never sidestep the latch.
    out = Path(args.out) if args.out else canonical
    try:
        prior = _load_prior_manifest(canonical)
    except ApiRefused as exc:
        # fail closed WITHOUT rewriting the corrupt canonical record
        safelib.append_journal(journal, {
            "event": "refused", "stage": "latch_load", "mode": mode,
            "reason": str(exc)})
        print(json.dumps({"script": "order_submit", "status": "error",
                          "error": str(exc)}, indent=1))
        return 2
    _merge_prior_api(man, prior)

    api_verdict = None
    if (args.api or args.api_create) and man["api"]["available"]:
        try:
            # U5 (codex C1): a governed workspace orders through its
            # attestation or not at all - refused BEFORE any network call.
            # A blocked disposition (hold restriction, ambiguous create
            # attempt, corrupt record) refuses even beside a valid
            # attestation - blocked outranks everything.
            rel = man.get("release") or {}
            if rel.get("governed") and (
                    not rel.get("valid")
                    or rel.get("disposition") == "blocked"):
                raise ApiRefused(
                    "release attestation missing/invalid or release "
                    "BLOCKED for this governed workspace (disposition "
                    f"{rel.get('disposition') or 'unknown'}): "
                    + "; ".join((rel.get("problems")
                                 or ["no attestation"])[:6])
                    + " - run attest.py build / clear the block first")
            session = _make_session()
            if args.api_create:
                if not (args.api_quote_file and args.confirm):
                    raise ApiRefused(
                        "--api-create requires --api-quote-file and "
                        '--confirm "<board> <qty>pcs <total>"')
                api_verdict = _api_create(
                    session, man, Path(args.api_quote_file), args.confirm,
                    Path(args.ship_json) if args.ship_json else None,
                    canonical=canonical, journal=journal)
            else:
                api_verdict = _api_quote(
                    session, man, Path(args.fab_dir),
                    prior_steps=(prior or {}).get("human_steps"),
                    country=args.country, ship_method=args.ship_method,
                    copper_oz=args.copper_oz)
        except ApiRefused as exc:
            if not exc.recorded:
                man["api"].update({"verdict": "refused", "note": str(exc)})
            api_verdict = "refused"
            if args.api_create:
                safelib.append_journal(journal, {
                    "event": "refused", "stage": "create", "mode": mode,
                    "recorded": bool(exc.recorded), "reason": str(exc)})
        except jlcapi.JlcApiError as exc:
            man["api"].update({"verdict": "transport", "note": str(exc)})
            api_verdict = "transport"
            if args.api_create:
                safelib.append_journal(journal, {
                    "event": "transport", "stage": "create", "mode": mode,
                    "reason": str(exc)})

        # a recorded created order is never downgraded by later runs: the
        # fresh outcome lands in last_quote_verdict / last_create_verdict,
        # and a failed re-quote flags surviving quote pointers as stale.
        # Sticky on recorded ids OR a prior "created" verdict - an ids-less
        # created record (S-2) must stay pinned too.
        rec = man["api"].get("order") or {}
        prior_created = (prior or {}).get("api", {}).get("verdict") == "created"
        if (rec.get("batchNum") or rec.get("orderId") or prior_created) \
                and api_verdict not in (None, "created"):
            key = ("last_create_verdict" if args.api_create
                   else "last_quote_verdict")
            man["api"][key] = api_verdict
            man["api"]["verdict"] = "created"
        if not args.api_create and api_verdict not in (None, "ok") \
                and ("quote_real" in man["api"]
                     or "api_quote_json" in man["api"]):
            man["api"]["quote_stale"] = True

    text = safelib.atomic_write_json(canonical, man)
    if out.resolve() != canonical.resolve():
        safelib.atomic_write_text(out, text)
        man["order_json_copy"] = str(out)
    man["order_json"] = str(canonical)
    man["attempt_journal"] = str(journal) if journal.exists() else None
    print(json.dumps(man, indent=1))

    if args.api or args.api_create:
        if not man["api"]["available"]:
            code = 2
        elif api_verdict not in ("ok", "scope_pending", "created", "skipped"):
            code = 2
        else:
            code = 1 if man["status"] in ("incomplete",
                                          "not_order_ready") else 0
    else:
        code = 1 if man["status"] in ("incomplete", "not_order_ready") else 0
    if args.api_create:
        safelib.append_journal(journal, {
            "event": "end", "mode": mode, "verdict": api_verdict,
            "status": man["status"], "exit": code})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
