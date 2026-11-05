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

Reads the block as written (fabmaterial.board_copper_layers), since geom swaps
a zero for its default. No block is nothing_to_judge; a copper layer that gives
no thickness is listed in not_judged.

CLI: check_stackup.py --pcb BOARD.kicad_pcb [--out REPORT.json]
Exit: 0 pass, 1 findings, 2 error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import checklib  # noqa: E402
import fabmaterial  # noqa: E402

SCRIPT = "check_stackup"
SOURCE = SCRIPT
COPPER_MIN_MM = 0.009
COPPER_MAX_MM = 0.21


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


def run(argv=None) -> tuple[dict, str | None]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pcb", required=True, type=Path)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if not args.pcb.is_file():
        raise checklib.CheckError(f"board not found: {args.pcb}")
    layers = fabmaterial.board_copper_layers(args.pcb)
    vios, left = judge(layers)
    return checklib.report(
        SCRIPT, args.pcb, vios, copper_mm=dict(layers), not_judged=left,
        nothing_to_judge=None if layers else
        "the board has no stackup block listing its copper"), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
