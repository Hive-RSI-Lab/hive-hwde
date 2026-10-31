# EE ingestion: DFM, DFT and assembly

Task 261031-011721-463fb66ac1, 2026-10-31. One domain of the EE ingestion
run (the living EE map, task 261031-011929-e7ca26f8ad, links here). The
sources are free to read: vendor pages, app notes, vendor-hosted IPC
summaries, open-source rule files and first-person post-mortems. No paywalled
IPC text was read, so every IPC number here comes second-hand.

Each rule hwde could turn into a check is marked with one of three verdicts:

- **verified**: reproduced by a script here, or two independent sources agree.
- **disputed**: the sources disagree. Both sides are given.
- **folklore**: repeated often, but no source or number holds it up.

Scripts, run from the repo root:

- `python3 reference/ingest/dfm-assembly/stencil_rules.py` works each claim
  out by calculation and exits 1 if a verified claim no longer holds.
- `PYTHONPATH=scripts:scripts/lib $HWDE_PY reference/ingest/dfm-assembly/paste_margin_vs_kicad.py`
  compares `check_paste`'s paste-aperture model with what kicad-cli plots.

## What hwde already does (read before adding)

- **dfm_check** (Gerber level) checks:
  - trace, gap and copper-to-edge;
  - hole size, hole-to-hole, hole-to-edge and annular ring (one ring value for
    both vias and PTH pads);
  - castellations, silk width, silk over pads and mask dam (one value,
    whatever the mask colour);
  - tented paste pads;
  - stale zone fills;
  - CPL polarity and rotation, now including the bottom side;
  - that the release is complete.
- **fp_verify** checks pad count, pin 1, pitch, pad size, THT annulus and
  that a courtyard exists.
- **check_courtyard** checks that a courtyard covers its own pads.
  **check_model_fit** checks that a 3D model sits on its pads.
- **check_observability** checks at netlist level that a rail is reachable by
  a test point or an ADC.
- **stock_check** and the ledger record lifecycle as "JLC still sells it".
- **first-spin.md** gives the DFM/assembly reviewer its domain.

Nothing checked paste volume or stencil release, tombstoning balance, probe
access, fiducials and rails, solder wicking into vias, or the mask-colour
bridge.

## New this run: `hwde check_paste`

`scripts/check_paste.py` (tests: `tests/test_check_paste.py`) reads paste
apertures from the board file using KiCad's own margin rules. The model
matched kicad-cli's plot on seven pads, covering every margin source. That
comparison caught a bug: a paste-only pad takes no margin. The check raises:

- **`paste_area_ratio`** (warning): an aperture's area ratio is under 0.66 at
  the stencil thickness.
- **`paste_ep_missing`** (error): an exposed pad has no paste at all.
- **`paste_ep_coverage`** (warning): a leadless part's exposed pad has paste
  over more than 90 % or less than 40 % of it.

The stencil thickness comes from `--stencil-mm`, else `constraints.json`
`stencil_mm`, else 0.12 mm.

It is a tool an agent runs. It is not in the verify suite yet, because of
what it finds on the pinned boards (gap 2 is wiring it in):

| Board | Findings |
|---|---|
| core | U1 (DFN-10, 0.4 mm pitch) and U13 (QFN-20, 0.4 mm pitch): area ratio 0.64–0.65 on 0.12 mm. U1, U9, U10 and U13: exposed-pad paste at 100 % |
| lite-main | U7 (QFN-28, 0.45 mm pitch): exposed-pad paste at 100 %. The HTSSOP and ESSOP parts are left alone (PowerPAD 1:1) |
| ultra-front, hwde-led-test, golden blinky2, rf4, usbbuck4 | pass |

All of these look like real findings. JLC's own advice is a 0.10 mm foil for
pitch of 0.4 mm or less. JLC also says its engineers may cut IC apertures
down unless the order asks for 1:1. The board should not rely on that.

## Rules, with verdicts

### Stencil and paste

