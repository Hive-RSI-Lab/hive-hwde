# Vendors: fabs and assemblers beyond JLCPCB

hwde started as a JLCPCB pipeline: its fab rules, prices, rotations and part
numbers are all JLC's. This page covers the other vendors. Each vendor gets a
profile in `reference/vendors/<id>.yaml`, and `vendor.py` uses those profiles
to:

- **list / show**: what each vendor builds, and where its numbers come from.
- **fit**: whether this board can be built there. It checks the board's
  layers, size, copper, castellations, bottom-side parts and component pitch
  against the vendor's limits. With gerbers, it also runs the same
  gerber-level `dfm_check` against that vendor's minimums. Then it gives a
  price estimate when the vendor publishes prices. It compares every vendor
  side by side and names the ones that can build the board.
- **package**: the files that vendor's upload page asks for, written to
  `fab/<vendor>/`: gerber zip, BOM and CPL in the vendor's columns and
  rotation convention, and `ORDER.md` (which options to pick, which facts are
  still unchecked).
- **choose**: records the board's vendor in `state.json`. From then on the
  ledger expects a `vendor` fact for it: a `fit` that passed, from the
  board's current files. Release refuses until that fact holds.

JLC remains the default everywhere. `dfm_check` and `order_quote` read JLC's
tables unless `--vendor` names another profile.

```
hwde vendor list
hwde vendor show pcbway
hwde vendor fit --pcb kicad/board.kicad_pcb [--fab-dir fab] [--vendors all] [--assembly] [--qty 5,10]
hwde vendor package --workspace boards/X --vendor pcbway
hwde vendor choose --workspace boards/X --vendor pcbway
hwde dfm_check --pcb ... --vendor oshpark
hwde order_quote --pcb ... --vendor aisler
```

## Profile schema

Every number has a source. `meta.source_urls` lists the pages the profile was
copied from, and `meta.verified` is the date it was copied. A number the
vendor doesn't publish is left out. Don't guess one: a missing key means
"unknown", and `fit` says so.

```yaml
meta:
  id: pcbway                      # = file stem
  name: PCBWay
  country: CN
  services: {pcb: true, pcba: true, stencil: true}
  quote_url: https://...          # where the person gets the real price
  source_urls: [https://...]      # pages the numbers were copied from
  verified: "2026-10-31"          # date they were copied
  order: {method: web_upload, api: false, notes: "..."}

# Either point at a JLC-style capabilities file...
capabilities_file: ../jlc_capabilities.yaml
# ...or carry the same blocks inline:
design_rules:                     # key "<layers>layer_<outer oz>oz", the
  2layer_1oz:                     # keys of jlc_capabilities.yaml, all mm,
    min_trace_width_mm: 0.127     # fab MINIMUMS
    min_clearance_mm: 0.127
    min_via_drill_mm: 0.3
    min_via_diameter_mm: 0.6
    min_annular_ring_mm: 0.15
    min_hole_diameter_mm: 0.3
    min_copper_to_edge_mm: 0.3
    min_hole_to_hole_mm: 0.5
    min_hole_to_edge_mm: 0.5
    min_silk_width_mm: 0.15
    min_solder_mask_dam_mm: 0.1
    outer_copper_oz: 1.0
    inner_copper_oz: 0.0
castellated: {...}                # optional, keys as jlc_capabilities.yaml

board:
  layers: [1, 2, 4, 6]            # layer counts offered
  max_size_mm: [500, 1100]
  min_size_mm: [5, 5]
  copper_oz_outer: [1, 2]
  thickness_options_mm: [0.8, 1.6]
  surface_finish: [HASL, ENIG]
  solder_mask_colors: [green]
  castellated: true               # offered at all
  blind_buried_vias: true
  via_in_pad: true
  impedance_control: true

assembly:                         # omit when the vendor doesn't assemble
  upload: files                   # files: BOM + CPL beside gerbers | native:
                                  # the vendor reads both from the .kicad_pcb
                                  # (AISLER); package ships the board file
  sides: [top, bottom]
  min_board_mm: [10, 10]
  min_pitch_mm: 0.35              # finest lead pitch placed
  smallest_passive: "0201"
  bga: true
  sourcing: turnkey               # turnkey | turnkey_library | consigned | both
  part_number: mpn                # what the BOM keys parts on: mpn | lcsc
  distributors: [digikey, mouser, arrow]
  consigned: true
  zero_orientation: "..."         # when the vendor's 0 degrees isn't KiCad's
                                  # (Eurocircuits: IEC 61188-7); ORDER.md warns
  rotation: kicad                 # kicad: the board's own rotations (IPC-7351
                                  # zero, as KiCad exports them) | jlc
  bom:
    columns: ["Item #", Designator, Qty, Manufacturer, "Mfg Part #", "Description / Value", "Package/Footprint", Type, "Your Instructions / Notes"]
  cpl:
    columns: [Designator, Mid X, Mid Y, Layer, Rotation]
    side_labels: {top: Top, bottom: Bottom}

files:
  gerber: RS-274X
  drill: excellon
  archive: zip
  accepts_native: [kicad_pcb]     # board files the vendor takes as-is
  notes: "..."

pricing:                          # or pricing_file: ../jlc_pricing.yaml
  model: per_area                 # table | per_area | quote_only
  currency: USD
  ...                             # see "Pricing models"
lead_time_days:
  pcb: {2: 12, 4: 12}
  assembly_additional: 10
```

