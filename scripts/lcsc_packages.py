"""lcsc_packages.py - the pinned table of each LCSC part's package (#879).

check_bom_sync's bom_package_mismatch compares a part's chip size with its
footprint's. A board that arrives foreign (oasis v0.2: no parts.json, no
parts/<LCSC>.json) gives it no package to compare, and the score and CI run
without the network, so `--live` can't fill the gap there. This table is
JLC's own record (stocklib.jlc_record: model and package) for the parts that
matter, written down once with the date it was read, so an offline run
judges what a live run would.

The table is reference/lcsc_packages.json:
  {"source": ..., "parts": {"C844504": {"package": "0402",
                                        "model": "CRCW040222K1FKED",
                                        "read": "2026-11-13"}, ...}}
It is a lookup, never a verdict: check_bom_sync prefers the workspace's own
part data, uses this table next, and asks JLC (--live) only for a code that
is in neither. A code JLC has no record of is written with package "" so a
reread is not needed to know that.

CLI (asks JLC; needs the network):
  lcsc_packages.py --pcb board.kicad_pcb [...]   add every LCSC number the
                                                 boards carry on a chip-size
                                                 footprint (0201..2512)
  lcsc_packages.py --add C844504 [...]           add these codes
  [--refresh]   reread codes already in the table
  [--table PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import stocklib  # noqa: E402

TABLE = HERE.parent / "reference" / "lcsc_packages.json"
SOURCE = ("JLC's parts record (stocklib.jlc_record): componentModelEn and "
          "componentSpecificationEn, read on the date given")


def load(path: Path = TABLE) -> dict[str, dict]:
    """code -> {package, model, read}; {} when the table is absent."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    parts = data.get("parts") if isinstance(data, dict) else None
    return parts if isinstance(parts, dict) else {}


def save(parts: dict[str, dict], path: Path = TABLE) -> None:
    out = {"source": SOURCE,
           "parts": {k: parts[k] for k in sorted(parts, key=_code_key)}}
    Path(path).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


def _code_key(code: str):
    digits = code[1:]
    return (0, int(digits)) if digits.isdigit() else (1, code)


def chip_codes(pcb: Path) -> set[str]:
    """Every LCSC number the board carries on a chip-size footprint."""
    import bom_cpl
    import check_bom_sync
    import engcalc
    fps = check_bom_sync.board_footprints(pcb)
    return {f["lcsc"] for ref, f in bom_cpl.board_part_fields(pcb).items()
            if f.get("lcsc") and ref in fps
            and engcalc.package_code(fps[ref]["fpid"])}


def add(codes, parts: dict, refresh: bool = False, fetch=None) -> list[str]:
    """Ask JLC for each code not yet in `parts` (all with refresh); returns
    the codes it could not ask about."""
    failed = []
    for code in sorted(set(codes), key=_code_key):
        if code in parts and not refresh:
            continue
        try:
            rec = stocklib.jlc_record(code, fetch or stocklib.http_fetch)
        except stocklib.SourceError as exc:
            failed.append(f"{code} ({exc})")
            continue
        parts[code] = {"package": (rec or {}).get("package") or "",
                       "model": (rec or {}).get("model") or "",
                       "read": stocklib.now()[:10]}
    return failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", nargs="*", default=[])
    ap.add_argument("--add", nargs="*", default=[])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--table", default=str(TABLE))
    args = ap.parse_args(argv)
    codes = set(args.add)
    for p in args.pcb:
        codes |= chip_codes(Path(p))
    parts = load(Path(args.table))
    failed = add(codes, parts, args.refresh)
    save(parts, Path(args.table))
    print(f"{len(parts)} parts in {args.table}")
    for f in failed:
        print(f"not read: {f}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