| Rule | Verdict | Evidence |
|---|---|---|
| Area ratio A/(P·t) ≥ 0.66 and aspect ratio W/t ≥ 1.5 (IPC-7525) | verified | NXP AN1902 §5.2.3; JLC stencil blog. stencil_rules.py: a 0201 (0.3²) aperture is 0.625 on 0.12 and 0.75 on 0.10; a 0.4 mm-pitch lead (0.2×0.6) is 0.625 on 0.12 and 0.75 on 0.10. For any aperture longer than it is wide, the area ratio is the binding limit |
| Use a 0.10 mm foil for pitch ≤ 0.4 mm or 0201, and 0.12 mm for 0603 or 0.5 mm pitch | verified | JLC stencil-order help and blog; NXP AN1902 Table 3 (100–125 µm at 0.4 mm). The calculation above agrees |
| JLC has a default stencil thickness | folklore | "Select by JLCPCB": its engineers choose from 0.10 to 0.20 mm. hwde should state the thickness in the order |
| Leadless exposed-pad paste at 50–80 %, as a window pane | verified | Microchip/Amkor 50–80 %; NXP 60–65 %; TI SLUA271 about 50 %; Broadcom 60–85 %. stencil_rules.py: 2×2 panes on 1.7, 2.6 and 3.3 mm exposed pads give 42–53 % and each pane still prints |
| Every exposed pad gets panes | disputed | TI PowerPAD (HTSSOP/TQFP) asks for a single 1:1 opening (SLOA120). check_paste exempts leaded packages |
| Perimeter-pad aperture reduction of 5–20 % | disputed | JLC 5–7 %, forums 10 %, Venture 10–20 %, TI QFN 1:1. Not a check |
| Home-plate apertures for 0402 | verified (reduces mid-chip balls) | Cookson/Shea study. Not a check: JLC owns the stencil |
| No paste over an unplugged via | verified | NXP AN1902; TI SLOA122; Microchip |
| Exposed-pad voiding up to about 50 %; BGA up to 30 % | verified, second-hand | IPC-A-610G and J-STD-001H as quoted by FCT. The often-quoted 25 % is out of date |
| Pin-in-paste volume = (V_hole − V_pin + V_fillet) / 0.5 | verified (physics) | AIM; Würth ANP078. stencil_rules.py: a 1.0 mm hole needs about 1.8 mm³, an aperture of about 3.9 mm square on 0.12, which is far beyond the pad. Pin-in-paste is a decision to state, not a default |

### Tombstoning

| Rule | Verdict | Evidence |
|---|---|---|
| The end that wets first lifts the part | verified | Indium/IPC S27-1 (169 parts of 0402); FCT SMTAI 2021. stencil_rules.py: on an 0402 the surface-tension moment is about 17× the weight moment, so any skew in wetting time can lift it |
| Thermal imbalance (one pad solid into a plane, the other on a thin trace) causes it | verified, qualitative | FCT case studies; vendor guides; IPC-7351 rationale |
| "The trace into a small pad must be ≤ X mm" | folklore as a number | No free study gives a maximum. What holds is the same copper into both ends and thermal relief to pours. JLC's spokes for 0402/0603 are 0.10–0.20 mm with a 0.20–0.30 mm gap |
| A 0.1 mm difference in pad length gives 5–10× more tombstones | folklore | Uncited (AtlasPCB) |
| Placement offset, nitrogen atmosphere and part height matter as much as layout | verified | FCT 2021 (in one study 80 % of 0201 tombstones were capacitors) |

### Fab capabilities (JLC, fetched 2026-10-31) against `reference/jlc_capabilities.yaml`

| Rule | Verdict | Evidence |
|---|---|---|
| PTH annular ring: 2-layer 1 oz 0.18 min (0.25 recommended); multilayer 0.15 min (0.20 recommended); 2 oz 0.254 | verified, and **our table is too lenient** | JLC capabilities page (I fetched it myself, and a helper fetched it too). The table has 0.15 for 2-layer and 0.10 for 4-layer, applied to vias and PTH pads alike. A via's ring is set by "hole + 0.1 mm", so the two need separate rows |
| Trace/space 0.10/0.10 for 2-layer 1 oz, 0.09 for multilayer | verified | JLC page. Our 0.127 and 0.1016 are conservative, so this is not a defect |
| Via 0.15/0.25 min, 0.2 hole preferred | verified | JLC page. rules_gen's 0.3/0.6 for 2-layer is conservative |
| PTH to track 0.28 (0.35 recommended); NPTH to track 0.2; NPTH hole ≥ 0.5 | verified | JLC page; agausmann and darkxst .kicad_dru files (0.33, 0.254, 0.5). dfm_check checks none of these |
| Plated slot ≥ 0.5 (2-layer) or 0.35 (multilayer); non-plated ≥ 1.0 (PCBWay 0.8); plated slot length ≥ 2× its width | verified (JLC), disputed (PCBWay) | Not checked |
| Mask bridge 0.10 for green and other colours, 0.13 for black and white, 0.20 at 2 oz | verified | JLC page and blog. PCBWay: 0.1 green, 0.114 black. dfm uses 0.10 whatever the colour |
| Copper to edge: 0.2 routed, 0.4 V-cut | verified | JLC page and V-cut blog. Our 0.3 does not depend on edge type |

### Assembly (JLC)

