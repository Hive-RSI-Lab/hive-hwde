"""IEC 60664-1 / IEC 62368-1 spacing requirements for check_isolation (#353).

Pure lookups over reference/iso_tables.yaml. Each answer carries `verified`
(every row and rule it rests on was read from a document) and `basis` (which
table and how), so a finding can say how far to trust its number.

    r = isotables.requirement({"standard": "iec60664-1", "grade": "reinforced",
                               "working_v_rms": 250, "impulse_v": 2500,
                               "pollution_degree": 2, "material_group": "IIIa",
                               "altitude_m": 2000})
    r["clearance"]["mm"], r["creepage"]["mm"]    # 3.0 from 4 kV; 5.0, doubled
"""
from __future__ import annotations

import bisect
from pathlib import Path

import yaml

TABLES = Path(__file__).resolve().parents[2] / "reference" / "iso_tables.yaml"
STANDARDS = ("iec60664-1", "iec62368-1")
GRADES = {"functional": 0, "basic": 1, "supplementary": 1, "reinforced": 2}
GROUPS = ("I", "II", "IIIa", "IIIb")
COLUMNS = ("general", "printed_wiring")
_cache: dict = {}


class IsoTableError(ValueError):
    """A declaration the tables can't answer (off the table, unknown name)."""


def tables() -> dict:
    if not _cache:
        _cache.update(yaml.safe_load(TABLES.read_text(encoding="utf-8")))
    return _cache


def lookup(rows: list[dict], xkey: str, ykey: str, x: float) -> dict:
    """Linear interpolation between rows; below the first row reads the first
    row; above the last is refused (a table doesn't extrapolate)."""
    rows = [r for r in rows if r.get(ykey) is not None]
    xs = [float(r[xkey]) for r in rows]
    if x > xs[-1]:
        raise IsoTableError(f"{x:g} is above the table's last row ({xs[-1]:g}, {ykey})")
    if x <= xs[0]:
        return {"mm": float(rows[0][ykey]), "rows": [xs[0]],
                "interpolated": False, "verified": bool(rows[0]["verified"])}
    i = bisect.bisect_left(xs, x)
    if xs[i] == x:
        return {"mm": float(rows[i][ykey]), "rows": [x], "interpolated": False,
                "verified": bool(rows[i]["verified"])}
    lo, hi = rows[i - 1], rows[i]
    y0, y1 = float(lo[ykey]), float(hi[ykey])
    return {"mm": y0 + (x - xs[i - 1]) * (y1 - y0) / (xs[i] - xs[i - 1]),
            "rows": [xs[i - 1], xs[i]], "interpolated": True,
            "verified": bool(lo["verified"] and hi["verified"])}


def _pd_key(pd: int) -> str:
    if pd not in (1, 2, 3, 4):
        raise IsoTableError(f"pollution degree must be 1-4, got {pd!r}")
    return f"pd{min(pd, 3)}"


def rated_impulse_v(mains_v: float, category: str, standard: str = "iec60664-1") -> dict:
    """Table F.1: rated impulse for a mains line-to-neutral voltage, taken at
    the row 'up to and including' it."""
    t = tables()
    block = t["iec60664_1"]["rated_impulse"]
    if category not in ("I", "II", "III", "IV"):
        raise IsoTableError(f"overvoltage category must be I-IV, got {category!r}")
    for row in block["rows"]:
        if mains_v <= row["v"]:
            return {"v": float(row[category]), "row": row["v"],
                    "verified": standard == "iec60664-1"
                    or bool(t["iec62368_1"]["rated_impulse_verified"])}
    raise IsoTableError(f"mains {mains_v:g} V is above Table F.1's 1000 V row")


def reinforced_impulse_kv(kv: float, standard: str = "iec60664-1") -> dict:
    """IEC 60664-1 3.1.5: one step up the preferred series; a value off the
    series, or the top of it, takes 160 %. 62368-1: 1.6 x (from memory)."""
    if standard == "iec62368-1":
        return {"kv": 1.6 * kv, "rule": "1.6 x", "verified": False}
    series = tables()["iec60664_1"]["preferred_impulse_kv"]
    if kv in series and series.index(kv) + 1 < len(series):
        return {"kv": float(series[series.index(kv) + 1]),
                "rule": "next preferred", "verified": True}
    return {"kv": 1.6 * kv, "rule": "160 %", "verified": True}


