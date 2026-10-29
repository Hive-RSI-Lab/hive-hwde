"""Mutant: dfa-07-dnp-populated (blinky2).

The lumina-par / rf-de-20m hit: R2 is marked DNP in the schematic and on
the board footprint (both agree, so ERC, DRC and parity stay clean), but the
assembly files are the stale ones from before the change and still list R2
in BOM.csv and CPL.csv, so it would be placed. The golden has no fab files
(Section 3.5 fixture rule), so this mutant carries a BOM.csv, CPL.csv and
BOM-full.csv built from every golden footprint, plus the edited schematic.
Must be caught by check_bom_sync as bom_dnp_populated at R2.
"""
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

import sexpdata

REF = "R2"


def _footprints(text):
    """[(ref, value, footprint, x, y)] for every footprint, board order."""
    out = []
    for node in sexpdata.loads(text)[1:]:
        if not (isinstance(node, list) and node
                and node[0] == sexpdata.Symbol("footprint")):
            continue
        props, at = {}, (0.0, 0.0)
        for sub in node[1:]:
            if isinstance(sub, list) and sub and sub[0] == sexpdata.Symbol(
                    "property"):
                props[str(sub[1])] = str(sub[2])
            elif isinstance(sub, list) and sub and sub[0] == sexpdata.Symbol(
                    "at"):
                at = (float(sub[1]), float(sub[2]))
        out.append((props["Reference"], props.get("Value", ""),
                    str(node[1]).split(":")[-1], at[0], at[1]))
    return out


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def fab_files(text):
    """The stale bom_cpl output: every footprint smt_placed, one BOM row per
    value + footprint (the golden carries no LCSC numbers)."""
    fps = _footprints(text)
    groups = {}
    for ref, val, fp, _, _ in fps:
        groups.setdefault((val, fp), []).append(ref)
    bom = [(v, ",".join(rs), fp, "") for (v, fp), rs in groups.items()]
    full = [(v, ",".join(rs), len(rs), fp, "", "", "smt_placed", "")
            for (v, fp), rs in groups.items()]
    cpl = [(ref, f"{x:.4f}", f"{-y:.4f}", "Top", "0.0000")
           for ref, _, _, x, y in fps]
    return {
        "BOM.csv": _csv(["Comment", "Designator", "Footprint", "LCSC"], bom),
        "CPL.csv": _csv(["Designator", "Mid X", "Mid Y", "Layer",
                         "Rotation"], cpl),
        "BOM-full.csv": _csv(["Comment", "Designator", "Qty Per Board",
                              "Footprint", "MPN", "LCSC", "Assembly Class",
                              "Instructions"], full),
    }


def mark_schematic_dnp(sch, ref):
    anchor = f'(property "Reference" "{ref}"'
    ai = sch.find(anchor)
    if ai < 0 or sch.find(anchor, ai + 1) >= 0:
        raise mutlib.SurgeryError(f"schematic {ref}: reference not unique")
    start = sch.rfind("\n\t(symbol", 0, ai)
    block = sch[start:ai]
    if block.count("(dnp no)") != 1:
        raise mutlib.SurgeryError(f"schematic {ref}: (dnp no) not found")
    return sch[:start] + block.replace("(dnp no)", "(dnp yes)") + sch[ai:]


def surgery(text):
    sidecars = fab_files(text)          # stale: built before the DNP edit
    text = mutlib.edit_footprint(text, REF, "(attr smd)", "(attr smd dnp)",
                                 f"{REF} dnp attr")
    sch = (mutlib.GOLDEN / "blinky2" / "blinky2.kicad_sch").read_text(
        encoding="utf-8")
    sidecars["blinky2.kicad_sch"] = mark_schematic_dnp(sch, REF)
    return text, {"ref": REF, "sidecars": sidecars,
                  "note": "DNP part still listed in BOM.csv and CPL.csv"}


if __name__ == "__main__":
    sys.exit(mutlib.run("dfa-07-dnp-populated", "blinky2", surgery))
