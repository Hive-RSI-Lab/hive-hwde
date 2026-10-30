#!/usr/bin/env python
"""fabfloors.py - the ONE place fab minimums enter a design (T1).

`drc_routed` 0/0 does NOT imply fabricable: DRC checks a board against the
rules the pipeline itself wrote. Two live boards shipped with a
`.kicad_pro` whose `min_track_width` (0.1 mm) was BELOW every JLC profile
(4-layer 1 oz is 0.1016) and whose `min_hole_to_hole` (0.25 mm, KiCad's
default) was half the fab's 0.5 mm - at severity *warning*, so two real
drill defects survived to the P9 DFM gate.
(LEARNINGS 2026-07-29 / 2026-07-30 [board_init][rules_gen][dfm][gates].)

The root cause was TWO writers of the same block: `board_init.write_pro`
hard-coded its floors while `rules_gen.update_pro` derived them from
`reference/jlc_capabilities.yaml`. This module is the single source both
now call, so a project file can never be written with a floor the selected
JLC capability profile does not allow:

  profile(layers, outer_oz)  -> (class name, capability row)
  pro_rules(cap)             -> board.design_settings.rules block
  pro_rule_severities()      -> the floor checks, forced to ERROR
  check_pro(pro, cap)        -> [] or human-readable failures
  check_dru(dru_text, cap)   -> [] or failures (the .kicad_dru half: the
                                aiee_* baseline rules survive hand edits)

`check_pro` is the standing assertion the LEARNINGS entries ask for at P7
entry; board_init runs it against what it just wrote, and any later stage
(or an external board taken in at T9) can run it on an arbitrary project.

Only COPPER/HOLE floors are injected. Silk floors stay out on purpose: the
generated `.kicad_dru` already carries `aiee_silk_width_floor`, and pulled
library footprints routinely ship 0.1 mm silk (T3's problem, not the
project file's).

Not a CLI - library only (imported by board_init.py and rules_gen.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REFERENCE = Path(__file__).resolve().parents[2] / "reference"
CAP_FILE = REFERENCE / "jlc_capabilities.yaml"


class FabFloorError(ValueError):
    """No such capability profile / unusable capability data."""


# .kicad_pro board.design_settings.rules key -> jlc_capabilities.yaml key.
# Every entry is a fab MINIMUM: the project value must be >= the profile's.
PRO_RULE_KEYS = {
    "min_clearance": "min_clearance_mm",
    "min_track_width": "min_trace_width_mm",
    "min_via_diameter": "min_via_diameter_mm",
    "min_through_hole_diameter": "min_via_drill_mm",
    "min_via_annular_width": "min_annular_ring_mm",
    "min_hole_to_hole": "min_hole_to_hole_mm",
    "min_copper_edge_clearance": "min_copper_to_edge_mm",
}

# The DRC checks that enforce those minimums, pinned to ERROR. KiCad's own
# default for `hole_to_hole` is *warning*, which is how two sub-fab drill
# pairs reached the DFM gate on lumina-carrier; the rest default to error
# today but are stated explicitly so the project never depends on a KiCad
# default. Every key here appears in KiCad 10.0.3's own rule_severities set
# (checked against project files KiCad itself wrote).
FLOOR_SEVERITIES = {
    "track_width": "error",
    "clearance": "error",
    "hole_to_hole": "error",
    "hole_clearance": "error",
    "copper_edge_clearance": "error",
    "annular_width": "error",
    "drill_out_of_range": "error",
}


# The eight fab-floor BASELINE rules rules_gen emits into every .kicad_dru,
# mapped to their jlc_capabilities.yaml key. rules_gen.baseline_rules pulls
# its minimums FROM this map and check_dru asserts against the same map, so
# the writer and the checker cannot drift (T6 P5-5, ladder row 160: nothing
# previously asserted that a HAND-EDITED .kicad_dru still carried the
# aiee_* floors - the lumina-carrier failure path).
DRU_FLOOR_KEYS = {
    "aiee_track_width_floor": "min_trace_width_mm",
    "aiee_clearance_floor": "min_clearance_mm",
    "aiee_via_drill_floor": "min_via_drill_mm",
    "aiee_via_diameter_floor": "min_via_diameter_mm",
    "aiee_annular_floor": "min_annular_ring_mm",
    "aiee_edge_clearance_floor": "min_copper_to_edge_mm",
    "aiee_hole_to_hole_floor": "min_hole_to_hole_mm",
    "aiee_silk_width_floor": "min_silk_width_mm",
}


def load_capabilities(cap_file: Path | None = None) -> dict:
    path = Path(cap_file) if cap_file else CAP_FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FabFloorError(f"cannot read {path}: {exc}") from exc
    rows = (data or {}).get("design_rules")
    if not isinstance(rows, dict) or not rows:
        raise FabFloorError(f"{path} has no design_rules table")
    return rows


def capability_class(layers: int, outer_oz: float) -> str:
    """capabilities key, e.g. (4, 1.0) -> '4layer_1oz'."""
    oz = int(outer_oz) if float(outer_oz).is_integer() else outer_oz
    return f"{int(layers)}layer_{oz}oz"


def profile(layers: int, outer_oz: float,
            cap_file: Path | None = None) -> tuple[str, dict]:
    """-> (class name, capability row). Raises FabFloorError naming the
    available rows rather than silently falling back to a default profile -
    an unknown profile must never quietly become 1 oz."""
    rows = load_capabilities(cap_file)
    cls = capability_class(layers, outer_oz)
    if cls not in rows:
        raise FabFloorError(
            f"no capability row {cls!r} in {(cap_file or CAP_FILE).name} "
            f"(have: {', '.join(sorted(rows))})")
    row = rows[cls]
    missing = sorted(set(PRO_RULE_KEYS.values()) - set(row))
    if missing:
        raise FabFloorError(f"capability row {cls} lacks {missing}")
    return cls, row


def pro_rules(cap: dict) -> dict:
    """board.design_settings.rules block for a capability row."""
    return {pro_key: float(cap[cap_key])
            for pro_key, cap_key in PRO_RULE_KEYS.items()}


def pro_rule_severities() -> dict:
    """rule_severities entries that pin the fab floors to ERROR."""
    return dict(FLOOR_SEVERITIES)


def check_pro(pro: dict, cap: dict) -> list[str]:
    """Assert a .kicad_pro's floors against a capability row.

    -> [] when every injected minimum is >= the profile's and every floor
    check is at ERROR; otherwise one message per failure. `pro` is the
    parsed project dict (not a path), so callers can check a file, a
    freshly built dict, or a board's project in one line.
    """
    ds = ((pro or {}).get("board") or {}).get("design_settings") or {}
    rules = ds.get("rules") or {}
    sev = ds.get("rule_severities") or {}
    bad: list[str] = []
    for pro_key, cap_key in PRO_RULE_KEYS.items():
        want = float(cap[cap_key])
        got = rules.get(pro_key)
        if got is None:
            bad.append(f"rules.{pro_key} missing (fab floor {want:g} mm)")
        elif float(got) < want - 1e-9:
            bad.append(f"rules.{pro_key} {float(got):g} mm is BELOW the fab "
                       f"floor {want:g} mm")
    for check, level in FLOOR_SEVERITIES.items():
        got = sev.get(check)
        if got != level:
            bad.append(f"rule_severities.{check} is {got!r}, must be {level!r}"
                       " (a fab floor at warning hides real defects)")
    return bad


def _rule_block(dru_text: str, name: str) -> str | None:
    """Extract one balanced `(rule "name" ...)` block, or None.

    Paren-balanced scan (NOT a line regex): hand-edited DRUs close rules on
    the same line as their last clause (lumina-carrier style), and condition
    strings legally contain parens - quoted spans are skipped."""
    start = dru_text.find(f'(rule "{name}"')
    if start < 0:
        return None
    depth, in_str = 0, False
    for j in range(start, len(dru_text)):
        c = dru_text[j]
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return dru_text[start:j + 1]
    return None  # unbalanced - treat as missing


def check_dru(dru_text: str, cap: dict) -> list[str]:
    """Assert a .kicad_dru still carries the aiee_* fab-floor baseline.

    The DRU half of `check_pro` (ladder row 160): DRUs DO get hand-edited
    (carrier appended HV rules), and a dropped or lowered aiee_* rule was
    previously invisible until the P9 DFM gate. -> [] when every baseline
    rule in DRU_FLOOR_KEYS is present with `(min ...)` >= the capability
    row's floor; otherwise one message per failure. Same >= semantics as
    check_pro. Callable via `rules_gen.py --check-dru PATH`."""
    bad: list[str] = []
    for name, cap_key in DRU_FLOOR_KEYS.items():
        want = float(cap[cap_key])
        block = _rule_block(dru_text, name)
        if block is None:
            bad.append(f'rule "{name}" missing (fab floor {want:g} mm)')
            continue
        m = re.search(r"\(min\s+([0-9.]+)mm\)", block)
        if m is None:
            bad.append(f'rule "{name}" has no (min ...) constraint '
                       f"(fab floor {want:g} mm)")
        elif float(m.group(1)) < want - 1e-9:
            bad.append(f'rule "{name}" min {float(m.group(1)):g} mm is BELOW '
                       f"the fab floor {want:g} mm")
    return bad
