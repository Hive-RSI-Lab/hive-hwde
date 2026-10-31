"""Does check_paste's aperture model match what KiCad really plots?

Builds a small board whose pads exercise every paste-margin source (pad
margin, pad ratio, footprint margin, board setup clearance, a paste-only
window pane pad, rotation, roundrect), exports F.Paste with kicad-cli and
compares each aperture's area with check_paste's. Run:

  PYTHONPATH=scripts:scripts/lib $HWDE_PY reference/ingest/dfm-assembly/paste_margin_vs_kicad.py

Prints one line per pad and exits 1 on any area off by more than 1 %.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import check_paste
import gerblib

BOARD = """(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0")
 (general (thickness 1.6) (legacy_teardrops no))
 (paper "A4")
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (13 "F.Paste" user) (1 "F.Mask" user)
  (25 "Edge.Cuts" user))
 (setup (pad_to_mask_clearance 0) (pad_to_paste_clearance -0.02)
  (pcbplotparams (layerselection 0x00000000_00000000_55555555_5755f5ff)))
 (net 0 "")
 (footprint "t:A" (layer "F.Cu") (at 10 10)
  (property "Reference" "A1" (at 0 -2 0) (layer "F.SilkS"))
  (pad "1" smd rect (at 0 0) (size 1 0.5) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 2 0) (size 1 0.5) (layers "F.Cu" "F.Paste" "F.Mask")
   (solder_paste_margin -0.05))
  (pad "3" smd rect (at 4 0) (size 1 0.5) (layers "F.Cu" "F.Paste" "F.Mask")
   (solder_paste_margin_ratio -0.1))
  (pad "4" smd roundrect (at 6 0 90) (size 1.2 0.6) (roundrect_rratio 0.25)
   (layers "F.Cu" "F.Paste" "F.Mask")))
 (footprint "t:B" (layer "F.Cu") (at 10 20 30) (solder_paste_margin -0.1)
  (property "Reference" "B1" (at 0 -2 0) (layer "F.SilkS"))
  (pad "1" smd rect (at 0 0) (size 2 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd oval (at 3 0) (size 1.5 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "" smd rect (at 6 0) (size 0.8 0.8) (layers "F.Paste")))
 (gr_rect (start 0 0) (end 30 30) (stroke (width 0.1) (type default)) (fill no)
  (layer "Edge.Cuts"))
)
"""


def main() -> int:
    cli = os.environ.get("HWDE_KICAD_CLI", "kicad-cli")
    with tempfile.TemporaryDirectory() as td:
        pcb = Path(td) / "t.kicad_pcb"
        pcb.write_text(BOARD)
        out = Path(td) / "g"
        out.mkdir()
        subprocess.run([cli, "pcb", "export", "gerbers", "--layers", "F.Paste",
                        "-o", str(out), str(pcb)], check=True,
                       capture_output=True)
        g = next(out.glob("*Paste*"))
        lg = gerblib.read_gerber(g, "F.Paste")
        comps = lg.components()
        pads, _ = check_paste.read_pads(pcb)
        bad = 0
        for ref in sorted(pads):
            for p in pads[ref]:
                ap = p["aperture"]
                if ap is None:
                    continue
                hit = [c for c in comps if c.intersects(ap.centroid.buffer(0.01))]
                kicad = hit[0].area if hit else 0.0
                off = abs(kicad - ap.area) / ap.area
                bad += off > 0.01
                print(f"{ref} pad {p['number'] or '(paste)'}: model "
                      f"{ap.area:.4f} mm2, KiCad {kicad:.4f} mm2, "
                      f"{off:.1%} {'OK' if off <= 0.01 else 'MISMATCH'}")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
