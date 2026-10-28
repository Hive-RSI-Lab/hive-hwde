#!/usr/bin/env python
"""lib_pin_types.py - retype pulled symbol pin electrical types from datasheet JSON.

easyeda2kicad emits `unspecified` (and a few arbitrary `input`) electrical
types on nearly every pin; kicad-cli ERC --severity-all then floods
`pin_to_pin` warnings plus FALSE `pin_not_driven` errors, so the P4 gate
(errors+warnings == 0) cannot pass on an untouched pulled lib (LEARNINGS
2026-07-27 [easyeda2kicad][erc]). Fix at the SOURCE - the symbol library the
schematic embeds - keyed by each symbol's "LCSC Part" property against the
datasheet-extract JSONs (parts/<lcsc>.json pinout types):

  power_in | ground -> power_in    supplies and grounds
  power_out         -> power_out   regulator outputs; a DUPLICATE pin with the
                                   same NAME (a SOT-223 tab) stays passive, or
                                   ERC raises power_out<->power_out
  everything else   -> passive     kills the Unspecified pin_to_pin flood

Symbols with no matching extraction get the blanket passive retype: the junk
types are library-inherent, not part-specific (the field-proven
boards/stm32-blinky recipe this script productionizes).

Idempotent text surgery: only the electrical-type token of each `(pin ...`
node is rewritten; nothing else is reformatted. Re-run after any lib_pull
refresh (a re-pull restores the junk types).

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2.
  0 = retype ran (changes applied or nothing to change)
  2 = unreadable library/JSON, internal error

Examples:
  lib_pin_types.py --lib lib/aiee.kicad_sym --datasheet-json parts/C8734.json
  lib_pin_types.py --lib lib/aiee.kicad_sym --datasheet-json "parts/*.json" --dry-run
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))
import fplib  # noqa: E402

DEFAULT = "passive"

# The reference implementation's proven scan: the electrical type is the first
# token after `(pin`; the graphic style (line/inverted/clock/...) follows it.
PIN_RE = re.compile(r"\(pin\s+([a-z_]+)\s+([a-z_]+)")
SYM_RE = re.compile(r'\(symbol\s+"([^"]+)"')
NUM_RE = re.compile(r'\(number\s+"([^"]+)"')


def build_rules(index: list[dict], extracts: dict[str, dict]) -> tuple[dict, list[str]]:
    """(symbol name -> {pin number -> kicad type}, matched lcsc ids)."""
    rules: dict[str, dict[str, str]] = {}
    matched: list[str] = []
    for entry in index:
        ds = extracts.get(entry["lcsc"])
        if ds is None:
            continue
        matched.append(entry["lcsc"])
        pin_map: dict[str, str] = {}
        out_names: set[str] = set()
        for row in ds.get("pinout", []):
            t = row.get("type", "")
            if t in ("power_in", "ground"):
                want = "power_in"
            elif t == "power_out":
                name = row.get("name", "")
                if name in out_names:
                    want = "passive"      # duplicate output pin (tab)
                else:
                    out_names.add(name)
                    want = "power_out"
            else:
                want = "passive"
            pin_map[str(row.get("pin", ""))] = want
        rules[entry["name"]] = pin_map
    return rules, matched


def retype(text: str, rules: dict[str, dict[str, str]]) -> tuple[str, list[dict]]:
    """Rewrite every pin's electrical type; unruled symbols/pins -> passive."""
    changes: list[dict] = []
    out: list[str] = []
    pos = 0
    for m in PIN_RE.finditer(text):
        sym_matches = SYM_RE.findall(text, 0, m.start())
        symbol = re.sub(r"_\d+_\d+$", "", sym_matches[-1]) if sym_matches else "?"
        num_m = NUM_RE.search(text, m.end())
        number = num_m.group(1) if num_m else "?"
        sym_rules = rules.get(symbol, {})
        want = sym_rules.get(number, sym_rules.get("*", DEFAULT))
        have, style = m.group(1), m.group(2)
        out.append(text[pos:m.start()])
        out.append(f"(pin {want} {style}")
        pos = m.end()
        if have != want:
            changes.append({"symbol": symbol, "pin": number,
                            "from": have, "to": want})
    out.append(text[pos:])
    return "".join(out), changes


def _load_extracts(paths: list[str]) -> dict[str, dict]:
    files: list[Path] = []
    for raw in paths:
        if any(ch in raw for ch in "*?["):
            files += [Path(p) for p in sorted(glob.glob(raw))]
        else:
            files.append(Path(raw))
    extracts: dict[str, dict] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read datasheet JSON {f}: {exc}") from exc
        lcsc = (data.get("lcsc") or "").strip()
        if lcsc:
            extracts[lcsc] = data
    return extracts


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", required=True, help="the pulled .kicad_sym library")
    ap.add_argument("--datasheet-json", nargs="+", default=[],
                    help="datasheet-extract JSON file(s); globs accepted")
    ap.add_argument("--dry-run", action="store_true", help="report, do not write")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        lib = Path(args.lib)
        if not lib.exists():
            raise RuntimeError(f"symbol library not found: {lib}")
        text = lib.read_text(encoding="utf-8")
        index = fplib.symbol_index(lib)
        extracts = _load_extracts(args.datasheet_json)
        rules, matched = build_rules(index, extracts)
        new, changes = retype(text, rules)
        if changes and not args.dry_run:
            lib.write_text(new, encoding="utf-8")
        lib_ids = {e["lcsc"] for e in index}
        payload = {
            "script": "lib_pin_types",
            "status": "pass",
            "lib": str(lib),
            "dry_run": bool(args.dry_run),
            "symbols": len(index),
            "extracts_matched": sorted(matched),
            # e.g. a part later removed by amendment: informational, not fatal
            "extracts_unmatched": sorted(set(extracts) - lib_ids),
            "changed": len(changes),
            "changes": changes,
        }
    except Exception as exc:  # noqa: BLE001 - contract: any error -> exit 2
        print(json.dumps({"script": "lib_pin_types", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2

    out_text = json.dumps(payload, indent=1, ensure_ascii=True)
    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
