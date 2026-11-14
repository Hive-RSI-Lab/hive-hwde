# LEARNINGS

Non-obvious gotchas. CLOSED ARCHIVE since 2026-10-06: never append here - a new lesson is its own file in `learnings.d/` (format: `learnings.d/README.md`). Recall by tag/keyword across both before touching an area: `grep -rn '\[tag\]' LEARNINGS.md learnings.d/`.
Entries sourced from prior attempts are marked; re-verify at first use here. Renamed 2026-09-04: the skill is `/hwde` (tree `[path masked]`, env vars `HWDE_*`, the old `AIEE_*` spelling still read); entries below keep their original `ai-ee` wording. Entry line numbers are cited by `design/ladder-triage.md` and asserted by `tests/test_remediations.py`, so nothing above the first entry may gain or lose a line.

## Tags
[windows] [kicad] [kicad-cli] [ipc] [swig] [freerouting] [easyeda2kicad] [python] [prior-attempts] [geometry] [shapely] [parts] [datasheet] [gerber] [gerbonara] [dfm] [jlc] [fab] [skill] [git] [latex] [spice] [linux] [docker] [kicad-sch-api]

## 2026-07-06 [windows] cp1252 console crashes on non-ASCII output
Printing degree signs, ohms, plus-minus, emoji to a default Windows console raises
UnicodeEncodeError. Every script does `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
and keeps its own strings ASCII. (Source: ai-ee2, re-affirmed S0.)

## 2026-07-06 [windows] Python does not resolve MSYS /c/ paths
Bash redirects understand `/c/dev/...`; Python `open()` does not. Always pass `C:/dev/...`
style to Python. Bit prior attempts in bash-heredoc + `python -c` mixes. (Source: ai-ee2.)

## 2026-07-06 [kicad] File formats are not forward-compatible - pin ONE KiCad
A KiCad-10-format board is unreadable by kicad-cli 9 (exit 3). This host has 8.0/9.0.5/10.0.3
side by side. The whole pipeline resolves kicad-cli, bundled python, and templates through
`scripts/lib/env.py` (pinned: 10.0.3; AIEE_KICAD_CLI overrides). Never mix versions mid-pipeline.
(Source: ai-ee2 spike; enforced here at S0.)

## 2026-07-06 [kicad-cli] Host quirks: not on PATH; refill-zones is 10.x-only; pos defaults to inches
kicad-cli is NOT on PATH (full path via env.py). `pcb drc --refill-zones --save-board` exists on
10.0.3 but NOT 9.0.5 (S0-verified) - a reason KiCad 10 is the pin. From prior attempts (re-verify
at S2/S12): `pcb export pos` defaults to INCHES (pass `--units mm`); pos CSV headers are
Ref,Val,Package,PosX,PosY,Rot,Side with Side values top/bottom; ERC json nests violations
per-sheet while DRC's are flat.

## 2026-07-06 [ipc] No `kicad-cli api-server` in 9.0.5/10.0.3 - sandboxed-GUI IPC works instead
kipy 0.7.1 ships kipy/server.py driving `kicad-cli api-server`, but that subcommand exists in
neither installed KiCad (spec's "headless=True" claim corrected). S0-verified working path:
launch pcbnew.exe with KICAD_CONFIG_HOME pointed at a scratch config that sets
api.enable_server=true and seeds empty fp/sym-lib-tables (skips first-run dialogs) -> kipy
connects and reads the board; user's real config untouched. Automated in scripts/smoke_ipc.py.
A PCB editor window appears (fine on this desktop host). Headless alternative: SWIG bundled python.

## 2026-07-06 [swig] pcbnew lives in KiCad's BUNDLED bin/python.exe, verified on 9.0.5 and 10.0.3
The venv python has no pcbnew; the bundled one roundtrips CreateEmptyBoard/Save/LoadBoard and has
ExportSpecctraDSN + ImportSpecctraSES on both versions (S0-verified). Prior-attempt gotchas to
re-verify at first SWIG use (S9/S11): Specctra export WEDGES headless on a wx assert unless
suppressed (make wx.App, wx.DisableAsserts(), APP_ASSERT_SUPPRESS); bulk board.Remove() of
tracks/vias corrupts the process heap (do reads first, then save + fresh interpreter);
GetCourtyard() is empty until fp.BuildCourtyardCaches(); bundled python maps bare /tmp to C:\tmp.

## 2026-07-06 [freerouting] 2.2.4 needs Java 25 (not "21+"); batch flags are non-obvious
Java 24 fails with UnsupportedClassVersionError - portable Temurin 25 vendored at tools/jre/
(gitignored; check_env remediation has the re-fetch URL). Batch recipe from prior attempts
(re-verify at S11): `--gui.enabled=false -de in.dsn -do out.ses -mp <passes> -da`; `-da` is
mandatory or it phones home and can stall; `-h` not `--help` (--help launches the GUI). Do not
trust FR's success signal or internal DRC: parse the "(N unrouted)" completion line and gate on
kicad-cli DRC only.

## 2026-07-06 [easyeda2kicad] Imported footprints: missing courtyards + library defects + UA 403s
Prior attempts found easyeda2kicad imports lack courtyard geometry (courtyard-based checks
silently degrade) and can ship self-violating footprints (EP pad with no net, thermal drills
under min-hole). S6's fp_verify must check courtyard presence and a per-part DRC baseline.
EasyEDA endpoints 403 some default User-Agents (browser UA passes). Output layout:
`<base>.pretty/<name>.kicad_mod` + `<base>.kicad_sym`. (Source: both attempts; re-verify S6.)

## 2026-07-06 [python] skidl prints env-var warnings on import - keep JSON stdout clean
Importing skidl without KICAD*_SYMBOL_DIR set emits warnings; check_env redirects
stdout/stderr around package imports so its JSON contract holds. Do the same in any script
importing skidl/kicad_sch_api before emitting JSON.

## 2026-07-06 [python] skidl drops `<script>.log` / `<script>.erc` files in CWD on import
Merely importing skidl creates log/erc files named after the running script (or `skidl_REPL.*`
from `python -c`) in the current directory - the litter visible in AI-EE's repo root. Import
skidl with CWD pointed at scratch (`contextlib.chdir`, see check_env.py) or expect droppings;
gitignore patterns added as backstop. Applies to S7 schematic tooling.

## 2026-07-06 [prior-attempts] Deep routing/placement/fab gotchas archived in the old repos
C:/dev/ai-ee2/LEARNINGS.md and C:/dev/AI-EE/docs/LEARNINGS.md hold machine-verified findings
(2-layer GND-pour fragmentation, stitch-via keepouts, header pin-order routability lever, JLC
CPL rotation handling, wx/SWIG details). Consult them before S9-S12 sessions for FACTS ONLY -
do not port their architecture (user directive: this build follows SPEC.md fresh).

## 2026-07-11 [swig] pcbnew.ZONE_FILLER segfaults headless on Windows 10.0.3 - fill via kicad-cli
ZONE_FILLER(board).Fill(...) segfaults the bundled python headless (with or without the wx.App
+ DisableAsserts recipe). Working pattern (used by tests/golden/generators): build+save the board
UNFILLED via SWIG, then `kicad-cli pcb drc --refill-zones --save-board` fills and persists (fill
survives; a later plain DRC is clean). Also: board.Save() writes a DEFAULT .kicad_pro next to the
board, clobbering any custom one - write the project file AFTER pcb generation.

## 2026-07-11 [kicad] .kicad_pro is the DRC/ERC authority; keep it minimal and hand-rolled
With a .kicad_pro present, DRC rules come from the PROJECT, not the board's embedded setup
(pcb_build's ds.m_TrackMinWidth is ignored; KiCad's 0.2 mm default applied until
board.design_settings.rules.min_track_width went into the pro). rule_severities overrides
(lib_symbol_issues/lib_footprint_issues/footprint_link_issues/lib_symbol_mismatch: ignore) work
from a MINIMAL pro, but pasting the full default-shaped blob (defaults/net_settings/teardrops...)
made KiCad reject/ignore the overrides - warnings returned. Golden corpus: sch_build.write_project.

## 2026-07-11 [python] kicad-sch-api 0.5.6 quirks (verified against kicad-cli 10.0.3 ERC)
(wheel 0.5.6; its __version__ string lies "0.5.5"). Output opens clean in KiCad 10 (V2 evidence).
Gotchas: add_global_label() writes NOTHING to the file (silent no-op) - local labels only, so
root-sheet nets come out "/NAME" in netlists/boards; alphanumeric pin numbers get renumbered when
embedding symbols (USB_B_Micro shield "SH" -> "6") - sch_build._apply_pin_number_fixups repairs
the saved file; a pin touching a wire MID-SPAN does not connect (pins connect at wire ENDPOINTS
only - a PWR_FLAG placed mid-wire ERCs as unconnected); component anchors snap to the 1.27 mm
grid, so off-grid positions silently shift symbols and wires miss pins.

## 2026-07-11 [python] kiutils needs encoding="utf-8" passed explicitly on Windows
SymbolLib/Board/Footprint.from_file default to the locale codec (cp1252) and crash on official
libs containing non-ASCII (RF.kicad_sym). Also kiutils pad size.X/Y are pre-rotation values -
do not derive board-space bounding boxes from them (SMA edge-mount pads are 5.08 mm along X;
kiutils showed 1.5x5.08). Use pcbnew pad dumps (generators/pcb_build.py --pads-out) for geometry.

## 2026-07-11 [kicad] KiCad 10 board files store nets by NAME in segments/vias - mutation surgery
`(net "GND")` (not numeric ids) appears in segment/via/pad blocks, so deterministic text surgery
keyed on coordinates+netnames works (tests/golden/mutations/mutlib.py: exact-match asserts, fixed
UUIDs for added items, refill via kicad-cli after). Double-run byte-identical verified for all 7
mutants. Mutants must be re-run whenever goldens regenerate (gen.py assigns fresh UUIDs).

## 2026-07-11 [kicad-cli] Misc 10.0.3 flags: render uses --width/--height; ERC exit = count
`pcb render` wants --width/--height (-w exists but -h collides with help; use long forms) plus
--quality high. With --exit-code-violations the exit CODE equals the violation count (saw exit 5
= 5 violations) - treat nonzero as "has violations", never as errno. `sch export netlist --format
kicadsexpr` emits multiline-formatted s-exprs (regex across lines, see gen.py parse_netlist).

## 2026-07-11 [kicad-cli][windows] A stale persistent AIEE_KICAD_CLI silently pins the WRONG KiCad
S2 start: env.find_kicad_cli() resolved 9.0.5, not the 10.0.3 pin, because a User-level (registry)
`AIEE_KICAD_CLI` env var pointed at 9.0 - it takes precedence over env.py's 10.x preference (by
design: the documented "flip the pin" override). But 9.0 CANNOT load 10-format goldens (version
20260206): kicad-cli exits 3 "Failed to load board" and writes NO json report, so any wrapper that
reads the report file dies on FileNotFoundError. Removed it via
`[Environment]::SetEnvironmentVariable("AIEE_KICAD_CLI",$null,"User")` (old value
C:\Program Files\KiCad\9.0\bin\kicad-cli.exe). NOTE: a persistent User var is INHERITED by an
already-running shell - deleting the registry value does not clear it from the current process;
`unset AIEE_KICAD_CLI` per shell too. If goldens suddenly "fail to load", suspect this first.

## 2026-07-11 [kicad-cli] DRC/ERC JSON: layer/net/refdes are embedded in item description strings
kicad-cli violation objects are `{type, severity, description, items:[{description, pos{x,y},
uuid}]}`. There are NO separate layer/net/ref fields - they live in the item description text:
"Pad 1 [GND] of D1 on F.Cu" -> net in [brackets], layer after " on ", refdes = [A-Z]{1,4}\d+ (strip
[brackets] FIRST or bracketed pin names like [PB0] get mis-read as refdes). DRC report has three
parallel sections that share this shape and must be merged: violations + unconnected_items +
schematic_parity; ERC nests per sheet under sheets[].violations. `--severity-all` is required or
warnings are omitted. Normalizer + parsers: scripts/kc.py (parse_drc_data / parse_erc_data).

## 2026-07-11 [kicad] cpl-rotation mutant does NOT fail --schematic-parity (manifest note is wrong)
S2 spot-check: `pcb drc --schematic-parity` on the committed tests/golden/mutants/cpl-rotation board
reports 0 violations under 10.0.3, contradicting manifest.yaml's note ("intentionally fails
--schematic-parity too"). The board IS mutated (test_golden proves it differs from golden), and its
DESIGNATED catcher is dfm_check (S12, CPL polarity), not parity - so this is a stale secondary claim,
not a broken fixture. S12 must catch it via CPL/polarity validation, NOT lean on parity. Registered
as verify-later V9.

## 2026-07-11 [geometry][kicad] Pad absolute geometry: center = fp + R(-fp_angle).local; shape angle absolute
S3-verified against pcbnew on usbbuck4/rf4 (10.0.3): in the .kicad_pcb, a pad's `(at lx ly a)` gives
lx/ly RELATIVE to the footprint anchor (pre-rotation) and `a` is the pad's ABSOLUTE board-frame angle
(already includes footprint orientation - a pad drawn at 0 in a fp rotated 90 is saved with pad angle
90). Absolute center = fp_pos + R(-fp_angle).(lx,ly) where R is the standard CCW matrix (KiCad rotates
CW for positive angle because Y points down). Probe: C10 fp(104.8,105.5,90deg), pad1 local(-0.95,0) ->
pcbnew GetPosition (104.8,106.45) = R(-90).(-0.95,0)+fp. shapely: affinity.rotate(pt,-fp_angle,origin=0).
All corpus pad shapes (rect/roundrect/oval/circle) are axis-symmetric, so the SHAPE rotation SIGN does
not change the covered region (rect(+t)==rect(-t)) - only the center convention matters, and it is
locked. Non-symmetric pads (trapezoid/custom) are absent from the corpus; validate their shape-rotation
sign against the pcbnew oracle when first encountered. No FLIPPED footprints in the corpus either (the
B.Cu pads are J1 SMA edge tabs + J2 thru-hole header on non-flipped fps); flip = mirror local x + swap
F/B, implemented but corpus-unvalidated.

## 2026-07-11 [geometry][kicad] KiCad-10 .kicad_pcb: net refs by NAME, no numeric net table; zone fills are keyhole rings
Copper items reference nets as `(net "NAME")` (name only, no number) - there is NO `(net N "name")` table
in these SWIG-saved boards. geom.py resolves a net node by taking its trailing string, falling back to a
number->name table if present (robust to both KiCad formats). Zone fill = one or more `(filled_polygon
(layer L) (pts ...))` blocks; holes are stitched into the outer ring via zero-width keyhole slits, so
`shapely.Polygon(pts).area` gives the correct filled area directly (validated vs zone.GetFilledArea).
Multi-layer zones emit one filled_polygon per layer (group by the block's own `(layer)` tag, authoritative).
An unfilled zone has an outline but zero filled_polygon blocks -> geom flags it (freshness gate).

## 2026-07-11 [geometry][kicad] FLIP is baked into the file (parser must NOT mirror); keepouts never fill
S3 Fable review, verified vs pcbnew on a SWIG-flipped usbbuck4 (C10 rot-90 + J1 USB micro: 15 copper
pads incl. asymmetric x and duplicate "SH" numbers - all exact): pcbnew Flip() REWRITES the footprint
block - pad local coords come out mirrored, angles negated (90 -> 270, fp 90 -> -90), pad (layers)
renamed to B.* - so the SAME abs = fp_pos + R(-fp_angle).local formula covers front AND back parts,
and pad copper layers are read literally. Any parser-side mirror or F/B swap DOUBLE-flips (the bug
geom.py shipped with earlier today; fixed + regression-tested same day). CORRECTS the "flip = mirror
local x + swap F/B" guess in the earlier [geometry] entry above. Also: rule areas
`(zone ... (keepout ...))` NEVER carry filled_polygon blocks - any zone-freshness gate must exclude
them or it hard-fails on every board with a keepout (the plane-split mutant, S4's PRIMARY
check_return_path fixture, has exactly one; geom.assert_fresh() excludes rule areas and exposes them
as BoardGeom.rule_areas metadata instead). Duplicate pad numbers in one footprint are legal (J1 has
3x thru + 4x smd "SH") - never key pad collections by number alone.

## 2026-07-11 [shapely] Per-net copper = union of tracks(buffered)/pads/vias/zone-fills; oracle via TransformShapeToPolygon
S3 round-trip: build per-(net,layer) copper as unary_union of track LineString.buffer(w/2,round),
via Point.buffer(size/2), pad shape polys, and zone filled polys, then `.area` (mm^2; file is already mm).
Ground truth = pcbnew: for the same net/layer, BooleanAdd each track/pad/via TransformShapeToPolygon +
zone.GetFilledPolysList(layer) into a SHAPE_POLY_SET, Simplify, `.Area()`/1e12. Both sides union the SAME
KiCad primitives so they agree tightly on big nets (GND pours <0.5%); tiny signal nets differ more in %
(pad-corner faceting) but negligibly in absolute mm^2 - test tolerances are relative-with-absolute-floor.

## 2026-07-11 [geometry][shapely] Spec-literal return-path corridors FP on every legit board - 3 artifact classes
S4: buffering the centerline by k*w (spec 6.3 step 3) and differencing the reference fill flags
all three CLEAN goldens. The artifact classes and the shipped fixes (check_return_path.py):
(1) round end-caps poke past the landing pad into neighbouring clearance channels -> flat-capped
chain buffers (corridor ends where the trace ends); (2) the net's OWN via/thru-pad punches the
plane (annular clearance void at every legal transition; containment-in-disk waivers fail at the
CORNERS - deficit bits sit just outside any disk); (3) a lone other-net via antipad nicking the
corridor (usbbuck4 golden: VBUS via 0.65 mm from USB_DM = 0.94 mm centerline crossing - benign at
FS, board is clean by design). Fix for 2+3: EXCISE disks (item_r + 0.65 zone clearance) around
every via punching the ref layer + own/refnet thru pads from the deficit, then judge the REMAINDER
(>= 0.05 mm2); slots/moats survive excision because they are not at vias (rf4 slot mutant still
caught at full 1.4 mm crossing). Severity = centerline crossing (error) vs corridor-edge nick
(warning). Other-net THT pad FIELDS deliberately not excised (connector row under a clock is real).

## 2026-07-11 [shapely] linemerge() raises ValueError on a bare LineString input
shapely 2.1.2 ops.linemerge accepts MultiLineString/sequences but raises "Cannot linemerge
LINESTRING(...)" when unary_union of one segment collapses to a single LineString. Guard:
`merged = linemerge(u) if u.geom_type == "MultiLineString" else u`. Bit check_return_path on
single-segment synthetic boards (corpus boards always had multi-segment nets, so it hid).

## 2026-07-22 [kicad-cli][drc] Custom .kicad_dru IS auto-loaded by kicad-cli; rule name is in the description
S8-verified (10.0.3): `kicad-cli pcb drc` reads `<board-basename>.kicad_dru` sitting next to the
board automatically (no flag to point at it). A violated custom rule reports type = the constraint
kind (e.g. "track_width") and description = "Track width (rule 'NAME' min width X; actual Y)" - the
rule NAME is embedded in the description string, so kc.py's normalizer surfaces it in `msg` (parse
`rule '([^']+)'`). This is the enforcement backbone for rules_gen (SPEC P5): any width/clearance/
via/diff-pair minimum expressible as a rule becomes a named DRC violation for free.

## 2026-07-22 [kicad-cli][drc] DRU condition token is A.NetName (A.Net silently matches nothing); LATER rule wins
S8-verified: in a `(condition ...)` the net-name property is `A.NetName == 'GND'` (fired on 20 tracks).
`A.Net == 'GND'` evaluates FALSE silently (0 matches, no error) - a wrong token degrades to "rule never
fires", never an error, so ALWAYS smoke a new condition against a board you know violates it. `A.NetClass
== 'Default'` works (all nets are Default when net_settings is null/empty). When TWO rules of the same
constraint type match one item, the LATER rule in file order wins (a broad `A.NetClass=='Default'` rule
placed after a specific `A.NetName=='GND'` rule overrode it - GND showed 0, default showed all 72).
=> rules_gen must emit GENERIC/baseline rules FIRST and SPECIFIC per-net/per-class rules LAST so specifics win.

## 2026-07-22 [swig][kicad] Headless netlist->board import: SWIG place from netlist = parity-clean; bbox-pack to avoid density DRC
S8-verified: kicad-cli has NO netlist->board path (`pcb import` is for non-KiCad formats only). Working
headless import mirrors pcb_build.py: bundled-python SWIG loads each footprint (FootprintLoad from
share/kicad/footprints/<lib>.pretty), assigns pad nets from the netlist netmap {REF.PAD:net}, saves
unfilled. `kicad-cli pcb drc --schematic-parity` then reports 0 parity issues (proves the import is
correct) with only unconnected_items (expected - unrouted). Footprints placed too close trigger
courtyards_overlap/shorting_items/solder_mask_bridge/silk_over_copper: pack them on a shelf grid by
GetBoundingBox(False,False) (needs BuildCourtyardCaches() first) + margin => 0 non-parity violations.
board_init acceptance = parity==0 AND (violations - unconnected_items)==0. Bundled python has NO yaml -
board_init runs in venv (yaml+sexpdata) and shells the SWIG worker a JSON job (bundled python has json).

## 2026-07-22 [kicad][swig][geometry] Stackup: SWIG can't build it - inject the (stackup) block as text
S8: `ds.GetStackupDescriptor().BuildDefaultStackupList` is absent on the 10.0.3 SWIG wrapper
('SwigPyObject' has no attribute) - confirms S1's "stackup didn't serialize". Working path: after the
SWIG save, TEXT-inject a `(stackup ...)` block right after `(setup\n` in the .kicad_pcb, built from
stackups.yaml. geom._build_stackup reads it (assumed=False, source "board (stackup)") and kicad-cli DRC
still loads the board. geom's copper-to-copper walk ignores the outer silk/paste/mask layers correctly
(they sit before the first / after the last copper), so a full realistic block parses to the right
per-gap dielectric heights + epsilon_r. Layer names must match the board's copper layers exactly.

## 2026-07-22 [parts][easyeda2kicad] Pinned easyeda2kicad 1.0.1 wraps an ANONYMOUS JLCPCB parts search (resolves V5)
`from easyeda2kicad.easyeda.easyeda_api import EasyedaApi; EasyedaApi().search_jlcpcb_components(
keyword, page=1, page_size=N, part_type="base"|"expand")` hits JLCPCB's public parts endpoint
(`jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList`) with NO
credential and returns `{total, results:[{lcsc, name, model, brand, package, category, stock,
type: Basic|Extended, price, price_breaks, min_qty, reel_qty, description, url, datasheet,
attributes:[{name,value}]}]}`. Verified live from this host 2026-07-22: "100nF 0603 X7R" -> total
18783, C14663 Basic stock 88M. This is parts_search.py's PRIMARY path - the spec's "credentialed
JLCPCB Parts API" (V5, still needs an access application) is NOT required. jlcparts SQLite is an
optional local cache (none present on this host); web-search is the agent-level last resort. Also
`EasyedaApi().get_cad_data_of_component(lcsc)` pulls raw CAD JSON. Endpoint could change/rate-limit
- network tests are `net`-marked; parts_search exits 2 with remediation when the live call fails
and no --db cache is given.

## 2026-07-22 [easyeda2kicad][kicad-cli] Full export layout + LEGACY footprint format that still loads in KiCad 10
`python -m easyeda2kicad --lcsc_id=Cxxxx --full --output <base>` writes THREE artifacts:
`<base>.kicad_sym`, `<base>.pretty/<fpname>.kicad_mod`, `<base>.3dshapes/<name>.{wrl,step}` (the
fp/sym names come from EasyEDA, e.g. C1525 0402 cap -> footprint "C0402"). Footprints come out in
the KiCad-5 LEGACY format: `(module NAME (layer F.Cu) (tedit ..) ... (pad N smd rect (at x y rot)
(size w h) (layers F.Cu F.Paste F.Mask)))` - top token is `module` not `footprint`, layer tokens
are UNQUOTED. They still load in KiCad 10: `kicad-cli fp upgrade <lib.pretty>` rewrites them to
`(footprint "NAME" ...)`, and `kicad-cli fp export svg` renders them. So fp_verify must parse BOTH
`(module ...)` and `(footprint ...)` (unquoted AND quoted layer tokens). Courtyard presence is
PART-DEPENDENT (C1525 0402 HAS an F.CrtYd rect; the old [easyeda2kicad] entry's "missing
courtyards" is not universal) - fp_verify checks for it and warns if absent, never assumes.

## 2026-07-22 [kicad-cli] `fp export svg <lib.pretty> --output <dir>` needs the output dir to EXIST first
It prints "Plotting footprint ..." then "Failed to create file ... Error creating svg file" and
writes nothing if <dir> is missing (the overall exit is still success-ish). mkdir the output dir
before calling. With the dir present it writes one `<fpname>.svg` per footprint. (Used as the
"footprint loads in KiCad" gate for lib_pull / fp_verify.)

## 2026-07-22 [datasheet][python] No PDF lib was in the venv; added pypdf 6.14.2 (pure-python)
S6 needs PDF text extraction for datasheet_extract.py; none of pypdf/pdfplumber/PyMuPDF/pdfminer
were installed. Added `pypdf==6.14.2` (pure-python wheel, no native deps, imports clean on 3.13) to
requirements.txt/lock. `from pypdf import PdfReader; PdfReader(path).pages[i].extract_text()` works
on text-based PDFs (image-only datasheets yield empty text - the LLM agent reads those from the PDF
pages directly). A minimal valid one-page text PDF can be hand-assembled in ~15 lines for a
hermetic fixture (see tests/fixtures/parts builder) - no authoring lib needed.

## 2026-07-22 [geometry][kicad] KiCad 10 stores refdes/value as (property ...); fp_text/property angle is ABSOLUTE
S5 check_silk: reference designators and values are NOT fp_text in KiCad-10 .kicad_pcb - they are
`(property "Reference" "U1" (at lx ly angle) (layer "F.SilkS") (effects ...))` inside the footprint
(there are also separate fp_text user items). A silk-over-pad checker that only reads fp_text sees
1 text per board (the top-level gr_text label) and misses all 17-24 refdes. Parse `property` nodes
too (text = the 2nd string). CRUCIAL: the stored text `angle` is ABSOLUTE board-frame (already
includes the footprint rotation), exactly like pad angles ([geometry] entry above) - do NOT add the
footprint rotation. Adding it swung a vertical refdes (rf4 C15, fp rot -90, text angle 270)
horizontal across its own pads -> false positive. Position IS local (transform by fp_pos +
R(-fp_angle).local); only the angle is pre-baked. (hide) detection: `(hide yes)` hides, `(hide no)`
does not - test the value, not mere presence.

## 2026-07-22 [geometry][kicad] KiCad text bounding box, calibrated vs pcbnew GetBoundingBox (10.0.3)
For silk-over-pad geometry, KiCad's PCB_TEXT.GetBoundingBox at size s (mm), stroke thickness t:
height ~= 1.6*s + t (glyph-independent); width is PROPORTIONAL/per-glyph (W~1.39*s, M~1.23, digits
& caps ~0.94-1.03, i~0.56). A per-char advance of 1.0*s + t is a good average box. Golden refdes
clear pads by as little as 0.10 mm (bbox), so a silk-over-pad rule keyed on bbox-touch false-positives;
use "pad CENTER inside the silk box OR silk covers >=50% of the pad" instead - robust to +/-0.1 mm
box error and still catches centered text (the mutant). Measure with the bundled pcbnew, not a font lib.

## 2026-07-22 [shapely][geometry] Diff-pair "skew" mutant is a COUPLING defect, not a length mismatch
S5 check_diffpair: the diffpair-skew mutant adds a 6.2 mm meander to the SHORTER net (USB_DM), so raw
total-length skew goes DOWN (golden |DP-DM|=5.55 mm -> mutant 0.65 mm) - a length-match check both
FALSE-POSITIVES on the clean golden (5.55>5) and MISSES the mutant. The real signature is UNCOUPLED
LENGTH (run of one trace whose partner is >~3x the nominal gap away): golden 2.48 mm, mutant 12.96 mm
- clean separation. Manifest "golden ~2.5 mm skew" == golden uncoupled length, confirming intent.
Also: length skew must be measured on the BRANCH-FREE trunk (segment-graph shortest path between the
two matched pad terminals), else a USB D+ pull-up stub (R3, +3.1 mm) inflates DP and breaks the match;
a stub joins mid-segment so it lands in its own graph component and Dijkstra naturally excludes it.
Report gap deviation as facts only (never gate on it): a legit pair fans out to >nominal gap at its
pad breakouts. Guard empties: an unrouted half -> shapely distance to an empty geom is NaN (invalid
JSON + silently disables the check); a 1-copper-layer board -> epsilon_between divides by zero.

## 2026-07-22 [python] kicad-sch-api 0.5.6 hierarchical sheets DO serialize (global labels still do not)
S7: add_sheet / add_sheet_pin / add_hierarchical_label all write to the saved file and ERC/netlist
correctly under kicad-cli 10.0.3 (S1 only proved the flat path; add_global_label remains a silent
no-op). API shape: sheets come back as plain DICTS (sch.sheets.get_sheet_by_name(name)["pins"]);
sheet-pin positions are ABSOLUTE, with left-edge position_along_edge measured from the sheet BOTTOM.
Hierarchy net-name semantics: power SYMBOLS make a net global across all sheets (no sheet pin
needed); a child net merged with the root via a sheet pin takes the ROOT-side name ("/VIN");
child-internal nets become "/<sheet>/NAME". Consequence: wiring labels are sheet-local, so metadata
recording "exact board net names" (decoupling rail) must carry the FINAL name - schlib.py's
rail_net/gnd_net override exists for this, and netlist_audit.py --decoupling catches the drift.

## 2026-07-22 [python][erc] Labels attach anywhere ALONG a wire; every wire endpoint needs a pin or label
A label whose anchor lands on a foreign wire RUN merges the two nets (ERC multiple_net_names plus a
real netlist short): hierdemo's cap row initially put a vertical cap stub through the IC's GND label
anchor -> GND merged with +3V3. Pins connect at wire ENDPOINTS only, but labels bind mid-span too -
generated placement must keep label anchors off foreign wires (schlib caps_at is caller-guaranteed
free area). Conversely a wire ENDPOINT carrying neither pin nor label warns unconnected_wire_endpoint;
a label ON the endpoint terminates it legally (flag-only rail clusters label the start endpoint).

## 2026-07-22 [kicad-cli] Netlist export facts (10.0.3, kicadsexpr)
Netlists are MULTILINE pretty-printed - parse with sexpdata, not line regexes. Every NC/unconnected
pin appears as a singleton net "unconnected-(REF-PINNAME-PadN)" with a "+no_connect" pintype suffix
(blinky2: 32 of 43 nets). Power symbols and PWR_FLAG (#PWR/#FLG refs) are EXCLUDED from netlist
nodes entirely, so "is this rail driven" is NOT answerable from the netlist when the driver is a
flag or connector - ERC owns that check; netlist_audit.py warns on undeclared power_in feeders
instead of driver presence.

## 2026-07-22 [ipc] Sandboxed-GUI IPC REGRESSED on this host: "KiCad is not ready to reply" (V8 evidence)
S9: the S0-verified gui-sandboxed path (scripts/smoke_ipc.py, unchanged) now returns verdict
`unavailable` - pcbnew.exe launches, the kipy socket answers, but every request within the 45 s
window errors "ApiError: KiCad returned error: KiCad is not ready to reply" (a custom V8 probe
opening a real golden also never connected in 60 s). kipy 0.7.1 edit-op coverage (move/rotate via
update_items - the API exists: FootprintInstance.position/.orientation setters + b.update_items +
b.save) is therefore UNVERIFIABLE at this pin, and library research confirms headless kipy only
arrives with KiCad 11. S9 decision: place_edit.py drives SWIG bundled python (headless, proven at
S8); kipy/IPC is the KiCad-11 migration target. If a future session needs the GUI-IPC path, debug
with pcbnew stdout/stderr UNsuppressed first (a modal dialog is invisible under DEVNULL).

## 2026-07-22 [swig] Placement-edit API facts on 10.0.3 (place_swig.py, live-verified)
FindFootprintByReference/SetOrientationDegrees/SetPosition/Flip/SetLocked all exist and work
headless. SetOrientationDegrees NORMALIZES to (-180, 180]: setting 270 stores -90 in the file and
GetOrientationDegrees returns -90 - always compare angles mod 360 (place_edit._angdiff), never
literally. pcbnew.FLIP_DIRECTION_LEFTRIGHT does NOT exist on the 10.0.3 SWIG wrapper - use the
legacy bool: fp.Flip(fp.GetPosition(), True) (flip_fixture.py's fallback branch is the ONLY branch
here). mm->IU: pcbnew.pcbIUScale.mmToIU(32.3) == 32300000 (correct rounding) while
pcbnew.FromMM(32.3) == 32299999 (float truncation, prior-attempt fact re-verified) - workers that
write coordinates must use mmToIU. board.Save() writes sibling project files; place_edit sidesteps
any clobber by staging in a scratch dir and os.replace()-ing ONLY the .kicad_pcb back (the real
.kicad_pro is test-guarded byte-identical across edits).

## 2026-07-22 [geometry][placement] Courtyard containment needs edge-part exemptions (corpus-calibrated)
Two legality-gate false-positive classes on legitimate boards: (1) edge-mount connectors - the rf4
SMA clamps the board edge and keeps only 35% of its courtyard on-board (usbbuck4's USB micro also
overhangs) -> declared-edge parts get a >=25% on-board threshold (placelib.ON_BOARD_MIN), not full
containment, and EDGE_TOL 2.5 mm accepts the golden's ~2 mm-inboard THT headers; (2) board_init's
corner mounting holes (board_only) sit with courtyards kissing/crossing the outline by construction
-> non-movable footprints are exempt from outline containment (they stay overlap obstacles).
Golden refdes/angle probe C10 (fp 104.8,105.5 rot90, pad local -0.95,0 -> abs 104.8,106.45) is
pinned as placelib's transform regression test.

## 2026-07-22 [placement][python] Spring embedding: classic F-R attraction is d^2/k - linear-in-d starves it
place_seed's first cut used attraction ~ w*d/k against repulsion k^2/d; repulsion dominated at
board scale (k = sqrt(area/n) ~ 14 mm), exploding singletons/crystal cluster to the interior
corners - seed HPWL came out WORSE than the S8 shelf pack (948 vs 832 mm). Classic
Fruchterman-Reingold attraction w*d^2/k fixed it in place: seed HPWL 484 mm vs golden hand
placement 452 mm (within 7%), crystal + load caps adjacent to the MCU, decoupler Manhattan
2.5-6.6 mm. Render and EYEBALL the seed (kicad-cli render) whenever touching the force model -
the legality gate cannot see "legal but scattered".

## 2026-07-23 [placement][python] SA stall detection must not count the hot phase
place_anneal's first schedule counted "epochs without a new best" unconditionally: at high T the
walk sits far above the incumbent best by design, so stall=15 killed the run at epoch 15 while
acceptance was still ~75% - 17.1% HPWL improvement instead of 43.4% on the same seed. Fix: the
stall counter only ticks when the epoch acceptance ratio is < 0.2 (cold regime); hot epochs reset
it. Companion facts that worked as-is: T0 = 20x mean uphill delta from ~50 sampled moves,
TimberWolf-style window W *= (0.56 + alpha), cooling 0.6/0.9/0.95/0.9/0.75 by acceptance band.
Perf on usbbuck4 (13 movable clusters, 46 nets): ~0.8 ms/move with incremental cost (per-net HPWL
rebuild, moved-cluster-only shapely overlap with bbox prefilter, per-net MST + crossing recount,
congestion cell diffs); full_sync() every epoch kills float drift (invariant pinned by test).

## 2026-07-23 [swig][freerouting] S11 DSN/SES roundtrip verified on 10.0.3 (V1/V7/V3 resolved)
ExportSpecctraDSN(board, path) and ImportSpecctraSES(board, path) (two-arg forms) work headless
from the bundled python WITH the wx recipe (wx.App() + wx.DisableAsserts() +
app.SetAssertMode(wx.APP_ASSERT_SUPPRESS)); wx sprays "Adding duplicate image handler" lines to
stdout, so SWIG workers must return results via a FILE (route_swig.py's contract), never stdout.
A FILLED zone exports as "(plane NET ...)" and Freerouting connects pads to it ITSELF, including
dropping new stitch vias to reach a back-side plane (blinky2 smoke: 26 vias with plane vs 7
without). Post-SES the pre-existing fills are STALE (33 clearance/hole violations on the smoke
board); `kicad-cli pcb drc --refill-zones --save-board` clears them (0 after). SES import only
ADDS copper; ImportSpecctraSES on an already-routed board would re-add echoed guide wires as
duplicates - route_auto never imports into a board that already got that session's copper.

## 2026-07-23 [freerouting] 2.2.4 verified flags + completion parse (this host, our own DSN)
`--gui.enabled=false -mt 1 -is sequential -da --logging.file.enabled=false -mp N -de in.dsn -do
out.ses` exits 0 and is the deterministic set (-mt >=2 has a known clearance bug + nondeterminism;
-is sequential removes item-order randomness). -da does NOT disable the update check (an outbound
"New version available" call still happens and must be tolerated offline); it disables analytics.
Completion parse priority: session line "final score: S (N unrouted)" > LAST pass line "(N
unrouted)" > a pass line WITHOUT the parenthetical means 0 unrouted. FR's own success flag and
internal DRC stay untrusted - kicad-cli DRC is the gate (routelib.parse_fr_log pins all of this).

## 2026-07-23 [parts][python] KRT (KiCadRoutingTools 0.19.0) vendored: no-KiCad headless router
tools/krt/KiCadRoutingTools-0.19.0/plugins (MIT, pinned zip + prebuilt abi3 grid_router pyd -
imports clean in venv python 3.13; sha256s in tools/krt/PROVENANCE.txt; env.find_krt() resolves).
Facts: scripts must run with cwd=plugins (relative sys.path inserts); pass args as a LIST (net
names like "/USB_DP" get MSYS-path-mangled through a shell); ALWAYS pass --no-fix-drc-settings
(route.py silently rewrites the sibling .kicad_pro DRC floors); route to a FRESH output path per
attempt; results = last "JSON_SUMMARY: {...}" stdout line; output uuids are uuid4 (reruns never
byte-identical - compare parsed state); scipy is a hard dependency (added scipy==1.18.0 to the
venv pins, S6-pypdf precedent).

## 2026-07-23 [freerouting][routing] FR 2.2.4 DSN reader can WEDGE on KRT guide-wire copper - detect + fall back
Boards carrying KRT-routed copper (either power net's tracks+vias) drove Freerouting into
INFINITE RECURSION at app.freerouting.board.PolylineTrace.combine while READING the design
(before pass 1); the same board with all routed copper stripped routes in 2.8 s. Signature:
rung process timeout with ZERO parsed pass lines. route_auto's mitigation (S11-verified): skip
the remaining ladder rungs (same DSN, same wedge) and fall back to KRT for the whole remainder
(batched route.py --nets over the DRC-unconnected nets, kept only when the DRC strictly
improves). Related Windows fact: killing the parent shell does NOT kill the FR JVM - orphaned
java.exe grinds forever; kill the process tree explicitly when cancelling.

## 2026-07-23 [routing][placement] P7 chain order is board-class dependent; plane nets are never outer-trunked
2-layer: route_critical -> route_auto -> stitch -> repair -> cleanup (pre-route stitch vias are
FR obstacles - prior-attempt fact confirmed). 4-layer: route_critical -> stitch -> route_auto
(stitch pre-connects SMD pads to inner planes; FR's remaining work shrinks ~40%). A power net
CARRIED BY A PLANE must not be trunk-routed on outer layers: the trunk's vias fragment the
plane locally (live: a +3V3 outer trunk starved J2's thermal spokes onto an In2 island -
error starved_thermal). route_critical skips plane-carried nets (plane IS the trunk).

## 2026-07-23 [stitch][geometry] Via-candidate obstacles = WIRED copper only; single-layer-bond vias dangle
Foreign ZONE FILLS must not block via candidates (fills re-flow at refill - a via through a
foreign plane gets a legal antipad): treating fills as obstacles rejected 40/40 candidates on
the 4-layer board (In1/In2 planes cover everything). Only foreign tracks/pads/vias block, plus
rule-area keepouts and the 0.5 mm hole-to-hole floor (net-agnostic - same-net drills count).
Separately, KiCad flags via_dangling for a via bonded on only ONE layer even when it sits in a
plane fill - grid/area stitch vias must verify same-net copper contact on >= 2 layers.

## 2026-07-23 [kicad-cli][drc][zones] zones_intersect fires on same-net same-priority overlaps
KiCad 10 DRC rejects overlapping zones of the SAME net at the SAME priority - a plane generator
re-run that duplicates a pour breaks DRC. planes_gen guards with an existing-fill coverage skip
(>= 80% covered -> no-op "existing") and assigns distinct priorities to any same-layer
overlapping pair (island-in-pour needs the island HIGHER).

## 2026-07-23 [placement][routing] Courtyard-only packing is silk-blind; KRT leaves sub-grid crumbs
place_anneal's tight packing (courtyard legality only) put refdes silk over neighbour pads -
12 silk DRC warnings that fail the err+warn drc_routed gate; the SEEDED placement is silk-clean.
Until placement/fixers are silk-aware (P6 stage-3 agent / P8 silk fixer, S13), route from the
seed. Also: KRT output can contain sub-0.05 mm segments that KiCad flags track_dangling but sit
below route_cleanup's touch tolerance - route_auto's KRT pass sweeps them (removal is
connectivity-safe: neighbouring round caps overlap far beyond the crumb length). And
route_cleanup's loop-breaking can remove a load-bearing segment when connectivity runs through
a plane (union-find/fill edge case) - it SELF-DETECTS (exit 1 cleanup_regression, board left
modified); the orchestrator restores (git or snapshot) and continues without cleanup.

## 2026-07-24 [gerbonara][gerber][geometry] ArcPoly.outline drops curvature - tessellate arcs before shapely
gerbonara's `Flash.to_primitives('mm')[0].to_arc_poly()` returns an ArcPoly whose `.outline` holds
only the segment ENDPOINTS; the curvature lives in its arc segments (`.segments` / `.arc_centers`).
Feeding `.outline` straight into `shapely.Polygon` therefore turns a ROUND pad into a coarse polygon
(measured on our own exports: a 0.6 mm via pad read as ~0.55 mm inscribed, i.e. a phantom 0.025 mm
annular-ring shortfall that failed all 25 blinky2 vias). Fix: `.approximate_arcs(max_error=1e-3)`
BEFORE `.outline` (gerblib.ARC_MAX_ERROR_MM). Even then, keep a geometric tolerance in the
comparisons - blinky2's vias sit EXACTLY on JLC's 0.15 mm annular floor, so micron-scale flattening
error must not fail them (dfm_check.GEOM_TOL_MM = 2e-3, two orders below any real DFM delta).

## 2026-07-24 [dfm][gerber] Annular ring must be measured against PAD FLASHES only, never pours
A zone fill exports as a Region whose exterior is a keyhole ring threading past every via's antipad
(the LEARNINGS [geometry] keyhole entry). Counting pours as "the copper around the hole" therefore
measures the ANTIPAD GAP, not the ring, and invents a ~1 mil shortfall on perfectly good vias
(blinky2: one hole reported 0.1245 mm). dfm_check.check_annular_ring considers `lg.pads` (flashes)
on the OUTER layers only; a hole with no flash around it (NPTH, inner-only) is skipped, not failed.

## 2026-07-24 [dfm][kicad] CPL polarity: compare per PAD NUMBER, not per net (this is what catches V9)
`pcb drc --schematic-parity` compares NET MEMBERSHIP, so a polarized part rotated 180 deg with its
pad nets swapped is invisible to it (confirmed 10.0.3, the cpl-rotation mutant - V9 resolved S12).
Comparing the board's `pad number -> net` against the netlist's `pin -> net` catches it immediately,
and the pad GEOMETRY yields the misorientation: find the pad actually carrying each expected net,
take the angle from the footprint's pad centroid to that pad vs the pad that should hold it; a
consistent delta (spread < 5 deg) is the rotation the CPL would ship (D1 -> exactly 180.0). A
mismatch with no consistent permutation is a wiring error (`pad_net_mismatch`), not a rotation.

## 2026-07-24 [dfm][gerber] Clearance from gerbers = gaps between UNIONED copper islands
Gerbers carry no net data, so the only honest grouping is "touching copper is one conductor" - union
the layer, explode into components, measure pairwise gaps. That is exactly what a fab's DFM engine
sees. Two guards are required: skip distances <= 1e-4 mm (numerically-abutting features that are
really connected, else every trace/pad junction is a phantom zero-clearance short), and prefilter
pairs with an STRtree or the comparison is quadratic over hundreds of islands.

## 2026-07-24 [jlc][kicad-cli][fab] JLC export facts (10.0.3, verified on our goldens)
`pcb export gerbers` with no `--layers` emits 25 files incl. Adhesive/Courtyard/Fab/User_* - curate
to copper + silk + mask + paste + Edge.Cuts for the upload zip. There is NO Protel-extension toggle
in kicad-cli (it always writes standard .gtl/.gbl/.g1/.gts/... which current JLC accepts), and the
drill lands as ONE .drl alongside. Do NOT pass `--subtract-soldermask`: silk-over-pad must stay
VISIBLE in the gerber or dfm_check cannot see what the fab would clip. Board copper order in the
file's `(layers)` block is by layer ID, where B.Cu (31) can precede the inners - re-sort to
PHYSICAL order (F, In1..InN, B) or outer-vs-inner copper-weight rules get applied to the wrong
layer. `pos` defaults to inches (kc.py forces mm). kicad-cli writes G90 after the Excellon header,
which makes gerbonara emit a SyntaxWarning it then parses correctly - suppress it at the read site
(gerblib) so library callers like gate.py keep clean stderr, not just the CLI.

## 2026-07-27 [skill][git] gate.py --commit is a repo-root `git add -A` - dirty trees get swept
The gate-pass commit helper (S2, wired into the S13 SKILL.md flow) stages EVERYTHING at
env.repo_root() and commits - it cannot scope to the design workspace. During /ai-ee runs (S14+)
any unrelated dirty file in the repo (parallel-session WIP, scratch edits - this repo has had
parallel WIP at S6/S7/S12) silently rides along in gate commits. Keep the tree clean during
pipeline runs, or commit/stash WIP first. Also the reason dry-run tests must NEVER use --commit
(they run inside this repo; test workspaces live in pytest tmp dirs instead).

## 2026-07-27 [easyeda2kicad][erc][python] Pulled-lib pin electrical types are junk; ERC gate needs a retype pass
easyeda2kicad symbols carry `unspecified` electrical type on nearly every pin (a few arbitrary
`input`, e.g. chip resistors and crystals). kicad-cli 10.0.3 ERC --severity-all then floods
`pin_to_pin` warnings ("Unspecified and Unspecified are connected", one per pin pair) plus FALSE
`pin_not_driven` errors on the input-typed pins - the P4 gate (errors+warnings=0) cannot pass on
an untouched pulled lib. Fix at the SOURCE, not the .kicad_pro severities: retype pins in
lib/aiee.kicad_sym from the datasheet-extract JSON (supplies/grounds -> power_in, regulator output
-> power_out, everything else passive; a duplicate output pin like a SOT-223 tab stays passive or
ERC raises power_out<->power_out). stm32-blinky reference: boards/stm32-blinky/kicad/gen/
lib_pin_types.py (idempotent, re-run after any lib_pull refresh). Two subtleties: (a) schlib.py
--pins REPORTS "passive" for pins the file stores as `unspecified` (kicad-sch-api mapping) - the
lib file text / ERC output is the type ground truth, not pin_table; (b) kicad-sch-api resolves
lib_ids via its global cache which does NOT read the project sym-lib-table - generators must call
`ksa.get_symbol_cache().add_library_path(<abs path to aiee.kicad_sym>)` before add_component, and
the mtime-based cache DID pick up the lib edit correctly on rebuild (verified: embedded lib_symbols
in the saved .kicad_sch carried the new types).

## 2026-07-27 [board_init][kicad] P5 gate: --schematic SameFileError in-place + LCSC field parity warnings fail self-check
Two board_init findings from the first real P5 run (stm32-blinky). (a) `--schematic` does a bare
shutil.copy to `<out>/<name>.kicad_sch`; when the schematic ALREADY lives in the out dir under that
name (the normal pipeline layout), copy raises SameFileError -> exit 2 AFTER the board is written
but BEFORE self_check runs. Workaround without code/hand edits: pass a byte-identical staged copy
from a temp dir (parity check still runs). Omitting --schematic silently skips parity - don't.
(b) Boards whose symbols carry an LCSC field (every real P4 output; goldens didn't) fail the parity
gate: netlist has the LCSC property but board_swig.py copies NO fields into footprints, so KiCad 10
DRC (--severity-all) emits `footprint_symbol_field_mismatch` "Missing symbol field 'LCSC' in
footprint" - one warning per component, counted as parity. Net-membership parity itself was 0.
Fix belongs in board_init/board_swig (copy netlist component properties into footprint fields), not
in the schematic.

## 2026-07-27 [placement][drc][geometry] P6 gate is courtyard-blind: place_seed output shorted 9 pad pairs while gate=PASS
First real P6 run (stm32-blinky, 20 parts). place_seed's satellite ring passed `gate.py --gate place`
with 0 violations, yet KiCad DRC on the SAME board reported 21 copper errors (9 shorting_items,
9 solder_mask_bridge, 2 clearance, 1 copper_edge_clearance) + 68 silk warnings. Cause: placelib's
legality is courtyard-only, and several footprints have courtyards SMALLER than their pad field -
the LQFP48 U1's courtyard is a 7.05 mm box while its pads span 10 mm, so decouplers legally parked
"outside the courtyard" sat ON the pin tips (the deferred D2 quirk is not an isolated library bug).
Corollary: check_silk is far more lenient than DRC (its "pad centre covered OR >=50% of pad" rule
reported 12 hits where DRC found 68) - never treat check_silk=0 as silk-clean. Two more facts: the
board-setup edge clearance (0.50 mm) is STRICTER than the generated .kicad_dru floor (0.30 mm), and
`check_silk` violation `refs` names the PAD's owner, not the silk's owner (attribute the silk owner
yourself before blaming a part). Practical P6 recipe that ended err+warn=0: anneal candidate ->
clearance-driven repair against real DRC semantics (pad-pad >=0.5, silk-pad/silk-silk >=0.25,
courtyard gap >=0.15, pad-edge >=0.62 mm) -> verify with `kc.py drc`, not the place gate alone.

## 2026-07-27 [placement][python] Coordinate descent cannot evict a blocker; anneal candidates repair better than the seed
Two placement-search facts from the same run. (a) A per-part cost descent never moves a part whose
OWN cost does not improve, so a blocker parked between a decoupler and its pin is immovable: C1 sat
8.7 mm from U1.48 with D2 in the corridor and no single-part move (even at 30x decoupler weight,
12 mm cap) could fix it. A 2-level cooperative search (grid over the blocker x best-response grid
for the riders) dropped C1 to 2.7 mm. (b) The S13 note "the SEEDED placement is silk-clean, route
from the seed" is board-specific: here the anneal candidates were silk-DIRTIER than the seed
(22-26 vs 12 check_silk hits) but structurally far better (HPWL 160 vs 313, crystal 4.1 vs 5.5 mm),
and repairing cand1 beat repairing the seed on every metric (HPWL 227 vs 330, crystal 4.1 vs 12.0,
both DRC-clean). Repair the best candidate; do not fall back to the seed on silk counts alone.

## 2026-07-28 [easyeda2kicad][parts] Symbol pulls are NOT idempotent for names with spaces or '/', and lib_pull hides a failed symbol pull
Three machine-verified facts from the usb-buck P3 library pull (18 parts, easyeda2kicad 1.0.1).
(a) `ExporterSymbolKicad.save_to_lib` guards with `id_already_in_symbol_lib(component_name=
self.input.info.name)` - the RAW EasyEDA name - but writes the block under `sanitize_fields(name)`
(spaces stripped, `/`->`_`). Any part whose EasyEDA name contains a space or slash therefore never
matches its own guard and is APPENDED AGAIN on every re-run: `HX PZ2.54-1x4P ZZ`,
`TS263065A 340gf SX BD SMD Tactile Switch`, `CKCS4030-4.7uH/M` turned an 18-symbol lib into 21 on
the second pull. Duplicate `(symbol "NAME")` blocks are silently accepted by kicad-cli. lib_pull.py's
"registration is idempotent, re-runs are safe" only covers the lib-TABLES; the symbol library is not.
Re-pull only with `--overwrite`, or de-duplicate afterwards (keep first occurrence).
(b) `_pull_one` returns "pulled"/"exists" whenever the LCSC id is found in ANY existing footprint file,
so a part whose footprint is already present reports success even when its symbol pull failed - a full
18-part re-pull reported 12 "pulled" while the EasyEDA API 403'd every single symbol request and the
symbol lib was never recreated. Check `payload.symbol_lib` / the symbol count, never the per-part status.
(c) The anonymous EasyEDA CAD endpoint rate-limits: ~3 full passes over 18 LCSC ids within ~15 min
returned `HTTP Error 403: Forbidden` for every subsequent part, and a 60 s backoff was not enough.
Treat a pulled lib as expensive: back it up before deleting, and do not re-pull to "clean up".

## 2026-07-28 [datasheet][parts] LCSC datasheet PDFs are fetchable via the wmsc.lcsc.com path transform
`https://www.lcsc.com/datasheet/lcsc_datasheet_<id>_<name>_<lcsc>.pdf` serves a JS-rendered viewer
shell (curl gets `<!doctype html>`, ~50 KB), which is why direct datasheet fetches "fail". The same
document is served as a real PDF from
`https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/<id>_<name>_<lcsc>.pdf`
(drop `lcsc_datasheet_`, swap the host/path). Verified on C2286 (KT-0603R, 11 pages) and C2939564
(HOOYA USB-111FD-B-SU, 1 page) with a browser User-Agent. Some parts.json entries already carry the
wmsc URL directly. The venv has pypdf (text only) - no rasteriser - but the Read tool renders PDF
pages, which is how the J1 land pattern was read.

## 2026-07-28 [easyeda2kicad][drc] Every pulled footprint ships silk under 0.25 mm from copper; 4 put a silk dot INSIDE pad 1
Measured across all 12 footprints of the usb-buck pull (silk stroke edge to pad copper edge, stroke
width included). C0603, C0805, R0603 and LED-SMD_L1.6-W0.8-R-RD each carry a tiny `fp_circle` on
F.SilkS (r 0.03-0.05, width 0.06-0.10) sitting ON or INSIDE pad 1 - gaps -0.11, -0.105, -0.092,
-0.10 mm. On this board that is ~20 component instances each contributing a guaranteed
silk_over_copper DRC hit BEFORE placement, which explains most of the S13 run's 68 silk warnings.
The remaining 8 footprints have no overlap but sit well under the 0.25 mm silk-pad rule
(SW-SMD 0.015, IND 0.055, LQFP-48 0.060, USB 0.085, TSOT-26 0.105, SOT-23-6 0.154). The offending
dots all have stroke width < 0.15 mm (JLC's minimum silk line width), so a safe library sanitiser is
"drop F.SilkS graphics with width < 0.15". Related: those same sub-0.15 strokes are why some pin-1
marks do not print - the TSOT-26 (AP63203) pin-1 indicator is a width-0 zero-area `fp_poly` plus a
0.06-wide dot, and its real marker circle is on Cmts.User, so U2 has NO printable pin-1 mark.

## 2026-07-28 [easyeda2kicad][drc] CORRECTION + fix recipe for the silk-on-pad dots (measured against real DRC)
The entry above over-claimed: it predicted ~20 guaranteed silk_over_copper hits from the four
library dots. MEASURED with a scratch board (one instance each of C0603/C0805/R0603/LED-SMD/
TSOT-26, `kc.py drc --severity-all`, KiCad 10.0.3): only ONE fires - the LED's dot (r 0.05,
stroke 0.10, centred exactly on pad 1's corner) -> `silk_over_copper` "Silkscreen clipped by
solder mask". The three 0.06-stroke dots on C0603/C0805/R0603 sit INSIDE pad 1 and do NOT trip
DRC; KiCad's clipped-by-mask test evidently needs the item to straddle the mask aperture by more
than a hair (each of those crosses the pad edge by only ~0.01 mm). Geometry checkers that measure
stroke-edge-to-copper-edge see all four; DRC sees one. Verify a silk claim with `kc.py drc`, never
with a geometry script alone - and note this cuts the other way from the S13 finding that check_silk
is more lenient than DRC.
Fix recipe that worked (approved, see boards/usb-buck/lib/EDITS.md): delete the artifact circles;
where the remaining outline then still sat under a 0.15 mm silk-to-copper bar, NARROW the stroke
rather than move coordinates (0.25 -> 0.20 on C0603's outline buys 0.025 mm per edge and keeps the
geometry byte-identical; 0.20 and 0.15 are both >= JLC's 0.15 mm minimum line width). Result:
worst gaps 0.150-0.156 mm across the four, DRC 1 -> 0. A printable pin-1 dot is
`(fp_circle (center X Y) (end X+0.15 Y) (layer F.SilkS) (width 0.15))` - 0.3 mm diameter, outer
radius 0.225, so its centre must be >= 0.375 mm from the nearest pad edge to clear copper by 0.15.
Scratch-board trick for verifying a footprint edit in isolation: bundled python + `pcbnew.
CreateEmptyBoard()` + `FootprintLoad(pretty, name)` spaced 30 mm apart + an Edge.Cuts rect, save,
then `kc.py drc` - only intra-footprint findings can appear.

## 2026-07-28 [kicad-cli][python] Hierarchical netlist export DROPS the no-connect singleton nets; schlib --pins cannot see a project lib
Two P4 facts from the usb-buck schematic build (3 child sheets, 28 parts, KiCad 10.0.3).
(a) The 2026-07-22 entry "every NC/unconnected pin appears as a singleton net
`unconnected-(REF-PINNAME-PadN)`" holds only for FLAT designs. Same board, same
`kc.py netlist`: exporting the ROOT of the hierarchy gives 16 nets and ZERO `unconnected-*`
entries (the 29 NC-flagged U1 pins + J1's ID pin are simply absent from the netlist and from
U1's node list), while exporting the child `mcu.kicad_sch` STANDALONE gives 29 of them. The
no_connect s-exprs are byte-shaped identically to stm32-blinky's (which does emit 32), and ERC
is clean either way - it is the export that differs. Consequence for P5: those pads arrive at
board_init with NO net at all rather than an `unconnected-` net, unlike run (a)'s flat board.
(b) `schlib.py --pins "aiee:SYM"` - the documented P4 grounding aid - CANNOT read a project
library: it builds a scratch schematic through kicad-sch-api's global symbol cache, which never
reads kicad/sym-lib-table, so every project symbol comes back `LibraryError: not found ...
Common libraries include: Device, Connector_Generic, ...`. Ground against a 6-line wrapper that
calls `ksa.get_symbol_cache().add_library_path(<lib/aiee.kicad_sym>)` before `schlib.pin_table()`
(same call the generators already need). Also note `--pins` takes ONE lib_id, not a list.

## 2026-07-28 [placement][geometry] placelib's FpPad DROPS per-pad rotation - re-derive pad boxes for any clearance check
`(pad N smd rect (at x y ROT) (size w h))`: placelib._parse_fp reads at[0:2] and size verbatim and
never applies ROT, so `_pad_box_local` (and therefore the EFFECTIVE courtyard that S14's fix unions
in) is built from unrotated pads. usb-buck's USB micro-B (J1) has all five signal pads at ROT 90 and
both oval shield pads at 90: placelib sees a 9.5 x 4.28 pad box where the truth is 8.3 x 4.13 mm.
Here the error is conservative in x and hidden in y by the courtyard, but it is NOT conservative in
general (a rotated tall pad reads as a short wide one). Any pad-pad / pad-edge clearance check must
re-parse `(at ... ROT)` and swap w/h at 90/270 - do not reuse FpPad.size. The place gate never
noticed because its legality is courtyard-only.

## 2026-07-28 [placement][kicad] Connector mating direction: read the WRL, not the silk outline
J1 (HOOYA USB-111FD-B-SU, easyeda2kicad) at footprint angle 0 has its MOUTH at local +Y, so 270 deg
points it out of the board's left edge. Two traps: (a) the silk outline has a GAP at the -Y end and a
solid line at +Y, which reads backwards - the gap is just clearance around the SMT signal pads and
the two round shield holes at y=-1.34; (b) the shield legs are not a reliable cue either (the wider
7.20 mm pair sits mid-body, not at the flange). Decisive cheap test: parse the `.wrl` (KiCad units =
0.1 inch, and wrl_y = -pcb_y), bucket the vertices near each Y extreme and print the (x,z)
occupancy - the mouth end is a hollow FRAME, the closed end is filled. A `kicad-cli pcb render --views
iso` crop confirms it visually but is ambiguous at small scale.

## 2026-07-28 [placement][drc][silk] Pulled footprints park Reference 4 mm off-origin -> 47 silk DRC warnings from placement alone
Every easyeda2kicad footprint in the usb-buck pull carries `(property "Reference" ... (at 0 -4 ...))`
- a 4 mm offset that lands a 0603's refdes squarely on its neighbour once parts are packed. First
DRC after the P6 repair: 24 silk_overlap + 15 silk_over_copper, ALL of them Reference fields, plus 8
silk_edge_clearance from J1's own mouth-end outline poking 0.05 mm off the board edge (fix: nudge the
connector inboard; the silk stroke reaches |local y| 4.10 + width/2). Both classes are scriptable via
S14's `move_text` op. A greedy solver works if it (1) offers BOTH text angles on ALL FOUR sides
(0-deg-above/below + 90-deg-left/right only is not enough in a channel), (2) scores
`(min(clearance, 0.30), -distance_to_own_silk+pads)`, and (3) processes the most CROWDED parts first
(descending neighbour count within 4 mm) - largest-first orphans the boxed-in small caps. Hard limit:
a 3-char refdes at size 1.0 needs ~3.5 x 1.75 mm, so in a channel narrower than ~4 mm no label can be
closer to its own part than to the parts flanking it. Relieve that structurally (move the loosest-
constrained cap out) rather than fighting the solver.

## 2026-07-28 [easyeda2kicad][parts] lib_pull with a RELATIVE --out-dir bakes unresolvable 3D model paths
`easyeda2kicad --output <base>` copies the base string verbatim into each footprint's
`(model "<base>.3dshapes/<name>.wrl")`. Invoke lib_pull.py with a RELATIVE `--out-dir`
(`--out-dir boards/pd-trigger/lib`) and every footprint ends up with
`(model "boards/pd-trigger/lib/aiee.3dshapes/X.wrl")`, which KiCad resolves against the PROJECT dir
(`boards/pd-trigger/kicad/`) and therefore never finds - STEP export and 3D render silently lose all
models. The usb-buck pull was run with an absolute `--out-dir` and has absolute model paths, which is
why this never showed up before. Always pass an absolute `--out-dir`; repairing after the fact is a
pure string rewrite of the 16 `(model ...)` lines (no re-pull needed - re-pulling would duplicate the
space/slash-named symbols per the entry above).

## 2026-07-28 [easyeda2kicad][drc] Pulled 16P USB-C: plated peg holes = 4 DRC ERRORS; DIP switch ships 8 silk_overlap
Scratch-board DRC (one instance of each of pd-trigger's 16 pulled footprints, 30 mm apart, bare
board, `kicad-cli 10.0.3 pcb drc --severity-all`, defaults clearance 0.20 / min annular 0.10):
17 findings, and they are NOT evenly spread.
(a) `USB-C-SMD_MC-311D` (C5184243 GCT USB4105) alone accounts for all 4 errors + 3 warnings: its two
Ø0.65 locating-peg holes are emitted as `(pad "" thru_hole circle (size 0.65 0.65) (drill 0.65))` -
copper diameter == drill diameter, so `annular_width` 0.000 mm (x2 error) and `padstack` "hole leaves
no copper" (x2 warning), and each peg sits 0.1801 mm from the outer GND pad (A1-B12 / B1-A12) ->
`clearance` (x2 error). The pegs are mechanical (GCT drawing: 2x Ø0.50 plastic pegs, no electrical
function). Converting both to `np_thru_hole` + deleting the 0.06 artifact silk dot = **0 violations**
(measured). Applies to any pulled USB-C receptacle with peg holes.
(b) `SW-SMD_6P-L7.6-W6.0-P2.54-LS9.3-BL` (C7421520 3-pos DIP switch) ships 8 `silk_overlap` warnings
out of the box: its own `fp_text user` marks ("1","2","3","ON","KE") collide with its own silk body
outline. All five texts sit INSIDE the body outline, i.e. hidden under the part once assembled.
(c) Only 3 of the 10 sub-0.15 mm silk artifact dots actually trip `silk_over_copper` (both LED0603
variants + the USB-C one). The dots on C0603/C1206/F1206/R0603/R0805/R1206 and a 0.15-wide circle
overlapping the ESSOP-10 thermal pad by 0.013 mm do NOT fire - consistent with the earlier
"geometry checkers see 4, DRC sees 1" correction. Always measure with `kc.py drc`.

## 2026-07-28 [placement][kicad][render] Connector mating direction: the WRL bbox is a COINCIDENCE TRAP - fit the below-board pins, or just render an orthographic SIDE view
Follow-up to the 2026-07-28 "read the WRL, not the silk outline" entry, from pd-trigger P6
(`USB-C-SMD_MC-311D` + `CONN-TH_P5.08_KF128-5.08-2P`). Two traps and one cheap decisive test.
(a) Aligning the WRL by matching its BOUNDING BOX to the footprint is unreliable. The MC-311D's raw
wrl y-range (x2.54) is [-5.07, 2.83]; negating y gives [-2.83, 5.07], which matches the courtyard's
+y limit (5.07) EXACTLY and the THT pad extent (-2.85) to 0.02 mm - a perfect-looking 2-point fit
that is WRONG. Cavity ray-casts under that mapping put the mouth at local -Y; the truth is +Y.
The reliable anchor is the BELOW-BOARD geometry: cluster the wrl vertices with z < -0.3 (leg/peg
tips) and least-squares fit (sign_x, sign_y, y_offset) against the footprint's own thru-hole/NPTH
pad centres. For the MC-311D that lands `pcb = (+x, +y + 2.10)` on all 6 features with residual
sum-sq 0.013 mm2 - i.e. the shipped model is offset 2.1 mm in y (easyeda2kicad), which is exactly
what made the bbox "match" under the wrong sign. easyeda2kicad's `(rotate (xyz 0 0 180))` plus
KiCad's own y-flip cancel, so raw wrl x,y map straight to footprint x,y here.
(b) MUCH cheaper decisive test: `render.py <board> --views front,back,left,right`. These are
ORTHOGRAPHIC side views - a connector opening appears FACE-ON (dark slot / visible wire cages) in
exactly one of them, and its rear is solid in the opposite one. Unlike `--views iso`, there is no
axis-mapping guesswork. Verified both ways here. Do NOT judge orientation from an iso render: a
first read of the pd-trigger iso called J2 backwards, and the side views proved it correct.
(c) Both of this board's connectors open toward local **+Y** (USB-C mouth AND KF128 wire entry), so
with placelib/KiCad's `to_abs` = `_rot(local, -angle)`: angle **270 points the opening out the LEFT
edge, 90 out the RIGHT**, 180 up (-Y), 0 down (+Y). place_seed got J1's 270 right by luck of the
edge constraint; it has no mating-direction model, and it left J2 at 0 (wires into the board).

## 2026-07-28 [drc][kicad-cli] DRC on a board copy OUTSIDE the project dir silently changes the rules
Staging `kicad/<board>.kicad_pcb` to `work/try1.kicad_pcb` and running `kc.py drc` reported 30
bogus `lib_footprint_issues` ("configuration does not include the footprint library 'aiee'") AND
5 `copper_edge_clearance` errors quoting "board setup constraints edge clearance 0.5000 mm" - but
the project's `.kicad_pro` sets `min_copper_edge_clearance: 0.30`. KiCad fell back to its DEFAULT
0.5 mm because the sibling `.kicad_pro` was missing. Copy `fp-lib-table` + `<stem>.kicad_pro` +
`<stem>.kicad_dru` next to any staged board or both the library warnings and the clearance numbers
lie (in opposite directions: noise added, and a stricter rule than the real one).

## 2026-07-28 [routing][placement] Freerouting cannot merge a USB-C's two VBUS pads at 1.75 mm - it is a topology fact, not a placement defect
pd-trigger P6 route probe (2-layer, 2 oz, VBUS min width 1.75 mm by DRU rule). 150-pass probe
converges at pass 13 and stops: 65/68 connections routed, and ALL 3 residuals are net VBUS
(`J1-B4-A9 -> J1-A4-B9`, `R14-1 -> C1B-1`, `C1B-1 -> J2-1`). Cause: on the 16P USB-C the pad row is
GND,VBUS,CC1,<6 nc>,CC2,VBUS,GND at 0.5 mm pitch, so CC1/CC2 are SANDWICHED between the two VBUS
pads. Escaping VBUS leftward (around the pad row, under the connector body) is pinched to 1.50 mm
between the GND pad (y 39.61) and the CC1 pad (y 41.11) - narrower than the 1.75 mm rule. Escaping
right forces the merge to cross both CC escapes. So on ONE layer the merge is impossible; it needs
CC1/CC2 (or VBUS) to hop to the other layer. Consequence: a P6 route probe on any USB-C PD board
will cap around 0.95, below the >=0.98 bar, no matter how good the placement - judge the probe by
WHICH connections fail. The fix is P7's KRT-wrapped critical nets (power routed first, thin nets
displaced to B.Cu), not more Freerouting passes or a placement change.

## 2026-07-28 [placement][python] place_edit ops files need the {"version":1,"ops":[...]} envelope
`place_seed --ops-out` writes it, but a hand-built bare JSON list fails with
`CheckError: ops file must be {'version': 1, 'ops': [...]}` (exit 2). Also: `place`/`move` ops set
the footprint ORIGIN, not the courtyard centre - for asymmetric parts (USB-C: extents
x[-5.07,+3.10] about the origin) compute the target from the origin or the part lands offset.

## 2026-07-28 [routing][kicad][drc] A net-wide `track_width` DRU floor can be geometrically UNMEETABLE at a fine-pitch pad - pour it, do not neck it
pd-trigger P7. `aiee_pwr_width_VBUS (min 1.75mm)` applies to every VBUS **track**. Measured on the
real board (widest track whose copper can touch the pad while holding the 0.1524 mm clearance
floor): J1-A4-B9 / J1-B4-A9 = **1.465 mm** - i.e. NO legal VBUS track can reach the USB-C's own
VBUS pads, because CC1/CC2 and the 6 unconnected pads sit 0.2 mm away on a 0.5 mm-pitch column.
Every other VBUS pad measured >= 6 mm, so the defect is local to the connector. Corollary: the
prior entry's "CC1/CC2 dive to B.Cu so VBUS merges on F.Cu" does NOT fix it - displacing the CC
*escapes* does not remove the CC *pads*, which are what pinch the fan-in. The fix is a **zone**:
a zone is not a track, so the DRU width rule does not apply, and KiCad's filler necks around the
foreign pads by itself. `planes_gen --constraints <file with only a "planes" key>` creates it
(region is a rect only); then patch `(connect_pads (clearance X))` -> `(connect_pads yes
(clearance X))` on that zone's block - planes_gen leaves KiCad's default THERMAL relief, which
would spoke-starve a 5 A pad. Result: 3.5 mm of continuous copper across the merge vs 1.75 needed.
Reusable test: for each pad, max over centreline points P of `2*(dist(P,foreign)-CLR)` subject to
`dist(P,pad) <= W/2` - that is the widest connectable track.

## 2026-07-28 [routing][rules_gen][freerouting] rules_gen puts EVERY power net in one "Power" netclass at the WIDEST width - Freerouting then routes 20 mA nets at 5 A width
`rules_gen.net_classes()` emits a single `Power` class at `max(min_width_mm)` over all
constraints["power"] entries and assigns all of them to it. On pd-trigger that put /VDD (0.02 A,
DRU floor 0.005 mm) and /VIND (0.05 A) into a 1.75 mm class. The DSN export carries netclass
widths, so FR would have tried 1.75 mm traces into U1's 0.6 mm-wide 0.5 mm-pitch pads and failed
those nets. The DRU rules are per-net and were already correct - only the .kicad_pro netclass is
wrong. Fix before route_auto: split `netclass_patterns` so the wide class holds only the fat net
and the thin ones get their own class (or Default), leaving the .kicad_dru untouched. With that
done FR hit completion 1.0 on rung 1 (55 -> 3 unrouted, the 3 finished by the KRT/dedup pass).

## 2026-07-28 [routing][check_current] check_current's via-count rule is NET-WIDE - a 5 A net must not change layers at all
`need = ceil(current_a / via_amps)` is applied to EVERY via cluster of the net, with no
per-segment override (`overrides` only feeds the track-width check, not vias or pour necks). VBUS
at 5 A / 0.5 A-per-via => 10 vias at EVERY layer transition, including a bulk-cap or 1 A fuse tap
that carries nothing like 5 A. Practical consequence on a 2-layer board: keep the whole high-
current net on ONE layer (pour + rule-width tracks, zero vias) and give the boxed-in taps a
detour rather than a via pair. Bonus: `pour_neck` only measures between via attachments and
returns None with < 2 vias in the fill, so a via-free pour is also exempt from the neck check -
do not lean on that silently, state the measured channel widths in the report. pd-trigger shipped
VBUS with 0 vias, min track 1.75 mm, 3.0 mm x 32 mm trunk -> check_current 0 violations.

## 2026-07-28 [routing][stitch][drc] KiCad hole_to_hole does NOT test a via drill against a same-net THT PAD drill - the 0.5 mm floor must be enforced by the generator
Live test on pd-trigger (scratch copy, `aiee_hole_to_hole_floor` min 0.4995 mm): a 0.3 mm-drill via
placed so its hole edge sits **0.121 mm** from J1-1's THT oval drill produced ZERO violations
against that pad; the same DRC run DID flag the identical via against another VIA at 0.403 mm. So
KiCad checks via<->via but skips via<->pad-hole when the via lands on the same-net pad's copper.
Consequence: stitch_vias' pad-attachment vias can ship drill spacing the fab cannot produce and
DRC stays green - two such vias (0.12 mm to J1-1 / J1-4) were sitting on this board. Any generator
that drops a via next to a THT pad must compute the pad's drill itself. Doing that needs the
footprint transform: inside a `(footprint ... (at fx fy frot))`, a pad's `(at px py prot)` stores
prot as the ABSOLUTE board angle (frot is already folded in) - so position rotates by `-frot` but
the pad/drill SHAPE rotates by `-prot`. Using `prot - frot` silently yields a drill turned 90 deg
(caught here only because the derived drill boxes disagreed with the pad bounding boxes).

## 2026-07-28 [routing][placement] A USB-C's SMD GND contact pads need a pour lobe + via field, not stitch_vias' one-via-per-pad
pd-trigger P8 review: the 5 A return choked at J1. Freerouting attaches GND contact pads
(A1-B12 / B1-A12, ~2.5 A each) with 0.2 mm track = **0.80 A** at 2 oz, and stitch_vias adds ONE via
per pad - neither script models per-pad current, and check_current only audits nets in
constraints["power"] (GND is deliberately NOT one, decisions D7). Fix that held: two F.Cu GND zone
lobes (planes_gen with a planes-only sidecar) spanning the contact pads AND the THT shell/EH pads,
at **priority 1 so the neighbouring VBUS pour yields**, patched to `connect_pads yes` (planes_gen
leaves KiCad's default THERMAL relief, which would spoke-starve a 2.5 A pad). Result 2.09 mm /
1.62 mm narrowest pad->via cross-section (5.68 A / 4.70 A) and 8 / 7 vias. Cross-net zone overlap
at distinct priorities is fine - `zones_intersect` only fires same-net same-priority.

## 2026-07-28 [skill][fab] Silk strap tables must print the SWITCH positions, not the raw config bits
The CH224K map is `(CFG1,CFG2,CFG3)`: 1XX=5 V, 000=9 V, 001=12 V, 011=15 V, 010=20 V - but the DIP
switch shorts to GND, so ON = logic 0 and the bit table is INVERTED relative to what the user sets.
Printing "000=9V" on the silk would tell someone to set all three OFF, which selects 5 V. Print the
ON/OFF table from decisions.md D3 instead. Also: `place_edit add_text` auto-mirrors any `B.*` layer
(place_swig `SetMirrored(True)`), so B.SilkS text is written in board coordinates and reads
correctly in the bottom render - do not pre-mirror the string or the position.

## 2026-07-28 [datasheet][python] Zooming a datasheet drawing without a rasterizer: pypdf crop + scale
No pymupdf/pdf2image in the venv (pypdf + PIL only). To read a pin diagram or dimension table
visually: pypdf `page.scale_by(s)` (s~5), then set `mediabox.lower_left/upper_right` to the crop
region multiplied by s, write a single-page PDF, and Read that. Resolved the CH224K pin numbers
and the L78L33 table columns when the text layer was garbled/misaligned. (Derived by the S14
extractor agents; both used it successfully.)

## 2026-07-28 [python] kicad-sch-api preserves compound alphanumeric pad names ("A4-B9") through save
Unlike the known alphanumeric renumbering of simple names ("SH" -> "6", repaired by fixups),
COMPOUND pad names like "A4-B9"/"B1-A12" on the USB4105 USB-C receptacle survive
kicad-sch-api save/netlist intact - no fixup needed. (S14 run (c); the fixup machinery stays
for the simple-name case.)

## 2026-07-28 [placement] place_anneal separation refs absent from the board were silently dropped
A refdes rename (R2 -> R2A/R2B split) invisibly lost a thermal-separation constraint - the
anneal ignores unknown refs. Now surfaced as facts.separation_unknown_refs (test-pinned);
orchestrators must sync constraints refs after any refdes change (sheets.md tracking rule).

## 2026-07-28 [latex][windows] pdfpages 2026 passes an `artifact` key older graphics stacks reject
MiKTeX auto-installs pdfpages v0.6h (2026), which emits an `artifact` keyval into
\includegraphics on MULTI-page \includepdf insertions; a 2024-era graphics/graphicx lacks the
key -> "keyval Error: artifact undefined", fatal - while SINGLE-page includes work fine (why
quick probes miss it). report_gen's preamble defines the key as a no-op iff absent
(\makeatletter/\makeatother guard). Recognize it by: 1-page schematic embeds fine, 4-page dies.

## 2026-07-28 [jlc][api] JLCPCB Open API facts (probe-verified on a live app)
Base URL open.jlcpcb.com (/overseas/openapi/...) - api.jlcpcb.com is the portal/console only.
Auth "JOP" scheme: 5-line HMAC-SHA256 string-to-sign (METHOD, path[+?query], unix-ts, 32-char
nonce, raw body - EACH line \n-terminated INCLUDING the last), Base64; header carries
appid+accesskey+timestamp+nonce+signature (the secret never travels). Official doc vector
reproduced exactly (pinned in test_jlcapi). Error taxonomy: HTTP 401 = bad signature; HTTP 403 =
app valid but service scope unapproved (live body while scopes are "Reviewing":
{"code":403,"success":false,"message":"API insufficient permissions, access denied"}); business
code 1000 = IP-whitelist block and wins at ANY HTTP status; 1002/429 = rate. NO sandbox exists -
pcb/create places a REAL order. NO PCBA/assembly API (official api-list 2026-07-16): BOM/CPL
ordering stays the web flow. IP whitelist optional; two keys per app for rotation. Contract
brief + sources: C:/dev/ai-library/jlcpcb-openapi-2026/.

## 2026-07-28 [jlc][api] pcbParam keys are layer/width/length/qty/thickness - NOT stencil*
The hendley console-PDF transcription is the key-name authority; stencilLayer/stencilPly belong
to the impedance-template request only. An early build inferred stencil* names for
calculate/create - unknown keys could have priced/ordered a DEFAULT board with real money
(caught by adversarial review, round 1). Watch item: copperWeight is typed "Number" in both
tables but the official create example sends string "2" - code sends strings; a strict live
parser rejects at calculate (fail-safe, before money). 2 oz derives ONLY from stackup.md's
"## Chosen:" id (e.g. JLC2313_1.6_2oz) - spec_snapshot/quote.json do not carry copper weight,
and the guard refuses when order-note oz mentions exceed the derived value.

## 2026-07-28 [latex] Escaping the special chars is not enough: [ and * are context-sensitive
After the \\ that joins tt-block/longtable lines, a line-initial "[" parses as the OPTIONAL ARG
of \\ ("! Missing number", fatal - a digest line "[ok] ..." or BOM comment "[NRND] ..." kills
the compile); "\item [x]" swallows [x] as the item label (silent content loss in task lists /
human_steps); "\\*" swallows a line-initial star. Per-char escaping cannot see context; fix is
brace-wrapping in the map ("[" -> "{[}", "]" -> "{]}", "*" -> "{*}") - renders identically,
inert everywhere. Related: OT1 text mode prints raw < > | as inverted punctuation/em-dash -
map to \textless{}/\textgreater{}/\textbar{}. (Adversarial review of report_gen, probe-verified.)

## 2026-07-28 [geom][layout] Arc endpoints were recomputed, so any arc-based board outline parsed as POLYGON EMPTY
geom._arc_points sampled the arc from the fitted centre+radius, so its first/last points were
`cx + r*cos(a1)` rather than the DECLARED start/end - off by ~1e-14. unary_union then refuses to
node the arc against the gr_line meeting it, polygonize finds no closed face, and
BoardGeom.outline silently becomes an empty Polygon (no error, no warning). Any rounded or
arc-cornered outline hit this, not just generated ones - and every downstream consumer
(planes_gen, DFM, order_quote, area checks) reads that empty outline. Fix: pin pts[0]/pts[-1] to
the declared start/end (they ARE the endpoints by definition). Detect it by asserting
outline.area > 0, never by trusting board_init's self_check - that passed the whole time.

## 2026-07-28 [testing][windows] tests/test_report.py is not concurrency-safe - it diffs global `git status`
test_smoke_pd_trigger_with_residue_and_rerun and test_smoke_stm32_blinky snapshot
`git status --porcelain` before/after and assert the ONLY new lines are `?? .../reports/design_doc/`.
Anything else touching the working tree during the run fails them - a second ai-ee orchestrator
writing under boards/, or a research agent dropping a scraped page at the repo root
(`?? docs_start.html` was a real instance). Failures look like flaky asserts on a list of git
lines and are NOT caused by the code under test. Re-run the file in isolation to confirm before
chasing it; when running boards concurrently, expect these two to be unreliable.

## 2026-07-28 [layout] Corner radius must be clamped to the mounting-hole inset, not solved by moving holes
board_init packs parts on a shelf grid that already routes around the corner mounting holes at
inset = margin/2. Pushing a hole inward so it clears a larger corner radius therefore drives it
into a neighbour's courtyard (observed: H1 vs C1 on golden usbbuck4 at radius 4, inset 3).
Shrink the radius to the inset instead and tell the caller to raise --margin if it wants more.

## 2026-07-28 [kicad][python] A power symbol's net name comes from its VALUE field, not its library pin name
P4 lumina-carrier, kicad-cli 10.0.3. A board rail with no stock power symbol (`+48V_SW`) is NOT a
dead end and must NOT fall back to a local label (that yields `/<sheet>/NAME` and
`netlist_audit --constraints` then raises missing_net at ERROR). Place any power symbol and set its
Value: `power:+48V` with Value `+48V_SW` exports a bare global net `+48V_SW`. Measured both ways on
a 2-part scratch sheet: with the wire carrying local label `+48V_SW` but the symbol left at Value
`+48V`, the netlist net is `+48V` - the POWER SYMBOL WINS over a coincident local label, so the two
must be made to agree or the label is silently ignored. Two API traps in the same idiom:
(a) `component.set_property("Value", x)` is a SILENT NO-OP on kicad-sch-api 0.5.6 - Value is a
dedicated attribute (`component.value = x`), and the generic property store is a different thing;
(b) schlib's `power_flag(..., flag=False)` is the clean "rail cluster with no PWR_FLAG" form for a
sheet that consumes a rail another sheet drives.

## 2026-07-28 [python][kicad-sch-api] add_hierarchical_label() DROPS its shape argument; rotated 2-pin symbols get INWARD stubs
Two schlib/kicad-sch-api 0.5.6 defects found building a P4 child sheet.
(a) `Schematic.add_hierarchical_label(text, position, shape=...)` documents five shapes and then
discards the argument: it calls `self._hierarchical_labels.add(text, position, rotation, size)`
with no shape, and `_sync_hierarchical_labels_to_data` emits no shape key at all - every
hierarchical label is written `(shape input)` whatever you ask for. There is no API path to it.
`Schematic.add_sheet_pin` DOES honour pin_type, so `Project.add_sheet` still writes correct shapes
on the ROOT's sheet pins and only the child's labels are wrong. NOT worth patching: the S7 golden
already has the mismatch (`tests/s7_regen/hierdemo` root sheet pin `CTL` is `passive`, the child
label is `input`) and that root's ERC is 0/0 - KiCad 10.0.3 does not check sheet-pin/label shape
parity. Keep the semantic shape in the generator; it lands where it matters.
(b) schlib's `stub_dir` and kicad-sch-api disagree on the SIGN of a 90 deg symbol rotation, so for
any rotated 2-pin part BOTH auto-stubs are emitted pointing INWARD through the symbol body, putting
the local-label anchors inside the part (10k 0603 at rot 90, anchor y 127.0: pin1 121.92 -> 124.46,
pin2 132.08 -> 129.54). Electrically survivable - the stubs stay 5.08 mm apart and the labels still
bind - but visually broken. Generators should keep every component at rotation 0 until this is
fixed; hierdemo and pd-trigger never rotate, which is why it had not surfaced.

## 2026-07-28 [geom][layout] An inner Edge.Cuts gr_rect silently BECOMES the board outline
geom._parse_outline scans gr_rect on Edge.Cuts FIRST and returns on the first match, before it ever
looks at gr_line/gr_arc. So an interior cutout emitted the obvious way - a SHAPE_T_RECT window -
replaces the outline entirely rather than punching a hole in it. Measured: a 10x10 mm window on a
100x80 mm board parsed as outline.area == 100.0, not 7892. planes_gen, DFM, order_quote and every
area check would then see a 10x10 board, with no error raised anywhere. board_init now REFUSES
interior cutouts: --cutout must touch an outline edge and become a notch (exit 2 with remediation).
If interior windows are ever genuinely needed, _parse_outline must collect ALL closed loops and
subtract the inner ones instead of returning the first rect it finds.

## 2026-07-28 [spice][windows] InSpice + KiCad's bundled ngspice.dll: the working recipe has traps
KiCad 10.0.3 ships ngspice.dll v46 beside kicad-cli; InSpice 1.7.0.5 (transitive via skidl, now
pinned) drives it - no standalone ngspice needed. Traps (all machine-verified): (1)
NGSPICE_LIBRARY_PATH must be the BARE name "ngspice.dll" + KiCad bin prepended to PATH -
find_library splits at the first "." so a full path containing "KiCad/10.0" truncates; (2)
SPICE_LIB_DIR must be SET (any dir) or _load_library crashes on Path(None) ("can't find spinit"
is benign); (3) circuits passed to load_circuit() must end ".end" or you get a silent "no
circuits loaded". .measure only works for tran/dc/ac (never op) and results are NOT vectors -
parse "name = value" lines from captured stdout; a FAILED .measure makes run() raise while the
successful measures stay parseable. Always inject .options rshunt=1e9 (one floating node makes
.ac singular while DC stays perfect); keep refs to all callback thunks; run each bench in a
killable subprocess (in-process DLL hangs are unkillable on Windows). KiCad "1M" resistor value
means 1 MEG in SPICE (map it); a bare-"F" cap value ("1F") silently becomes femtofarads - treat
prefix-less F as unresolved. kicad-cli spice export is UNUSABLE without Sim.* fields (model-less
symbols emit REF __REF with ZERO nodes - topology destroyed); synthesize fragments from the
kicadsexpr .net instead.

## 2026-07-28 [spice][geometry] PDN cavity-model band edges are validity limits, not layout facts
With no VRM branch (low f) and no package/die capacitance (high f), max|Z| over the sweep ALWAYS
lands at a band edge - gating a target on band-max fails every known-good board (adversarial
B-1; a 165 mOhm target read 1.4-1.6 Ohm at 200 MHz on the clean usbbuck4 golden). Gate
antiresonance PEAKS only; judge peaks/first_min, never z_max. Empty bounds sidecars must be
rejected like missing ones - "[]" gate-passes an unproven testbench (A-1); JSON NaN/Infinity
bound limits validate as numbers and can never trip (A-2) - require finite.

## 2026-07-28 [parts][datasheet] parts_search returns www.lcsc.com datasheet URLs that curl CANNOT fetch; rewrite them to the wmsc mirror
`parts_search.py` faithfully returns whatever LCSC publishes, and for most parts that is
`https://www.lcsc.com/datasheet/lcsc_datasheet_<stamp>_<Vendor-MPN>_<Cxxxx>.pdf`. That URL serves a
**JS-shell HTML page** to any non-browser client - `curl -sL` writes a ~46-48 kB file starting
`<!doctype html>`, exit 0, no error anywhere. A minority of parts instead return
`https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/<stamp>_<Vendor-MPN>_<Cxxxx>.pdf`, which serves
the real PDF. Same catalogue, two URL forms, only one of them fetchable.

Measured on lumina-strobe P3: **7 of 9** datasheet URLs from parts.json were the unfetchable form.
The fix is a pure string rewrite - drop `/datasheet/lcsc_datasheet_`, keep the
`<stamp>_<Vendor-MPN>_<Cxxxx>.pdf` stem, and prefix
`https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/`:

    www.lcsc.com/datasheet/lcsc_datasheet_2304140030_STMicroelectronics-LM2901DT_C142961.pdf
 -> wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/2304140030_STMicroelectronics-LM2901DT_C142961.pdf

All 7 recovered on the first try (294 kB - 2.2 MB, all `%PDF`). **Always check the first 4 bytes are
`%PDF` after downloading a datasheet** - the failure is silent and the datasheet-extractor would
otherwise "extract" a pinout from an HTML error page, and its output is the ONLY pinout source the
schematic agents may wire from.

Second, unrelated trap seen in the same batch: **many distinct parts share one LCSC date stamp**
(`2304140030` covered LM2901DT, IRF640NSTRLPBF and both CONNFLY DS1023 sockets). That looks exactly
like a pattern-fabricated URL and is not - it is LCSC's own batch-upload date. Verify against
`parts_search` output before accusing a sourcing agent of inventing URLs. Relatedly, the two DS1023
sockets (2x7 and 2x12) return byte-identical PDFs because one family datasheet covers the range.

## 2026-07-28 [parts] parts_search returns zero rows for value tokens like "10K"
Queries containing resistor/capacitor value tokens (`10K`, `1K`, `56K`) and some long
multi-word phrases silently return **zero results** - not an error, just an empty set that
reads like "no such part exists". Search by exact MPN (`0603WAF1002T5E`) or by a spelled-out
value (`10 ohm`) instead. Cost the lumina-par P3 part-sourcer several wasted round-trips
before it worked out the pattern. The failure mode is dangerous because an empty result set
looks identical to a genuine stock-out and can push an agent into re-architecting around a
part that is actually available.

## 2026-07-28 [parts] LCSC www/datasheet URLs return HTML; rewrite to the wmsc host
`parts_search` hands back two URL shapes. `https://www.lcsc.com/datasheet/lcsc_datasheet_<stamp>_<name>_<lcsc>.pdf`
serves a **47 kB HTML page**, not a PDF - 15 of 18 datasheets on lumina-par came back that way,
including the main LED driver and both emitters. The working form is
`https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/<stamp>_<name>_<lcsc>.pdf` - i.e. strip
`lcsc_datasheet_` and swap the host. Rewriting recovered all 18 on the first retry.
Combined with the existing `%PDF` magic-byte rule this is a two-step must: rewrite the URL, then
verify the first 4 bytes. Without the byte check the extractor "extracts" a pinout from an error
page, and that pinout is the only source the schematic agents may wire from.

## 2026-07-28 [datasheet][parts] TI SN74LVC00A (SCAS279R) Pin Functions table has shifted TYPE/DESCRIPTION columns
The sec 6 table prints `3Y ... - / Power Pin`, `3A|3B ... Gate 4 input`, `4A|4B ... Gate 3 input`,
`VCC ... O / Gate 3 output`: the TYPE and DESCRIPTION columns are shifted one row up from the 3Y row
down, so a text-layer extraction reads pin 8 as a power pin and pin 14 as a gate output. The pin
NUMBER columns and the D-package Top View diagram are correct and self-consistent
(7=GND, 8=3Y, 14=VCC) - read the pin-configuration DIAGRAM as the authority whenever a TI pin table
disagrees with it, and cross-check against the package diagram on the same page before trusting a
name/number pair pulled from the table text. Found on lumina-par P3 (C485072).

## 2026-07-28 [datasheet] LM339LV pin drawing and pin table transpose the channel names
TI's LM339LV datasheet gives conflicting channel labels: Figure 5-3 (pin drawing) and Table 5.2
(Pin Functions) **transpose the OUT names**, and Table 5.2 is self-inconsistent - it names pin 13
"OUT3" while describing it as "Output pin of the comparator 4". The datasheet's own footnote admits
the transposition. Both sources DO agree on the pin-number groupings, so the rule is:
**wire quad comparators by pin number, never by channel name.**
Correct groupings: out1<->in6/7, out2<->in4/5, out13<->in10/11, out14<->in8/9.
Two more traps in the same part: **no internal hysteresis** (needs external, or the output chatters
on slow signals like an NTC ramp), and POR holds outputs **Hi-Z for up to 30 us**, so an
open-drain fault line reads "no fault" during that window.

## 2026-07-28 [librarian][kicad] A wrong lib-table URI silently pulls into the REPO ROOT and every board then shares it
`lib_pull.py --project <ws>/kicad` writes symbols/footprints to whatever `${KIPRJMOD}`-relative path
the board's `fp-lib-table` / `sym-lib-table` already contain - it does NOT derive the path from
`--project`, and it does NOT complain when the URI escapes the workspace. lumina-strobe shipped with
`${KIPRJMOD}/../../../lib/aiee.pretty` (three levels up from `boards/<name>/kicad/` = the repo root)
where lumina-carrier correctly had `${KIPRJMOD}/../lib/aiee.pretty`. The first pull therefore created
an untracked repo-root `C:\dev\ai-ee3\lib\`, left `boards/lumina-strobe/lib/` empty, and `lib_pull`
reported `status: pass` with `load_check ok` - because the pull genuinely succeeded, just into a
shared location three concurrent board runs would have collided in.

**Check both lib-table URIs before the first `lib_pull` of a run**, not after: the correct form from
`boards/<name>/kicad/` is `${KIPRJMOD}/../lib/aiee.pretty` and `${KIPRJMOD}/../lib/aiee.kicad_sym`.
The tell is an untracked top-level `lib/` in `git status` plus an empty `boards/<name>/lib/`.

Recovery is safe and cheap: fix the two URIs, copy `aiee.kicad_sym` / `aiee.pretty` / `aiee.3dshapes`
into the board's own `lib/`, delete the stray root `lib/`, and re-run the pull (registration is
idempotent). Do this before P4 - after P5 the board file carries absolute-ish footprint references
and the cleanup gets more expensive.

Related, same run: **easyeda2kicad numbers a D2PAK/TO-263 MOSFET's drain terminal pin 4 and omits
pin 2 entirely** (the cropped centre lead has no land, so the pulled footprint has 3 copper pads:
1 = gate, 3 = source, 4 = the 8.4 x 10.6 mm tab = drain). The datasheet/JEDEC numbering is 1/2/3+TAB.
A schematic that wires the drain to pin "2" or "TAB" connects nothing, and `fp_verify` reporting
`pad_count` 4 vs 3 on such a part is EXPECTED, not a real error. Reconcile the two numbering schemes
in the part's extraction JSON before P4 wiring.

## 2026-07-28 [datasheet] JST catalog PDFs hide dimensions in a non-embedded CID font
Every dimension number in the JST PH-series drawing is drawn in a **non-embedded Adobe-Japan1
Identity-H CID font**: it renders BLANK in pdftoppm/poppler and extracts as mojibake, so the
drawing looks dimensionless in both text and image paths. Recover by decoding the CID stream
directly - **CID = ASCII - 31**, with CID 692/693/694/695 = `+ - +/- x` - using per-glyph device
coordinates, then cross-check against the drawing's own vector rectangles (the pitch gives you
the pt/mm scale). On lumina-par every pad matched within 0.001 mm. Applies to any JST catalog PDF.

## 2026-07-28 [parts][concurrency] lib_pull's --out-dir default is RELATIVE, so it lands in the repo root and is shared by every run
--out-dir defaulted to the bare string "lib", resolved against the CWD - and orchestrators run scripts
from the repo root, so omitting the flag silently created <repo>/lib and registered
${KIPRJMOD}/../../../lib/ in the board's fp-/sym-lib-table. The board's own lib/ stayed EMPTY, three
concurrent runs would have written parts into one shared library (symbol-name collisions across
boards, --overwrite clobbering), and lib_pull still reported status: pass. Found by the lumina-strobe
run as its BLOCKING-06. Fixed centrally: the default now derives from --project
(<project>/../lib), and the resolved dir is refused outright if it equals <repo>/lib (exit 2 with
remediation). Symptom to recognise: a populated <repo>/lib, an empty boards/<name>/lib, and lib-table
URIs with three or more ../ segments.

## 2026-07-28 [check_creepage][gates][magnetics] check_creepage only knows working VOLTAGE, so it cannot see a magjack isolation-barrier collapse
`check_creepage.py` derives required spacing from `constraints.json.voltages` via IPC-2221B - i.e.
purely from the DC working voltage between two nets. On a PoE board the 48 V domain gives 0.635 mm
(51-100 V band) and everything passes at that number.

That is the wrong model for an RJ45 magjack. The barrier that matters there is **chip-side to
line-side**, and it is not sized by the 57 V working voltage at all - it is sized by the cable-side
hipot requirement (1500 Vrms / 2250 VDC) and by the vendor's own land-pattern guidance. HALO's app
note asks **55 mils = 1.40 mm** at 2.54 mm pitch. `check_creepage` will happily pass a 1.05 mm gap,
because 1.05 mm > 0.635 mm and the checker has no concept of an isolation barrier.

Found on lumina-carrier while evaluating a replacement magjack: the candidate's land pattern
collapsed the chip-side/line-side pad gap from 3.58 mm to **1.05 mm** - a real defect that no gate in
the pipeline would have flagged. The P8 verify suite would have been green.

Recognise it whenever a part carries an isolation barrier that is NOT a function of the board's own
rail voltages: Ethernet magnetics, opto-isolators, isolated DC-DC, digital isolators, mains-facing
anything. For those, the spacing requirement comes from the *part's* datasheet and the safety
standard, not from `voltages[]`, and it must be enforced by hand - a `.kicad_dru` rule keyed on
`A.NetName`, plus a placement `separation` entry - and checked by a human reading the land pattern.

Related trap already recorded: `rules_gen.py` never reads the `voltages` key at all, so even the
0.635 mm figure is not enforced during routing and only surfaces at P8.

## 2026-07-28 [easyeda2kicad][parts] lib_pull reports "pulled" for parts it never pulled, once the lib is non-empty
Second, worse variant of the 2026-07-28 "lib_pull hides a failed symbol pull" entry, measured on the
lumina-par P3 pull (44 LCSC ids). `_pull_one` bails to `status: "error"` only when
`not fps and not sym_lib.exists()`. `sym_lib` is the ONE shared `aiee.kicad_sym`, so as soon as the
FIRST part of a run succeeds, that file exists and **every subsequent part reports
`status: "pulled"` no matter what happened** - including a hard `HTTP 403` where nothing at all was
written. Observed: a 44-part run logged `status: pass`, `pulled/exists = 44 of 44`, and
`load_check.ok = true`, while only **13** parts actually had both a symbol and a footprint on disk.
The per-part `footprints: []` list is the only tell in the payload, and it is empty for legitimately
shared footprints too (see below), so it is not a reliable discriminator either.
**Ground truth, and the only check worth writing:** a part is present iff `aiee.kicad_sym` holds a
top-level `(symbol ...)` block carrying `(property "LCSC Part" "<id>")` AND the `aiee:<name>` in that
block's `Footprint` property exists in `aiee.pretty`. Verify per part after each pull and re-pull the
failures; never batch-trust the exit code.
**Corollary trap:** do NOT map LCSC -> footprint by grepping the LCSC id out of the `.kicad_mod`.
Footprints are SHARED (one `C0603.kicad_mod` served 5 parts, `R0603` 12, `SOIC-14` 2) and the file
records only the FIRST puller's id, so every later sharer looks unpulled. Map through the symbol's
`Footprint` property instead.

## 2026-07-28 [easyeda2kicad][parts] The EasyEDA CAD endpoint rate-limit is a CloudFront WAF block on one path, and it clears in ~60 s
Refines the earlier "~3 full passes ... and a 60 s backoff was not enough" note with measurements.
Only `https://easyeda.com/api/products/<id>/components` 403s; `easyeda.com/` (200),
`modules.easyeda.com` (404 = alive) and the JLCPCB search API (405 = alive) all still answer, so the
block is per-path, not per-host. The body is CloudFront's `Request blocked.` page with
`X-Cache: Error from cloudfront` - i.e. a WAF/rate rule at the edge, NOT an application response.
It is immune to every client-side fix: bare UA, no UA, a full Chrome header set with
`sec-fetch-*`/`X-Requested-With`, and a real `easyeda_session` cookie fetched from the homepage all
403 identically. The only thing that works is waiting - a 1-probe-per-60 s poll recovered on the
**second** probe (~60-120 s). Budget: `--full` costs ~3 requests/part (components + 3dmodel + step);
4 parts per ~35 s tripped it after ~13 parts, while **one part per 15 s ran 31 parts with only 2
transient failures**, each cleared by a single 90 s backoff. Pace one part at a time, verify on disk,
back off 90 s on failure.

## 2026-07-28 [easyeda2kicad][placement] Pulled courtyards enclose the BODY only - 20 of 22 exclude their own pads
Measured across the whole lumina-par pull (pad bbox with per-pad rotation applied vs F.CrtYd bbox).
Every footprint HAS an F.CrtYd - `fp_verify`'s `no_courtyard` warning never fires - but the outline
is the package body, so the pads protrude by up to **3.7 mm** (TO-252 tab, `TO-252-2_L6.6-...`),
1.69 mm (SOIC-14), 1.42 mm (MSOP-10-EP) and 0.30-1.13 mm on the small stuff. Only the two THT
headers contain their pads. This is the general case of the S14 LQFP48 finding, not an outlier.
`placelib.PlacedFp.extents_local()` already unions `_pad_box_local()` into the courtyard, so P6
legality is covered - EXCEPT that `_pad_box_local` reads `(size w h)` verbatim and ignores
`(at x y ROT)`. On this board exactly one footprint is exposed: `SOT-23-3_L2.9-W1.5-P1.90-LS2.6-BR`
(C427379 BSS123, Q102) has all three pads at ROT 90 with `(size 0.700 1.250)`, so placelib builds a
0.70 mm-wide box where the truth is 1.25 mm - under-estimating each side by 0.275 mm.
Checking courtyard presence is not the same as checking courtyard adequacy; measure the pad bbox.
Corroboration from the same board: when L301's pull was swapped to KiCad stock
`Inductor_SMD:L_Cenker_CKCS4030`, the stock footprint's courtyard CONTAINS its pads with 0.25 mm to
spare (pull: pads 0.40 mm outside). "Swap to KiCad stock" fixes courtyard adequacy as a side effect.
Watch the parser though: KiCad 10 stock uses the multi-line form where `(layer "F.CrtYd")` sits on
its own line, so a line-oriented `if "CrtYd" in line` scan finds NO courtyard and reports a false
"missing" - `fplib.parse_footprint` gets this right, ad-hoc greps do not.

## 2026-07-28 [librarian][easyeda2kicad] EasyEDA rate-limits at ~31 fetches / 13 min and lib_pull reports pass while symbols silently fail
Extends the 2026-07-28 entry on `_pull_one`'s success heuristic. On a 50-part pull the anonymous
EasyEDA CAD endpoint began returning **403 after ~31 fetches in ~13 minutes**. `lib_pull.py` reported
`status: pass` for an entire 10-part batch while **7 of those 10 produced no symbol at all** - because
`_pull_one` decides success on "this LCSC id appears in some existing footprint file", and a part
reusing an already-pulled package (R0603, C0805, SOD-123 ...) satisfies that test even when the API
returned nothing for it.

**Exit code cannot detect this. Symbol COUNT can.** After a bulk pull, assert
`len(symbols in aiee.kicad_sym) == len(parts.json parts)` and check that every symbol's `Footprint`
property resolves to a file in `aiee.pretty`. Recovery that worked: 10 min cool-down, then ~20 s
per part with a 180 s backoff-retry on failure - 26/26 recovered, two parts needing one retry each.
Budget roughly one second per part of pacing on any pull over ~30 parts rather than discovering it
at P4 when a symbol is missing.

Two smaller findings from the same run:
- **`fp_verify`'s pad-size check uses the MODE, so asymmetric packages slip through.** An SSOP-20
  land pattern has pins 1-10 at 0.4 x 1.8 and pins 11-20 at 0.4 x 2.0; `_modal_size` hit a 10/10 tie,
  returned 1.8, passed, and never compared the 2.0 column (which was 0.25 mm over the datasheet max).
  The check should bound min/max, not the mode.
- **Pulled symbol libraries contain non-ASCII** (178 chars in a 50-symbol lib: CJK manufacturer names
  like `Infineon(...)`, plus Ohm and +/- signs). kiutils' `from_file` defaults to cp1252 and raises on
  these, so any downstream netlist/BOM reader must force UTF-8 explicitly.

## 2026-07-29 [parts][thermal] A bridge rectifier's CURRENT rating is not a thermal rating - check RthJA before believing "1 A"
Selecting the two PD input bridges on lumina-carrier: the obvious pick was an MB6S/MB10S in MBS,
sold as a "1 A" bridge and stocked as JLC Basic. It fails thermally at the actual operating point.

At 802.3at only ONE Alternative conducts, so a single package takes the whole load - not half of it.
`2 x Vf(0.6 A) x 0.6 A` is ~0.83 W hot / ~1.06 W with no thermal credit. Measured from the
datasheets: MBS package **RthJA 90 C/W**, mean rectifying current actually **0.5-0.8 A not 1 A**, and
Vf ~1.03 V at 0.6 A -> **Tj ~176 C**, over the 150 C limit. The ABS/SOP-4 package (ABS210, 2 A /
1000 V) is **65 C/W** -> Tj 118-133 C at the board's 64 C worst-case internal air. Same nominal
"amps", completely different answer.

Rule: for any rectifier or pass element, size on **RthJA x P_dissipated + T_ambient_internal**, not on
the headline current. And check the *mean rectifying current* spec, which is often well below the
part's marketing number. Note the JLC Basic constraint pushed toward the wrong part here - Extended
was the correct choice and the only Basic bridge available was exactly the one that fails.

## 2026-07-29 [magnetics][check_creepage] A magjack's shell board-lock pads can collapse HV-to-shield creepage independently of the isolation barrier
Distinct from the chip-side/line-side barrier (see 2026-07-28 [check_creepage][gates][magnetics]).

A magjack's shell board-lock pads are frequently the ONLY shell-to-board copper path - the large
mounting holes are usually NPTH and carry no copper. On lumina-carrier's LPJG0926HENL land those
pads sat 2.287 mm centre-to-centre from the VC1/VC4 PoE centre taps, giving **0.487 mm** of copper
against the board's 0.635 mm HV rule.

Unlike the isolation barrier, this one **IS** voltage-derived (48 V tap to a shell that is tied to
GND through the shield hybrid), so `check_creepage` DOES model it and would have failed the board at
P8 - after routing. Worth catching in the library.

The fix is constrained from both sides at once: `gap = c_c - (w_a + w_b)/2` while every pad must keep
>= 0.15 mm annular ring. On a 1.70 mm board-lock drill and a 0.90 mm tap drill the best simultaneous
result is 0.687 mm, with both annulars exactly at the 0.15 mm minimum. Making the board-lock pad OVAL
(narrow toward the tap, tall along it) buys the clearance while keeping copper under a pad that takes
insertion force. Check both numbers after any such edit - it is easy to fix creepage and silently
create an annular-ring violation instead.

## 2026-07-29 [parts] lib_pull reported every part "pulled" once ONE part had landed
_pull_one's failure gate was `not fps and not sym_lib.exists()` - but aiee.kicad_sym is SHARED across
every part in the run, so after the first successful pull `sym_lib.exists()` was permanently true and
every later part returned status "pulled" whether or not anything was written for it. Measured on
lumina-par: `status: pass`, `44 of 44 pulled`, `load_check.ok: true`, and **13 parts actually on
disk**. A half-empty library ships through a green gate and only fails much later at board_init or
fp_verify. Fixed: the gate is now `not fps` alone - _footprints_for_lcsc greps the pretty dir for
that part's own LCSC id, so it is a true per-part filesystem signal. Corollary for orchestrators:
never accept lib_pull's own count; re-derive completeness from the filesystem, which is how the
lumina-par librarian caught this.

## 2026-07-29 [gates] fp_verify has NO drill handling, so a wrong THT annulus passes silently
fp_verify.py / fplib.py model pads but not drills. On lumina-par the ICD-mandated connector geometry
(1.70 mm annulus / 1.10 mm drill) came out of easyeda2kicad as J3 1.80 mm and J4 1.60 mm on a 1.050 mm
drill, and fp_verify returned `pass` on both - because the annulus is unchecked AND the DS1023
extraction omits `pad_size_mm` (the vendor publishes none), so both halves of the check were absent.
Note the failure was ICD CONFORMANCE, not safety: as-pulled J3 still gave a 0.74 mm gap = 1.17x the
0.635 mm rule. But a frozen interface two boards mate through is exactly where conformance matters.
Verify THT annulus/drill against the ICD by hand at P3; the gate cannot do it.

## 2026-07-29 [jlc][api] First live calculate: field rules the doc tables get wrong
Scopes approved; live pd-trigger quote succeeded after two corrections the transcribed tables
could not predict: (1) insideCuprumThickness is a 4+ LAYER selection only - a 2L board rejects
it with code 2129 "Only boards with four layers or more support the selection of inner copper
weight" (table says Required=yes unconditionally); (2) isAddCustomerCode/markOnPcb/
autoConfirmProductionFile must be OMITTED from calculate - the "Yes"+2 pairing rejects with
code 2708 "The Remove Order Number error" and the doc's own calculate example omits all three
(create-side options; decide at the first gated create). CONFIRMED live: copperWeight "2"
STRING form accepted on a 2L 2oz board (watch item closed); upload->fileKey->calculate chain
works; real price 40.00 USD for 10x 48x30 2L 2oz HASL vs our 19.65 estimate - order_quote's
2oz/options undercount is large, always present the API quote. Still async/missing: pcb/audit
returns code 2501 no_audit_result_error immediately after upload (JLC DFM runs async - re-poll
later; quote proceeds without it); calculate returned NO shipList because we pass no country -
freight attestation (N3 gate) needs a country input plumbed before the first create.

## 2026-07-29 [routing][drc] A net-wide HV clearance rule makes fine-pitch HV PADS unroutable, not just tight
lumina-carrier P7. The hand-written `HV_48V_clearance` (0.635 mm, TI TPS2378 guidance) excluded
only `!(A.Type=='Pad' && B.Type=='Pad')`. That stops the rule firing between two pins of a package
but NOT between a pin and the fan-out stub that must land on it - a track inherits its pad's
neighbourhood. Measured: 15 of 37 48 V pads sit < 0.635 mm from foreign copper OF THEIR OWN
footprint (U1 SOIC-8 0.200 mm, U22 HTSSOP-20 0.250-0.375 mm, U20 pad-3-to-its-own-EP 0.295 mm,
0805 caps 0.590 mm across their own pads), so those pads could not be connected at all. Confirmed
live: a V48_RAW stub on U22 pad 6 fired the rule twice at 0.250 mm against pins 5 and 7. Fix that
held: extend the exclusion to "both items inside ONE named footprint's courtyard"
(`!(A.intersectsCourtyard('U22') && B.intersectsCourtyard('U22'))`, enumerated per refdes, never
wildcarded). Generalisation: any `clearance` DRU floor above a package's own pad gap is
unsatisfiable for that package - check `min(distance to foreign copper)` per pad BEFORE routing
(work/p7/hv_escape.py is 40 lines and answers it).

## 2026-07-29 [routing][kicad] Netclass clearance is PAD-BLIND - it cannot carry an HV rule
Same board: setting the three 48 V netclasses to 0.635 mm clearance so Freerouting would honour it
in the DSN produced 30 instant DRC errors between adjacent pins of U1/U22 and across C61/C62/C63's
own two pads - a netclass has no pad-pair exclusion and no scoping at all. The .kicad_dru rule is
the only mechanism that can express "0.635 mm except inside a package". Consequence for P7: the HV
netclass clearance must stay at the routing floor (0.2 mm) and the DRU stays the DRC authority;
Freerouting therefore routes HV at 0.2 mm and open-board HV spacing must be audited after routing.

## 2026-07-29 [routing][parts] KRT: a 3-pad diff net routes as a MESH, and only the FIRST leg closes
lumina-carrier MDI runs J1 (magjack) -> D10 (ESD array) -> U10 (PHY). Handed all three pads,
`route_diff.py` builds J1-D10 + J1-U10 + D10-U10 (a ring: 96 mm of copper for a 50 mm chain, RX
skew 51 mm); `--ordering mps` gives the same star. route_critical's own mitigation (detach the
"unmatched stub pads" - here J1's staggered magjack pads) routes ONLY the short D10->U10 leg and
silently leaves the 38 mm haul to Freerouting, i.e. uncoupled. Routing the legs sequentially does
not work either: whichever leg runs second is DEFERRED ("electrically-short"/multipoint) because
the first leg's copper already hangs off the shared middle pad. What works: route each leg on its
OWN branch from the SAME clean board (detach U10 for one, J1 for the other, via
route_critical.detach_stub_pads/restore_stub_pads) and graft the two copper sets with route_edit.
The branches cannot see each other, so give them disjoint layers - F.Cu for one leg, B.Cu-preferred
(`--layer-costs 3 1`) for the other - or they overlap and merge into a short (measured: 18
shorting_items + 3 tracks_crossing when both preferred F.Cu). Post-graft, the two legs land on the
shared pad at different points, so check_diffpair reports `branch_free: false` and falls back to
TOTAL copper length - a bogus 45 mm "skew" that a short in-pad joining segment mostly resolves.

## 2026-07-29 [parts][python] KRT: relative --work-dir silently breaks, and bash mangles net names
route_critical passes `--fab-overrides <work>/fab_overrides.txt` through unchanged and runs KRT with
`cwd=<krt plugins dir>`, so a RELATIVE `--work-dir` makes KRT die with "fab-overrides file not
found" and route_critical reports `no JSON_SUMMARY`. Always pass absolute --pcb/--work-dir. Second
trap, the LEARNINGS 2026-07-23 argv rule seen from the other side: it is not enough for the SCRIPT
to pass a list - a net name typed on a Git-Bash command line is already mangled before python sees
it (`/ETH_TXP` -> `C:/Program Files/Git/ETH_TXP`). Hard-code pair names in the driver, read them
from a file, or set `MSYS2_ARG_CONV_EXCL='*'`.

## 2026-07-29 [planes_gen][drc] Two footprint facts planes_gen cannot see: existing vias-in-pad, and touching regions
(a) `ep_pads` picks the largest netted SMD pad per footprint and grids vias into it without checking
whether the FOOTPRINT already ships thermal vias. U22 (HTSSOP-20 eFuse) carries 15 thru-hole
0.6/0.3 mm vias-in-pad in its own land; planes_gen added 21 more on top -> 24 hole_to_hole warnings
and 2 holes_co_located at 0.000 mm. Check `pads_of(ref)` for thru-hole pads inside the EP first.
(b) planes_gen only de-conflicts zone priorities for regions that OVERLAP. Three same-net plane
rectangles that merely TOUCH along a shared edge (the standard "positive rectangles cannot have a
hole" pattern for a keepout band) are still `zones_intersect` ERRORS in KiCad 10 - assign distinct
priorities explicitly. Same-net fills at different priorities still merge into one island (measured:
7162 mm2 In1 GND, 1 island).

## 2026-07-29 [drc][gates] drc_routed's --parity finds footprint/symbol mismatches the P6 place gate never runs
The place gate runs DRC without `--parity`, so `footprint_symbol_mismatch` never appears until P7.
On lumina-carrier H5 (mounting hole) had `exclude_from_bom` on the footprint and `(in_bom yes)` on
the symbol - one warning, and drc_routed fails at err+warn = 0. Its four siblings H1-H4 are
`board_only` so parity skips them entirely. Fix at the symbol (a mounting hole is not a BOM line),
not the footprint. Worth a --parity DRC at the END of P6.

## 2026-07-29 [routing][kicad] A module's own pads can sit INSIDE its declared antenna keepout
lumina-carrier's ESP32-S3-WROOM-1 keepout was authored as 10 x 22 mm at the board edge (Espressif's
normative zone is 6 x 18 mm; the extra 4 mm was margin). With U30 placed at its ICD datum the
module's outermost pad columns (pins 1/2/3 and 38/39/40) land 2.0 mm INSIDE that band, so a rule
area with "no tracks" over the literal rectangle makes six module pins unconnectable - and "copper
free on every layer" is false the moment the module is placed. Author the rule area as the true
antenna zone (measured from the footprint: `at` + courtyard extent, antenna = the last 6 mm of the
25.5 mm body) plus a margin band that stops short of the pad rows. KiCad's Specctra export DOES
carry rule areas to Freerouting as `(keepout "" (polygon <layer> 0 ...))`, one per layer - verified
in the exported DSN - so the keepout is honoured by the autorouter, and stitch_vias skips pads
inside it with `no_clear_spot`. No pipeline script creates rule areas; planes_gen only makes
positive pours, so a small bundled-python SWIG worker (`SetIsRuleArea` + `SetDoNotAllowTracks/Vias/
ZoneFills` + `SetLayerSet`) is the sanctioned way to add one.

## 2026-07-29 [easyeda2kicad][kicad] 3D-model origin offsets in renders are NOT footprint defects
pd-trigger J1 (GCT USB4105-GF-A-120, LCSC C5184243): the render showed the connector body
overhanging the board edge with contacts/peg-slots exposed behind it - user flagged possible
misalignment AFTER the fab order was placed. Ground truth: the fab drill/copper matched GCT's
recommended layout dimension-for-dimension (2x O0.65 pegs at 5.78; slots 1.0x2.1 + 1.0x1.8 at
3.68/4.18; 12x1.15 contacts 8x0.30+4x0.60 at 0.5 pitch) - the EasyEDA 3D model origin is just
~2 mm off along the mating axis. Verification path that settles it in minutes: (1) the ordered
.drl (tool table + hit coords) is the truth the fab received; (2) the manufacturer's drawing
PDF (gct.co/files/drawings/<part>.pdf worked with a browser UA; LCSC's /datasheet/<code>.pdf
URL returns HTML). Renders judge MODELS, drills judge BOARDS. fp_verify only covers parts with
datasheet extracts (pd-trigger had U1 only) - connector footprints deserve the extract+verify
treatment before ordering, and 3D-model offsets deserve a fix in the workspace lib so design-doc
renders stop alarming humans.

## 2026-07-29 [geometry][keepout][planes] Keepout checks need STRICT-interior containment: plane regions deliberately ABUT the band they exclude
Verifying that the ESP32-S3 antenna band was copper-free on lumina-carrier, a hand-written check
reported **14 violations** on In1.Cu and In2.Cu. All 14 were false.

The cause is structural, not a coding slip. The recipe that carves a hole out of a plane uses three
positive rectangles per layer, because a single positive rectangle cannot have a hole - so the middle
region is authored to **stop exactly at the keepout's edge** (`ex2 - 10`, i.e. x = 109.58, the band's
left boundary). Its fill polygon therefore *legitimately* carries vertices lying precisely ON the
boundary. An inclusive test (`BAND[0] <= x <= BAND[2]`) counts every one of them as copper inside the
band. A strict test with a small epsilon (`BAND[0] + EPS < x < BAND[2] - EPS`) returns zero.

**Any containment test against a deliberately-abutting region must be strict, not inclusive.** This
will bite every future keepout, courtyard, plane-void, board-edge and exclusion-zone check, because
"the excluded region and the thing excluded from it share an edge" is the normal case, not the
exception - that is what makes the exclusion exact.

The dangerous part is the failure direction: an inclusive test cries wolf on a board that is
**correct**, and the obvious "fix" is to tear up good copper or shrink a plane that was already right.
Fix the checker, not the board. Confirm by sweeping epsilon (0.00 / 0.01 / 0.10 / 0.50 mm) - if the
count collapses to zero at any nonzero epsilon, every hit was a boundary artifact and the geometry was
never wrong.

Related: no gate in the pipeline checks a keepout band at all. `constraints.json.placement.keepouts`
is read only by the P6 placement scripts; the router and `planes_gen` never see it, so an F.Cu/B.Cu
keepout RULE AREA has to be added at P7 and the inner-layer exclusion has to come from the plane
region shaping. Verifying it is therefore a manual geometric step - which is exactly why the checker
being trustworthy matters.

## 2026-07-29 [geometry][creepage][measurement] A pad-gap formula is only valid for one pad SHAPE - using the wrong one moves the answer by 0.3-0.5 mm in either direction
Measuring the cable-side/PHY-side barrier on lumina-carrier's magjack, I produced **three different
numbers for the same pair of pads** - 1.451, then 1.148, then 1.613 mm - and reported two of them
upstream before the third settled it. The cause was never the parser; it was applying a formula that
did not match the pad shape.

    circle-circle : gap = hypot(dx, dy) - (r1 + r2)                       <- radial form
    oval          : stadium - distance between the two spine SEGMENTS, minus radii
    rect-rect     : per-axis - dx = |dx_c| - (w1+w2)/2 ; dy likewise ;
                    gap = hypot(max(dx,0), max(dy,0))

**The radial form is EXACT for circles and overestimates rectangles** (by 0.303 mm on one diagonal
pair here). **The rect form is EXACT for rectangles and underestimates circles** (by 0.465 mm on the
same pair). I used each on the wrong shape in turn, so I first over-reported a gap, then "corrected"
a number that had been right, declared a false failure, and shrank a pad that did not need shrinking.

KiCad tells you the shape in the pad header - `(pad "2" thru_hole circle ...)` - and it is the second
token after the pad number. Read it. THT signal pads are usually `circle`; board-lock and shield tabs
are often `oval`; SMD pads are `rect`/`roundrect`. A single footprint routinely mixes all three, so a
gap tool that assumes one shape is wrong somewhere in every real footprint.

Write ONE shape-aware helper and use it everywhere. A capsule model covers all three: a circle is a
degenerate segment with a radius, an oval is a segment along its long axis with radius = half the
short axis, and only true rectangles need the per-axis form.

Two second-order lessons:
- **The direction of the error matters more than the size.** Over-reporting a gap hides a real
  violation; under-reporting invents one and invites you to "fix" correct hardware. I did both, and
  the under-report was worse - it cost a pad shrink and a retraction.
- **Re-deriving a number is not the same as re-deriving it correctly.** When a measurement is
  contested, fix the METHOD and state which model you used, rather than producing another figure.

## 2026-07-29 [routing][parts] KRT's default 200k A* iteration cap, not congestion, is what fails a long haul - and the log says which
lumina-carrier P7 spent three routers and ~10 rip-set attempts on `/FAULT` (U22 pin 15 -> the J4 end,
a 68 mm MST edge) and `/ADC0` (D41 -> U30, 59 mm). Every attempt reported `No route found after
200000 iterations (forward) ... 410000 (both directions)` and `All rip-up attempts failed`; broad rip
sets only traded one net for another. Raising `--max-iterations` from its 200000 default to 4000000
(with `--max-probe-iterations 60000`) routed BOTH on the first try, and neither needed a wider rip set
than the log's own hint list. The tell is in the rip-up ladder's own diagnostic:
`Coverage: 1863/13659 frontier cells attributed to routed nets; 11796 static/unrippable` - 86 % of the
blocking frontier was static copper (pads, 48 V tracks at 0.635 mm, plane vias), so no amount of
ripping could open it; the flood front simply needed to be allowed to go further around. Read that
Coverage line BEFORE building a rip set: high static share => raise the iteration budget, not the rip set.

## 2026-07-29 [routing][gates] KRT output boards must be refilled before DRC, or the gate reports ~375 phantom zone errors
`krt_finish.py` writes a new .kicad_pcb with fresh vias but does NOT refill the pours, so
`gate.py --gate drc_routed` on the raw KRT output reported 375 failing (261 errors) - 371 of them
`clearance`/`hole_clearance` between a NEW via and a `Zone [GND] on In1.Cu` / `Zone [+3V3] on In2.Cu`
whose fill still predates the via. Only 4 were real. Copy the KRT output over
`kicad/<board>.kicad_pcb` first (so the .kicad_pro/.kicad_prl/.kicad_dru sidecars are beside it -
kicad-cli needs them for correct rules), then
`kc.run_drc(kc.resolve_cli(), pcb, refill=True, save_board=True)`, and only then gate. Same board,
same copper: 375 -> 7.

## 2026-07-29 [routing][placement] "Widen the corridor" was the wrong diagnosis for a boxed-in QFP GND pad - measure the pad extent, not the footprint origin
lumina-carrier U10 (LQFP-48, 0.5 mm pitch) pad 9 (GND) read as needing a wider gap between the south
pad row (ends y 83.6819) and C35. A P7 predecessor moved C35 +0.300 mm in y, computing the corridor
from C35's FOOTPRINT y (85.813 - 0.45 = 85.363) instead of its pad-1 CENTRE y (85.113 - 0.45 =
84.663) - a 0.700 mm error, so the "1.6811 mm corridor" was really 0.9811 mm. The move also put pad 1
on top of an existing `/ETH_RSTn` run at y 85.55: `gate place` PASS and `check_decoupling` unchanged,
but +6 DRC errors (1 shorting_items, 4 clearance, 1 solder_mask_bridge). ALWAYS re-run DRC after a
placement micro-adjust; the place gate does not model copper.
And the corridor was never the blocker: `via_why.py` showed the real ones were a `+3V3` fan-out rail
crossing at y 84.02, a void in the In1 GND fill created by the neighbouring +3V3 plane vias' clearance
holes (the via would land in the void and connect to nothing), and `/ETH_MISO`'s B.Cu descent at
x 97.55/97.40. The pad escaped NORTH instead, into the QFP's own 7x7 mm die shadow (via at
(96.35, 80.65), 0.5 mm) - which needed only `/eth/EXRES` ripped and re-routed, because its y-81.85
horizontal was the one wall sealing the north side (0.2319 mm residual gap; a 0.1 mm track needs 0.5).

## 2026-07-29 [place][gates][routing] STOP: after P7 begins, `gate place` and `check_decoupling` are NOT valid oracles for a placement change - only DRC is
**This mistake shorted a board and both checkers said PASS.**

On lumina-carrier I moved C35 by +0.300 mm to widen a fan-out corridor on a board that already carried
**3089 routed segments**. I validated the move the way P6 taught me to:

    gate place            -> PASS (0 failing)
    check_decoupling      -> unchanged, no new warnings

Then DRC found **6 new errors including `shorting_items` and `solder_mask_bridge`**: the pad had landed
on an existing `/ETH_RSTn` run at y 85.55.

**Both oracles are structurally blind to routed copper.** `place_metrics`/`placelib` reason about
courtyards, pad extents, keepouts and the outline. `check_decoupling` reasons about cap-to-pin distance
and loop inductance. **Neither one looks at a single track or via.** They are complete oracles at P6,
when there is no copper, and they silently stop being complete the instant the router lays the first
segment - and nothing in the pipeline tells you that transition happened.

**Rule: any footprint move after routing has started must be validated with `kc.py drc` (or the
`drc_routed` gate), not with the place gate.** Use the place gate as a NECESSARY-but-not-sufficient
pre-filter: it still catches courtyard and keepout illegality, which DRC will not phrase as such.

Two aggravating factors worth knowing:
- The correct sequence is snapshot -> move -> **DRC** -> place gate -> check_decoupling, and revert on
  any new DRC violation. I had the snapshot (which is why the revert was clean) but ran the checks in
  the wrong order and stopped at the first PASS.
- The same move was ALSO built on a bad measurement: the corridor figure read the footprint's `(at ...)`
  ORIGIN y rather than the **pad-1 centre** y, off by exactly 0.700 mm - so a claimed 1.681 mm corridor
  was really 0.981 mm. When measuring clearance to a specific pad, resolve the pad, never the footprint
  origin; on a 0603 they differ by roughly half the pad pitch.
- And it could not have worked regardless: the true blocker was a **void in the In1 GND fill** created
  by neighbouring plane vias' clearance holes, so a via in that corridor would have connected to
  nothing. Check that a plane actually has copper where you intend to land a stitching via.

## 2026-07-29 [routing][krt] KRT's iteration cap, not the rip set, is usually what blocks a long haul - and its own diagnostic tells you which
Two long nets on lumina-carrier (`/FAULT` 68 mm, `/ADC0` 59 mm) defeated roughly ten rip-set attempts.
The ladder's own diagnostic line explained why:

    Coverage: 1863/13659 frontier cells attributed to routed nets; 11796 static/unrippable

**86 % of the frontier was static**, i.e. pads, keepouts, plane edges and locked copper - things no rip
set can move. Ripping more nets could not help by construction. Raising `--max-iterations` from the
200000 default to **4000000** routed both nets on the first try.

Read that coverage line before choosing a rip set. High static fraction -> raise iterations. Low static
fraction -> a rip set may genuinely help.

Second finding from the same board: **broad rip sets trade nets 1-for-1.** One set fixed STATUS+RXD0 but
broke BOOT+I2C_SDA; the next fixed BOOT but broke ENABLE_M+ETH_RSTn. What converged was **one target net
at a time, with only its own hint list, gate after each attempt, and keep the result only if strictly
better** - otherwise revert. Keep a snapshot ladder so "strictly better" is enforceable.

## 2026-07-29 [krt][clearance] KRT's `--clearance` is a CAP on the netclass map, not a floor - but `--net-clearances` is not capped
On a board with a hand-written HV rule (0.635 mm on the 48 V nets) and a 0.2 mm general floor:

- Passing `--clearance 0.2` **silently produced 480 HV violations.** The flag caps the netclass map, so
  it pulled the HV nets DOWN to 0.2 mm rather than raising anything.
- Passing an explicit **`--net-clearances` file is NOT capped** (only the auto-read path is), which is
  how per-net HV clearance survives while everything else keeps the floor.

Also: KRT cannot read a `.kicad_dru`. Every `PWR_*` netclass in the `.kicad_pro` carried 0.2 mm and the
0.635 mm existed only in the DRU file, so the router had no way to know about it except the explicit
net-clearances file. If a hand-written DRU rule is the only thing holding a safety clearance, the
autorouter is not enforcing it - pass it explicitly and re-run DRC after.

Practical note: Git-Bash mangles a leading `/` in net names on the command line, so pass net lists via a
JSON job file rather than argv.

## 2026-07-29 [geom][drc][clearance] `bg.nets` omits UNNETTED pads, so any clearance model built from it is blind to 37 real copper items
lumina-carrier P8. A widening pass computed each undersized power segment's max width as
`2*(dist(centreline, foreign_copper) - clearance)`, iterating foreign copper as
`for onet in bg.nets: bg.net_copper(onet, layer)`. `BoardGeom.nets` contains only NAMED nets, so every
pad with no net is invisible to that loop - **37 of them on this board** (U10 x16, J1 x6, U30 x4,
U22 x4, D10, U20, H1-H5). The pass widened four +3V3 escapes to 0.500 mm and DRC came back with 4
clearance errors at **0.0665 / 0.0689 mm** against `U10-18`, an unconnected LQFP-48 pin. Fix:
`for p in bg.pads_of(): if not p.net and layer in p.layers: ...`. Same trap applies to any
`net_copper`-driven audit (stitch candidates, via placement, creepage sweeps).

Second, related: `net_copper(net, layer)` counts a through VIA's own barrel as copper on **every layer
it spans**. So "which layers does this via bridge?" answered by intersecting the via with
`net_copper` returns *all four* on a 4-layer board, always. Build the bridged-layer set from
tracks + pads + zone fills only.

## 2026-07-29 [route_edit][kicad] route_edit adds BEFORE it removes, so you cannot replace an item at the SAME position in one ops file
`route_swig.verb_apply_ops` indexes existing items first, applies every `add_*`, and only then applies
`remove` (documented at the top of the module, easy to miss). `add_via` dedups on
(position, net) with a 0.001 mm tolerance, so an ops file that does
`remove <uuid of via at P>` + `add_via at P (different size)` has its add skipped as `"exists"`, the
removal then deletes the original, and the post-apply verify fails with
`via at (x, y) (NET) not in saved board` - the whole file rolls back. Changing a via's pad size or a
track's width **at unchanged geometry** needs TWO route_edit invocations (remove, then add). Widening a
track works in one file only because the width is part of `_track_key`, so the wider copy is not a
duplicate.

## 2026-07-29 [check_diffpair][gates] check_diffpair's graph ignores vias AND pads - a 0.14 mm endpoint mismatch silently turns "skew" into TOTAL COPPER LENGTH
`net_graph()` nodes are track endpoints snapped to 3 dp; **vias and pad copper are not edges.** So two
track chains that meet only *through* a via (or through a pad's copper) land in different graph
components, `trunk_length` cannot connect the terminals, and it falls back to
`sum(all track lengths)` - reported only as `branch_free: false` in `checked`, never as its own
violation. On lumina-carrier's grafted MDI this produced "skew 35.65 mm / 45.54 mm" against a 2.5 mm
limit on pairs whose real trunk skew was 6.27 mm and 0.98 mm. Three separate causes, all
sub-0.3 mm and all electrically fine (the copper OVERLAPS in every case, so DRC and the netlist are
clean):
- `/ETH_TXN` B.Cu (93.95, 83.1851) vs (93.80, 83.35) - **0.2229 mm** apart, 0.26 mm tracks
- `/ETH_RXP` B.Cu (82.7474, 85.8649) vs (83.00, 85.85) - **0.2530 mm** apart
- `/ETH_RXP` F.Cu escape ending at (96.15, 81.85) and its B.Cu continuation starting at
  (96.05, 81.95) - joined only by the via at (96.15, 81.85), **0.1414 mm** of endpoint mismatch
Adding one same-net segment per break (3 tracks, 0.14-0.25 mm long) made both pairs `branch_free:
true`. **Always read `branch_free` before believing a skew number** - and if it is false, the number is
not a skew at all.

Second limitation in the same check: `matched_terminals` pairs a p-pad with an n-pad only within
`TERM_PAIR_MM = 2.5`. A magjack's differential pads are further apart than that (LPJG0926HENL:
TXP/TXN pads 2.84 mm, RXP/RXN pads **4.58 mm**), so J1 is not a matched terminal and the "trunk" is
measured from the ESD array to the PHY only. On this board the gate PASSED `/ETH_RXP//ETH_RXN` at
0.98 mm while the true magjack-pad-to-PHY-pad skew was **6.451 mm (43 ps)**, 4.326 mm of it inside the
J1 escape the gate never looks at. Measure connector-pad to IC-pad yourself before believing a pass.

## 2026-07-29 [check_current][gates] the via-count rule is unsatisfiable for a PLANE-fed rail, and `overrides` cannot reach it
Extends the 2026-07-28 pd-trigger entry. `check_current` needs
`ceil(current_a / via_amps)` vias in EVERY cluster, and `segment_current`'s `overrides` feed only the
track-width test - vias and pour necks always use the full rail budget. For a rail whose trunk is a
POUR, every via is a single-pin leaf tap by construction: on lumina-carrier `+3V3` (1.0 A, 0.5 A/via)
had **27 clusters of exactly one via**, each a pad -> 0.2-0.6 mm escape -> plane tap.
Machine-measured feasibility of doubling them (0.6/0.3 vias, 0.2 mm clearance, 0.5 mm hole-edge floor,
companion must sit on the net's copper on the same two layers): **2 of 44 clusters placeable**; 26 had
**zero** candidate positions because the outer-layer stub is shorter than the 0.8 mm minimum
via-to-via pitch. Doubling needs new outer-layer copper, i.e. a re-route, not a via drop. Budget for
this at P5/P7 (fan the rail out with 2-via taps from the start) - it cannot be retrofitted at P8.

## 2026-07-29 [check_creepage][gates] `voltages` is net-to-REFERENCE, so it cannot express a bridge-input PAIR - a 0.33 mm 57 V gap passes silently
`check_creepage` computes `dv = v_hv - v_other` from `constraints.json.voltages` and skips any pair
with `abs(dv) <= 30`. The four PoE centre taps are all declared 57 V, so **A1<->A2 and B1<->B2 are
never checked** - yet those are the two AC inputs of one external bridge, i.e. the full line voltage
sits between them. Measured on lumina-carrier: `/poe/POE_TAP_A1` <-> `/poe/POE_TAP_A2` =
**0.3295 mm on all four layers** (the two transition vias at (49.650, 74.213) and (49.200, 73.400),
0.9295 mm centre-to-centre, 0.6 mm pads), against an IPC-2221B B2 requirement of 0.60 mm outer.
B1<->B2 measures 1.4778 mm and is fine. Nothing in the gate suite reports the A-pair. Declare the
negative-side tap of each bridge at **-57 V** (as `V48_RTN` already is) and the pair becomes a
114 V difference the checker can see - or add an explicit `.kicad_dru` rule.

Also worth knowing: `check_creepage`'s violation `pos` is `ca.representative_point()` - a point
somewhere on the offending NET's copper, **not** the location of the tight gap. Locating a creepage
violation means re-deriving the nearest-item pair yourself.

**FOLLOW-UP (same day, after fixing it): a worst-pair-only sweep HIDES ITS SIBLINGS.** The A1<->A2
fix above moved both transition vias and took the gap to 0.6502 mm (F.Cu) / 0.6524 mm (inner, B.Cu),
DRC-confirmed. Only *then* did a re-sweep of the same net pair find a SECOND violating pair -
A1's south transition via against A2's B.Cu run at **0.5500 mm**, and A1's run against A2's corner at
**0.5826 mm**. Both were masked by the 0.3292 mm pair while it existed. So: after fixing a
geometric-minimum violation, RE-SWEEP the whole net pair; do not trust the pre-fix list, and do not
trust one violation per pair as the whole story. Same applies to any check that reports a minimum.

Two more from the same fix, both cheap and both worth doing every time:
- **A DRU rule that does not parse and a DRU rule that never fires look identical** (DRC just says
  0 violations). PROVE a new rule is live by temporarily setting its threshold to something
  unreachable and confirming it fires with the actual distances you expect, then set it back. Raising
  `poe_tap_differential_pair` from 0.60 to 0.90 mm produced 17 hits reporting `actual 0.6502 mm` and
  `actual 0.6524 mm` - which simultaneously proved the rule live AND independently confirmed the fix
  with a second oracle (KiCad DRC) instead of only the geometry script that made the fix.
- **Where a checker's model structurally cannot express a constraint, encode it in `.kicad_dru`.**
  The tap-pair gap is now held by a named rule for exactly the reason
  `magjack_isolation_barrier` exists: a voltage-derived checker cannot see a hipot/differential
  requirement, so without the rule any future reroute silently reopens the gap and nothing reports it.

## 2026-07-30 [jlcapi][order_submit][SPEND] `pcb/create` returned `unknown_error` code 2 on HTTP 200 - and there is NO way to ask the API whether the order landed
First live `--api-create` on this account. Everything upstream was clean - `uploadGerber` ok,
`audit` ok/200 with `red[]` and `yellow[]` empty, `calculate` returning the exact authorised
43.19 USD - and `pcb/create` came back **HTTP 200 with `{"code": 2, "message": "unknown_error"}`**,
trace `4e0f08d4e3c04a9b80b19553624c43a8`. `classify()` has no rule for code 2, so it falls through to
the generic `error` bucket and `REMEDIATION` has no entry, i.e. **the tool cannot tell you what to do
next and neither can the API**.
**The dangerous part is not the failure, it is the unobservability.** The Open API exposes
`pcb/order/detail` and `pcb/wip/get`, and **both require a `batchNum` you must already hold**;
`wip/get` with an empty payload returns `param_empty` (code 3), and there is **no list/search endpoint
at all**. So after an ambiguous create you cannot ask "did an order appear on my account?" - the only
oracle is the JLCPCB web portal. Combined with the fact that an HTTP-200-plus-business-error is
indistinguishable from a partially-applied create, **a blind retry is how you buy the board twice.**
Protocol that follows, and it should be in the playbook: on any create verdict that is not `created`,
STOP. Do not retry. Report the trace id, state that the created-latch is unarmed (so the tool WILL
allow a retry - the latch is not protecting you here), and require a human portal check before any
second attempt. The latch only arms on success, which means it defends against a *deliberate* second
order and not at all against an *ambiguous first* one - the exact case that costs money.

## 2026-07-30 [order_submit][stackup] `derive_copper_oz` silently defaults to 1 oz on a heading it cannot parse, and says "no stackup.md" when the file exists
`derive_copper_oz` scans `architecture/stackup.md` for a line starting with `## Chosen`. This board's
heading is `## 1. Chosen stackup` - numbered - so `startswith("## Chosen")` is False, the loop finds
nothing, and the function falls through to the **wrong** terminal message:
`"no architecture/stackup.md -> default 1 oz"` on a board whose `stackup.md` is present and 
readable. Two separate defects in one line: a parser that misses any numbered heading, and a
diagnostic that blames a missing file for a parse miss.
It was harmless here only because the board really is 1 oz (JLC04161H-3313). The module's own
docstring calls copper weight "a board-killer" and `_check_oz_mentions` exists specifically to stop a
2 oz design being quoted at 1 oz - so a 2 oz board with a numbered stackup heading would defeat that
guard **silently and by default**, which is the failure the guard was written to prevent. Match on
`"## " ... "Chosen"` anywhere in the heading, and distinguish "file absent" from "no Chosen line
found" in the source note.

## 2026-07-30 [fab_export][order_submit][jlcapi] The gerber sha256 is NOT a design fingerprint - every export changes it, so a sha-bound order latch self-invalidates
`fab_export.py` re-exports gerbers whose headers embed a creation timestamp, so **re-running it on an
unmodified board produces a different zip sha256**. Measured on lumina-carrier: sha
`3afe6590...` -> `4e192c7e...` across two exports 55 minutes apart with **zero** design change - and the
only differing line in the entire 12-file package was
`; DRILL file KiCad 10.0.3 date 2026-07-30T07:07:03` vs `...T08:02:01`. Everything else, including all
four copper layers, was byte-identical.
Two consequences that matter:
1. **A changed sha does not prove the design changed**, and a coordinator or reviewer asking "re-upload,
   the board changed" may be reacting to nothing. Diff the package with the date lines filtered
   (`grep -v -iE "creationdate|generation software|^G04 .*20[0-9][0-9]"`) before believing it.
2. `order_submit`'s order latch is **gerber-sha-bound**, so a harmless re-export invalidates a
   previously approved quote token and forces a re-upload + re-audit. That is fail-safe (it errs
   toward re-checking before money), but it means **the sha cannot be used as a "has the design
   changed" test** - only as a "is this exact file the one I quoted" test.
Fix worth having: a canonicalised design hash (copper + drill coordinates only, headers stripped) as a
separate field alongside the file sha.

## 2026-07-30 [jlcapi][order_submit] The Open API has NO assembly surface at all - it cannot answer any PCBA question, even to refute one
Confirmed by grepping the entire `calculate` response and request: the only assembly-adjacent keys in
the whole payload are `stencilFee`, `originStencilMoney` and `stencilLayer` - and a stencil is a
PCB-side product. `calculate_request` carries exactly `{achieveDate, country, fileKey, orderType,
pcbParam}`: **there is no `bomParam`, no `smtParam`, no assembly quantity, no BOM or CPL reference.**
`pcb_cost_info` is entirely fabrication line items (`insideCuprumThicknessFee`, `adornPutFee`,
`halfHoleFee`, ...).
So questions of the form "does ordering bare PCBs now foreclose assembling a subset later?" are
**not answerable from this API in either direction** - it has no concept of an assembly order to
relate a bare order to. Extends the existing "PCB ordering only, no assembly/PCBA API" limit with the
practical corollary: do not attempt to infer PCBA business rules from `calculate` output, and say
plainly that it cannot be determined rather than reasoning from the absence of a field.
Useful real numbers the API *does* give, which the estimator does not: at qty 10 the bare-PCB total was
**35.47 USD**, of which **17.07 USD (48 %) is `insideCuprumThicknessFee`** - the inner-copper-weight
charge on a 4-layer 1 oz stackup. `order_quote`'s estimate for the same 10 boards was 9.90 USD of PCB,
i.e. **3.6x low**, because it models outer-layer area and not inner-copper weight. Also: JLC rejects
non-standard quantities (`pcb_qty_error` code 2103 for qty 14) but accepts 5/10/15/30, and PCB price is
**not monotonic per unit** - 10 boards cost 35.47 (3.55 each) while 15 cost 62.70 (4.18 each).

## 2026-07-30 [board_init][dfm][gates] `min_hole_to_hole: 0.25` at severity WARNING is the SECOND sub-fab floor in the shipped `.kicad_pro` - and it hid two real defects until P9
Companion to the `min_track_width: 0.1` entry, same shape, found the same way. The generated
`.kicad_pro` carries `min_hole_to_hole: 0.25` where **every** JLC profile in
`reference/jlc_capabilities.yaml` says `min_hole_to_hole_mm: 0.5` - and it is severity **warning**.
Consequence on lumina-carrier: two drill pairs at **0.3136 mm** and **0.3505 mm** were above 0.25 and
therefore never fired, surviving to `gate dfm` as 5 errors (each defect produced one hole-to-hole plus
one or two copper-clearance findings, because the annular rings were 0.0136 / 0.0505 mm apart).
Raising the floor to 0.5 mm and the severity to `error` still leaves the board at DRC 0/0 - the
board-wide minimum is **0.5016 mm** across 440 drills - so this costs nothing and would have caught
both at P7.
**Standing check at P7 entry, now covering two fields**: assert
`.kicad_pro.min_track_width >= profile.min_trace_width_mm` AND
`.kicad_pro.min_hole_to_hole >= profile.min_hole_to_hole_mm`. Better: have `rules_gen` emit an
`aiee_hole_to_hole_floor` DRU rule alongside `aiee_track_width_floor`, so a hand-written `.kicad_dru`
that replaces the generated file loses both floors loudly rather than silently.
Margin note worth a policy decision: after the fix, 38 drill pairs sit between 0.50 and 0.56 mm, the
tightest at **0.5016 mm** - legal, but 1.6 micrometres over the fab floor. A router that optimises to
the bare limit leaves nothing for drill wander.

## 2026-07-30 [kicad][connectivity][route_edit] A track endpoint inside a VIA or PAD is connected; inside another TRACK's body it is NOT
Machine-verified while repairing a deleted via's stub. KiCad's connectivity treats an endpoint landing
inside a via or a pad as connected, but an endpoint landing inside another **track's body** is not -
even well within that track's half-width. A replacement bridge that ended **0.0368 mm** from the stub
end, i.e. deep inside its 0.250 mm half-width, still produced `track_dangling` in DRC.
So: **deleting a via that a track end rested on always requires the replacement to share an EXACT
vertex with that track end**, not merely to overlap it. Snap to the endpoint coordinate, do not
approximate. This is the same family as the 2026-07-29 `check_diffpair` finding that sub-0.3 mm
endpoint mismatches split a net's graph into separate components - two different subsystems, one
underlying rule: **KiCad joins copper at coincident vertices, not at overlapping geometry.**

## 2026-07-30 [stitch_vias][dfm] `stitch_vias` puts a stitch 0.35 mm from a thermal-via drill INSIDE the pad it is stitching
Both P9 DFM hole-to-hole defects on lumina-carrier were generator output, and one was this: a GND
stitch via at (44.46, 96.46) landed 0.3505 mm from U22 pad 21's own plated thermal-via array - 15
drills on a 1.3 mm grid inside the HTSSOP-20 exposed pad. `stitch_vias`' hole floor is a centre-point
test that does not read footprint-embedded plated pad drills at all (extends the existing note that its
hole floor cannot see slot extents). It should (a) read footprint pad drills, applying the `-frot` /
`-prot` position and shape transforms already recorded here, and (b) skip candidates inside a pad that
already carries its own via array.
Recovery that worked, and the reasoning is the reusable part: **move the via rather than delete it**,
to the centroid of the surrounding thermal-via cell - that held the via count constant, so no
`check_current` cluster changed and `check_thermal`'s `nearest_via_mm` moved only 0.27 mm. When you
must delete instead, check which member of a via cluster you remove: on the +3V3 cluster here,
removing one via left a 2-via cluster that still passes `check_current`'s 1 A / 2-via rule, while
removing the *other* would have split it into two 1-via clusters and ADDED two violations.

## 2026-07-30 [place_edit][placement][silk] Moving a footprint on a ROUTED board: two things nothing in the pipeline does for you
From a scoped P6/P7 backward edge that moved 5 parts on a board with ~3294 tracks and landed at
`drc_routed` 0/0 - but only on the second attempt.
1. **`place_edit` / `place_metrics` have no silkscreen model.** Attempt 1 was courtyard-legal AND
   copper-clean (0 DRC errors) and still failed the gate with **39 silk warnings**, because
   `drc_routed` counts warnings. A courtyard-legal move is not a legal move. Until `placelib` owns a
   silk legality class, build the obstacle set from KiCad's own
   `TransformShapeToPolygon` / `TransformTextToPolySet` (see `work/p8/silk/probe_geom.py`) and screen
   candidates against it before touching the board.
2. **Moving a footprint ORPHANS the GND stub and stitching via that served its pads.** Nothing rips
   them; they become `track_dangling` / `via_dangling` warnings that fail `drc_routed`. On this edit
   that was 4 stubs + 4 vias, removed by hand after confirming nothing else touched them. A
   `place_edit`-adjacent detach/reattach-pad-stubs helper should own it
   (cf. `route_critical.detach_stub_pads`).
**Correct op order, and it is the whole difference between this and the C35 short earlier in the run:**
rip the affected nets FIRST (`route_edit` remove by uuid), THEN move the parts, THEN route fresh.
Moving parts while their old copper is still attached is what lands a pad on a live run.
Refinement to the transform note above: a footprint text field's stored `at` **angle is ABSOLUTE**,
not local-plus-footprint, even though its **position** is local. A hand-rolled model that adds the two
mis-rotates the refdes obstacle of every rotated part - R34's label is 1.21 x 2.68 mm tall, not
3.0 x 1.15 wide - and then silently optimises against the wrong obstacle set. And a 3-char refdes at
size 1.0 / thickness 0.15 inks to **2.64-2.69 x 1.16 mm** (advance ~0.845 x size), so guessing 0.8 or
0.95 per character both give wrong answers. Probe it, do not assume it.

## 2026-07-30 [check_return_path][stackup] On an F / GND / +3V3 / B stackup the check fails EVERY B.Cu trace by construction - name it as a waiver CLASS
`check_return_path` compares a signal layer against the nearest plane carrying its DECLARED reference.
On lumina-carrier (F.Cu / In1=GND / In2=+3V3 / B.Cu) the nearest plane to any B.Cu trace is **+3V3**,
so every GND-referenced B.Cu run reports `corridor_void` no matter how good the layout is. All 13
current findings are this, including the 2 that appeared the moment three oscillator nets were
declared in `high_speed`. Consequence worth internalising: **declaring a net can only ever raise the
finding count**, and on this stackup a B.Cu route is unfixable-by-construction, so the count is not a
quality signal. Either resolve the reference plane per signal layer (the real fix) or record it once as
a stackup waiver class - otherwise every future B.Cu route silently adds errors nobody can clear, and
teams learn to ignore the check.

## 2026-07-30 [kicad_dru][check_creepage][safety] A DRU rule that covers an HV net against SOME neighbours reads as protection and is not - audit COVERAGE, not existence
The most important systemic finding of the lumina-carrier run. The board declared **7 nets at
|V| >= 30 V** and had 5 hand-written HV rules, and a reviewer still found two sub-requirement 57 V
gaps on a board reporting **0 DRC errors**. Cause: only **3 of the 7** (`V48_RAW`, `V48_RTN`,
`+48V_SW`) were named in a *general* clearance rule. The four `/poe/POE_TAP_*` nets appeared only in
rules that constrained them against a NAMED subset - `magjack_isolation_barrier` (taps vs the four
`/ETH_*` nets) and `poe_tap_differential_pair` (taps vs each other). Nothing held a tap to 0.635 mm
against the rest of the board, so:
- `/poe/POE_TAP_A2` <-> `/poe/LED_Y_A` sat at **0.2031 mm** (217 pairs), and
- tap <-> J1 board lock sat at **0.6029 mm**,
both invisible to DRC *and* to `check_creepage` (the latter for separate reasons - netless pads have
no declared voltage, and equal-potential pairs are skipped).
**Standing check after authoring any HV rule set**: enumerate every net in `constraints.json.voltages`
with `|V| >= 30` and assert each appears in at least one clearance rule whose condition does NOT
restrict `B` to a named list. Grep for `B.NetName ==` in your own rules - every occurrence is a
coverage hole unless a general rule also covers that `A`.
Second, narrower trap in the same family: **netless conductors**. J1's two board locks are plated
3.2 mm pads on all four layers with no net, 0.66 mm from 57 V. `check_creepage` cannot see them
(no net -> no declared voltage -> no `dv`), and a `.kicad_dru` pad-pair exclusion - added for the good
reason that a lead frame is not a routing choice - also excludes them, because the tap side is a pad
too. Reach them with `B.NetName == ''`, and set the threshold to what the LAND can actually achieve
(0.60 mm here, IPC-2221 B2), not the board-wide aspiration, or the rule is permanently waived.

## 2026-07-30 [board_init][rules_gen][dfm] `min_track_width: 0.1` is below EVERY JLC profile - and this is the second time the same defect bit
Already recorded on 2026-07-29 from the DFM side; repeating the actionable half because it cost a
second fix cycle. `board_init.write_pro(pro_path, min_track: float = 0.1)` hard-codes 0.1 mm.
`reference/jlc_capabilities.yaml` minimums are 0.1016 (4-layer 1 oz), 0.127 (2-layer), 0.1524 (2 oz) -
**all coarser**. `rules_gen` emits the correct floor plus an `aiee_track_width_floor` DRU rule, but a
hand-written `.kicad_dru` REPLACES that file rather than extending it, so on lumina-carrier the floor
was absent and 189 tracks were laid at exactly 0.1000 mm - legal against the board's own DRC, and
**1.6 micrometres** under what the fab can make.
Two rules: (1) a hand-written `.kicad_dru` must START from the `rules_gen` output, never replace it;
(2) assert `.kicad_pro.min_track_width >= jlc_capabilities[profile].min_trace_width_mm` at P7 entry.
The general form of the lesson is the dangerous part: **`drc_routed` 0/0 does not imply
manufacturable**, because DRC checks the board against rules the pipeline itself wrote.

## 2026-07-29 [silk][place_edit][kicad] `GetTextBox()` is 1.70 mm where DRC's INKED stroke box is 1.16 mm - use the wrong one and correct refdes targets look infeasible
Measured on KiCad 10.0.3 at text size 1.0 mm / thickness 0.15 mm: `PCB_TEXT.GetTextBox()` reports
**1.6965 mm** of height, but the polygon DRC actually tests - `TransformTextToPolySet`, the inked
stroke outline - is **1.162 mm**. Sizing refdes placement off `GetTextBox` therefore over-constrains by
about **0.27 mm per side**, which on a 2.5 mm-pitch passive row is the difference between a solvable
label and an "impossible" one. This is what let a greedy solver take lumina-carrier from 95
mis-attributed refdes to 3 on its first clean DRC run.
Four more facts from the same pass, all measured against real DRC on KiCad 10.0.3:
- KiCad **does** report `silk_overlap` between a footprint's own outline and its own reference field -
  a part's silk is not exempt from its own label.
- `silk_over_copper` is silk vs **pad mask apertures only**. Tracks and vias never triggered it across
  283 sampled violations, so a refdes over a bare track is not a DRC finding (still bad practice).
- `silk_edge_clearance` fires on actual clipping when `min_silk_clearance` is 0;
  `min_copper_edge_clearance` (0.5 mm here) does **not** apply to silk.
- `place_edit._parse_board_texts`' local->absolute transform
  (`ax = fx + lx*cos + ly*sin`, `ay = fy - lx*sin + ly*cos`) was verified exact on all 116 footprints
  across 0/90/180/-90 degrees. Trust it; do not re-derive the signs by hand.
Also: a **sandbox DRC** - copy `pcb + pro + dru + sch` to scratch - reproduces `drc_routed` exactly at
**3.4 s/run**. That is what makes "DRC is the only oracle" affordable when you have 111 candidate
edits to screen, and it carries zero risk to the live board. Do this instead of hoping a geometry
model agrees with the gate.
Caveat on the metric itself: `refdes_prox.py` compares a LOCAL offset magnitude against a RADIAL
pad-corner extent, so it is direction-blind and scores large rectangular parts generously (the ESP32
module's label sits 0.064 mm off the module outline yet scores `beyond = -3.17`). `offset_max` alone is
not a quality signal - read `beyond_extent` counts.
Corollary for `lib_refdes_norm.py`: its formula should be
`min(pad_top, silk_top) - margin - inked_h/2`. As written it ignores the footprint's own silk outline
and uses `size/2` for the half-height, so applying its targets verbatim to a placed board produced
**283 DRC warnings across 85 of 111 refs**. The targets are the right *starting point*, not the answer.

## 2026-07-29 [check_creepage][gates][ipc] `check_creepage` cannot express a COATED board, so it over-reports every outer-layer pair on any soldermasked design
`check_creepage.py` hardcodes exactly two rows of IPC-2221 Table 6-1 -
`CLEAR_EXTERNAL = [0.10, 0.10, 0.60, 0.60, ...]` and `CLEAR_INTERNAL` - and its own docstring names
them "Bare board", i.e. **B2 external UNCOATED** and B1 internal. There is no flag, no
`constraints.json` key, and no per-net way to say the board has soldermask. Every real board does.
The full table has seven rows, and the 51-100 V band spans **0.10 to 1.50 mm** depending on which one
applies (verified 2026-07-29 against ema-eda, Altair Pollex - which quotes 6.3.4 verbatim - and
protoexpress; all three agree):

| row | 51-100 V | 101-150 V | applies to |
|---|---|---|---|
| B1 | 0.10 | 0.20 | internal conductors |
| B2 | 0.60 | 0.60 | external conductors, UNCOATED, <= 3050 m |
| B3 | 1.50 | 3.20 | external conductors, uncoated, > 3050 m |
| **B4** | **0.13** | **0.40** | **external conductors with permanent polymer coating** (any elevation) |
| A5 | 0.13 | 0.40 | external conductors, conformal coating over the assembly |
| **A6** | **0.50** | **0.80** | **external component lead/termination, UNCOATED** |
| A7 | 0.13 | 0.40 | external lead/termination with conformal coating |

**B4 is 4.6x more permissive than B2 at 51-100 V.** Cost on lumina-carrier: the single reported item
`/poe/POE_TAP_A2 <-> /poe/LED_Y_A at 0.2031 mm` is trace-to-trace under soldermask, so its real
requirement is 0.13 mm and it **passes with +0.073 mm** - but it was reported as a 3x shortfall,
generated a 217-pair population, consumed two bounded fixer attempts, and produced one edit that had
to be applied, tested and retracted. That is the most expensive single tool gap this run hit.

**The subtlety that is easy to get wrong** (and that a first reading of this got wrong): exposed
copper does NOT fall back to B2. IPC-2221 6.3.4 says, verbatim: *"The assembly electrical clearances
of lands and leads that are not conformably coated require the electrical clearance requirements
stated in category A6."* B2/B3 describe a board with no coating **at all**. A masked board whose lands
are open by mask relief puts those lands on **A6 (0.50 mm at 51-100 V, 0.80 mm at 101-150 V)**. So the
adjudication is per-ITEM-TYPE, not per-layer: masked trace or tented via -> B4; exposed land -> A6;
inner layer -> B1. Note A6 is *stricter* than B2 in the 101-150 V band.
Fix proposal (recorded, not implemented): a `--coating {none,soldermask,conformal}` flag or a
`constraints.json` `"coating"` key, plus per-item-type row selection. Until then, **re-adjudicate every
outer-layer creepage finding by hand before spending any fix budget on it**, and record which row you
used - the interpretation of LPI mask as "permanent polymer coating" is industry-standard but is a
judgement the owner should get to see, not one a tool should make silently.

## 2026-07-29 [check_creepage][gates] the violation COUNT is not a violation count - it reports only the WORST pair per net pair, and hid 216 siblings
`check_creepage` emits one violation per (net_a, net_b, layer) at the minimum spacing. On
lumina-carrier the single reported item `/poe/POE_TAP_A2 -> /poe/LED_Y_A on F.Cu: 0.203 mm` was
**1 of 217 pairs under the 0.600 mm requirement** (17 of 36 LED_Y_A segments against 21 of 27 A2 F.Cu
segments, spanning 0.2031-0.5071 mm over roughly 10 mm of parallel run). A fixer that sizes a repair
from the one reported coordinate is sizing a 10 mm reroute from a point sample, and a reviewer reading
"15 creepage errors" is reading a number that understates the real defect count by more than an order
of magnitude. **Always re-sweep the whole net pair before believing, or acting on, a creepage count.**
Same shape as the sibling-masking note above, but worse: there the siblings appeared only after the
worst was fixed; here 216 of them were never reported at all.
Fix proposal for the script: emit every pair below the requirement, or at minimum carry a
`pairs_under_requirement` count and the distribution alongside the minimum.

## 2026-07-29 [check_current][gates] no bridge awareness: the script asks the fixer for a parallel path it cannot itself detect
`check_current`'s `undersized_track` fires per SEGMENT with the full rail current, and its own
remediation guidance tells the fixer to check whether a parallel same-net path carries some of it -
but the script has no connectivity graph, so it cannot answer its own question. On lumina-carrier all
five investigated segments turned out to be **cut edges (bridges) in their net's graph**, so each
really did carry the whole rail and the widths were genuine defects; but that had to be proven by
hand. ~40 lines (build the net graph from track endpoints, find bridges) would let the check label
each undersized segment `bridge: true|false`, which is the difference between "this is a real
bottleneck" and "this is one of four parallel feeds". Worth doing before anyone waives a width
finding on a parallelism assumption.

## 2026-07-29 [geometry][pads][python] roundrect pads carry 20 points, not 4 - a `sum/len` centroid silently drops them all
A shape-aware pad model that windows candidate pads by centroid-from-vertices (`sum(pts)/len(pts)`)
works for rect (4 points) and breaks for **roundrect**, whose corner arcs are emitted as ~20 points -
the average is pulled off the true centre enough to fall outside the search window, so every roundrect
pad vanishes from the model. Consequence measured on lumina-carrier: a "feasible" solve that placed a
via **inside U22 pin 11's pad**, caught only by a full-board pre-flight against real DRC rather than
by the solver's own model. Two corollaries: (a) window pads by their BOUNDING BOX, never by a
vertex-average centroid; (b) roundrect corner radius is load-bearing, not cosmetic - modelling
U22-11 (1.575 x 0.40, rratio 0.25) as a sharp rectangle understated its gap by ~0.041 mm and flipped
one item from "capped at 0.4624 mm" to "ceiling 0.5453 mm", i.e. from unfixable to nearly fixable.
Related: a circumscribed CIRCLE for a 1.90 x 2.50 rect pad hid a real 1.100 mm ceiling entirely.
This geometry (rotated rect + arc-approximated roundrect + oval stadium + circle + zone fills)
now exists three times over in `work/` scratch files on this board alone
(`p7/pad_gap.py`, `p8/tapcreep/capsule.py`, `p8/hvpwr/board_model.py`) because the first two run
argparse at import and cannot be imported. It belongs in `scripts/lib/`.

## 2026-07-29 [constraints][gates] two copies of constraints.json, two different answers - 61 vs 53 undersized_track
The playbook says sidecars resolve from the BOARD's directory from P5 onward, so
`kicad/constraints.json` is canonical - but `architecture/constraints.json` survives as the P2 record
and nothing keeps them in step. On lumina-carrier the architecture copy still carried
`V48_RAW current_a: 1.5` after the P8 correction to 1.0, and a check run against it reported
**61** `undersized_track` instead of **53**. Nothing warns you which file you loaded. Either delete
the architecture copy at P5 or (as done here) correct it and keep the original value in a
`_p2_original_current_a` field with the reason - annotation alone in a long `_comment` is too easy to
miss, and a stale current rating is how a wrong number gets quoted at P9.

## 2026-07-29 [board_init][rules_gen][dfm][gates] `board_init` writes `min_track_width: 0.1`, BELOW every JLC profile - so `drc_routed` 0/0 does not mean fabricable
`board_init.write_pro(pro_path, min_track: float = 0.1)` hard-codes 0.1 mm into
`design_settings.rules.min_track_width`. **Every** JLC profile in
`reference/jlc_capabilities.yaml` is coarser than that: 4-layer 1 oz is
`min_trace_width_mm: 0.1016` (4 mil), 2-layer is 0.127, 2 oz is 0.1524. `rules_gen` DOES emit the
right floor (`min_track_width: cap["min_trace_width_mm"]` plus an `aiee_track_width_floor` DRU rule),
but on lumina-carrier the `.kicad_dru` was hand-written for the 48 V rules and therefore contains
**zero** `aiee_*` rules, and `board_init` was re-run three times (`work/board_init{,2,3}.json`), so
the 0.1 default is what survived into the `.kicad_pro`.
Consequence measured on a board sitting at `drc_routed` **0 errors / 0 warnings**: `gate dfm` reports
**189 errors of "trace width 0.1000 mm below JLC minimum 0.1016 mm"** (136 F.Cu + 53 B.Cu) out of 194
total. The traces are legal against the board's own DRC floor and unmanufacturable against the fab's,
and the gap is **1.6 micrometres** - far too small to notice by eye in any report.
Two rules follow: (1) whenever you hand-write a `.kicad_dru`, START from the `rules_gen` output and
add to it - do not replace it, or you silently drop the fab floor, the annular floor and the
hole-size floors; (2) if `board_init` is ever re-run after `rules_gen`, re-run `rules_gen`
afterwards. Cheap standing check at P7 entry: assert
`.kicad_pro.min_track_width >= jlc_capabilities[profile].min_trace_width_mm`.

## 2026-07-29 [parts][silk] easyeda2kicad puts EVERY refdes at a blanket (0,-4.0) mm regardless of part size
Every footprint pulled by lib_pull carries its reference text 4.0 mm above the part origin - which on an
0603 (pad extent ~1.2 mm) puts the label ~3 mm clear of its own body and frequently nearer a NEIGHBOUR
than its own part, so a populated board cannot be read. Nothing catches it: KiCad's silk DRC checks
overlap/edge/copper, never proximity-to-owner, and check_silk only covers silk-over-pad. Measured on
lumina-carrier: median refdes offset 4.079 mm, and 100 of 116 labels more than 1 mm beyond their own
part's pad extent. It is a LIBRARY defect, inherited by every board built from the library, so fixing it
per-board is treating the symptom. New script `lib_refdes_norm.py --lib <dir>.pretty` re-derives each
offset from the footprint's own pad bbox (0603 -> -1.18, 0805 -> -1.44, 1210 -> -2.1, WROOM -> -10.15);
idempotent, text-surgery only, verified to keep all footprints parsing under kicad-cli 10.0.3 and fplib.
Run it after every lib_pull, BEFORE board_init - once footprints are placed the offsets are copied into
the .kicad_pcb and only place_edit move_text can fix them.

## 2026-07-29 [geometry][fixer] A min-over-endpoints segment distance reports a SHORT as clearance
`work/p7/pad_gap.py`'s `seg_seg_dist`, copied verbatim into `work/p8/tapcreep/capsule.py`, computes the
minimum over endpoint-to-segment distances. That is exact for DISJOINT segments but returns a positive
nearest-endpoint number for two segments that properly CROSS - so a dead short reads as clearance. A P8
fixer's optimiser found and exploited it, producing an "opens by +0.0037 mm" candidate that DRC rejected
with 2 x "Tracks crossing"; the fixer's own full-board pre-flight passed it too, because the pre-flight
used the same primitive. Fix is a `seg_cross()` test returning -1.0 on intersection before measuring
(reference implementation: `work/p8/hvpwr/board_model.py::gap_of`).
Scope, verified: the SKILL's checkers are unaffected - none of them use this primitive, and
check_creepage measures with shapely `.distance()`, which correctly yields 0 for intersecting geometry.
So the damage was wasted fixer iterations, not corrupted gate results, and any board that passes
drc_routed 0/0 provably has no crossings, which makes its disjoint-segment gaps exact.
Rule for fixers: ad-hoc geometry helpers must test intersection FIRST, or just use shapely (already a
dependency). And a model-based pre-flight gates cheap iteration - it never substitutes for the gate.

## 2026-07-29 [parts][silk] Refdes placement must clear the footprint's OWN silk, and layer names are unquoted in easyeda2kicad output
Two bugs in the first cut of lib_refdes_norm.py, both found only by DRC and by inspecting raw files:
(a) it reserved `size/2` for the text half-height, but KiCad's DRC measures the INKED box - glyph height
PLUS stroke thickness (measured: nominal 1.0 + thickness 0.15 -> 1.162 mm inked, while GetTextBox
reports 1.6965 mm). Correct reservation is `(size_h + thickness)/2`.
(b) it positioned labels clear of PADS only, ignoring each footprint's own silkscreen outline - which on
an 0603 sits at y=0.71 vs the pad edge at 0.45, so the label landed on the part's own outline. Applied
verbatim it produced 283 DRC silk warnings across 85 of 111 refdes. Correct form:
`min(pad_top, silk_top) - margin - inked_h/2`.
And the trap that hid (b): silk layer names are **QUOTED** in KiCad-10 stock footprints, `(layer
"F.SilkS")`, but **UNQUOTED** in the older format easyeda2kicad emits, `(layer F.SilkS)`. A regex
requiring quotes matched nothing, reported silk_top=None for every part, and produced a plausible wrong
answer with no error - the same silent-failure shape as the min-over-endpoints distance bug. Related:
KiCad 10 stock also uses a multi-line `(layer "F.CrtYd")` that line-oriented greps miss.

## 2026-07-30 [pipeline] There is no incremental board-from-netlist update, so adding ANY part after P5 costs all of P6+P7
board_init.py regenerates a .kicad_pcb from a netlist; place_edit's op vocabulary is move/rotate/flip/
lock/add_text/move_text and CANNOT add a footprint; no script anywhere calls AddFootprint or syncs a
board against a changed schematic. So a P4 rewind that adds even one passive discards the entire
placement and routing - there is no KiCad-GUI-style "update PCB from schematic". On lumina-carrier this
forced two genuine electrical fixes to be deferred to rev B rather than applied: an HF input ceramic on
the 3.3 V buck (which had one 22 uF at 9.2 mm / 8.378 nH and no ceramic, feeding the MCU, the W5500 and
every daughter) and PESD clamping on +3V3 / the LED nets. Plan for it: get decoupling and protection
parts into the schematic BEFORE P5, because after that the choice is a full re-route or a deferral.

## 2026-07-30 [gates][dfm] drc_routed 0/0 does NOT imply fabricable - board_init's track floor is below every JLC profile
board_init writes `min_track_width: 0.1` mm, but JLC's minimum is 0.1016 mm (4 mil), so a board can route
to 0.1000 mm tracks, pass drc_routed at 0 errors AND 0 warnings, and then fail the P9 dfm gate - on
lumina-carrier, 189 of 194 dfm errors were "trace width 0.1000 mm below minimum 0.1016 mm", a 1.6 um gap
repeated 189 times. It is worse when the board carries a HAND-WRITTEN .kicad_dru (as this one does for
its 0.635 mm HV rule), because that file then contains none of rules_gen's fab floors at all. Fix at P5:
set the floor to the fab profile's minimum, and if you hand-write a .kicad_dru, port rules_gen's floors
into it rather than replacing them.

## 2026-07-30 [ordering][impedance] insideCuprumThickness was hardcoded to 1 oz, fabricating a different stackup than the one designed
order_submit.build_pcb_param set `insideCuprumThickness: "1"` on EVERY 4+ layer order, copied from the
JLC create-example. JLC's standard 4-layer inner copper is 0.5 oz. Two consequences, the second serious:
(a) it silently bought a premium - measured `insideCuprumThicknessFee` $17.07 on lumina-carrier, 48% of
the whole PCB cost, which is also why order_quote read 3.6x low against the real API price; and (b) it
would have FABRICATED A DIFFERENT STACKUP than the impedance was solved against. lumina-carrier targets
100 ohm differential MDI on JLC04161H-3313, whose inner layers are 17.5 um / 0.5 oz; 35 um inner copper
moves the reference spacing and the controlled impedance with it - on a board that is 4-layer solely to
hold that 100 ohm. Nothing downstream would have caught it: the gates check geometry against the stackup
file, never against what the order actually asks the fab to build. Now derives from
spec.inner_copper_weight_oz, defaulting to 0.5. Rule: any pcbParam value taken from a vendor EXAMPLE
rather than from the board's own stackup is a fabrication defect waiting to happen.

## 2026-07-30 [ordering] JLC Open API `pcb/create` refuses 4-layer boards with an unclassified `code 2`, while `calculate` accepts them
Three live attempts on lumina-carrier (4L, 100x80, qty 10, US, HKTHZXR-RMB) all returned HTTP 200 with
`{"code": 2, "message": "unknown_error"}` - traces 4e0f08d4…, f90769b3…, b1eba7f4… - differing only in
`insideCuprumThickness` ('1' / '0.5' / absent). Control: pd-trigger (2L) created successfully as
W2026073002475378 on the SAME account, API, shippingMethod and country, with a byte-identical payload
shape and byte-identical values for every shared pcbParam field. `calculate` priced the 4L board
correctly at every variant; only `create` refuses. So the cause is not the payload, the ship method, the
freight, the confirm token or the account scope - the remaining differences are `layer` (2 vs 4) and
board size. Treat 4-layer API ordering as UNAVAILABLE until JLC support explains code 2; the manual cart
works and the fab package is independently JLC-audited.
Two related tooling gaps this exposed: (a) jlcapi.classify() has no rule for code 2 and REMEDIATION no
entry, so an unclassified business error yields no guidance; (b) the created-latch arms only on SUCCESS,
so it guards a deliberate second order but not an ambiguous FIRST one - the case that actually risks
double-buying. And there is no order list/search endpoint at all (`order/detail` and `wip/get` both
require a batchNum you must already hold), so after an ambiguous create the portal is the only oracle.

## 2026-07-30 [stackup][ordering] reference/stackups.yaml's JLC04161H-3313 IS NOT A REAL JLC STACKUP
Verified live: `getImpedanceTemplateSettingList` for 4L / 1.6 mm / 1 oz outer / 0.5 oz inner returns
exactly three templates - `JLC04161H-自定义` (20220811093551), `JLC04161H-1080B` (202601040426384154,
L1-L2 0.2444 mm, core 1.065 mm) and `JLC04161H-7628G` (202607130748059522, L1-L2 0.5124 mm, core
0.500 mm) - and **no -3313**. Swept plateType 0-3 and delamination 0-3; only plateType 1 returns data
and it always returns the same three. All three carry `enableFlag: false`.
This matters because stackups.yaml's -3313 entry (prepreg 0.2104 mm, er 4.05) is the ONLY 4-layer entry
publishing a 100 ohm differential profile, so it is the reason lumina-carrier is 4-layer at all and it
produced the routed MDI geometry 0.260 / 0.210 / 0.470 mm. Measured against the real templates at that
same geometry: 1080B = 105.5 ohm (+5.5%), 7628G = 127.6 ohm (+27.6%), vs 99.9 ohm on the phantom.
1080B is acceptable for 100BASE-TX (2.7% reflection, spec allows +-15% on the cable) but it is a
DIFFERENT BOARD than the one solved for, and nothing in the pipeline would have said so.
Fourth instance on this board of the same failure mode - after the hardcoded 1 oz inner copper, the
`## Chosen` heading miss, and `impedanceFlag: "no"` on an impedance-controlled design - where the ORDER
asks the fab for a different board than the one designed and verified. Gates check the design against
its own stackup file; nothing checks the stackup file against what the fab actually sells.

## 2026-08-06 [ordering] order_track.py sees WEB-created orders fine - `order/detail` keys only on batchNum, not on how the order was created
lumina-carrier's 4-layer order failed via `pcb/create` (code 2, see the entry above) and was placed
manually through the JLC cart instead, so the workspace never captured a batchNum the way pd-trigger's
API-created order did. Owner supplied the web order's batch number (W2026073100331078) this session;
`order_track.py --workspace boards/lumina-carrier --batch W2026073100331078` returned a normal `pass`
payload on the first try - status Shipped, total_money 15.72, same shape as pd-trigger's API-created
order. No distinction in the response between an order the API created and one placed by hand in the
browser: `pcb/order/detail` (and by extension `pcb/wip/get`) is keyed purely on batchNum/orderUUID, which
the cart UI surfaces to the human the same way batchNum is returned from `create`. Recorded the number
into `fab/order.json.order_number` (no `api.order` block, since the API never created it) - order_track's
`_batch_from_order_json` already falls back to `order_number` when `api.order.batchNum` is absent, so no
code change was needed. Answers the open question from S12/T0: a 4-layer web order is trackable exactly
like an API order, once you have its batch number in hand.
Rule: validate any impedance-controlled stackup against the vendor's live template list BEFORE solving
geometry against it, not at order time.

## 2026-08-06 [kicad-sch-api][python] `Schematic.save()` SILENTLY GUTS lib_symbols for any symbol its cache cannot resolve
kicad-sch-api 0.5.6 re-serialises the whole `(lib_symbols ...)` block on save from its GLOBAL symbol
cache, which never reads `sym-lib-table` (see the 2026-07-28 `--pins` entry). Load and save
`boards/pd-trigger/kicad/pd-trigger.kicad_sch` as-is and the file goes 8879 -> 4359 lines: every
`aiee:` definition disappears, with no error, no warning and exit 0. The schematic still parses, so
nothing downstream notices until KiCad opens it. Register the project library FIRST -
`ksa.get_symbol_cache().add_library_path(<lib>/aiee.kicad_sym)` before `Schematic.load` - and the
round trip is faithful (8879 -> 8879, 92 cosmetic `(at x y 0)` -> `(at x y 0.0000)` diffs only,
measured on both pd-trigger and the s7 fixtures). Rule for ANY ksa write path: register the project
libs, then assert the lib_symbols NAME SET is unchanged after save before trusting the file
(`schem_refdes.write_placements` does exactly this and raises otherwise). The T3 plan called for
placement "via kicad-sch-api"; this is why the READ side is a direct s-expression parse instead - the
schematic's own embedded lib_symbols needs no library resolution at all.

## 2026-08-06 [kicad-sch-api][silk][python] ksa places instance property positions with the WRONG Y SIGN, mirroring every field to the far side of its part
Library symbol coordinates are y-UP, page coordinates are y-DOWN, so KiCad maps a library offset
`ly` to `page_y = py - ly`. kicad-sch-api 0.5.6 ADDS it (`page_y = py + ly`), so every Reference and
Value lands mirrored through the symbol origin. Measured on the s7 blinky2 fixture: C1 at page
(152.40, 240.03), Device:C Reference library offset (0.635, +2.54) -> written at page y 242.57
(BELOW the cap) while its pin at library (0, +3.81) is wired at page y 236.22 (ABOVE) - the pin
proves the sign. Net effect on a generated sheet: reference below / value above, the reverse of
KiCad's convention, and on a dense sheet the mirrored fields land on wires, labels and neighbours
(27 overlaps on blinky2 counted against symbol bodies, pin-number bands, wires and labels).
`schem_refdes.py` re-places both fields from a class table and reports what it cannot clear. Note
the fields are cosmetic - ERC stays 0 and the exported netlist is byte-identical either way - so
nothing in the pipeline catches this; it is a readability defect only a human or this script sees.

## 2026-08-06 [easyeda2kicad][drc][parts] The whole known-bad pull set is 16 DRC violations, and one measured recipe takes it to 0 - but only if filled graphics are left alone
A live pull of the four worst-known parts (C14663 0603, C2286 LED, C7421520 3-pos DIP switch,
C5184243 GCT USB-C) DRCs at **16 violations = 4 errors + 12 warnings** on a scratch board
(annular_width x2, clearance x2, padstack x2, silk_overlap x8, silk_over_copper x2) - the exact
numbers the three shipped boards' `EDITS.md` recorded by hand. `fpfix.py` now applies the same
recipe at pull time and measures **0** after. Three things the generalisation turns on:
(a) narrowing is arithmetic, not judgement: a stroke clears copper by `d_centerline - w/2`, so the
widest legal width is `w_max = 2*(d - min_gap)`, floored to the 0.05 grid and never below JLC's
0.15 mm line width. Violators sharing an ORIGINAL width narrow together, which reproduces the
approved hand edit exactly (C0603 0.25 -> 0.20, gaps 0.135 -> 0.160; LED glyph 0.25 -> 0.15, gaps
0.105/0.135 -> 0.155/0.185).
(b) a FILLED graphic must be exempt from the "promote sub-0.15 strokes so they print" rule: its ink
comes from the fill, and widening the stroke grows the printed shape. The DIP switch's three solid
slider indicators are `fp_poly` at stroke 0 - promoting them would have silently redrawn the part.
(c) `Path("SW-SMD_6P-...-LS9.3-BL").stem` returns `...-LS9`: footprint names contain dots, so stem
strips a fake extension and the footprint is skipped with no error. Match on the literal
`.kicad_mod` suffix instead.
Unchanged conclusion from the 2026-07-28 CORRECTION: verify silk claims with `kc.py drc`, never with
a geometry script alone.

## 2026-08-06 [schematic][placement] On a generated sheet the space above and below a symbol is NOT free - the corners are
The S1/S7 generator pattern stubs a wire and a local label at EVERY pin, so the bands directly above
and below a block symbol are crossed by a wire every 2.54 mm for the symbol's whole width: on
blinky2 the STM32's centred-above and centred-below candidates, and every 1.27 mm slide along them,
all collide. The free area is DIAGONALLY outside the pin bounding box corners, where no stub runs -
which is where a human puts the label too. A field placer that only offers above/below/left/right
will report false residue on any real sheet; add corner rings (and for power symbols, lateral
offsets - two rails 5.08 mm apart cannot both carry a centred `PWR_FLAG`, whose text is 7.5 mm
wide). Second trap in the same area: KiCad prints each pin NUMBER alongside its pin line, outside
the body and absent from the library graphics, so a field cleared against pin lines alone still
lands on printed text - model the pin as a band (0.7 mm) not a line.

## 2026-08-06 [stackup][jlcapi][ordering] JLC's impedance-template list CHURNS - and the endpoint lies quietly when you under-specify it
T1 re-probed `pcb/getImpedanceTemplateSettingList` (read-only, free) to rebuild
`reference/stackups.yaml` from what JLC actually sells. Three facts worth keeping:
1. **The list changes week to week.** For 4L / 1.6 mm / 1 oz outer / 0.5 oz inner, 2026-07-30
   returned THREE templates and 2026-08-06 returns TWO: `JLC04161H-7628G`
   (202607130748059522) was withdrawn in between, same account, same request. So "verified
   live" has a shelf life measured in days: re-probe before solving impedance geometry, and
   record the date next to the numbers (stackups.yaml now does, per entry).
2. **`insideCuprumThickness` is REQUIRED even where it is meaningless.** Omit it and the API
   answers `{"code": 3, "message": "param_empty"}` - not an empty list. A 2-layer probe
   without it therefore looks identical to "no 2L templates exist". WITH it, 2L returns a
   legitimate empty list at both 1 oz and 2 oz: JLC sells no impedance-controlled 2-layer
   stackup, which is a real (negative) answer worth recording.
3. **Inner copper weight selects a different template FAMILY**, not a variant: 1 oz inner
   returns `JLC041611-7628E/J/K/L`, 0.5 oz inner returns `JLC04161H-1080B`, 2 oz outer
   returns `JLC04162H-7628A`. Ordering a different `insideCuprumThickness` than the stackup
   was solved for silently buys a different lamination (see the 2026-07-30 entry).
Also: `enableFlag: false` on EVERY template of every family, so it does not mean "not
orderable" - do not gate on it. The API returns materials + thicknesses but NO dielectric
constant, so er stays an assumption (stackups.yaml flags `epsilon_r_assumed: true`; V12).
Re-probe recipe: `jlcapi.session_from_env().post_json("/overseas/openapi/pcb/getImpedanceTemplateSettingList",
{"stencilLayer": 4, "stencilPly": 1.6, "cuprumThickness": "1", "insideCuprumThickness": "0.5", "plateType": 1})`.

## 2026-08-06 [fab_export][ordering][dfm] The exact volatile set in a KiCad fab package - 5 line forms + 2 JSON keys
Implementing T1's normalized design hash (`lib/fabhash.py`) needed the complete list of what
KiCad restamps on every export. Measured against real 10.0.3 output, it is exactly:
gerber `%TF.CreationDate,..*%`, `%TF.GenerationSoftware,..*%`, `G04 Created by KiCad (PCBNEW ..) date ..*`;
Excellon `; DRILL file KiCad .. date ..`, `; #@! TF.CreationDate,..`, `; #@! TF.GenerationSoftware,..`;
and in the `.gbrjob` the JSON keys `Header.CreationDate` + `Header.GenerationSoftware`.
The `.gbrjob` is the trap: `GenerationSoftware` is an OBJECT spanning four lines, so a
line-filter normalizer silently leaves the version behind and the "design hash" drifts on a
KiCad upgrade. Parse that file as JSON and drop the keys recursively.
`%TF.ProjectId` is NOT volatile (stable per project) - keep it hashed, it catches a board
exported from the wrong project. Verified end to end: two real `fab_export` runs of an
unchanged golden board produce different zip sha256 and IDENTICAL design hashes, while moving
one track coordinate changes the design hash (tests/test_fab.py::test_design_hash_*).

## 2026-08-06 [board_init][rules_gen][gates] Raising the .kicad_pro floors to the fab profile at ERROR costs the corpus nothing
The fear that stopped this fix earlier ("stricter floors will fail existing boards") is
unfounded, measured: with `min_track_width` at the profile value, `min_hole_to_hole` at
0.5 mm and both checks pinned to `error` (plus clearance / annular / hole_clearance /
copper_edge_clearance / drill_out_of_range), `board_init` on golden usbbuck4 still
self-checks at parity 0 / setup_violations 0, and all three goldens stay DRC-clean.
lumina-carrier measured the same thing from the other side: its tightest drill pair is
0.5016 mm across 440 drills.
The reusable root cause is not the numbers, it is that **two different writers owned the same
block** - `board_init.write_pro` hard-coded floors while `rules_gen.update_pro` derived them
from `jlc_capabilities.yaml`, so whichever ran LAST won and re-running board_init silently
downgraded the board. One source (`lib/fabfloors.py`) + `check_pro()` asserted by both
writers is what actually fixes it; the severities are stated explicitly so the project never
inherits a KiCad default (`hole_to_hole` defaults to *warning*).

## 2026-08-06 [tests][skill] A live-run fix that does not update its test leaves the suite RED and nobody notices
`test_build_pcb_param_inner_copper_by_layer_count` asserted `insideCuprumThickness == "1"`.
The 2026-07-30 lumina-carrier run FIXED that hardcoded 1 oz inner copper (it was buying a
premium and fabricating a stackup the impedance was not solved against) but did not update
the test, so `check.cmd` was already failing when T1 started - and the whole point of the
green-suite session protocol is that a red suite is a stop sign, not background noise.
Rule: when a live run edits a script's constant, `grep tests/ for the OLD value` in the same
change. And a session that opens on a red suite must say so before adding to it.

## 2026-08-06 [tests][freerouting][skill] Wave-1 parallel sessions make the route_auto completion assert flaky - re-run it ALONE before believing it
`test_route_auto_full_flow` asserts `completion >= 0.9` on blinky2. It failed at **0.8182** in a
full-suite run while three other v2 sessions (T2/T3/T4) were running their own suites against the
SAME working tree, and passed minutes later in isolation with nothing changed. Freerouting is
time-bounded (`-mp` passes plus route_auto's wedge timeout), so CPU contention ends a pass early
and the failure reads exactly like a routing regression.
Two consequences for the v2 protocol: (a) a tree-wide pass count from a parallel wave is not
attributable to one session - report YOUR files' counts and say the full number is shared;
(b) T5's stage bench must keep wall-clock and completion-ratio metrics out of the deterministic
class, or run the bench alone. Standing rule: re-run a failing smoke test alone before treating it
as a regression, and never "fix" a threshold from a contended run.

## 2026-08-06 [bench][kicad-cli][tests] A frozen KiCad fixture is the file PLUS its stem-matched project files
T5's first P7 bench run reported **122 DRC errors on the shipped pd-trigger board** (114
clearance + 8 copper_edge_clearance) - a board that shipped DRC 0/0 - and 60 ERC warnings on
its schematic. Cause: the fixture copy was renamed (`routed.kicad_pcb`), and kicad-cli resolves
`<stem>.kicad_pro` / `<stem>.kicad_dru` as SIBLINGS BY STEM - netclass clearances, custom
rules and the severity map all live there, so the orphaned board was judged under KiCad
defaults. With per-role dirs keeping the ORIGINAL stem (`route/pd-trigger.kicad_pcb` +
`.kicad_pro` + `.kicad_dru`) the same board scores 0/0 and the sheet ERCs clean. The golden
boards never showed this because they are referenced IN PLACE next to their project files.
Rule: a KiCad artifact fixture = artifact + stem-matched project files, all sha-pinned;
pure-geometry consumers (geom.py, the P8 checks) are exempt - they never read the project.

## 2026-08-06 [bench][schematic][geometry] Label-vs-symbol-body overlap is a pin-line artifact - measure label-vs-label only
benchlib's first `label_collisions` metric counted label text boxes against symbol bodies too.
Measured on the SHIPPED pd-trigger sheet: **50 of 50 hits were a local label grazing the pin
LINE of the very pin it names** (`Sheet.body()` includes pin segments; a stub label sits at
the wire end by construction) - zero label-label overlaps. The dirty s7 blinky2 fixture shows
the real signal the other way: 11 genuine label-label text overlaps. So the v0 metric counts
ONLY label/global/hier/text box PAIRS; field-vs-body coverage already exists in the
schem_refdes audit (which models pin-number bands properly instead of raw pin lines).

## 2026-08-06 [git][gates][windows] `git status --porcelain` collapses an untracked directory to one `dir/` line - scoped staging must use `-uall`
T6 gate.py commit scoping: the workspace filter matched porcelain paths against the
`boards/<name>/` prefix, but for a NEW workspace porcelain emits a single collapsed
`?? boards/` line, so the "inside the workspace" set came up empty and the scoped commit
refused with "nothing to commit inside the workspace" while the files sat right there.
Silent-failure shape: no error, just a wrong empty set. `git status --porcelain -uall`
lists every untracked FILE individually and the prefix filter works. Any code that
partitions porcelain output by path prefix (scoped staging, litter assertions, workspace
diffs) needs `-uall` or a directory-aware matcher.

## 2026-08-06 [kicad-sch-api][kicad][python] ksa mirrors PIN POSITIONS at 90/270; KiCad rotates FIRST then mirrors - and ERC cannot see swapped pins
Two transform facts settled by the T6 rotmirror fixture (kicad-cli 10.0.3 ERC + netlist
oracle, tests/fixtures/sch/rotmirror), correcting the 2026-07-28 "inward stubs" entry:
(a) The V19 inward-stub defect was NEVER stub_dir's sign - stub_dir's composition matches
real KiCad at every rotation. kicad-sch-api 0.5.6 `get_component_pin_position` rotates the
wrong way at 90/270, returning the true position MIRRORED through the anchor (R(-t) vs
R(t); at right angles the correction is a point reflection). ERC-measured: rot-90 Device:R
pin1 (lib (0,+3.81)) is truly at anchor x-3.81; ksa says x+3.81. schlib.pin_pos now
reflects ksa's answer back through the anchor for rot 90/270, so generators may rotate
freely. (b) KiCad composes instance transforms ROTATION FIRST, THEN mirror (page frame).
schem_refdes.to_page originally mirrored first - identical results for mirror-only or
rotation-only instances, but a rot90+mirror part gets its two pins SWAPPED. ERC is blind
to this (the pin position SET is unchanged - both wires still land on pins); only the
exported netlist shows the wrong memberships. Any transform fixture must therefore assert
per-pin NET assignment, not just ERC 0/0.
## 2026-08-06 [gerbonara][gerber][dfm] gerblib LayerGeom.pads holds PRIMITIVE FRAGMENTS, not one polygon per flash - a KiCad RoundRect aperture is 9 of them
T6 P9-3 (check_pad_tented): _flash_polys expands each Flash via to_primitives(), and
KiCad's RoundRect aperture MACRO decomposes into 9 primitives (1 box polygon + 4 corner
circles + 4 edge rects), so ONE paste aperture contributed 9 entries to lg.pads and the
first tented-pad implementation reported the same pad 9 times. Area/union checks never
noticed (they union everything first), which is why this stayed invisible since S12.
Any NEW check that iterates lg.pads per-aperture (counting, per-pad predicates) must
group by connected component first - LayerGeom.components() is the honest unit.

## 2026-08-06 [bench][git][windows] sha-pin INDEX content, not working-tree bytes - stale CRLF checkouts and CRLF-writing tools break pins on fresh clones
T6 P3-BENCH pinning tests/fixtures/lib/pristine: the working copies were CRLF while the
index was LF (`git ls-files --eol`: i/lf w/crlf) - they were checked out BEFORE
`.gitattributes` pinned `* text=auto eol=lf`, and git rewrites eol only at checkout, so
the stale smudge persists indefinitely with `git diff` EMPTY the whole time. A sha256 pin
of those working-tree bytes passes every local test and drift-refuses on every fresh
clone (which checks out LF). Two more CRLF producers in the same capture path: kicad-cli
netlist export writes CRLF on Windows, and Python `write_text` default-translates 
 to
os.linesep (bench.py's own --baseline files land CRLF). Rule: capture fixture bytes from
the INDEX (`git show :path`) or explicitly LF-normalize before hashing, and verify with
`git ls-files --eol` that w/ matches i/ for every pinned file.

## 2026-08-07 [gates][dfm][pipeline] The dfm gate's freshness inputs are the BOARD + schematic + parts.json - NOT the shipped gerber zip
gate.py run_dfm exports gerbers to a SCRATCH dir from the .kicad_pcb on every run and reads the
sibling schematic (CPL-polarity oracle) + parts.json (BOM leg); the fab/<board>_gerbers.zip it never
touches is an ORDERING artifact whose freshness is the T1 design-hash latch's job. Consequence for
invalidation (T7): a pcb edit makes the dfm GATE stale via the pcb hash, while the shipped zip goes
stale only via the edit-class MARK - its bytes (and even its design hash) are unchanged on disk until
re-export, so no hash comparison can see it. Wiring the zip as a dfm gate input would have made dfm
immune to board edits made after the last export - the exact staleness the map exists to catch.
Encoded in reference/invalidation.yaml gate_inputs (verified against gate.py run_report_for_gate).

## 2026-08-07 [pipeline][state] v1 artifact-registry names COLLIDE with v2 kinds - a name match is not a kind override
state.json v2 lets a registry entry named for a standard kind redirect that kind's path (non-standard
layouts opt in). But v1 registries reused those names for DIFFERENT pipeline artifacts:
lumina-strobe registered "constraints" = architecture/constraints.json (the P2 architecture output),
while the verify/place gates read kicad/constraints.json - same name, different artifact. Blessing
the name match at migration redirected gate-input hashing to the architecture file: hashes then
track a file the gate never reads, and an edit to the real sidecar is INVISIBLE - missed staleness,
the failure class T7 exists to kill, silently reintroduced by its own migration. Caught by
diff-eyeballing the migrated output, not by the (passing) tests. Fix is three-sided: a kind override
requires entry.kind == kind (statelib.kind_path), migration types an entry only when its path IS the
kind's default location, and rehash never re-infers kind from an entry's name (the auto-register on
the next record-gate reclaims the slot with the typed entry). Regression:
tests/test_state_v2.py::test_migration_does_not_bless_name_collisions_as_kind_overrides.

## 2026-08-07 [kicad][connectivity][board_update] Body-overlap evidence is CONTRADICTORY (T-junction lobes pass DRC, a lapped stub gets flagged) - copper-removal decisions need baseline subtraction, not a smarter join rule
Two machine-verified observations on 10.0.3 that no single join rule explains: (a) on the frozen
pd-trigger route fixture, two wide VBUS fan-in lobes (2.0 / 1.75 mm) end with their far endpoint
INSIDE another VBUS track's body (a T-junction, no shared vertex) and the board reports DRC
**0 errors / 0 warnings**; (b) an adversarial-review probe on the same fixture injected a
DRC-clean chain whose last segment body-crossed the F.Cu GND island with its free endpoint just
off the fill - after its neighbor was removed, KiCad flagged THAT track `track_dangling` even
though its body lapped its own net's pour. So body overlap sometimes reads as connected (a) and
sometimes not (b), and the earlier 0.0368 mm cap-to-cap flag (2026-07-30) adds a third data
point. Consequence for `board_update`'s orphan surgery: do NOT encode any body-overlap mercy
(the first build's "pour-lap veto" kept exactly the track KiCad then flagged, hard-rolling-back
a legitimate del_part). Safety comes from two layers that need no connectivity theory:
(1) **baseline subtraction** - anything the analysis cannot anchor on the PRE-edit board is kept
untouched and reported (`netconn_unanchored_kept`), so a live T-fed chain is never ripped; and
(2) the **DRC dangling delta gate** - if the staged board carries more `track_dangling` /
`via_dangling` than the original, the whole update rolls back. Pattern worth reusing: when the
tool's own connectivity model is uncertain, diff AGAINST THE BASELINE and let KiCad's DRC
arbitrate the delta, rather than arguing with its connectivity rules.

## 2026-08-07 [board_update][placement][drc] An overlap-only copper test places pads 0.05 mm from a pour - candidate scans must inflate by the clearance floor
First live add_part smoke: `resolve_placement`'s region scan scored candidates by pad-copper
INTERSECTION with foreign nets only, picked a spot whose GND pad sat **0.0500 mm** from the VBUS
pour, and DRC failed it against `aiee_clearance_floor` (0.1524 mm) - a "clear" candidate by the
scan, an error by the fab rules. Overlap tests answer "does copper touch?", clearance rules ask
"is there a GAP?", and the gap between those questions is exactly the clearance floor. Fix:
inflate each candidate pad polygon by PAD_CLEAR = 0.2 mm (covers every JLC floor,
0.1016-0.1524 mm) before the foreign-copper intersection test. General rule: any
generator/placement heuristic that screens against existing copper must test at
copper + clearance, never bare copper - DRC remains the truth, the screen just has to be on the
same side of it.

## 2026-08-07 [kicad-cli][kicad][intake] KiCad 10 ships its OWN demos in KiCad-9 format, and `sch upgrade` does not follow hierarchical sheets
Facts probed on this host while building `intake.py` (T9), all on the 10.0.3 pin:
- `C:/Program Files/KiCad/**10.0**/share/kicad/demos/ecc83` is `(version 20241229)` pcb +
  `(version 20250114)` sch, both `generator_version "9.0"` - a 10.x install is NOT evidence that
  the files beside it are 10-format. Our own goldens are the same shape on the schematic side
  (kicad-sch-api writes 20250114), so intaking a golden upgrades its `.kicad_sch`.
- `kicad-cli pcb upgrade FILE` / `sch upgrade FILE` rewrite IN PLACE (no --output) and are
  idempotent: on an already-current file they print "Board/Schematic file was not updated" and
  leave the bytes byte-identical; otherwise "Successfully saved ... using the latest format"
  (pcb -> 20260206, sch -> 20260306). Exit 0 either way.
- A file NEWER than the pin exits non-zero with "Failed to load board". That failure IS the
  version probe: intake needs no hardcoded format-version table - it upgrades a COPY and lets
  kicad-cli decide readable/not.
- `sch upgrade` operates on ONE file: upgrading `pic_programmer.kicad_sch` (20260101 -> 20260306)
  left its child `pic_sockets.kicad_sch` at 20260101. Walk the hierarchy (`(property "Sheetfile"
  "...")`) and upgrade every sheet, or the project ends up mixed-version.
- Side effect: `sch upgrade` drops a `<stem>.kicad_prl` beside the schematic.
Consequence for any importer: upgrade the copies, then assert that all files of ONE TYPE agree on
their format version - upgrading is precisely the cure for a mixed SOURCE, so "refuse mixed
versions" only means anything AFTER the upgrade pass.

## 2026-08-07 [gerber][dfm][gerblib] A user-RENAMED copper layer changes the gerber FILE NAME, and every name-keyed consumer goes blind
The ecc83 demo declares `(0 "F.Cu" signal "top_cu")` - KiCad lets users rename copper (and user)
layers, and `kicad-cli pcb export gerbers` names each file after the layer's USER name:
`ecc83-pp-top_cu.gtl`, `ecc83-pp-bottom_cu.gbl`. `gerblib`'s `_COPPER_RE` keyed on the canonical
token (`-F_Cu|B_Cu|In\d+_Cu`), matched nothing, and `dfm_check` died with "no copper gerbers
found" - the whole P9 gate was unavailable for that board, on a package kicad-cli had exported
perfectly. Our corpus never renamed a layer, so nothing caught it in v1.
Fix (T9): fall back to the PROTEL EXTENSION, which states the layer function regardless of the
display name - `.gtl` = F.Cu, `.gbl` = B.Cu, `.g<n>` = In<n>.Cu (verified against this repo's own
4-layer exports: `-In1_Cu.g1`, `-In2_Cu.g2`). Silk/mask/paste/edge names are safe (KiCad does not
let those be renamed). Rule: key gerbers on the extension or the X2 `%TF.FileFunction%`
attribute, never on the layer's display name.

## 2026-08-07 [kicad][intake][parts] `${KICAD<n>_FOOTPRINT_DIR}` is a KiCad-INTERNAL variable, not an OS env var - expandvars marks every stock library "missing"
`pic_programmer`'s fp-lib-table reaches 7 stock libraries through `${KICAD10_FOOTPRINT_DIR}/...`.
`os.path.expandvars` leaves those untouched (the variable exists only inside KiCad), so a naive
resolver reports seven unresolvable libraries on a completely healthy project - noise that would
train the reader to ignore the real ones. Resolve the family against the PINNED install instead,
derived from the resolved kicad-cli (`<cli>/../../share/kicad/{footprints,symbols,3dmodels,
template}`): `KICAD<n>_FOOTPRINT_DIR`, `_SYMBOL_DIR`, `_3DMODEL_DIR`, `_TEMPLATE_DIR`. A URI naming
another generation (`KICAD9_*` under the 10 pin) still resolves there - worth ONE aggregated note,
not one warning per library. `${KIPRJMOD}` is the only variable a copy-in importer must resolve
itself, and a `${KIPRJMOD}/../..`-style URI that climbs OUT of the project is the shared-library
trap from 2026-07-28 [librarian][kicad]: import the target and rewrite the URI in the COPY.

## 2026-08-07 [skill][tests][router] A `--flag` that APPEARS in a script is not a flag it accepts - validate against add_argument, or ship invocations that die instantly
T4's remediation-ref test asserts `flag in source_text`. T10 reused that rule for the task
recipes and it green-lit FOUR invocations that would have failed on first use, because a script
that drives other tools naturally contains their flags: `gate.py --pcb <board>` (gate.py's input
is POSITIONAL; the `--pcb` in its source belongs to the kc.py commands it builds), `route_auto.py
--nets X` (route_auto has NO net scope at all - that string is what it passes to KRT's route.py;
the per-net router is `route_critical.py --nets`), `lib_pull.py --full` (passed through to
easyeda2kicad), and `parts_search.py "<query>"` (the query is `--query`, not positional). The
strong form is: collect `add_argument("--flag")` declarations, and for subcommand scripts collect
`add_parser("name")` INCLUDING the loop-registered ones (state.py adds show/resume/freshness via
`for name in (...)`, so a literal-only scrape declares `state.py resume` unknown). Scope matters
though: retro-fitting the strict rule to the 14 T4 refs produces 5 findings that are ALL false -
their citation style ("route_critical.py passes `--board-edge-clearance`", "`kc.py:461-462` ...
re-run with `--verify-fill`") defeats nearest-script attribution. Strict validation belongs where
commands are written to be RUN (reference/tasks.yaml, reference/recipes/), not where they are
cited as provenance. Second half of the same lesson, found by hand right after: existence is not
SEMANTICS. `route_critical.py --nets` IS declared and passed the strict check, but it is consumed
only by the `--pad-window` probe (route_critical.py:1146-1147) - routing scope is `--only
diff|rf|power` from constraints, and NO script re-routes one named net end to end (that is a
route_edit op list). A checker can prove a flag exists; only reading the code proves it does what
the sentence around it claims.

## 2026-08-07 [kicad-sch-api][schematic][bom] DNP is unreachable from a generator, and NOTHING in the skill reads a DNP mark
Found by the lumina-par P4 `power` sheet agent, confirmed by grep. kicad-sch-api 0.5.5 exposes no
`dnp` field on `SchematicSymbol` and its writer hard-codes `(dnp no)`, so KiCad's NATIVE do-not-populate
flag cannot be set from inside a `build()`. The workaround is a `Variant=DNP` component field, which
does reach the netlist as a component property. But that only makes the mark *present*, not *honoured*:
grep across `scripts/` finds no reader of any DNP marking anywhere - the single hit is a COMMENT in
`bom_cpl.py`. `bom_cpl` builds its list from the board pos export, so every DNP option set ships as a
populated part. On lumina-par that is 9 branch-B front-end parts plus the converter-idle one-shot.
Consequence for any board using DNP option sets (sheets.md s4 rule 1 mandates they be IN the netlist,
because a DNP part still has pads and must be accounted at P6/P7): the option is correct on the
schematic and wrong on the BOM. A P9 filter keyed on `parts.json` `dnp:true` or the `Variant` field is
missing infrastructure, not a board defect - do not let it be "fixed" by deleting the parts.

## 2026-08-07 [parts][datasheet] JST PH entry direction is the LEADING letter, not the "B" in the circuit count
A P3 extract flagged `S10B-PH-SM4-TB` as side entry while a reviewer read the "B" as the JST top-entry
code, leaving a P4 open action on a connector whose exit direction drove an enclosure decision. The
datasheet settles it and the extract was right: `parts/C265014.pdf` p3 splits the Model No. table into
`Top entry type` and `Side entry type` COLUMNS - top entry is B2B..B16B-PH-SM4-TB, side entry is
S2B..S15B-PH-SM4-TB - and the same split repeats for the THT (B*B-PH-K-S / S*B-PH-K-S) and
low-insertion-force (B*B-PH-KL / S*B-PH-KL) families. So the entry code is the FIRST letter; the "B"
after the circuit count appears in every model number of the series, both columns, and carries no
entry information at all. Generalisation worth keeping: when a part-number convention and an extract
disagree, the convention is the thing to re-derive from the datasheet's own model table - a
half-remembered naming rule reads as authority and is not. Second-order fact for placement: a
side-entry PH land is ASYMMETRIC front-to-back (tabs 0.2 mm beyond the wafer's mating face, circuit
pads 5.5-9.0 mm behind it), so it has a correct orientation AND must not be mirrored - a symmetric
hole grid does not mean a symmetric part.

## 2026-08-07 [kicad-sch-api][schematic][erc] Multi-unit symbols: ksa reports EVERY unit's pins on ANY instance, so a natural placement silently floats most of the part
Found on lumina-par P4 `thermal`. `LM339LVPWR` is a FOUR-UNIT symbol (the only multi-unit symbol in
that board's pulled lib). kicad-sch-api places ONE unit per component, but `get_component_pins` /
`--pins` reports the pins of EVERY unit on ANY instance - so wiring "the symbol" the ordinary way
builds clean, renders clean, and leaves units B/C/D absent. kicad-cli ERC is what catches it:
`missing_unit [B, C, D]` plus one `unconnected_wire_endpoint` per silently floating pin (9 here:
2/4/5/8/9/10/11/13/14). Second trap on top of the first: stack the units on one anchor and the
bodies overlap, so a standard stub from one pin ENDS ON another unit's pin - here pin 7's stub
landed on pin 10 and shorted two nets that ERC then reports as a legitimate connection. Recipe that
worked: place each unit on its OWN anchor under a temporary distinct ref, wire it, then rename to
the shared ref afterwards - ksa refuses duplicate refs in BOTH `components.add` and the `reference`
setter, so the rename must come last. Generalisation: `--pins` output is a LIBRARY fact, not an
instance fact; for any symbol with units, count units first and treat the pin table per unit.

## 2026-08-07 [erc][schematic][kicad-cli] A child sheet's own ERC is uninformative - stitch it under a throwaway root to tell artifact from defect
Every lumina-par P4 sheet agent hit this independently. Running `kc.py erc` on a hierarchical child
built standalone reports a wall of findings that are ALL artifacts of having no parent: each
hierarchical label raises `pin_not_connected` ("hierarchical label in root sheet cannot be connected
to non-existent parent sheet"), each single-pin hier net raises `isolated_pin_label`, and each
CONSUMED rail raises `power_pin_not_driven` (because PWR_FLAG lives on whichever sheet owns rail
entry). Counts seen: power 4, led_if 7, control 20, drivers 22, and every one of them benign. The
useful move, which the `drivers` agent invented and the `thermal` agent repeated: stitch the child
under a THROWAWAY root plus a stimulus sheet in a scratch dir - one load per interface net, plus the
PWR_FLAGs the owning sheet will carry - and re-run. Both boards' sheets went to 0 errors / 0 warnings
that way, which is the only evidence that separates a real defect from the no-parent noise. Do this
BEFORE reporting a sheet clean; a subagent that reports raw standalone ERC counts has told you
nothing either way.

## 2026-08-07 [erc][schematic][kicad-cli] You cannot rename a single-sheet net to `/NAME` by crossing the root: one sheet pin is not a connection
lumina-par P4 root stitch. `constraints.json.high_speed` spelled the four PWM nets `/PWM0..3`, but
J4, the pull-downs and the NAND inputs all live on `control`, so KiCad names them `/control/PWMn`
and `netlist_audit` raises missing_net. The obvious fix - expose the net as a hier pin so the ROOT's
local label spells it bare - BUILDS and produces a byte-correct netlist (`/PWM0` = J4-1 + R202-1 +
U202-1) and then fails ERC: **4x `label_dangling` "Label not connected"** on the root labels. The
root subgraph is wire + label + ONE sheet pin, and KiCad 10.0.3 wants a real CONNECTION POINT there.
Two probes pinned the rule, both on the real board: a second same-named root label does NOT satisfy
it (8 errors - both labels flagged, so it is not "the name appears once"), while dropping one
`Connector:TestPoint` pin onto each root PWM wire takes the same project to 0/0. Every other root net
escapes it only because TWO children expose the net, giving the root two sheet pins - checked against
lumina-carrier, where all 70 root labels appear exactly 2-3 times. Consequence: the bare `/NAME`
spelling is available only to genuinely inter-sheet nets. For a single-sheet net, fix the CONSTRAINT
to the netlist's name (`/control/PWMn`) - every consumer keys off the exported name and none
validates its shape (constraints_lint has no net-name pattern) - or accept adding a part. Do not
reach for the .kicad_pro severity: the finding is real, it is telling you the label buys nothing.

## 2026-08-07 [datasheet][parts] TI's TPS92515HV Eq 1 and Eq 8 disagree about the freewheel diode, and re-deriving from the text alone lands on an unsafe value
Found on lumina-par P4 while fixing a dead shunt-dimming off-timer. Two compounding traps in one
vendor's own equations. (a) **Eq 9 sizes ROFF2 as if VCC charged COFF through ROFF2 alone**, which
is only true when ROFF1 >> ROFF2. With a shunt FET across the string, ROFF1 returns to the LED
anode - which the shunt pulls to ~0 V - so COFF sees a DIVIDER, and at ROFF1 = 10 k / ROFF2 = 47 k
its asymptote is 0.93-1.00 V against a 1.00 V VOFT: the timer never trips at ANY corner, every
shunted cycle stretches to tOFF(max), and the dimming linearity the network exists to protect is
lost. The failure is silent - ERC, netlist_audit and every machine gate pass. (b) Worse for anyone
re-deriving it: **Eq 1 omits the freewheel diode drop while Eq 8 includes it as 0.7 V.** Under the
physically consistent convention (and a 0.45 V Schottky) the "exact ripple match" moves to ~38 k,
which is DEAD at the cold/low-Rds corner. So the tidier-looking derivation is the unsafe one. The
constraint that actually binds is **V_inf vs VOFT across corners, not the Eq 8 off-time target** -
solve the divider asymptote first, then check timing. Method that settled it: a 243-corner box
(VCC x Rds(on) x VOFT x tolerance); 47 k failed 176 of 243, 30 k fails 0 with +12.6 % margin.

## 2026-08-07 [erc][schematic][gates] The P4 machine gates cannot see a wrong VALUE, and two hand-derivations of the same network disagreed
Three ERROR-class defects on lumina-par survived a schematic with `erc` 0 errors / 0 warnings and
`netlist_audit` exit 0, and all three were found only by an adversarial fresh-context reviewer: a
dead off-timer (values, above), unprotected comparator inputs on a harness that also carries 6.8-24 V
LED anodes, and four enable pins with no pull-down that latch their drivers ON during the +12V-before-
+3V3 power-up window. **Connectivity gates verify topology; nothing in the toolchain checks a value
against the physics of the pins it connects.** Two consequences worth building on. (1) The trap in
the enable fix is machine-checkable and general: a pull resistor must be sized against any specified
bias/hysteresis current on the pins of its net - here a reflex 100 k sits at 2.5 V from the enable
pin's own 25 uA, above its 1.0 V threshold, so the "fix" would not have fixed anything. The numbers
are already in `parts/<lcsc>.json`. (2) The comparator-hysteresis finding produced two confident and
CONTRADICTORY hand-derivations (the sheet author's and the reviewer's). It was settled only by
building an MNA nodal solver, validating it against the as-built (it reproduced the author's figures
to 0.1 K), and then re-solving - verdict: a dead open latches, as the author said, but a PROGRESSIVE
crimp failure passes through the chatter window, as the reviewer said. There is no DC-solve anywhere
in the skill, so this class of question is currently an argument rather than a gate.

## 2026-08-07 [dfm][gerblib][gates] A 1-NANOMETRE gerber round-off empties the outline, and `dfm` PASSES with two checks silently dead
lumina-carrier passed `dfm` at P9 with 0 errors. Re-run today it reports one error, from T6 Batch I's
new `dfm_open_outline`. **The outline is not broken.** Edge.Cuts exports as 8 segments / 8 endpoints /
zero dangling ends, but three corner joints disagree by exactly **1e-6 mm - one nanometre**, gerber
round-off at the format's own resolution. `shapely.polygonize` has no snap tolerance, returns nothing,
and `fab.outline` is EMPTY. `set_precision(..., 1e-5)` closes it instantly; JLC's own audit read
`setLength 100.0 x setWidth 80.0` off these very files, so it was always fabricable.
**The consequence is the finding:** `check_copper_to_edge` and the hole-to-edge leg of `check_holes`
BOTH early-return on an empty outline, silently - so that P9 PASS never evaluated either, and the
report gave no hint. This is the same class as the P9-era lesson that `drc_routed` 0/0 does not imply
manufacturable: **a green gate proves the checks that ran, not the checks it lists.**
Second trap, found while fixing the first: `gerblib` flattens Edge.Cuts arcs to **chords**, cutting a
3.0 mm rounded corner ~0.879 mm inboard. Patch the snap without also interpolating arcs and you get
two brand-new FALSE `copper_to_edge` errors on every rounded-corner board - I generated exactly those,
then cleared them by rebuilding the true arc outline (7992.27 mm2, vs the `.kicad_pcb`'s own arc-aware
7992.23 mm2) and re-measuring: zero real violations on either check.
Rule: an early-return on missing/degenerate input must emit a `skipped` finding, never silence. Any
check that can no-op needs to say so in the report, or its gate result is unfalsifiable.

## 2026-08-07 [check_creepage][constraints] A SIGNED voltage in constraints.json doubles the requirement and invents failures
T2 taught `check_creepage` to evaluate all net pairs via `dv = v_hv - v_other`. lumina-carrier declares
`V48_RAW: 57` and `V48_RTN: -57`, so the pair computes **dv = 114 V**, selects the 101-150 V IPC-2221
row, demands **0.80 mm**, and produced 8 of the board's 11 creepage ERRORS at 0.657-0.745 mm.
No such potential exists. The netlist settles it in one query: **U1 is the only part touching both
`V48_RTN` and `GND`** - the TPS2378's return IS system ground in a non-isolated PD. Two nets on one
57 V PoE bus cannot be 114 V apart. At the true 57 V the row is 0.60 mm and all eight gaps pass, as
does the board's stricter 0.635 mm house rule. Fixing one constraints line drops `verify` 108 -> 100.
The sign was written to mean "the negative rail", which is true as topology and wrong as a potential.
Rule: `voltages[]` entries are **node potentials against the board's 0 V reference**, not rail
polarity labels. Declare the reference node as 0 and everything else relative to it; if you catch
yourself writing a negative, first ask what net is 0 V - and confirm it from the netlist, not intent.
A useful tell: any `dv` above the design's own maximum bus voltage is a declaration bug, not a defect.
`constraints_lint.py` could assert exactly that.

## 2026-08-07 [placement][constraints] placement.groups is load-bearing and ALL THREE of its failure modes are silent
Three P6 defects on lumina-par, one root cause: a part that belongs to a network but is absent (or
unreachable) in `constraints.json placement.groups` is invisible to `place_seed` and `place_anneal`,
and nothing warns. (a) **A group anchored on a LOCKED ref is silently deleted** - `build_clusters`
drops satellites whose anchor is immovable, so R201 (the ICD-mandated ENABLE pull-down, anchored on
J4 precisely to hold it at the connector) was never placed AT ALL and sat outside the board outline.
Anchoring a group on a part you then lock destroys the group; anchor on a movable neighbour, or place
the satellite explicitly and lock it too. (b) **Parts in NO group become free singletons**: the four
switch-node RC snubbers (C3x3/R3x4) ended 22-50 mm from their own switch node - a snubber that far
away is a resonant tank, not a damper. (c) **Locking a part voids `placement.separation` rules that
name it**, reported only as `separation_unknown_refs`: locking the inductors let the anneal park the
I2C EEPROM 3.31 mm from L341 against a declared 8 mm. Corollary for any P6: after locking anything,
re-read the seed/anneal report for dropped clusters and unknown-ref warnings - the gate will not tell
you, because a part that was never placed cannot violate a placement rule.

## 2026-08-07 [drc][kicad][fab] A voltage-class DRU rule only protects the nets you NAMED, and the ICD's own part-size policy can violate its own creepage rule
Two coupled findings from lumina-par P6. (a) `rules_gen` writes an HV clearance rule per net listed in
`constraints.voltages`. lumina-par declared only `+48V_SW`, but `/power/V48_B` - the branch-B hot-swap
output (Q101 drain -> bulk) - carries the same 57 V and was never declared, so it inherited the
0.1016 mm global floor: SIX TIMES tighter than the ICD's board-wide 0.635 mm for "every 48 V net".
Its parts are DNP, and **a DNP part still has pads**, so P7 would have routed it at the floor and the
clearance would have been wrong in copper forever. Declaring it and adding the rule immediately
produced a real violation - which is the proof the gap was not theoretical. Sweep EVERY net that can
reach a rail voltage, not just the one the architecture named. (b) The violation it found was C108's
OWN two pads at 0.590 mm on a single 0805 land. The ICD mandates 0805 for the 48 V domain on
VOLTAGE-RATING grounds and separately mandates 0.635 mm creepage board-wide - and never checked that
an 0805 land's intrinsic pad gap (0.59 mm) satisfies it. It does not, and it is under IPC-2221B B2's
0.60 mm floor too. A footprint's internal geometry is not reachable by placement or routing: if a
creepage rule is tighter than a land pattern's own pad gap, the PART choice is the defect.

## 2026-08-07 [placement][kicad] An annealer has no model of a switching loop - build the tile by hand and pin it
lumina-par P6: both anneal candidates converged identically (hpwl -34.8%, crossings 436->159), so
candidate SELECTION carried no information - the win came from overriding the cost function. Its
objective is wirelength/crossings/congestion and it has no term for a buck converter's commutation
loops, so it spread each TPS92515HV channel over a 13.5 x 6.5 mm switch-node bbox. Recipe that worked:
hand-build ONE channel tile (TI LOOP1/LOOP2, Kelvin RSENSE return, BOOT loop, COFF at the pin),
instantiate it identically on all four channels, then RE-RUN seed+anneal with the channels pinned so
the optimizer legalises everything else around them. Result per channel: SW-node bbox 88 -> 17 mm2,
catch diode 8.6 -> 3.7 mm, L-to-D 9.8 -> 3.7, BOOT 6.3 -> 2.4. **The cost is reproducibility**: the
tile is hand-built, so re-running place_seed or place_anneal silently overwrites every bit of it.
Record that loudly in the workspace - it is a trap for whoever resumes at P6.

## 2026-08-07 [planes_gen][constraints] planes_gen will NOT resize an existing zone - it reports the REQUESTED region and zones_added:0, and the board never changes
lumina-par P7. The board was short of routing layers (In1 GND plane + In2 +12V plane + a B.Cu largely
consumed by two reverse-mounted THT sockets = effectively one free signal layer), so the In2 +12V
pour was shrunk in `constraints.json` from x=88 to x=76 board-local - sound, because +12V's last LOAD
is at x=70.1 and ~18 mm of pour sat under the congested cluster carrying nothing. `planes_gen` then
returned `status: pass` with the NEW region echoed back, a plausible smaller `area_mm2`, and - the
part that matters - **`"status": "existing"` and `"zones_added": 0`**. It had not touched the zone
polygon. The board stayed byte-identical; the "freed" copper never existed. Two lessons. (1) The
echoed `region`/`area_mm2` in planes_gen's report describe what was REQUESTED, not what is in the
file - verify a pour change by reading the zone polygon out of the .kicad_pcb (`(zone ... (xy ...))`
bounds), not from the tool's own report. (2) A downstream agent hashing the board against the
snapshot it was told about is what caught this; brief subagents with the snapshot label so they CAN
check your premises, and treat "your premise is false" as a successful outcome rather than a
deviation.

## 2026-08-07 [route_edit][routing] Pre-flight the op list against a geometry checker before writing copper on a dense board
The lumina-par P7 last mile closed 8 connections in a single atomic `route_edit` (29 ops) on a board
with 1564 tracks already down. What made it work first time: building a shapely clearance checker and
iterating the proposed op list against existing copper until clean BEFORE invoking route_edit. It
caught 5 collisions hand-geometry had missed - including one net boxed in on all four layers at once
by three PARALLEL 45-degree escape diagonals stacked across In2 and B.Cu, which is invisible if you
reason layer-by-layer. Two general points: fan-out diagonals from a dense IC form walls that block
later nets on layers you are not looking at, so check all layers as one 3D problem; and where two new
connections must cross, assign them different layers in the SAME op list rather than routing one and
discovering the other. route_edit is atomic, so a pre-flighted list either lands whole or not at all.

## 2026-08-08 [gate][waivers] The verify gate's DEFAULT waiver path resolves next to the BOARD, not in the workspace reports/ dir
Small but costly: gate.py documents the default as `<input dir>/reports/verify-waivers.json`, and the
gate's input is the .kicad_pcb - which lives in `<workspace>/kicad/`. So the default it actually looks
for is `<workspace>/kicad/reports/verify-waivers.json`, NOT the `<workspace>/reports/` directory where
every other P8 artifact (verify.json, renders, review reports) is written. A waiver file placed in the
obvious workspace `reports/` folder is silently ignored - the gate simply reports the same failures
with `waived: 0`, which reads exactly like a waiver that failed to MATCH rather than one that was
never LOADED. Pass `--waivers <path>` explicitly and check the `waived` count in the result, which is
the only positive confirmation that the sidecar was read at all.

## 2026-08-08 [check_silk][drc][geometry] check_silk treats an UNFILLED fp_circle as a filled disc - three false silk-over-pad errors on stock KiCad test points
lumina-par P8. `check_silk` failed the verify gate with `silk circle on F.SilkS covers pad TPxxx.1
(1.77 mm2)` on TP101/TP102/TP103, all stock `TestPoint:TestPoint_Pad_D1.5mm`. The geometry refutes
it: that footprint's silk `fp_circle` is radius **0.950 mm with `(fill no)`** and 0.12 mm stroke, so
the printed ring spans radius 0.890-1.010 mm while the pad is radius 0.750 mm - a 0.140 mm gap, no
intersection at all. The giveaway is the reported area: 1.77 mm2 is exactly the WHOLE pad
(pi x 0.75^2 = 1.767), which is what you get by intersecting the pad with a FILLED disc of radius
0.95. kicad-cli DRC - which the skill already says to trust over check_silk - reports zero silk
violations on the same board, and the 2.0x2.0 mm TestPoint variant (silk drawn as four fp_LINES
outside the pad) is not flagged; both consistent with the fill bug. Two lessons: (1) the earlier
entry "check_silk is far more LENIENT than DRC" is not the whole story - it is also STRICTER in this
specific way, so the two disagree in BOTH directions and neither is a superset; (2) the tempting fix
was swapping the footprint, which on a fully-routed board means a schematic rebuild, a board_update
and re-routing three stubs - measuring the actual silk/pad radii first cost one grep and avoided all
of it. Fix belongs in check_silk: honour `(fill no)` and intersect the ANNULUS.

## 2026-08-08 [git][learnings] An uncommitted LEARNINGS append can be silently reverted by the scoped gate commits - commit it in the same turn
Lost an entry this session and only caught it by noticing `wc -l` went DOWN after an append. The
`gate.py --commit` path runs git operations scoped to `boards/<name>/` and correctly reports
LEARNINGS.md under `excluded_dirty` - but the file still ends up reverted to its committed state
(a CRLF-normalising checkout is the likely mechanism: git warns "CRLF will be replaced by LF the next
time Git touches it" on every one of these commits). Net effect: append an entry, run a gate commit,
and the entry is gone with no error anywhere. Practical rule: `git add LEARNINGS.md && git commit` in
the SAME turn as the append, never "I'll commit the learnings at the end", and if a line count moves
the wrong way after an append, check `grep -c` for the entry rather than trusting the number.

## 2026-08-08 [parts][sourcing] LCSC/JLC catalog "Output Type" attributes can contradict the manufacturer's own ordering table - cross-check before committing to a "Fixed" SKU
buck-5v3a P1 regulator scout, live parts_search. The JLC catalog lists AP63356DV-7 with Output Type
"Fixed", but the Diodes AP6335x datasheet ordering-information table shows the entire family as
ADJUSTABLE-only (external FB divider required). Designing to the catalog attribute would have shipped
a board with FB tied straight to the output and no divider - a silent 0.8 V output, caught only at
bring-up. The catalog attribute is a scraped/derived field, not vendor data. Rule: any part attribute
that changes the SCHEMATIC (fixed vs adjustable, internal vs external compensation, pin count
variants within a family) must be confirmed against the datasheet ordering table by the
datasheet-extractor at P3, never accepted from search results alone. Note the same trap already bit
the house buck reference from the other direction (topologies/buck.md s4: do NOT copy the ADJUSTABLE
variant's divider onto a genuinely FIXED part) - the failure is symmetric, the fix is the same.

## 2026-08-08 [parts][parts_search] A bare category word ("polymer") returns zero rows - pair it with a value
buck-5v3a P1 powerpath scout, live JLCPCB queries. `parts_search.py --query "polymer"` returns an
empty set; `"100uF polymer"` returns the expected rows. Same family as the documented `10K`-token
quirk: the upstream search wants a value token to anchor on and treats a lone category word as an
unmatched keyword rather than a filter. Scouts that conclude "no polymer caps in stock" off a bare
category query are wrong. Always query category+value, and treat a zero-row result on a common part
class as a query defect until a second phrasing confirms it.

## 2026-08-08 [research][tools] WebFetch fails on vendor PDFs ("corrupted binary") where the Read tool renders them fine
Same scout, pulling WJ500V-5.08-2P and CENKER inductor drawings. WebFetch reports the PDF as corrupted
binary; Read on the same URL-downloaded file renders the pages as images and the mechanical drawing is
legible. This matters because the parametric fields that scouts rely on are sometimes WRONG in a
direction that hides a violation: LCSC listed the screw terminal's IEC current rating (24 A) in the
current field, not the conservative UL rating (10 A) that the requirement floor is actually written
against, and only the vendor drawing carried the 14.1 mm body height that nearly breaks a 15 mm
height cap. Rule: when a parametric value is load-bearing for a REQUIREMENT (current rating, body
height, Isat, DCR), pull the vendor drawing with Read - do not trust the catalog field alone.

## 2026-08-08 [order_submit][stackup] `derive_copper_oz`'s `_(\d+)oz` regex outranks the stackup-name lookup - a `4layer_2oz` rule-class token in the `Chosen` window decides the quoted copper weight
Follow-up to the 2026-07-30 entry (which fixed the *heading* match). The resolution order inside the
window is: **(1) `_OZ_ID_RE = _(\d+(?:\.\d+)?)\s*oz\b` anywhere in the text, (2) a stackup id known to
stackups.yaml, (3) refuse.** Step 1 does not care whether the underscore-oz token belongs to a stackup
id. Machine-measured on buck-5v3a's first `architecture/stackup.md`: the window contained the
DFM rule-class name `4layer_2oz` and the return was
`(2.0, 'stackup.md: ## Chosen stackup (_2oz)')` - the right answer for the wrong reason. Write
`4layer_1oz` in that window on a 2 oz board (easy: a "rejected the 1 oz class" sentence) and it
silently quotes 1 oz copper, which is the exact board-killer `_check_oz_mentions` exists to stop.
It also outranks a correct stackup id sitting in the same window.
**Rule for the architect: the `Chosen` window (heading to the next `#` line, max 20 lines) contains
the chosen stackup id and NOTHING else that matches `_<digits>oz` - no rule-class names, no fallback
stackup id, no "rejected 1 oz" prose.** Push all of that below a sub-heading; the window break on any
`#`-prefixed line makes that free. Verify with
`order_submit.derive_copper_oz(Path(workspace))` and require the source note to read
`stackups.yaml[<name>].stack[0].copper_oz` - if it reads `stackup.md: ... (_Noz)`, the regex won and
the answer is a coincidence.

## 2026-08-09 [spice] One-port S11 inside a plain `.ac`: build Gamma as a node voltage, don't post-process
`.ac` is a linear small-signal solve, so nothing nonlinear (a B-source, abs(), log()) can compute
`|(Zin-z0)/(Zin+z0)|` at run time, and `.measure` cannot do complex arithmetic. Make the NETWORK
carry Gamma instead: drive the DUT from an ideal `2 V AC` source through exactly z0 (so the incident
wave is 1 V and `v(dut) = 1 + Gamma`), hold a second node at `1 V AC`, and subtract with a UNITY VCVS
(`Eg g 0 dut ref 1`) - a linear element, exact in `.ac`. Then `vdb(g) = -RL` in dB and `vm(g) = |Gamma|`.
For the impedance itself, `Iz 0 z AC 1` makes `v(z)` numerically equal Zin in ohms, and `vr(z)`/`vi(z)`
read its real/imaginary parts in `.meas` - which is how you prove a tuner is truly nulled (Im -> 1e-6)
while the reflection that remains is entirely real. Machine-verified on rf-term-150w against a closed
form: 72.583 vs 72.5845 dB. Four more engine facts confirmed on ngspice 46 / KiCad 10.0.3 the same run:
(1) `.meas ac <n> param='-<prior_measure>'` works and is the ONLY clean way to get a POSITIVE return
loss - bounds sidecars reading `min: 26` instead of `max: -26` are worth the extra line; (2) `.func`
with nested calls and `min()`/`max()` evaluates fine, including inside an X-line parameter expression
(`X1 d oneport cval={ctset(50,6.9n)}`) - but route `.func` results through a `.param` before using them
in a `.meas param=` string, which is the form actually verified; (3) `.meas ac <n> max vdb(g)
from=.. to=..` gives the worst in-band RL in one line (max of a negative dB = closest to 0);
(4) ngspice has NO `.step`, so sweep component values by REPLICATING the DUT as N `.subckt` instances
sharing one source - 47 instances and 182 measures still solve in 0.63 s, and each corner gets its
own measure name, which is exactly what a bounds sidecar needs (one `.control` loop would collide
every name).

## 2026-08-09 [librarian][parts][windows] A backgrounded lib_pull that gets silently killed and then re-launched creates a second, RACING lib_pull writing the same shared library
sbuck-5v3a P3. Ran `lib_pull.py --parts parts.json` with `run_in_background: true`; the harness
reported it running, but the process was actually killed the moment the turn ended (progress froze
at 8/24 parts on disk with no error surfaced - looked identical to "still running, just slow").
Re-launching the SAME batch command in the foreground (per the coordinator's correction) did not
detect or wait for the first process - `tasklist` showed **two live python.exe processes with the
byte-identical command line**, both appending to the same `aiee.kicad_sym`/`aiee.pretty`
concurrently. No corruption resulted this time (checked post-hoc: `symbol_dedup.removed: 0`, zero
duplicate symbol names/LCSC ids) - lib_pull's per-part symbol-index write happens to be atomic
enough at this pull rate - but it is a real race the pipeline does nothing to prevent, on top of the
already-documented "three concurrent runs would collide" risk (2026-07-28 entries). Practical rule:
after any backgrounded lib_pull is resumed/re-run, `tasklist //FI "IMAGENAME eq python.exe"` (or
equivalent) before trusting the output - if more than the expected count is running, `wmic process
... get ProcessId,CommandLine` to identify duplicates and kill the stale one BEFORE the second
finishes, then re-verify symbol/footprint counts from the filesystem (never from either process's
own exit code). Separately: do not rely on `run_in_background: true` (or a bash call that
auto-backgrounds past its timeout) to survive a turn boundary in this harness - budget a single
foreground call up to the 600 s tool cap, and pull any remainder individually via `--lcsc`.

## 2026-08-09 [parts][footprint][thermal] A DFN package name's number counts TERMINALS, not lands - "V-DFN3020-13" has 9 copper lands and no exposed pad
buck-5v3a P3, AP63356QZV-7 (C3194571). The datasheet names 9 electrical pins but calls the package
V-DFN3020-13, and the pulled EasyEDA footprint has 9 pads. That mismatch looks exactly like the
well-known "EasyEDA dropped the thermal pad" defect, and the theta_JA of 25 C/W on a 2x3 mm body
looks impossible without a belly pad - so the natural inference (and the orchestrator's) was that
four pads were missing. WRONG, and expensively so if acted on. The vendor's Suggested Pad Layout
(datasheet p.27, and Diodes' standalone package-outline sheet V-DFN3020-13-SWP-Type-A1.pdf) annotates
its own multiplicities `X1(2x) + X2 + X(6x)` = 9 lands, and the dimension chain closes exactly with no
room for a tenth. The 13 counts package TERMINALS: VIN is terminals 1-3 merged into one continuous
land, GND is 10-12 merged into another, SW is 13, signals are 4-9.
**Two decisive tells, both cheap to check before hypothesising:** (1) a part with a belly pad quotes
`theta_JC(bottom)`; this one quotes plain `theta_JC` - the evidence a thermal pad WOULD produce was
absent. (2) The datasheet's own layout section named the heat exits in words ("vias around the GND pin
and the VIN pin") - no mention of a pad. Also: a low theta_JA is NOT proof of a belly pad; Note 6 tied
25 C/W to this exact 9-land pattern.

## 2026-08-09 [footprint][kicad][drc] `(layer "User.Drawings")` on a footprint item parses fine but makes kicad-cli refuse to load the board
rf-term-150w R1 (`R_LapPad_T50R0-250-12X.kicad_mod`, from a prior librarian pass) had 16 items on
`(layer "User.Drawings")` - the GUI display alias from the layers-table 4th field
(`(17 "Dwgs.User" user "User.Drawings")`), not the canonical token. fp_verify.py never catches this
(pad geometry only). Symptom: the bundled-python scratch-board trick (2026-07-28) silently reassigns
those items to a sentinel "Rescue" layer on save (no warning), then `kicad-cli pcb drc` exits 3
"Failed to load board" with no JSON at all - total verification failure, not a wrong result. Ruled out
fp_scratch.py's `CreateEmptyBoard()` layer set as the cause: explicitly calling
`board.SetEnabledLayers(pcbnew.LSET.AllLayersMask())` before adding footprints did not help, still 16
Rescue reassignments - the sole cause is the wrong token in the source file. Fix: use `Dwgs.User` (and
likewise `Cmts.User`/`Eco1.User`/`Eco2.User`, never their `User.Comments`/`User.Eco1`/`User.Eco2`
display names) in every item-level `(layer ...)`.
Consequence to watch for: any thermal analysis run before the land pattern is confirmed may be
modelling a heat path that does not exist. Here check_thermal had been run with an EP + 3x3 via array
and produced Tj 95 C, which invalidated the 4-layer justification and had to be recomputed against
two ~1.16 mm2 lands. Confirm the land pattern from the vendor drawing BEFORE the thermal model, not
after. And `fp_verify`'s pad-count check compares against the extraction, so a package-name-derived
pad_count silently turns into a false-positive ERROR - fix the extraction, not the footprint.

## 2026-08-09 [easyeda2kicad][fab][parts] Courtyard-redraw excess convention, and fp_verify's pad_size check cannot clear a multi-land-class part
buck-5v3a P3 repair pass, U1 (AP63356QZV-7). Two reusable facts from fixing the land-ruling defects:
(1) When redrawing an easyeda2kicad courtyard by hand (the pulled one is always the body outline, not
the pad bbox - 2026-07-28 entry above), this repo's own convention for the excess margin is **0.25 mm
beyond the pad-field bounding box**, taken from the same 2026-07-28 entry's KiCad-stock comparison
("stock footprint's courtyard CONTAINS its pads with 0.25 mm to spare") - there is no separate
constant for it in fpfix.py/fp_verify.py (fpfix does silk/peg/text repairs only, never courtyard); 0.25
mm is a convention inferred from that one measured data point, not a hard-coded rule anywhere, so cite
the LEARNINGS entry rather than a source file when asked "where does 0.25 mm come from". (2) A part
with pads of more than one physical size (U1: 6 signal lands 0.30x0.60, 2 large lands 0.75x1.50, 1
centre land 0.30x1.73) can NEVER pass `fp_verify`'s `pad_size` check clean: the check (fp_verify.py
~line 109) only stores ONE expected `[w,h]` pair in `land_pattern.pad_size_mm` and bounds the MIN and
MAX of the footprint's distinct pad sizes against that single pair - so the large/centre lands will
always warn even when they exactly match the vendor drawing. This is a schema limitation, not a
footprint defect; do not try to "fix" it by editing pad geometry, and do not treat the warning as
newly-introduced when re-verifying a footprint you just repaired for something else.

## 2026-08-09 [check_thermal][thermal][stackup] `check_thermal` is an area/layer-count screen: it scores identically with ZERO thermal vias and NO top pour, and its 2-layer branch is a 1 oz calibration
buck-5v3a P2 thermal re-check, after the U1 exposed-pad refutation (entry above). Two facts that
change how much weight the gate can carry, both machine-verified on a synthetic probe:
(1) **The gate cannot see the heat path.** `check_thermal.check_part` computes `a_eff` = heatsink-net
copper within a 14.3 mm radius summed over ALL layers and capped at `A_SAT_MM2` = 645, then
`theta_ja(a_eff, multilayer)`. On any 4-layer board with GND planes the cap saturates, so the gate
reports **exactly 51.106 C/W** regardless of layout - and a probe stripped of every thermal via AND
the entire top GND pour returned the identical `rise_c` and still passed. Its `need_vias` warning
only fires when `dt/power < theta_ja(A_SAT)` (51.1 on 4L), so a comfortable `dt_c` also disables the
only via check. Corollary: a via/pour prescription written into `constraints.json` `min_vias` is
DOCUMENTARY - nothing enforces it but P6/P7 review. Say so in the constraint's `_basis` text.
(2) **`MODEL_2L` is documented as "1 oz / 2-layer"** (module docstring) and `MODEL_ML` as "2 oz /
4-layer", and the only stackup input is `len(bg.copper_layers) >= 4`. On a board with 2 oz OUTERS the
model therefore overstates the 4L-vs-2L gap badly: it claims 51.1 vs 73.8 C/W (~20 C of T_j), but a
first-principles radial spreader model puts it at ~3 C, because 0.5 oz inner planes add only 0.030 mm
of copper against 0.140 mm on 2 oz outers (18 % more lateral sheet conductance), and a 50 x 40 board
is already near-isothermal at two layers. Do not justify a layer count on `check_thermal`'s delta
alone - and if the real reason for 4 layers is the return plane, write that down instead.
Also worth keeping: the same first-principles model reproduces a vendor's JEDEC-board theta_JA within
5 % (25.3 vs 25 C/W for AP63356Q on a 2s2p coupon), which is what licenses using it on the real
board; and a small board's `theta_JA` is dominated by BOARD AREA, not inner copper weight (swapping
1 oz -> 0.5 oz inners on the JEDEC board moved it ~2 C/W, while shrinking 8710 -> 2000 mm^2 moved it
~11 C/W). Keep the probe generator next to the board (`research/raw/`) - the P2 probe was thrown away
and had to be rebuilt from scratch to re-check one number.

## 2026-08-09 [kicad][schematic][erc] A LITERAL `/` in a local label becomes `{slash}` - the root prefix is added by KiCad, never typed
sbuck-5v3a P4, flat root sheet. `architecture/sheets.md` spells the canonical nets `/VIN`, `/SW`,
`/FB`... and `constraints.json` matches those exact strings, so the obvious move is to pass that
string straight to `Sheet.wire_pin(ref, pad, "/VIN")`. KiCad 10.0.3 then **escapes the slash**: the
exported net is `/{slash}VIN`, not `/VIN`. Nothing warns - the generator exits 0, the schematic
opens, `kc.py erc` is 0/0, and the only symptom is `netlist_audit --constraints` raising
`missing_net` for every affected net (here `/VIN` and `/SW`, i.e. the two most current-carrying nets
on the board, each declared in TWO constraint sections). The `{slash}` token appears only if you dump
the netlist's net names; the schematic still renders the label as "/VIN". Rule: **label text is
always BARE** (`VIN`, `SW`, `COMPZ`); the leading `/` in the exported name is the ROOT SHEET PATH
that KiCad prepends, which is exactly what `schlib`'s docstring means by "sheet-local labels become
`/NAME` (root)". Same trap in reverse for power symbols: those Values ARE the literal net name and
must stay bare (`+VIN`, not `/+VIN`). Verify by dumping the exported names
(`grep -oE '\(name "[^"]*"' board.net | sort -u`), not by reading the schematic.

## 2026-08-09 [schematic][kicad-sch-api][schem_refdes] A rot-90 symbol's Reference and Value overprint each other: KiCad rotates field TEXT with the symbol, schem_refdes does not
rf-term-150w P4, C1 (a 2-pin trimmer drawn as a vertical shunt at `rotation=90`). The saved file
looked correct - `(property "Reference" ... (at 102.36 74.93 0))` and `(property "Value" ... (at
102.36 77.47 0))`, two distinct points 2.54 mm apart, field angle 0, and `schem_refdes` reported
`residue: []`. The PLOT showed `1-3(C1)0pF`: KiCad ADDS the symbol's rotation to the field's own
angle, so both strings render VERTICALLY, and two vertical strings whose only separation is 2.54 mm
of Y overlap almost completely (each is ~8 mm long in Y). schem_refdes separates the pair along Y
because it models the text as horizontal. Cosmetic only - ERC 0/0 and the netlist is identical - so
no gate sees it. Cheapest fix on a 2-pin passive is to draw it HORIZONTALLY (rot 0/180) and let the
GND power symbol carry the "this is a shunt" meaning; the general fix would be for schem_refdes to
add the symbol rotation to the field angle before choosing an axis. Related: a narrow custom symbol
that SHOWS long pin names (`aiee:5602`, names "STATOR"/"ROTOR" on a 1.02 mm-wide body) prints them
on top of each other and of the fields at any rotation - `(pin_names (offset 1.016) (hide yes))` on
the library symbol is the fix, and `expect={...}` pin-name insurance still works because the name is
only hidden, not removed.

## 2026-08-09 [schematic][kicad-sch-api] Sheet text is CENTRED on its `at` point and `add_text` has no justify parameter - long note blocks run off the page
Same board. `sch.add_text(line, position=(25.4, y))` for a 90-character assembly note put the middle
of the line at x = 25.4, so half of every line rendered off the left edge of the A4 sheet - visible
only on the plot, never in the file. `kicad_sch_api` 0.5.6 exposes bold/italic/size/color/face on
`add_text` but NOT justification (`labels.py` takes `justify_h`, `texts.py` does not), so the only
route is a post-save pass that inserts `(justify left)` into each top-level `(text ...)`'s
`(effects ...)`. Two more measured limits on the same block: the A4 title block starts at about
y = 185 mm, so a note block must end above it; and a title longer than ~45 characters is clipped by
the right edge of the title block.

## 2026-08-09 [placement][geometry][python] placelib's effective courtyard is `declared courtyard UNION the single pad BBOX` - a spread multi-pad part claims the empty space between its pads and can make a small board unplaceable
rf-term-150w P6 (6 footprints, 26 x 20 mm, 2 nets). `Footprint.extents_local()` unions the declared
F.CrtYd with `_pad_box_local()`, and that helper is ONE bbox over ALL pads, not per-pad boxes. Two
consequences measured on this board: (a) `MountingHole:MountingHole_3.2mm_M3_Pad` draws a 6.9 mm
CIRCLE courtyard but its effective extent is a 6.9 mm SQUARE (6.4 pad + 0.25), because the circle
does not contain the pad box - 47.6 mm2 not 37.4; (b) the custom `aiee:R_LapPad_T50R0-250-12X`
(5.0 x 7.0 lap pad + two 3.5 x 2.0 strap lands at +/-5.10 mm) gets a 14.2 x 7.5 mm solid rectangle,
so the ~4 x 5 mm of BARE BOARD either side of the lap pad is claimed even though the part's body is
entirely off-board. Trimming the footprint's F.CrtYd does NOT help - the pad-bbox union re-inflates it.
Result: with R1 pinned to a 26 mm edge, the two side strips beside its extent sum to
26 - 14.2 - 0.1 = 11.7 mm, so only ONE 6.9 mm hole can ever sit beside it, and the four free items
(C1 + 3 holes) provably cannot all be placed - `place_seed` reported "could not legalize cluster
H2/H3" and all 3 `place_anneal` candidates came back 4-5 violations / 48-64 mm2. The arithmetic that
predicts this before you place anything: for a part of extent width W centred on axis Xa on a board
of width B, a square extent of side S fits beside it iff `B >= W + 2*S + 2*edge_inset`. Corollary for
scoping: a mounting hole's PAD diameter, not its drill, sets the board width - dropping M3 pads from
6.4 to <=5.35 mm made this same intent fully legal with no outline change.

## 2026-08-09 [placement][constraints] `placement.fixed` silently DISABLES every separation constraint that names those refs
sbuck-5v3a P6. `placement.fixed` does two things, only one of which is documented: (a) `place_seed`
and `place_anneal` never move those footprints (they become obstacles - `place_seed.py:441`,
`place_anneal.py:302`); (b) because separation pairs are resolved through `ref2cid` (cluster ids) and
fixed refs are excluded from the cluster list, EVERY `placement.separation` entry that references a
fixed ref is dropped. Here `fixed = [H1-H4, J1, J2, U1, L1]` turned into
`separation_unknown_refs = ['C4','F1','J2','L1','U1']` - i.e. all four separations (C4>=12 mm from
U1/L1, F1>=10, R6/R7>=5 from L1, J2>=14 from U1/L1) had ZERO cost weight. The refs are surfaced in
`facts.separation_unknown_refs` (S14 fix) but the run still "succeeds". Separation is also a SOFT
squared-distance cost on cluster CENTRES, never a legality violation, so `gate place` cannot catch it
either. If a separation matters, the P6 agent must measure it by hand after placing.

## 2026-08-09 [placement][kicad] Schematic-sourced mounting holes trip their OWN keepout rects - lock them
With `board_init --mounting-holes 0` the M3 holes arrive as ordinary schematic footprints, so
`f.is_movable` is True and `legality_violations` tests them against `placement.keepouts` - each hole
intersects the very rect that exists to protect it (4 x 37.33 mm2 "keepout_violation"). Both the
keepout test and the outside-outline test are gated on `is_movable`, so a single
`{"op":"lock","ref":"H1","locked":true}` clears it; no geometry change is needed. Real `board_only`
holes (the `--mounting-holes N` path) are exempt by construction.

## 2026-08-09 [constraints][placement] The board-local -> absolute keepout translation is a real, silently-skipped step
`constraints.json` for sbuck-5v3a warns in its own `_comment` that `placement.keepouts` rects are
BOARD-LOCAL and must be translated by `reports/board_init.json.outline_bbox` before P6. P5 did not do
it. The failure mode is exactly as advertised - silent: three of the four untranslated corner rects
landed entirely off-board (no effect at all), and the fourth, `[43,33,50,40]`, landed at board-local
`[20.3,-4.2,27.3,2.8]` and produced ONE phantom violation against an unrelated part (C16) in the top
edge band. Sanity check before trusting any keepout: compare `rect` against
`board_init.json.outline_bbox` - if the numbers start near 0 while the outline starts at 22.72/37.225,
nothing has been translated.

## 2026-08-09 [placement][kicad][render] DB128L-5.08-2P wire entry is at local +Y, same as KF128 (270 = out the LEFT edge, 90 = out the RIGHT)
Second footprint family confirming the 2026-07-28 KF128 finding, and again the constraint file was
wrong: `placement.edges` declared `J1 rot 0` / `J2 rot 180`, which points both wire entries INTO the
board. `render.py <board> --views left,right` settled it in one shot - the mouth shows as two dark
cavities with the metal clamp visible, face-on, in exactly one view. The WRL alone was NOT decisive
here: the mouth-end plastic face is the SHORTER one (z to ~7 mm vs ~10 mm at the closed end), which is
the opposite of the "tall wall = wire entry" intuition, and the below-board pin cluster only fixes the
x/y mapping, not which Y face is open. Use the side render; treat the WRL as corroboration.

## 2026-08-09 [placement][thermal] SO-8EP (AP64350) cannot hold a 4x4 in-pad thermal via array - 12 is the geometric maximum
`constraints.thermal[U1].min_vias = 16` and power.md's "target 16" are both unachievable: the vendor
EP is 3.502 x 2.613 mm, and 4 rows of 0.55 mm lands at the 1.0 mm pitch need 3.55 mm, more than the
2.613 mm axis. With JLC's `min_hole_to_hole 0.5` and a 0.3 mm drill the pitch floor is 0.8 mm, so the
best in-pad array is 3 x 4 = 12 vias at 0.9-0.95 mm pitch. power.md's own "3.4 mm fits 4x4" assumed a
SQUARE pad. Thermal cost of 12 vs 16: R_via = 25.3/(0.85*N) = 2.48 vs 1.90 K/W, i.e. +0.5 C of Tj at
1.0 W - inside margin (the same note prices 9 vias at +1.3 C). Make the target "12 in-pad + >=8
stitching vias in the surrounding F.Cu island", and never write a `min_vias` that the land pattern
cannot physically hold - `check_thermal` will not catch it (its via warning needs dt_c/power_w < 51.1).

## 2026-08-09 [routing][rules_gen][placement] A per-net `track_width` floor makes every SMALL-PART STUB on that net illegal, not just the trunk
sbuck-5v3a's `.kicad_dru` carries `aiee_pwr_width_SW (min 2.31mm)` for `/SW`. `/SW` does not only
reach the inductor - it also reaches the bootstrap cap (0603, 0.8 x 0.9 mm pads), the DNP snubber
(1206) and TP2. A 2.31 mm track cannot sensibly land on a 0.9 mm pad, and each such stub also spends
the design's `<= 40 mm2` SW-area budget at 2.31 mm width. Same class as the 2026-07-28 pd-trigger VBUS
finding, and the same fix: pour `/SW` as a ZONE (a zone is not a track, so the width rule does not
apply and KiCad necks around foreign pads by itself). P6 consequence: place the BST cap / snubber /
SW test point so their pads sit 0.7-1.2 mm from the main pour, otherwise the stubs alone blow the area
ceiling.

## 2026-08-09 [placement][drc][silk] Off-board-part footprint SILK (registration marks/labels) is a hard placement keepout for mounting holes
rf-term-150w P6. `R_LapPad_T50R0-250-12X` carries two `BOLT CL` labels 9.21 mm off its own axis, on
F.SilkS, so a builder can align a heatsink drilling template. They land 2.5 mm inboard of R1's origin,
i.e. in the exact band where the two flanking M3 holes want to sit. Nothing in `placelib` sees them
(legality is courtyard/pad-bbox only), and `check_silk` is lenient - but `drc_routed` fails on
warnings, so a `silk_over_copper` there is a P7 blocker. Practical numbers for a 5.0 mm pad: the hole
copper must clear the GLYPH extent, which is `+/-(size/2 + thickness/2)` = +/-0.475 mm at size 0.8 -
NOT the `+/-(1.6*size+t)/2` = +/-0.715 mm GetBoundingBox height. Designing to the bbox costs 0.24 mm
of board per side. Confirmed live: `min_silk_clearance` is 0.0 in the generated `.kicad_pro`, so DRC
fires only on real glyph overlap. Consequence here: the flanking holes had to sit 3.9 mm north of the
strap lands they flank.

## 2026-08-09 [placement][anneal] place_anneal cannot rotate a `placement.groups` satellite - a big satellite makes the board unsolvable by SA
rf-term-150w declares `{"name":"port","anchor":"J1","members":["C1"]}`. C1 is a 7.5 mm trimmer whose
courtyard is 11.1 x 8.1 mm; J1's courtyard is 8.3 x 16.2 mm. The satellite rides its anchor as a rigid
unit at whatever rotation the seed gave it, so on a 26 x 20 mm board neither seed nor anneal ever finds
a legal placement: `place_seed` exits 1 with `courtyard overlap J1/R1` + `C1 extends 77.6 mm2 outside
the outline`, and `place_anneal` returns 2 candidates, both `legal: false`, after 33 720 moves (it only
had 3 movable clusters - the two edge-pinned parts and the satellite are all frozen). This is NOT the
"escalate, board too small" case: hand placement solved it at HPWL 54.4 mm vs seed 68.5 / anneal 67.6.
Read `movable_clusters` in the anneal report first - if it is <= half the footprint count, stage 3 is
the whole job and the SA numbers are noise.

## 2026-08-09 [stitch_vias][drc][fab] stitch_vias' hole-to-hole filter is blind to THT PAD drills - 10 of 89 vias fired the 0.5 mm DRU floor
rf-term-150w P7. `stitch_vias --pitch 1.6 --clearance 0.85` reported `rejected: {hole_to_hole: 35}`, so
its own hole filter clearly runs - yet the board it produced failed DRC with **20 `aiee_hole_to_hole_floor`
errors** (10 unique vias, actual 0.225-0.479 mm against min 0.4995). Every offender was a stitch beside a
same-net THROUGH-HOLE PAD drill (H1/H2/H3 3.2 mm, J1 1.4 mm, C1 6.3 mm), never via-to-via: the generator
measures via drills against via drills and treats a pad as copper only. Extends the 2026-07-28 finding
(KiCad's *built-in* hole_to_hole skips same-net via-vs-THT-pad) with the other half of the picture: the
`aiee_hole_to_hole_floor` DRU rule has no net condition, so **kicad-cli DOES flag it** and the gate fails.
Recipe that worked: after any stitch run on a board with THT pads, recompute
`dist(via, pad_drill_centre) - via_drill/2 - pad_drill/2 >= 0.5` from `geom` (`Pad.drill_poly` bounds),
then `route_edit` a `remove`-only op list for the offenders - they are peripheral fill, so 79 of 89 vias
survived and the return path near the strap lands (0.30 / 0.83 mm) was untouched.

## 2026-08-09 [geom][check_return_path][kicad-cli] `--verify-fill` refills in a TEMP DIR, so a custom .kicad_dru makes it fail on a perfectly fresh board
`geom.BoardGeom.assert_fresh(refill=True)` - reached from `check_return_path --verify-fill` and anything
else that asks for thorough freshness - calls `_refill_copy()`, which `shutil.copy`s the .kicad_pcb into
`tempfile.mkdtemp()` and refills it there. That is the 2026-07-28 "DRC on a board copy OUTSIDE the project
dir silently changes the rules" trap, now inside a library: the sibling `.kicad_pro` / `.kicad_dru` do not
follow, so the "fresh" fill is computed with KiCad defaults. On rf-term-150w (board-wide 0.80 mm
`aiee_hv_122p5v_RF` vs the zone's own 0.5 mm local clearance) the fresh copy filled 356.204 mm2 against a
correct committed 340.448 mm2 - a 15.8 mm2 delta that is exactly 0.3 mm x ~53 mm of /RF perimeter - and
raised `StaleFillError` on a board that `kicad-cli pcb drc --refill-zones --save-board` had just written.
So: **on any board with a per-net clearance rule, `--verify-fill` is a false failure**; use the fast
`assert_fresh()` (what `gate.py --gate drc_routed` already uses) and prove freshness with an in-place
refill instead. Real fix would be to copy the project sidecars alongside, or refill in the board's own dir.

## 2026-08-09 [drc][kicad][silk][routing] KiCad's silk clearance test does NOT see TRACKS - silk is a placement keepout, never a routing one
Measured on this host (KiCad 10.0.3) before committing to a wide RF flare on rf-term-150w, whose P6 pass
had already spent real board area dodging silk. A 1.28 mm F.Cu track was driven straight through the
centre of J1's `Reference` text (size 1.0, thickness 0.15) and DRC reported **zero** silk findings - only
the expected `track_dangling`. Pads and vias are tested (that is what the P6 `silk_over_copper` fight was),
zones and tracks are not. Consequence, and it is worth money: at P7 you may route copper of any width
under any silkscreen, so the only keepouts for a flared RF conductor are copper clearance, edge clearance
and hole-to-hole. Do not narrow a trace to dodge a refdes. Caveat on how this was measured - the scratch
board was named `_silktest.kicad_pcb` in the project dir, so it loaded KiCad DEFAULT board settings
(edge clearance came back 0.5 mm, not the project's 0.3 mm); silk clearance defaults to 0.0 either way and
the project sets no silk rule, so the conclusion holds, but re-confirm on the real board (done: final
`drc_routed` is 0 errors / 0 warnings with 34 flare segments crossing three silk texts).

## 2026-08-09 [planes_gen][constraints] `planes_gen` rejects `_note` - the P2 constraints convention makes `constraints.planes[]` unusable as its own input
sbuck-5v3a P7 step 0. `planes_gen.py --pcb <board>` (constraints default = the sidecar beside the
board) died instantly with `CheckError: planes[0]: unknown keys ['_note']`. `_PLANE_KEYS` is a strict
whitelist (`net/layer/region/priority/min_island_mm2/clearance/min_width/connect`), while the P2
convention puts a `_note` on every constraints entry - and on this board `planes[]` carried the single
most load-bearing note in the file (all four layers GND, overriding the 4-layer "In1 GND + In2 dominant
power" default). Every other constraints consumer tolerates `_note`, so nothing upstream warns. Two
consequences: (1) the documented step-0 invocation cannot work on any board that annotates `planes[]`;
(2) the fix is the same planes-only sidecar the remediations already prescribe for pour fan-in
(`planes_gen --constraints <sidecar>`), which is better anyway because it is where the P7-only regions,
priorities and `connect: solid` belong - do not strip the notes out of `constraints.json` to make the
default path work.

## 2026-08-09 [routing][freerouting][check_current] A clean `--pad-window` does NOT predict that Freerouting will honour the netclass width - FR necks to the PAD's own width at every small-part stub
sbuck-5v3a P7. `route_critical --pad-window` returned `ok: true` for all 61 power pads (R6.1 and R8.1,
both 0402 +5V taps, reported 6.966 / 8.000 mm of headroom against the 2.055 mm floor). Freerouting then
routed those same taps at **0.8058 mm - the exact 0402 pad width** - and produced 9 `track_width`
errors: 0.8058 at the two 0402 taps, 1.6348 (the 1210 pad width) on the +5V trunk, 1.4848 (the 0805 pad
width) on +VIN. The two probes measure different things: `--pad-window` maximises `2*(dist(P,foreign) -
CLR)` over centreline points whose ROUND END-CAP can reach the pad from outside, so a wide track that
merely overlaps a tiny pad counts as connectable; FR instead fans in from inside the pad footprint and
takes the pad dimension as its width. It also necks at pinch points the window never sees (here 1.6348
where a 2.055 trunk would have cleared TP4 and C12.2 by exactly 0.2005 mm - legal, but 0.5 um of
margin). Practical rule: on any net with an `aiee_pwr_width_*` floor, budget a pour fan-in for every
small-part stub BEFORE running route_auto, and read `--pad-window` only as "is this floor geometrically
unmeetable" (exit 1), never as "FR will route it at width".

## 2026-08-09 [planes_gen][thermal] `via_grid` is centroid-centred and pitch-stepped, so it can only emit ODD x ODD arrays - 3x4 in-pad thermal vias must be hand-placed
sbuck-5v3a U1 (SO-8EP, exposed pad 2.613 x 3.502 mm) needs 12 in-pad vias; `planes_gen`'s
`via_grid` puts points at `centroid + (i*pitch, j*pitch)` and keeps those whose 0.6 mm land fits inside
`poly.buffer(-(size/2 + margin))`, i.e. an inner rect of 1.813 x 2.702 mm here. Because every offset is
a whole multiple of the pitch about the centre, the count is odd on each axis: 3 x 3 = 9 at 0.9 mm
pitch, and no pitch fixes it (0.67 mm would give 5 rows but breaks JLC's 0.5 mm hole-to-hole floor).
The 3 x 4 = 12 array the pad actually holds needs y offsets of +/-0.45 and +/-1.35 - a half-pitch
stagger the generator cannot express. Fix used: `planes_gen --no-thermal-vias` plus a 12-op
`route_edit add_via` list at x = cx+{-0.9,0,+0.9}, y = cy+{-1.35,-0.45,+0.45,+1.35} (0.6/0.3 mm vias,
0.6 mm hole-to-hole, 0.1 mm land margin inside the pad). Check the parity of the array your thermal
budget assumes before trusting `facts.thermal_vias`.

## 2026-08-09 [spice][sim-analyst] P2 feasibility benches hard-code geometry-derived parasitics that go STALE at P7, and nothing in the pipeline re-derives them
rf-term-150w's four sim benches modelled `cpad=1.3p` of port shunt capacitance, computed at P2 from
an architecture that assumed a 3.55 mm launch. The as-routed launch is 15.065 mm of mostly-wide trace
plus a 5.0 x 7.0 mm lap pad = ~4.7 pF, 3.6x larger. The `sim` gate stayed green through P4-P8 because
every bound was written against the stale model, and the error propagated into the README's headline
tuning range. Two consequences worth generalising: (1) any bench parameter whose value comes from
GEOMETRY (launch C, trace L, thermal R) is a P7 dependency, not a P2 constant - re-run the bench after
routing and diff the numbers, the gate will not tell you; (2) put the load-bearing geometric quantity
in its own `.meas ... param=` line with a tight window (here `ctneed_p47_pf` in [-2.2,-1.4] pF) so a
reverted or unre-derived value trips the gate instead of silently shifting every RL by tens of dB.
Also: when a corrected parasitic makes the ideal tuner setting NEGATIVE (a hypothetical shunt smaller
than the fixed pad term), model the hypothetical as a single capacitor with the pad term collapsed to
`1f` - never pass a negative capacitance to ngspice.

## 2026-08-09 [route][rf][impedance] A pour-flanked narrow trace is a grounded CPW, not a microstrip - blocks.md's L'/C' table over-states L by 21 % and under-states C by 12 % at the RF width floor
rf-term-150w's `blocks.md` s4 solves the launch with IPC-2141A **surface microstrip** (`eps_eff` from
`(1+12h/w)^-0.5`, `Z0 = 87/sqrt(er+1.41)*ln(5.98h/(0.8w+t))`). That model ignores the F.Cu GND pour,
which the `aiee_hv_*` rule holds at only 0.80 mm from the trace - closer than the 1.53 mm core. At
w = 0.94 mm the two models disagree materially: microstrip gives Z0 87.8 ohm, L' 0.519 nH/mm,
C' 0.067 pF/mm; grounded-CPW (Hilberg K(k)/K(k'), s = 0.80, h = 1.53, er 4.5) gives Z0 75.3 ohm,
L' 0.428 nH/mm, C' 0.0755 pF/mm. The error is one-sided and compounding for a tuning-range argument:
the coplanar ground BOTH lowers the inductance you are trying to add AND raises the capacitance that
eats the trimmer's low-end authority. Rule: whenever the pour clearance is comparable to or smaller
than the dielectric height, size RF geometry with CPWG and quote microstrip only as the upper bound.
The gap between the two IS the design's uncertainty band - report both, do not pick one.

## 2026-08-09 [check_return_path][gates] The corridor is `k x the chain's WIDEST track`, so deleting one fat segment silently re-scales every deficit area on that net
`corridor_on()` merges a net's tracks into chains and buffers each chain by `k * max(width in chain)`.
On rf-term-150w the /RF chain contained a 7.34 mm lap-pad flare, so the corridor was buffered by
3 x 7.34 = 22.0 mm - it covered the whole board and 623 mm2 of off-board area, and `corridor_coverage`
read 0.23. Deleting that one segment dropped the buffer to 3 x 2.73 = 8.2 mm, coverage rose to 0.74,
the off-board warnings fell from 623 to ~117 mm2 - and the *unrelated* C1 antipad ERROR grew from 0.74
to 1.46 mm2 because a different fraction of that fixed annulus now falls inside the corridor. Nothing
physical moved. Two consequences: (1) never treat a change in `area_mm2` as evidence of a physical
regression - `crossing_len_mm` is the physical number and it was byte-identical here; (2) waiver
`reason` text that quotes an area is stale the moment any track width on that net changes, so quote
the crossing length instead, or re-verify the areas after every re-route.

## 2026-08-09 [spice][sim-analyst][rf] "Adding inductance is free or better" is a statement about being BELOW the null window, and it inverts the moment you act on it
rf-term-150w's correction benches proved that at L = 7.21 nH with a fixed 4.7 pF port the trimmer
bottomed out and was over-correcting, so more series L IMPROVED the match (+0.72 dB for the first
7 nH at the binding +5% resistance corner). The board was then re-routed on that finding to 19.5 nH.
Re-deriving the same benches shows the sign has flipped: the null is now interior, so every further
nH only buys Reff = R + X^2/R, and 19.5 -> 30 nH now COSTS 0.74 dB at the same corner. The finding
was never "inductance is free"; it was "you are below the window", and the fix consumed exactly the
thing that made it true. Two rules: (1) a bench whose conclusion is a *gradient* (more X is better)
must be re-derived, not re-run, after a change made in its direction - re-running it silently
re-asserts a premise that the change deleted; (2) gate the gradient itself with a signed bound
(`d_more30` max 0.0 here), so a revert that puts the board back below the window fails the gate
instead of quietly restoring the old story. Related: the same re-derivation moved the WORST corner
from "pessimistic parasitic + as-routed L" to "top of the residual band", so before/after deltas are
only meaningful when you say which comparison they are (like-for-like +0.48 dB, worst-to-worst
+1.06 dB on the same re-route).

## 2026-08-09 [place_edit][kicad][silk] A board `gr_text` could not be MOVED by any script - and `board.Remove()` on one poisons `GetDrawings()`
sbuck-5v3a P8 found D2's cathode marker `K` printed at the ANODE end (a board-level `gr_text` from
P6, not footprint silk). Nothing in the pipeline could fix it: `place_edit`'s `add_text` matches an
existing text on **(layer, string, TARGET position +-0.01 mm)**, so it can create or update in place
but can never relocate one, and there was no delete. Relocating a mismarked polarity/pin-1/cathode
legend is exactly the case where the silk is a safety artifact (assembled to this silk, D2 becomes a
forward diode, Vgs sticks at -0.7 V, and 2.6 A runs in Q1's body diode at ~2.1 W in an SO-8), so the
gap was closed rather than worked around: `remove_text {text,x,y,layer}` in place_edit + place_swig,
idempotent (absent -> `{"removed": 0}`), verified by the driver's independent sexpdata parse
asserting ABSENCE. `remove_text` + `add_text` is now the relocation idiom.
The trap that cost the first attempt, measured on KiCad 10.0.3: **use `board.RemoveNative(item)`, not
`board.Remove(item)`.** `Remove()` hands ownership to python; once that proxy is collected,
`board.Drawings()` comes back as a bare SwigPyObject and **every later `GetDrawings()` raises
`TypeError: 'SwigPyObject' object is not iterable`** - so the NEXT op in the same job dies, not the
removal. It reproduces only when the proxy is dropped (holding the list alive hides it), which is why
an interactive probe passed and the worker failed. `RemoveNative` is clean under gc.

## 2026-08-09 [silk][silk_place][check_silk] silk_place's score is attribution-BLIND, so it certifies the exact defect check_silk flags
On sbuck-5v3a, `check_silk` reported 8 `silk_misattributed` refdes (C9 printed on C7, R7 on C12, a
row of R5/C2/R3 each sitting over a different part) while `silk_place --apply` proposed only 3 moves
and declared the rest optimal. Cause: the score is `(min(clearance, 0.30), -distance_to_own)`, so a
spot 3 mm away with 0.30 mm clearance strictly outranks a snug one with 0.29 - closeness is only a
tie-break AT the cap. `check_silk`'s rule is the opposite ("attribution beats closeness"): flagged
iff `own_off > 1.0 mm` AND `nearest_other < min(1.0, own_off)`. Re-scoring the SAME candidate set as
`(tier, min(clearance,0.30), -own_off)` with `tier = 2 if clean and own_off < nearest_other else 1 if
clean else 0` took it 8 -> 3 at DRC 0/0. Three further facts, each of which cost a pass:
- **A target that ends up NOT moving is invisible to every target processed before it.** Targets are
  excluded from the static obstacle set and only enter `placed_boxes` when their turn comes, so an
  earlier label can be placed on top of a later one that never moved (C2's label landed on R4's ->
  `silk_overlap`). Seed the obstacle set with every target's CURRENT box and drop each as decided.
- **A "keep the current position" rule must re-check that the current position is still legal**, or a
  label that a previous pass left overlapping is frozen there forever.
- Move only on a **tier increase**; extra clearance is never a reason to relocate a legible label
  (that rule cut a 28-op churn to 12 with the same attribution outcome).
Residual and the durable fix: 3 labels (C9, R7, C2) have NO attribution-clean candidate at any legal
clearance - at refdes size 1.0 mm the inked label is ~2.6-3.2 mm wide against a 2.5-3.0 mm passive
pitch. Re-running the search at size 0.8 / thickness 0.12 (still above `check_silk`'s MIN_TEXT_H 0.8
and JLC's floor) clears R7 and C2 and leaves only C9. `move_text` has no `size` field, so refdes
resize is the next real gap.

## 2026-08-09 [dfm][gerber][gerbonara][silk] gerblib flattens DRAWN arcs to their chord, so every silk circle around a pad reads as a phantom diameter line
`gerblib.read_gerber` turns each `Line`/`Arc` object into `LineString([(x1,y1),(x2,y2)])` - an
explicit, commented decision ("Arcs are approximated by their chord ... the corpus routes arcs only
as chamfers"). KiCad exports a full `fp_circle` as TWO 180-degree G02 arcs whose endpoints are
`(cx-r, cy)` and `(cx+r, cy)`, so both chords collapse onto the SAME straight segment: the circle's
DIAMETER. On sbuck-5v3a's seven stock `TestPoint:TestPoint_Pad_D1.5mm` instances this produced a
1.9 mm x 0.12 mm bar straight through each pad and 7 x `dfm_silk_over_pad` ERRORS
("silkscreen printed over a solder-mask opening (0.1798 mm2)" = the 1.5 mm aperture x the 0.12 mm
stroke, exactly). The ring is innocent: measured on the real gerber, the F.Mask aperture is
1.7664 mm2 = pi*0.75^2, i.e. EXACTLY the 1.5 mm pad with zero mask expansion, while the ring spans
r 0.89-1.01 and clears it by 0.14 mm. Consequences worth remembering: (a) the S12 fix
`approximate_arcs(max_error=1e-3)` was applied to `_flash_polys` (Flash/Region) ONLY - drawn arcs
still chord; (b) `dfm_silk_over_pad` on a footprint that draws a circle AROUND a pad is a
false positive by construction, and it fires on the same innocent geometry that `check_silk`
already false-positives on for a different reason (it buffers an unfilled `fp_circle` into a filled
disc, check_silk.py:172) - two independent tool defects on one ring, so "two checkers agree" is
NOT corroboration here; (c) ENLARGING the ring cannot fix it - the chord runs through the centre at
any radius - so the only board-side fix is deleting the circle.

## 2026-08-13 [tests][freerouting][skill] A wall-clock-bounded Freerouting assert reads as a routing regression on a contended host - gate it on `timed_out`
`test_route_auto_full_flow` asserted `facts["rungs"][0]["unrouted"] == 0`, i.e. that rung 1 of
`routelib.DEFAULT_LADDER` (`mp: 20`) fully routes blinky2. Freerouting itself is deterministic here
(`-mt 1 -is sequential`), so the assert is not racy in the usual sense - what varies is whether the
rung finishes inside `--timeout-s` (default 600 s per rung). Under parallel-session load the rung is
killed with partial passes, `unrouted` comes back non-zero or None, and the failure message points at
the router rather than at the host. Second occurrence of the class already recorded on 2026-08-06
(wave-1 sessions); this one surfaced at U0. **Decision: pin, do not tolerate** - but pin the
DISTINCTION, not the number. `route_auto` already reports `timed_out` per rung, so the ladder-quality
claim is asserted only when the rung actually got its budget; a starved rung leaves the flow-level
assertions (`completion >= 0.9`, `unrouted_nets <= {GND}`, board replaced atomically) doing the work,
and those do not depend on which rung won. General rule for any test that wraps a time-boxed external
solver: assert the OUTCOME the tool controls, and make the tool's own timeout flag the branch - a
bare quality assert on a wall-clock-bounded process is an environment sensor wearing a regression
label. In the U0 full-suite run (2026-08-13, single session) the test PASSED and the only failure was
the standing AP63203 `net` test.

## 2026-08-13 [tests][git][report_gen] Committing a generated deliverable flips a litter assertion that encoded "untracked" - and `check.cmd` re-dirties three workspaces every run
`test_report.py`'s two smoke tests asserted `all(ln.startswith("?? ") and "/reports/design_doc/" in ln
for ln in new)` over the git-status lines a `report_gen` run adds. Their comment says the intent -
"nothing new outside reports/design_doc/, no tracked file touched" - but the `?? ` half silently
encoded a second premise: that the board's design doc had NEVER BEEN COMMITTED. Both tests passed for
weeks while the pdf/tex sat dirty in the tree, because a file that is already ` M ` contributes no NEW
status line at all; the set difference was empty and the assertion was vacuous. U0 committed those
regens (the v3 plan says to), the baseline went clean, and the very next full-suite run failed both
tests with ` M boards/<b>/reports/design_doc/<b>-design-doc.tex` - a real change in what the assertion
measured, with nothing wrong in the code under test. Fixed by dropping the prefix requirement and
keeping the scope one (`assert_no_residue_outside_design_doc`): anything appearing outside
`design_doc/` still fails, which is the invariant worth having.
Two things worth carrying. **(1) A vacuous assertion and a passing assertion look identical.** If a
litter test's power depends on the baseline being clean, it is measuring nothing on a dirty tree -
consider asserting the set difference is non-empty first, or run the probe against a clean scope.
**(2) `check.cmd` is not hermetic** (codex H2): `report_gen` has no output-dir override, so the suite
regenerates the pd-trigger and stm32-blinky design docs in place, so every full run leaves those TWO
workspaces dirty with a new timestamp (measured on the U0 acceptance run: 1 failed / 1511 passed, both
docs dirty afterwards). lumina-carrier's doc is NOT touched by the suite - only a hand `report_gen`
run dirties that one, which is how it arrived dirty at U0. "Clean tree" is therefore a between-runs property,
not a steady state - do not treat a dirty design_doc after `check.cmd` as unfinished work, and do not
commit it reflexively either. The durable fix is a `--doc-dir` on report_gen so the smoke run can
write to tmp.

## 2026-08-14 [decoupling][gates][schematic] Value-classing cannot see a MISSING cap - a role must come from metadata, and absence needs a group check
check_decoupling judges each association by its VALUE class (bulk/mid/hf), so lumina-carrier's
22 uF-only buck input read as "bulk cap, loose 20/30 mm limits, fine at 9.89 mm" and the absent
100 nF HF ceramic on U21 (TPS563201) VIN produced ZERO findings - the exact defect the P8 reviewer
had to catch by hand and the only soldering-iron rework on the shipped batch (retro R1). Two
structural lessons. (1) The checker's unit was the ASSOCIATION: every check answered "is this cap
well placed?", and no check could answer "is a cap MISSING?" - absence is only visible to a
GROUP-level requirement over a declared role, never to per-item thresholds. (2) The role cannot be
inferred from value or topology at check time: a 22 uF at a buck VIN is legitimate as the
reservoir, wrong as the only cap - only the emitter knows the pin is a switching-regulator input.
U1 adds `role: "reg_input"` to the decoupling metadata (schlib passthrough + intake docs) and a
per-(ic,pin,rail) group check: no HF-capable member (<= 1 uF or explicit class "hf") within the
hf error distance (7.5 mm) -> error kind=reg_input_no_hf anchored at the PIN. Declaring the bulk
cap reg_input does NOT tighten its own per-association limits - the group check carries the
requirement, so the reservoir stays legal where it is. Known-answer: carrier U21 fires at
9.89 mm; U20 is the clean twin (C61 100 nF at 5.16 mm qualifies its group).

## 2026-08-14 [yaml][knowledge] An unquoted YAML flow-scalar value splits at ANY comma - and only additionalProperties catches the wreckage
Authoring knowledge records as YAML flow mappings, `{file: x.md, section: s4 (choice, fallback)}`
silently parses as THREE keys: `section: "s4 (choice"` plus a bare `fallback)` key - the comma
inside the parenthetical ends the value. Nothing errors at load; the damage surfaces only because
RECORD_SCHEMA sets `additionalProperties: false` on sources entries, which turned it into a named
schema violation at `knowledge.py --validate` (two records failed exactly this way at U4 build).
Rule: quote any flow-mapping value containing a comma; keep additionalProperties:false on every
schema level of hand-authored YAML - it is the only thing that makes this failure visible.

## 2026-08-14 [yaml][python] yaml.safe_dump ASCII-escapes non-ASCII, so a raw-file ASCII lint passes while the PARSED values smuggle it through
knowledgelib's ASCII check read the raw file bytes (the test_remediations pattern - correct for
hand-written md). A record written via `yaml.safe_dump` (default allow_unicode=False) encodes a
mu sign as an escape sequence: the file on disk IS ASCII, the lint passes, and the non-ASCII
character reappears at yaml.safe_load - straight into a prompt_block that contracts ASCII-only
output. Fix in knowledgelib.validate: after parsing, `json.dumps(data, ensure_ascii=False)
.encode("ascii")` - checks every parsed string in one shot. Any linter of machine-WRITTEN YAML/
JSON must check parsed values, not file bytes; raw-byte checks only cover hand-authored files.

## 2026-08-14 [constraints][lint] Adding a documented constraints key can retro-flag SHIPPED artifacts - the close-match net catches neighbors, not just typos
constraints_lint errors on unknown keys with difflib ratio >= 0.8 to a documented key. Adding the
U4 `blocks` key made lumina-strobe's committed research envelope key `subblocks` (ratio 0.82)
retroactively read as a MISSPELLING error, failing test_shipped_artifacts_have_no_errors on a
file U4 never touched. The close-match net is doing its job - but it means every NEW documented
key must be checked against the shipped-artifact corpus in the same commit, and legitimate
envelope neighbors added to KNOWN_ENVELOPE (where `subblocks` now lives). The failure is
invisible until the lint suite runs repo-wide: run test_constraints_lint before committing any
schema-key addition.

## 2026-08-14 [gerber][dfm][fixtures][tests] Deleting a Gerber D01 draw does NOT open the contour - coordinates are modal, the ring re-routes
Building the U2 open-outline mutant (the carrier's silently-skipped edge checks, codex C7): removing
a mid-contour `X...Y...D01*` line from Edge.Cuts left `FabStack.outline` CLOSED and the dfm coverage
test red. Gerber coordinates are modal: each D01 draws from the CURRENT point to its target, so
dropping a draw just makes the next D01 draw from the previous vertex directly - the polygon loses a
corner but stays a ring, and polygonization succeeds. Same trap for "nudge an endpoint": the next
draw starts at the moved point, so the ring distorts but never opens. To genuinely open a contour,
break CONTINUITY, not geometry: turn one mid-contour D01 into D02 (draw -> move) - the ring splits
into two open chains and polygonization fails exactly like the carrier's 1 nm joint mismatch did.
Pinned by tests/test_gate_strict.py::_tampered_gerbers.

## 2026-08-14 [git][process][waves] Wave-parallel sessions in ONE working tree: anchor-edit shared files, re-verify your hunks, stage by file list
U2 ran while U1/U3/U4 were live in the same checkout (the v3 wave-1 design - no worktrees).
dfm_check.py GREW ~60 lines under this session between read and edit (U3's assembly-class legs);
the exact-anchor Edit model composed cleanly because the anchors sat in regions the other session
was not touching (run() tail + argparse vs check_release) and the additions were purely additive.
What made it safe: (1) after any "file changed on disk" notice - and again before the session-end
commit - re-grep your OWN hunks; the other session may Write the whole file over them. (2) Commit
an explicit file list; never stage a shared file that still carries the other session's
uncommitted hunks - leave it for whichever session closes later (transient HEAD incoherence is
fine, the TREE stays green and each session's check.cmd runs against the tree). (3) New tests go
in a NEW file, never a test module another wave step owns. This is codex C4's staging lesson
applied to the build sessions themselves.

## 2026-08-14 [bom][kicad][dfm] KiCad's footprint `(attr ...)` flags ALREADY are the assembly class - reading them classed every non-part refdes on rf-de for free
U3 had to give 70 rf-de refdes an assembly class without hand-authoring 70 statements. The 9 DNP
sites are genuinely canonical parts data (`refdes_dnp` in `parts/parts.json`), but the other 61
needed no statement at all: `board_only` / `exclude_from_bom` -> a board feature, `dnp` -> DNP,
`exclude_from_pos_files` ALONE -> in the BOM but hand-fitted. Reading those three flags off the
board file classed all 12 of rf-de's non-part refs - H1-H6, FID1-3, HS1 **and L301/L302, the two
etched air-core spirals** - with zero extra data, and the spirals are the interesting case: they
are real netlist components with no LCSC number, so a class model that only knew "in the position
export or not" would have reported them as unbuyable parts forever. The generalisation: before
inventing a metadata field, check whether the CAD file already states the same fact - board_init
has been marking mounting holes `board_only exclude_from_pos_files exclude_from_bom` since S8, so
the statement existed three plan-waves before anything read it. Priority still matters
(parts.json > board attribute > default), because a `Variant=DNP` schematic field cannot reach the
board file at all (2026-08-07 entry) - the board attribute is a fallback, never the authority.

## 2026-08-14 [bom][fixtures][process] A hand-filtered artifact cannot be byte-reproduced by the generator that replaces the filter - and the SORT KEY is where it shows
Retiring rf-de's `fab/filter_dnp.py` came with an obvious acceptance test: regenerate BOM/CPL from
the assembly classes and byte-compare against the shipped package. CPL.csv matched exactly. BOM.csv
did not - same 25 rows, same 59 designators, one row in a different position. Cause: the shipped
file was sorted while it still contained the DNP refs, so the 56 pF line sorted under **C203** - a
designator the filter then deleted from that very line. The generator sorts by C301, the first
designator the line actually keeps. Neither ordering is wrong; the old one is a fingerprint of the
two-step process. Lesson for any "replace the manual post-step" acceptance: expect byte-identity
only for artifacts whose ORDER is derived from data that survived the post-step, and when a
byte-diff appears, diff the SORTED files first - if the row sets match, you are looking at
provenance, not a defect. Recording which of the two orderings ships (and why) belongs in the same
commit, or the next reader re-opens the question.

## 2026-08-14 [bom][parts][dfm] "No LCSC number" is not "cannot be bought" - conflating them turns a correct hand-built package into a false release blocker
Making a missing LCSC an ERROR on machine-placed parts (codex H1) immediately failed rf-term-150w,
whose C1 is a Johanson 5602 air trimmer bought from DigiKey - a real MPN, a real distributor part
number, no LCSC line and never will have one. The board's own decision A5 says "hand-built, 5 off,
not JLC PCBA", so the package is correct and the checker was wrong. Fix: BOM completeness asks
whether the part can be BOUGHT (LCSC number, or MPN + a named distributor line), and being off-LCSC
is a separate WARNING class (`dfm_bom_off_lcsc`) that says only "JLC cannot fit this - supply it,
substitute, or reclassify as hand_install". The general trap: a release check written against one
fab's catalogue silently encodes "orderable at THAT fab" as "exists". Keep the two facts in
separate fields, and let the strictly-JLC judgement live where the fab is actually chosen (the U5
order attestation), not in a geometry-and-package checker every board runs.

## 2026-08-07 [RETRACTED] The P1 "EPC2019 correction" was WRONG - the ORIGINAL brief numbers were right
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
**RETRACTED IN FULL.** A P1 agent (research-power-architect) reported Coss(er) 156 pF, Rds(on)
22/42 mohm, Qg 2.4/2.9 nC as "datasheet-verified" and this file recorded them. They are not in the
datasheet. The orchestrator later read the actual PDF pages (EPC2019 rev. (c)2021, pages 1-2)
directly. **Authoritative values:**

| Parameter | Value (datasheet, read directly) |
|---|---|
| Rds(on) | **36 typ / 50 max mohm** (cover headline is literally "R_DS(on), 50 mohm") |
| Coss | **110 typ / 150 max pF** @ VGS=0, VDS=100 V |
| Qoss | **18 typ / 23 max nC** @ VDS=100 V |
| Qg | **1.8 typ / 2.5 max nC** |
| Ciss / Crss | 200/270 pF, 0.7/1 pF |
| VGS abs max | **+6 / -4 V** |
| Thermal | RthJC **2.7**, RthJB **7.5**, RthJA **72** C/W (the one thing P1 got right) |
| ID | 8.5 A cont. (Ta 25 C), 42 A pulsed |
| Package | passivated **die with solder bars**, 7-bar row (P1's package correction WAS right) |

**There is NO Coss(er) or Coss(tr) figure anywhere in this 6-page datasheet.** Any design that
needs an effective Coss must derive it, and say so.

**Method lessons (two, both expensive):**
1. Never let a web-summary spec into a frozen operating point (the original sin).
2. **An agent asserting "datasheet-verified" is not verification.** Two agents contradicted each
   other; only reading the PDF settled it. For any number the whole design hangs on, the
   orchestrator reads the primary source itself. Cost of not doing so here: a whole P2
   architecture built on an invented capacitance.

## 2026-08-07 [footprint][gan] EPC2019: the 0.68-0.70 mm figure is the OUTER ENVELOPE, not a pad centre spacing
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
A fixer was instructed (by the orchestrator, on a librarian report) to widen EPC2019 column 1 from
0.46 to ~0.68-0.70 mm. It refused, extracted the datasheet's vector geometry, and proved
**both end columns are 0.45 mm centre-to-centre.** The 680/700 numbers are the envelope:
`450 + 230 (mask dia) = 680`; `450 + 250 (bump dia) = 700` = dim `c` = bar-pad length.
Cross-check: `(B-h)/2 = (950-450)/2 = 250` = dim `i`, which only closes if h applies to both ends.

**Applying the "fix" would have shifted the GATE pad 0.11 mm off a 0.25 mm pad** - the dead-board
failure the task existed to prevent. Correct action taken: 0.46 -> 0.450 exact, copper to bump size
with -0.01 mask margins (a genuine mask-defined land).

**Lesson: a land-pattern dimension read out of a report, not off the drawing, is a rumour.**

## 2026-08-07 [sourcing][jlcpcb] LCSC's package/type fields are USELESS for connector mount style - check pad layers
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
The part-sourcer marked an SMA "CONFIRMED genuine SMD board-side-launch". The manufacturer drawing
showed a vertical **through-hole** screw-thread panel jack. Both good and bad parts read
"Board Side ... SMA SMD" in LCSC's fields; `package: "Plugin"` (Chinese for plug-in/THT) is the
only tell, and it is not always present. **"Board Side" is a connector TYPE, not a mount style.**

Reliable two-step screen, now proven:
1. Pull the EasyEDA footprint and **inspect which layers its pads are on.** Any **B.Cu land** means
   it protrudes/needs bottom copper - fatal for a flat bottom-side heatsink face.
2. Confirm against the **manufacturer's customer drawing**, not the LCSC listing text.

Result: 4 of 4 orchestrator-suggested candidates were THT; 5 of 7 SMD-labelled alternates carried
B.Cu lands. Winner **C22418168 / CONSMA001-SMD-G-T** (TE/Linx) - drawing sheet 1 states verbatim
"Termination: PCB Surface Mount", centre-contact standoff 0.00-0.10 mm (nothing below the board).
**Cost of correctness: $0.39 -> $2.97 each** (+$25.86 on a 5-board build). 100% SMT preserved.

Also: pulled footprints carried two other latent fab defects worth checking for generally -
an `attr through_hole` on a surface-land cap (would have put it in the P9 CPL's THT column), and
courtyards that cut through their own pads. And **tent EP thermal vias on both faces** when a
bottom heatsink is involved - a mask-opened via there takes solder and breaks flatness.

## 2026-08-08 [driver][gan][gate] TI's ">=2 ohm at each output" is a PER-PIN floor, and paralleled FETs multiply it
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
The LMG1020 datasheet (s8.2 Typical Application) says "use at least a 2-ohm resistor at each OUTH and
OUTL". Read as a per-LEG spec that is easy to satisfy and easy to get wrong. This board hangs BOTH
paralleled EPC2019 off the same OUTH/OUTL pins, so the pin sees the PARALLEL combination of every
branch on it: 2 x 3R9 per leg x 2 FETs = 4 x 3R9 = **0.975 ohm**, less than half the floor, while
each individual leg looked like a compliant-ish 1.95 ohm and the schematic note said so. Nothing in
ERC, netlist_audit or check_* can see it - it is arithmetic over a net, not a rule violation.

The general form: with N devices on one driver pin, **each branch must be >= N x (the datasheet
floor)**. Here N=2 -> 4 ohm/branch minimum; 4R7 was taken (2.35 ohm/pin, 2.13 A peak instead of
5.1 A). The floor exists to bound gate-loop di/dt, and on an eGaN part the consequence of ringing is
not degradation but destruction: VGS abs max is +6 V against a 5 V drive, ~1 V of headroom, no
avalanche margin, gone on the first pulse.

TWO SECOND-ORDER EFFECTS THAT BOTH POINT THE SAME WAY, and are worth knowing BEFORE choosing R:
- **The gate-loop inductance budget scales as R^2.** EPC WP008 Eq.1 is L <= R^2.C_GS/4, so going
  3.1 -> 5.85 ohm total moved "the tightest layout spec on the board" from 0.48 nH to 1.70 nH. Raising
  R_G to fix a driver-floor violation BUYS layout margin; do not also keep treating the old
  inductance number as binding when picking package sizes.
- **Per-resistor dissipation goes UP even though total gate power does not.** Total is Qg.VDD.fSW per
  FET regardless of R; what changes is the split between the driver's internal impedance and the
  external resistor, and collapsing 2 parallel parts into 1 doubles the per-part share on top. Here
  0.029 W/part -> 0.100 W/part at Qg_max, which an 0603 (100 mW, 65 mW derated to a 90 C local board)
  cannot take. Size the package from Qg_max x VDD x fSW / 2 x R_ext/R_total, derated to the LOCAL
  board temperature, not the 70 C datasheet point.

## 2026-08-08 [footprint][sourcing][polarity] On the RVT (JIERR/Lumimax) V-chip electrolytic family the CHAMFERED corners mark the ANODE
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
The usual reading of a chamfered/bevelled base corner on an SMD aluminium electrolytic is "cathode",
and a P4 review flagged C101/C102 as unadjudicated on exactly that basis: the EasyEDA symbol puts "+"
at pin 1 while the footprint's only asymmetric feature - two chamfers - sits on the PAD-1 side, so
symbol and footprint appeared to contradict each other. The manufacturer drawing settles it the other
way. JIERR "RVT series" (LCSC C51953411, now committed at parts/C51953411.pdf) page 2 labels the two
terminals "Positive" and "Negative" with leaders, and **"Positive" lands on the chamfered end**;
Lumimax's SMDGP/RVT drawing agrees from the opposite face, putting the black negative top-stripe on
the SQUARE-cornered side. So the pull was self-consistent and correct all along.

Two transferable points. (1) LCSC's product page is HTML at `lcsc.com/datasheet/C<code>.pdf`; the
real PDF link is inside it (`datasheet.lcsc.com/datasheet/pdf/<hash>.pdf`) - an empty `datasheet`
field in parts.json usually means nobody followed the redirect, not that no datasheet exists. Fill it
at P3; a blank one on a POLARIZED part is what turned this into an unresolvable review finding.
(2) A chamfer is a BODY-OUTLINE feature, not a marking: this footprint's silk body square is +/-5.23 mm
and the part's plastic base is 10.3 mm, so once assembled the cue is invisible for inspection. Even
when the geometry is right, a polarized part still needs a real silk "+" placed OUTSIDE the body.

## 2026-08-08 [kicad][footprint][connectivity] Footprint copper GRAPHICS are not in KiCad 10 connectivity - an etched-copper part must be made of PADS
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
(Relocated from root LEARNINGS.md - this workspace keeps its own file while concurrent runs are in
flight. Discovered building the PCB spiral inductors, `kicad/gen/spirals.py`.)

KiCad's own `NetTie.pretty` draws its shorting bar as an `fp_poly` on F.Cu, so `fp_poly` on a copper
layer looks like the natural way to author an etched component (spiral inductor, PCB antenna,
current shunt, coplanar structure). **It is not.** Measured on 10.0.3 with a scratch board
(`CreateEmptyBoard` + `FootprintLoad` + pad nets + `kc.py drc`):

- an `fp_poly` on F.Cu joining two same-net pads 18 mm apart leaves them **`unconnected_items`** -
  the graphic carries no net and conducts nothing as far as connectivity is concerned;
- it additionally raises **`shorting_items`** ("nets <blank> and NA") against every netted pad it
  touches, because it is netless copper;
- `(net_tie_pad_groups "1, 2")` suppresses the `shorting_items` half but NOT the unconnected half.

The stock NetTie footprints get away with it only because each of their nets has exactly one pad, so
nothing is left to be unconnected to. Easy to misdiagnose: a footprint whose winding is a graphic
DRCs "clean" apart from an unconnected count that reads like a placement problem.

**Correct encoding for copper-is-the-component parts, all verified on 10.0.3:**
- winding/antenna body = `(pad "N" smd custom ... (primitives (gr_poly ...)))`, one per copper
  layer; ~440-point primitives load, round-trip and plot fine;
- `(net_tie_pad_groups "1, 2")` for the deliberate pad1-pad2 short (an inductor IS a DC short);
  pads of DIFFERENT numbers may overlap inside a tie with no violation;
- footprint-level rule areas `(zone ... (keepout (tracks not_allowed) (vias not_allowed)
  (pads allowed) (copperpour not_allowed)))` ARE supported, round-trip through pcbnew
  (`fp.Zones()` -> `GetIsRuleArea() True`), and are how a "no plane under this part" rule travels
  with the footprint;
- an SMD pad on inner layers only (In1+In2 bridge) works but costs one **`padstack` WARNING**
  ("SMD pad has no outer layers") that must be waived;
- duplicate pad numbers mixing `smd custom`, `smd rect` and `thru_hole` in one footprint are legal
  and connect correctly wherever they share a layer - `thru_hole` is the ONLY thing that ties an
  F.Cu pad to an In1/In2 pad at the same x,y.

**Second-order trap: DRC cannot check clearances INSIDE a net tie.** The tie exempts pad1<->pad2
entirely, so the inner-land-to-adjacent-turn gap (0.014 mm in the first cut of this part - a shorted
turn that would have collapsed the inductance) reports nothing at all. Any critical spacing between
tied pads must be enforced in the generator and measured out-of-band; **a scratch-board DRC pass is
NOT evidence that a net-tie part is geometrically sound.**

## 2026-08-08 [placement][p6] Locking a group ANCHOR silently orphans the whole group in place_seed/place_anneal
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`constraints.json.placement.fixed` on this board holds Q201, U201, R203-R206, L301, L302, J101,
J301 - i.e. the anchors of `switch`, `tank_ls`, `tank_lm`, `drive_in`, `bus_in`. `placelib.
build_clusters` sends any footprint that is `not is_movable or ref in placement.fixed` to the
**fixed** list, so a group whose anchor is fixed produces **no cluster at all**: place_seed's report
listed only 4 clusters (C110, C111, R104, U101) out of 6 declared groups, and C101-C104, C207-C212,
L201, L202, R201, R202 were left untouched at their P5 shelf coordinates (they then read as
`outside_outline` / `courtyard_overlap`). The same mechanism drops `separation` entries -
place_anneal reported `separation_unknown_refs: ["L301","L302","U201"]`, so the declared
"U101 >= 30 mm from either spiral" was never enforced and had to be checked by hand.

**Consequence for any board with a hand-built floorplan: everything in a group whose anchor you
lock is yours to place.** Here that meant hand-placing 50 of 70 parts and leaving only the `hk`
cluster (the one anchor NOT in `fixed`) to seed+anneal.

## 2026-08-08 [kicad][silk] place_edit `add_text` is idempotent only at the SAME coordinates - and there is no delete op
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`add_text` matches an existing text by (string, layer, position within 0.01 mm). Emitting the same
string at a *new* position therefore ADDS a second copy, and no `del_text` op exists. A misplaced
silk mark cannot be un-done through the ops interface. On this board the C101/C102 polarity "+"
landed on pad 1; the fix was to **move the capacitors** 1 mm instead of moving the text.

## 2026-08-08 [drc][creepage][gan] A qualified die's OWN terminal pitch is not a board creepage violation - the "part choice is the defect" rule has an exception
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Refines the root-LEARNINGS entry "a voltage-class DRU rule only protects the nets you NAMED, and the
ICD's own part-size policy can violate its own creepage rule", whose conclusion was: *if a creepage
rule is tighter than a land pattern's own pad gap, the PART choice is the defect.*

That holds when the part was chosen by policy and a larger one exists (lumina-par's 0805 -> 1206).
**It does not hold here.** P6 on rf-de-20m produced 12 `clearance` errors that are all intra-EPC2019:
its 0.6 mm solder-bar pitch leaves ~0.35 mm drain-to-source gaps, against the board's own
`aiee_hv_143v_SW` rule of 0.8 mm (IPC-2221, derived from /SW's 143 V peak).

Why this one is a waiver and not a part-selection defect:
- The 0.35 mm gap is **die geometry** on a manufacturer-qualified **200 V** device. EPC rates the
  part at 200 V *with that pitch*; the spacing is internal to a passivated die, not board copper.
- **IPC-2221 creepage/clearance governs board-level conductor spacing**, not the terminal geometry
  of a qualified component. Applying it inside a footprint is a category error.
- There is **no alternative part**: EPC2019 is the only 200 V GaN that closes this design, and every
  eGaN FET in this class has comparable bar pitch. "Choose a bigger part" has no referent.

**Practical rule:** when an HV DRU rule fires *inside* a single footprint, first ask whether the
pads belong to a qualified component or to board copper. Component-internal -> document and waive
with the vendor's own voltage rating as evidence. Board copper (including two discrete parts placed
close, or one part's pads on a land YOU specified) -> the original rule applies and it is a real defect.

Same board, same class, DIFFERENT verdict: U101's 4 `solder_mask_bridge` errors come from netless
PTH holes inside the LM5017's exposed pad. Those ARE ours (a pulled-footprint artefact), so they get
fixed or re-drawn, not waived.

## 2026-08-08 [PIPELINE BUG][planes][constraints] constraints rects are consumed as ABSOLUTE board coords, but board_init does not place the outline at the origin
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
**This is a skill-level defect, not a board-level one - it should be promoted to root LEARNINGS.md
once the concurrent runs settle.** Found at rf-de-20m P6 while chasing a missing GND return.

`planes_gen._region_rect` and `placelib._forbidden` consume `constraints.planes[].region` and
`placement` `rect` **verbatim as absolute board coordinates**. But `board_init` placed this 120x80
outline at origin **(6.635, 39.335)**, not (0,0). Architecture authors rects in the natural
board-local frame, so every rect silently lands displaced by the outline origin:

| Declared (board-local) | What it actually meant | Consequence |
|---|---|---|
| zone A `[0,0,48,80]` | poured only board-local x 0-41, **y 0-41** | buck + half the FET thermal island get **NO plane** |
| zone C `[88,0,100,80]` | landed **on top of L301** | plane over a spiral = shorted turn |
| heatsink keepout `[5,10,36,70]` | equally displaced | keepout guarding nothing |

**Nothing in the pipeline catches this:** `constraints_lint` does not know the board outline, and
`place_metrics` never fires the keepout (it is declared `side:"back"` and gated on `is_movable`).
`stackup.md` s2.1 documents translation as a mandatory P5 step - and it is simply skippable, with no
gate behind it. The failure is silent and survives to fab: you get a board that DRCs clean with no
plane where you thought you had one.

**Practical rules:**
1. After `board_init`, read the outline origin and translate EVERY rect in `constraints.json`
   (`planes[].region`, `placement` rects/keepouts) into absolute coordinates before `planes_gen`.
2. Record the translation in the file (`_coord_note` / `_planes_note` with the board-local
   equivalent) so the next reader cannot mistake the frame.
3. **After the P7 pour, re-run a geometric island check** - the FILL is the authority, not the
   planned rectangle. Verify each plane net forms the island count you intended.

## 2026-08-08 [layout][rf] A pour can bend: the "corridor width" between two keepout discs is the perpendicular gap, not the straight-strip intersection
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Orchestrator error worth keeping. Given two circular keepouts, I computed the clear corridor as the
intersection over all x of their y-spans - the widest straight horizontal strip - and got 2.8 mm,
concluding the ground return was unroutable.

That is the wrong measure, because **copper pours are not required to be straight**. The real
constraint is the perpendicular gap `d - 2R` between the disc edges, projected onto the corridor:
`45.88 - 41.10 = 4.78 mm` perpendicular = **5.01 mm** measured as a vertical section. Scanning the
actual clearance along x: 80 mm at x=48, 45 at x=60, 9.5 at x=70, **5.0 at x=78** (the pinch),
14 at x=90, 44 at x=95.

The underlying concern was still valid (and the real cause was the coordinate bug above), but the
number was wrong by 1.8x. **Measure a routing corridor as the minimum perpendicular gap along the
path, not as the largest inscribed rectangle.**

## 2026-08-08 [P7][kicad][zones] A `.kicad_dru` rule BEATS a zone's local clearance during fill - so a pour can never reach a fine-pitch HV die
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Measured on this board, both directions, same run:

- A **GND** zone with `(connect_pads yes (clearance 0.35))` next to the EPC2019 filled to
  **0.8006 mm** from every `/SW` pad - i.e. KiCad used `aiee_hv_143v_SW` (0.8 mm), not the
  zone's own 0.35 mm. The consequence is not cosmetic: the fill stops **0.20 mm short of the
  source bumps and 0.66 mm short of pin4**, so *no pour of any clearance setting can connect
  the die*.
- The same is true for a **/SW** zone: three fan-in lobes authored at clearance 0.35 over the
  drain bars filled **0.0 / 0.85 / 2.08 mm2** - all of it pushed 0.8 mm away.

**Practical rule: `SetLocalClearance` only ever LOOSENS a zone against nets that no custom rule
names.** If a per-net `.kicad_dru` clearance rule exists, the zone obeys the rule. To attach a
pad the rule holds a pour away from, you need a TRACK or a VIA - the filler is not negotiable.

Corollary for this board: the EPC2019's 7 bumps are on a 0.6 mm pitch with 0.35 mm drain-to-
source gaps, so the whole die fan-in (4 source escapes + 2 drain escapes + 1 rung per FET) is
0.25 mm hand track. GND carries no per-net width rule so those cost no `track_width` findings;
`/SW` does, so the two drain escapes and their rung are `track_width` findings by construction.

## 2026-08-08 [P7][kicad][swig] A via added into an ALREADY-FILLED zone takes the ZONE's net, not the op's
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`route_edit`/`stitch_vias` set the net explicitly (`v.SetNet(...)`) and the worker reports
`added`, but the saved board carries a different net and the post-apply verify then fails with
`via at (x, y) (+40V) not in saved board`. Grepping the board shows the via present with
`(net "GND")`.

Cause: KiCad re-derives a via's net from connectivity, and a board whose zones are already
filled has **no antipad yet** at the new hole - so the via is electrically inside the GND plane
at the moment it is added. Verified by placing the identical via on the bare board (net kept)
and on the poured board (net stolen), same coordinates.

This never bit the pipeline before because it only ever stitches GND (or a power net whose
plane is that net) - the "stolen" net is the one it wanted. It bites the moment a **power net
needs a bridge on a layer the plane owns**.

**Build order that works:** place power-net vias on the BARE board -> pour GND -> stitch GND ->
pour the power nets last. `route_edit` is atomic, so a mixed batch rolls back entirely; the
retry driver in `route/apply_ops.py` parses the verify message and drops the rejects (note
`route_edit` truncates that list to 10 per attempt, so it needs several passes).

## 2026-08-08 [P7][routing][krt][freerouting] Never hand KRT a PLANE-CARRIED net - GND alone blew a 1800 s budget
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Freerouting 2.2.4 **wedges reading this design** (rung 1 log stops after the
version banner; no pass lines, no SES, no completion) - the documented failure
mode on a board that already carries router-generated copper, and this one is
almost entirely pours + hand tracks. So the whole remainder falls to the KRT
mop-up, and two things had to be fixed before it would do anything:

1. **`route_auto`'s KRT pass inherits `grading_floors`, which takes the
   LOOSEST netclass clearance as KRT's base.** The HV power classes had been
   raised to 0.8 mm in the `.kicad_pro` so Freerouting would honour them in
   the DSN; KRT then tried to route the buck's 0.4 mm-pitch signals at 0.8 mm
   and produced nothing, which `route_auto` correctly reported as "not
   strictly better". **Keep HV clearance OUT of the netclass and pass it in
   `--net-clearances`** (which `route_critical.build_net_clearances` builds
   from the `.kicad_pro` AND the `.kicad_dru`) - the LEARNINGS 1522 rule, now
   confirmed from the other side.
2. **`route_auto` derives KRT's net list from the DRC's unconnected items,
   which includes GND** whenever a single GND pad is open. GND here has
   hundreds of pads on four layers, and KRT spent the full 1800 s on it
   without emitting a board. Excluding GND (it is plane-carried; the one open
   pad was a 0.2 mm WCSP ball that a single via closes) is what makes the pass
   finish.

Generalisation: the mop-up router's net list must be **the nets that are
actually meant to be tracks**. A plane-carried net that shows up unconnected
means "this pad needs a via", not "route this net".

## 2026-08-08 [P7][pipeline][rules_gen] Re-generating the schematic WIPES rules_gen's netclasses out of the .kicad_pro
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Found at P7 start: `kicad/rf-de-20m.kicad_pro` had **no `net_settings` at all**
and an empty `board.design_settings.rules`, even though `log/P5-rules_gen.json`
reports it wrote three power netclasses (`Pwr_5p5mm`, `Pwr_8p4123mm`,
`Pwr_11p8942mm`) and the fab floors.

Cause: `schlib.write_project()` writes a **whole minimal `.kicad_pro` from
scratch** (deliberately - "keep it MINIMAL; unexpected keys can make KiCad
reject the whole file"), and `Sheet.save(..., project=True)` calls it. So any
schematic regeneration after P5 - here the P4-FIX review pass - silently
deletes rules_gen's work. `rules_gen` is a P5 step and nothing re-runs it.

Consequences if it is not caught: the DSN handed to Freerouting carries no
per-net widths or clearances at all, and `route_critical.grading_floors`
(which reads the pro) falls back to KiCad stock defaults, so the mop-up

**Check `net_settings` in the `.kicad_pro` at the start of P7**, and re-run
`rules_gen.py --constraints ... --out-dru ... --pro ...` if it is missing.
Re-running is safe: the `.kicad_dru` it regenerates was byte-identical here.

## 2026-08-08 [P7][BLOCKER][krt] KRT `route.py` does not scale to a 120 x 80 mm board - it cannot finish ONE net
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Not a wedge, not a bad input: it simply never returns. Measured on rf-de-20m,
every configuration, each on a fresh stage with the correct floors and an
explicit `--net-clearances` map:

| grid | scope | budget | result |
|---|---|---|---|
| 0.05 | 11 nets incl. GND | 1800 s | no board emitted |
| 0.05 | 10 signal nets | 3000 s | no board emitted |
| 0.1 | per net | 300 s | `+5V` alone did not finish |
| 0.1 | per net, **zone-free board** | 240 s | `+5V` alone did not finish |
| 0.2 | per net, zone-free board | 240 s | `+5V` alone did not finish |

The zone-free run is the one that settles it: with NO pours on the board at
all (placement + a handful of hand tracks), a single 5-connection 0.2 mm
signal net still does not complete. So it is not the pours, not the HV
clearance map, and not the net count - it is the search space. 120 x 80 mm at
grid 0.05 is 2400 x 1600 x 4 nodes; every prior board in this pipeline ran KRT
as a mop-up for a few short nets on a smaller outline.

**Consequence: on a board this size, `route_auto` has no working fallback** -
Freerouting wedges on pre-existing copper and KRT cannot finish. Plan for
hand-routing the signal remainder, or route BEFORE any copper exists (FR's
wedge is triggered by existing router copper, not by the placement).

## 2026-08-08 [P7][freerouting][BLOCKER-RESOLVED][PROMOTE-TO-ROOT] Freerouting's DSN reader wedges on a pre-routed WIRE whose WIDTH rivals its LENGTH - not on big pads, keepouts or vias
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Supersedes the earlier "FR wedges on a board that already carries router-generated copper"
entry, which named the symptom and guessed the cause. Bisected on this board with
`route/fr_spiral_probe.py` and `route/fr_wire_bisect.py`. A healthy run prints
`Job '...' started` ~1.5 s in and finishes in ~10 s, so a MISSING `started` line is the wedge
signature and a 90 s budget is enough to classify a variant.

| DSN variant | result |
|---|---|
| as exported | wedges |
| both spiral winding padstacks (1116/828 pts, exported as 55-pt concave hulls) -> plain rects | **still wedges** |
| + the four r=20.3/20.55 mm polygon keepouts dropped | **still wedges** |
| `(wiring)` emptied (34 wires + 160 vias) | **runs**, 4 passes |
| wires kept, all 160 vias dropped | **wedges** |
| vias kept, all 34 wires dropped | **runs**, 5 passes |
| only the 4 land tracks > 2 mm wide dropped | **wedges** |
| the 9 highest width/length wires dropped (aspect >= 0.81) | **runs** |

A JFR profile of a wedged run (`-XX:StartFlightRecording`, then `jfr print --events
jdk.ExecutionSample`) puts every hot sample under `app.freerouting.io.specctra.parser.
Wiring.read_scope` -> `ShapeSearchTree.insert` -> `Simplex.intersects` /
`Simplex.remove_redundant_lines` (which is O(n^2) in half-planes), with 279 GCs in 70 s. It
never reaches pass 1. The trigger is the convex decomposition of a wire whose rectangle is
nearly degenerate - here **7.651 mm wide x 0.020 mm long (aspect 382)**, plus an
8.412 x 0.700, an 11.894 x 1.200, an 8.412 x 1.200 and five gate bars at aspect 0.81-1.44.

**This is a PIPELINE-LEVEL trap, not a board quirk.** `remediations/track_width.md` step 4
mandates pour fan-in - a short, full-width land track into a pour - whenever a net's DRU
width floor exceeds what its pad can take. That remedy PRODUCES wires of exactly this shape.
So does a wide gate bar landing on two resistors at once. Any board that follows the
remediation will wedge Freerouting on its next pass, and the failure looks like a hang with
no diagnostic at all.

**Working recipe** (`route/fr_signals.py`): export the DSN, delete every wire with
width/length >= 0.8 FROM THE DSN ONLY (the copper stays on the real board), run Freerouting,
then FILTER the SES to the nets that actually needed routing.

## 2026-08-08 [P7][kicad][swig][PROMOTE-TO-ROOT] ImportSpecctraSES REPLACES the board's wiring - it does not add to it
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Measured, and it silently deleted this board: `tracks_before 209, tracks_after 89`, and
unconnected went **24 -> 44**. `lib/route_swig.py`'s docstring says "adds copper only;
footprints/zones untouched" - the second half is true, the first is not.

`route_auto` gets away with `import_ses` only because the DSN it feeds Freerouting carries
every pre-existing track as a guide wire, so the SES echoes them all back and the round trip
is lossless (hence its `ses_echo_dups_removed` dedup step). The moment you hand Freerouting a
THINNED DSN, or filter the SES down to a subset of nets, the import becomes destructive.

**Fix: convert the session to `route_edit` ops instead** (`route/ses_to_ops.py`). route_edit
is additive, atomic and post-verified. SES geometry: `(resolution um 10)` -> 1 unit = 0.1 um,
and y is Specctra-up, so `board_y_mm = -y/10000`.

## 2026-08-08 [P7][freerouting][clearance] Per-net DRU clearances never reach Freerouting - and pushing them into the netclasses re-wedges it
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
KiCad writes the DSN's clearance rules from the `.kicad_pro` NETCLASSES only. This board's
HV rules (`aiee_hv_51v_40V` 0.5 mm, `aiee_hv_143v_SW` 0.8 mm, ...) live in the `.kicad_dru`,
so Freerouting routed at the netclass 0.2 mm and produced `/hk/BST` 0.26 mm from a +40V pad
and `+5V_DRV` 0.62 mm from a /SW pad.

The obvious fix - raise the three `Pwr_*` classes to 0.5/0.8/0.8 in the STAGED `.kicad_pro`
before export - **re-wedges the DSN reader** (no `Job started` line in 600 s), the same
failure mode as the degenerate wires. So there is no way to give Freerouting this board's
real clearances. Route with the netclass values and fix the HV-adjacent legs afterwards with
`route_edit`; that was 2 legs out of 15 nets here.

Related and still true from the other side: KRT must NOT get the HV clearance in its
netclass either (it then tries to route 0.4 mm-pitch buck signals at 0.8 mm and produces
nothing). Pass it in `--net-clearances`.

## 2026-08-08 [P7][drc][gotcha] A track can SWALLOW an existing via and steal its net; and check hole-to-hole against vias you did not place
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Two traps hit within minutes of each other while hand-routing, both found by DRC and neither
visible by inspection:

- a `/hk/BUCK_SW` track centred at x = 52.086 ran within 0.182 mm of a GND stitch via at
  (51.904, 93.17) - inside the via's 0.225 mm radius - so KiCad re-derived the via's net as
  BUCK_SW and reported `via_dangling`, not a short. The GND island it was stitching went
  quietly open.
- a new signal via at (29.85, 63.60) landed 0.584 mm centre-to-centre from a GND via at
  (30.085, 64.135): `hole_to_hole` needs 0.4995 mm plus both radii = 0.6995 mm.

This board carries 185 vias, most of them auto-placed GND stitching, so **scan
`bg.vias_of()` over the corridor before choosing a lane** - pads and tracks are not enough.

## 2026-08-08 [P7][zones][gotcha] A 0.2 mm signal track routed past a WCSP ball can starve the ball's only pour access
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`U201.B1` is a 0.2 mm GND ball whose ONLY pour access is the band north of U201's ball row
(south is B2 at 0.4 mm pitch, leaving a 0.098 mm sliver; west is C1 at 0.2 mm). Freerouting's
`A1 -> C202.1` link dived diagonally through that band and squeezed it to **0.25 mm**, below
the zone's minimum width, so it stopped filling and B1 read `unconnected_items` against
`Zone [GND]`. Nothing else changed and no clearance rule fired.

Re-routing the same link along a constant y = 62.75 restored a 0.76 mm band and B1
reconnected. **Neither a via nor a jumper could rescue it**: a via needs 0.45 mm + 2 x 0.1016
and the pocket is 0.775 mm wide but already holds a GND via; a diagonal jumper across the
2x2 ball square clears the neighbouring balls only at <= 0.034 mm width.

Rule: when a fine-pitch BGA/WCSP pad is fed by POUR rather than by track, treat the feeding
band as a routing keepout of at least the zone's minimum width plus clearance.

## 2026-08-08 [P7][footprint][drc] Netless PTH thermal vias inside an exposed pad: give them the EP's PAD NUMBER, not a mask change
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
The easyeda2kicad `SOIC-8 ... EP2.0` pull for the LM5017 shipped four `(pad "" thru_hole ...)`
inside pad 9. Their `*.Paste`/`*.Mask` layers had ALREADY been removed at P3 (tented both
faces, mandatory here because the bottom copper is the heatsink mounting face) and KiCad
STILL raised 4 `solder_mask_bridge` ("Front solder mask aperture bridges items with different
nets") plus 4 `clearance` errors at 0.25 mm against pad 9. Tenting the hole does not help: the
hole sits inside pad 9's mask aperture and carries no net, so the aperture bridges pad-9
copper to no-net copper.

**Renumbering them from `""` to `9` clears all eight at once** - same-net items neither bridge
nor violate clearance - and it is also what an EP thermal via IS. Verified on a scratch copy
of the board first (93 -> 85 violations) before touching the library.

Propagating a footprint change under a LOCKED part: rename the footprint, point the generator
at the new name, rebuild the netlist, and let `board_update` classify it as `swap_new_fp`
(rip and re-place at the recorded pos/deg/side, pads re-netted from the netlist). Two things
the swap does NOT preserve and that must be redone afterwards: **the lock** (re-assert with
`place_edit --ops` `lock`) and **any moved Reference/Value field**, which reverts to the
library default position - here straight on top of C106, producing 3 `silk_overlap` + 1
`silk_over_copper` until it was moved back with `place_edit` `move_text`.

## 2026-08-08 [P7][pipeline][lib_pull][PROMOTE-TO-ROOT] lib_pull --project re-normalises EVERY footprint in the library, not just the one you asked for
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`lib_pull.py --lcsc C2479122 --project ...` pulled the one 10R resistor as asked, and then its
`--refdes-norm` pass silently rewrote the Reference-text position of BOTH spiral footprints
(`SPIRAL_L110N`, `SPIRAL_L164N`) from `(at 0 -18.1)` to `(at 0 -4.825)` - i.e. from outside the
33 mm winding to on top of it. The board's placed copies are unaffected (they carry their own
geometry), so nothing DRCs differently, but a later re-import would have moved silk onto
copper.

`git status` on `lib/` after any `lib_pull` and revert what you did not intend. Use
`--no-refdes-norm` when adding a single part to a library that already contains hand-built
footprints.

## 2026-08-08 [P8][check_current][pipeline] `pour_neck` tests ONE zone at a time and only where vias land - so it can miss the bus entirely and flag a dead-end stub instead
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
The worst finding of the P8 verify pass, and the check pointed the fixer at the wrong copper.

`check_current.pour_neck` erodes **`z.fill_on(layer)` - a single zone's own fill** - and only
runs at all when that fill contains **>= 2 vias of the net** (`len(pts) < 2 -> return None`).
Both halves bite on any real power bus:

1. **Per-zone, not per-net.** `planes_gen` decomposes a bus into abutting rectangles. Each is
   eroded on its own, so a 4.7 mm column abutting a 3.2 mm strip abutting a 24 mm block reports
   whichever rectangle happens to hold vias, at ITS width - not the width of the conductor.
2. **Only where vias land.** On rf-de-20m the true bottleneck was the x 46..51 / y 34..56
   corridor, pinched to **2.295 mm** by R104 and to 2.25 mm by a `/hk/BUCK_SW` horizontal.
   Those zones hold no vias, so `check_current` said nothing about them. What it DID flag was a
   3.16 mm neck in the y 31..34.2 strip - which was carrying **zero current**, because a single
   0.200 mm `+5V` track crossed it diagonally and cut the fill in two. The finding was real
   arithmetic about copper that did not conduct.
3. **No parallel-path awareness.** `undersized_track` carries a `bridge` (cut-edge) label;
   `pour_neckdown` carries nothing equivalent, so a neck in one of two parallel branches reads
   the same as a neck in a sole path.

**What to do instead when a pour_neckdown fires on a bus that matters:** solve the copper.
`route/bus_solve.py` + `route/bus_cuts.py` in this workspace rasterise the net's copper on both
outer layers at 0.25 mm, tie the layers at the net's vias, inject the rail current at its source
pad and draw it at its sink pad, and report bus resistance, the current in each branch, and the
section-average A/mm with its IPC-2152-equivalent width. It is ~60 lines of scipy on top of
`lib/geom` and it runs in 8 s on a 120 x 80 mm board. It found a 10.4 mOhm / 369 mW bus with a
60-76 C hot spot that the gate had scored as one 3.16 mm neck in a stub, and it proved the fix:
5.5 mOhm, 196 mW, every section at 1.19-1.32 A/mm against the 1.273 A/mm that IS 5.500 mm at
7.0 A. **This is skill-level, not board-level** - promote with the numbers.

**Corollary, and it is the cheapest lesson here:** a single 0.2 mm signal track laid across a
poured power bus severs it, silently. Nothing in the pipeline reports it - the net stays
"connected" through the long way round, DRC is clean, `plane_repair` passes (each fill is
electrically whole), and the island count does not change because the bus reconnects elsewhere.
**After any signal-routing pass, re-check that each power pour's fill is still ONE piece between
its source and its load** (`zones_of(net)` -> per-zone fill part count is the cheap version).

## 2026-08-08 [P8][kicad][swig] The "via takes the zone's net" trap is NON-DETERMINISTIC when the outer layers already carry the via's own net
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Extends the P7 entry above. There the case was clean - a `+40V` via into a board where GND owned
every plane - and it failed every time. At P8 the new bridge vias landed where **F.Cu and B.Cu
were both already `+40V` fill** and only In1/In2 were GND, i.e. 3 of 4 layers agreed with the op.
It still failed, and it failed *differently each time*:

| batch | vias | lost |
|---|---|---|
| 8 vias at local x 50.1..43.8, y 31.3 | 8 | 1 (the x 50.1 one, whose centre sat 0.005 mm OUTSIDE the +40V fill) |
| the same 8 shifted one pitch west (49.2..42.9) | 8 | **4**, three of them at x-positions that had just SUCCEEDED |

So "is the via inside the target fill?" is necessary but not sufficient, and a passing attempt
does not predict the next one. `route_edit` is atomic and post-verifies every add, so both
attempts rolled back with the board byte-identical - the trap costs a retry, never a board.

**Only reliable procedure, and it is cheap:** strip every `(filled_polygon ...)` block from the
`.kicad_pcb` (paren-matched delete; the outlines in `(polygon ...)` stay), apply the ops on the
bare board, then `kicad-cli pcb drc --refill-zones --save-board`. That is the P7 build order
(`route/rebuild.sh` step 1) reached from the other direction: **you do not need the whole board
bare, you need the FILLS gone.**

Second-order, worth knowing before choosing via positions: a via whose CENTRE is 0.005 mm outside
the pour still reads as "connected" by eye and in DRC (its pad merges with the fill), but KiCad's
net derivation does not use the pad - it uses the centre. Check `net_copper(net, layer).contains(
Point(via.at))`, not `.intersects(via_pad)`.

## 2026-08-08 [P8][check_thermal][check_silk] Two P8 checks whose WINDOW, not whose model, produces the finding
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Both cost a fixer a wrong conclusion if the source is not read.

- **`check_thermal` counts thermal vias inside `max(2.0, sqrt(pad_hull_area/pi) + 1.5)` of the
  footprint CENTROID.** For an EPC2019 the pad convex hull is 1.84 mm2, so the window is
  **2.27 mm** - smaller than the via array it is asking for. It reported "found 3 via(s), want
  >= 10"; the board actually carries **9 within 4.0 mm of each FET centroid**. Measure the array
  yourself before believing the count. (Its theta_JA model is separately heatsink-blind by
  design - `theta_floor + (theta_0-theta_floor).exp(-A/tau)`, copper area is the only input, no
  heatsink/TIM/airflow term exists in the module. Its own docstring says "a screen, not a
  sign-off ... +/-30 %".)
- **`check_silk`'s attribution rule is a two-sided constraint** (> 1.0 mm from its own pads AND
  < 1.0 mm from another part's) and on a dense cluster it can be **unsatisfiable**. A grid search
  over every position within 4 mm of each part found **zero** legal positions for R203 here, 3
  for C202 and 6 for R204 - so "scripted fix: place_edit.py move_text" is not always available,
  and moving the five that CAN move would leave the sixth flagged while risking new
  `silk_over_copper` on a board whose DRC residual is signed off. Run the feasibility search
  before promising the fix.

## 2026-08-08 [P8][check_return_path] `k x trace_width` makes a POUR FAN-IN LAND look like a 71 mm-wide return-path defect
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`check_return_path` buffers a net's centreline by `k x width` (k = 3 by default). That is the
right model for a trace whose length greatly exceeds its width. It is the wrong model for the
short, full-width land track that `remediations/track_width.md` step 4 MANDATES to fan a pour
into a pad - the same construct that wedges Freerouting (entry above).

Here /SW's L301 terminal land is **11.894 mm wide and 1.200 mm long**, so its corridor is
1.2 x 71.4 mm: **27 % of the reported 81.29 mm2 deficit is off the board entirely** (the corridor
runs 18 mm past the north edge), and the rest is the deliberately plane-free magnetics zone.
Meanwhile the check never looks at the /SW **pour** at all - `corridor_on` reads `tracks_of()`
only - which is where the switching loop actually lives and which measured **96.13 % imaged on
In1**, with C203-C206 and L202.1 at 100 %.

**Practical rule: before treating a `corridor_void` as a defect, check the aspect ratio of the
track that produced it.** If width >= length, the corridor is a modelling artefact; measure the
real thing instead - `net_copper(sig, layer).difference(net_copper(ref, ref_layer))` over the
POUR, and the partial-inductance increment of the unimaged section
(`mu0.h.l/w` with an image vs `(mu0.l/2pi)[ln(2l/(w+t)) + 0.5 + 0.2235(w+t)/l]` without). Here
that was 0.031 nH vs 0.268 nH, i.e. **+0.24 nH on a 164 nH inductor - 0.15 %**.

## 2026-08-08 [P8][check_creepage][review] `checked[].pairs[].min_gap_mm == null` is how you PROVE a board has no hidden creepage - and the gate JSON throws it away
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Auditing "are all 21 creepage errors really intra-die, or is a real board-copper violation hiding
in the set?" looks like it needs a per-finding geometric investigation. It does not.
`check_creepage` already computes, for **every (primary net, other net, layer) combination it
sweeps**, the minimum gap among all items that came within `req_max` of each other, and reports
it in `checked[].pairs[].min_gap_mm`. **`null` means nothing on that layer for that net pair is
even within the largest applicable IPC-2221 requirement** - i.e. that pair is clean with margin,
proven, no inspection needed.

`gate.py` keeps only the `failing` list, so this evidence is **not** in `reports/gate-verify.json`.
Re-run the check standalone with `--out` to get it:

    .venv/Scripts/python [path masked] \
        --pcb <board> --constraints kicad/constraints.json --out creep.json

On rf-de-20m that reduced a 21-finding audit to three non-null rows (`/SW` vs GATE_Q1 / GATE_Q2 /
GND on F.Cu, all 0.350 mm) and proved every other pair - including `/tank/TANK_A` at 180 V and the
203 V `/SW`-`TANK_A` `voltage_pairs` entry - clean across all four layers in one read.

Two companion facts worth keeping:

- The violation records carry `item` / `other_item` **with a `type` field** (`pad` / `track` /
  `via` / `zone`). That is what settles "die geometry vs board copper": all 21 here were
  pad-or-escape-track, **zero involved a zone fill**. Dump the types before believing a waiver
  that says "this is the part, not the layout".
- `_same_fp()` downgrades only **pad-to-pad within one footprint** to `warning`. A vendor land
  pattern whose pads are 0.35 mm apart therefore emits 3 warnings *and* ~20 errors, because every
  escape **track** leaving those pads is a board item by the check's reckoning. Do not read the
  error count as "20 separate layout defects".
- The pour is a separate question and DRC will not answer it either. Measure it directly:
  `unary_union(zone fills of net A on L).distance(unary_union(every GND item on L))`. Here that
  returned **0.8004 mm**, i.e. `aiee_hv_143v_SW` honoured to 0.4 um - which is the fact that
  actually retires the "is HV pour too close to GND" worry.

## 2026-08-08 [spice][ngspice][sim] Ten machine-verified traps from authoring the P8 Class E bench
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
All reproduced on this host with the ngspice v46 that KiCad 10.0.3 bundles, driving
`scripts/sim_run.py`. Every one of them cost a debug cycle.

**`.measure` syntax, three hard limits.** (1) It does **not** accept the two-node form
`v(a,b)`: `.meas tran vgs max v(gq1,s1)` prints `failed!` and the measure simply vanishes from the
results. (2) It cannot read a **subcircuit-internal** source current: `.meas ... max i(Xm.Vsen1)`
also fails. (3) `param=` expressions CAN reference earlier measure results and do support `abs()`,
`sqrt()`, `pow()` and `ln()`. So the fix for (1) and (2) is the same - materialise the quantity as
a behavioural node inside the subcircuit and measure that:

    Bvgs1 vgs1 0 V={v(g1)-v(s1)}      -> .meas tran ... MAX v(Xm.vgs1)
    Bid1  id1  0 V={i(Vsen1)}         -> .meas tran ... MAX v(Xm.id1)

Both silently under-report if you skip them: `v(g1)` alone drops the whole common-source term,
which is the entire point of a gate bench.

**`vp()` returns RADIANS in `.meas ac`.** Verified against a known 4.13 + j4.760 network: `vm`
6.30148, `vp` -2.28551 (= -130.9 deg). `vr()`/`vi()`/`vm()`/`vdb()` all work and are the safer
route - extract R and X directly and never touch the phase.

**A SPICE current source drives current OUT of its + node.** `Iac ZIN 0 AC 1` gives
`v(ZIN) = -Z`. For impedance extraction write `Iac 0 ZIN AC 1`. The sign cancels in `X/R` so a
wrong-signed deck can look right on the ratio and be wrong on everything else.

**The behavioural capacitor `Cxxx a b c={expr}` works and conserves charge exactly.** Validated by
ramping 0 -> 142.5 V and integrating: an EPC2019 Coss fit reproduced Qoss(100 V) to 0.03 % and its
own analytic Coss(tr) to 0.02 %. ngspice implements it as an internal E-source (you will see
`e.xq1.ecoss#branch` and `xq1.coss_int1` in the vector list). **But do not write `abs(v(d,s))`
inside it** - the kink at v = 0 is unintegrable once the drain rings negative
(`Timestep too small ... 2.5e-23: trouble with node e.xq2.ecoss#branch`). Use the smooth positive
part instead, which costs nothing above ~0.2 V:

    c={cj0/pow(1+0.5*(v(d,s)+sqrt(v(d,s)*v(d,s)+0.01))/vj,mm)}

**Tight tolerances are a trap on a power deck.** `.options reltol=1e-4 abstol=1e-12 vntol=1e-7`
runs `classe_zvs_nominal.cir` fine and kills `gate_symmetry.cir` inside the first picosecond. A
1 pA `abstol` is meaningless against a 10 A power loop and it strands the gate-loop inductor nodes.
Engine defaults + `rshunt=1e9` ran every bench here.

**An ideal diode in series with an inductor has no state.** `.model D(... cjo=0)` plus a gate-loop
inductor leaves that inductor's node with zero capacitance while the diode is off, and the solver
cannot integrate it. Give the diode a real `cjo` (3 pF for a WCSP output ball is both physical and
sufficient).

**Lumping distributed copper invents GHz resonances.** A 0.157 nH common-source inductance against
a lumped 789 pF Coss is a 14 GHz mode at Q ~ 220, and it eats the timestep. 50 mohm of ESR in
series with the cap (0.25 % of its reactance at 20 MHz, and physically defensible) takes Q to ~9
and the deck runs. Same for the shunt bank at 20 mohm.

**`.step` is unusable through `sim_run.py`.** It runs, but `simlib.parse_measures` reads
`name = value` lines off stdout and later sets overwrite earlier ones, so a stepped sweep silently
reports only its LAST point. Sweep by instantiating N independent copies of the stage as a
`.subckt` with parameters - six full Class E stages in one deck cost 14.5 s, well inside the 60 s
per-bench gate timeout.

**Subcircuit instances sharing a top-level source silently load it N times.** Three GSTAGE
instances hung off one `Routh`/`Routl` pair moved off-state VGS from 0.21 V to 1.12 V - from 4x
margin on `VGSth_min` to 0.7x, i.e. a fabricated "spurious turn-on" finding. Put the driver's
output impedance INSIDE the subcircuit and share only the logic command.

**Measurement windows have to clear the edge.** "Highest VGS during the off period" measured from
0.4 ns after the falling edge reads the TAIL OF THE TURN-OFF EDGE, and it reads worse for the
larger gate resistor purely because that discharges more slowly (0.79 V at 6R8 vs 0.21 V at 4R7).
Moved to >= 13 ns after the edge - >= 8 gate-loop time constants - both read ~0.13 V. Always state
the window in time constants, not nanoseconds.

## 2026-08-08 [sim][classE][layout] A zone with no return plane is a 30 nH series element, and Class E notices
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`decisions.md` D4 correctly forbids In1/In2/B.Cu pour under the magnetics zone (a plane under a
spiral is a shorted turn). The uncosted consequence: the `TANK_A -> C_s bank -> TANK_B` run in that
zone has **no return image**, so its inductance is free-space partial self-inductance
(`(mu0.l/2pi)(ln(2l/w)+0.5)` ~ 23 nH for 40 mm of 8 mm strip), not microstrip `mu0.h/w`
(~0.1 nH/mm, which would be 4 nH). That is a **7x** modelling error on the same copper.

At 20 MHz the 30 nH is j3.77 ohm = **0.91 R** dropped into a network whose entire design reactance
is 1.283 R. Measured effect on the same deck, bridge in vs bridge out, everything else identical:
X_net/R 1.16 -> **2.00**, ZVS residual at conduction onset 7.2 V -> **15.6 V**, and output power
**121 W -> 53 W**. The board is DRC-clean, verify-clean and fab-ready with that in it.

Two transferable rules:

- **Any net that leaves a planed zone loses its microstrip inductance model at the zone boundary.**
  Compute partial self-inductance for the un-imaged segment, and hand the number to P8 as a
  parasitic the way pour capacitance already is (`route-notes.md` s7 does this for C, not for L).
- **Extra series L is cancelled by LESS series C, not more.** The intuitive move (add trim
  capacitors) makes it worse. Here the fix is depopulating two 56 pF sites from the C_s bank and
  populating one 27 pF trim site: 504 -> 419 pF, X_net/R back to 1.285.

## 2026-08-08 [P8][gate][waivers][PIPELINE BUG] `gate.py`'s DEFAULT waiver sidecar path is `<pcb-dir>/reports/`, not the workspace `reports/` - so a correct sidecar is silently ignored
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`gate.py` main():

    sidecar = (Path(args.input).parent / "reports" / "verify-waivers.json")

`args.input` for the verify gate is the BOARD, `boards/<b>/kicad/<b>.kicad_pcb`, so `.parent` is
`kicad/` and the default resolves to **`boards/<b>/kicad/reports/verify-waivers.json`** - a
directory that does not exist in any workspace in this repo. Every waiver sidecar the pipeline
has ever written lives at `boards/<b>/reports/verify-waivers.json` (lumina-par's included).

The failure is SILENT and it fails in the unsafe direction *for the reader*: the gate reports
`FAIL (31 failing)` with `waived` absent entirely, which looks exactly like "the owner never
signed these off" rather than "the file was not found". `gate.py` has a `workspace_dir()` helper
that computes `boards/<name>/` correctly - it is simply not used here.

**Always pass `--waivers <workspace>/reports/verify-waivers.json` explicitly.** Verified on
rf-de-20m: without the flag 31 failing / no `waived` key; with it, PASS 0 failing / 31 waived.

## 2026-08-08 [P8][pipeline][decoupling] `root.py` REWRITES `decoupling.json` from scratch - hand-added rail associations do not survive a schematic regen
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Same shape as the `.kicad_pro` netclass wipe already recorded at line 815, and it bit for the
same reason: `Project.save(..., decoupling=out_dir/"decoupling.json")` regenerates the whole
sidecar from the sheets' `place_ic_with_decoupling` calls, and that helper only ever emits
**IC-PIN bypass** entries.

On this board the `+5V` rail's decouplers are C108 (22 uF) and C109 (100 nF) hanging off L101.2,
the buck's OUTPUT node - `+5V` has no IC load pin at all, because U201 sits behind FB201 on
`+5V_DRV`. So they can only be added by hand, and the P8 verify pass did add them, with a `_note`
predicting exactly this. The 22:12 root regen deleted both, and `check_pdn` went straight back to
`power rail +5V (0.3 A) has no decoupling capacitors` - a 32nd verify ERROR on a board whose
copper had not changed.

**Check `kicad/decoupling.json` after ANY `gen/root.py` run**, the same way you check the
`.kicad_pro` for `net_settings`. Both are "the generator owns this file" traps and both are
invisible until a gate fires.

## 2026-08-08 [P8][drc][parity] A schematic FIELD-ONLY edit costs 15 `footprint_symbol_field_mismatch` findings until `board_update` syncs the board
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Editing only `Note` / `Variant` text in a generator - no value, no footprint, no net - produces a
netlist that is **electrically identical** (verified: 70 components, 20 nets, same values,
footprints and node sets) and therefore looks like a no-op. It is not, for `drc_routed`: KiCad
footprints carry their own copy of the schematic properties, and `gate.py --gate drc_routed`
folds in a `parity` source that compares them. The 15 refs whose `Note` changed became 15
`footprint_symbol_field_mismatch` findings and the residual went **55 -> 70**.

`board_update.py` classifies them as `swap_same_fp` with `value: null` and applies them as a
pure field sync: measured before/after on rf-de-20m, tracks 125, vias 230, zones 38, fills 70,
locks 70 and every footprint position **identical**, and `drc_routed` back to exactly 55.

**So: any generator edit, even a comment-only one, needs `kc.py netlist` + `board_update.py`
before the DRC baseline means anything.** The cheap tell is the `parity` source in
`gate-drc_routed.json` - if it is non-zero, the board's field copies are stale.

## 2026-08-09 [P9][gate][pipeline][PIPELINE BUG] `gate.py --gate dfm` looks for `parts.json` BESIDE THE BOARD, so the BOM-completeness leg silently never runs on this workspace
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
Exactly the same shape as the `verify-waivers.json` default-path bug already recorded above
(2026-08-08 `[P8][gate][waivers]`), and it deserves the same treatment because it is silent in the
same way. `gate.py:run_dfm` is:

    parts = board.parent / "parts.json"
    if parts.exists(): kwargs["parts"] = parts

i.e. `kicad/parts.json`. This board - like every ai-ee workspace - keeps its BOM of record at
`parts/parts.json`. So `dfm_check` runs with `parts=None`, `check_release` skips the
`missing_lcsc` leg entirely, and **the gate reports a clean `dfm` pass having never looked at the
BOM at all.** `reference/invalidation.yaml` has the same assumption baked in
(`parts: {path: "kicad/parts.json"}`), which is why the `parts` artifact hashes to null.

Nothing distinguishes "BOM complete" from "BOM never checked" in the gate JSON: `missing_lcsc`
is simply absent from `facts`. **Always follow the gate with an explicit run:**

    dfm_check.py --pcb ... --fab-dir fab/ --parts parts/parts.json --schematic ... --out ...

which also audits the SHIPPED package rather than a scratch re-export. On rf-de-20m both agree at
0 findings and the explicit run adds `missing_lcsc: []` over 68 lines.

## 2026-08-09 [P9][fab][tenting][gerber] Board-level via tenting does NOT protect a via that lands inside a PAD's mask opening - and the board file cannot tell you
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
The instruction carried into P9 three times was "the signal vias in the bottom heatsink land must
stay tented; do not enable via-tenting-off at plot time". Both halves of that are satisfiable and
were satisfied - `(tenting (front yes) (back yes))` at board level, **zero of the 230 vias carrying
a per-via `(tenting ...)` override**, and no tenting flag in `kicad-cli pcb export gerbers` (the
wrapper passes only `-o` and `--layers`). But that is not the whole question, because:

**KiCad's "tented" means it does not GENERATE a mask aperture for the via. It does not SUBTRACT the
via from someone else's aperture.** Any via that falls inside a pad's mask opening is bare in the
exported gerber no matter what the tenting setting says. Measured on rf-de-20m's export:

| | count |
|---|---|
| vias with a mask opening because of their own tenting setting | **0** |
| vias sitting inside an `F.Mask` PAD opening (via-in-pad) | **30** |
| of those, ALSO inside the `B.Mask` heatsink-land aperture -> open at BOTH ends | **17** |

All 17 are GND at 0.30 mm drill, so there is no electrical hazard here (the sink is at land
potential), but they are a solder wick path from a top joint to the heatsink mating face - which is
the exact failure mode HS-2 exists to prevent. Two of the owning pads (`C203.2`, `C205.2`, 4 vias
each) belong to **DNP** sites, so they carry unconstrained bare paste with nothing to hold it.

**How to actually prove tenting on a fab package: enumerate the mask gerber's openings and account
for every one.** Reading the board's tenting setting proves nothing about the shipped files.
rf-de-20m's `B.Mask` has exactly 11 openings - 3 for the HS1 land (1430.150 mm2, 100 % over B.Cu
copper), 4 M3 holes, 2 J101 THT pads, 2 M2 clamp holes - and not one is a via. That is the
statement worth making; "the board says tenting yes" is not.

Consequence for the order: **POFV (epoxy filled + capped) stays specified.** Its original rationale
(an LMG1020 via-in-pad, `stackup.md` s5) was retired by `review-board.md` s2.2 because that via was
never built - but the 17 measured here re-justify it, on flatness. Do not let a retired rationale
retire the option.

## 2026-08-09 [P9][gerber][drill][gerblib] `Hole.plated` is ALWAYS True on KiCad's merged drill export - do not use it as an NPTH oracle
Promoted from boards/rf-de-20m/LEARNINGS.md (promotion pass 2026-08-14).
`fab_export.py` calls `kc.export_drill(..., fmt="excellon")` with no `--excellon-separate-th`, so
KiCad 10.0.3 writes ONE `.drl` with `TF.FileFunction,MixedPlating,1,4` and carries plating per TOOL
as an X2 attribute comment:

    ; #@! TA.AperFunction,Plated,PTH,ViaDrill        T1C0.200 / T2C0.300
    ; #@! TA.AperFunction,NonPlated,NPTH,ComponentDrill   T5C2.200 / T6C3.200

`gerblib._holes` does `plated = getattr(obj, "plated", True)` and gerbonara does not surface the
attribute, so **all 326 holes on rf-de-20m read `plated=True`**, including the four M3 mounting
holes and the two M2 heatsink clamp holes that the board declares `np_thru_hole`.

Latent consequence: `dfm_check.check_annular_ring` gates on `if not h.plated: continue`, so an NPTH
hole is graded as if it needed an annular ring. It did not fire here only because the ring test
needs a PAD FLASH containing the hole centre, and a `size == drill` mounting-hole pad leaves no
copper to flash (pours are regions, not flashes). A mounting hole placed inside a real SMD pad would
produce a phantom annular-ring error with no way to see why. **The plating truth is in the drill
file's `TA.AperFunction` lines, not in the parsed hole objects** - and it is the one thing to
eyeball in JLC's viewer, since a mixed-plating file is where a fab can get NPTH wrong.

## 2026-08-14 [yaml][process][learnings] A multi-line PLAIN YAML scalar breaks on ": " and on a "- " line - hand-authored prose belongs in a block scalar
Authoring U6's 66-ruling promotion batch (~1000 lines of hand-written YAML) failed to parse with
`mapping values are not allowed here` at a line deep inside a `note:`. Cause: `note: <prose>`
continued across several lines is a PLAIN scalar, so a continuation line containing `entry: after
the P7 pour` re-enters mapping context, and a continuation line beginning `- ` is read as a block
sequence item. Neither is visible while writing sentences, and the parse error points at the
continuation, not at the key. The whole-file fix is mechanical: rewrite every `key: <text>` as
`key: >-` with the text moved to the next line at key indent + 2 (the continuations already sit
there). Companion to the same-day entry on comma-bearing FLOW values - the quoting rules bite in
both styles, so the standing rule for any YAML this skill writes by hand is: **prose goes in a
`>-` block scalar, never in a plain scalar.** Recorded in `reference/recipes/promote.md` for the
one file format that invites it (ruling batches).

## 2026-08-14 [tests][recipes][lint] The recipe-doc flag lint re-attributes flags to a script named as an ARGUMENT - put path-valued flags last on the line
`test_task_router.py::test_recipe_doc_commands_use_real_flags` (and its twin in
test_remediations.py) walks each line of a recipe doc and re-points "the flags that are legal
here" at the nearest preceding `<script>.py` token. So a documented command line like
`--reason "<why>" --targets scripts/check_current.py` is fine, but the same line written
`--targets scripts/check_current.py --reason "<why>"` FAILS: the path is an argument VALUE, and
the checker reads it as a new command, then checks `--reason` against check_current.py's flags.
Cost two red parametrised cases on a doc whose commands were correct. Two ways out and the doc
one is cheaper: put any flag whose value is a script path at the END of its line (or use a
`<paths>` placeholder). The checker could also skip a `.py` token that sits in argument position
(immediately after a `--flag`), which is the actual defect - the doc is not wrong.

## 2026-08-14 [git][process][waves] `git commit <pathspec>` RE-STAGES those paths - surgical index staging needs a bare `git commit`
The wave-parallel rule (entry 247) says commit via pathspec so another session's hunks in a shared
file are not swept in. That is not enough once BOTH sessions must edit the SAME file: U6 added a
task verb, which forces edits to `reference/tasks.yaml` and to `SKILL.md` (the registry test
requires the verb be named in the playbook) while U5 was live in both. Two mechanisms settle it:
1. Stage the content you want without touching the working tree -
   `git show HEAD:<path>` -> apply only your own edit to that text -> `git hash-object -w --path
   <path> <tmp>` -> `git update-index --add --cacheinfo 100644,<blob>,<path>`. `git apply --cached`
   also works but fuzzes on CRLF working copies; hash-object takes the exact bytes you built.
2. Then commit with **no pathspec at all**. `git commit -- <paths>` re-reads those paths from the
   WORKING TREE and overwrites the index entry you just crafted, so the surgery is silently undone
   and the other session's half-finished work lands in your commit.
Check before committing that your own hunk does not reference the other session's new files
(`grep attest.py` here): a shared registry that names a script only they have makes YOUR commit
internally inconsistent even though the working tree is green.

## 2026-08-14 [state][gates][process] `state.py record-gate` blessed a result file the gate never produced - chain the run and the record, and bind the digest
U5's reference-board migration hit this live: `gate.py` failed at IMPORT (a new lib module's
dual-context import bug), wrote NO result file, and the follow-up `record-gate --result
reports/gate-erc.json` happily recorded the OLD S14-era report - stamping it with FRESH input
hashes computed from disk. The state then claimed "erc pass, hash-fresh" for a gate that never
ran against the current tools. Two mechanisms now guard it: (1) gate.py results carry the
underlying report's stamped `input_digest`, and record-gate REFUSES a digest that does not match
the current primary input (legacy digestless results still record - the tooth grows as results
regenerate); (2) operationally, always `gate.py ... && state.py record-gate ...` - the && is
load-bearing, a separate record step after a failed run is exactly this trap.

## 2026-08-14 [placement][geometry][gates] Effective-courtyard BBOX flags tight-but-legal decouplers - the LQFP pad-field bbox covers its pad-free corners
Re-running the place gate on stm32-blinky (U5 migration) failed 3 courtyard_overlap errors
(C1/C2/C3 vs U1, 0.25-2.03 mm2) on a board that shipped DRC-clean and passed this gate at S14.
Not a board defect: `placelib._pad_box_local` returns the pad-field BOUNDING BOX (+0.25 mm), and
an LQFP's bbox covers the pad-free corner/interior regions; T6's per-pad-rotation fix made the
bbox truthful enough to reach the caps. Measured pad-to-pad gaps: 0.66-1.82 mm - all above the
S14 rule (>= 0.62 mm) and KiCad-DRC clean. The tension is structural: check_decoupling DEMANDS
caps within 2 mm of the IC while courtyard legality flagged exactly that placement. Fix shape,
not thresholds: pairwise overlap now tests the PRECISE shape (declared courtyard UNION of
per-pad boxes, `precise_extents_abs`) while `extents_abs` stays the conservative hull for
containment/edges/keepouts/packing - covering more is safe there, it is the false-positive
direction for part-vs-part overlap. The S14 shorting class (part ON pin tips) still overlaps the
per-pad boxes. Bench-neutral by construction: the annealer packs against the unchanged hull.

## 2026-08-14 [learnings][process][tests] `learnings.py resolve --targets` fed a key apply_ruling never reads - every single-entry promotion recorded no artifacts
U7's dry-run (stand-in critique -> classed edit) hit it: the single-entry CLI built its ruling
with `"targets": _csv(args.targets)`, but `learnlib.apply_ruling` reads `ruling["artifacts"]`
for the resolution - so the documented promote.md invocation silently dropped the artifact list
and the very next `learnings.py validate` failed the promotion with "no artifacts". The U6
acceptance never saw it because the rf-de pass went through `--batch`, and that hand-written
file carried BOTH keys redundantly. They are genuinely different fields: `artifacts` is the
resolution's written-to list, `targets` in a batch ruling REFRESHES the entry's candidate list
(apply_ruling line ~378). Fix: the CLI maps `--targets` -> `artifacts` (flag name unchanged,
it is documented everywhere); promote.md now states the two batch keys are different fields.
Regression: test_learn.py round-trips resolve -> validate on a fresh queue. Shape to reuse:
when a CLI flag and a schema field share a NAME but not a meaning, the acceptance test must
exercise the single-entry path too, not only the batch that a careful author wrote.

## 2026-08-14 [tests][checklib][sim] checklib stamps every payload with wall-clock `generated_at` - whole-payload equality asserts are racy across a second boundary
U7's full-suite run (13.5 min, parallel load) tripped test_grid_doubling_determinism in
test_layout_sim.py for the first time in its life: two back-to-back check_irdrop.run() calls
landed at ..T03:58:26 vs ..:27 and the byte-compare of the FULL payloads failed on nothing but
checklib's `generated_at` stamp (checklib.py ~57, applied to every check payload). The stamp is
load-bearing elsewhere (U2's gate-report validation bounds generation-time staleness), so the
fix belongs in the assert, not the stamp: pop `generated_at` from both payloads before
comparing. Rule for any determinism test over check payloads: byte-equality only AFTER
stripping the stamp - the flake window is one second wide, which is exactly wide enough to
pass hundreds of runs and then fail one you care about. Grep hint for the class:
`json.dumps(p1` beside `json.dumps(p2` in tests/.

## 2026-08-15 [yaml][knowledge][process] An unquoted `date: 2026-08-15` in hand-authored YAML is a datetime.date, and it killed the lint before the lint could name it
U14's backfill wrote `approval: {by: owner, date: 2026-08-15}` into all 16 knowledge records.
YAML 1.1 implicit typing loads that as a `datetime.date`, not a string: the schema's
`{"type": "string", "pattern": "^\d{4}-\d{2}-\d{2}$"}` would have caught it, but
`knowledge.py --validate` never got that far - `_load_yaml_checked`'s ASCII probe does
`json.dumps(data)` and a date object raises TypeError, which surfaced as
`status: error, exit 2` with no file name and no field. Two rules fall out. (1) Quote every
date in hand-authored YAML (same family as the 2026-08-14 comma-in-a-flow-scalar entry: YAML's
implicit typing is the hazard, quoting is the fix). (2) A validator that serializes its own
payload must treat non-JSON-native scalars as a NAMED problem, not an exception - the fix
catches TypeError there and reports "YAML implicit typing produced a non-JSON value ... quote
it", so it exits 1 with the file and the value instead of exit 2 with a traceback. Regression:
test_yaml_implicit_dates_are_a_lint_problem_not_a_crash.

## 2026-08-15 [knowledge][coverage][architecture] Coverage envelopes are tested ONLY against the block's own operating_point - board-level facts must be repeated per block
U14 gave buck-power-ground-isolation the envelope `board_layers: {min: 2, max: 4}` (ROHM's join
recipe is a 4-layer assignment) and buck-thermal-via `pdiss_w: {max: 5}`. Both are facts the
architect already knows - but `knowledgelib.coverage` reads dims from `blocks[].operating_point`
and NOWHERE else: the stackup in constraints.json, the layer count in the board setup and the
thermal entry's `power_w` are all invisible to it. A board that declares a 4-layer stackup and
omits `board_layers` from the block leaves the record `envelope-unknown` -> the class sits at
`provisional` and the slot never reaches `covered`, with no error anywhere. The report does name
the exact missing dim (`unknown_dims`), which is the only reason this is diagnosable. Until
coverage lends board-level dims automatically, the operating point must restate them: a buck
block needs all eight of vin_v, iout_a, pdiss_w, board_layers, switching_kind, rectifier_kind,
integration_kind, source_kind (now stated in agents/architect.md and constraints_schema.md).

## 2026-08-15 [knowledge][coverage][process] An envelope is what BOUNDS a rule, not what the rule mentions - and when nothing bounds it, that IS the principle ruling
The U14 approval session ruled level + envelope on all 16 records at once, and the same three
mistakes were available every time. (1) Numbers a record CARRIES are not bounds: ROHM quotes
1 mm/A at 1 oz and 0.7 mm/A at 2 oz, so copper weight is a parameter the record already spans -
its actual bound is hard switching (the edge content that makes enlarged SW copper an antenna).
The test is "where does this stop being TRUE?", never "what numbers appear in it". (2) The level
ruling and the envelope ruling are ONE decision, because the schema requires an envelope at
topology/family/part and forbids one at principle: a record with no honest bound (loop area x
di/dt; check the enable pin's own behaviour; what a block must emit into constraints.json) is a
principle, and inventing a filler envelope to keep it at topology is the failure mode the schema
exists to catch. 3 of 16 landed principle. (3) A checklist row must not demand a level its own
subject matter cannot reach - `sequencing` and `constraints-emission` had to drop to
`min_level: principle`, or their class could never be covered by the very records that own it.
Corollary for anyone writing records later (U15's researcher): prefer ONE dim, and only dims P2
can actually declare - every dim is a declaration the architect must make or the record sits
`provisional` forever (entry 295).

## 2026-08-15 [git][process][waves] The wave-parallel pathspec commit silently drops NEW files - `git commit -- <dir>` stages modifications only
U14 committed 25 of its 27 files. The two MISSING ones were the new interface coverage
checklists (`100base-tx.yaml`, `usb-fs.yaml`): `git commit -- <paths>` re-reads those paths from
the working tree, but only for files git already TRACKS - untracked ones are not "changes to
commit" and are skipped without a word, even when the pathspec is the directory that contains
them. The working tree stayed green, so nothing failed locally; a fresh clone would have failed
`test_committed_library_lint_green_and_checklists_present` (which asserts all three checklist
ids) with the tree that produced a passing check.cmd. Extends 247/289: the wave convention says
commit by pathspec so a parallel session's hunks are not swept in, and the price is that git's
"add" step is skipped too. Rule: when a session CREATES files, `git add` them explicitly first,
then commit by pathspec - and verify with `git show --stat HEAD | wc -l` against the file count
you expect, because the failure is silent in the direction that looks fine.

## 2026-08-15 [windows][process][tools] The Claude Code Bash tool collapses `\\` to `\` inside a QUOTED heredoc - write edit scripts with the Write tool, never `cat <<'EOF'`
U15 authored its file edits as small Python scripts and hit the same wall three times before
characterizing it: a `cat > x.py <<'EOF' ... EOF` heredoc through the session's Bash tool
delivers `\\Users` as `\Users` and `\\b` as `\b` (measured with `cat -A`; a real bash leaves a
single-quoted heredoc body byte-for-byte). Consequences, in the order they surfaced: (1) a
script whose body contained escaped sequences died before running with bash's `unexpected EOF
while looking for matching quote` (the collapse un-balances quotes); (2) a script that did run
carried `C:\Users` inside a normal Python string literal, which is a `SyntaxError: (unicode
error) truncated \UXXXXXXXX escape` on the FIRST use of a Windows path; (3) a regex written
as `'\\b'` would have silently become a backspace. Nothing in the tool output says the body was
altered - the failure appears one layer down, as a Python error about text you did not
write. Rule for sessions on this host: author any edit/one-off script with the Write tool
(bytes land verbatim), then run it with `.venv\Scripts\python.exe`; keep heredocs to plain
ASCII prose with no backslashes. Companion to the 2026-07-06 [windows] entry (MSYS paths in
`python -c` mixes) - the shell layer between the agent and Python is where Windows sessions
lose bytes, so keep it out of the path of anything that matters.

## 2026-08-15 [parts][sourcing][tools] Digi-Key SEARCH pages report price tiers that are wrong by 2x - always fetch the product DETAIL page, and key a BOM on MPN not DK part number
Building the pd-trigger hand-assembly BOM, the Digi-Key results/list page advertised the
Cal-Chip GMC31X5R106M50NT at "~$0.28 at qty 20" and the TDK CGA5L3X5R1H106M160AB at "~$0.25";
the product detail pages priced the same parts at $0.556 and $0.521 respectively at the qty-10
tier. The list page appears to surface a high-volume tier as if it were the small-qty price, so
a BOM costed from search results understates by ~2x on exactly the lines that dominate the
total. Rule: cost a line only from the detail page's full price-break table, and remember the
tier is "buy N or more" - 20 pcs pays the qty-10 rate, not a blended one.
Companion failure in the same area: two DK part numbers in the previous sheet were invalid
(`311-47.0KHCT-ND`, `311-1.50KHCT-ND` vs the -HR- forms), and re-querying by MPN showed DK
itself returns different CT part numbers for the same Yageo MPN depending on series/packaging
revision. DK bulk-add resolves manufacturer part numbers directly, so make MPN the primary key
of any distributor BOM and treat the DK PN as a convenience column - it is the field that
silently rots.

## 2026-08-15 [parts][sourcing] Two structural sourcing facts for CH224-class PD boards: WCH has no US distribution, and 1A-hold @ 30V does not exist in a 1206 PPTC
Both were re-verified against the distributors rather than inherited from the prior sheet.
(1) CH224K is carried by LCSC (and Amazon 10-packs) but by neither Digi-Key nor Mouser - WCH
has no US distributor at all. Every DK/Mouser PD-sink alternative is a different package and
pinout (STUSB4500 QFN-24, AP33772 QFN-14, HUSB238), so it is a schematic+layout respin, never a
drop-in. Any CH224-based board therefore forces a non-US order line no matter how the rest of
the BOM is sourced - design that in, or pick a Western PD sink up front if single-vendor
sourcing matters.
(2) A 1206 PPTC that holds 1A at 30V is not a rating Western makers offer: Littelfuse's 30V
1206L line tops out at 0.35A hold (1206L035/30) and Bel's 1A 1206 part (0ZCJ0100FF2E) is rated
6V. The Chinese BSMD1206-100-30V class claims both at once. This is package physics (fault
energy in a 3216 body), not a stocking gap - so it will not resolve by waiting or by checking
another Western distributor. Substituting the 0.35A part fits the footprint but silently
derates whatever the silk promises.

## 2026-08-15 [constraints][planes_gen][lint] `planes[]` entries reject unknown keys - including the `_note` convention used everywhere else - and `constraints_lint` does NOT catch it

Found at bb-buck P2 by the architect reading the CONSUMER before writing the producer, then
machine-verified. Two coupled facts:

(a) `planes_gen.py:242` raises `CheckError(f"planes[{i}]: unknown keys {sorted(unknown)}")`.
The `_note` key that every other constraints section carries as an inline-comment convention
is an unknown key HERE, so it hard-fails the plane build.

(b) `constraints_lint.py` passes such a file clean (0 errors, 0 warnings). Verified both ways:
`constraints_lint --file boards/sbuck-5v3a/architecture/constraints.json` exits 0, while that
same file's `planes[]` entries each carry a `_note`. So the lint is NOT a proxy for
planes_gen acceptance, and a P2 that lints green can still hard-fail at P5/P7.

Consequence worth knowing: **boards/sbuck-5v3a's shipped constraints.json would fail a
planes_gen re-run today.** It is a latent defect, not an active one - the board is built and
its zones exist - but any re-pour, resume, or copied-forward constraints file inherits it.

The workaround that held on bb-buck: put the plane rationale in a SIBLING top-level key
(outside `planes[]`), keeping the entries to the keys planes_gen accepts.

Second, independent `planes[]` trap from the same reading: `planes_gen.build_plan` REPLACES
the layer defaults entirely when `planes[]` is present - it does not merge. Declaring only an
F.Cu thermal island therefore leaves the board with NO bottom pour at all, taking out the
return path, the /SW reference and the radiator in one stroke. Declare every layer you want
poured, not just the one you are overriding.

## 2026-08-16 [routing][routelib][freerouting] routelib's score regex ate the sentence's full stop, so route_auto crashed ON SUCCESS - a greedy digit-or-dot class is the wrong score capture

Found live at bb-buck P6 (route probe, completion 1.00), fixed in
`[path masked]`.

`_PASS_RE`/`_SESSION_RE` captured the Freerouting score as `([\d.]+)`. When the router
finishes with nets still unrouted the line reads `...score of 1234.5 (7 unrouted)` and the
space terminates the greedy class, so it parses. When it finishes with **zero** unrouted
there is no parenthetical and the line ends in a full stop - `...score of 997.76.` - which
`[\d.]+` happily swallows, and `float("997.76.")` raises
`ValueError: could not convert string to float: '997.76.'`.

**So the parser failed only on the success path**, which is why three prior boards never hit
it: they all had unrouted nets at probe time. A clean board is the unusual case.

Fix is the score capture, not the suffix: `(\d+(?:\.\d+)?)`. A trailing sentence period is
not consumed because the second `.` is not followed by digits. Verified both ways (with and
without the ` (N unrouted)` suffix, pass line and session line).

Generalisable: when a vendor log's optional suffix is what terminates your greedy capture,
the capture is wrong even though every test you have passes. Prefer a shape-anchored pattern
over a character class whose terminator you do not control.

## 2026-08-16 [placement][constraints][gates] Two constraint mechanisms silently do not enforce: `keepouts` are board-LOCAL (never translated) and `separation` is centre-to-centre AND skipped when either ref is locked

Both found by hand at bb-buck P6 while cross-checking a passing `place` gate.

(a) `constraints.json` `placement.keepouts` rects are documented board-LOCAL and must be
translated by `reports/board_init.json.outline_bbox` before use. **Nothing does that
automatically.** The place gate's `keepouts` leg therefore compares placed parts against
rectangles sitting at absolute board origin - off in space - and passes trivially. The
placement agent enforced the translated rects (+26.07, +34.48) through a scratch working copy;
had it not, the M3 washer keepouts would have been unenforced on a board where they own the
corners.

(b) `placement.separation` reads as **centre-to-centre**, not courtyard-to-courtyard, and the
rule is **dropped entirely when either ref is locked**. On bb-buck all four declared pairs have
a locked member, so all four were unchecked. The FB-divider-to-L1 pair measures 3.84 mm
courtyard-to-courtyard against a declared 5 mm, while centre-to-centre is 13.14 mm and
"passes".

Consequence for any board: a green `place` gate does NOT mean keepouts and separations were
checked. Verify both by hand until these are fixed, and do not read the gate's silence as
evidence.

## 2026-08-16 [check_silk][geometry][gates] check_silk drew every circle as a FILLED DISC, ignoring `(fill no)` - so a stock KiCad ring footprint reports its own pad as 100% silk-covered

Found and FIXED at bb-buck P8. `check_silk.py::_shape_geom` returned
`Point(center).buffer(r + w2)` for any `*_circle` node, with no reference to the sibling
`(fill ...)` node. An UNFILLED circle is an annulus; buffering it as a disc fills in the
hole - and the hole is exactly where the pad lives.

Signature to recognise it by: **the reported overlap area equals the whole pad area.**
Here it was `silk circle on F.SilkS covers pad TP1.1 (1.77 mm2)`, and pi * 0.75^2 = 1.767 -
the pad, entire. A real silk-over-pad defect overlaps a *fraction* of the pad.

Real geometry of the stock `TestPoint:TestPoint_Pad_D1.5mm`: `fp_circle` r 0.95 with a 0.12
stroke = a ring spanning r 0.89-1.01 mm, against a 1.5 mm-diameter pad, r 0.75. That is
**0.14 mm of clearance** - the silk never touches the copper. The footprint is correct and
the finding was pure false positive.

Blast radius: every stock ring footprint - `TestPoint_Pad_*`, fiducials, polarity/pin-1
rings, many connector outlines. Any board using them has been eating phantom
`silk_over_pad` errors at the verify gate.

Fix: an `_is_filled(node)` helper reading `(fill yes|solid)` (absent = unfilled), and for an
unfilled circle return `buffer(r+w2).difference(buffer(r-w2))`. `rect`/`poly` are still
buffered solid - conservative, so it over-reports rather than misses, but it is the same
latent class if an unfilled box ever rings a pad.

The generalisable trap: a checker that renders geometry to test it must honour the SAME
fill semantics the fab does. Silkscreen is imaged from the stroke, not from the bounding
shape - and "buffer the outline" is not the same operation as "fill the outline".

## 2026-08-16 [state][gates][pipeline][PIPELINE BUG] A gate result that never reaches state.json is not evidence - bb-buck passed six gates and recorded none of them

bb-buck reached P9 with a complete fab package and six PASSING gate reports on disk while
`state.json` held 114 history events, ZERO gate events, `gates: {}`, `gates_passed: []`,
and a `resume` still naming P4 erc as the next gate. Nothing was wrong with the board - the
run had genuinely passed erc, place, drc_routed, verify, sim and dfm. What was missing was
the RECORD, and with it every derived guarantee: no recorded input hashes means no
freshness, no invalidation, no attestation, and a resumed session would have re-run the
whole pipeline.

The mechanism is the shape to recognise, not the instance: **the operation was two steps -
`gate.py ...` then `state.py record-gate ...` - and only the first was enforced.** SKILL
rule 3 said "record everything in state.json" while rule 4 printed the gate command WITHOUT
the record, so the orchestrator ran what rule 4 gave it at every phase and rule 3's prose
was never mechanically owed by anything. Same family as entry 290 (a record step separated
from its run recorded a file the gate never produced) and the same split codex C1 names for
phase-vs-certificate. A two-step protocol decays to its first step under any pressure.

Fixed (U16) by making the evidence a BY-PRODUCT of the action instead of a follow-up to it:
`gate.py` records the result itself into the workspace whose state.json sits above the input
(`--workspace` names it; auto-detection covers the agent and remediation call sites that
will never carry a flag), pass AND fail, before `--commit` so the state update rides in the
gate commit. A requested-but-failed record is exit 2, exactly like a requested-but-failed
commit. Second tooth: `state.py set-phase` REFUSES to advance past a gate phase whose gate
has no recorded result - bb-buck's walk would have stopped at P4 -> P5. `--force` exists and
logs `phase_forced` with the missing list.

What held the line anyway: U5's derived disposition read `draft` (unorderable), because it
derives from recorded gates rather than from phase. Defence in depth worked - but only at
the last gate before money, and only by refusing a board that had actually earned its pass.

Rule to carry: when a script's output is the ONLY evidence a later stage can use, producing
it and recording it must be ONE command. Any recipe sentence of the form "and then also run
X" is a defect report against the thing that precedes it.

## 2026-08-16 [geom][outline][board_edit] A corner radius read back from the outline POLYGON is biased by geom's own arc sampling - and an AREA comparison of "is this the shape I asked for" scales with r^2
Building board_edit (U17), which rewrites Edge.Cuts wholesale and must therefore READ the
shape it is replacing. Two measured traps in one afternoon:
(a) Deriving the corner radius from the area a rounded rectangle loses at its corners
(`bbox - face = r^2 (4 - pi)`) looks exact and is not: `geom._arc_points` samples each arc
as 16 chords, so the parsed polygon UNDERSTATES the quadrant and r=3.000 reads back as
3.009. Harmless once - except board_edit defaults `--corner-radius` to "keep what the board
has", so every re-edit would inflate the board's own corners (3.0 -> 3.009 -> 3.018).
The arcs' three DECLARED points give the radius exactly (circumcircle), so geom now exposes
`outline_arc_radii` and 4 equal arcs == a rounded rectangle.
(b) Verifying the applied outline by comparing AREAS fails at large radii: the sampling
error per corner is ~r^2 * n * (theta - sin theta), i.e. 0.03 mm2 at r=3 but 0.18 mm2 at
r=6, so any fixed area tolerance either passes a wrong shape or rejects a legitimately
rounded board. Use HAUSDORFF distance instead - the worst point-to-shape gap is the chord
sagitta (< 0.01 mm at any board radius) while a notch or chamfer deviates by its own
millimetre-scale size. Rule: when one side of a geometric comparison is sampled and the
other is analytic, compare DISTANCES, never areas.

## 2026-08-16 [geom][outline][board_edit] `.outline` cannot tell you what else is on Edge.Cuts - the parser returns on the first gr_rect, so a second one is invisible
Follow-on to the 2026-07-28 entry "an inner Edge.Cuts gr_rect silently BECOMES the board
outline". That entry fixed the PRODUCER (board_init refuses interior cutouts); the consumer
side is still blind, and it matters the moment anything REWRITES the layer: board_edit
deletes every Edge.Cuts board shape and redraws it, so a window, a chamfer or a second
board in the same file would vanish without a word - and `bg.outline` looks perfectly
normal in every one of those cases, because `_parse_outline` returns on the first gr_rect it
finds and never looks further. A polygonize-based faces list does not save you either: it
is only reached when there is no gr_rect at all. Fix: `BoardGeom.outline_items` counts the
Edge.Cuts primitives BY KIND before any early return, and board_edit refuses to apply
unless that inventory is one of the three board_init can produce ({gr_rect: 1},
{gr_line: 4}, {gr_line: 4, gr_arc: 4}) - `--replace-shape` is the explicit consent to lose
the rest. Cross-check after the edit: the worker's removed-item count must equal the
driver's count, or something else lived on that layer.

## 2026-08-16 [planes][zones][board_edit] A zone OUTLINE does not follow the board edge - growing the board leaves the pour at the old boundary, and a refill never expands it
Measured on the frozen pd-trigger route fixture: outline grown from 48x30 to 49.32x30.90
(9.18..58.50 in x), refilled through `kicad-cli pcb drc --refill-zones --save-board`, and
the B.Cu GND pour still spans 10.30..57.30 - unchanged to the micron. The zone's own
polygon was drawn by planes_gen against the OLD edge; refilling only re-clips the fill to
the zone outline intersected with the board, so it can lose copper but never gain any.
Consequences: (1) after any board GROWTH the plane must be regenerated (planes_gen, then
stitch_vias) or the board ships with a ring of bare FR4 no check complains about -
check_return_path and check_pdn reason about the pour that exists, not the one you meant;
(2) "refilled: true" in an edit report is NOT "the planes cover the board". Shrinking is
the safe direction: the fill clips back to the new edge automatically, which is why
board_edit deliberately does not run its copper-to-edge check against zone fills.

## 2026-08-16 [kicad][place_edit][geometry] Every scripted flip has been mirroring TOP_BOTTOM through a silent fallback - and the two directions are NOT interchangeable for an absolute-op writer
`place_swig.set_side` (copied into `update_swig.set_side`) read
`pcbnew.FLIP_DIRECTION_LEFTRIGHT` inside a `try`, with an `except AttributeError` fallback
commented "10.0.3 SWIG has no enum; bool = left/right". Both halves are wrong: KiCad 10.0.3
DOES export the enum, spelled `FLIP_DIRECTION_LEFT_RIGHT` (underscored, `dir(pcbnew)`
confirms), so the attribute never resolved and the fallback ALWAYS ran - and there
`fp.Flip(pos, True)` converts True to the enum's `1` = **TOP_BOTTOM**. Every flip the
pipeline has ever written mirrored the opposite way from the one the code names. Nothing
caught it because a top-bottom flip is a perfectly legal flip; it only surfaced (U19) when a
model-side mirror had to predict the file the worker would write.

Measured on usbbuck4 J1 (a USB micro at -90 deg, THT + duplicate "SH" pads), flipping about
the footprint's own position:
- **LEFT_RIGHT**: pads mirror in the BOARD x, orientation **unchanged** (-90 -> -90). The
  stored LOCAL frame therefore comes out as `R(a).M_x.R(-a).L` - it depends on the angle the
  part happened to have when the op ran.
- **TOP_BOTTOM**: local y negated, x intact, orientation **negated** (-90 -> +90), with no
  dependence on the starting angle.

So for a writer whose contract is "ops are ABSOLUTE and idempotent", only TOP_BOTTOM is
sound: with LEFT_RIGHT the same `{x, y, deg, side}` op applied to the same part at two
different starting angles produces two different geometries, because `SetOrientationDegrees`
runs after the flip and cannot undo an angle-dependent local mirror. Both workers now name
`FLIP_DIRECTION_TOP_BOTTOM` explicitly, and `placelib.Footprint.mirror` models exactly that
(mirror local y, leave the angle to the caller). Validated the whole way through: mirror the
model, emit `place` ops, run the SWIG worker, re-parse - every movable footprint of usbbuck4
agrees to 1e-3 mm on pad locals, absolute pad centres and courtyard bounds
(`test_model_mirror_matches_pcbnew_flip`). This also closes the 2026-07-11 entry's
"implemented but corpus-unvalidated" flag - and corrects its guess a second time: the mirror
is in **y**, not x.

## 2026-08-16 [placement][anneal][tests] place_anneal's fast test budget never reaches the cold regime - any assertion about WHICH optimum it lands in is noise
The suite's `FAST` params (25 moves/cluster, 12 epochs) leave `t_end` around **150** on a
board whose whole cost is ~100, i.e. the metropolis test still accepts nearly everything when
the run ends; the reported result is then just the best state the random walk happened to see
plus the short greedy quench. Measured on the 7-part scatter fixture: at FAST the same board
came out at cost 110 / 120 / 79 for seeds 1/2/3, while the default 140-epoch budget landed at
19.8 / 20.0 / 19.5 with `t_end` 0.03 - a 5x spread collapsing to 3%. A U19 acceptance test
("a board that does not need two sides stays single-sided") written against FAST looked like
a real failure and was not: at a converging budget the same board never flips.
Rule: FAST-budget tests may assert INVARIANTS (legality, determinism, incremental == full
sync, a term reacting in the right direction) but never WHICH placement wins. For an
outcome assertion, use a fixture small enough to converge in ~1.5 s (5 free clusters, 40
moves x 40 epochs) - or assert on the cost model directly, which is the thing the outcome is
supposed to follow. `t0`/`t_end` are already in the anneal report; read them before believing
a placement comparison.

## 2026-08-16 [build-modes][state][pipeline][PIPELINE BUG] A dimension given at a CHECKPOINT is invisible to every P0 lint - the tooth that makes geometry an output has to sit on board_init
The obvious place to catch "a stated size bound the design" is the requirements artifact, and
on bb-buck that place was CLEAN: section 5 reads "Outline: **no HARD cap.** Nothing here binds
permanently at P5 board_init", with 30-40 x 20-28 mm marked "Non-binding expectation for
planning only (NOT a cap)". P0 did its job. The 35 x 25 arrived TWO CHECKPOINTS LATER, from
the owner at H1, and entered the pipeline as a `board_init --outline 35x25` CLI argument -
which no artifact records, no lint reads and no reviewer sees. P2 had already derived 40 x 30
with a mechanism behind it ("the outline IS the radiator": R_ba 39/34/31 C/W at
875/1064/1200 mm2), and P6 then closed "OUTLINE IS FINAL AT 35 x 25 - measured, not estimated"
with 0.05 mm of slack on all four edges. Every stage was individually right.
So U18 puts the tooth where the number actually enters: `state.py mode` records the run's
binding as machine state, and `board_init` REFUSES `--outline WxH` when that binding makes
geometry an output (`--allow-fixed-outline` is the reported consent). The general rule: a
value that reaches the pipeline as a command-line argument can only be policed by the script
that takes it - checking the ARTIFACT catches the values that were written down, and the ones
that hurt are the ones that never were.

## 2026-08-16 [board_edit][placement][build-modes] `--outline fit` on a board that was placed to FIT cannot recover the size the layout wanted - it measures the squeeze, and reports a bigger board
Running the U18 canonical flow backwards over bb-buck: `board_edit --outline fit --margin 0.5`
on the finished 35 x 25 board returns **35.9 x 25.901** - it GREW. Not a bug: the courtyards
sit 0.05 mm from the edge, so content bbox + 2 x 0.5 mm margin is larger than the board, and
fit is doing exactly what it says. The lesson is what it CANNOT do: fit measures the placement
in front of it, and a placement that was optimized against a small outline has already spent
the difference. There is no shrink-to-fit that recovers 40 x 30 from a board squeezed into
35 x 25 - the information is gone, not compressed.
Consequence for the canonical binding: the order of operations is load-bearing, not stylistic.
`board_init --outline auto` (generous room) -> place -> fit is a measurement of what the
layout wanted; place-into-a-size -> fit is a measurement of the size. Same command, opposite
meanings. Any later "we can always shrink it afterwards" argument is answered by this number.

## 2026-08-19 [placement][anneal] `place_anneal` RE-DERIVES every satellite slot from `place_seed` - it cannot preserve a hand-placed decoupler, and on bb-adc the slots it re-derives FAIL the board's own declared cap distances
`_build_bodies` opens with `slots = place_seed.layout_satellites(model, c, warnings)`, so the SA
starts from the seed's satellite geometry, not the board's. The tell is in the report itself:
bb-adc's anneal on a hand-placed board printed `hpwl_input_mm 242.12` beside
`hpwl_start_mm 299.80` - a 58 mm gap that is entirely the re-slotting, before a single move ran.
Every candidate therefore carries the seed's satellite offsets no matter what the file said.
That is not merely lossy here, it is wrong: `place_seed`'s own slots put C3 (C1210, VREF
reservoir) **3.97 mm** from U1's REF pin against a declared 2.5 mm limit and C2 **2.44 mm**
against 2.0 - i.e. the annealer's starting state fails `gate --gate place`'s decoupler leg, and
its best candidate still did (C3 3.97 / C2 2.44) while the tap stub went 2.6 -> 7.18 mm and the
anti-alias cap 1.97 -> 12.01 mm from the ADC input. HPWL "improved" 0.7 %.
Consequences. (a) A `placement.groups` note like bb-adc's "place by explicit place_edit and LOCK
before place_anneal" is not a style preference - locking is the ONLY way a hand-placed satellite
survives stage 2, because a cluster is rigid but its internal slots are recomputed. (b) Read
`hpwl_input_mm` vs `hpwl_start_mm` first on every anneal report; if they differ, the SA is not
refining the placement you handed it. (c) With the analog + converter + reference clusters
locked, `movable_clusters` fell to 0 and the one candidate emitted moved only C1 - from beside
J2's +3V3/GND pins (2.8 mm) to 10 mm past its south end, hpwl 242 -> 260. Both runs were
rejected on structure; the hand placement also won on HPWL.

## 2026-08-20 [research][process][tools] `learnings.py compile` WRITES - smoke-testing a run-close verb straight at a real board mutated it
U21 wired the unverified-research sweep into `learnings.py compile` (the run-close step) and
smoke-tested it by pointing it at `boards/bb-amp`, the workspace whose six stalled records are
the whole motivating case. It worked - and wrote `learnings/queue.yaml` plus a state decision
and a `research_drafts_unverified` event into a committed board. `git checkout -- boards/bb-amp/`
reverted it cleanly, but nothing in the tooling stopped it or warned: the read-only board rule
(U15's note) is prose, and the run-close verbs mutate BY DESIGN - `compile` writes the queue,
`close` writes LEARNINGS + the task, `open` consumes budget. The read-only probes are the ones
safe to aim at a real board: `researchlib.draft_sweep`, `knowledgelib.coverage`, `research.py
status`. Rule for smoking a mutating verb: copy the workspace into the scratchpad first, or use
a synthetic tmp_path workspace, and run `git status -- boards/` after ANY script smoke - the
mutation is silent and a board's state.json is the one file a later run trusts absolutely.

## 2026-08-27 [linux][docker] The whole toolchain runs in one container built on the official KiCad image
`docker/Dockerfile` (FROM `kicad/kicad:10.0.5`, Debian 13) gives kicad-cli 10.0.5, the bundled python
3.13.5 WITH the SWIG pcbnew module at `/usr/bin/python3` (env.find_kicad_python resolves it as
`cli.parent/python3`), symbol+footprint libs under `/usr/share/kicad`, and libngspice at
`/usr/lib/x86_64-linux-gnu/libngspice.so.0` (NOT beside kicad-cli - pin it with AIEE_NGSPICE_DLL). No
10.0.3 image is published; 10.0.x share the file format. `check_env.py --full` is all-pass except the
known ipc-headless warn. `kicad-cli pcb render` works headless with NO display/xvfb (Mesa EGL in the
image). Freerouting 2.2.4 on Temurin 25 runs headless with the verified `--gui.enabled=false` flag set
(the "Couldn't get screen resolution" line is a warning, not a failure). KRT's prebuilt
`grid_router-linux-x86_64.so` (abi3) imports in the venv once copied to `rust_router/grid_router.so`.
Pins are env vars there (`AIEE_KICAD_CLI/JAVA/FREEROUTING_JAR/KRT_DIR/NGSPICE_DLL`); `tools/` unused.

## 2026-08-27 [linux][tests][fab] Fixture CSVs come out LF on a Linux checkout - byte-compare normalized
`bom_cpl` writes CRLF (csv module default, what JLC gets). `.gitattributes` (`* text=auto eol=lf`)
stores the shipped `fab/*.csv` as LF, so every FRESH checkout has LF; the Windows working tree still
held the CRLF bytes the writer had produced in place (git never re-checked them out), which is the only
reason `test_assembly`'s byte-identity asserts passed there. Compare with `\r\n`->`\n` normalized on
both sides (`_lf` helper); never make the writer platform-dependent.

## 2026-08-27 [linux][tests][bench] bench baselines are pinned to KiCad 10.0.3 - they fail on 10.0.5 by design
`bench.py` refuses to score against a baseline recorded on a different kicad-cli ("scores across
toolchains are not comparable; re-record the baselines deliberately"). In the 10.0.5 container every
`test_live_fixture_matches_baseline[...]` case fails for that reason alone; re-recording is an owner
decision (a deliberate `bench.py --freeze` pass), not something a Linux port does on the side.

## 2026-08-27 [linux][docker][skill] Unattended runs: `ai-ee-loop` + `docker/run-contract.md`
`claude -p "/ai-ee ..."` loads the skill in headless mode (verified: the front-door verbs and the
Linux venv path come back). `ai-ee-loop <board>` iterates fresh contexts (`--max-turns` cuts them),
resuming through `/ai-ee --resume` from state.json + `log/run-journal.md`; the contract delegates
H1-H4, forbids ordering, and defines DONE. Launch: `docker build -t hwde-run:latest -f
docker/Dockerfile .`, then run `hwde-loop <board>` in that image over the repo (docker/README.md).

## 2026-08-27 [linux][docker][research] st.com is unreachable from the run container - ST RMs/ANs cannot be acquired
Every fetch of an `st.com/resource/...` PDF fails here: `curl` over HTTP/2 dies with
`(92) stream 1 was not closed cleanly: INTERNAL_ERROR`, HTTP/1.1 with a browser UA hangs past 120 s,
and a researcher agent saw 0 bytes at 280 s. `lcsc.com` (allowlisted) mirrors ST **datasheets**
(DS12991 came through that way) but NOT reference manuals or application notes, so RM0454 / AN2606 /
AN5096 / AN4989 are simply unavailable in an unattended container run. Consequence for the pipeline:
a record whose load-bearing claim lives only in an ST RM/AN cannot be verified here and stays draft -
the right move is to change the DESIGN so it does not depend on the claim (g0-sense fitted a 10k BOOT0
pull-down instead of trusting a forum-sourced option-byte default), not to lower the evidence bar.
mouser.com is also bot-blocked (PerimeterX). Off-list RM mirrors exist (embedded.fel.cvut.cz,
aliyuncs) - allowlisting one is an owner decision, not a workaround a run may take.

## 2026-08-27 [research][skill] jlcpcb.com part attributes: use the per-part URL, not componentSearch
`https://jlcpcb.com/parts/componentSearch?searchTxt=<code>` renders 0 results when fetched headlessly
(client-side search). `https://jlcpcb.com/partdetail/<vendor-slug>/<LCSC>` works and carries the fields
that settle assembly questions - "Assembly Process" (SMT/THT) and which tiers (Economic / Standard) can
place the part. That is how g0-sense resolved whether JLC Economic PCBA can place a Type-C receptacle
with THT shield legs (C165948: Assembly Process SMT, Economic supported - so the legs are not a blocker).

## 2026-08-27 [librarian][easyeda2kicad][parts] lib_pull footprint_verified:false, deterministic: symbol's Footprint property drops a name suffix that the actual .kicad_mod keeps
g0-sense P3, pulling SHT40-AD1B-R2 (C2909890, DFN-4-EP 1.5x1.5). `lib_pull.py --parts ...` (batch)
and a solo `--lcsc C2909890 --overwrite` retry both ended with the SAME error, byte-for-byte:
`symbol 'SHT40-AD1B-R2' names footprint 'aiee:DFN-4_L1.5-W1.5-P0.8' but no such file exists in
aiee.pretty | log: .8-TL-EP.kicad_mod` - i.e. easyeda2kicad wrote the real footprint file as
`DFN-4_L1.5-W1.5-P0.8-TL-EP.kicad_mod` (TL-EP = thermal-land/exposed-pad suffix it appends for
parts with a center pad) but stamped the symbol's `(property "Footprint" "aiee:DFN-4_L1.5-W1.5-P0.8")`
without that suffix - a genuine generator-side name-derivation mismatch between its symbol writer and
footprint writer, not a rate-limit/network flake (retry does not fix it; it is 100% reproducible for
this part). Net effect: the symbol-to-footprint link is broken even though both halves individually
exist and load fine (`--verify-load` passes for the .kicad_mod on its own; `fp_verify.py --footprint
<the real path>.kicad_mod` runs fine too - only the symbol's Footprint *property string* is wrong).
`lib_pull.py`'s own footprint_verified check IS what catches this - do not ignore an `"error"` entry
in the per-part results just because `footprint`/`symbol` blocks look present elsewhere in the JSON.
The fix (one line: correct the Footprint property value in the .kicad_sym to the real filename) is a
SYMBOL edit, not a footprint geometry edit - the librarian role's "don't hand-edit footprints without
approval" rule is about pad/silk/courtyard geometry, but this fix was still left for the orchestrator
since no established repair pattern for this class of bug existed in LEARNINGS.md yet. Likely trigger:
any pulled part whose footprint name gets a generator-appended suffix (EP/TL-EP/multi-land-class
disambiguators) is a candidate to re-check the same way.

## 2026-08-27 [librarian][easyeda2kicad][polarity][parts] Determining polarized-2-pin-part anode/cathode when the pulled symbol only has generic "1"/"2" pins: re-fetch the EasyEDA source, don't trust pad numbering
g0-sense P3, C3 = CA45-A010K226T tantalum cap (C122643). Orchestrator required settling anode/cathode
from the datasheet, explicitly forbidding inferring it from pad numbering alone (a prior board's
LEARNINGS entry, "RVT V-chip electrolytic CHAMFERED corners mark the ANODE," already burned that
shortcut once). The manufacturer spec PDF (fetched from the LCSC-hosted URL in parts.json) gives the
PHYSICAL marking convention (Sec.9 "Marking": dark/copper case stripe = positive-electrode marking)
but, being a generic case-level spec sheet, has no pin-number table - 2-terminal chip caps don't carry
manufacturer-assigned pin numbers, "1"/"2" is purely an easyeda2kicad library-generation artifact.
The resolution: re-fetch the RAW EasyEDA CAD source directly (`EasyedaApi().get_cad_data_of_component
(lcsc_id=...)`, the same call `lib_pull.py` makes) and read `dataStr.shape` - the schematic symbol's
own polarity "+" graphic survives as two short `PL~` (polyline) primitives forming a plus-sign, at a
position that sits unambiguously on one pin's side (here, next to the pin-1 plate). This is PART-
SPECIFIC hard evidence, not a generic convention - and it independently matches what's already sitting
un-labeled in the pulled `.kicad_sym` (the "+" polylines survive the symbol conversion; only their
semantic tag as "this is a plus sign" is lost, so `grep`-ing the .kicad_sym for a literal "+" property
finds nothing - you have to look at the raw polyline geometry). Cross-check the SAME conclusion two
ways before trusting it with hardware safety on the line.

## 2026-08-27 [librarian][connector][parts] Settling connector pin-1 physical location: KiCad's own system-library footprint for the identical part number is strong corroboration, but fetch the manufacturer drawing too
g0-sense P3, J2 = JST SM04B-SRSS-TB (Qwiic 4P, C160404). Two independent checks, both confirming the
pulled easyeda2kicad footprint's pad "1" (leftmost of the 4 signal pads) was correct, not flipped:
(1) `/usr/share/kicad/footprints/Connector_JST.pretty/JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal
.kicad_mod` - KiCad's OWN system library ships a footprint for this exact MPN, its `descr` cites JST's
own `eSH.pdf` datasheet URL, and its pad-1-through-4 geometry matches the pulled footprint almost
exactly (same left-to-right order, same offset mounting-tab row). (2) The official datasheet itself is
directly fetchable: `curl http://www.jst-mfg.com/product/pdf/eng/eSH.pdf` (no lcsc.com redirect
needed, jst-mfg.com is un-gated) - page 1 "PC board layout and Assembly layout / Side entry type"
explicitly states "the PC board layout figure shown is viewed from the connector mounting surface"
(i.e. the same top-down view as a KiCad footprint) and labels "No. 1 circuit" at the leftmost pad.
Three independent sources (pulled footprint, KiCad system lib, manufacturer PDF) agreeing is what
"settled, not guessed" looks like for a connector pin-1 call.

## 2026-08-27 [kicad-sch-api][schlib][schematic][bom] Native KiCad `dnp` from a generator is now `Sheet.mark_dnp()` - and the saved-file patch MUST run last, after `write_placements`
kicad-sch-api 0.5.6 exposes no writable `dnp` field on a schematic symbol: its writer hard-codes
`(dnp no)` on every instance it emits (the 2026-08-07 entry recorded this as "DNP is unreachable from
a generator"; g0-sense P4 needed it, because DNP carried only in a custom `Variant` field lets a
native `kicad-cli bom`/POS export emit THT headers as populate lines into an SMT-only JLC order).
`schlib.py` now carries `Sheet.mark_dnp(ref)` + `apply_native_dnp(refs, path)`, a saved-file text
repair in the same shape as the existing `apply_pin_number_fixups` (nearest preceding `(dnp no)`
before that instance's own `(property "Reference" "<ref>" ...)`; raises on a missing or non-unique
reference rather than guessing). It is a no-op when nothing is marked, so other boards are unaffected.
THE ORDERING IS THE TRAP, and it cost one failed attempt: `Sheet.save()` calls
`schem_refdes.write_placements()` for field placement, and that function round-trips the whole file
back through `ksa.Schematic.load().save()` whenever ANY field actually moved - which silently
re-hardcodes `(dnp no)` and erases a dnp patch applied earlier in `save()`. Measured: with the call
placed before `_place_fields`, J3/J4 came back `(dnp no)` with no error anywhere. Any future
saved-file repair of a field ksa also writes has the same hazard - apply it AFTER the last ksa
round-trip in the save path, and grep the written file to prove it stuck.
Corollary for the BOM side: `bom_cpl.py` resolves assembly class parts.json-first
(`refdes_class` > line `assembly_class` > the board footprint's own `dnp` attr > `smt_placed`), and
`refdes_class` is keyed by refdes independently of whether the line carries a `refs` list - so a
hand-fitted THT header is `refdes_class: {"J3": "hand_install"}` on the part line, and the native
`dnp` attribute is belt-and-braces for third-party exporters, not the mechanism this pipeline reads.

## 2026-08-27 [board_init][kicad][parity] KiCad's `unconnected-(...)` pseudo-nets: a FLAT schematic wants them on the pads, a HIERARCHICAL one refuses them - so board_init measures instead of guessing
g0-sense P5. `kicad-cli sch export netlist` invents a net named `unconnected-(REF-PIN-PadN)` for
every pin the schematic leaves unconnected, and board_swig faithfully assigned it to the pad. On
g0-sense (hierarchical: root + `/power/` + `/main/`) that produced 18 parity warnings, one per
deliberately-NC pad of J1/J2/U2: `net_conflict` "No corresponding pin found in schematic". Dropping
those pseudo-nets wholesale took g0-sense parity 20 -> 0 - and broke `tests/golden/usbbuck4` (FLAT,
sheetpath "/"), which then reported the OPPOSITE message on its 31 NC pads: "Pad missing net given
by schematic". Two boards, contradictory requirements, same KiCad 10.0.5.
What is NOT the discriminator, measured: `pintype`. Both boards spell these pins `*+no_connect`
(g0-sense 19 x `passive+no_connect`; usbbuck4 1 x `passive+no_connect` + 30 x
`bidirectional+no_connect`), so an explicit-NC-flag rule is wrong. Prefixing the component's sheet
path onto the pseudo-net name (`unconnected-(/main/U2-PA0-Pad7)`) does NOT fix the hierarchical case
either - still 18. The only structural difference left is flat vs hierarchical, and the underlying
KiCad reason is not established here.
Resolution shipped: do not encode a guessed rule. `board_init` builds WITH the pseudo-nets, runs the
real parity checker, and only if EVERY parity violation is `net_conflict` + "No corresponding pin
found in schematic" + a pad whose net literally contains `unconnected-` does it re-run the worker
with `job["skip_unconnected_nets"]` and re-check, reporting `unconnected_nets_skipped: true`. The
`unconnected-` guard is what stops a genuine pad-not-in-symbol defect from being retried away. Cost
is one extra DRC pass on affected boards; the payoff is that it is correct on both shapes without
anyone having to know which shape they have.

## 2026-08-27 [board_init][kicad][bom] A symbol's native `dnp` must be MIRRORED onto the footprint or parity fails
Same P5. Once J3/J4 carried KiCad's native `(dnp yes)` in the schematic, `drc --schematic-parity`
reported `footprint_symbol_mismatch` "Footprint attributes don't match symbol: 'Do not populate'
settings differ" - board_swig copies symbol FIELDS onto footprints but never copied attributes. The
netlist exposes it as a valueless `(property (name "dnp"))` on the comp (not a `(fields ...)` entry,
so `_comp_fields` never sees it). board_init now parses that property and board_swig ORs `FP_DNP`
onto the footprint. Set ONLY FP_DNP: touching `exclude_from_bom` here trades one parity mismatch for
another (LEARNINGS 2026-07-29 says the same for mounting holes - fix at the symbol, and let the
footprint mirror it).

## 2026-08-27 [librarian][footprint][easyeda] An easyeda2kicad custom pad's outline STROKE inflates effective copper - measure with GetEffectivePolygon, not the vertex list
g0-sense P5, J1 = TYPE-C-31-M-12 (C165948). DRC found the ganged GND and VBUS pads 0.100 mm apart
against a 0.127 mm fab floor. The polygon vertices were the vendor's own 0.60 mm width (HRO drawing,
"RECOMMEND P.C.B LAYOUT", callout `4-0.60`, 0.8 mm pitch -> 0.20 mm gap), but the converted
`gr_poly` carried a `(width 0.1)` outline stroke, and KiCad grows the copper by half that on every
edge: 0.60 -> 0.70 mm effective, gap 0.20 -> 0.10 mm. Reading the vertex list would have said the
footprint matched the datasheet; `pcbnew`'s `GetEffectivePolygon` is what tells the truth. The fix
is to shrink the VERTICES until effective width equals the vendor number (here -0.05 mm per side),
not to delete the stroke. Check this on every pulled custom-pad footprint whose pads look
suspiciously fat, and remember that fp_verify cannot catch it without a datasheet-extract JSON -
J1 had none, so nothing checked this footprint until board_init's own DRC did.

## 2026-08-27 [place_edit][swig][silk] Footprint-internal silk on a PLACED board is now `{"op": "silk_clear"}` - and two SWIG traps come with it
g0-sense P6. A dense 35 x 28 mm layout finished with 13 silk DRC warnings, 12 of them
footprint-INTERNAL graphics (a 0603's body outline wedged between two ICs; a flush edge
connector's mouth-end outline hanging off the board). `drc_routed` fails at errors+warnings = 0,
so they had to go - but the librarian fixes the LIBRARY, and a library edit cannot reach a board
that is already placed without re-running `board_init` and losing the placement. Nothing in the
pipeline could edit footprint graphics on a board. `place_edit` now carries
`{"op": "silk_clear", "ref": R, ["layer": "F.SilkS"], ["only_offboard": true]}`: deletes a
footprint's own graphic silk (never its Reference/Value text - those are `move_text`'s), and
`only_offboard` keeps the items whose bbox is inside the board outline, which is exactly the
flush-connector case.
TRAP 1, SWIG GARBAGE COLLECTION. `fp.Remove(item)` inside a FUNCTION corrupts the board when the
function returns: the next `board.FindFootprintByReference()` comes back as a bare `SwigPyObject`
with no FOOTPRINT methods. It is the wrappers going out of scope, not ownership - `thisown` is
False on the footprint before AND after, and setting `item.thisown = 1` changes nothing. The same
code inlined at module scope works, which is what makes this so easy to mis-diagnose. Fix: hold
every touched FOOTPRINT and PCB_SHAPE wrapper in a module-level `_KEEPALIVE` list for the life of
the (short, single-job) worker process. Related: `board.GetBoardEdgesBoundingBox()` SEGFAULTS once
any footprint has had an item removed, so cache it as plain ints BEFORE the first removal.
TRAP 2, THE LEAK LINES ARE ON STDOUT. After any detach, KiCad's SWIG runtime prints
`swig/python detected a memory leak of type 'PCB_SHAPE *', no destructor found.` at interpreter
shutdown - i.e. AFTER the worker's result JSON. `place_edit` was reading `stdout.splitlines()[-1]`
and reported a clean run as "worker exit 0 (rolled back)". It now scans BACKWARDS for the last
parseable JSON object, the same shape `board_init._last_json` already used. Any bundled-python
worker that mutates the board needs that parser.
TRAP 3, LIBRARY PARITY. Clearing silk on the board alone trades 12 warnings for 2
`lib_footprint_mismatch` ("Footprint 'C0603' does not match copy in library 'aiee'") - also
warnings, so also fatal to drc_routed. Whatever you clear on the board must be deleted from
lib/<lib>.pretty/<fp>.kicad_mod too, AND cleared on every other board instance of that footprint.
Budget the fix as "one footprint, all its instances, plus the library", never "one refdes".

## 2026-08-27 [silk_place][gates] `silk_place --apply --verify-drc` APPLIES FIRST and reports the regression afterwards - it does not roll back, and its model cannot see text-over-pad
g0-sense P6, after the board was already silk-clean (0 non-unconnected DRC). Re-running
`silk_place.py --pcb <board> --apply --verify-drc` proposed and APPLIED 8 refdes moves, one of
which put D1's Reference field back at (27.76, 38.3) - exactly the spot a fixer had just moved it
out of - re-creating `silk_over_copper` "Silkscreen clipped by solder mask" against J1's VBUS pad.
The report is honest about it (`status: violations`, `drc_silk_total: 1`,
`kind: silk_drc_regression`) but `applied` is ALSO true: the DRC runs POST-apply and nothing
restores. Two consequences. (1) Never point `--apply` at a board you have already cleaned by hand
without a snapshot, or better, run it on a COPY first and diff the DRC - the solver optimises
`beyond_extent_mm` (label near its part) and has no term for a label sitting over another
footprint's pad mask, so on a dense board it will happily trade a real DRC violation for a
cosmetic gain (median_beyond_extent_mm was 0.85 before AND after here - it gained nothing).
(2) A `<board dir>/silk_ops.json` left on disk is a PROPOSAL, not a record of what was applied;
after a hand fix it can be actively regressive. g0-sense deletes it at P6 close rather than leave
a file whose contents, if replayed, would undo a verified fix. Related: `state.py snapshot` only
covers the files you name (the `.kicad_pcb`), so a restore silently desyncs companion files like
this one.

## 2026-08-27 [check_current][gates][planes_gen] `pour_neck` runs PER ZONE FILL, so a pour tiled across abutting same-net zones reports its own tiling seam as a neck
`check_current.py` erodes each zone fill in isolation (check_current.py:283) and reports a
neck when the fill's attached vias split. But `planes_gen.assign_priorities` deliberately
tiles one electrical pour across several abutting same-net zones at distinct priorities, so
the seam between two abutting fills reads as a 0.10 mm neck even where the copper is
continuous and wide. g0-sense P8: VBUS reported "necks to ~0.10 mm" against a 0.800 mm
IPC-2152 requirement; the physical union of the same-net fills plus the connector pad copper,
eroded by 0.4 mm (the full 0.8 mm test at the conservative 1.5 A basis), left EVERY J1 VBUS
pad connected to all three of its vias. Real minimum in that copper was 0.680 mm, found by
bisection, and the feature it gates is a 0.33 mm2 dead lobe holding no via and no pad.
HOW TO TELL THEM APART without trusting either the checker or the fixer: union the same-net
fills for the layer (shapely `unary_union` over `GetFilledPolysList(layer)` outlines), add the
relevant pad polygons (`pad.GetEffectivePolygon(layer)` - it REQUIRES the layer argument on
10.0.5), erode by required/2, and ask whether each source pad still reaches its vias. That is
the question IPC-2152 actually asks; the per-zone test is a proxy for it.
Two related traps measured the same session: (a) nothing in the toolchain can delete or
reshape an EXISTING zone - `planes_gen` only adds, and skips any region already >=80% covered
by same-net fill - so the `pour_neckdown` remediation ladder's "grow the zone polygon" /
"second lobe" rungs are unreachable on an already-poured board; an annex zone at a distinct
priority is the only lever. (b) An annex zone added with planes_gen's DEFAULT connect mode
produced a `starved_thermal` DRC error (J2 pad 2 dropped to 1 spoke, min 2) and had to be
rolled back and re-added with `connect: "solid"` - default a power-spreader lobe to solid.

## 2026-08-27 [check_thermal][gates] the theta_JA model credits only same-net TOP copper - no backside-pour credit, so it is ~40-60 C/W pessimistic in the 100-200 mm2 band
`check_thermal.py:47,55` uses theta_JA(A) = 55 + (174-55)*exp(-A/350) for 1 oz / 2-layer.
Checked against the vendor tables that the ai-ee knowledge base already carries for the same
package (record `ldo-sot223-thermal-copper-sets-current`): the model returns 144 C/W where
AMS1117 datasheet p5 Table 1 MEASURED 80 (100 mm2 top + backside pour), and 123 C/W where TI
LM1117 Table 9-2 measured 84 (0.3 in2). It converges with the vendors only past ~600 mm2.
The gap is backside copper: the model counts only the pour on the dissipator's own net, while
the AMS row credits a 2500 mm2 back pour, and the knowledge record states explicitly that the
spreading layer NEED NOT BE ELECTRICALLY CONNECTED. On a 2-layer board with a solid GND pour
under the regulator the model therefore understates reality badly. g0-sense P8 waived it with
both vendor rows plus a measured proof of unreachability: satisfying the model needed 446.4
mm2 of credited copper, and assigning EVERY free mm2 of front copper on the whole board to the
rail yields only 439.9 mm2 inside the checker's 14.33 mm reach disc. When this fires, compute
the vendor-anchored Tj before spending copper - and note that the fix that would satisfy it on
2 layers (a backside island + thermal vias) carves the ground return plane, trading a passing
check_return_path for margin the measured data may say is not needed.

## 2026-08-27 [silk][drc][place_edit] KiCad's `silk_overlap` fires against a footprint's OWN reference text vs its OWN body-outline graphic
Moving a refdes with `place_edit move_text` must treat the part's own F.SilkS body
outline as a keepout, not just its courtyard and its neighbours. g0-sense P9: D1's
reference was moved off its neighbour and onto D1's own silk outline, producing a real
DRC error where there had been a warning; it was reverted. The own-part exemption people
carry in their heads (a part's own courtyard does not collide with its own label) does
NOT extend to silk graphics. Practical consequence for dense boards: a refdes that
check_silk calls "misattributed" may have NO legal destination at all - g0-sense finished
with 4 residual misattributions after an exhaustive nearest-legal-spot search, because
J1's connector pads extend past its drawn courtyard and C10 is boxed in by four parts.
Related measurement worth reusing: 0.1 in header per-pin labels need ~1.70 mm of clear
run in any orientation at a 1.0 mm / 0.15 mm silk floor, so a <= 1.34 mm inter-part gap
cannot hold one - label pin 1 and pin 4 and put the full pinout in fab/README instead.
Script gap proposed by the fixer: `place_edit --suggest-text-spot` (pads + silk graphics
as keepouts, own courtyard exempt, edge clearance, check_silk's own_off/nearest_other
formula) - it hand-built that search this session.

## 2026-08-27 [attest][p10][linux] `attest.py build` crashes on a RELATIVE --workspace when a waiver sidecar exists
`attest.py build --workspace boards/<b>` exits 2 with
`ValueError: '/workspace/boards/<b>/reports/verify-waivers.json' is not in the subpath of
'boards/<b>'`: the waiver path is resolved to absolute while the workspace stays relative,
so the containment check compares an absolute path against a relative one. Every other
script in the pipeline takes a relative --workspace happily, so this is easy to trip after
a whole run of relative paths. WORKAROUND: pass the workspace absolute -
`--workspace /workspace/boards/<b>` - which succeeds immediately with the identical inputs.
Only boards carrying a `reports/verify-waivers.json` hit it; a waiver-free board builds
either way, which is why it survived earlier runs. Fix belongs in attest.py: resolve the
workspace before the containment test.

## 2026-08-27 [attest][p10] the attestation refuses on STALE ARTIFACT MARKS, not just gate freshness - `state.py rehash` is the intended clear
`attest.py build` listed five blockers of the form "artifact bom: carries 1 stale mark(s) -
regenerate it first" alongside the expected "gate place: passed but not fresh". The marks
came from a P4 `state.py edit --class swap_part_same_fp` and had been carried, correctly,
all the way to P10 - the derived artifacts HAD since been regenerated (fab_export + bom_cpl
at P9), but regenerating a file does not clear the mark that says it was invalidated.
`state.py rehash --workspace <ws> --names bom bom_full cpl gerbers netlist` re-hashes them
from disk and drops the marks. Do this only AFTER genuinely regenerating each artifact -
rehash is a "I have rebuilt these, re-measure them" statement, not a way to silence a
blocker. Also note `report_gen.py` looks for `reports/bom_cpl.json`, `reports/fab_export.json`
and `reports/verify_all.json` specifically, so writing those reports only into `fab/` leaves
report_gen at exit 1 with `missing`/`warnings` entries.

## 2026-08-27 [dfm][gates][sidecars] a missing `parts.json` beside the board makes the dfm gate PASS with its bom leg silently skipped
The SKILL's sidecar rule (constraints/decoupling/parts/schematic resolve from the BOARD's
directory, kept beside it from P5) is not decorative: g0-sense had parts.json only at
`parts/parts.json`, so the recorded dfm gate ran 7 of 8 legs with
`skipped_error {'bom': 'no parts.json'}` and still reported **PASS** - a skipped leg is not
a failed one. An attestation was then built on that result. Nothing in the gate path flags
it; what caught it was `state.py freshness` reporting the registered artifact `parts`
(path `kicad/parts.json`) as `exists=False`. CHECK THE ARTIFACT SWEEP, not just the gate
verdict, and read `coverage.skipped_error` on every gate result you record.
Established convention (verified by diff on bb-amp / bb-buck / bb-mcu): parts.json exists
BOTH at `parts/parts.json` (source) and `kicad/parts.json` (sidecar beside the board).
Two adjacent traps found the same session: (a) `gate.py` resolves the parts sidecar from the
board directory but bare `dfm_check.py` does NOT - without an explicit `--parts` a
hand-generated `--strict` release report drops the bom leg, then `attest.py` refuses it with
"--report refused: status 'error' is not a completed run"; (b) the attestation's input
binding DOES catch the repair - adding parts.json invalidated rev 1 with "input parts:
changed since attestation (recorded 'None')", which is the machinery working correctly.
## 2026-08-27 [kicad-sch-api][kicad][linux] KiCad-10 stock libs carry `(property private ...)` - ksa 0.5.6 mangles it and kicad-cli refuses the schematic
The 10.0.5 symbol libraries (format 20251024) write KLC notes as `(property private "KLC_S3.3"
"text" ...)` - a bare `private` flag BEFORE the name (419 of them; Device:Crystal_GND24 is one).
kicad-sch-api 0.5.6 reads the flag as the name and the name as the value and emits the note as bare
atoms: `(property "private" "KLC_S3.3" The rectangle is not ...`. kicad-cli 10.0.5 then fails the
WHOLE file with the only message it gives, "Failed to load schematic" (exit 3) - bisect by splicing
lib_symbols entries one at a time into a known-good file. `schem_refdes.strip_private_properties`
removes each balanced block after every ksa save (schlib.Sheet.save, write_placements); the notes
have no electrical meaning. Version-bumping the schematic header does NOT help. The 10.0.3 Windows
libs predate the token, which is why S7 never saw it.

## 2026-08-27 [swig][freerouting][linux] SWIG Specctra export/import needs an X display on Linux - Xvfb :99
`route_swig` (export_dsn, dedup_copper, import_ses) under the bundled python fails on Linux with
"Unable to access the X Display, is $DISPLAY set properly?" - the pcbnew Specctra path is
wx-backed. Plain LoadBoard/SaveBoard (check_env's round trip) and `kicad-cli pcb render` need no
display. The container starts `Xvfb :99` (docker/project-init) and bakes `DISPLAY=:99`;
`env.py` also exports it to children when the caller has no DISPLAY and the :99 socket exists.
kicad-cli has no `pcb export specctra` subcommand (checked), so SWIG stays the only DSN path.

## 2026-08-27 [kicad][drc][erc][linux] KiCad 10.0.5 vs 10.0.3 deltas seen through the suite (fixture-recorded numbers move)
Same boards, same rules, different kicad-cli: (1) `hole_clearance` errors between a footprint's OWN
pads and its NPTH holes (USB-C receptacle: 0.18-0.21 mm vs the 0.25 mm rule) - 4 on the pd_trigger
stage fixture, so every board_update DRC==0 assert fails; expect it on any USB-C J1 at drc_routed
and judge it against the vendor geometry + jlc_capabilities, do not move pads; (2) the pristine
easyeda footprint fixture DRCs 18, not 16, and a second field-placement pass moves 1 field
(test_lib_hygiene); (3) the blinky2 regen carries one 0.0254 mm wire stub (unconnected_wire_endpoint
warning) against the 10.0.5 Crystal_GND24 geometry while the committed golden ERCs clean. None of
these are Linux defects; the assert numbers are 10.0.3 recordings. Re-baseline deliberately or keep
10.0.3 as the scoring toolchain - the bench refusal (row 317) is the same decision.
## 2026-09-01 [state][order_submit][lock][snapshot][U12] Two writers, one file: the old `<name>.tmp` + os.replace pattern is NOT concurrency-safe, and a snapshot manifest is an input, not evidence

`state.py`, `order_submit.py` and `order_track.py` all wrote `<file>.tmp` then `os.replace`.
Two concurrent writers share that temp name: writer B truncates A's half-written temp, A
renames B's partial bytes into place - a torn state.json/order.json from two "atomic"
writes. U12 replaces it with mkstemp (unique name, same dir) + fsync + os.replace + dir
fsync (`safelib.atomic_write_*`) AND an OS-exclusive writer lock held across the whole
load -> mutate -> save (state CLI) / load -> check -> create -> finalize (order latch), plus
a base-digest compare-and-swap in `State.save()` so a stale in-memory view can never
overwrite a newer file. Second gotcha: `State.restore` trusted `manifest.json` paths and
`shutil.copy2` follows symlinks - a manifest entry of `../../x` or a workspace target that
had become a symlink would write outside the workspace. Every snapshot/restore entry now
goes through `safelib.contained_rel` (absolute/traversal/symlink refused, both directions)
and restore stages + verifies everything before the first swap. Third: `fcntl.flock` is
per open-file-description, so a nested `writer_lock` on the same path in one process would
self-deadlock - safelib keeps a process-wide per-path registry (RLock + depth) and only
takes the OS lock at depth 0. Tests: `tests/test_u12_safety.py` (real subprocesses for the
lock race and the os._exit crash; `safelib.FAULT_HOOK` for in-process faults).

## 2026-09-24 [linux][kicad][freerouting] No-container host needs three more things than CLAUDE.md lists
On the user-space KiCad 10.0.6 host: (a) `kicad-cli sch erc/netlist/export` fail with "Failed to
load _eeschema.kiface: libwebkit2gtk-4.1.so.0" - unpack libwebkit2gtk-4.1-0, libjavascriptcoregtk-4.1-0
and their deps (gstreamer-plugins-base, gstreamer-gl, soup-3.0, harfbuzz-icu, wayland-server,
enchant-2, hyphen, egl, orc) with apt-get download + dpkg-deb -x and append the lib dir to
LD_LIBRARY_PATH; (b) kicad-sch-api finds no stock symbols (power:, Connector_Generic:) unless
KICAD_SYMBOL_DIR points at ~/.local/kicad10/usr/share/kicad/symbols; (c) env.find_java only globs
tools/jre/*/bin/java, so a JRE unpacked flat as tools/jre/bin/java needs HWDE_JAVA, and Freerouting's
AWT init needs libXtst6 (same unpack trick). pd-trigger-lite kept these under tools/kicad-extra/.

## 2026-09-24 [jlc][parts][bom][order] JLC's public parts search cannot see idle stock - list every Extended part as a pre-buy
Owner's pd-trigger-lite order: JLC's BOM review left J1 (C2798175, USB-C, Extended, JLC stock
2769) UNSELECTED at qty 0 - its "idle components" column read 0 ("the idle components need to be
purchased before they can be used for PCBA ordering"). The part had to be bought into the parts
inventory first. The anonymous search hwde uses (`selectSmtComponentList`, via easyeda2kicad -
entry 2026-07-22) returns the SAME componentSource / isBuyComponent / assemblyComponentFlag for
C2798175 (idle) as for C21189 (0R Basic, assembles straight from stock), so no field it returns
tells the two apart and a part that is "in stock" can still stop at the BOM review. The
credentialed Open API (`jlcapi.component_detail`) was not checked live; the repo does not have
its response schema (contract.md lives only on the Windows host's ai-library). The owner
is fine with pre-buying: pick parts on stock, fit and cost as usual and do NOT avoid idle-stock
parts. bom_cpl.py now writes fab/prebuy.csv (every placed Extended part, LCSC number, qty for the
build) and the guide's ordering section lists it, so the pre-buy is on paper before the cart.

## 2026-09-24 [render][guide][kicad-cli][3d] The guide reused a render made before renders showed parts
Both pd-trigger guide PDFs showed a bare board. Their `reports/guide/top.png` was a copy of a
render taken before kc.render_png learned to find kicad-cli's 3D model loaders (PR #14), and
the board-guide step used whatever render was already in `reports/`. File dates can't catch
this: a checkout gives the render and the board the same mtime. So `guide_facts.py --render`
now renders top and bottom again through render.py on every guide build, and board-guide.md
copies `paths.top_render` to `reports/guide/top.png` each time. Also seen: pd-trigger-lite's
.kicad_pcb names its models by absolute path into another worktree. render_png now renders a
temp copy relinked to the workspace's own `lib/*.3dshapes` when such a path no longer
resolves, and never writes the board. The stock KiCad 3D library is not unpacked on the
no-container host, so the 1x02 pin header J2 (`${KICAD10_3DMODEL_DIR}`) renders as bare pads.

## 2026-09-27 [sim][linux][ngspice] SPICE never ran on the no-container Linux host
Every sim gate on this host failed with "undefined symbol: ngSpice_Init": off Windows,
InSpice resolves the library through `ctypes.util.find_library`, which only sees the
system linker cache, so KiCad's `~/.local/kicad10/.../libngspice.so.0` was never found
and InSpice dlopen'd the python binary itself. simlib now points InSpice's `find_library`
at the pinned `HWDE_NGSPICE_DLL` off Windows. Also: ngspice `.meas` cannot take another
measure in `when v(x)=par('g0-3')` (the circuit is rejected), so a -3 dB point relative to
a divider's DC gain is two decks (bench E2E `rc_lowpass`). And kicad-cli ERC/DRC write a
`.kicad_prl` beside the file they open: score a copy, or a pinned fixture drifts.

## 2026-09-27 [impedance][stackup][jlcapi] JLC's impedance calculator is a public API
- `POST https://jlcpcb.com/api/jlcTools/impedance/selectPageImpedanceDefaultTemplate` with `{"appointName": "JLC04161H-1080B"}` returns the template WITH `dielectricConstant` per layer (the order API's getImpedanceTemplateSettingList has none). No login.
- `POST .../jlcTools/impedance/calc` takes `{accessId, impedance_calc_mark, impedance_calc_arg:{H1,Er1,W1,W2,[S1],T1,C1,C2,[C3],CEr,...}, uuid}` in MIL, but the answer comes back over `wss://tools.jlc.com/jlcTools/webSocket/{uuid}`, not in the POST response. Forward mode (width -> ohms) works; the solve-for-width mode returned status 6 for every argument set tried.
- SI9000 OffsetStripline1B1A: the trace sits on H1 and is embedded in H2 (eps_eff matched lib/impedance.py to 4 digits with that reading).
- lib/impedance.py's field solver runs +0.6..0.9% above JLC on every case; treat that as the solver's known bias, well inside JLC's +/-10%.

## 2026-09-27 [linux][kicad][swig][footprint] board_swig's footprint root assumed sys.executable is inside the KiCad install
On the no-container host, board_init/board_swig's own footprints (Capacitor_SMD:C_0603_1608Metric
etc., not covered by a board's fp_paths) all "footprint not found". `board_swig.py`'s
`DEFAULT_FP_ROOT` was `Path(sys.executable).parents[1]/"share"/"kicad"/"footprints"` - true only
when sys.executable IS the KiCad install (container's bundled python, Windows kicad-cli's
python.exe). Here `sys.executable` is `~/.local/kicad10/bin/python3`, a wrapper that sources
`env.sh` then `exec /usr/bin/python3`, so the derived path is `/usr/share/kicad/footprints`,
which doesn't exist - the real library is `env.sh`'s `KICAD10_FOOTPRINT_DIR`. Fixed by preferring
the `KICAD<n>_FOOTPRINT_DIR` env var family (same family intake.py's `classify_uri` already
reads for lib-table URIs, kind "env") - highest major first, first that names an existing dir -
and falling back to the old sys.executable path otherwise. `resolve_default_fp_root()` takes
environ/executable so a test can monkeypatch both with no KiCad and no real board.

## 2026-09-27 [mcp][gate][kicad-cli] a gate run leaves a .kicad_prl beside the board
Every gate that loads a board through kicad-cli (drc, place, verify, dfm) creates
`<project>.kicad_prl` next to the `.kicad_pcb` when none is there, even with
`--no-record`. So "gate.py --no-record writes nothing" is false at the file level.
`mcp_server.py` deletes the prl files its read-only calls created (ones that were
already there stay), and `tests/test_mcp_server.py` asserts the whole workspace tree
hashes the same afterwards.

## 2026-09-27 [routing][krt][drc] KRT power widths under-clear off-grid 45-degree tracks by up to half a diagonal grid phase
usbbuck4's critical routing failed DRC with 3 or 6 `clearance` errors, VBUS (0.375 mm, on the
0.05 grid) running 45-degree-parallel to the off-grid USB_DM pair track at 0.1854 mm against
0.2. Not a host difference: the same placed board fails the same way under any PYTHONHASHSEED,
and whether it fails at all turns on the fresh board's incidental UUID order, so a clean
pytest run proves little. KRT keeps a power net's extra half-width over the base width as a
fractional A* margin, which falls short by under half a diagonal phase (0.05/(2*sqrt 2) =
0.0177 mm). route_critical's power pass now raises the clearance of nets already carrying
tracks by grid_step/2 in its own --net-clearances file (KRT keeps each obstacle out by that
obstacle net's clearance). Raising the routed power nets instead also fixed the DRC but cost
+3V3 a fine-pitch pad tap.

## 2026-09-27 [placement][place_seed][place_anneal] Edge snap goes by the pads when the courtyard is a designed overhang
- A declared-edge part whose courtyard reaches past its pad field (rf-term's SMA barrel, 12 mm) is snapped with its pads `edge_margin` inside the edge; the rest hangs off, floored at `placelib.ON_BOARD_MIN` (+0.05) on-board. A courtyard that ends at its pads (R1's on-board-only lap pad) is still courtyard-flush.
- Perimeter satellite slots used to start at the anchor's circumradius, which a long barrel inflates (C1 landed 6.5 mm off J1). They now start where the ray leaves the anchor courtyard.
- `pos` is the fraction of the span the CLUSTER CENTER can take, not of the edge. A designer who writes "pos 0.375 = x 9 mm" means the edge length; on rf-term that leaves no room for the three mounting holes. Unresolved.

## 2026-09-27 [place_seed][place_anneal][route_auto][mounting-holes][dsn] Three M3 holes on rf-term: pos, edge margin and the DSN's missing per-net rules
- `placement.edges[].pos` is "0..1 along the edge" (schema): the part's ORIGIN at that fraction of the edge length. The seed read it as a fraction of the cluster-centre span, which put rf-term's J1 4 mm off the designer's intent and left no column for two holes.
- Mounting holes (exclude_from_bom + exclude_from_pos_files, no board_only attr) are free singletons. The seed kept every courtyard 0.8 mm (edge_margin) in; rf-term's layout needs hole courtyards 0.05 mm off the edge. The real bound is copper: `Footprint.edge_keep(cu_edge)` = how far the courtyard must stay in for the pads to clear `.kicad_pro` min_copper_edge_clearance. Anneal kept free courtyards only inside the outline, which left pads 0.25 mm off the edge against 0.3 mm (DRC).
- KiCad's ExportSpecctraDSN writes netclass rules only: a `.kicad_dru` per-net track_width / clearance never reaches Freerouting, so `/RF` routed at 0.2 mm through its 0.94 mm and 0.8 mm HV rules. `routelib.dsn_apply_net_rules` gives such a net its own DSN class.

## 2026-09-28 [rules_gen][route_auto][dsn][freerouting][hole-clearance] HV clearance and hole clearance now live in the .kicad_pro netclasses
- PCB-0018's P7 route came back with 406 aiee_hv_* clearance errors. The run used a board worktree's own vendored hwde copy from before route_auto's DSN per-net patch, so nothing handed Freerouting the DRU-only HV clearance. rules_gen now also moves every HV net into a netclass carrying its clearance (`HV_<c>mm`, or `<class>_HV_<c>mm` for a power/diff net), so KiCad's DSN export, KRT and interactive routing all see it without the patch.
- KiCad's board-setup min_hole_clearance (0.25 mm default; hwde never sets it) is checked from the drill hole, and Freerouting knows nothing of it: on 4layer_1oz a track at 0.1016 mm beside a 0.45/0.2 via sits 0.2266 mm from the hole. update_pro floors every class clearance at hole clearance minus that class's via ring (0.125 there); a Diff class stops at its own pair gap.
- A SWIG test on a long-lived Xvfb can fail with "wxEntryStart failed"; a freshly started private display cleared it.

## 2026-09-29 [fwe][pinmap][toolchain] /fwe's first facts
- /fwe's scripts package is `fwelib`, not `lib`: fwe imports hwde's `lib` (env, simlib) read-only, and two packages named `lib` on one sys.path shadow each other.
- PCB-0018-A's DRV8300 (QFN) has no enable and no nFAULT pin, and MODE is 10k to GND, so INLx are non-inverted (active high). Arming the bridge means TIM1 MOE; the only hardware fault path is the G4 comparators on the sense pins (ISENSE_A/B/C = COMP1/2/4_INP, VBUS_SENSE = COMP3_INP) into TIM1 break.
- The same board puts ISENSE_A and ISENSE_C on ADC1 and ISENSE_B on ADC2, so the three phase currents can't be sampled in one simultaneous ADC1/ADC2 pair; sample two and reconstruct the third.
- Renode 1.17.0 ships no STM32G4 platform (G0, F4, L5, H7 yes); a G431 sim needs its own .repl. xPack QEMU 9.2.4 has no G4 machine either.
- The ST cmsis-device-g4 repo (v1.2.6) has a gcc startup file for the G431 but no gcc linker script; /fwe writes its own.
- (2026-09-29) A generated C header must print floats as C float literals: Python's `f"{16:g}f"` gives `16f`, which gcc rejects ("invalid suffix f on integer constant"); pinmap's `_f()` gives `16.0f`. Compile the generated header in a test.
- (2026-09-29) [fwe][sim] On the box `cc` on PATH is the session launcher (~/bin/cc), not a C compiler: a host-test runner that picks `shutil.which("cc")` first gets "invalid repo/track name '-std=c11'" on every test. Pick gcc, then clang, then cc.
- (2026-09-29) [fwe][sim] A custom Renode G431 .repl boots the bring-up ELF to its console with only core, NVIC, memory and STM32F7_USART modelled, plus Python read-back stubs for RCC (ready flags follow enables, SWS follows SW) and ADC1/2. Leave a polled peripheral unmapped and Renode logs a warning per read: an ADC timeout loop then takes minutes of wall time instead of microseconds. USART1 is IRQ 37.
- (2026-09-29) [fwe][tests] Every skill's router is `task_router.py`. A test that puts fwe's scripts dir on sys.path and does `import task_router` caches fwe's router in sys.modules, and hwde's tests/test_task_router.py + test_gate_record.py then fail collection (`no attribute load_tasks`) in a full pytest run, though each file passes alone. Load another skill's router by path under its own name (importlib spec_from_file_location "fwe_task_router").

## 2026-10-01 [freerouting][route_auto][dsn] Split pre-routed wires overflow Freerouting's stack; route_auto now joins them
- KiCad's DSN export writes hand-routed copper as one `(wire (path ...))` per track segment. On PCB-0019 (rp2040-mini, hand-routed USB D+) Freerouting 2.2.4 threw `java.lang.StackOverflowError` in `PolylineTrace.combine` while reading them, so route_auto fell back and Freerouting routed nothing. Same frame as the 2026-07-23 KRT guide-wire wedge.
- `routelib.dsn_merge_wires` joins each chain of wires with the same net, layer, width and attributes (`(type route|protect|fix)` included) into one path with the full point list; route_auto runs it after `dsn_apply_net_rules` on the main and probe paths and reports `dsn_wires_merged`. A branch point (3+ wires on one endpoint), a via on the joint, a cycle or a chain that closes on itself stays split. On the boards repo's saved DSNs it cut the wire count by up to 6x (2093 to 566 on lumina-par) with every segment kept, and Freerouting read a merged lipo-boost DSN cleanly.
- KiCad's DSN parser header carries `(string_quote ")`: a lone quote that flips every quote-aware s-expression scan after it, so `_sexp_end` from the top of the file raises "unbalanced". Blank it in a same-length copy before scanning.

## 2026-10-01 [bom][fab][research][tools] Distributor BOM upload headers could not be read live - DigiKey's come from an old template, Mouser's are a guess
For `<ws>_BOM_digikey.csv` / `<ws>_BOM_mouser.csv` (distributor_bom.py). mouser.com answers
both WebFetch (60 s timeout) and curl ("Access to this page has been denied") on its BOM Tool
help pages, so the Mouser field names were never read; the tool maps columns by hand at upload
and remembers the map, so plain names were chosen ("Mouser Part Number", "Manufacturer Part
Number", "Manufacturer", "Quantity", "Customer Part Number"). DigiKey's own upload template
(Digi-Key Part Number, Manufacturer Name, Manufacturer Part Number, Customer Reference,
Quantity 1) was read off a BOM Manager screenshot in a Georgia Tech capstone handout PDF, which
WebFetch called binary and the Read tool rendered (the 2026-08-08 entry again); today's myLists
upload was not checked. First real upload to either tool: confirm the auto-map, and whether a
long designator list fits the Customer Reference field, then fix the headers in one place.

## 2026-10-02 [placement][connector][verify] A connector facing into the board passed every gate; the place and verify gates now check mating
PCB-0021-A J4 (USB-A) and PCB-0018-A J701/J702 (JST GH) sat with their backs at the edge and their mouths into the board, and passed place, DRC, verify, DFM and the render review. lib/matinglib.py now fails that at P6 and P8. Gotchas hit building it: "SMA" is also the DO-214AC diode package (`SMA_L4.3-W2.6...`), so a bare SMA match calls a diode a coax connector; the mouth reads reliably from the pad layout (contact row at the back, body toward the mouth) on every connector in the boards corpus, which EasyEDA footprints give no rotation convention for; and `checklib.violation` sorts `refs`, so a check that needs to say which ref is the subject puts it in its own field. The same sweep found PCB-0021-A J1 (micro-USB) with its contact row parallel to the edge it sits at, so its mouth points along the edge.

## 2026-10-02 [jlc][fab][easyeda2kicad] CPL rotation: an imported footprint needs NO correction; the package table turned 41 shipped parts
JLC places a part the way its LCSC footprint model is drawn in EasyEDA. A footprint pulled by
easyeda2kicad (lib_pull.py, every `aiee:` footprint named like `SOT-23-6_L2.9-W1.6-...`) IS that
model's geometry, so its CPL rotation must equal the board rotation. jlc_rotations.csv's package
rows (`^SOT-23,180`, `^LQFP-,270`, `^SOIC-,270`, ...) are for KiCad LIBRARY footprints and still
matched the imported names, so every board in ~/dev/boards shipped such parts 90 or 180 deg off
(the owner's SOT-23-5/6 JLC previews). cpl_verify.py now fits each part's own model; on all 161
parts with a cached model the imported footprint needed correction 0. Two more facts:
(a) the EasyEDA component endpoint rate-limits PER IP - about ten quick requests, then 403 for
minutes, curl included - and easyeda2kicad's EasyedaApi swallows the 403 into `{}`, which reads as
"no such part"; lib/easyeda.py fetches itself and raises on HTTP errors. (b) A pad-size fit
tolerance is wrong: an SO-8 LCSC model's rows sit 6.0 mm apart vs 5.12 in a KiCad-style land
pattern; use half the smallest pad pitch (each model pad nearer its own pad than any other).

## 2026-10-04 [verify][waivers] gate verify skipped a board's waivers when the board sat in a worktree
rp2040-mini, checked out at ~/.cc/worktrees/boards/<track>/<board>, failed findings it had waived: `releaselib.waivers_for_input` only recognised a workspace whose parent is a boards dir, so it never looked at reports/verify-waivers.json and fell through to the input dir. It now takes the first parent holding state.json as the workspace (the same marker statelib.find_workspace uses, which is what gate freshness already binds through the `waivers` kind), with the boards-dir rule and the input-dir path kept as fallbacks. A rule that finds a workspace by where it sits breaks on the first checkout elsewhere; find it by what it holds. `gate.workspace_dir` still uses the boards-dir rule, but only to choose which repo a gate commit goes into.

## 2026-08-15 [P4][erc][kicad-sch-api][schematic] An ALREADY-retyped pulled library can still hide exactly one ERC-blocking pin: BOOT on a buck
Promoted from boards/PCB-0010-A_bb-buck/LEARNINGS.md (promotion pass 2026-10-04).
The pulled `LMR33630ADDAR` symbol arrived with a sane typing pass already applied
(PGND/VIN/EP `power_in`, SW/VCC `power_out`, EN/PG/FB `passive`) - so the usual
"easyeda types are junk, retype everything" reflex reads as unnecessary here. It is
not: BOOT (pin 7) came typed `power_in`, and `/BST` has no schematic-visible driver
(the bootstrap diode VCC->BOOT is inside the package; the net's only other member is
C6, a passive). Measured with the fix disabled, `kc.py erc` on the finished sheet
returned EXACTLY ONE finding - `power_pin_not_driven` "Input Power pin not driven by
any Output Power pins" on U1 pin 7 - and zero of everything else, so the whole ERC
gate hung on that single library attribute.
Two ways out, and they are not equivalent. A PWR_FLAG on `/BST` clears ERC but leaves
the pin `power_in`, which pulls `/BST` into `netlist_audit`'s `power_undeclared`
warning - and `architecture/sheets.md` s4 deliberately leaves `/BST` undeclared
(a `power` entry would give a gate-charge node a width rule). Retyping the pin to
`passive` in `lib/aiee.kicad_sym` clears both, matches sbuck-5v3a's AP64350 BST pin,
and matches the standing retype rule (supplies/grounds power_in, regulator output
power_out, everything else passive - a bootstrap node is not the part's supply).
Done in `kicad/gen/lib_pin_types.py`, which lists only DEVIATIONS from the pulled
typing so a later `lib_pull` refresh cannot be silently re-typed wholesale, and which
`gen/root.py` calls before the symbol cache reads the library. Generalisation: judge a
pulled symbol pin-by-pin against the datasheet extract even when the file looks
already-fixed, and prefer the library fix over a PWR_FLAG whenever the net is one the
architecture deliberately leaves out of constraints.json.

## 2026-08-15 [P4][spice][sim-analyst] The runner's injected `rshunt=1e9` is a ~1 nA current source at every node - on a high-impedance FB divider that is 2 % of the IFB spec you came to measure
Promoted from boards/PCB-0010-A_bb-buck/LEARNINGS.md (promotion pass 2026-10-04).
`sim_run.py`/`prepare_circuit` injects `.options rshunt=1e9` unless the deck sets rshunt
itself (it exists so one floating node cannot make the solve singular). On a 100k/24.9k
feedback divider the tap sits at 1.0 V, so the injected shunt quietly pulls 1 nA out of it
- and the datasheet term the bench exists to bound, LMR33630 IFB, is 50 nA max. The shunt
is therefore 2 % of the modelled worst-case error term, and it lands with the SAME SIGN
(it raises the implied VOUT by ~0.1 mV), i.e. it silently flatters nothing but does
contaminate a corner that is being reported to 5 digits. The deck has no floating node
(every node reaches ground through R2, the reference source or the amplifier), so the fix
is one documented line: `.options rshunt=1e12`, which drops the artefact to 0.1 uV and
made all 15 measures match closed form to 6 significant digits. Generalisation: on any
bench whose measured quantity is a sub-microamp current or a megohm-class node, treat the
injected 1e9 shunt as a real circuit element and override it deliberately.

## 2026-08-15 [P4][spice][sim-analyst] A switcher's DC setpoint IS simmable without any converter model - one high-gain VCVS is the whole Tier-B boundary model
Promoted from boards/PCB-0010-A_bb-buck/LEARNINGS.md (promotion pass 2026-10-04).
Policy forbids simulating the buck (no vendor model, and an agent-authored switcher model
would not be honest), which reads as "the output voltage cannot be verified at all". It
can: the only thing the regulator does to the divider at DC is drive VOUT until
v(FB) == VREF, and that is one ideal amplifier - `Vref nref 0 DC {vref}` plus
`Eu1 vout 0 nref fb 1e8`. The network then SOLVES for the divider ratio instead of the
deck restating it, which is the whole point (a retyped `Vout = vref*(1+r1/r2)` B-source
would pass even if R1/R2 were wrong). Gain 1e8 leaves 50 nV of residual setpoint error, six
decades under any useful bound. Corners come from N `.subckt` instances (ngspice has no
`.step`), and `.meas dc <n> find v(<node>) at=0` against a 2-point `.dc` sweep of a dummy
source is enough analysis to make `.measure` legal - 11 corners + 4 derived params solved
in 1.0 s. Keep the as-drawn `--fragment` lines verbatim at the TOP level as the nominal
instance: it is the one bound that still fires if a later editor changes the schematic
values without touching the `.param` corner block.

## 2026-08-15 [P4][easyeda2kicad][parts][erc] An in-stock LCSC part can have NO EasyEDA CAD record at all - lib_pull fails it while its own family siblings pull clean
Promoted from boards/PCB-0010-A_bb-buck/LEARNINGS.md (promotion pass 2026-10-04).
The A3 setpoint recentring needed YAGEO `RT0603BRD07102KL` (LCSC **C861068**, 2009 in
stock, full parametrics from `parts_search`). `lib_pull --lcsc C861068 C861257` pulled
C861257 and returned `status: error` / exit 1 for C861068 with only
"Failed to fetch data from EasyEDA API". That reads like the known WAF/rate-limit class
(LEARNINGS 2026-07-28), and it is NOT: probed directly,
`EasyedaApi().get_cad_data_of_component()` returns `{}` for C861068 and a full dict for
C861257 and C136967 in the same second. **Distinguish the two before retrying or backing
off** - a rate limit clears, an absent CAD record never will, and retrying just burns the
WAF budget. Fix used: derive the symbol from the just-pulled SAME-FAMILY sibling
(RT0603BRD0725K5L -> RT0603BRD07102KL, identity fields only: symbol name, Value, MPN,
Datasheet, `LCSC Part`), which keeps the pulled geometry and the shared `aiee:R0603`
footprint - defensible because a 2-pin chip resistor's symbol carries no part-specific
pinout to get wrong. Do NOT hand-draw a symbol for a part whose pinout is non-trivial;
there, change the MPN instead.
Second fact from the same pull, and the reason the ERC gate did not break: the freshly
pulled `RT0603BRD0725K5L` came typed **`unspecified` on both pins** while
`RT0603BRD07100KL` / `RT0603BRD0724K9L` - same YAGEO RT0603BRD family, pulled at P3 -
came typed `passive`. Pin typing therefore varies WITHIN one family by pull date, so the
`gen/lib_pin_types.py` deviation list has to be re-checked after every single pull, not
just after a wholesale library refresh.

## 2026-08-16 [P7][routing][planes_gen] planes_gen's DEFAULT thermal-relief pad connection strands a pad in a tight corridor - `connect: solid` in a planes-only sidecar is the fix
Promoted from boards/PCB-0010-A_bb-buck/LEARNINGS.md (promotion pass 2026-10-04).
On this 2-layer board U1's PGND pin (pad 1, 1.3 x 0.6 mm) sits in a 0.79 mm-tall corridor
between the +VIN bulk-feed track above and the C1->VIN hot-loop track below. KiCad's default
zone `connect_pads` is THERMAL RELIEF, whose ~0.5 mm thermal gap is applied from BOTH the pad
and the neighbouring exposed pad, so the two gaps overlapped and no fill could form between
U1.1 and the EP. Result: `starved_thermal` (min spoke count 2, actual 1) PLUS a genuine
`unconnected_items` - U1.1 + C1.2 sat on their own 1.2 mm2 pour island, electrically off the
GND net, while F.Cu GND reported only "2 islands" and looked healthy. Re-pouring the same
region with `{"layer":"F.Cu","net":"GND","connect":"solid"}` in a planes-only sidecar
(constraints.json untouched) cleared both in one step and is independently correct here: the
EP is AGND and the datasheet wants it soldered SOLID to the plane. Two corollaries: (a) a
zone-fill island COUNT is not a connectivity check - only kicad-cli DRC is; (b) planes_gen
will not restyle an existing zone (>=80% existing-fill coverage -> skip), so the pour must be
regenerated from a clean board, not patched.

## 2026-08-16 [P7][routing][geometry] Read pad extents from `geom.pads_of().poly.bounds`, never from the raw `(size w h)` - the pad carries its OWN rotation
Promoted from boards/PCB-0010-A_bb-buck/LEARNINGS.md (promotion pass 2026-10-04).
Three hand-computed track endpoints landed 0.2-1.3 mm outside their target pad because a naive
read of L1's `(pad ... (size 5.4 2.9))` ignored the pad's own `at` rotation: the real land is
2.9 wide x 5.4 tall (x 45.72..48.62, y 38.53..43.93), not 5.4 x 2.9. route_edit ACCEPTS such an
op - it only verifies the segment landed where asked - and the error surfaces two steps later as
`unconnected_items` + `track_dangling` naming a track by LENGTH, not by endpoint. Cheap guard
before emitting any route_edit op list: assert every add_track endpoint is `covered` by the
target pad's `poly`; the footprint rotation, the pad rotation and the board transform are all
already baked into it.

## 2026-08-16 [P9][bom_cpl][fab] A THT footprint with `attr through_hole` but no `exclude_from_pos_files` defaults to `smt_placed` - the terminals shipped in CPL.csv
Promoted from boards/PCB-0010-A_bb-buck/LEARNINGS.md (promotion pass 2026-10-04).
bb-buck P9. `bom_cpl.py` decides BOM/CPL membership from `assembly_class`, and its
auto-classifier keys on `exclude_from_pos_files`, NOT on `attr through_hole`. The
easyeda2kicad KF128-5.08-2P screw-terminal footprint carries `attr through_hole` and no
pos-file exclusion, so both terminals were classified `smt_placed` and written into
`CPL.csv` - onto a pick-and-place list for a machine that physically cannot place them.

`bom_cpl` reported `status: pass`, `violations: []`, `bom_complete: true`. Nothing in the
pipeline flags it, because from the script's point of view a placed part with an LCSC code
and a position is exactly what a CPL row is.

Fix: `"assembly_class": "hand_install"` on the parts.json line (applies to every ref in
`refs`), plus `refdes_notes` for the assembler. Class counts then read
smt_placed 14 / hand_install 2 / board_feature 4, and CPL.csv drops to the 14 real
placements.

**Check the class split by eye on any board with THT parts** - `n_placed == n_parts` is not
the reassurance it looks like. Candidate script fix: treat `attr through_hole` as a
hand_install signal in the auto-classifier the same way `exclude_from_pos_files` is.

## 2026-08-16 [P6][gates][build-modes][PIPELINE BUG] `placement.edges` pins the connectors to the PROVISIONAL outline, so under a `canonical` binding the place gate cannot pass BEFORE `--outline fit`
Promoted from boards/PCB-0011-A_bb-mcu/LEARNINGS.md (promotion pass 2026-10-04).
`placelib.legality_violations` measures every `placement.edges` ref against
`edge_line(model.outline, ...)` with `EDGE_TOL = 2.5 mm`, and the outline it reads is
whatever `board_init --outline auto` produced. On bb-mcu that was 51.15 x 43.524 mm, a
shelf-pack artifact; the canonical layout is 33.77 x 21.26 mm of content. Placing to the
canonical layout leaves J1 16.85 mm from the provisional right edge -> `edge_violation`,
gate FAIL. Placing to satisfy the edge means spreading the three connectors to the
provisional edges, and then `--outline fit` measures the spread and "earns" the
provisional size - the bb-buck lesson (2026-08-16 board_edit entry) with the sign
flipped: fit measures the placement in front of it, and a placement stretched to a big
outline has already spent the difference.
Both `reference/recipes/resize-board.md` ("Place (P6) against that room, gate `place`",
then fit) and the P6 agent contract order it place -> gate -> fit. For a `canonical`
binding with ANY `placement.edges` entry the order has to be **place -> fit -> gate**.
Measured on this board: after `board_edit --outline fit --margin M` the place gate is
clean (0 violations, all five coverage legs green) at M = 0.5 / 1.0 / 2.0, giving
34.77 x 22.26 / 35.77 x 23.26 / 37.77 x 25.26 mm. The fix is the ORDER, not the
placement. Second-order trap in the same shape: `silk_place` and `board_edit --outline
fit` disagree about what is on the board - fit measures courtyards + copper + rule areas
and ignores silk, while silk_place solves refdes positions against the CURRENT outline,
so on the provisional board it parked J1/J3/H1/H2's refdes where the earned edge later
cuts them. Hand-place any refdes outside the future content bbox, or re-run silk_place
after the fit.

## 2026-08-16 [P3][librarian][footprint][fp_verify][PIPELINE BUG] A pulled THT footprint was under-drilled 1.30 mm against the vendor's own 1.50 mm, and nothing in the pipeline could have caught it
Promoted from boards/PCB-0011-A_bb-mcu/LEARNINGS.md (promotion pass 2026-10-04).
J1 (WJ500V-5.08-2P, LCSC C8465) pulled with a 1.30 mm drill. The vendor's own
"PCB LAYOUT" panel recommends **1.50 mm**, and the datasheet dimensions the pin
0.90 mm wide. If that pin is square - which the square screw-clamp opening
drawn above it indicates, though the shape is never explicitly labelled - its
worst-case diagonal is 0.90 * sqrt(2) = **1.273 mm**, leaving **0.027 mm** of
total diametral clearance. That is a terminal that does not seat, discovered at
assembly, on every board in the batch.

Why no gate saw it, and this is the part worth keeping:

1. `fp_verify` has **no drill-vs-pin check at all**. It checks pad count,
   pitch, pin-1, pad size and a fab annulus floor. Nothing compares the hole to
   the thing that goes through it.
2. `fp_verify`'s land-pattern diff needs a `parts/<lcsc>.json`, and connectors
   do not get datasheet extractions by default - the P3 roster runs an
   extractor "per nontrivial IC". So the diff never ran, and the librarian
   correctly reported the connectors as verified only for courtyard, pin-1 and
   annulus floor.

It surfaced ONLY because the librarian reported honestly what it could NOT
verify instead of reporting a clean pass, and that gap was then chased with two
extra extractions. An agent that had summarised "3/3 footprints pass" would
have shipped it.

Two fixes worth making: extract datasheets for THROUGH-HOLE CONNECTORS as well
as ICs at P3 (their drill is a board-killer and their datasheets are short),
and give `fp_verify` a drill-vs-stated-pin check with the square-pin diagonal
built in.

## 2026-08-16 [P3][fp_verify][footprint] fp_verify never compares row_spacing_mm, although land_pattern carries the field
Promoted from boards/PCB-0011-A_bb-mcu/LEARNINGS.md (promotion pass 2026-10-04).
The U1 SOP-20 diff returned pad_count 20/20, pitch ok, pin-1 present and ONE
pad_size warning. Hand measurement found what the tool does not look at:

    pad size     0.35 x 1.494 mm  vs datasheet 0.40 x 1.35 mm  -> warned
    row spacing  6.00 mm          vs datasheet 5.75 mm         -> NOT CHECKED

`datasheet_extract` populates `land_pattern.row_spacing_mm`, so the data is
there and only the comparison is missing. On a two-row leaded package the row
spacing is what decides whether the pads capture the lead FEET at all - it is
more load-bearing than pad size, because getting it wrong slides both rows off
the leads while pad-count and pitch still pass.

Here it was benign and was accepted on a worked argument (the pulled land spans
2.253-3.747 mm from the centreline against a lead foot at ~2.6-3.2 mm, so it
captures the foot with MORE toe and a WIDER inter-pad gap than ST's own land).
But it was found by hand, not by the gate.

## 2026-08-16 [P8][silk][silk_place] silk_place SKIPS board_only refs by design, so a mounting hole's refdes is never solved and can end up labelling the IC
Promoted from boards/PCB-0011-A_bb-mcu/LEARNINGS.md (promotion pass 2026-10-04).
`check_silk` flagged `silk_misattributed`: H3's refdes sat 1.67 mm from its own
hole and **0.23 mm from U1**, reading as U1's designator on a board whose only
IC it is. The obvious remedy fails silently in the right direction:

    silk_place --refs H1,H2,H3,H4 --apply
    -> moved 0, skipped [{H1 board_only} {H2 board_only} {H3 board_only} {H4 board_only}]

`silk_place` solves "every visible non-board_only silk refdes", and mounting
holes are board_only. It exits 0 and reports honestly - it just cannot help.
The fix is the one `check_silk`'s own message names: a direct
`place_edit move_text`, which needs the hole's real coordinates (they are in
the P6 place-edit report, not in the silk report).

Worth considering: mounting-hole refdes carry no assembly information at all -
nothing is placed at H3 - so hiding them may beat placing them.

## 2026-08-16 [P3][process][windows][state] Backticks in a Bash-tool argument are COMMAND SUBSTITUTION, and a state.py decision silently lost a word to it
Promoted from boards/PCB-0011-A_bb-mcu/LEARNINGS.md (promotion pass 2026-10-04).
Recording a decision whose `--why` prose contained a backtick-quoted term:

    ... with the floor at `proven`, a verified-but-unapproved record ...

Bash ran `proven` as a command, substituted its empty output, and state.py
stored "... with the floor at , a verified-but-unapproved record ...". state.py
exited 0; the only signal was one `proven: command not found` line on stderr
from a different process than the one being checked.

Same family as the recorded `\`-in-a-quoted-heredoc collapse: prose destined
for a file should not travel through the shell. Long `--why` / `--note` text is
exactly that, and it is the audit trail, so a silent deletion there is worse
than most places. Read a long decision back out of state.json once after
recording it - that is how this was caught.

## 2026-08-16 [P4][schematic][easyeda2kicad][kicad-sch-api] A pulled symbol's reversed pin ANGLES make schlib emit INWARD stubs, and rotating the part is not the fix
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
`aiee:293D226X9016D2TE3` (C2, the 22 uF compensation tantalum) came out of easyeda2kicad with
its two pin angles backwards relative to its own graphics: pin 1 at x=-5.08 carried angle 180
and pin 2 at x=+5.08 carried angle 0, so both leads are DRAWN away from the plates (connecting
to nothing, 3.5 mm short of the electrode) and `schlib.stub_dir` - which reads outward as the
opposite of the pin angle - emitted both auto-stubs INWARD. Result: the rail and ground labels
land 5.08 mm apart ON the body and render as one run-together string ("+3V3GND") with the rail
label over the plate. ERC is 0/0 and the netlist is correct throughout - a pin's connection
point is its `(at ...)`, which the angle does not move - so NO machine gate sees it; only the
H2 schematic PDF does. The obvious workaround (rotate the cap to a vertical shunt) is WORSE and
already on the ladder: KiCad rotates field TEXT with the symbol while schem_refdes does not, so
a rot-90/270 2-pin passive overprints its own Reference/Value (2026-08-09 entry) - measured here
too, the 22-char value string ran vertically through the refdes and the LCSC field. Fix at the
SOURCE, like the pin-TYPE repair does: `kicad/gen/lib_fixups.py` (idempotent, `--check`
mode, re-run after any lib_pull) rewrites the two angles and trims both lead lengths to 3.81 mm.
Nothing electrical changes - verified by `netlist_audit --compare` against the pre-repair
netlist: 0 differences. Sibling symbol `aiee:TAJA106K016RNJ` (C1, same vendor family, same
pull) has CORRECT angles, so this is per-symbol damage and cannot be assumed away: check any
pulled 2-pin passive by placing it and reading `Sheet._pin_out_dir`, not by reading the file.

## 2026-08-16 [P4][schematic][erc] Hang a rail's power symbol in its own cluster, not on a pin stub, when the IC's pins are on 2.54 mm pitch
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
`power_symbol_at_pin("U1", "2", "power:+3V3")` puts the symbol's PIN on the stub end and the
symbol BODY (the arrow) extends from there - on the AMS1117's 2.54 mm pin pitch that body lands
on the neighbouring pin's local label, so the rendered sheet shows the +3V3 arrow drawn through
U1 pin 1's "GND" text. Electrically fine (ERC 0/0), unreadable on the PDF. Measured root cause:
every KiCad power symbol is EXACTLY 2.54 mm tall (GND's triangle hangs 2.54 below its pin, the
rail arrows rise 2.54 above), which is exactly the pin pitch - so a symbol on any middle pin
reaches precisely into the neighbouring pin's row, and the geometry is unsolvable by stub length
alone (whichever of two adjacent pins gets the shorter stub, its symbol lands on the other's
wire). The arrangement that works on bb-ldo's final sheet: the middle pin (VOUT) takes the short
stub and its rail symbol, and the neighbour (GND) is routed OFF its own row first - out 1.27,
up, across, then down into its symbol - which is also the only ordering of the three left-side
pins with no wire crossing. `power_flag(net, at=..., sym=..., flag=False)` remains the clean
form for a free-area rail cluster that must not carry a PWR_FLAG.

## 2026-08-16 [P4][schematic][easyeda2kicad] A pulled 2-pin passive names its pins "1"/"2" - KiCad prints those names ON the body, on top of each other
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
Every easyeda-pulled 2-pin part on this board (both tantalums, both screw terminals) carries pin
NAMES that are the same strings as the pin NUMBERS. KiCad renders names INSIDE the symbol body, so
on a small part both names land at the body centre and overprint each other and the numbers - a
garbled glyph in the middle of every passive, which is what the schematic reviewer saw. Nothing
machine-checkable notices: ERC 0/0, netlist unchanged, `schem_refdes` reports `residue: []` because
pin names are symbol graphics, not fields it places. Fix at the source with `(pin_names hide)` on
the symbol (kept in `kicad/gen/lib_fixups.py`); the NUMBERS stay visible, which is what tells a
reader which end of a polarized part is pin 1. Write the bare-token form `(pin_names hide)` into a
`(version 20211014)` pulled lib - that file's own properties use bare `hide`, not `(hide yes)` -
and kicad-sch-api carries the token through into the schematic's embedded `lib_symbols`.

## 2026-08-16 [P4][schematic][erc][netlist] Two pins of ONE part are two nets on the sheet: a drawn run off the tab left the rail unnamed and ERC stayed 0/0
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
Redrawing bb-ldo with real wires, the +3V3 output path was drawn from U1 pin 4 (the SOT-223 tab)
through C2 to J2, while U1 pin 2 (VOUT) got the rail's `power:+3V3` symbol. Pins 2 and 4 are the
same node INSIDE the package, but on the sheet they are two separate nodes: the drawn run never
touched the symbol, so it exported as `Net-(C2-Pad1)` and `+3V3` held only U1.2. **ERC reported
0 errors / 0 warnings** - an unnamed net with three pins on it is perfectly legal - and the only
gate that caught it was `netlist_audit --compare` against the pre-redraw netlist. Two rules. (a) A
cosmetic redraw is not verified until the netlist is diffed against the version it replaced;
compare is the gate, ERC is not. (b) When a part exposes one node on two pins, each pin needs its
own connection to the rail - a second power symbol on the drawn run is the cheapest, and is what
joins them globally.

## 2026-08-17 [P7][planes_gen][thermal-via][knowledge] planes_gen would have via-stitched the live SOT-223 tab: its EP heuristic never reads constraints' `min_vias`
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
`planes_gen` places a thermal-via grid under "the largest NETTED SMD pad per footprint whose area
>= 4.0 mm2 and whose net is a plane net". U1's tab (pin 4, 2.34 x 3.6 = 8.4 mm2) is exactly that,
and its net `+3V3` IS the F.Cu plane net - so the default run drills a grid from the VOUT heatsink
pour straight into the B.Cu GND plane. That is the short forbidden by verified record
`linear-regulator-live-tab-thermal-vias`, and `constraints.thermal[U1].min_vias: 0` does NOT stop
it: grep the script - nothing in `planes_gen` reads `min_vias`, the two numbers never meet. On any
board whose plane net is a live tab net (1117-class VOUT, a high-side FET drain), run
`planes_gen --no-thermal-vias` and let the pour do the cooling by dielectric coupling. Same trap
waits on C2, whose 8.3 mm2 tantalum pads also clear the 4.0 mm2 floor.

## 2026-08-17 [P7][route_auto][stitch_vias][thermal] route_auto's KRT finish connects plane-net SMD pads with TRACES, not vias - on a thermal-pour board that silently eats the heatsink
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
Freerouting itself stopped at 0.60 completion (2 nets unrouted, all three rungs identical); the KRT
mop-up finished GND and DRC went to 0. But it did it by daisy-chaining the three F.Cu GND SMD pads
(U1.1, C1.2, C2.2) to J1's thru-hole with ~25.6 mm of 0.2575 mm F.Cu trace - straight across the
`+3V3` thermal pour, including a slit between U1 and C2 at 7 mm from the tab. Cost: pour 1210.8 ->
1189.6 mm2 and check_thermal's effective area 592.6 -> 577.1 mm2 (-2.6%), on a board whose whole
margin IS that number. The chain's own answer is in `route_critical`'s report note ("plane is the
trunk; SMD pads stitched by stitch_vias"): rip the KRT GND traces with `route_edit` and run
`stitch_vias`, which puts one via per pad on a ring just past the pad edge, away from the body -
inside the void the pad already cuts in the pour, so they cost ~0 mm2 and leave B.Cu continuous
under the tab. Recovered to 1199.2 / 585.0 mm2 with DRC still 0. On a 2-layer pour board the
stitch therefore has to come AFTER route_auto *and* after ripping what KRT laid, not merely after.

## 2026-08-17 [P7][route_critical][scripts] `--out-report` into a missing directory crashes AFTER the board is already written
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
`route_critical --out-report boards/bb-ldo/route/route_critical.json` mutated the board (4 +5V
segments landed), then died with a raw `FileNotFoundError` traceback from `checklib.emit` because
`route/` did not exist - no JSON, no facts, exit non-zero on a run that actually succeeded. The
workspace scaffold (`state.py`) creates `routing/`, while the P7 role prompt and every route script
default to `route/`, so the first `--out-report` of a session hits this. `mkdir` the report
directory before the first write; re-running `route_critical` afterwards is safe (it detects
`already_routed` and adds no duplicate copper).

## 2026-08-18 [P8][stitch_vias][dfm] stitch_vias places vias INSIDE large pads, and its own checker cannot see it
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
Two compounding defects. (1) `RING_RADII` are measured from the pad CENTRE, so
any pad with a half-extent > 0.65 mm gets its "just past the pad edge" via
placed inside the pad. (2) `via_check` only tests FOREIGN copper, so a via
landing in its OWN pad is never flagged. On JLC economy PCBA - which neither
fills nor plugs vias, with paste apertures equal to the pads - paste prints
over an open barrel and wicks down it at reflow: starved or open joints.

Third defect in the same family: it emits **no F.Cu stub** with a pad via, so
on a board whose top layer has no copper for that net (here GND, because the
top layer is the +3V3 heatsink pour) an off-pad via is orphaned. The tool
should emit stub + via as a pair.

Caught only by a human-style visual review at P8, three stages after the edit.

## 2026-08-18 [P8][check_current][plane] check_current charges every transition via the whole net's current
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
It wants >= 1 via per 0.5 A and attributes the full rail current to each via.
On bb-ldo the 0.515 A load return never crosses the three GND stitch vias at
all: it flows J2's GND pin -> B.Cu plane -> J1's GND pin, and BOTH are
through-hole screw terminals whose pins penetrate every layer. Those vias carry
only the regulator's quiescent current and capacitor ripple. Doubling them to
satisfy the heuristic would have spent ~2 mm2 of heatsink pour to silence an
advisory. The check's own message says "per-cluster current unattributed" -
believe that qualifier before spending copper.

## 2026-08-18 [P0][build-modes][thermal] At block-only, thermal is the whole design - and the datasheet's copper table is the spec
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
Nothing about a 5 V -> 3.3 V linear regulator is interesting except where the
watt goes. The part choice turned on which candidate published a copper-area
-> theta_JA table measured on OUR board class (1 oz FR-4, 2 layer), not on
which had the better headline numbers: the electrically superior MCP1825S was
rejected because its only theta_JA figure is a JEDEC 4-layer number that a
2-layer board cannot reach and its datasheet gives no curve to design against.
Choosing it would have meant sizing the copper with no applicable data - the
exact error the board exists to teach against.

## 2026-08-20 [P6][planes_gen][board_edit][scripts] planes_gen has no re-pour path: on a board that already has zones and whose outline GREW, it ADDS a duplicate zone - and nothing in the skill can delete a zone
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
`board_edit --outline WxH` re-clips the existing fills but says plainly that
zone OUTLINES do not follow the edge, so the pour has to be regenerated. Run
`planes_gen` on that board and its idempotency guard - "an existing same-net
fill covering >= 80% of the planned region means skip" (`EXISTING_COVER`) -
reads only 73% here, because the old zone's rectangle stops at the old
boundary. So it ADDS a second `+3V3` F.Cu zone and a second `GND` B.Cu zone,
same nets, same layers, both at priority 0, on top of the two already there.
The fill union is correct (measured 1097.86 mm2 either way) but KiCad DRC
returns two hard `zones_intersect` errors: "intersecting zones must have
distinct priorities".

There is no way out with the sanctioned editors. `route_edit`'s `remove
{uuid}` indexes `board.GetTracks()` only, so a zone uuid comes back "absent"
and then fails the driver's own verify (the uuid is still in the file);
`place_edit` is footprints + text; `board_edit` only removes Edge.Cuts items;
`plane_repair` bridges splits. Nothing exposes `SetAssignedPriority` on an
existing zone either, which would have been the other legal fix.

The recovery that works is `state.py restore --label <a zone-free snapshot>`
and re-running the whole chain (place -> board_edit -> planes_gen -> silk) on
a board that has no zones. On bb-ldo `pre-P7-routing` was exactly that, and
diffing it against the delivered board (ignoring uuids, tracks, vias, zones)
showed a single line of difference - the `HOT SURFACE` legend - which
`place_edit add_text` puts back. So: **before re-pouring a board whose outline
changed, get it to a zero-zone state first**; discovering it afterwards costs
a full rebuild.

Wanted: `planes_gen --repour` (drop the same-net/layer zones it is about to
replace), or a `zone` op on route_edit.

## 2026-08-20 [P6][silk][placement] Centring both edge connectors on a small square leaves no room for pin-adjacent silk - and the binding obstacle is a neighbour's own polarity marker
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
Honouring `placement.edges pos 0.5` on the 34.655 mm square puts J1 and J2
courtyards 12.655 mm apart, and the U1+C1+C2 cluster only fits that corridor
turned 90 deg. Measured silk-to-silk (centreline + stroke/2, NOT courtyards),
the left channel is 16.270 -> 17.026 = 0.756 mm and the right is 26.957 ->
28.635 = 1.678 mm. A 1.2 mm silk label is 1.4 mm tall, so neither takes one -
and the left channel is narrow not because of a courtyard but because C1's
FOOTPRINT prints a 0.25 mm-stroke `+` polarity cross 3.1 mm out from its
centre, 1.1 mm beyond its own courtyard. Turning the labels 90 deg into the
channels was tried and real DRC rejected it (2 hits on that `+`, 1 on U1's
body line at 0.024 mm).

Two things to carry: courtyard extents are the wrong model for silk clearance
(footprint silk routinely exceeds the courtyard), and when the corridor loses,
the answer is to move the legends OUT - net name above the connector for the
upper pin, below for the lower, function label as the header. That keeps a
3.9 mm / 9.0 mm = 2.3:1 adjacency to the right pad and clears real DRC at 0.

## 2026-08-20 [P7][route_auto][freerouting] Freerouting cannot read this board at all - route_auto has nothing to contribute, so its KRT fallback is the ONLY thing it can do
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
Re-routing the 34.655 mm square: `route_critical` lays the +5V trunk with KRT,
and Freerouting 2.2.4 then dies with `StackOverflowError` at
`PolylineTrace.combine` while READING the DSN - ZERO pass lines, rung1.log is
75 KB of one repeated frame. That is the documented KRT-guide-wire wedge
(repo LEARNINGS 2026-07-23), and every rung parses the same DSN, so the whole
ladder is dead. `route_auto` therefore has exactly two outcomes on bb-ldo:
`--no-krt-finish` -> exit 2, "board untouched"; or the KRT finish, which is
the pass that daisy-chains the F.Cu GND SMD pads with traces across the
thermal pour (2026-08-17 entry). Running it to then rip what it laid buys
nothing: the deliberate `route_edit` via+stub pairs are the same end state.
Set `--timeout-s` low (120) - the default 600 makes the wedge cost 10 minutes
per rung for a result that is knowable from the first log line.

## 2026-08-20 [P6][build-modes][canonical] Moving the edge parts in fixes the WIDTH; the SHAPE stays inherited
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
At P6 this board was found to have inherited its width from `board_init
--outline auto`'s provisional 86.29 mm, via `placement.edges` pinning the
connectors to whatever outline was on the board. The fix - move the connectors
inward, then `--outline fit` - corrected the width and was verified. It was
still only half a fix: `fit` wraps wherever the parts ended up, so the ASPECT
remained a property of the provisional rectangle. The board shipped at
50.000 x 26.420 (1.89:1) when the design wanted a square.

A 22-candidate measured study (build each candidate, pour it, measure the real
fill) found:

- The free-aspect optimum is a broad **plateau from 1.00 to 1.47**; r25 varies
  0.4% across it. There is no peak to chase, only a boundary to stay under.
- That boundary is **derivable, not empirical**: check_thermal's reach disc
  plus the pour inset fits wholly on the board only when the short dimension is
  >= 2 x (14.329 + 0.5) = 29.66 mm. At 1321 mm2 that caps aspect at 1.50:1.
  The delivered 26.42 mm height spilled 28.2 mm2 of the disc off both edges.
- `placement.edges` cost **0.00 mm2** at aspect <= 1.47 and only 31.8 mm2 at
  1.89. Its real width floor is ~32.3 mm. **The constraint the finding blamed
  was not the cause** - a 36.35 mm square satisfies J1-left/J2-right with the
  terminals CENTRED at the `pos: 0.5` the constraint actually declares, which
  the delivered board did not honour.
- The square at 1201 mm2 beat the 1321 mm2 rectangle on **all four** measures
  in 9.1% less board, and the rectangle met its own >= 1000 mm2 effective floor
  only on r25 - its r20 was 904 mm2, below it.

Lesson: at a canonical binding, verify the outline's ASPECT is derived, not
just its area. And when a review names a mechanism, measure the mechanism -
this one was wrong while its conclusion was right.

## 2026-08-20 [P8][silk][render] Silk can pass check_silk AND real DRC while being invisible
Promoted from boards/PCB-0012-A_bb-ldo/LEARNINGS.md (promotion pass 2026-10-04).
A relocated refdes was placed in an open pocket that passed `check_silk` (0
violations) and `kc.py drc` (0 violations) - and was completely invisible in
the render, hidden under the through-hole connector's own body, because the
pocket sat inside the footprint's envelope. Both checkers reason about
geometric collision in 2D; neither has a concept of "underneath the part".

Only the render caught it. On any board with tall through-hole parts, a silk
placement is not verified until it has been LOOKED at.

Related, same board: `silk_place --refs J2` reported an EMPTY residual list
while leaving a refdes 0.33 mm from a neighbouring part's pads, reading as that
part's label. Its collision-only scoring treats "close to the wrong part but
not touching" as good enough - `check_silk`'s `silk_misattributed` is the check
that catches it, and it is not what `silk_place` optimises for.

## 2026-08-16 [P4][spice][sim-analyst] `.meas ac rms` is FREQUENCY-WEIGHTED, which is how you get integrated noise out of ngspice without `.noise`
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
ngspice has no way to report integrated noise into a measure - `.measure` is documented for
ac/dc/tran/sp only, and the `.noise` totals never reach the "Measurements for" block the
runner parses. The way through: inject the RTI noise DENSITY as a plain AC source, then
`.meas ac x rms vm(out) from=f1 to=f2`; total rms = x * sqrt(f2-f1). Verified on this host
against a closed-form RC integral (fc = 100 Hz over 1..10 kHz): analytic 0.124137, measured
0.124537 on `.ac lin` and 0.124539 on `.ac dec` - so the weighting is by frequency, not by
sweep point, and a decade sweep is fine. `integ` matches the same closed form to 5 digits
(528.834 vs 528.83). ONE source only: `.ac` superposes sources COHERENTLY, so two noise
sources add as voltages, not in quadrature - lump the whole RTI density into one and list
in the header what you left out and why. Second engine fact from the same session: a prior
measure's result IS available to `.meas <n> param='<prior> ...'` but NOT to
`.meas <n> when v(x)='<prior>-3'` - `when` values are substituted at parse time and the run
dies with "Undefined parameter". Hard-code the trigger level and say in the sidecar that a
wrong gain therefore shows up as a MISSING measure (still an error) rather than a bad one.

## 2026-08-16 [P4][spice][sim-analyst] Pad every .dc/.ac sweep: the FIRST point can converge to junk and `at=` on the LAST point errors out
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
On a 5-op-amp chain (three inside the AD8226 macromodel, two OPA2333 halves) ngspice 46
reported "Dynamic gmin stepping failed / True gmin stepping failed / source stepping failed"
at the first point of `.dc Vd -0.001 0.025` and returned a bogus operating point there:
`v(amp1)` read 0.1025 V (pinned to the model's output clamp) where the correct answer is
0.2123 V, while every later point - which converges from the previous solution - was exact.
At the other end, `at=0.025` on the last point of the same sweep failed with "out of
interval". Both vanish if the range is padded (`-0.002 .. 0.026`) so that no MEASURED point
is a sweep endpoint. Costs nothing, and without it a bench silently reports a wrong number
rather than failing. Two related traps found the same session: `.meas tran ... fall=last`
with no `from=/to=` scans the WHOLE run (a settling-time measure on the first step returned
the recovery edge of an overload 2 ms later - 2020 us instead of 23.6 us), and there is no
`i(x1.r2)` vector for a resistor inside a subcircuit - derive branch currents from node
voltages in a `param=` instead.

## 2026-08-16 [P4][spice][sim-analyst] A behavioural op-amp needs its anti-windup on the INTEGRATOR node, and its swing clamp AFTER ro - the datasheet swing number is already loaded
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
Two defects measured in one agent-authored generic op-amp macromodel, both of which produced
plausible-looking wrong answers rather than errors. (1) Clamping only the output buffer
leaves the R||C integrator node free: 1 ms of a +25 mV overload charged it to ~160 V at the
slew limit, and the bench then showed the amplifier NEVER recovering - a fake overload-
recovery failure. Clamp the integrator node itself (stiff diode pair to rail +- a small
anti-windup headroom) so recovery costs headroom/slew-rate. (2) Clamping ahead of the
open-loop output resistance and then dropping ro*Iload on top DOUBLE-COUNTS the load: a
datasheet "output swing 30 mV from the rail at RL = 10 k" is already a loaded figure, and
stacking a 1 kohm ro on it put the clip level at 3.152 V instead of ~3.27 V - inside the
gate window by 2 mV, i.e. it would have passed while being wrong. Put the swing clamp on
the output NODE and leave ro free to do its real job (AC output impedance and capacitive-load
stability). Corollary for the sidecar: state that the recovery TIME is then a property of
the anti-windup headroom you chose, not a datasheet number, and gate it as a warning.

## 2026-08-16 [P4][sim-analyst][inamp] Build the in-amp from its OWN published 3-op-amp structure and the REF-impedance defect becomes a gate failure instead of a review opinion
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
Modelling the AD8226 as its published topology - two preamps with 24.7k feedback around the
external RG, then a difference amplifier from four 50k resistors with REF at the end of one
of them - costs about fifteen lines and makes three separate things emergent rather than
asserted: the gain law G = 1 + 49.4k/RG comes from the resistors, internal Node 1 is a real
node you can probe (`v(x1.xu1.nd1)` works), and any impedance in series with REF reproduces
the datasheet's own 2*(50k+Rref)/(100k+Rref) uneven amplification exactly. That last one
turns "the REF buffer is not optional" from a design-review claim into a measured gate bound:
deleting the buffer and driving REF from the bare 9.24 k divider Thevenin moved the CMRR
bench from 0.134 mV to 17.06 mV over a 0.2 V common-mode sweep, 57x over its window, and
moved the reference node itself by 16.9 mV. Seed exactly that defect to prove the bound
before shipping it.

## 2026-08-16 [P4][sim-analyst][datasheet] A datasheet stability curve is taken AT A STATED GAIN - a unity-gain overshoot figure does not transfer to a stage running at higher noise gain
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
The error this board actually made, and the reason a part was designed in and then removed
one phase later. OPA2333 Figure 15 ("SMALL-SIGNAL OVERSHOOT vs LOAD CAPACITANCE") shows
overshoot reaching roughly the mid-30 % range by CL = 1 nF, and requirements allowed 1 nF of
output cable, so P3 added R6 = 100 R as an isolation resistor. What the figure does not say
on its face - its test conditions are not labelled on the page, only the page-level
"TA = +25C, VS = +5V" note - is that an overshoot-vs-CL curve is a UNITY-GAIN measurement,
the worst case for a voltage-feedback amplifier. The stage it was applied to runs at a noise
gain of 3.49, which moves the loop crossover from GBW to GBW/3.49 and is worth roughly 25
degrees of phase margin against the identical load. Measured in one bench: the same
calibrated macromodel gives 32.0 % for a plain unity buffer into 1 nF (reproducing Figure 15,
which is how the model was calibrated) and 6.6 % for the real stage into the same 1 nF with
NO isolation resistor. The part was never needed. Rules: (1) before designing a part in on
the strength of a capacitive-load, overshoot, phase-margin or settling curve, find the GAIN
the curve was taken at and re-ask the question at the gain you are actually running - and if
the datasheet does not label the figure, say so and treat unity gain as the assumption;
(2) a stability curve read at the wrong gain is not conservative, it is simply a different
circuit; (3) put the calibration instance IN the bench (here a unity buffer reproducing
Figure 15 alongside the real stage) so a reader can audit the transfer instead of trusting
it. Corollary already recorded above: an out-of-loop series resistor only isolates in
proportion to R/ro, so 100 R against a 1-2 kohm open-loop output impedance moved overshoot
by 0.3 to 1.3 points - it would not have done the job even if the job had existed.

## 2026-08-17 [P6][board_init][board_edit][build-modes] `outline_bbox` is a BBOX, not a size - and the "generous provisional room" auto picked was the wrong ASPECT, not the wrong area
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
`reports/board_init.json` records `"outline_bbox": [13.0, 13.0, 41.02, 57.69]` - min/max corners.
The P5 digest (and the P6 spawn brief that quoted it) both read that as "41.0 x 57.7 mm". The board
is 28.02 x 44.69 mm. That is not a rounding difference: it inverts which dimension is scarce. The
canonical layout for this block is a WIDE board (J1 on the left edge, J2 on the right edge, a two-IC
chain between them), and it does not fit in 28 mm of width at any packing density - while the AREA
auto chose (1252 mm2) is within 8 % of what the layout finally needed (1259 mm2 of content). Two
rules: (1) print `w x h` derived from the bbox, never the bbox corners, in any prose a later stage
reads; (2) at a `canonical` binding `--outline auto` is only sized, never shaped - check the aspect
against the block's own layout before treating the provisional room as roomy.

## 2026-08-17 [P6][board_edit][placement] `--outline fit` CLIPS to the CURRENT outline, so "place canonically, then fit" only works if you GROW the room first
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
`board_edit.content_bounds()` intersects the union of courtyards/copper/rule-areas with the current
outline before taking the bbox - deliberately, so fit can never invent a containment violation. The
consequence for the canonical flow is an ordering constraint nobody states: if the placement you want
extends past the provisional edge, `--outline fit` measures only the part that was inside and returns
a SMALLER board, silently. The working order is grow (`--outline WxH`, generous in both axes) ->
place -> `--outline fit`. `--outline WxH` also refuses while the CURRENT placement has anything
outside the NEW rect, so the grow rect has to cover the union of the old and new placements, or the
placement has to be applied first. Companion fact: fit is idempotent (re-running with the same margin
reproduces the same rect), so the orchestrator's own post-gate fit is a no-op confirmation.

## 2026-08-17 [P6][P4][placement][diff-pair][inamp] A connector's pole ORDER against the IC's pin order forces exactly one crossing on the input pair - and it is a free schematic fix, measured both ways
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
bb-amp's J1 was drawn (IN+, IN-, GND) on poles 1/2/3 while the AD8226's inputs run (-IN, RG, RG,
+IN) on pins 1..4. Those two orders are reversed, so /IN_P and /IN_N must swap exactly once between
connector and amplifier. This is TOPOLOGICAL, not a placement failure: rotation preserves orientation,
so I enumerated all eight combinations (J1 on each of four edges x U1's input face toward it) and
every one interleaves. Rotating BOTH parts mirrors both orders and preserves the interleave; only a
reflection of one of them fixes it, and a front-side SMD part cannot be reflected.

Measured, same placement, only the two pad-net assignments changed (J1.1 <-> J1.2):
  before   /IN_P, /IN_N HPWL 15.23 mm each, board HPWL 173.45, crossings_signal 6 (incl. IN_N x IN_P)
  after    /IN_P, /IN_N HPWL 11.43 mm each, board HPWL 165.85, crossings_signal 5 (pair gone)
  geometry start dy -/+2.540 -> end -/+1.900, dx 10.790, euclid 10.8090 on BOTH legs, delta 0.000000
  routing  two straight F.Cu segments at the netclass width, ZERO vias, kicad-cli --severity-all
           reports 0 non-unconnected findings and 0 remaining unconnected on either input net
Before the swap the same placement needed a via pair on one leg (~0.6 pF of imbalance, ~-123 dB at
1 kHz by in-input-path-equivalence's 1.1 ppm/pF) plus a slot in the B.Cu reference under the input
region, which blocks.md section 5 item 6 forbids.

Rules: (1) at P4, order a multi-pole input terminal to MATCH the amplifier's pin order along the
facing direction, not to match the schematic's reading order - it costs nothing and it is the
difference between a zero-via pair and a pour slot under the input; (2) P6 cannot fix it, so a
placement agent that finds an unavoidable pair crossing should report the pole swap rather than
spend vias on it; (3) a `board_update` will NOT carry a pad-net rewire (it refuses by design) - the
fix costs a P5 re-init, so it is cheapest caught at P4 review.

## 2026-08-17 [P2][P5][P7][P8][constraints][diff-pair][orchestrator] `diff_pairs` conflates "route these symmetrically" with "control these to an impedance", and every consumer assumes the second
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
Four sightings on ONE board, each in a different phase and each looking like an
unrelated problem:

- **P2** - the `interface:in` coverage slot is typed from `diff_pairs[].base`, so it
  inherited `gap_mm` / `max_skew_mm` / `max_uncoupled_mm`. None of the interface
  records' envelopes could be tested against geometric dims, so all five returned
  `unknown` and could only ever read `provisional`. Fixed by retyping the slot's
  `operating_point` with electrical dims (`rsource_ohm`, `vcm_v`, `ibias_ua`, `dt_c`...).
- **P5** - `rules_gen.py:166` is `dp.get("impedance_ohm", 90)`, and the diff-pair
  NETCLASS WIDTH is solved from that number. A pair with no impedance declared got a
  **1.3743 mm** trace width on a board with no controlled impedance anywhere. The router
  traces at class width, so this ships silently.
- **P7** - `route_critical --only diff` drives `route_diff.py`, which REQUIRES the pair to
  emerge coupled at the declared gap and errors otherwise. Running it would have converged
  both legs onto the mirror axis and into R1's pads.
- **P8** - `check_diffpair` failed the verify gate demanding both legs stay within 0.75 mm
  of each other, flagging all 10.81 mm of a deliberately mirror-symmetric pair as
  "uncoupled".

**The rule.** A low-frequency differential pair needs EQUALITY (length, width, via count,
neighbourhood, capacitance to the reference), not COUPLING. Coupling is an
impedance-control mechanism, and forcing it on a precision DC pair actively destroys the
symmetry it looks like it is protecting.

**What to do until the schema grows a `symmetry_only` flag:** declare `impedance_ohm` as
the impedance the pair ACTUALLY HAS at the width you want (solve it backwards from
`lib/impedance.py` against the real stackup), set `max_uncoupled_mm` past the run length,
and put a `_note` key in the JSON saying both are consequences rather than targets. Fix
the premise, never waive the symptom - a waiver leaves the wrong number in the file for
the next reader.

## 2026-08-17 [P6][P4][process][orchestrator] A late pin change must sweep every artifact that STATES the pin order, not just the ones a gate reads
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
The P6 J1 pole swap propagated correctly into the netlist, the schematic, the board and
the P6 digest - every artifact a gate consumes. It did NOT reach `requirements.md`
(sections 2 and 9a), `architecture/blocks.md` (B1 and the block diagram), or the
design-doc PDF already delivered at H1. Those are exactly the artifacts a HUMAN reads.

The board then shipped to P8 review with **no connector legend at all** and **every
document stating the opposite pole order**. On a hand-wired board that is a destroyed-IC
path: J3 reversed puts -3.3 V on both supply pins.

No gate catches either half. ERC, DRC, verify, DFM and the netlist audit are all blind to
a missing silkscreen legend and to a stale sentence in a markdown file. Only a
fresh-context reviewer looking at the RENDER found it.

**Rule:** when a pin or pole assignment changes after P4, grep the whole workspace for the
old order and fix every hit, then regenerate the design doc. Treat "which artifacts state
this fact" as part of the change, not as documentation cleanup afterwards.

## 2026-08-17 [P4][kicad][schematic][render] KiCad DRAWS text-box overflow instead of clipping it, and no gate sees it
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
Hit twice on this board while adding notes to the equations box. When a text box's content
outgrows its border, KiCad renders the overflow outside the frame - across the sheet and
into the margin - while ERC and `netlist_audit` both stay clean, because neither looks at
graphics.

Both times it was caught only by measuring the rendered line pitch off the plotted PDF
(~1.45x font size per line) and re-reading the render. **Any generated schematic text
needs a render check, not a gate run.**

## 2026-08-20 [P9][research][knowledge][second-reader] Envelope authoring, not citation accuracy, is where research records fail - and a bad envelope runs BACKWARDS
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
Six draft records were re-read blind by fresh readers on 2026-08-20. All six refuted, and
the failure was concentrated: **transcription was near word-perfect in all six** and page
numbers were right in four. What broke them was the `envelope`, in three of the six - and
each of those three excluded **exactly the regime the rule was written for**:

- `inamp-gain-pin-and-input-node-parasitics` - `f_signal_hz max 5000` on an error the
  record's own mechanism says GROWS with frequency. `envelope_contains` therefore returns
  `outside` for precisely the designs where the effect is worst.
- `in-leakage-symmetry-and-guarding` - capped at `1 Mohm` while the record's own guard
  regime STARTS at 1 Mohm.
- `in-bias-return-sizing` - capped `rsource_ohm` at 10 k while its own rule prescribes the
  balanced bleeder pair for HIGHER source impedance.

**The test that catches all three** is the U14 ruling already in `knowledgelib`: an
envelope bounds *where the rule stops being TRUE*, never *the numbers the rule happens to
carry*. All three had silently bounded the numbers - two of them the rows of a spec table,
one an example value. A useful second question: **does the envelope's edge sit where the
mechanism is weakest or strongest?** If the cap is where the effect is strongest, it is
backwards.

Corollary found the same day: a worked number quoted out of its stated test condition is
the other recurring defect. `in-path-symmetry-sets-cmrr` quotes SBOA582's 40 dB case
without its `Gain = 1 V/V` condition from Fig 3-2 - at the board's own G ~ 150 the same
formula gives 77.5 dB. Same family as the Figure-15 unity-gain error from P4: **a vendor
number carries its test condition, and dropping the condition always flatters the claim.**

## 2026-08-20 [P9][research][knowledge][process] A blind re-read is worth running even when you expect it to confirm - it prices the repair
Promoted from boards/PCB-0013-A_bb-amp/LEARNINGS.md (promotion pass 2026-10-04).
Re-reading six already-refuted records looked like it could only reproduce a known answer.
It did reproduce it (0 verified of 6, matching the earlier pass on every record, reached
without being told the prior verdicts) - but the run paid for itself three ways:

1. **A new concrete error** nobody had: the 40 dB / Gain = 1 V/V condition above.
2. **A sharper witness line.** The first pass ruled that INA333-based bias-return SIZING
   does not transfer to an AD8226 board at all. The re-read split it properly: the LAW
   (Ib x R against the error budget) transfers as a general in-amp principle; only the
   47k/10k NUMBERS, which are 70 pA CMOS values, do not. That distinction is what makes
   the record repairable instead of dead.
3. **A repair price per record.** Five of six are fixable from sources already in their
   ledgers with no fetch; one (`in-aggressor-separation`'s unbroken-reference rule) needs a
   plane-continuity source nobody has, and the reader proved that by checking the
   uncited pages too - the app note's own reference design splits its plane.

Blind matters: a reader told the prior verdict tends to re-derive it. A reader told only
"both outcomes are expected" produces evidence you can actually compare.

## 2026-08-16 [P0][modes][check_requirements][pipeline] A blockquoted brief silently disables the ENTIRE U18 mode leg of check_requirements.py
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
`modeslib.detect` skips lines beginning with `>` and lets the first PLAIN line of the
brief decide the token. Write the owner's brief as a markdown blockquote - the obvious
way to mark "these are their words, verbatim" - and `detect` returns `None`. The
consequence is not a failure but a silence: `req_mode_unnamed` and
`req_mode_unmarked_size` can never fire, so a requirements.md that names no mode and
states an unmarked HARD size passes the lint clean. What you get instead is a
`req_mode_stray` WARNING whose text points the wrong way ("section 1 names a build mode
but no brief/ file opens with a mode token"), which reads as a cosmetic complaint about
section 1 rather than as "your mode checks are all switched off".

Verified on this run: with the brief blockquoted, exit 0 / status pass / one stray
warning; with the token as the first plain line, exit 0 / 0 violations and
`brief_token` populated in the report. Both are exit 0 - the difference is only visible
in the report body, which is why it can pass unnoticed.

The mechanical half of U18 is NOT affected: `state.py mode` records the resolved mode
into state.json independently, so `board_init`'s refusal of a fixed `--outline` still
bites. Only the lint goes blind.

Fix here: the brief's first plain line IS the token line; provenance ("owner brief,
verbatim, received <date>") goes BELOW a rule, not above the token. Sibling workspace
bb-mcu had the same shape at the time of writing.

## 2026-08-16 [P1][parts][adc][jlc] JLC's "External" reference attribute lies for 8-pin MCP3202: VDD and VREF are ONE physical pin
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
Scouting 12-bit SPI SARs with a separate reference input, the MCP3202 ranks first on
every catalog axis that parts_search can see - better stock (1347) and lower price
($2.06) than the MCP3201 - and its JLC attribute row claims an External reference.
It is disqualified anyway: in the 8-pin package MCP3202 bonds VDD and VREF to a single
pin, so the reference cannot be set below the supply. On a board whose whole accuracy
argument rests on an external 2.5 V-class reference, that silently turns the design
ratiometric to the host's +/-5 % rail.

Only the datasheet (DS21290F) shows it; the catalog attribute does not. The general
form: for a converter, "has an external reference" is a PIN-MAP question, and the pin
map lives in the datasheet's package section, not in a distributor attribute. Check it
per package variant, not per part family - the 8-pin and 14-pin members of one family
differ exactly here.

Second finding from the same sweep: of the SARs the scout surfaced, every one with
16-bit-class accuracy (ADS8318 / ADS8681 / ADS8688) requires >=4.5 V AVDD.
**CORRECTED at P2 - do not carry the generalisation.** The architect found ADS8326
(16-bit, 2.7-5.5 V supply, external VREF, +/-2.5 LSB INL max) in stock, which
falsifies "nothing that accurate runs natively at 3.3 V". The true statement is
narrower: the 16-bit parts with the WIDEST input ranges and on-chip attenuators need
>=4.5 V, because the attenuator needs the headroom - not 16-bit accuracy as such.
A one-sweep absence is evidence about the sweep, not about the catalogue.

## 2026-08-16 [P2][parts][adc][accuracy] A 12-bit part's "+/-5 LSB gain error" is a BIGGER voltage than a 16-bit part's, and ranking SARs on INL hides it
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
P1 ranked 12-bit SARs and reported INL per candidate, which is the spec everyone
quotes. MCP3201-B's own DS21290F also specifies **gain error +/-5 LSB MAX and offset
+/-3 LSB**, and at 12 bits over a 5 V full scale one LSB is 1.22 mV - so the gain
term alone is 6.10 mV, **1.22x the entire +/-5 mV error budget** of this board. The
part was the P1 lead candidate and would have failed the board's headline spec on a
datasheet number nobody had read.

The mechanism generalises and is counter-intuitive: DC error specs denominated in
LSB shrink 16x in VOLTAGE going from 12-bit to 16-bit at the same full scale. So a
16-bit converter quoting "+/-16 LSB gain error" is FOUR TIMES BETTER in volts than a
12-bit one quoting "+/-5 LSB". Resolution and accuracy move together in the spec
table, in the opposite direction to the intuition that a coarser part has an easier
job. "12-bit or better" in a brief is a resolution floor; it says nothing about the
DC error, and on an accuracy-driven board the higher-resolution part is often the
cheaper way to buy accuracy.

Selection rule to carry: for a converter on a DC-accuracy board, rank on OFFSET,
GAIN and INL **as maxima, converted to millivolts at the terminal** - never on INL
alone, and never in LSB. Parts that specify only typicals for offset and gain are
disqualified by that alone: an unbounded term cannot enter an error budget.

## 2026-08-16 [P1][parts][jlc][sourcing] One LCSC search term returns six clone brands under a precision part's MPN pattern, and a clone's offset spec can be 300x looser
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
Searching JLC for an AD8605-pattern precision op-amp returns the genuine
Analog-Devices row alongside six clone-brand rows sharing the same MPN pattern and
pinout. Pulling one clone's own datasheet (brand TECH PUBLIC) gives 5 mV MAX input
offset voltage against the genuine part's reputation of tens of microvolts - 30 to
300x looser, on a part number a BOM would treat as equivalent.

On an 0.1 %-class board that single number IS the whole budget: 5 mV of offset
against a +/-5 mV total error target at 25 C. The failure mode is the bad kind - the
board assembles, powers up, converts, and is simply wrong by a percent, with a BOM
line that reads like the right part.

Generalises past this part: for any part whose VALUE is a precision spec (offset,
tempco, initial accuracy, ratio match), the LCSC brand column is a selection
criterion, not metadata. Pin the manufacturer, not just the MPN, and pin it in
parts.json so P3 cannot silently resolve to a cheaper row. Catalog attributes are
not a substitute - they are frequently copied from the original part's datasheet.

## 2026-08-16 [P1][P2][research][env] analog.com and mouser.com time out from this sandbox; farnell.com is the working ADI-datasheet mirror and is already allowlisted
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
Four independent agents this session failed to reach `analog.com`, and two failed
on `www.mouser.com/datasheet/...`. Both are on the fetch allowlist, so the failure
is network reachability from this host, not policy. That matters because ADI now
owns Linear and Maxim, so a large share of precision-analog primary material -
references, precision amplifiers, matched networks, the MT-series notes - is behind
that one unreachable host.

The workaround that WORKED, without touching the allowlist: `www.farnell.com/
datasheets/<id>.pdf` serves ADI datasheets and is already an allowlisted domain.
That is how the ADR4520 dropout question was settled. Caveat found in the same
pass: the Farnell mirror served **Rev 0 (2012)** while current ADI/LCSC copies are
a later revision, so a mirror is good enough to SETTLE a question and not good
enough to COMMIT a part - re-confirm the load-bearing row against the current
revision before ordering.

Also refused, and correctly: `www.lcsc.com/datasheet/<C-id>.pdf` is an HTML viewer
shell, not a PDF; research.py fetch rejects it under `--expect pdf`. The wmsc.lcsc
stem is the real PDF path.

Practical ordering for a researcher on this host: ti.com first (fast, deep, and
its cross-vendor app notes cover most analog topologies), vishay.com for passives,
farnell.com as the ADI mirror, and do not spend depth-cap attempts on analog.com.

## 2026-08-16 [P2][agents][parallel][pipeline] The session scratchpad is shared across concurrently running agents and its filenames are not collision-safe
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
A researcher writing a PDF crop helper to the session scratchpad as `crop.py` had it
OVERWRITTEN mid-run by a sibling agent doing the same thing under the same obvious
name. No damage this time - the first agent had finished cropping - but the failure
mode is silent and ugly: agent A's script is replaced by agent B's between A writing
it and A running it, so A executes B's code and reports results for the wrong input.

The scratchpad path is per-SESSION, not per-agent, and a full-run spawns many agents
concurrently. Obvious names collide precisely because they are obvious: `crop.py`,
`tmp.json`, `extract.py`, `out.pdf`. Nothing in the harness namespaces them.

Two habits fix it, and orchestrators should put one of them in the spawn prompt when
running agents in parallel: give every scratch file a name unique to the agent's task
(`crop-<task-id>.py`), or have each agent create and work inside its own subdirectory
of the scratchpad. Prefer the subdirectory - it also keeps a failed run's debris
identifiable afterwards.

Worth noting the same hazard is already handled correctly one level up: research
outputs are workspace-first (`<ws>/research/...`) and gate commits are workspace-
scoped, which is why four concurrent researchers could not corrupt each other's
records. Only the scratchpad is unprotected.

## 2026-08-16 [P3][parts][jlc][sourcing] LCSC's own datasheet URL can resolve to a "Datasheet temporarily unavailable" stub, and parts_search does not filter that pattern
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
part resolved to a stub page reading "Datasheet temporarily unavailable" rather than
to a PDF. `parts_search.py` already drops JLC placeholder ROWS (no-brand, no-datasheet,
~$0.04 entries), but a real, branded, in-stock part whose datasheet LINK is a stub
passes through as fully sourced. The role's own rule - "no fetchable datasheet =
unverified" - then bites at extraction time, one phase later than it should.

The recovery that worked: the genuine document was reachable through an independent
distributor mirror (`docs.rs-online.com`), cross-checked for page count and file size
against a third listing before being trusted. Two mirrors agreeing on a 32-page,
~1 MB file is decent evidence you have the real document.

Two things fell out of the same episode, both worth more than the stub itself:

**Do not trust an AI web-search summary for a datasheet revision.** Two separate
search summaries confidently asserted this part was at "Rev G, 05/15/2024" and
"Revision B". Direct inspection of the PDF shows **Rev 0, 4/12** - and that Rev 0 IS
the current revision, still in distribution fourteen years on. A revision claim is
exactly the kind of fact that reads as trivially checkable and is therefore rarely
checked; here it decided whether an 87 mV headroom margin was still valid.

**Reaching a document through a mirror is not the same as knowing it is current.**
The first pass settled the spec from a mirror and correctly logged "Rev 0 (2012) -
re-confirm against the current revision before BOM release". Closing that item needed
a SECOND, independent channel, not a re-read of the same file. The distinction between
"this is what the document says" and "this document is current" has to be carried
explicitly, because the first is cheap and the second is not.

## 2026-08-17 [P4][schlib][python] `place_ic_with_decoupling`'s default `caps_dx` is too small for this library's 2-pin symbols, and it fails as a confusing label-guard error
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
The default `caps_dx=12.7` puts adjacent decoupling caps 12.7 mm apart. Every 2-pin
symbol pulled into `lib/aiee.kicad_sym` has its pins at +/-5.08 mm from the anchor
(measured: R0805, C0402, C0603, C0805, C1210 all +/-5.08; the 1 nF C0603 alone is
+/-3.81), so a cap's pin-2 stub ends 7.62 mm right of its anchor and the NEXT cap's
pin-1 label anchor lands 7.62 mm left of its own - i.e. exactly on the neighbour's
stub wire. Generation then dies with

    ValueError: label 'VDD_ADC' at (132.08, 114.3) lands on an existing wire run
    (132.08, 114.3)-(134.62, 114.3) - it would merge nets

which names the LABEL, not the spacing, and sends you looking at the net contract.
The guard is doing its job - two nets really would have merged - but the fix is
`caps_dx=25.40`, which is what bb-buck already passes explicitly on every call.
Rule for this library: never take the default; 25.40 mm is the working minimum for a
row of these caps, and the failure is at build time, not silent.

## 2026-08-17 [P4][schematic][erc] KiCad 10.0.3 ERC is indifferent to `no_connect`-typed pins - marker or no marker - so the NC marker is documentation, not compliance
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
The ADR4520 carries four NIC pins plus a factory test pin, all hand-typed `no_connect`
in the project library (a P3 edit that must survive any re-pull, decision 69a).
Probed both ways on a one-part scratch sheet through `kc.py erc`: five NC markers ->
0 errors / 0 warnings; the same five pins left completely alone -> also 0/0. So the
ERC gate cannot tell "deliberately unconnected" from "forgotten", and neither reading
is enforced by the tool. The markers are placed anyway, because the only place the
datasheet's "Do not connect" can live in the artifact is the artifact.

Same session, second cosmetic fact with a real consequence: `schem_refdes` does not
rotate field text but KiCad does (LEARNINGS root, 2026-08-09), and on a rot-90 symbol
that lands the Value as a VERTICAL string. A 27-character Value ("2-pos 5.08mm screw
terminal") on a connector near the left border then runs clean off an A4 sheet - no
warning, `place_report.residue` still empty, visible only in the rendered PDF. J1 got
the rotation (it separates two otherwise-overprinted net labels) and a short MPN Value
to pay for it.

## 2026-08-17 [P4][schematic][analog] schlib guards labels-landing-on-wires; a hand-drawn wire needs the MIRROR check, and on this board that guard is the only thing between a correct netlist and a silently wrong one
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
`Sheet._assert_label_clear` rejects a LABEL whose anchor lands on an existing wire run.
Nothing checks the other direction, and this board needs the other direction: `U1`'s
-IN is a dedicated sense run drawn as an explicit multi-segment wire to `R5`'s bottom
pad, carrying no label of its own, so the geometry IS the connection. A segment
crossing any foreign label anchor or pin would merge the sense net into whatever it
touched, and every downstream gate would still pass - ERC sees one net either way,
`netlist_audit` sees `U1.3` on `GND` either way, and both readings are electrically
"correct" because there really is one net.

`gen/root.py` therefore ships `sense_run()`, which builds the path and asserts each
segment clear of every label anchor (recomputed from `Sheet._pin_nets`) and every pin
of every placed component, endpoints excepted. It is also called BEFORE the remaining
labels are placed, so schlib's own guard covers everything that follows: the two
directions together are complete. Verified after the build by dumping the exported net
memberships (14 real nets, `U1.3` and `R5.2` together on `GND` and nothing else joined)
and by asserting all four wire segments are present in the saved file.

## 2026-08-18 [P4][schematic][analog][layout] SUPERSEDES the 2026-08-17 sense-run entry: a remote sense must be its OWN NET with a single-point tie PART - "electrically one net" is the wrong unit of analysis once a pour exists
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
The 2026-08-17 entry above reasoned that `U1` -IN and the attenuator's bottom node are
electrically one net, so the schematic's job was the WIRE and `constraints.json`'s
`R5` -> `U1` corridor would carry the Kelvin intent into copper. **Both halves are
wrong, and the adversarial review (E1) found it.**

1. **"One net" stops being true the moment a plane is poured.** On the netlist `U1.3`
   was one of SIXTEEN `GND` nodes, indistinguishable from `U1.4`. `planes_gen` connects
   every node of the pour's net, so pin 3 gets a thermal to the B.Cu pour at the
   converter exactly as pin 4 does - and the measurement is then referenced to the pour
   AT THE CONVERTER, which is the error the sense exists to cancel. The drawn wire does
   not survive netlist export; only nets do.
2. **A corridor is a KEEP-CLEAR SWATH, not a connectivity rule.** It reserves area. It
   cannot stop a pour from tying a node, and no gate reads it as if it could.

The fix that works is structural: `/AGND_SENSE` as its own net with exactly three nodes
(`U1.3`, `R5.2`, `R8.1`) and `R8`, a 0 ohm link, as the ONE tie to `GND` at the string
bottom. The pour cannot swallow a net that is not `GND`, so the isolation is now
enforced by connectivity rather than by intent. Residual after the split:
`K*(V_sig - V_S) + Vos`, where the only error term is the J1-return-to-string-bottom
pour offset, input-referred at UNITY and carrying only the string's own 5 uA - sub-1 uV.
`R8` adds ~0.25 uV, leaving -IN six orders inside the -0.3/+0.5 V window.

**The transferable rule: any Kelvin / remote-sense / star-point node needs a NET of its
own and a physical single-point tie (0 ohm link or a deliberate junction), never a
shared ground net plus a placement hint.** Cost here was one Basic 0402-class part from
the same reel family as R6/R7. Corollary worth its own line: `netlist_audit`'s
`_constraint_nets` covers `high_speed`, `power`, `voltages`, `thermal` and `diff_pairs`
- it does NOT read `placement.corridors[].net`, so a corridor naming a net that does not
exist passes silently. Sheets.md s6's "an entry naming a net that does not exist fails
silently" applies to corridors with no gate behind it; check it by hand at P4.

## 2026-08-18 [P4][parts][lib_pull][erc] A freshly pulled symbol comes back with `input` pins into a library whose other symbols were already retyped - and the whole-library fixer would undo a hand-edit
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
Adding `R8` mid-P4 meant one new `lib_pull.py --lcsc C21189` into a library that P3 had
already put through `lib_pin_types.py` plus a hand-edit. Two things fell out.

**The pull is untyped.** `0603WAF0000T5E` arrived with BOTH pins typed `input` while its
siblings `0603WAF499JT5E` / `0603WAF100JT5E` (same family, same reel, pulled at P3) were
`passive`. Two `input` pins on a passive net is an ERC error waiting to happen, and the
difference is invisible unless you dump pin types - `schlib.py --pins` reports it.

**The whole-library fixer is not the answer.** `lib_pin_types.py` takes `--lib` and no
symbol filter, and it has no NC concept: running it would silently revert `U2`'s five
hand-set `no_connect` pins to `passive` (dry-run-confirmed at P3, decision 69a). So the
repair was a SCOPED text edit - `(pin input line` -> `(pin passive line` inside that one
symbol's block only, with an assert on the expected count - and both facts were verified
afterwards: the new symbol reads `passive`/`passive`, `ADR4520BRZ-R7` still reads
`no_connect` on 1/3/5/7/8. Rule: after any incremental `lib_pull` into an
already-corrected library, diff the pin types of the NEW symbol only, and repair it
scoped. `--overwrite` would have been worse - it re-pulls the parts whose types are the
hand-corrected ones.

## 2026-08-19 [P8][sim][ngspice] `.meas ... param='...'` rejects {braces} - and the measure vanishes with the run still reporting ok
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
Machine-verified on ngspice 46 through `sim_run.py`. Inside a measure's `param='...'`
expression, `{param_name}` is a hard syntax error - stderr carries `Syntax error:
letter [{]`, `Expression err: ...`, `Cannot compute substitute` - and the measure is
simply ABSENT from the returned dict. `run_circuit()` still returns `status: ok`, so a
deck can lose a third of its measures and look healthy; only counting the measures you
expected catches it. The rules, all probed:

- `param='(vpk-vfin)/vstep*100'` with BARE `.param` names: WORKS.
- `param='(vpk-vfin)/{vstep}*100'`: dropped.
- `AT={t0+tacq}` and `WHEN v(x)={vhi-15.6e-6}`: braces WORK in these clauses.
- a measure RESULT inside a braced clause, e.g. `WHEN v(o1)={vhi+0.5*(vpk1-vfin1)}`:
  rejects the whole circuit (`NameError` from the parser) - braced clauses are resolved
  at parse time, before any measure has a value.

So: braces in `AT=`/`WHEN=`, bare names in `param=`, never a measure result in braces.
The useful corollary is that `param=` over bare `.param` names lets a bounds sidecar gate
a FITTED VALUE directly (`cbulk_uf param='(c5+c3)*1e6'` against the ADR4520's two-ended
1-100 uF window) or a closed-form limit (`qdyn/cbulk*1e6`), with no solver in the path.

## 2026-08-19 [P8][sim][ngspice][opamp] Mixing clamped and unclamped output stages in ONE .dc deck silently corrupts the OTHER instances
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
`zero-scale-swing` put three copies of the same op-amp boundary model in one `.dc` sweep:
two with `V- = GND` (so at zero scale the output sits exactly ON the `min(max(...))`
rail clamp, where the derivative is zero) and one with `V- = -0.3 V` (TI's contingency
generator, never clamped). The operating point then falls back through `Dynamic gmin
stepping failed` / `True gmin stepping failed` / `source stepping failed` - 63 to 99 of
them across the sweep - and ngspice returns `status: ok` with values that are WRONG FOR
THE INSTANCES THAT WERE FINE ON THEIR OWN: the as-built buffer read a flat ~5 uV across
the whole bottom decade instead of tracking its input, which reads exactly like a real
zero-scale failure. Each instance converges cleanly in isolation.

Two rules. (1) A rail-clamped behavioural output stage and an unclamped one do not share
a DC matrix - sweep the rail between RUNS (it is a `.param`, and that is also how the
seeded defect is applied) rather than between instances. (2) `Warning: ... stepping
failed` on stderr is not cosmetic in a DC bench; treat any of them as invalidating the
whole sweep, because the wrong numbers arrive silently and plausibly. The cheap check
that caught it: run the deck at three `reltol` settings decades apart - real physics is
identical to 5 digits (verified so for every settling number in `acquisition-settling`),
a fallback-corrupted solve is not.

## 2026-08-18 [P8][sim][analog] Seeding a REJECTED PART as the deliberate defect turns a bench into an independent audit of an earlier decision
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
Every bench on this board was calibrated by mutating one value to the defect it exists
to catch, confirming the bound trips, and reverting. For the settling bench the obvious
mutation was a wrong resistor - but substituting the **rejected amplifier** was
available for free, and it did something a value-mutation cannot.

The OPA333 had been rejected at P2/P3 on three arguments assembled from datasheet
numbers: a GBW floor of 48/(2*pi*t_acq) = 849 kHz against its 350 kHz, its own measured
40.4 us settling curve, and an isolation-resistor interval that looked empty (that third
argument was later WITHDRAWN as an overreach - the vendor equation gives an optimum, not
a maximum). Dropping its model into the settling bench moved the settling error from
**0.136 uV to 4509 uV** - a 33,000x miss of a 15.6 uV budget.

Why this is worth more than an ordinary seeded defect: the rejection had been argued
across two phases, partly on a leg that turned out to be wrong, and re-derived from a
different datasheet's timing section. A defect seed that reproduces a REAL earlier
decision closes the loop on it with a different method entirely - simulation rather than
datasheet arithmetic - and would have flagged a rejection made for bad reasons.

Generalise: when a bench needs a seeded defect and the project has already REJECTED a
candidate for that exact role, seed the rejected candidate. It costs nothing extra, it
proves the bound discriminates on the axis that actually decided the design, and it
audits a decision that otherwise never gets re-tested.

## 2026-08-19 [P7][stitch_vias][drc][kicad] stitch_vias' hole-to-hole model is 0.3 mm too permissive - KiCad measures hole EDGE to hole EDGE, so the tool ships DRC errors it cannot see
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
stitch_vias' docstring pins its spacing model as "EDGE_GAP = 0.5 - 0.3 = 0.2 mm - identical
to the S11-verified 0.5 mm CENTRE floor for two standard 0.3-drill vias". That equivalence
is wrong, and it is wrong in the unsafe direction.

THE ARITHMETIC. KiCad 10 measures `hole_to_hole` between hole EDGES, so what the rule sees is

    measured = centre_spacing - drill_diameter        (both drills 0.30 mm)

On this board stitch_vias proposed a GND via at (41.85, 39.85) for U3.2 while an existing
0.6/0.3 GND via sat at (41.60, 40.40): centre spacing sqrt(0.25^2 + 0.55^2) = **0.604 mm**,
comfortably past its own 0.5 mm model floor. Applied to a scratch copy of the finished board,
that exact spacing produces a hard error:

    error hole_to_hole | Drilled hole too close to other hole
                       | (rule 'aiee_hole_to_hole_floor' min 0.4995 mm; actual 0.3042 mm)

0.604 - 0.30 = 0.304, matching the reported 0.3042 exactly. So a 0.5 mm hole_to_hole rule needs
**0.8 mm centre-to-centre** for two 0.3-drill vias, not 0.5 mm: everything stitch_vias places in
the (0.5, 0.8) mm band next to an existing same-net via is a violation its own check passes and
the gate then fails on. The band widens with drill size - the floor is always
`rule + drill_a/2 + drill_b/2` centre-to-centre.

SECOND TRAP, same op. `already_stitched` is a **0.2 mm pad-centre-to-via proximity test**, so a
pad properly connected by a short track to a via 0.9 mm away is reported as needing stitching at
all. 11 of 12 GND SMD pads here were correctly skipped; the 12th was electrically stitched
already and still generated a proposal.

MITIGATION. Always run `--dry-run` first and treat every proposal as a candidate **LOCATION**,
not as an addition to apply. Measure each proposed `at` against the existing same-net vias with
the arithmetic above before writing anything. On this board the proposed spot was directly under
the pad and strictly better than what was there, so the right answer was not to add the via but
to **REPLACE** the existing one - and the jog track that had forced the offset via disappeared
with it, shortening the op-amp's V- return and leaving more guard copper inside the ring. A
proposal that is illegal as an addition is often correct as a relocation.

## 2026-08-19 [P7][planes_gen][guard-ring][analog] A driven guard ring is a planes_gen zone with connect:solid - and closure is a provable geometric property, not an eyeball
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
The /AIN_DIV tap on this board (~240 kohm Thevenin) needs a guard ring poured on
/AIN_BUF, the buffer OUTPUT - same potential, low impedance (record
`resistive-attenuator-high-z-tap-guard-and-leakage`). Two things made it buildable
without hand-drawing a trace loop:

1. **planes_gen builds it.** One entry in a `planes` sidecar -
   `{"net":"/AIN_BUF","layer":"F.Cu","region":[40.40,37.90,45.20,43.20],"priority":1,
   "connect":"solid","min_width":0.25}` - and KiCad's own filler forms the ring, because
   the fill flows around every foreign pad at clearance and merges with the same-net pad
   it must be driven from. `connect: solid` is load-bearing: with the default thermal
   relief the fill does NOT merge with U3.1's pad and the ring never closes through it.
   Gotcha: planes_gen REJECTS unknown keys on a `planes` entry - a `_why` string, which
   every other constraints.json section carries, is a hard `CheckError: planes[0]: unknown
   keys ['_why']`. Park the rationale in a sibling key on the sidecar root.

2. **The region is chosen from the CHANNELS, not from the node.** Copper 0.127 wide plus
   2 x 0.127 clearance needs 0.381 mm, so the 0.35 mm SOT-23-5 pin1/pin2 and pin2/pin3
   channels cannot carry the ring at all - the closed ring has to encircle the V- pin
   TOGETHER with the +IN pin, and V- escapes straight down through its own via. The four
   legs run through the channels that DO fit: the 1.5 mm inter-row channel under the
   package (north), the 0.87 mm channels under the two adjacent 0805 bodies (west/east),
   and open board south of the string.

3. **Closure is a test, not a judgement.** Union the net's F.Cu copper (fill + pads +
   tracks); the ring is closed iff that union is ONE polygon carrying an interior ring
   (a hole) that `contains()` the whole guarded net's copper. Here: 1 polygon, 1 hole,
   `contains(/AIN_DIV) == True`, minimum ring copper width 0.612 mm, guard-to-node gap
   exactly 0.127 mm (the DRU floor - the guard sits as tight as it is legal to sit).
   Re-run it after EVERY refill: route_auto, route_edit + refill and plane_repair all
   restate the fill, and a ring that closed before the autoroute is not evidence about
   the ring that ships.

Anything placed inside the ring must exist BEFORE the pour, because KiCad's filler leaves
no room for a via added later: the V- escape via and both /AIN_DIV traces were laid with
route_edit first, and the fill formed around them.

## 2026-08-19 [P8][check_return_path][geom][kicad] `--verify-fill` is broken by a missing `.kicad_dru`, NOT by a fill-model disagreement - and it mis-measures every ai-ee board
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
`check_return_path --verify-fill` dies on this board with

    StaleFillError: zone 0 on F.Cu: committed fill 17.176 mm^2 differs from fresh 6.613 mm^2 (> 1%)

The tempting reading - "KiCad's filler disagrees with the checker's own independent fill
model, so a `connect: solid` zone cannot be verified" - is WRONG on both halves. There is no
independent model: `geom.BoardGeom.assert_fresh(refill=True)` calls `_refill_copy()`, which runs
the real `kicad-cli pcb drc --refill-zones --save-board` on a temp copy. Both numbers are KiCad's.

The actual cause is a missing sidecar. `_refill_copy` copies the `.kicad_pcb` and, explicitly,
"the sibling .kicad_pro if present (keeps DRC rules identical)" - but **not the `.kicad_dru`**.
Measured three ways on the finished board, reading the guard zone and the GND pour:

    staged files              guard F.Cu     GND B.Cu
    pcb + pro + dru           17.176 mm2     1745.233 mm2   <- EXACTLY the committed fill
    pcb + pro (_refill_copy)   6.613 mm2     1728.589 mm2
    pcb alone                  6.613 mm2     1728.502 mm2

The `.kicad_pro` makes no difference at all; the `.kicad_dru` makes all of it. Without the DRU,
`aiee_clearance_floor (min 0.127 mm)` is gone and KiCad refills at its stock 0.2 mm default. The
loss is not proportional: wider clearance drops narrow passages under the zone's 0.25 mm
min_thickness, those passages vanish, and island removal then culls whatever they were feeding -
so 0.073 mm of extra clearance destroyed 61% of the guard pour.

BLAST RADIUS. `rules_gen` writes a `.kicad_dru` for every ai-ee board, so `--verify-fill`
mis-measures every one of them - this is not a `connect: solid` quirk. The READ path is safe
(the copy is discarded, and the gate does not pass `--verify-fill`), but the same omission in
any WRITE path would commit a silently wrong fill. Verified NOT affected here: planes_gen,
route_auto and route_edit stage the board with its same-stem sidecars, and every fill they
produced matched the pcb+pro+dru number.

## 2026-08-19 [P7][check_return_path][routing] An unavoidable layer-change crossing stops being an ERROR when the crossing lands inside the transition via's excision disk
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
`sev = "error" if crossing >= CROSSING_ERROR_MM else "warning"`, and `CROSSING_ERROR_MM = 0.01`.
So shortening or straightening a crossing never clears it; the centerline must not cross
surviving deficit at all. What removes deficit is the excision step: a disk of
`item_radius + ALLOW_CLEARANCE_MM (0.65)` around each single via - 0.95 mm for a 0.6 mm via.

This board's SPI fan needs 2 crossings (J2 orders CS,SCLK,DOUT; the converter orders
CS,DOUT,SCLK - a 3-cycle, so /CS must cross both). The crossings are topologically forced: /CS's
B.Cu tunnel MUST pass under both F.Cu traces, so "move it under bare board" is not available.
What IS available is moving the tunnel so each crossing sits inside a transition via's disk.

    before: one 4.39 mm tunnel, 45 deg diagonal, single via
            /SCLK  ERROR   0.73 mm2, 0.64 mm crossing
            /DOUT  warning 0.09 mm2, 0.00 mm crossing  <- already excised, 0.53 mm from the via
            /CS    ERROR   2.35 mm2, 1.81 mm crossing

    after:  1.93 mm tunnel, PERPENDICULAR, via pair straddling both crossed traces
            (66.000, 40.980) via -> B.Cu -> via (66.000, 39.050); /DOUT crossed at 0.65 mm
            from the south via, /SCLK at 0.63 mm from the north via, both < 0.95 mm
            /SCLK  gone      /DOUT  gone      /CS  error 0.20 mm2, 0.03 mm (-91% / -98%)

/DOUT was the tell: it already passed as a warning purely because its centerline happened to
cross 0.53 mm from the existing via. Reading WHY one of three sibling nets passed gave the rule.

This is physics, not checker-gaming: where the pour void under the aggressor is the transition
via's own antipad rather than a slot, the return current detours around a small disk instead of
running the length of a cut. The routing rule: put the layer transition as close to the crossed
trace as DRC allows (via edge + clearance + half the crossed trace), and cross perpendicular.

COROLLARY, and a real design margin: this board's driven guard ring CLOSES at the DRU's 0.127 mm
clearance and does NOT close at 0.2 mm - refilled at 0.2 the /AIN_BUF pour drops 19.03 -> 8.68
mm2, the entire south leg is culled and the closure test finds ZERO holes. The ring still fills,
still looks like copper in a render, and is no longer a guard. Any change to the clearance floor
or fab class must re-run the closure proof.

## 2026-08-19 [P8][footprint][silk][review] `CONN-TH_2P-P5.00_WJ500V-5.08-2P` draws its silk entry arrows on the OPPOSITE face from its own 3D model's openings - a render of a footprint cannot outrank the part geometry it draws
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
J1 (library footprint **`CONN-TH_2P-P5.00_WJ500V-5.08-2P`**, `lib/aiee.pretty`) shipped rotated 180 degrees: its wire throats faced
EAST, into the board. A render check had "confirmed" the orientation and passed it. The
render was not misread - the artifact lied.

The footprint contradicts itself. Its pads sit on local y = 0 and its body is ASYMMETRIC
about that pad row: F.Fab spans local y -5.64 .. +4.52 and F.CrtYd -5.5 .. +4.5, so the
**4.50 mm face is local +y** and that is the wire-entry face on the vendor drawing (two
3.0 mm openings, z 3.0-9.8, centred x = +/-2.54; the -y face is a solid wall). But the
two `fp_poly` entry ARROWS on F.Silkscreen are drawn on local **-y** - the solid face.
Arrows and throats point at opposite faces, so any check that reads the silk concludes
the exact opposite of the truth, and does so confidently.

THE TEST THAT WORKS. Measure the body asymmetry about the pad row from fab/courtyard
geometry and match it to the vendor number, then apply the footprint transform yourself.
At rot 90 KiCad maps local +y -> board +x, so the 4.52 face pointed east (into the board);
at rot -90 it maps local +y -> board -x. After rotating, the courtyard reads x[19.955,
30.045] about a pad row at x = 24.500 - west face 4.545 mm, east 5.545 mm - so the 4.50
wire-entry face now points west, out of the board edge. That is a number, not a picture.

CONSEQUENCES BEYOND THE ROTATION. (a) The pads SWAP: a 180-degree rotation of a 2-pin
connector exchanges the nets at the two positions, so every net on it must be ripped and
re-routed - here /AIN_RAW moved from y 41.600 to 36.520 and GND took its place. (b)
place_edit REFUSES a footprint move on a routed board ("rip the affected nets FIRST, then
move, then route fresh"; --allow-routed to override) - obey it, do not reach for the flag
first. (c) THE ARROWS ARE PRINTED SILK, not just a library annoyance - they ship in F.SilkS on
the fabricated board, so a corrected "SIG"/"GND" label and a contradictory arrow end up
millimetres apart and a user who trusts the arrow is actively misled.

FIXING THE ARROWS ON A BOARD INSTANCE IS UNOWNED. No pipeline writer can touch a
footprint's `fp_poly`: place_swig's silk ops are TEXT only (add_text / remove_text /
move_text, and remove_text matches `board.GetDrawings()` - board-frame gr_text - so it
cannot see footprint children); route_edit does tracks/vias; board_edit does Edge.Cuts;
fpfix takes `--lib <dir>.pretty` and never a board. Corrected here with a minimal SWIG
worker following place_edit's own contract (stage a copy beside the board, edit under
bundled python, re-parse + DRC the copy, os.replace only on success). PIPELINE GAP worth
closing: a `mirror_fp_graphic` / `remove_fp_graphic` op on place_swig.

THE MIRROR AXIS IS THE BODY, NOT THE PAD ROW. J1's silk body lines sit at x 19.855-20.105
(west) and 30.015-30.265 (east), so the body's own axis is x = 25.06 - mirroring the arrow
polys about THAT maps them onto the opposite face preserving their exact 0.015 mm abutment
to the body outline, and reflecting along the arrow's own axis reverses head and tail, so
the glyph stays a valid arrow pointing inward. Mirroring about the PAD ROW (x = 24.5)
instead would have overhung the body by ~1 mm and collided with the new "SIG" label.
Arrows moved x[27.000, 30.000] -> x[20.120, 23.120]; DRC 0, check_silk 0.

The LIBRARY is still wrong and is deliberately NOT patched from here (a re-pull reverts
it): anyone reusing `CONN-TH_2P-P5.00_WJ500V-5.08-2P` inherits the same trap.

## 2026-08-19 [P7][guard-ring][silk][drc] Guard-ring leakage is set by what is INSIDE the ring - and the cheapest fix can be re-routing the GUARDED net, not moving the offender
Promoted from boards/PCB-0014-A_bb-adc/LEARNINGS.md (promotion pass 2026-10-04).
Review priced GND conductors inside the closed /AIN_BUF guard ring at 0.227 / 0.289 /
0.350 mm from the guarded node - about 1.20 mV at the terminal on blocks.md's 1 Gohm
model, against 0.78 mV the guard exists to stop. The ring cannot intercept a leakage path
that STARTS inside it.

U3 pin 2 (V-) is irreducibly inside: the pin2/pin3 channel is 0.350 mm and copper needs
width + 2 x clearance = 0.381 mm at the 0.127 mm floor, so the ring must encircle pin 2
together with pin 3. "Move the via out of the ring" is likewise unreachable - the pad it
serves is inside, so any conductor touching it is inside.

What actually worked was re-routing the GUARDED net. Both sub-0.35 mm paths (0.227 and
0.289) were to the same thing: the /AIN_DIV stub running down the WEST side of the pocket,
right past the GND via and pin 2. /AIN_DIV is a 3-pad net (R3.2, R4.1, U3.3) whose
spanning tree can take the stub off EITHER resistor for the SAME 2.600 mm. Moving it from
R3.2->U3.3 to R4.1->U3.3 - identical length, requirement "keep /AIN_DIV short" untouched
at 4.600 mm total - put the stub on the east side and deleted both paths outright. With
the via also nudged 0.2 mm west, the two GND conductors inside the ring now measure 0.350
mm (pin 2, the irreducible pin gap) and 0.556 mm (the via). Scaling review's own 1/d model
that is roughly 1.20 -> 0.52 mV, i.e. under the 0.78 mV the guard stops.

Generalise: before moving the offending conductor, check whether the GUARDED net has a
degenerate spanning tree. Re-routing the victim can be free where moving the offender is
blocked.

SILK GOTCHA from the same pass: `add_text` at size 0.7 draws a `text_height` DRC error -
board setup enforces a 0.8 mm silk minimum. `check_silk` does NOT catch it (it is lenient
by design and never the oracle); `kicad-cli pcb drc` does. Size every scripted silk string
at >= the board's own minimum and verify with kc drc.

## 2026-10-04 [fab][cpl][easyeda2kicad] cpl_verify's default model cache was a board-local dir no board has, and a failed fetch read as no_model - twelve false pin-1 failures

Re-running the fab step on PCB-0016-B from a host worktree, dfm_check failed all 12 parts
with `cpl_no_model` although `~/.cache/hwde/easyeda` held every model. `default_cache`
returned `<parts.json dir>/easyeda` whenever a parts.json was given, whether or not that dir
existed, and `easyeda.get` swallowed the resulting fetch error into `None`, which
`analyse` reports as `no_model`. Setting `HWDE_EASYEDA_CACHE` hid it.

Now `env.easyeda_cache(board_dir)` resolves env var, then `<board_dir>/easyeda` only if it
exists, then `~/.cache/hwde/easyeda`; a fetch that raises records its error and the row is
`fetch_failed` (a failing verdict, kind `cpl_no_model`) with the error and cache dir in
`why`. `no_model` now means only "EasyEDA answered and has nothing".

Generalise: a default path that may not exist must be checked for existence before it
shadows a fallback, and a catch-all `except` that returns "no data" must still hand the
error to a caller that reports a verdict from it. A `no_model` from a run that fetched
nothing is a cache or network fault until proven otherwise - never chase pin 1 from it.

## 2026-10-05 [easyeda2kicad][cpl][dfm] An EasyEDA 403 is a rate limit for the whole run: latch it, say it once, never call it no_model

Re-running bom_cpl + dfm_check over 16 boards, the EasyEDA component endpoint 403'd after ~20
fetches (per-IP, shared by everything on the box). `bom_cpl` still swallowed the error, so the
part's rotation source read `none`/`table`, and dfm failed most parts as `cpl_no_model`; nothing
said the network had refused, and the batch was void before anyone traced it.

Now `easyeda.get` classifies a failed fetch (`HTTP 403/429 ... (rate limited)`, `HTTP <n>`,
`network error: ...`; 404 and an empty answer stay "no model"), and the first 403/429 latches
module state (`easyeda.rate_limited()`): one stderr line, and every later uncached part in the
process is skipped as `not fetched: ...` instead of asked again - dfm runs bom_cpl and cpl_verify
in one process, so the second never re-hammers. bom_cpl marks those parts `source: fetch_failed`
(+ `fetch_error`) with one `cpl_model_fetch_failed` violation; dfm emits one `cpl_fetch_failed`
error for the run. Remedy is a rerun once the limit clears; the cache keeps what did arrive.

Gotcha: the latch is process state, so a test that mocks a 403 leaves every later test's fetch
skipped. conftest's autouse `_easyeda_offline` fixture resets it (`easyeda.reset_rate_limit()`).

## 2026-10-04 [connector][verify][mate-pins] Pair mated pins by where they touch, not by pin number
check_mate_pins.py pairs a stacked mate's pads by overlaying the two pad patterns on their centres:
KiCad stores a B.Cu footprint as placed, so pads that touch sit at the same top-view offset. Pin
numbers do not survive the trip - on the lumina carrier and par (PCB-0004-A J4 male on F.Cu, PCB-0005-A
J4 female on B.Cu) carrier pin 19 meets par pin 20, and a pin-number compare reports a clean pair as
crossed. Gotchas: JLC footprints name headers `HDR-TH_<n>P-P2.54-V-M` (male) and `-V-F` (female), not
PinHeader/PinSocket, so a KiCad-only family regex misses every shipped board; two boards name one
signal differently (ADC0_CONN vs ADC0), which a pair's `net_map` covers rather than a fuzzy match;
and PCB-0011-A J2/J3 (same 5-pin male header, +3V3 vs /IO3 on pin 3) still carries the MECH-06 fault.

## 2026-10-04 [verify][scorecard][bom] A check's "input missing" warning is a scorecard false positive on every bare golden board
score_checks counts every finding a check emits on the golden corpus as a false positive, whatever
its severity, and the golden boards are bare board dirs with no fab files or parts.json. So
check_bom_sync's first cut, which warned when BOM/CPL or parts.json were absent, scored 6 FPs and no
real hit. A skipped leg now goes in the report's `skipped` list, not in `violations`. Also,
`exclude_from_pos_files` alone does not mean hand_install: fiducials, printed NFC coils and pogo
pads carry it with `exclude_from_bom`/`board_only`, and bom_cpl classes those board_feature.

## 2026-10-06 [route_auto][freerouting][timeout] Freerouting 2.2.4 takes ~5 min per pass on a 4-layer board, so a fixed per-rung timeout kills a healthy run
PCB-0023-A (4-layer): route_auto's old 600 s wall-clock timeout per rung fired after two passes and the run was lost; a subagent then ran the passes by hand (~$1, 30 min). `routelib.run_freerouting` now streams the process output and kills on STALL (`--stall-s`, default 900 s with no output and no .ses growth) or at a HARD cap (`--timeout-s`, default 3600 s). A run that keeps logging passes is never killed by the stall rule; the report carries `kill_reason` (stall|hard).

## 2026-10-04 [check_current][gates] A neck or via transition that feeds only a resistor carries that resistor's current, not the rail's - and a plated pad's centre is never in its fill
PCB-0021-A (lipo-boost) failed +SYS at 2 A on a 0.8 mm pour leg and its single-via
transitions, but the leg fed nothing but R6 (470R 0603), which can pass at most
sqrt(0.25 W / 470 R) = 23 mA. check_current now re-judges a still-failing neck or via
cluster at the most its far side can draw: only a two-pad chip resistor with a parseable
Value and an imperial size in its footprint id is bounded; any other part, and copper that
reaches no pad at all, keeps the full budget. Passing re-judgements are listed in
facts["leaf_branches"]. On the bench this cleared 17 one-via pull-up/divider taps on
lumina-carrier and one on pd-trigger.

Two join gaps turned up on the way. A through-hole pad's centre sits in no fill, because
the fill stops at its thermal relief, so a centre test never let a THT pad (USB-C shield
tabs, J4 on PCB-0021-A) join F.Cu to B.Cu; it now joins the fill touching its copper
within 1 mm. A fill can also end on a via's ring short of its centre (the +SYS B.Cu strip
stops at y 56.8, the via sits at 56.85), so vias join by their copper disk. Together these
cleared pd-trigger's 1.62 mm F.Cu GND advisory neck beside J1, whose shield barrels carry
the current to B.Cu.

## 2026-10-06 [check_current][gates][stitch] A stitch via is not a layer transition - count a pour-to-pour hop per pour pair
check_current clustered every via of a net (<= 2 mm) and asked each cluster for ceil(I / via_amps)
vias. On PCB-0023-A (GaN inverter) /SW was one pour on all four layers, stitched by ~120 vias on a
grid wider than 2 mm, so 112 one-via clusters each asked for 24 vias at 12 A: 112 errors, and 112
of the board's 135 pending waivers. A stitch via (no track touches it, every pad it touches sits in
the pour on that layer, fill on >= 2 layers) now leaves the cluster rule. Each pair of fill pieces
on two layers is judged once instead, counting every via and plated hole that joins them, and a
pair is skipped when one piece is a dead end (no pad or track, all its barrels in the group). Do
not drop stitches outright: the same board's VBUS crosses F.Cu -> B.Cu -> F.Cu through two 3-via
stitch groups, and those are real findings. A track hop or a via under a pad with no pour around
it still counts per cluster.

## 2026-10-04 [place_edit][silk][swig][check_silk] Refdes hide/shrink/re-layer is now `set_text` - the stroke floor is 0.15, not check_silk's 0.12
Dense 0603 rows (PCB-0020-A) left 8 refdes `silk_misattributed` with no legal spot within 1 mm
of their own pads, and place_edit had no op to hide or shrink one. It now carries
`{"op": "set_text", "ref": R, "field": "reference"|"value", ["hide": bool], ["size": mm],
["thickness": mm], ["layer": F/B.SilkS|F/B.Fab]}`; silk_place's unplaceable residuals carry
ready `fix_ops` (shrink, then hide). Three facts from building it, measured on KiCad 10.0.6:
- The silk floor is size 0.8 (check_silk MIN_TEXT_H, also the board's `text_height` DRC) and
  stroke **0.15**: `jlc_capabilities.yaml` min_silk_width_mm, which rules_gen writes as the
  `aiee_silk_width_floor` `text_thickness` DRU rule. The 2026-08-09 entry's "0.8 / 0.12 is
  above JLC's floor" is wrong on the stroke - 0.12 passes check_silk and fails DRC.
- SWIG: `field.SetVisible(False)` saves `(hide yes)` on the property; `SetLayer(B.SilkS)` on
  a FRONT footprint's field needs `SetMirrored(True)` too (saves `(justify mirror)`).
- `board.GetLayerName(id)` returns the USER name ("F.Silkscreen"); a report that compares
  against file layer names needs `pcbnew.LSET.Name(id)` ("F.SilkS").
check_silk already skipped hidden refdes; it now lists every refdes not printed on silk under
the report fact `refdes_off_silk` (a fact, not an info violation, because any violation turns
the status - and verify_all - to "violations").

