#!/usr/bin/env python
"""check_stackup.py - copper thicknesses in a board's (stackup) block that no
fab builds, and outer layers no order can state (P8 verify; board only;
hive-hwde #728).

rf-kit-ro4350b's generator wrote the laminate's 1.524 mm into B.Cu, about
43 oz, and nothing noticed: geom reads the stack as written, so every check on
that layer's copper took it as true, and the order compared F.Cu alone. KiCad's
DRC doesn't read these numbers either.

Findings:
  copper_thickness_range  error: a copper layer outside 0.009 to 0.21 mm (0.25
                          to 6 oz, inclusive). A plausibility bound, not a fab's
                          capability: dfm_check owns what a fab offers.
  outer_copper_differs    error: F.Cu and B.Cu differ by over 0.05 oz. JLC's
                          copperWeight is one value for both, so
                          order_submit --api-create refuses this board at the
                          same tolerance. Left out when either side is out of
                          range: that finding is the one to fix.

  mask_thickness_range / silk_thickness_range
                          error: a solder mask or silkscreen layer thicker
                          than 0.05 mm or thinner than 0.005 mm (#753). The
                          48 boards' mask is 0.01; a layer with no thickness
                          is left out.

  thickness_stack_vs_general
                          error: the stackup's layers sum to a thickness
                          over 10 % off the board's (general (thickness)).
  thickness_quote_differs error: fab/quote.json's spec thickness_mm is over
                          10 % off the stack sum or the general thickness
                          (#745). 10 % is over twice the 4.4 % spread of 47 of
                          48 boards (mask and silk make the sum 1.674 against
                          1.6); the 48th, rf-kit-ro4350b, is 4.617 against 1.6.
                          No quote beside the board, or no general thickness,
                          is left out.

Reads the block as written (fabmaterial.board_copper_layers), since geom swaps
a zero for its default. No block is nothing_to_judge; a copper layer that gives
no thickness is listed in not_judged.

CLI: check_stackup.py --pcb BOARD.kicad_pcb [--out REPORT.json]
Exit: 0 pass, 1 findings, 2 error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import checklib  # noqa: E402
import fabmaterial  # noqa: E402

SCRIPT = "check_stackup"
SOURCE = SCRIPT
COPPER_MIN_MM = 0.009
COPPER_MAX_MM = 0.21
# Mask and silk ink: JLC's mask is 0.01 to 0.04 mm, silk about 0.01 to 0.03; KiCad
# writes 0.01 for mask. A plausibility bound like copper's, not a capability.
COATING_MIN_MM = 0.005
COATING_MAX_MM = 0.05


def _oz(mm: float) -> float:
    return round(mm / fabmaterial.MM_PER_OZ, 2)


def judge(layers: list[tuple[str, float | None]]) -> tuple[list[dict], list[str]]:
    """Findings and what was left out, from (name, mm) in stack order."""
    vios, left = [], []
    for name, mm in layers:
        if mm is None:
            left.append(f"{name}: the stackup gives no thickness")
        elif not COPPER_MIN_MM <= mm <= COPPER_MAX_MM:
            vios.append(checklib.violation(
                SCRIPT, "error", None, name, None, [],
                f"{name} copper is {mm:g} mm ({mm / fabmaterial.MM_PER_OZ:.1f} oz), "
                f"outside {COPPER_MIN_MM:g} to {COPPER_MAX_MM:g} mm (0.25 to 6 oz). "
                f"Checks on this layer's copper read it as true; correct the "
                f"stackup", SOURCE,
                kind="copper_thickness_range", thickness_mm=mm))
    copper = dict(layers)
    front, back = copper.get("F.Cu"), copper.get("B.Cu")
    if front is None or back is None or vios:
        return vios, left
    if abs(front - back) / fabmaterial.MM_PER_OZ > fabmaterial.COPPER_OZ_TOLERANCE:
        vios.append(checklib.violation(
            SCRIPT, "error", None, None, None, [],
            f"F.Cu is {front:g} mm ({_oz(front):g} oz) and B.Cu {back:g} mm "
            f"({_oz(back):g} oz): an order's copper weight is one value for "
            f"both outer layers, so no quote can state this board", SOURCE,
            kind="outer_copper_differs", front_mm=front, back_mm=back))
    return vios, left


def judge_coatings(layers: list[tuple[str, str, float | None]]) -> list[dict]:
    """Mask and silk layers outside COATING_MIN_MM..COATING_MAX_MM. A layer
    giving no thickness (KiCad leaves silk without) is nothing to judge."""
    vios = []
    for name, kind, mm in layers:
        if mm is None or COATING_MIN_MM <= mm <= COATING_MAX_MM:
            continue
        vios.append(checklib.violation(
            SCRIPT, "error", None, name, None, [],
            f"{name} {kind} is {mm:g} mm thick, outside {COATING_MIN_MM:g} to "
            f"{COATING_MAX_MM:g} mm. The stack sum, and every check that reads "
            f"it, counts this as built; correct the stackup", SOURCE,
            kind=f"{kind}_thickness_range", thickness_mm=mm))
    return vios


def judge_thickness(stack: float | None, general: float | None,
                    quote: float | None) -> list[dict]:
    """Each pair of the three thicknesses that sits over the tolerance."""
    tol = fabmaterial.THICKNESS_TOLERANCE
    vios = []

    def gap(a, b):
        return a is not None and b is not None \
            and fabmaterial.thickness_gap(a, b) > tol

    if gap(stack, general):
        vios.append(checklib.violation(
            SCRIPT, "error", None, None, None, [],
            f"the stackup's layers sum to {stack:g} mm and the board's general "
            f"thickness is {general:g} mm: over {tol:.0%} apart. One of them "
            f"is wrong, and checks read both", SOURCE,
            kind="thickness_stack_vs_general", stack_mm=stack,
            general_mm=general))
    for name, other in (("stack sum", stack), ("general thickness", general)):
        if gap(quote, other):
            vios.append(checklib.violation(
                SCRIPT, "error", None, None, None, [],
                f"the quote's thickness is {quote:g} mm and the board's "
                f"{name} is {other:g} mm: over {tol:.0%} apart. Order and "
                f"board must state one thickness", SOURCE,
                kind="thickness_quote_differs", quote_mm=quote,
                against=name, other_mm=other))
    return vios


def _quote_thickness(pcb: Path) -> float | None:
    """spec.thickness_mm of the workspace's fab/quote.json, if there is one."""
    try:
        spec = json.loads((pcb.resolve().parent.parent / "fab" / "quote.json")
                          .read_text(encoding="utf-8")).get("spec") or {}
        return float(spec["thickness_mm"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def run(argv=None) -> tuple[dict, str | None]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pcb", required=True, type=Path)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if not args.pcb.is_file():
        raise checklib.CheckError(f"board not found: {args.pcb}")
    layers = fabmaterial.board_copper_layers(args.pcb)
    vios, left = judge(layers)
    vios += judge_coatings(fabmaterial.board_coating_layers(args.pcb))
    if layers:
        vios += judge_thickness(fabmaterial.board_stack_sum_mm(args.pcb),
                                fabmaterial.board_general_mm(args.pcb),
                                _quote_thickness(args.pcb))
    return checklib.report(
        SCRIPT, args.pcb, vios, copper_mm=dict(layers), not_judged=left,
        nothing_to_judge=None if layers else
        "the board has no stackup block listing its copper"), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
