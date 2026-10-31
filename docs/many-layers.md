# Many-layer and HDI boards

hwde builds boards of 2 to 32 copper layers (JLC: "1-32 layers"), with
blind, buried and laser micro vias on JLC's HDI builds. Issue #317.

## Stackups

`reference/stackups.yaml` holds a default lamination per layer count, JLC's
free or general build for it, and three HDI builds:

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

## Not done yet

- Inner-layer stripline impedance and per-layer rules in rules_gen are task
  261031-013616's (the impedance half of the 6-layer Linux board).
- route_auto and planes_gen don't place blind or micro vias themselves; a
  board gets them by hand or a later router.
- rules_gen writes no per-via-type DRU rules yet (KiCad's `A.Via_Type`).
