# Workarounds

Every place where hwde gets a board through its gates only because something
worked around a defect in the module's scripts, by a shim in `bin/hwde` or by an edit
made by hand in a board's files. The score (`bin/hwde score`) reads this
table, and the test (`bin/hwde check-all`) holds it to the truth:

- Each row's id has a probe, `tests/workarounds/<id>.py`, that exits 0 while
  the cause still stands and 10 once it is fixed. The test runs every probe. A
  probe whose cause stands with no row here fails the test, so removing a row
  without fixing the cause fails it. A row whose probe says it's fixed fails
  the test too: take the workaround out (the shim, or redo the board's step
  without the hand edit), remove the row, and the score goes up.
- A shim in `bin/hwde` runs only while its row is here (`wa <id>`), so
  delisting it also turns it off for the gates.

`steps` are the gate steps whose input the workaround made or changed, out of
the five `check-all` runs per board: `erc`, `drc_routed`, `verify`, `fab`
(gerbers, drill, BOM and CPL) and `dfm`. `-` means none of them (the
workaround serves a stage before the gates, or renders). `boards` is `*` for
every board, or a comma-separated list. When a fix lands somewhere other
than where its probe looks, the fix updates the probe to look where the
cause now is; a reviewer reads a probe change as a change to the score.

| id | kind | boards | steps | where | cause | brief |
|---|---|---|---|---|---|---|
| symdir | shim | * | erc | `bin/hwde`, `bin/flatten-symdirs.py` | kicad-sch-api 0.5.6 can't read KiCad 10's `.kicad_symdir` stock libraries, and `scripts/lib/env.py` hands it them as they are; the schematic every ERC reads was written with flat copies | 261028-141924 |
| render-3d-loaders | shim | * | - | `bin/hwde` | `scripts/kc.py` `render_env` looks for the 3D loaders only at distro paths beside the kicad-cli it runs, and misses the AppImage's `shared/lib/kicad/plugins/3d` | 261028-141924 |
| sch-uuids | hand | hwde-led-test | - | `boards/hwde-led-test/kicad/hwde-led-test.kicad_sch` | regenerating `kicad/gen/root.py` gives new symbol UUIDs, so the board's footprints no longer match it; the committed schematic is kept by hand instead of rebuilt. A rebuilt one passes erc, verify and dfm today (the board has no symbol paths), so no step is charged | 261028-141934 |
| bulk-cap-pdn | hand | hwde-led-test | verify | `boards/hwde-led-test/kicad/gen/root.py` (`extra=` in `emit_decoupling`) | on a board with no IC nothing associates the bulk cap with its rail, and `check_pdn` errors on +5V; C1 was recorded against J1.1 by hand | 261028-141930 |
| parts-refdes | hand | hwde-led-test | fab, dfm | `boards/hwde-led-test/parts/parts.json` (`refdes` per line) | `agents/part-sourcer.md` asks for one line per part with no `refdes`, and `bom_cpl` maps a ref to its LCSC number only through `refdes`, so the BOM ships with no LCSC numbers and dfm fails on them; `refdes` was added by hand | 261028-141930 |
| courtyard-pads | hand | hwde-led-test | drc_routed | `boards/hwde-led-test/lib/aiee.pretty/*0603*.kicad_mod` and the board's copies | EasyEDA's 0603 courtyards (via `lib_pull`) are the body outline and don't cover the pads, and no gate catches it; widened by hand to ±1.40 x ±0.70 mm | 261028-141932 |
| outline-silk | hand | hwde-led-test | drc_routed | `boards/hwde-led-test/kicad/hwde-led-test.kicad_pcb` (Edge.Cuts) | `board_edit --outline fit` measures copper and footprints but not reference text, so a fit at the usual margin clips refdes silk; the margin was raised to 2.0 mm by hand | 261028-141932 |