def clearance_impulse(kv: float, pd: int, standard: str = "iec60664-1") -> dict:
    """Table F.2 Case A, or 62368-1 Table 10. PD4 = PD3 with a 1.6 mm floor."""
    t = tables()
    if standard == "iec62368-1":
        return _clearance_62368(kv)
    got = lookup(t["iec60664_1"]["clearance_impulse"], "kv", _pd_key(pd), kv)
    if pd == 4:
        got["mm"] = max(got["mm"], 1.6)
    got["basis"] = "IEC 60664-1 Table F.2 Case A"
    return got


def _clearance_62368(kv: float) -> dict:
    t = tables()
    got = lookup(t["iec60664_1"]["clearance_peak"], "kv", "mm", kv)
    read = t["iec62368_1"]["clearance_verified_kv"]
    got["verified"] = all(float(r) in read for r in got["rows"])
    got["basis"] = "IEC 62368-1 Table 10"
    return got


def clearance_peak(kv: float, standard: str = "iec60664-1") -> dict:
    """Table F.7a Case A: steady-state, temporary or recurring peak volts."""
    got = lookup(tables()["iec60664_1"]["clearance_peak"], "kv", "mm", kv)
    got["basis"] = "IEC 60664-1 Table F.7a Case A"
    if standard == "iec62368-1":
        got = _clearance_62368(kv)
    return got


def altitude_factor(m: float, standard: str = "iec60664-1") -> dict:
    """Table A.2. Up to 2000 m is 1.00; between rows takes the next row up."""
    t = tables()
    for row in t["iec60664_1"]["altitude"]:
        if m <= row["m"]:
            return {"factor": float(row["factor"]), "row": row["m"],
                    "verified": standard == "iec60664-1"
                    or bool(t["iec62368_1"]["altitude_verified"])}
    raise IsoTableError(f"altitude {m:g} m is above Table A.2's last row")


def groove_x_mm(pd: int) -> float:
    """Clause 4.2: narrower than this a slot gets no creepage credit."""
    return float(tables()["iec60664_1"]["groove_x_mm"][f"pd{pd}"])


def _creepage_key(pd: int, group: str, column: str) -> str:
    if group not in GROUPS:
        raise IsoTableError(f"material group must be one of {GROUPS}, got {group!r}")
    if column == "printed_wiring":
        if pd not in (1, 2):
            raise IsoTableError("the printed wiring columns are for PD1 and PD2")
        if pd == 2 and group == "IIIb":
            raise IsoTableError("the printed wiring PD2 column excludes group IIIb")
        return f"pwm{pd}"
    if pd == 1:
        return "pd1"
    table_group = "III" if group.startswith("III") else group
    return f"pd{min(pd, 3)}_{table_group}"


def creepage_basic(v_rms: float, pd: int, group: str, column: str = "general",
                   standard: str = "iec60664-1") -> dict:
    """Table F.4 (Table 17 in 62368-1, not read: borrowed, unverified)."""
    if pd == 4:
        raise IsoTableError("the creepage table has no pollution degree 4 column")
    t = tables()
    got = lookup(t["iec60664_1"]["creepage"], "v",
                 _creepage_key(pd, group, column), v_rms)
    got["basis"] = "IEC 60664-1 Table F.4"
    if standard == "iec62368-1":
        got["verified"] = bool(t["iec62368_1"]["creepage_verified"])
        got["basis"] = "IEC 62368-1 Table 17 (60664-1 Table F.4's values)"
    return got


def impulse_declared(b: dict) -> dict:
    """The barrier's impulse level: impulse_v, or mains + overvoltage category."""
    std = b.get("standard", "iec60664-1")
    if b.get("impulse_v") is not None:
        return {"v": float(b["impulse_v"]), "from": "impulse_v", "verified": True}
    if b.get("mains_v_rms") is not None and b.get("overvoltage_category"):
        got = rated_impulse_v(float(b["mains_v_rms"]), b["overvoltage_category"], std)
        return {**got, "from": "Table F.1"}
    raise IsoTableError("needs impulse_v, or mains_v_rms + overvoltage_category")


