# Canonical interface fragment: USB-C power sink with USB-PD

Machine-readable half: `usbc-pd-sink.json` (exact constraints_schema shapes;
`power`/`voltages` values are the 20 V / 5 A worked example - recompute from
YOUR negotiated profile). Seeded T6 (2026-08-06) from
`boards/pd-trigger/research/interface-usbc-pd.{json,md}` - a P1 fragment
opus-verified against primary sources; the board shipped. The full worked
derivation (CH224K-specific VDD dropper / sense-pin / PTC sourcing analysis)
stays in that file; this copy keeps the CLASS-level canon.

HOW TO USE (research-interface-spec agents): START here. Validate-and-adapt:
(1) set current_a/voltage from the profile the board negotiates (EPR > 30 V
engages check_creepage for real), (2) reconcile net names, (3) re-resolve the
part-specific rows (Rd integration, DP/DM handling, tap protection resistors)
against YOUR controller's datasheet, (4) mark every delta in your md.

## Sources

| Tag | Document |
|-----|----------|
| GCT | USB4085 rev B / USB4105 rev A1 receptacle product specs (ratings 4.1, contact test 6.1.5) |
| SI21-03 | Semtech "ESD Protection of USB Type-C Interfaces" (TVS working voltages, pin table) |
| CH224 | WCH CH224 datasheet v1F (worked-example sink controller; PD-only mode 5.5, E-Mark 5.4) |
| PD | USB PD spec numbers via onsemi AN-5086 (CC capacitance 5.8.6), TI TPS25730/51 (cSnkBulkPd), Allion/Microchip AN3265 (vSrcSlewPos) |
| TA0357 | ST "Overview of USB Type-C and Power Delivery" (BMC 300 kbit/s, Rd 5.1k +/-20%) |
| beyondlogic | CH224 trigger-board teardown (no-TVS failure at 20 V under load) |

## The numbers that bind design

| # | Constraint | Value | Source |
|---|---|---|---|
| 1 | CC trace class | none - BMC 300 kbit/s, 300 ns edges, critical length ~7.5 m | TA0357, TI TUSB422 (computed) |
| 2 | CC node capacitance | 200 pF min .. 600 pF MAX, TVS/filters count | PD 5.8.6 via AN-5086 |
| 3 | Rd | exactly ONE path per CC line, 5.1 kohm +/-20%; integration is part-specific | TA0357, CH224 s6 figures |
| 4 | CC wiring | A5->CC1, B5->CC2 straight through, no crossover, no series R | SI21-03 pin table, CH224 5.4 |
| 5 | Receptacle VBUS rating | COLLECTIVE nameplate over A4/A9/B4/B9 (5 A = 1.25 A/contact), 30 C shell-rise test | GCT 4.1 / 6.1.5 |
| 6 | Receptacle voltage rating | check it - twins differ (USB4085 48 V vs USB4105 20 V); prefer >= 24 V for a 20 V port | GCT 4.1 |
| 7 | VBUS TVS | working >= 22 V, clamp <= ~34 V, at the connector, before protection | SI21-03 |
| 8 | Sink bulk on VBUS | <= cSnkBulkPd 100 uF under contract; 10-22 uF recommended (0.66 A charge at 30 mV/us slew) | TI TPS25730/51, AN3265 |
| 9 | e-marked cable | required by the SOURCE for >3 A; board-side = documentation only | USB-IF (restated Renesas) |
| 10 | PD-only DP/DM | off the connector, shorted at the chip (controller-specific) | CH224 5.5 |

## Pipeline traps this fragment encodes (the load-bearing part)

- **`diff_pairs: []` DP/DM-short trap**: auto-discovery by name suffix will
  "find" a deliberately shorted DP/DM node and report violations on it. One
  net name with no DP/DM token, or an explicit empty list in the MERGED file
  - architect's decision, never the fragment's.
- **check_current absent-net exit 2**: provisional power-net names must be
  reconciled before P5; creepage skips absent nets, current does not.
- **Tap-stub false positive**: thin spurs off a full-current net get flagged
  at the full budget - land tap pads inside the pour, or `overrides`.
- **GND deliberately unlisted** in `power` (full-budget via demands in every
  GND cluster otherwise).
- **Copper-weight reality check**: a 2 oz capabilities profile without a
  matching stackups.yaml entry means board_init writes 1 oz and check_current
  computes against 0.035 mm - verify before promising 2 oz widths.
- **Protection order**: TVS at connector -> bulk (connector side) ->
  controller taps -> resettable element -> output; keeps the contract alive
  and indicable through an output fault, and dumps shorts through the
  protective element.

## What stays per-run (do not generalize from here)

Controller-specific supply topology (CH224K's shunt VDD + 1 kohm dropper
dissipation trap), sense-pin protection values, PTC/eFuse sourcing at the
profile's V/I corner, CFG strap tables, exact receptacle part. See the
pd-trigger fragment for the worked example of each.
