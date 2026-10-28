#!/usr/bin/env python
"""constraints_lint.py - machine-validate research fragments + merged constraints.

P1 interface fragments and the P2-merged constraints.json feed every later
script through the shapes in reference/constraints_schema.md - and a key
emitted wrong is a key the pipeline silently ignores (the interface-spec
prompt used to carry that trap as prose; lumina-carrier's P1 close validated
8 fragments by hand). This lint makes the failure class deterministic:

  errors (exit 1) - schema-shape violations in the schema-typed sections:
    section of the wrong type, non-object entry, missing required key, wrong
    value type, bad enum (coating, placement edge), and MISSPELLED keys (a
    close match >= 0.8 to a documented key, e.g. max_skew -> max_skew_mm,
    voltage -> voltages). Unreadable/invalid JSON is also an error - the
    artifact itself is broken.
  warnings (exit 0, advisory) - unknown keys with no close documented match:
    ad-hoc envelope prose at the top level and entry extras (the power.json
    thermal_constraints notes/role class); scout candidates lists over the
    6-entry contract (research-component-scout.md).
  keys starting with '_' are the comment convention (_comment, _p8_basis,
    _p2_original_*) and are allowed at every level, never reported.

Envelope aliases: power_constraints / thermal_constraints / voltage_constraints
(research-power-architect's power.json) validate as power / thermal / voltages.
Other documented envelope keys (block, candidates, decisions, rails, notes,
...) pass silently; reference/constraints_schema.md stays the human-
authoritative doc and tests/test_constraints_lint.py pins this script's
key-set to it so they cannot drift apart.

Script contract (SPEC.md section 6): argparse, JSON to stdout or --out,
ASCII, no interactivity. Exit 0 = pass (warnings advisory), 1 = errors,
2 = cannot run (missing file, empty glob).
"""
from __future__ import annotations

import argparse
import difflib
import glob as globmod
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "constraints_lint"
CLOSE_MATCH_CUTOFF = 0.8

# --- shapes (mirror reference/constraints_schema.md; test-pinned) -----------
SECTIONS: dict[str, dict[str, dict[str, str]]] = {
    "high_speed": {
        "required": {"net": "str", "reference": "str_or_map"},
        "optional": {"k": "num", "t_rise_ns": "num",
                     "return_via_radius_mm": "num", "impedance_ohm": "num"},
    },
    "power": {
        "required": {"net": "str", "current_a": "num"},
        "optional": {"dt_c": "num", "via_amps": "num", "pdn": "bool",
                     "plane_fed": "bool", "overrides": "overrides"},
    },
    "diff_pairs": {
        "required": {"p": "str", "n": "str"},
        "optional": {"base": "str", "impedance_ohm": "num", "gap_mm": "num",
                     "max_skew_mm": "num", "max_uncoupled_mm": "num",
                     "term_pair_mm": "num", "operating_point": "map"},
    },
    # route_cleanup --snap-only leaves these nets as routed
    "length_match": {
        "required": {"nets": "str_list"},
        "optional": {"tol_mm": "num", "reason": "str"},
    },
    "voltages": {
        "required": {"net": "str", "voltage": "num"},
        "optional": {},
    },
    "voltage_pairs": {
        "required": {"a": "str", "b": "str", "voltage": "num"},
        "optional": {},
    },
    "thermal": {
        "required": {"ref": "str", "power_w": "num"},
        "optional": {"net": "str", "dt_c": "num", "min_vias": "num",
                     "airflow_lfm": "num", "airflow_m_s": "num",
                     "rating": "map", "theta_ja_c_w": "num",
                     "theta_source": "str"},
    },
    "planes": {
        "required": {"layer": "str", "net": "str"},
        "optional": {"region": "rect"},
    },
    # U4: P2's machine-readable block list - knowledge.py --select keys
    # record retrieval on each entry's topology token. U13: operating_point
    # = unit-suffixed dims ({vin_v: 12, edge_ns: 5, switching_kind: hard})
    # that knowledge.py --coverage tests against record envelopes.
    "blocks": {
        "required": {"topology": "str"},
        "optional": {"block": "str", "name": "str", "operating_point": "map"},
    },
}
PLACEMENT: dict[str, dict[str, dict[str, str]]] = {
    "edges": {"required": {"ref": "str", "edge": "edge"},
              "optional": {"pos": "num", "rot": "num"}},
    "groups": {"required": {"name": "str", "anchor": "str",
                            "members": "str_list"},
               "optional": {}},
    # rect-or-poly requirement enforced separately
    "keepouts": {"required": {},
                 "optional": {"rect": "rect", "poly": "point_list",
                              "side": "str", "reason": "str"}},
    "separation": {"required": {"a": "str_list", "b": "str_list",
                                "min_mm": "num"},
                   "optional": {"reason": "str"}},
    # T6 P6A-5: consumed by place_anneal as a keep-clear cost term. It was
    # never added here, so every board declaring one drew an unknown_key
    # warning for a documented key (found in U19).
    "corridors": {"required": {"a": "str", "b": "str"},
                  "optional": {"width_mm": "num", "net": "str",
                               "reason": "str"}},
    # U19: the side a part must be assembled on. A pin, not a request - the
    # annealer never flips a pinned cluster.
    "sides": {"required": {"ref": "str", "side": "side"},
              "optional": {"reason": "str"}},
}
OVERRIDE = {"required": {"near": "pair", "radius_mm": "num",
                         "current_a": "num"}, "optional": {}}