def requirement(b: dict) -> dict:
    """{clearance, creepage, inner, groove_x_mm} for one barrier declaration.

    clearance = the larger of the impulse row (reinforced stepped up) and the
    peak row, x altitude. creepage = the table at the working voltage, doubled
    for reinforced, and never less than the clearance (IEC 60664-1 3.2: "a
    creepage distance cannot be less than the associated clearance")."""
    std = b["standard"]
    if std not in STANDARDS:
        raise IsoTableError(f"standard must be one of {STANDARDS}, got {std!r}")
    grade = b["grade"]
    if grade not in GRADES:
        raise IsoTableError(f"grade must be one of {sorted(GRADES)}, got {grade!r}")
    pd = b["pollution_degree"]
    _pd_key(pd)
    reinforced = grade == "reinforced"

    imp = impulse_declared(b)
    kv = imp["v"] / 1000.0
    step = None
    if reinforced:
        step = reinforced_impulse_kv(kv, std)
        kv = step["kv"]
    by_impulse = clearance_impulse(kv, pd, std)
    verified = bool(imp["verified"] and by_impulse["verified"]
                    and (step is None or step["verified"]))
    cl_mm, basis = by_impulse["mm"], f"{by_impulse['basis']} at {kv:g} kV"
    peak_kv = None
    if b.get("peak_v") is not None:
        peak_kv = float(b["peak_v"]) / 1000.0
    if b.get("withstand_v_rms") is not None:
        peak_kv = max(peak_kv or 0.0, float(b["withstand_v_rms"]) * 2 ** 0.5 / 1000.0)
    if peak_kv is not None:
        by_peak = clearance_peak(peak_kv, std)
        if by_peak["mm"] > cl_mm:
            cl_mm = by_peak["mm"]
            basis = f"{by_peak['basis']} at {peak_kv:.3f} kV peak"
            verified = bool(by_peak["verified"])
    alt = altitude_factor(float(b["altitude_m"]), std)
    clearance = {"mm": cl_mm * alt["factor"], "basis": basis,
                 "altitude_factor": alt["factor"],
                 "verified": bool(verified and alt["verified"])}

    table = creepage_basic(float(b["working_v_rms"]), pd, b["material_group"],
                           b.get("column", "general"), std)
    cr_mm = table["mm"] * (2.0 if reinforced else 1.0)
    cr_verified = bool(table["verified"]
                       and (not reinforced or std == "iec60664-1"))
    basis = (f"{table['basis']} at {float(b['working_v_rms']):g} V rms"
             + (" x 2 (reinforced)" if reinforced else ""))
    if cr_mm < clearance["mm"]:
        cr_mm = clearance["mm"]
        basis += "; raised to the clearance"
        cr_verified = clearance["verified"]
    creepage = {"mm": cr_mm, "basis": basis, "verified": cr_verified,
                "interpolated": table["interpolated"]}
    # the board's own floor can raise the tables' answer, never lower it
    for key, block in (("min_clearance_mm", clearance), ("min_creepage_mm", creepage)):
        floor = b.get(key)
        if floor is not None and float(floor) > block["mm"]:
            block.update(mm=float(floor), verified=True,
                         basis=f"the board's {key}, over {block['basis']}")
    if creepage["mm"] < clearance["mm"]:
        creepage.update(mm=clearance["mm"], verified=clearance["verified"],
                        basis=f"{creepage['basis']}; raised to the clearance")

    # Inner layers have no air and no surface, so neither table applies. The
    # gap is treated as a cemented joint in solid insulation: the board's own
    # inner_min_mm, else the printed-wiring PD1 column (x 2 reinforced). The
    # column is read; using it this way is from memory of IEC 60950-1 2.10.6.4.
    if b.get("inner_min_mm") is not None:
        inner = {"mm": float(b["inner_min_mm"]), "declared": True, "verified": True,
                 "basis": "the board's inner_min_mm"}
    else:
        v = float(b["working_v_rms"])
        key = "pwm1" if v <= 1000 else "pd1"
        got = lookup(tables()["iec60664_1"]["creepage"], "v", key, v)
        inner = {"mm": got["mm"] * (2.0 if reinforced else 1.0), "declared": False,
                 "verified": False,
                 "basis": "printed-wiring PD1 column as a cemented joint (rule "
                          "from memory; declare inner_min_mm)"}
    if b.get("solid_min_mm") is not None:
        solid = {"mm": float(b["solid_min_mm"]), "declared": True, "verified": True,
                 "basis": "the board's solid_min_mm"}
    elif GRADES[grade] == 2 or grade == "supplementary":
        solid = {"mm": 0.4, "declared": False, "verified": False,
                 "basis": "0.4 mm through insulation (from memory of IEC "
                          "60950-1 / 62368-1; declare solid_min_mm)"}
    else:
        solid = None   # 60664-1 sets no thickness for basic: it is proved by test
    return {"clearance": clearance, "creepage": creepage, "inner": inner,
            "solid": solid, "groove_x_mm": groove_x_mm(pd),
            "impulse_v": imp["v"]}
