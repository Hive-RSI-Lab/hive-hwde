#!/usr/bin/env python
"""stackup_rows.py - controlled_impedance rows for a stackups.yaml entry.

The `controlled_impedance` table of a stackups.yaml entry is impedance.py's
output for the entry's own stack. This prints it (as YAML), so a new entry -
a vendor's published stackup (#446) - gets its table from the same solver
rules_gen sizes boards with, and tests/test_board_setup.py can regenerate it.

  outer   se_50 / diff_90 / diff_100 coated microstrip against the nearest
          plane (rules_gen.outer_microstrip_params), plus the IPC-2141A
          `closed_form` fallback.
  inner   for entries with `vendor` (6+ layers): the same three profiles as
          stripline on every inner layer whose two neighbours are inner
          layers too, referenced to both (rules_gen.stripline_geometry,
          the same model rules_gen uses when constraints put pours above and
          below). Field solver only.

Fab floor: a width under the stackup's fab minimum trace (the vendor's
profile, lib/fabfloors.vendor_profile) is pinned at that minimum and the
gap solved (`gap_solved: true`), as rules_gen does on JLC's 6-layer inner
layers. Only applied to vendor entries; JLC's tables predate it.

  hwde stackup_rows --stackup OSHPARK-4L-1.6-FR408HR

I/O: YAML to stdout, exit 0 / 2 (bad name).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import fabfloors  # noqa: E402
import impedance as imp  # noqa: E402
import rules_gen  # noqa: E402

PROFILES = (("se_50", "single", 50), ("diff_90", "diff", 90),
            ("diff_100", "diff", 100))
NOTES = {"diff_90": "USB 2.0 / ULPI", "diff_100": "Ethernet / LVDS / general 100R"}


def fab_floor(stackup: dict) -> float:
    """Min trace width (mm) of the entry's fab; 0 for JLC entries (their
    tables are unpinned)."""
    vendor = fabfloors.vendor_of(stackup)
    if vendor is None:
        return 0.0
    coppers = [ly for ly in stackup["stack"] if ly["type"] == "copper"]
    try:
        _, row = fabfloors.profile(stackup["layers"],
                                   float(coppers[0].get("copper_oz", 1.0)),
                                   vendor=vendor)
    except fabfloors.FabFloorError:
        return 0.0   # the vendor publishes no rules for this build (AISLER 8L)
    return float(row["min_trace_width_mm"])


def _outer(kind: str, z: float, h: float, t: float, er: float,
           floor: float, method: str) -> dict:
    if kind == "single":
        w = imp.solve_width(z, h, t, er, method=method)
        if w >= floor:
            return {"width_mm": round(w, 4)}
        return _reach({"width_mm": round(floor, 4), "below_fab_floor": True},
                      imp.microstrip_z0(floor, h, t, er, method), z)
    w, g = imp.diff_pair(z, h, t, er, method=method)
    if w < floor:
        w, g = imp.diff_pair(z, h, t, er, width=floor, method=method)
        return _reach({"width_mm": w, "gap_mm": g, "gap_solved": True},
                      imp.zdiff(w, g, h, t, er, method), z)
    return {"width_mm": w, "gap_mm": g}


def _reach(row: dict, z_got: float, z_target: float) -> dict:
    """A pinned row says what impedance it actually gives, and flags it when
    that misses the target by more than 2 % (a 100 ohm pair on a thin
    dielectric at the fab's min trace can't get there at any gap)."""
    row["z_ohm"] = round(float(z_got), 1)
    if abs(z_got - z_target) > 0.02 * z_target:
        row["unreachable"] = True
    return row


def outer_rows(stackup: dict, floor: float = 0.0) -> list[dict]:
    h, er, oz = rules_gen.outer_microstrip_params(stackup)
    t = imp.CU_OZ_MM.get(oz, 0.035)
    rows = []
    for prof, kind, z in PROFILES:
        row = {"profile": prof, "kind": kind, "impedance_ohm": z,
               "layer": "outer", "reference": "nearest_plane"}
        row.update(_outer(kind, z, h, t, er, floor, "field"))
        row["closed_form"] = _outer(kind, z, h, t, er, floor, "closed_form")
        if prof in NOTES:
            row["note"] = NOTES[prof]
        rows.append(row)
    return rows


def inner_layers(stackup: dict) -> list[str]:
    """Inner coppers whose neighbours are both inner: the stripline layers
    of a sig-plane-sig-...-plane-sig build."""
    names = [ly["name"] for ly in stackup["stack"] if ly["type"] == "copper"]
    return names[2:-2]


def inner_rows(stackup: dict, floor: float = 0.0) -> list[dict]:
    names = [ly["name"] for ly in stackup["stack"] if ly["type"] == "copper"]
    rows = []
    for lname in inner_layers(stackup):
        k = names.index(lname)
        refs = [names[k - 1], names[k + 1]]
        geo = rules_gen.stripline_layers(
            stackup, {"planes": [{"layer": r} for r in refs]})[lname]
        for prof, kind, z in PROFILES:
            g = rules_gen.stripline_geometry(float(z), geo, kind == "diff", floor)
            row = {"profile": prof, "kind": kind, "impedance_ohm": z,
                   "layer": lname, "reference": refs, "model": "stripline",
                   "width_mm": g["width_mm"]}
            if kind == "diff":
                row["gap_mm"] = g["gap_mm"]
            for flag in ("gap_solved", "below_fab_floor"):
                if g.get(flag):
                    row[flag] = True
            if row.get("gap_solved") or row.get("below_fab_floor"):
                _reach(row, g["zdiff_ohm" if kind == "diff" else "z0_ohm"], z)
            rows.append(row)
    return rows


def rows_for(stackup: dict) -> list[dict]:
    floor = fab_floor(stackup)
    rows = outer_rows(stackup, floor)
    if fabfloors.vendor_of(stackup) is not None:
        rows += inner_rows(stackup, floor)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stackup", required=True)
    ap.add_argument("--file", default=str(rules_gen.STACKUP_FILE))
    a = ap.parse_args(argv)
    st = yaml.safe_load(Path(a.file).read_text(encoding="utf-8"))["stackups"]
    if a.stackup not in st:
        print(f"no stackup {a.stackup!r}", file=sys.stderr)
        return 2
    print(yaml.safe_dump({"controlled_impedance": rows_for(st[a.stackup])},
                         sort_keys=False, default_flow_style=None, width=100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
