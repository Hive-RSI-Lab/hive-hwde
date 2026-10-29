# Workarounds

Every place where hwde gets a board through its gates only because something
worked around a defect in the skill, by a shim in `bin/hwde` or by an edit
made by hand in a board's files. The test (`bin/hwde test`, and
`bin/hwde check-all` after its boards) holds it to the truth. The score
(`bin/hwde score`, [score.md](score.md)) doesn't read it: it runs the gates
with every shim off and charges what they find.

- Each row's id has a probe, `tests/workarounds/<id>.py`, that exits 0 while
  the cause still stands and 10 once it is fixed. The test runs every probe. A
  probe whose cause stands with no row here fails the test, so removing a row
  without fixing the cause fails it. A row whose probe says it's fixed fails
  the test too: take the workaround out (the shim, or redo the board's step
  without the hand edit) and remove the row.
- A shim in `bin/hwde` runs only while its row is here (`wa <id>`), so
  delisting it also turns it off for the gates.

`steps` are the gate steps whose input the workaround made or changed, out of
the five `check-all` runs per board: `erc`, `drc_routed`, `verify`, `fab`
(gerbers, drill, BOM and CPL) and `dfm`. `-` means none of them (the
workaround serves a stage before the gates, or renders). `boards` is `*` for
every board, or a comma-separated list: boards under `boards/`, or the
product boards built in other projects that [boards.md](boards.md) lists
(their `where` paths are in that project's board). When a fix lands somewhere other
than where its probe looks, the fix updates the probe to look where the
cause now is.

| id | kind | boards | steps | where | cause | brief |
|---|---|---|---|---|---|---|
| symdir | shim | * | erc | `bin/hwde`, `bin/flatten-symdirs.py` | kicad-sch-api 0.5.6 can't read KiCad 10's `.kicad_symdir` stock libraries, and `scripts/lib/env.py` hands it them as they are; the schematic every ERC reads was written with flat copies | 261028-141924 |
| render-3d-loaders | shim | * | - | `bin/hwde` | `scripts/kc.py` `render_env` looks for the 3D loaders only at distro paths beside the kicad-cli it runs, and misses the AppImage's `shared/lib/kicad/plugins/3d` | 261028-141924 |
| bulk-cap-pdn | hand | hwde-led-test | verify | `boards/hwde-led-test/kicad/gen/root.py` (`extra=` in `emit_decoupling`) | on a board with no IC nothing associates the bulk cap with its rail, and `check_pdn` errors on +5V; C1 was recorded against J1.1 by hand | 261028-141930 |
| parts-refdes | hand | hwde-led-test, lite-main, core | fab, dfm | `boards/hwde-led-test/parts/parts.json` (`refdes` per line); the hi-fi boards write theirs with board-local scripts (lite-main `kicad/gen/parts_json.py`) | `agents/part-sourcer.md` asks for one line per part with no `refdes`, and `bom_cpl` maps a ref to its LCSC number only through `refdes`, so the BOM ships with no LCSC numbers and dfm fails on them; `refdes` was added by hand | 261028-141930 |
| pwr-clearance | shim | lite-main | drc_routed | lite-main `kicad/gen/pro_clearance.py` (Pwr_* classes to 0.15 mm after rules_gen) | `rules_gen` gives every power class max(floor, 0.2 mm) whatever its voltage, so Freerouting can't enter a pin flanked by power pins closer than that and left CC1/CC2/CFG1 unrouted | 261028-181236 |
| power-neckdown | hand | lite-main, core | drc_routed | the boards' `kicad/constraints.json` (`power`: plane-fed rails and amp outputs at the current of their taps) | `rules_gen` gives a power net one width on every track: a 5 A rail's 2.38 mm minimum also binds the stub into a 0.22 mm DFN pin, which no track meets; the currents were declared down by hand to fit the pins | 261028-181236 |
| via-hole-floor | shim | lite-main, core | drc_routed, dfm | core `kicad/gen/pro_vias.py` (0.7/0.3 mm vias in every class); lite-main re-ran the route and nudged the last vias with route_edit by hand | Freerouting spaces vias by copper clearance only, and `rules_gen`'s classes (0.6/0.3 mm at 0.127 mm) put drill edges 0.43 mm apart, under JLC's 0.5 mm hole-to-hole | 261028-181236 |
| krt-dir | shim | lite-main, core | drc_routed | lite-main `build.sh`, core `kicad/gen/route.sh` (export `HWDE_KRT_DIR`) | `bin/hwde` doesn't point `HWDE_KRT_DIR` at the Hive's `kicad-routing-tools/`, and `env.find_krt` looks only under the skill's `tools/krt`, so route_auto's KRT finish pass is skipped | 261028-141924 |
| krt-finish-width | shim | core | drc_routed | core `kicad/gen/post_route.py` (deletes the finish pass's under-width power tracks) | `route_auto._krt_finish` hands KRT the open nets with no track width (KRT has `--track-width`, `--power-nets-widths`), so power nets come back at KRT's default, under their width rule | 261028-181236 |
| outline-snap | shim | lite-main | dfm | lite-main `kicad/gen/snap_outline.py` (outline to 1 um) | `board_init` draws the outline at a non-round origin, KiCad's Gerber corner arcs then miss the edges by up to 0.05 um, and `gerblib` (snapping to 0.01 um) reads the outline as open, so dfm skips every copper- and hole-to-edge check | 261028-181244 |
| ep-via-pads | shim | lite-main | drc_routed | lite-main `lib/fix_footprints.py` (TPA3118's exposed-pad vias) | EasyEDA's exposed-pad thermal vias come as unnumbered thru-hole pads with paste, so no net, and `fpfix` (run on every pull) leaves them so | 261028-181244 |