| Rule | Verdict | Evidence |
|---|---|---|
| Economic assembly: single side, pitch ≥ 0.4, 0402 or larger, 0.8–1.6 mm board, 10×10 to 470×500 mm | verified | JLC assembly capabilities page |
| Standard assembly: fiducials and rails required; rails ≥ 5 mm; boards under 70×70 are panelised | verified | JLC assembly page and rails help article |
| Part body ≥ 2.5 mm from the edge without rails | disputed | Seen only through JLC's T&C as quoted. Economic assembly says > 0.3 mm |
| Fiducial: 1 mm copper with a 2× (2 mm) opening; 3 global; 2 local for parts at ≤ 0.5 mm pitch | verified (copper size, count); disputed (opening 2× or 3×; distance to edge 3.35, 3.85 or 7.6 mm) | IPC-7351B as quoted; JLC; Solutions Mfg; Altium; LCSC |
| V-cut: ≥ 0.6 mm board; 25° groove; parts ≥ 1 mm from the score (2 mm if tall) | verified (JLC), single source for part distance | JLC V-cut blog |
| THT hole = maximum lead diameter + 0.25 / 0.20 / 0.15 mm (IPC-2222 levels A/B/C; use the diagonal for square leads) | verified as the standard, disputed in practice | EDN; Sierra; Worthington. stencil_rules.py: KiCad's stock 1.0 mm drill for a 0.64 mm square pin is below even level C (1.055), yet it fits with 0.095 mm clear. Take it as a warning, not an error |
| Wave solder: chips perpendicular to the wave, SOICs parallel, thieving pads | verified, qualitative | Sierra; Summit. "3×H shadow" is folklore |
| Selective-solder keep-out of 3 mm | folklore | No source. It depends on the nozzle |

### Test and bring-up access

| Rule | Verdict | Evidence |
|---|---|---|
| ICT pad 0.9–1.0 mm (0.8 min); pitch 2.54 preferred, 1.27 min; ≥ 1 mm to parts; about 3 mm from the edge; one side | verified (strong agreement) | FixturFab; Test Electronics; Genesys DFT v2.0; PRP; Magellan |
| Flying-probe pad minimum | disputed | 0.15 to 1.2 mm across vendors. 0.5 mm min and 0.75 mm recommended is the middle |
| Clearance scaled to part height | disputed | FixturFab (2/3 mm, or 0.3×H); Magellan (+0.5 mm per 2.54 mm of height) |
| A via used as a test point is untented, with a hole ≤ 0.5 mm | verified | FixturFab; pcbandassembly |
| Tag-Connect TC2030: 6 pads of Ø0.787 at 1.27 mm with no paste, 3 NPTH of Ø0.991, no tracks or vias in the keep-out, 0.5 mm clear of other signals | verified (the datasheet itself, repeated by Debug Store) | tag-connect.com TC2030-IDC Rev B |
| A 0 Ω link is a current shunt | folklore | A 0 Ω part is 20–50 mΩ with a loose tolerance. Fit a 10–20 mΩ precision part to measure current |

### BOM and lifecycle

| Rule | Verdict | Evidence |
|---|---|---|
| JLC Basic/Preferred/Extended tiers say something about lifecycle | folklore | They signal cost and feeder availability only (JLC Q&A). Neither jlcparts nor jlcsearch has an EOL field. Stock that stays at 0 is the practical proxy |
| Lifecycle is available as data | verified | Nexar `part.specs` `lifecyclestatus` (New/Production/NRND/EOL/Obsolete), plus factory lead days and the count of authorised sellers. KiCost and kicad-bot (`fail-on-eol`) use it |

### First-spin failure classes (18 first-person post-mortems: Hackaday, hackaday.io, SparkFun, EEVblog, KiCad forum, Glasgow, Bunnie)

| Class | Sources | hwde today |
|---|---|---|
| Wrong footprint or package variant (pin numbering, a WS2812 footprint used for an SK6812) | 7 | fp_verify against a datasheet extract; pin-to-pad review in first-spin.md |
| Mirrored pinout (top/bottom view, male/female header, bottom-side connector) | 5 | check_mate_pins and check_mating for mated pairs; **no check for a connector on the bottom side** |
| Part rotated or orientation wrong | 3 | cpl_verify (both sides) |
| SOT-23 and discrete pin-order variants | 3 | pin-to-pad review only |
| Swapped pair (USB D+/D−, SCK/MOSI) | 3 | interface checklists |
| Silk over pads or ambiguous | 3 | check_silk |
| Mechanical (connector unusable, holes too small) | 3 | check_mating; check_model_fit; **no IPC-2222 hole-versus-lead check** |
| Crystal load caps, regulator EN, paste on thermal vias | guides only | not found in any first-person post-mortem |

## Gap list, ranked (filed as briefs where marked)