### Column names in `assembly.bom.columns` / `assembly.cpl.columns`

`vendor.py package` fills each column by its meaning, matched on its name:

| meaning | names it recognises (case-insensitive) |
|---|---|
| designators | designator, designators, reference, refdes, ref des |
| quantity | qty, quantity, qty per board |
| manufacturer | manufacturer, mfr, manufacturer name |
| MPN | mpn, mfg part #, manufacturer part number, mfr part number, part number |
| LCSC code | lcsc, lcsc part # |
| value / description | comment, value, description, description / value |
| footprint | footprint, package, package/footprint |
| mount type | type (SMD / THT) |
| notes | notes, your instructions / notes, instructions |
| item number | item, item # |
| X / Y | mid x, posx, x, center-x(mm) / mid y, posy, y, center-y(mm) |
| side | layer, side |
| rotation | rotation, rot |

A column with an unrecognised name is written empty, and `package` warns
about it.

### Pricing models

- `table`: the same format as `jlc_pricing.yaml`. `pcb.base` is
  `{layers: {qty: order price}}`, with `promo_max_mm`, `options` and
  `assembly {setup_fee, per_extended_part_feeder, per_joint, stencil}`.
- `per_area`: priced by board area (OSH Park, AISLER). It has `unit`
  (`in2` / `cm2`), `rate: {layers: price per unit}`, `copies` (boards per
  set), `per` (`set`: the rate buys a set of `copies` boards, as OSH Park
  prices; `board`: each board pays it, sold in sets, as AISLER prices), and
  optional `setup` (a job fee) and `min_price` per order.
- `quote_only`: the vendor publishes no prices to transcribe. `fit` gives
  only the quote URL.

Every price is an estimate, labelled `estimated: true`, next to the quote
URL. The honesty contract in `jlc_pricing.yaml` applies to every vendor.

## Rotation and sides

JLC's CPL needs each part rotated to JLC's own zero orientation, which
`bom_cpl.py` corrects part by part. Every other vendor's assembly guide asks
for the centroid file exactly as the EDA tool exports it. Their engineers
check orientations against the silkscreen and ask about any doubt before
placing. Their CPL therefore carries KiCad's own rotations from
`<board>-pos.csv` (X/Y in mm, the bottom side as KiCad exports it), and
`ORDER.md` asks for the vendor's placement review.

## In the ledger

`vendor` facts take the vendor id as their subject. `vendor.py fit` writes
one for each vendor it checks. The fact is `verified` when the board fits,
`failed` when it doesn't, and `unknown` when a check couldn't run (for
example, no gerbers were given). Once `vendor choose` has named a vendor,
that vendor's fact is expected, so release (attest) waits for it. A board
with no vendor chosen expects none, which keeps JLC boards as they were.

## The vendors on file

| vendor | builds | rules | assembly | price |
|---|---|---|---|---|
| JLCPCB | 1-20 L | JLC's tables | both sides, LCSC library, JLC rotations | table (lower bound) |
| PCBWay | 1-14 L | normal-process column | both sides, MPN turnkey or consigned | quote only |
| OSH Park | 2/4/6 L, ENIG, purple | per service page | none | per in2, sets of 3 |
| AISLER | 2/4/6 L | ENIG builds | both sides, from the .kicad_pcb | job fee + per cm2 per board |
| Eurocircuits | 1-16 L | STANDARD pool classes | both sides, MPN turnkey, IEC zero | quote only |
| Seeed Fusion | 1-6 L | finest options | both sides, OPL / MPN | quote only |

Each profile's header comment lists where its figures disagree with
another page and which one was taken (the stricter, as
`jlc_capabilities.yaml` does), and what isn't published. Refresh a profile
the way it was made: open its `source_urls`, copy the figures again, and
update `meta.verified`.

What isn't covered yet: live stock and prices for MPN-sourced vendors
(`lib/distributors.py` can look up DigiKey and Mouser with keys; `fit`
doesn't call it), and any vendor's ordering API (JLC's alone is wired, in
`order_submit.py`). Ordering stays the person's on every vendor.