COATINGS = {"none", "soldermask", "conformal"}
EDGES = {"left", "right", "top", "bottom"}
SIDES = {"front", "back"}
SCHEMA_TOP_KEYS = set(SECTIONS) | {"placement", "coating"}
ENVELOPE_ALIASES = {"power_constraints": "power",
                    "thermal_constraints": "thermal",
                    "voltage_constraints": "voltages"}
# documented envelope vocabulary (scout/refdesign/power.json + fragment
# headers) - recognized, never warned about
KNOWN_ENVELOPE = {
    "interface", "role", "research_doc", "notes", "block", "board",
    "candidates", "count", "results", "script", "source", "date",
    "generated", "recommendation", "rails", "decisions", "errata",
    "layout_notes", "open", "summary",
    # strobe drive-stage.json envelope; would otherwise close-match the U4
    # "blocks" key and misfire as a misspelling
    "subblocks",
}
SCOUT_MAX_CANDIDATES = 6


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_ok(spec: str, v) -> bool:
    if spec == "str":
        return isinstance(v, str)
    if spec == "num":
        return _is_num(v)
    if spec == "bool":
        return isinstance(v, bool)
    if spec == "edge":
        return isinstance(v, str) and v in EDGES
    if spec == "side":
        return isinstance(v, str) and v in SIDES
    if spec == "str_or_map":
        return isinstance(v, str) or (
            isinstance(v, dict)
            and all(isinstance(k, str) and isinstance(x, str)
                    for k, x in v.items()))
    if spec == "str_list":
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if spec == "rect":
        return (isinstance(v, list) and len(v) == 4
                and all(_is_num(x) for x in v))
    if spec == "pair":
        return (isinstance(v, list) and len(v) == 2
                and all(_is_num(x) for x in v))
    if spec == "point_list":
        return isinstance(v, list) and all(
            isinstance(p, list) and len(p) == 2 and all(_is_num(x) for x in p)
            for p in v)
    if spec == "overrides":
        return isinstance(v, list)
    if spec == "map":       # flat scalar map (operating points): str keys,
        return isinstance(v, dict) and all(     # number/str/bool values
            isinstance(k, str) and (_is_num(x) or isinstance(x, (str, bool)))
            for k, x in v.items())
    return False  # unknown spec = programmer error, fail loud


