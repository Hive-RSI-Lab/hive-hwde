# hwde snags hit building lite-main

**Filed to hive-hwde:** 261028-181236-e777fe2a1d (routing: #15-#20, #23, #26, #29) and
261028-181244-a3c523d45b (library/setup: #3, #5, #10-#14, #21, #22, #27).
Others were already open: #1/#4 in 261028-153449, #6 in 261028-141933, #7 in 261028-143613
and #9 in 261028-141932.

**hwde version:** the gates last passed on hive-hwde release 94ec4b7
(installed 2026-10-28 14:18 UTC, after the courtyard check and module test
landed).

This board was hive-hwde's first real product board in the Hive: 130 parts on
2 layers, with USB-C PD, an ESP32 module, a codec and a 2 x 43 W class-D amp.
Below is every place the module got in the way or got something wrong, and
what was done about it. **Filed** means a brief went to project hive-hwde.
**Board-local** means a script in this workspace works around it until hwde
fixes it. Each workaround names the snag it stands in for.

| # | Where | Snag | What was done |
|---|---|---|---|
| 1 | `bin/hwde` | The file isn't executable (mode 0644), so `$HIVE_MODULES/hive-hwde/bin/hwde` gives "Permission denied". | Run it as `sh .../bin/hwde`. Filed. |
| 2 | `bin/hwde check --quiet` | `--quiet` still prints the whole check_env JSON. | Ignored. Filed with #1. |
| 3 | `bin/hwde` + `docs/README.md` | KiCadRoutingTools 0.19.0 is now in `/hive/tools/kicad-routing-tools`, but `bin/hwde` doesn't set `HWDE_KRT_DIR` and the README still says KRT is missing. | `build.sh` exports `HWDE_KRT_DIR`. Filed. |
| 4 | `task_router` | "full-run: design the lite-main board from the brief" matched no verb. | Re-ran with `--verb full-run`. Minor; included in the filing. |
| 5 | `lib_pull` | 21 parts took more than 10 min (15 s pacing, a 90 s back-off and verify-load). The run was killed part-way, which left absolute 3D-model paths until a second pull. | Pulled in smaller batches with `--pace-s 3-5`. Filed. |
| 6 | LCSC datasheets | Every wmsc.lcsc.com datasheet link returns a 141-byte stub through the proxy. | Took datasheets from the vendors (TI, WCH mirror, Everest mirror, Espressif); see `parts/datasheets.md`. Already filed (261028-141933). |
| 7 | `parts_search` | Can't find connectors by kind ("RCA jack", "screw terminal", "binding post"). | Searched by MPN family (KF128, DB128V). Already filed (261028-143613). |
| 8 | EasyEDA via `lib_pull` | The banana socket GC4010-F (C99865), the part product.md chose, has no CAD data. It's also an RC bullet connector, not a PCB banana socket. | Changed the design: a 16 A 4-way screw terminal (DB128V, C2915641), with banana or binding posts on the enclosure wired to it. Recorded in product.md and bom-estimate.csv. |
| 9 | EasyEDA footprints | 20 of 21 pulled courtyards don't cover their own pads (HTSSOP, ESSOP, SOT-23, 0603 LED, USB-C, ...). | Board-local `lib/fix_courtyards.py` grows each courtyard to pads + 0.25 mm. Already filed (261028-141932). |
| 10 | EasyEDA footprints | Part-specific defects: USB4105 locating pegs were PLATED with 0 annular ring; TPA3118 thermal vias had no pad number (so no net) and had paste on them; USB4105 VBUS/GND pads ended 0.18 mm from the peg holes. | Board-local `lib/fix_footprints.py`. Filed: fpfix should catch plated zero-ring pegs and unnumbered EP vias. |
| 11 | EasyEDA footprint XDJK-0271-1650 (stacked RCA) | No courtyard over the body, and no holes for the two locating pegs. | Added by hand in `lib/aiee.pretty` (pegs per Lian Xin drawing A-AV2-7A1). The jack's depth is uncertain, so check it with a real part before the enclosure. |
| 12 | `schlib` / kicad-sch-api | ksa doesn't read the project's `sym-lib-table`, so every `aiee:` symbol is "not found" until the generator registers the library with ksa's cache. schlib has no API for this, and hwde-led-test's README names a `kicad/gen/root.py` it never committed. | `root.py` calls `ksa.get_symbol_cache().add_library_path(...)`. Filed. |
| 13 | `lib_pin_types` | With no datasheet extracts it types every pin `passive`. ERC then passes 0/0 but checks no pin conflicts at all. The reviewer flagged this. | The fresh-context schematic review checked the netlist pin by pin. Filed: say so in the ERC gate result. |
| 14 | role prompts | `schematic-block.md`/`schematic-reviewer.md` say `kc.py erc --sch ...`, but kc.py takes the input positionally. | Used positional args. Included in a filing. |
| 15 | `rules_gen` | One width per net: a 5 A rail gets a 2.38 mm minimum on every track, including the stub into a 0.22 mm DFN pin or a 0.34 mm HTSSOP pin. That is unmeetable (`route_critical --pad-window` says so). The same happens with speaker outputs at 0.65 mm pitch. | Power trunks are pours (`kicad/gen/power.py`), so `current_a` sizes only the taps, with `plane_fed`. The amp OUT runs are declared at 1.0 A rms / 30 C (music at full power) so they fit the pins. Filed: a neck-down allowance at fine-pitch pads. |
| 16 | `rules_gen` | Power classes get 0.2 mm clearance, so Freerouting can't enter a pin flanked by power pins closer than that. It left CC1/CC2/CFG1 unrouted. | Board-local `kicad/gen/pro_clearance.py` sets Pwr_* to 0.15 mm (all nets < 30 V, JLC floor 0.127). Filed. |
| 17 | Freerouting vs JLC hole-to-hole | Freerouting spaces vias by copper clearance only, so its vias can land 0.46-0.49 mm apart hole to hole (floor 0.5). Raising the clearance to 0.2 mm fixes the vias but makes the 0.45 mm-pitch QFN unroutable. | Picked the cleanest of several route runs, then nudged the last vias with route_edit. Filed: route_auto should put the hole-to-hole floor in the DSN via rules. |
| 18 | `stitch_vias` | Keeps copper clearance but not the 0.5 mm hole-to-hole floor. It dropped GND vias 0.23-0.48 mm from mounting-hole via rings and the TPA3118's thermal vias. | Board-local `kicad/gen/fix_holes.py` removes the offending stitch vias after the gate. Filed. |
| 19 | Freerouting vs NPTH | Routed within 0.16 mm of the RCA jacks' NPTH locating pegs, inside the 0.25 mm hole clearance and 0.3 mm edge rule. | `kicad/gen/keepout.py` puts a no-track rule area on every NPTH hole. Filed: route_auto should export NPTH as keepouts. |
| 20 | `route_critical` | KRT power routing ignored existing pours (41 shorting items, rolled back). A 90 ohm USB pair on 1.6 mm 2-layer needs a 1.09 mm-wide pair, which it couldn't fit. | Skipped route_critical. USB full speed is routed as ordinary nets (`diff_pairs: []`). Filed. |
| 21 | No keep-out tool | constraints.json `keepouts` only steer placement. No hwde script draws a copper rule area, and the ESP32 antenna needs one. | `kicad/gen/keepout.py` (KiCad python) adds `ANT_KEEPOUT`. Filed. |
| 22 | `board_init` | Re-centres the outline on every run. Any absolute coordinate written before it (planes regions, pre-route ops) goes silently wrong: a VBUS stub landed 0.3 mm off and shorted a GND pad. | Everything is in board coordinates and converted after board_init (`place.py`, `power.py`, `keepout.py`). Filed: an `--origin` option. |
| 23 | Freerouting determinism | The same board routes differently on every run (98-100 %, 0-6 leftovers). | The routing step is re-run and the cleanest result kept. Filed with #17. |
| 24 | P6 view | `render.py` is 3D only, so there's no quick 2D plot of placement for an agent to look at. | `kicad-cli pcb export pdf` + `pdftoppm`. Filed as a small feature. |
| 29 | `route_edit` | Adds tracks and vias without checking clearance to copper already on the board. A pre-routed B.Cu VBUS track landed on the CH224A's exposed-pad GND vias (added by planes_gen) and shorted, which only drc_routed caught, after a 15-minute route. | Re-routed that track clear of the EP. `fix_holes.py` also drops stitch vias that short or crowd another net. Filed with #17. |
| 25 | CH224Q DFN-10 | Not hwde's fault, but worth knowing: a 0.4 mm-pitch PD sink under a USB-C, with VBUS between CC1 and CFG1, can't be fanned out on 2 layers. | Swapped to the CH224A (same die, ESSOP-10 1.0 mm, same price and stock). |
| 26 | `route_edit` | Doesn't refill zones after its edits, so drc_routed then sees stale fills (zone-to-via clearance errors on a moved via). | Board-local `kicad/gen/refill.py` after each route_edit. Filed with #17. |
| 27 | `board_init` + `dfm` | The outline is drawn at a non-round origin (x = 22.864999). KiCad's Gerber writer then leaves the 2 mm corner arcs up to 0.05 um off the straight edges, and dfm calls the outline "not closed" and skips every copper/hole-to-edge check. | Board-local `kicad/gen/snap_outline.py` snaps the outline to 1 um. Filed with #22. |
| 28 | `bom_cpl` / `dfm` | EasyEDA answers HTTP 403 (rate limit) after ~20 model fetches, and the dfm gate fails until all CPL models are cached. | Retried every few minutes until `parts/easyeda/` was complete, then committed that cache so a check needs no fetch (hive-hwde worker 150527's advice). |

## Review-2 ECO (2026-10-29, hive-hwde c23e27e)

Since the first build, hwde fixed #1 (bin/hwde executable) and #4
(task_router full-run) in e225475; `build.sh` still calls `sh .../bin/hwde`,
which works either way. The schematic was regenerated on 27024a8's readable
layout (netlist unchanged). New snags met in the ECO, filed with the review's
tool briefs where they are hwde's:

| # | Where | Snag | What was done |
|---|---|---|---|
| 30 | `build.sh sch` | Regenerating the schematic rewrites `kicad/lite-main.kicad_pro` without its board rules and net classes. | The committed `.kicad_pro` is restored after `sch`. |
| 31 | `board_update --lib` | Takes the folder that holds `<lib>.pretty`, not the `.pretty` itself, and KiCad's stock libraries (`/hive/tools/kicad-10.0.6/share/kicad/footprints`) aren't on its path. | Passed both roots. |
| 32 | `route_auto` on a routed board | Freerouting printed "job started" and nothing else for 25 min (machine load ~40), twice; the KRT fallback then made the board worse (243 violations). | New nets routed with `kicad/gen/miniroute.py` (from core) and short hand tracks; the KRT result was discarded. |
| 33 | KiCad python | Iterating `GetTracks()`/`GraphicalItems()` after a `Remove()` raises `'SwigPyObject' object is not iterable`. | Collect first, remove after (`rip_at.py`, `silk_clear.py`). |
| 34 | `find_spots.py` (board-local) | `GetCourtyard()` is empty until `BuildCourtyardCaches()`. | Called first. |
