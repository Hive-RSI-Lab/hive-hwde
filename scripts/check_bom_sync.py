"""check_bom_sync.py - DNP, BOM, CPL and board attributes agree (P8/P9).

One concern: the assembly files say something different from the design.
The real hits: lumina-par and rf-de-20m shipped nine DNP sites populated in
BOM and CPL (DFA-07); rf-term-150w left R1, the 250 W load and the board's
whole function, out of both (DFA-04); rf-de-20m and g0-sense passed the dfm
gate with the BOM leg silently skipped because parts.json was absent (DFA-08).

Which assembly class each ref has is decided exactly as bom_cpl.py decides it
(its resolve_assembly over parts.json, the board's footprint attrs and the
smt_placed default, imported read-only), so this check and the generator can
never disagree about the rules; the check only reports where the FILES now
disagree with the design. The schematic's own `(dnp yes)` is read as a fourth
statement bom_cpl does not consult.

Rules, each a finding's `kind` (error unless marked):
 - bom_dnp_populated: a ref the schematic, the board's `dnp` attr or parts.json
   marks DNP is listed in BOM.csv or CPL.csv - it would be placed.
 - bom_dnp_mismatch: the schematic says DNP but the class bom_cpl resolves is
   still smt_placed (board attr and parts.json both silent), or the board's
   `dnp` attr is set while the schematic says `(dnp no)`. The next bom_cpl run
   would place a part the schematic leaves empty, or the reverse.
 - bom_ref_missing: a board footprint whose class is smt_placed is absent from
   BOM.csv or CPL.csv, or a ref of any class but board_feature is absent from
   BOM-full.csv (the BOM of record).
 - bom_ref_unknown: BOM.csv, CPL.csv or BOM-full.csv names a designator that is
   no footprint on the board.
 - bom_pos_excluded (warning): an SMD footprint carries
   `exclude_from_pos_files` (and neither `exclude_from_bom` nor
   `board_only`) and parts.json gives it no class, so it became hand_install
   from the attr alone and will not be machine-placed.

A missing input is not a finding, because a bare board (the golden corpus)
has no fab files and that is no fault of the design. Instead the report's
`legs` says which legs ran, and `skipped` names each one that did not and why
(DFA-08: a skipped leg is stated, never left silent).

Inputs, each found beside a pipeline workspace's board when not given:
  schematic   <pcb stem>.kicad_sch beside the board, its sheets followed
  fab files   --fab-dir, else the board's dir when it holds BOM.csv or
              CPL.csv, else <ws>/fab
  parts.json  --parts (a parts dir or the file), else <ws>/parts/parts.json

CLI: --pcb board.kicad_pcb [--fab-dir DIR] [--parts DIR|parts.json]
     [--out report.json]   exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

import sexpdata

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import bom_cpl  # noqa: E402
import checklib  # noqa: E402
from checklib import violation  # noqa: E402

SCRIPT = "check_bom_sync"
PLACED = bom_cpl.PLACED_CLASS
FAB_FILES = ("BOM.csv", "CPL.csv", "BOM-full.csv")


def _head(n):
    return n[0].value() if isinstance(n, list) and n \
        and isinstance(n[0], sexpdata.Symbol) else None


def _sval(v) -> str:
    return v.value() if isinstance(v, sexpdata.Symbol) else str(v)


def _child(node, name):
    for sub in node[1:]:
        if _head(sub) == name:
            return sub
    return None


def board_footprints(pcb: Path) -> dict[str, dict]:
    """ref -> {pos, attrs, smd} for every footprint on the board."""
    data = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for node in data[1:]:
        if _head(node) != "footprint":
            continue
        ref, pos, attrs = None, None, set()
        for sub in node[1:]:
            h = _head(sub)
            if h == "property" and len(sub) >= 3 \
                    and _sval(sub[1]) == "Reference":
                ref = _sval(sub[2])
            elif h == "at" and len(sub) >= 3:
                pos = (float(sub[1]), float(sub[2]))
            elif h == "attr":
                attrs |= {_sval(a) for a in sub[1:]}
        if ref and not ref.startswith("#"):
            out[ref] = {"pos": pos, "attrs": attrs, "smd": "smd" in attrs}
    return out


def schematic_dnp(sch: Path) -> dict[str, bool]:
    """ref -> the schematic's `(dnp yes|no)`, over the root sheet and every
    sheet it names (each file read once). Refs come from the symbol's
    instances block (the per-sheet-instance truth), else its Reference."""
    out: dict[str, bool] = {}
    seen: set[Path] = set()
    todo = [sch]
    while todo:
        f = todo.pop()
        if f in seen or not f.is_file():
            continue
        seen.add(f)
        data = sexpdata.loads(f.read_text(encoding="utf-8"))
        for node in data[1:]:
            h = _head(node)
            if h == "sheet":
                for sub in node[1:]:
                    if _head(sub) == "property" and len(sub) >= 3 \
                            and _sval(sub[1]) == "Sheetfile":
                        todo.append(f.parent / _sval(sub[2]))
            if h != "symbol" or _child(node, "lib_id") is None:
                continue
            d = _child(node, "dnp")
            dnp = d is not None and len(d) > 1 and _sval(d[1]) == "yes"
            refs = []
            inst = _child(node, "instances")
            for proj in (inst[1:] if inst else []):
                for path in proj[1:]:
                    r = _child(path, "reference") \
                        if isinstance(path, list) else None
                    if r is not None and len(r) > 1:
                        refs.append(_sval(r[1]))
            if not refs:
                for sub in node[1:]:
                    if _head(sub) == "property" and len(sub) >= 3 \
                            and _sval(sub[1]) == "Reference":
                        refs.append(_sval(sub[2]))
            for r in refs:
                if not r.startswith("#"):
                    out[r] = dnp
    return out


def csv_designators(path: Path) -> set[str]:
    """Every designator a BOM/CPL csv names (BOM rows join them by commas)."""
    text = path.read_text(encoding="utf-8-sig")
    refs: set[str] = set()
    for row in csv.DictReader(io.StringIO(text)):
        cell = row.get("Designator") or ""
        refs |= {r.strip() for r in cell.split(",") if r.strip()}
    return refs


def find_fab_dir(pcb: Path, given: str | None) -> Path | None:
    if given:
        return Path(given)
    if any((pcb.parent / f).is_file() for f in FAB_FILES[:2]):
        return pcb.parent
    ws_fab = pcb.parent.parent / "fab"
    return ws_fab if ws_fab.is_dir() else None


def find_parts(pcb: Path, given: str | None) -> Path | None:
    if given:
        p = Path(given)
        return p / "parts.json" if p.is_dir() else p
    for p in (pcb.parent.parent / "parts" / "parts.json",
              pcb.parent / "parts" / "parts.json"):
        if p.is_file():
            return p
    return None


def check(pcb: Path, fab_dir: Path | None, parts: Path | None) -> tuple:
    fps = board_footprints(pcb)
    sch_path = pcb.with_suffix(".kicad_sch")
    sch = schematic_dnp(sch_path) if sch_path.is_file() else {}
    have_parts = parts is not None and parts.is_file()
    records = bom_cpl.load_parts_records(parts if have_parts else None)
    classes, _, src = bom_cpl.resolve_assembly(
        set(fps), records, bom_cpl.board_part_fields(pcb))
    parts_refs = {r for rec in records for r in rec["refs"]} | {
        r for rec in records for r in rec["refdes_class"]}

    viols: list[dict] = []

    def add(code, sev, ref, msg):
        f = fps.get(ref)
        viols.append(violation(SCRIPT, sev, f["pos"] if f else None,
                               None, None, [ref], msg, SCRIPT, kind=code,
                               ref=ref))

    def is_dnp(ref) -> bool:
        return classes.get(ref) == "dnp" or sch.get(ref, False)

    # DFA-07: the schematic's DNP is a machine-readable statement that bom_cpl
    # does not read, so it must agree with the class bom_cpl resolves.
    for ref in sorted(fps, key=bom_cpl._natural_key):
        if ref not in sch:
            continue
        cls = classes.get(ref, PLACED)
        if sch[ref] and cls == PLACED:
            add("bom_dnp_mismatch", "error", ref,
                f"{ref} is DNP in the schematic but bom_cpl resolves it "
                f"smt_placed ({src.get(ref, 'default')}); set the footprint's "
                "dnp attr or a parts.json refdes_class")
        elif not sch[ref] and "dnp" in fps[ref]["attrs"] \
                and src.get(ref) == "board_attr":
            add("bom_dnp_mismatch", "error", ref,
                f"{ref} has the board dnp attr but the schematic says "
                "(dnp no)")
    for ref in sorted(fps, key=bom_cpl._natural_key):
        f = fps[ref]
        # board_feature (exclude_from_bom / board_only: fiducials, printed
        # coils, pogo pads) is no part, so only an attr-made hand_install is
        if f["smd"] and classes.get(ref) == "hand_install" \
                and "exclude_from_pos_files" in f["attrs"] \
                and ref not in parts_refs and not is_dnp(ref):
            add("bom_pos_excluded", "warning", ref,
                f"{ref} is SMD with exclude_from_pos_files and no parts.json "
                "class, so it became hand_install from the attr alone")

    legs = {"schematic": bool(sch), "parts": have_parts, "fab": False}
    files: dict[str, set[str]] = {}
    if fab_dir is not None:
        for name in FAB_FILES:
            if (fab_dir / name).is_file():
                files[name] = csv_designators(fab_dir / name)
    legs["fab"] = bool(files)
    legs["skipped"] = [m for m, missing in (
        ("fab: no BOM.csv, CPL.csv or BOM-full.csv found, so the BOM and "
         "CPL legs did not run", not files),
        ("parts: no parts.json found, so classes come from board attrs and "
         "the schematic only", not have_parts),
        ("schematic: no schematic DNP marks read", not sch)) if missing]

    for name in ("BOM.csv", "CPL.csv"):
        listed = files.get(name)
        if listed is None:
            continue
        for ref in sorted(listed, key=bom_cpl._natural_key):
            if ref not in fps:
                add("bom_ref_unknown", "error", ref,
                    f"{name} lists {ref}, which is no footprint on the board")
            elif is_dnp(ref):
                add("bom_dnp_populated", "error", ref,
                    f"{ref} is DNP but {name} lists it, so it would be placed")
        for ref in sorted(fps, key=bom_cpl._natural_key):
            if classes.get(ref) == PLACED and not is_dnp(ref) \
                    and ref not in listed:
                add("bom_ref_missing", "error", ref,
                    f"{ref} is smt_placed but missing from {name}")
    full = files.get("BOM-full.csv")
    if full is not None:
        for ref in sorted(full - set(fps), key=bom_cpl._natural_key):
            add("bom_ref_unknown", "error", ref,
                f"BOM-full.csv lists {ref}, which is no footprint on the "
                "board")
        for ref in sorted(fps, key=bom_cpl._natural_key):
            if classes.get(ref) != "board_feature" and ref not in full:
                add("bom_ref_missing", "error", ref,
                    f"{ref} ({classes.get(ref, PLACED)}) is missing from "
                    "BOM-full.csv, the BOM of record")
    return viols, legs


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--fab-dir", help="dir holding BOM.csv / CPL.csv / "
                    "BOM-full.csv (default: beside the board, else <ws>/fab)")
    ap.add_argument("--parts", help="parts dir or parts.json (default "
                    "<ws>/parts/parts.json)")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    fab = find_fab_dir(pcb, args.fab_dir)
    parts = find_parts(pcb, args.parts)
    viols, legs = check(pcb, fab, parts)
    skipped = legs.pop("skipped")
    return checklib.report(
        SCRIPT, pcb, viols, legs=legs, skipped=skipped,
        fab_dir=str(fab) if fab else None,
        parts=str(parts) if parts else None), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