class _FileLint:
    """Accumulates violations for one file."""

    def __init__(self, path: str):
        self.path = path
        self.violations: list[dict] = []

    def add(self, severity: str, kind: str, msg: str, json_path: str) -> None:
        self.violations.append(checklib.violation(
            "constraints", severity, None, None, None, [], msg, SCRIPT,
            kind=kind, file=self.path, json_path=json_path))

    # -- key auditing -------------------------------------------------------
    def _unknown_key(self, key: str, known: set[str], json_path: str) -> None:
        """Misspelled (close match to a documented key) = error; else warn."""
        close = difflib.get_close_matches(
            key, sorted(known), n=1, cutoff=CLOSE_MATCH_CUTOFF)
        if close:
            self.add("error", "misspelled_key",
                     f"{json_path}: unknown key '{key}' - did you mean "
                     f"'{close[0]}'? (unknown keys are silently ignored "
                     f"by consumers)", json_path)
        else:
            self.add("warning", "unknown_key",
                     f"{json_path}: key '{key}' is not in "
                     "constraints_schema.md - consumers will ignore it",
                     json_path)

    def entry(self, shape: dict, entry, json_path: str) -> None:
        """Validate one object entry against a required/optional key spec."""
        if not isinstance(entry, dict):
            self.add("error", "bad_entry",
                     f"{json_path}: entry must be an object, got "
                     f"{type(entry).__name__}", json_path)
            return
        known = set(shape["required"]) | set(shape["optional"])
        for key, spec in shape["required"].items():
            if key not in entry:
                self.add("error", "missing_key",
                         f"{json_path}: required key '{key}' missing",
                         json_path)
            elif not _type_ok(spec, entry[key]):
                self.add("error", "bad_type",
                         f"{json_path}.{key}: expected {spec}, got "
                         f"{json.dumps(entry[key])[:60]}", f"{json_path}.{key}")
        for key, val in entry.items():
            if key.startswith("_") or key in shape["required"]:
                continue
            if key in shape["optional"]:
                if not _type_ok(shape["optional"][key], val):
                    self.add("error", "bad_type",
                             f"{json_path}.{key}: expected "
                             f"{shape['optional'][key]}, got "
                             f"{json.dumps(val)[:60]}", f"{json_path}.{key}")
                elif key == "overrides":
                    for i, ov in enumerate(val):
                        self.entry(OVERRIDE, ov, f"{json_path}.overrides[{i}]")
            else:
                self._unknown_key(key, known, json_path)

    def section(self, name: str, value, json_path: str) -> None:
        if not isinstance(value, list):
            self.add("error", "bad_type",
                     f"{json_path}: '{name}' must be a list, got "
                     f"{type(value).__name__}", json_path)
            return
        for i, e in enumerate(value):
            self.entry(SECTIONS[name], e, f"{json_path}[{i}]")

    def placement(self, value, json_path: str) -> None:
        if not isinstance(value, dict):
            self.add("error", "bad_type",
                     f"{json_path}: 'placement' must be an object, got "
                     f"{type(value).__name__}", json_path)
            return
        for key, val in value.items():
            if key.startswith("_"):
                continue
            if key == "fixed":
                if not _type_ok("str_list", val):
                    self.add("error", "bad_type",
                             f"{json_path}.fixed: expected a list of refdes "
                             "strings", f"{json_path}.fixed")
            elif key in PLACEMENT:
                if not isinstance(val, list):
                    self.add("error", "bad_type",
                             f"{json_path}.{key}: must be a list",
                             f"{json_path}.{key}")
                    continue
                for i, e in enumerate(val):
                    p = f"{json_path}.{key}[{i}]"
                    self.entry(PLACEMENT[key], e, p)
                    if (key == "keepouts" and isinstance(e, dict)
                            and "rect" not in e and "poly" not in e):
                        self.add("error", "missing_key",
                                 f"{p}: keepout needs 'rect' or 'poly'", p)
            else:
                self._unknown_key(key, set(PLACEMENT) | {"fixed"},
                                  f"{json_path}.{key}")

    def top(self, data) -> list[str]:
        """Lint one parsed document; returns its non-underscore top keys."""
        if not isinstance(data, dict):
            self.add("warning", "not_object",
                     f"top level is {type(data).__name__}, not an object - "
                     "no schema-typed sections to lint", "$")
            return []
        keys = [k for k in data if not k.startswith("_")]
        for key in keys:
            val = data[key]
            path = f"$.{key}"
            if key in SECTIONS:
                self.section(key, val, path)
            elif key in ENVELOPE_ALIASES:
                self.section(ENVELOPE_ALIASES[key], val, path)
            elif key == "placement":
                self.placement(val, path)
            elif key == "coating":
                if not (isinstance(val, str) and val in COATINGS):
                    self.add("error", "bad_enum",
                             f"{path}: coating must be one of "
                             f"{sorted(COATINGS)}, got {json.dumps(val)[:40]}",
                             path)
            elif key == "notes":
                if not isinstance(val, list):
                    self.add("error", "bad_type",
                             f"{path}: notes must be a list", path)
            elif key == "candidates":
                if isinstance(val, list) and len(val) > SCOUT_MAX_CANDIDATES:
                    self.add("warning", "scout_candidates_over",
                             f"{path}: {len(val)} candidates - the scout "
                             f"contract is max {SCOUT_MAX_CANDIDATES}; full "
                             "sweeps belong in research/raw/ via "
                             "parts_search --out", path)
            elif key in KNOWN_ENVELOPE:
                pass
            else:
                self._unknown_key(
                    key, SCHEMA_TOP_KEYS | set(ENVELOPE_ALIASES), path)
        return keys


def lint_file(path: Path) -> tuple[list[dict], dict]:
    fl = _FileLint(str(path).replace("\\", "/"))
    fact = {"path": fl.path, "top_keys": [], "errors": 0, "warnings": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        fl.add("error", "invalid_json",
               f"cannot parse {fl.path}: {type(exc).__name__}: {exc}", "$")
        data = None
    if data is not None:
        fact["top_keys"] = fl.top(data)
    fact["errors"] = sum(1 for v in fl.violations if v["severity"] == "error")
    fact["warnings"] = sum(
        1 for v in fl.violations if v["severity"] == "warning")
    return fl.violations, fact


def resolve_files(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pat in patterns:
        if any(c in pat for c in "*?["):
            hits = sorted(globmod.glob(pat))
            if not hits:
                raise CheckError(f"glob matched no files: {pat}")
            files.extend(Path(h) for h in hits)
        else:
            p = Path(pat)
            if not p.is_file():
                raise CheckError(f"file does not exist: {pat}")
            files.append(p)
    return files


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", nargs="+", required=True,
                    help="research fragment / constraints.json path(s); "
                         "glob patterns are expanded")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    violations: list[dict] = []
    facts: list[dict] = []
    for path in resolve_files(args.file):
        vs, fact = lint_file(path)
        violations.extend(vs)
        facts.append(fact)
    payload = checklib.report(SCRIPT, None, violations, files=facts,
                              schema_keys=sorted(SCHEMA_TOP_KEYS))
    if not any(v["severity"] == "error" for v in violations):
        payload["status"] = "pass"  # warnings are advisory
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