1. **Separate the JLC PTH annular ring from the via ring, and add the missing
   hole rules.** Change the table to: PTH ring 0.18 for 2-layer and 0.15 for
   multilayer; via ring set by "via diameter ≥ hole + 0.1"; PTH-to-track
   0.28; NPTH-to-track 0.2; NPTH hole ≥ 0.5; slots (plated ≥ 0.5 or 0.35,
   non-plated ≥ 1.0, plated slot length ≥ 2× width); mask bridge by colour
   and weight. Board case: the pinned lite-main (2-layer) has PTH rings
   under JLC's 0.18 that dfm passes today: U8 at 0.155 mm, and the H1–H4
   mounting-hole pads at 0.15. *Filed: 261031-013747-40c904f2ef.*
2. **Put check_paste in the verify suite, and order the stencil hwde
   needs.** Steps: register it in checks.d; plant a fault (a full-paste QFN
   exposed pad); judge the 7 findings on the pinned boards in
   `pinned/known/`; have order_submit request 0.10 mm (or a 1:1 or pane
   remark) when any aperture fails at 0.12. Have lib_pull or fpfix write
   window panes on a pulled QFN's exposed pad. Board case: core U13
   (QFN-20, 0.4 mm pitch, area ratio 0.64, exposed pad at 100 %). *Filed: 261031-013753-d3238414d5.*
3. **Tombstone balance check** for 2-pad chips of 0603 and smaller. Compare
   the copper thermally tied to each pad within about 1 mm: a solid zone
   connection or a wide track at one end only. Make it a warning that names
   the fix (thermal relief, or the same track width). Board case: an 0402
   with one pad solid into a GND pour and the other on a 0.15 mm track. Any
   threshold is a model and should say so (folklore above). Prove it with
   coupon 1. *Filed: 261031-013802-11889da13e.*
4. **Probe access (DFT, physical).** For every rail and status net
   check_observability lists, find a pad that can be probed: ≥ 0.8 mm (or
   ≥ 0.5 mm for a flying probe), not tented, ≥ 1 mm from other parts' bodies,
   ≥ 3 mm from the edge, on one side. Check a Tag-Connect footprint's keep-out.
5. **Fiducials and rails for standard assembly:** 3 global fiducials of
   1 mm copper with a ≥ 2 mm opening; 2 local fiducials for parts at
   ≤ 0.5 mm pitch; rails, or panelising, for boards under 70×70; fiducials
   on both sides of a double-sided build.
6. **Solder wicking:** an open via in an SMD pad, or within 0.25 mm of one
   with no mask dam between them (Eurocircuits). Also an exposed pad's
   thermal vias under paste with no plugging (NXP).
7. **Edge-type clearance and panel rules:** copper ≥ 0.4 mm from a V-cut
   against 0.2 mm from a routed edge; parts ≥ 1 mm (or 2 mm if tall) from a
   score; mouse-bite tabs; JLC's 70×70 minimum for standard assembly.
8. **Bottom-side connector mirror check:** a connector on B.Cu whose footprint
   was flipped but whose mate expects the top-side pinout (5 post-mortems).
9. **IPC-2222 hole against the lead,** from the part's datasheet lead
   diameter (diagonal for square leads). Make it a warning, because the
   standard is stricter than practice.
10. **Lifecycle from Nexar:** NRND/EOL/obsolete and the count of
    authorised sellers into the ledger's `lifecycle` fact (today that fact
    only means "JLC sells it"), with KiCost-style fail-on-eol.
11. **Pin-in-paste calculator** in eng_calc: the paste volume and the
    overprint a THT part needs if it is to be reflowed.
12. **Selective/wave rules** for a THT-heavy board that JLC wave-solders:
    part orientation and thieving. Low priority, because JLC hand-places or
    wave-solders THT itself.

### Test boards (coupons) worth building

The tools above need hardware before they can be trusted. Build each coupon
as a JLC standard-assembly order:

1. **Paste and tombstone coupon:** 0201, 0402 and 0603 rows with balanced
   and unbalanced copper (solid into a pour against thermal relief, against
   a 0.15 mm track), at both 0 and 90° to the reflow direction, on 0.10 and
   0.12 mm foils. Count the tombstones. This gives gap 3 a real threshold.
2. **Exposed-pad coverage coupon:** the same QFN-20 (0.4 mm pitch) at
   100 %, 80 %, 60 % and 40 % paste, and with open, tented and plugged
   thermal vias. X-ray the voids (JLC offers X-ray) and test for shorts
   between leads. This checks gap 2's 40–90 % band.
3. **Annular-ring and hole coupon:** PTH rings from 0.13 to 0.25 mm in
   0.02 mm steps, slots at their minimums, and NPTH at 0.5. See what JLC's
   DFM review flags and what comes back broken out. This checks gap 1's
   table.
4. **Probe-access coupon:** test pads from 0.5 to 1.0 mm next to parts of
   several heights, probed on the person's bench. This gives gap 4 its
   numbers, if bring-up ever uses a fixture.
