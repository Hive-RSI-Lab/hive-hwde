# Many-layer and HDI boards

hwde builds boards of 2 to 32 copper layers (JLC: "1-32 layers"), with
blind, buried and laser micro vias on JLC's HDI builds. Issue #317.

## Stackups

`reference/stackups.yaml` holds a default lamination per layer count, JLC's
free or general build for it, and four HDI builds:

| layers | default | outer prepreg (L1-L2) |
|---|---|---|
| 2 | JLC2313_1.6 | (1.53 mm core) |
| 4 | JLC04161H-1080B | 0.244 mm |
| 6 | JLC06161H-3313 | 0.099 mm |
| 8 | JLC08161H-2116 | 0.116 mm |
| 10 | JLC10161H-2116 | 0.119 mm |
| 12 / 14 / 16 | JLC12/14/16161H-2313 | 0.096 mm |
| 18 | JLC18161H-1080 | 0.078 mm |
| 20 | JLC20201H-2313 (2.0 mm) | 0.096 mm |

| HDI build | layers | vias it is laminated for |
|---|---|---|
| JLCH06161HN1-1078 | 6 | through; micro F.Cu-In1, In4-B.Cu |
| JLCH061611Y1-1078 | 6 | through; buried In1-In4; micro F.Cu-In1, In4-B.Cu |
| JLCH081611N2-2116 | 8 | through; micro F.Cu-In1, In1-In2, In5-In6, In6-B.Cu (2nd order, stackable) |
| JLCH101611Y2-1078L | 10 | through; buried In2-In7; micro F.Cu-In1, In1-In2, In7-In8, In8-B.Cu (2nd order, stackable; JLC's tags are empty, so it may be priced as special) |

`board_init --layers 8` takes the default; `--stackup JLCH081611N2-2116`
an HDI build. The thin outer prepreg of a 6+ layer JLC build makes
controlled-impedance outer traces narrow: 50 ohm is 0.12-0.19 mm, not the
4-layer default's 0.45 mm. Each entry's `controlled_impedance` table has
the numbers.

### Where they come from

JLC's impedance calculator serves every lamination it builds without an
account (`POST https://jlcpcb.com/api/jlcTools/impedance/selectPageImpedanceDefaultTemplate`).
`hwde stackup_fetch` reads it:

```
hwde stackup_fetch list --prefix JLC08 --thickness 1.6 --inner-oz 0.5
hwde stackup_fetch list --prefix JLCH08            # HDI builds, with their via spans
hwde stackup_fetch entry --name JLC08161H-1080 --fixture tests/fixtures/jlc_stackups
```

`entry` prints the stackups.yaml entry (stack, epsilon_r per ply, series
epsilon_r for a gap of several plies, finished thickness, the HDI via spans
and press cycles, and the outer impedance table solved by impedance.py).
Paste it into stackups.yaml and keep the fixture: `tests/test_vias.py`
checks every entry against its fixture's conversion. JLC tags a template
in its name: 免费 free, 通用 general, 特殊 special (priced and reviewed), and
不要选用 / 后续取消 do-not-use (written `available: false`).

A calculator template is how JLC laminates the build, but it is not an
order-API read-back, and the order list churns (stackups.yaml header). The
entries say `order_api_verified: false`, and board_init warns so on every
board that routes controlled impedance on one, until someone with a JLC API
account reads the build back.

## check_vias

A verify check (`scripts/checks.d/check_vias.yaml`). KiCad draws a via of
any type between any two layers, and DRC checks only the copper, so a buried
In1-In3 via passes DRC on a 6-layer board that JLC presses once and drills
through. check_vias matches the board's own stack block to a stackups.yaml
entry by its layer thicknesses (or `--stackup`) and holds every via to it:

- a blind/buried/micro via on a standard build (`via_needs_hdi`, naming the
  HDI builds that make that span), on a stack no entry matches
  (`via_no_lamination`), or a span the HDI build isn't laminated for
  (`via_span_unbuilt`);
- a via type that doesn't fit its span (`via_type_span`), and mechanical
  spans that overlap without nesting, so no press order drills both
  (`via_spans_cross`);
- JLC's HDI numbers (`jlc_capabilities.yaml` `hdi:`, from its HDI FAQ):
  laser holes 0.075-0.15 mm across 0.05-0.127 mm of dielectric at most 1:1
  deep, mechanical blind/buried 0.15-0.55 mm, 16:1 for mechanical holes,
  buried vias across >= 0.30 mm, pads hole + 0.15 mm (through on HDI:
  + 0.10), laser via hole-to-hole 0.13 mm same net / 0.24 mm other net, and
  HDI only at 4-24 layers;
- a stack that isn't mirror-symmetric (warning: it bows in reflow).

Its planted fault is `blind-via-no-lamination` on rf4.

## Escape on HDI spans (#477)

On a board whose stack block matches an HDI build, `route_escape` escapes every
ball field through `lib/hdilib.py` instead of KRT (`--hdi off` keeps KRT; a
standard build never gets microvias, `--hdi on` refuses one):

- outer balls leave on their own layer; the rest through a microvia in the pad
  (0.1/0.25 mm, the drill grown to the dielectric), stacked down to the first
  layer with a clear path: In1, In2, then through the buried via (0.15/0.30);
- a ball keeps a via on every layer it may still leave on, so each layer is
  swept outer ring to inner, straight out or by a 45 degree jog into the
  channel between two vias. 0.4 mm holds one ring per layer, 0.5 mm several;
- a ball whose net has an inner plane under the field gets a stack to it;
- inner planes (a zone over the field) are left out of the routing layers, as
  with KRT. `--layers` overrides them;
- diff pairs escape single-ended, not coupled.

Measured on 10-layer `JLCH101611Y2-1078L` fixtures (every ball escaped,
check_vias 0, DRC 0 errors):

| part | pitch | balls per layer |
|---|---|---|
| WLCSP-49, 7 x 7 | 0.4 mm | F 24, In1 16, In2 8, In3 1 (buried) |
| UFBGA-64, 8 x 8, 0.28 land | 0.5 mm | F 28, In1 36 |
| FBGA-153 eMMC, 0.25 land | 0.5 mm | F 100, In1 53 |
| TFBGA-54 | 0.8 mm | F 50, In1 4 |

The maze (`route_finish --no-rip-board`) then runs on the same boards, frozen in
`tests/fixtures/hdi_maze` (every net open, `tests/test_hdi_maze.py`):

| part | nets | routed | left unrouted | DRC errors | run time on the loaded tower |
|---|---|---|---|---|---|
| TFBGA-54 | 54 | 54, 100 % | 0 | 0 | 2 min |
| WLCSP-49 | 49 | 31, 63.3 % | 18 | 0 | 29 to 39 min |
| FBGA-153 | 153 | no answer yet | | | over 3 h; 109 routed and none failed at 40 min |

The maze keeps a pass only if it helps, so what it leaves is unrouted, not a
violation in copper. WLCSP-49's 18 are work for the escape or the router.

rules_gen and route_escape write the same rules (`aiee_hdi_*`): each via type's
own floors in place of the through-via ones (`A.Via_Type`), and 3 mil track and
clearance for what touches the field's courtyard. KiCad warns `holes_co_located`
on a microvia stacked on a buried via: a warning, and the stack is intended.

`dfm_check` holds a board on an HDI build to JLC's HDI line (3 mil track and
clearance, the laser's hole and ring). It measures a span's ring only on the
layers its hole crosses, and leaves hole-to-hole with a span to check_vias.
`fab_export` ships one Excellon per span (`<board>-front-in1.drl`,
`-in2-in7.drl`, ...) and all of them in the zip; KiCad writes them, and a test
holds the hits and the zip to the board's vias.

## Not done yet

- (Done, #323) rules_gen sizes inner stripline: an inner layer with a pour
  (constraints `planes`) on the copper above and below gets layer-scoped
  `aiee_diff_w_<pair>_<layer>` / `aiee_diff_gap_<pair>_<layer>` and
  `aiee_z<ohm>_w_<layer>` track_width rules, solved with
  impedance.field_stripline (the trace on the core, embedded in the
  prepreg, as check_diffpair solves). Where the solved width is under the
  fab floor (100 ohm on JLC 6-layer L3: 0.078 mm), the width is pinned at
  the floor and the gap solved. Inner layers without a pour on both sides
  keep the disallow guard. Not yet: the routers (DSN export, KRT) draw with
  the netclass (outer) width, so an inner run needs route_edit set_width or
  a DRC-led fix until the router reads the per-layer widths.
- planes_gen doesn't place blind or micro vias. route_auto offers Freerouting
  the build's spans only with `--offer-spans`, ahead of the through via because
  Freerouting takes them in order. KiCad's SES import calls each one buried, and
  route_auto puts the span's type back (`retype_vias`). Off by default because
  stubs slow Freerouting: on a UFBGA-64 fixture passes take 1-2 min without them.
  Stock 2.2.4 hung outright on stubs and on locked wires (#477, #657, #665); hwde
  now runs it with that loop bounded (`scripts/lib/freerouting_2_2_4/README.md`).
  On an escaped board use route_finish's maze, which takes the spans at
  0.3 (micro) and 0.6 (buried) of a through via's cost.
