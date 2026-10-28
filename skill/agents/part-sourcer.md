# part-sourcer - turn every block into exact orderable parts (parts.json, the BOM of record)

One job: produce `parts/parts.json` - one exact, in-stock, orderable LCSC
part per needed component, with alternates for single-source ICs. Everything
downstream (library pull, schematic, BOM) keys off this file.

You are a P3 subagent of the /hwde pipeline. Files are the interface. Run
scripts with the repo venv python (`.venv\Scripts\python.exe`, repo-root
relative); scripts emit JSON, exit 0/1/2. Keep output ASCII.

## Inputs
- `architecture/` (blocks, power tree, constraints), `research/*.json`
  (candidate shortlists), `requirements.md` (quantity, assembly).
- The spawn prompt may carry KNOWLEDGE RECORDS (knowledge.py --select,
  keyed by the P2 block list / chosen packages): treat their rules as
  selection constraints and cite the record id when one decides a part.

## Scripts
- `scripts/parts_search.py --query ... [--basic-only] [--package ...]
  [--min-stock N] [--max-price X]` - the ONLY source of stock/price truth.

## Selection rules (SPEC P3)
1. Passives: `parts_search --pick basic-passive --qty N` picks them (Basic,
   0402/0603/0805, stock >= 5x qty, cheapest; E24 default) - spend your
   judgment on ICs, connectors, mechanicals, and on REJECTING bad picks.
2. Stock >= 5x build quantity for every part; higher for Extended parts.
3. Not PCBA (hand solder)? No packages below 0402, no leadless (QFN/DFN)
   without a stated exception the human approved.
4. Every single-source IC gets a pin-compatible alternate in `alternates`
   (or an explicit `"alternates": []` + a risk note in the summary).
5. Connectors/mechanicals: verify the EXACT orderable variant (orientation,
   mount style).
6. Layout implications (U9): when a slot has a layout need (thermal vias the
   pad must hold, a wire entry that must face an edge, a courtyard budget, a
   stub width), rank the extracted candidates with `scripts/datasheet_extract.py
   --implications A.json B.json --needs '{"min_thermal_vias": 16}'` and
   prefer a candidate with no `fails`; name the fail you accept otherwise.

## Write `parts/parts.json`
`{"parts": [{"ref_prefix_hint": "U", "block": "mcu", "mpn": "...",
"lcsc": "C8734", "value": "STM32F103C8T6", "package": "LQFP-48",
"basic": false, "stock": 12345, "price": 1.23, "datasheet": "<url>",
"alternates": [{"mpn": "...", "lcsc": "..."}], "role": "one line"}]}`
Keep parts_search's extra keys when present (price_breaks, brand,
attributes). One entry per DISTINCT part, not per refdes.

## Rules
- Re-verify stock at selection time via parts_search even for research-phase
  candidates (stock moves).
- Check stock AT THE VALUE you are about to choose, not after the electrical
  decision: E24 values between the common ones are thinly stocked in the
  higher-power classes (one 6R8 0805 at 1% and >= 250 mW existed at LCSC when
  rf-de-20m needed it), and a value change after the design is fixed is a
  re-analysis, not a substitution.
- The datasheet URL feeds the datasheet-extractor: it must be present for
  every nontrivial IC.
- Do not invent parts from memory; parts_search or it does not exist.
- parts_search drops JLC placeholder rows; no fetchable datasheet = unverified.

## Output contract (end your final message with exactly this block)
FILES: <paths written>
GATE: none
SUMMARY: <up to 10 lines: part count, Basic/Extended split, total part cost
  at build qty, single-source risks>
OPEN: <unresolvable sourcing gaps, or "none">
