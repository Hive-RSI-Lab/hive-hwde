"""check_pdn.py - power-distribution decoupling inventory (SPEC 6.3, P8).

One concern: does each power rail have the reservoir it needs? For every rail in
constraints.json["power"], gather the caps associated with it (the same
decoupling.json metadata check_decoupling uses) and sanity-check the inventory:
 - a rail with NO decoupling at all is an error (nothing holds the rail up
   during a transient);
 - a rail with caps but NO bulk reservoir (nothing >= 1 uF) is a warning (only
   ceramics -> poor low-frequency impedance / droop under load steps).

Ceramic (high-frequency) coverage is REPORTED but not gated: an input/bulk rail
legitimately carries only bulk capacitance, and flagging it would false-positive
on real supplies (the corpus +5V / VBUS rails are exactly this). Plane-connection
width is left to check_current (pour neckdown) - the same geometry, one owner.

A ground/return net in the power list (there for its current budget) is
skipped: caps decouple a rail TO it, so it cannot itself be undecoupled.

The corpus rails all carry bulk + caps, so this check is clean on all goldens.

CLI: --pcb board.kicad_pcb --constraints constraints.json --decoupling dec.json
     [--out report.json]                       exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
from checklib import violation  # noqa: E402
import check_decoupling  # noqa: E402  (reuse the farad parser)
from check_ratings import is_ground  # noqa: E402  (one ground-name rule)

SCRIPT = "check_pdn"
BULK_MIN_F = 1e-6              # a bulk reservoir cap is >= 1 uF
CERAMIC_MAX_F = 1e-6          # high-frequency ceramic decoupling is < 1 uF


def rail_caps(rail: str, assocs: list[dict]) -> list[dict]:
    return [a for a in assocs if a.get("rail") == rail]


def check_rail(bg: geom.BoardGeom, rail: str, current_a, assocs: list[dict]):
    caps = rail_caps(rail, assocs)
    parsed = [(a.get("cap"), check_decoupling.parse_farads(a.get("value")))
              for a in caps]
    refs = sorted({c for c, _ in parsed if c})
    bulk = [c for c, f in parsed if f is not None and f >= BULK_MIN_F]
    ceramic = [c for c, f in parsed if f is not None and f < CERAMIC_MAX_F]
    total_f = sum(f for _, f in parsed if f is not None)
    on_board = rail in bg.nets

    violations: list[dict] = []
    if not caps:
        violations.append(violation(
            SCRIPT, "error", None, None, rail, [],
            f"power rail {rail}"
            f"{f' ({current_a} A)' if current_a is not None else ''} has no "
            f"decoupling capacitors", SCRIPT, kind="pdn_undecoupled",
            rail=rail, cap_count=0))
    elif not bulk:
        violations.append(violation(
            SCRIPT, "warning", None, None, rail, refs,
            f"power rail {rail} has {len(caps)} cap(s) but no bulk reservoir "
            f"(>= 1 uF); only ceramics hold it", SCRIPT, kind="pdn_no_bulk",
            rail=rail, cap_count=len(caps), bulk_count=0))

    facts = {"rail": rail, "current_a": current_a, "on_board": on_board,
             "caps": refs, "cap_count": len(caps), "bulk_count": len(bulk),
             "ceramic_count": len(ceramic),
             "total_uf": checklib.rnd(total_f * 1e6)}
    return violations, facts


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="PDN decoupling inventory per power rail.")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--constraints", required=True,
                    help="constraints.json with a power list")
    ap.add_argument("--decoupling", required=True,
                    help="decoupling.json (cap<->rail associations)")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    cons = checklib.load_json(args.constraints, "constraints")
    meta = checklib.load_json(args.decoupling, "decoupling metadata")
    assocs = meta.get("associations", [])
    bg = geom.load_board(Path(args.pcb))

    violations: list[dict] = []
    checked: list[dict] = []
    for entry in cons.get("power", []):
        rail = entry.get("net")
        if not rail:
            continue
        # `"pdn": false` opts a power entry out of the decoupling inventory:
        # nets declared only so rules_gen sizes their trace width (e.g. the
        # raw-input stub BEFORE a reverse-polarity element) are not rails
        # anything decouples by design (S14 finding).
        if entry.get("pdn") is False:
            checked.append({"rail": rail, "current_a": entry.get("current_a"),
                            "skipped": "pdn:false (width-only power entry)"})
            continue
        # A return net listed under `power` only so rules_gen sizes it for the
        # return current is not a rail: decoupling is measured TO it, never on
        # it (scorecard 2026-09-29: bb-buck, sbuck-5v3a and bldc-motor-driver
        # each waived this same GND finding).
        if is_ground(rail):
            checked.append({"rail": rail, "current_a": entry.get("current_a"),
                            "skipped": "return net: decoupled to, not on"})
            continue
        vs, facts = check_rail(bg, rail, entry.get("current_a"), assocs)
        violations.extend(vs)
        checked.append(facts)

    payload = checklib.report(SCRIPT, args.pcb, violations, checked=checked)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
