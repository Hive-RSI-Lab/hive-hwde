#!/usr/bin/env python
"""castellated_fp.py - a row of castellated (plated half-hole) edge pads as a .kicad_mod.

Writes one KiCad footprint: N plated through-hole pads at a fixed pitch, each
marked `(property pad_prop_castellated)` so the board, DFM and the order all
know they are castellated (lib/castellation.py). Sizes default to JLC's
RECOMMENDED values and are refused below JLC's minimums - both read from the
`castellated:` block of reference/jlc_capabilities.yaml, which cites the
sources.

Footprint frame: the board edge is the local X axis (y = 0) and the board
interior is +Y. Each hole is centred on y = 0, so placing the footprint with
its origin on Edge.Cuts puts every hole on the outline; rotate it to use
another edge. Each pad is a rect of width drill + 2*ring that reaches `ring`
outward past the hole (milled away with the edge) and `extension` inward past
it. A dashed F.Fab line marks where the edge must run. The footprint does not
draw Edge.Cuts: the board's outline stays the board's. It is marked
exclude_from_pos_files + exclude_from_bom, so bom_cpl classes it a board
feature (nothing to buy or place).

Checks before writing: drill >= min_drill_mm, ring >= min_annular_ring_mm,
extension >= min_pad_extension_mm, hole edge-to-edge (pitch - drill) >=
min_hole_to_hole_mm. Any failure is exit 2 with nothing written.

CLI:
  castellated_fp.py --pins N --pitch 2.54 --out DIR.pretty/NAME.kicad_mod
                    [--name NAME] [--drill 1.0] [--ring 0.25]
                    [--extension 0.8] [--first-number 1] [--json OUT]
Output: JSON {script, status, footprint, path, pins, pitch_mm, drill_mm,
ring_mm, extension_mm, pad_size_mm, row_length_mm} to stdout or --json.
Exit 0 written / 2 error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import castellation  # noqa: E402


def _f(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


def plan(pins: int, pitch: float, drill: float, ring: float,
         extension: float, rules: dict) -> dict:
    """Pad geometry for the row, or ValueError naming the broken JLC rule."""
    if pins < 1:
        raise ValueError("--pins must be at least 1")
    bad = []
    if drill < rules["min_drill_mm"] - 1e-9:
        bad.append(f"drill {drill} mm < JLC minimum {rules['min_drill_mm']} mm")
    if ring < rules["min_annular_ring_mm"] - 1e-9:
        bad.append(f"ring {ring} mm < JLC minimum "
                   f"{rules['min_annular_ring_mm']} mm")
    if extension < rules["min_pad_extension_mm"] - 1e-9:
        bad.append(f"extension {extension} mm < JLC minimum "
                   f"{rules['min_pad_extension_mm']} mm")
    if pins > 1 and pitch - drill < rules["min_hole_to_hole_mm"] - 1e-9:
        bad.append(f"hole edge-to-edge {pitch - drill:.3f} mm (pitch - drill) "
                   f"< JLC minimum {rules['min_hole_to_hole_mm']} mm")
    if bad:
        raise ValueError("; ".join(bad))
    w = drill + 2 * ring
    h = ring + drill + extension
    # copper spans y in [-(drill/2 + ring), drill/2 + extension]. KiCad keeps
    # the hole at the pad's (at) and moves the copper by the drill offset, so
    # the pad sits at y = 0 (hole on the edge) and an offset of +cy carries
    # the copper inboard.
    cy = (extension - ring) / 2.0
    return {"pad_w": w, "pad_h": h, "pad_cy": cy, "drill_off": cy,
            "row": (pins - 1) * pitch}


def render(name: str, pins: int, pitch: float, drill: float, geo: dict,
           first: int = 1) -> str:
    x0 = -geo["row"] / 2.0
    top = -(geo["pad_h"] / 2.0 - geo["pad_cy"])
    bot = geo["pad_h"] / 2.0 + geo["pad_cy"]
    half = geo["row"] / 2.0 + geo["pad_w"] / 2.0
    out = [
        f'(footprint "{name}"',
        '\t(version 20241229)',
        '\t(generator "hwde_castellated_fp")',
        '\t(layer "F.Cu")',
        f'\t(descr "{pins} castellated edge pads, pitch {_f(pitch)} mm, '
        f'drill {_f(drill)} mm; hole centres on the board edge (y = 0), '
        'board interior +Y")',
        '\t(tags "castellated half-hole edge")',
        f'\t(property "Reference" "REF**" (at 0 {_f(bot + 1.2)} 0) '
        '(layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))',
        f'\t(property "Value" "{name}" (at 0 {_f(bot + 2.6)} 0) '
        '(layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))',
        # a board feature, not a part: nothing to buy or place (bom_cpl
        # reads these flags as class board_feature)
        '\t(attr through_hole exclude_from_pos_files exclude_from_bom)',
        f'\t(fp_line (start {_f(-half)} 0) (end {_f(half)} 0) '
        '(stroke (width 0.1) (type dash)) (layer "F.Fab"))',
        f'\t(fp_text user "board edge" (at 0 {_f(top - 0.8)} 0) '
        '(layer "F.Fab") (effects (font (size 0.6 0.6) (thickness 0.1))))',
        f'\t(fp_rect (start {_f(-half - 0.25)} {_f(top - 0.25)}) '
        f'(end {_f(half + 0.25)} {_f(bot + 0.25)}) '
        '(stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd"))',
    ]
    for i in range(pins):
        x = x0 + i * pitch
        out.append(
            f'\t(pad "{first + i}" thru_hole rect (at {_f(x)} 0) '
            f'(size {_f(geo["pad_w"])} {_f(geo["pad_h"])}) '
            f'(drill {_f(drill)} (offset 0 {_f(geo["drill_off"])})) '
            '(property pad_prop_castellated) '
            '(layers "*.Cu" "*.Mask"))')
    out.append(")")
    return "\n".join(out) + "\n"


def run(pins: int, pitch: float, out: Path, name: str | None = None,
        drill: float | None = None, ring: float | None = None,
        extension: float | None = None, first: int = 1) -> dict:
    rules = castellation.load_rules()
    drill = float(drill if drill is not None else rules["rec_drill_mm"])
    ring = float(ring if ring is not None else rules["rec_annular_ring_mm"])
    extension = float(extension if extension is not None
                      else rules["rec_pad_extension_mm"])
    geo = plan(pins, pitch, drill, ring, extension, rules)
    name = name or out.stem
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(name, pins, pitch, drill, geo, first),
                   encoding="utf-8")
    return {"script": "castellated_fp", "status": "pass", "footprint": name,
            "path": str(out), "pins": pins, "pitch_mm": pitch,
            "drill_mm": drill, "ring_mm": ring, "extension_mm": extension,
            "pad_size_mm": [round(geo["pad_w"], 4), round(geo["pad_h"], 4)],
            "row_length_mm": round(geo["row"], 4)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pins", type=int, required=True)
    ap.add_argument("--pitch", type=float, required=True, help="mm")
    ap.add_argument("--out", required=True, help="the .kicad_mod to write")
    ap.add_argument("--name", help="footprint name (default: --out stem)")
    ap.add_argument("--drill", type=float, help="mm (default JLC recommended)")
    ap.add_argument("--ring", type=float,
                    help="copper beside and outside the hole, mm")
    ap.add_argument("--extension", type=float,
                    help="pad reach inward past the hole, mm")
    ap.add_argument("--first-number", type=int, default=1)
    ap.add_argument("--json", help="write the JSON result here")
    args = ap.parse_args(argv)
    try:
        rep = run(args.pins, args.pitch, Path(args.out), args.name,
                  args.drill, args.ring, args.extension, args.first_number)
        code = 0
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        rep = {"script": "castellated_fp", "status": "error",
               "error": f"{type(exc).__name__}: {exc}"}
        code = 2
    text = json.dumps(rep, indent=1)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    else:
        print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
