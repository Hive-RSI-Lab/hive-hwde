# mutant_paste - tented-pad gerber mutant (dfm_pad_tented known answer)

Copied 2026-08-06 from tests/fixtures/stages/mutant_cpl/gerbers (blinky2
cpl-rotation mutant export, kicad-cli 10.0.3), then ONE mask-opening flash
deleted from blinky2-F_Mask.gts: `X118400000Y-129625000D03*` (C8 pad 1,
board space ~(118.4, 129.625)). Its solder-paste aperture in
blinky2-F_Paste.gtp is untouched, so dfm_check.check_pad_tented must report
exactly one dfm_pad_tented ERROR there (and the inherited cpl_polarity D1
error stays). Board file: blinky2.kicad_pcb (the cpl-rotation mutant);
polarity oracle: tests/s7_regen/blinky2/golden.net.
