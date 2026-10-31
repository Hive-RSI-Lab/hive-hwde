#!/usr/bin/env python
"""fabmaterial.py - which laminate a board is built from, and what that
means at JLC (fab floors, order options, price line).

FR-4 was the only material the pipeline knew: jlc_capabilities.yaml keyed
its rows on <layers>layer_<oz>oz, order_submit hard-coded plateType 1
(FR-4), and nothing read a stackup's material at all. A board designed on
RO4350B would have been checked against FR-4 floors and ordered as FR-4 -
the SI-09 failure (order stackup differs from design) in its worst form:
every RF width on the board solved for er 3.66 and fabricated on er 4.4.

This module is the one place a material enters:

  materials(cap_file)          -> the `materials:` table of jlc_capabilities
  family_of_names(names)       -> 'fr4' | '<family>' | 'hybrid:<a>+<b>'
  stackup_family(entry)        -> family of a stackups.yaml entry
  board_family(pcb)            -> family of a .kicad_pcb's own (stackup) block
  capability_suffix(family)    -> '' for FR-4, '_<family>' otherwise
  order_problems(family, spec) -> [] or why JLC can't build this order
  not_offered(family)          -> the refusal for a material JLC doesn't sell

The family comes from the dielectric material NAMES (the stackup block
board_init wrote from stackups.yaml), matched against each family's
`match` patterns. Anything that matches no non-FR-4 pattern is FR-4, so
every board made before this module keeps its FR-4 class. A stack with
more than one family is a hybrid, which JLC does not build (see
`not_offered` in jlc_capabilities.yaml): it is reported, never folded
into FR-4.

Library only (imported by fabfloors, board_init, rules_gen, dfm_check,
order_quote, order_submit, fab_export).
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml

REFERENCE = Path(__file__).resolve().parents[2] / "reference"
CAP_FILE = REFERENCE / "jlc_capabilities.yaml"

FR4 = "fr4"

_MATERIAL_RE = re.compile(r'\(material\s+"([^"]*)"\)')


class MaterialError(ValueError):
    """A material JLC doesn't build, or an order that contradicts it."""


def _load(cap_file: Path | None = None) -> dict:
    return _load_cached(str(Path(cap_file) if cap_file else CAP_FILE))


@functools.lru_cache(maxsize=8)
def _load_cached(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def materials(cap_file: Path | None = None) -> dict:
    """{family: row} from jlc_capabilities.yaml `materials:`."""
    return dict(_load(cap_file).get("materials") or {})


def not_offered_table(cap_file: Path | None = None) -> dict:
    return dict(_load(cap_file).get("not_offered") or {})


def family_of_name(name: str, table: dict | None = None) -> str:
    """Family of one dielectric material name. Non-FR-4 patterns first, so
    'RO4350B' never reads as FR-4; unmatched names are FR-4 (KiCad's own
    default material string is "FR4")."""
    table = materials() if table is None else table
    nots = not_offered_table()
    for fam, row in list(table.items()) + list(nots.items()):
        if fam == FR4:
            continue
        for pat in (row or {}).get("match") or []:
            if re.search(pat, name or "", re.IGNORECASE):
                return fam
    return FR4


def family_of_names(names, table: dict | None = None) -> str:
    """Family of a whole stack: one family, or 'hybrid:<a>+<b>' (sorted)
    when the dielectrics mix families."""
    table = materials() if table is None else table
    fams = sorted({family_of_name(n, table) for n in names})
    if not fams:
        return FR4
    if len(fams) == 1:
        return fams[0]
    return "hybrid:" + "+".join(fams)


def stackup_family(entry: dict) -> str:
    """Family of a stackups.yaml entry: its declared `material_family`, which
    must agree with its dielectric names (a mislabelled entry is an error,
    not a guess)."""
    names = [ly.get("material", "FR4") for ly in (entry or {}).get("stack") or []
             if ly.get("type") != "copper"]
    derived = family_of_names(names)
    declared = (entry or {}).get("material_family")
    if declared and declared != derived:
        raise MaterialError(
            f"stackup declares material_family {declared!r} but its "
            f"dielectrics read as {derived!r} ({', '.join(names)})")
    return derived


def _stackup_block(text: str) -> str:
    m = re.search(r"\(stackup\b", text)
    if not m:
        return ""
    depth = 0
    for j in range(m.start(), len(text)):
        c = text[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[m.start():j + 1]
    return text[m.start():]


def board_dielectrics(pcb: Path) -> list[dict]:
    """[{name, type, thickness_mm, material}] for each dielectric layer of a
    .kicad_pcb's (stackup) block, top to bottom. Whitespace-agnostic, so it
    reads board_init's one-line layers and KiCad's own multi-line save
    alike. [] when the board has no stackup block."""
    block = _stackup_block(
        Path(pcb).read_text(encoding="utf-8", errors="replace"))
    out = []
    for chunk in re.split(r'(?=\(layer\s+")', block)[1:]:
        name = re.match(r'\(layer\s+"([^"]*)"', chunk).group(1)
        typ = re.search(r'\(type\s+"([^"]*)"\)', chunk)
        if not typ or typ.group(1) not in ("core", "prepreg"):
            continue
        th = re.search(r"\(thickness\s+([\d.]+)", chunk)
        mat = _MATERIAL_RE.search(chunk)
        out.append({"name": name, "type": typ.group(1),
                    "thickness_mm": float(th.group(1)) if th else None,
                    "material": mat.group(1) if mat else "FR4"})
    return out


def board_materials(pcb: Path) -> list[str]:
    """Dielectric material names in a .kicad_pcb's (stackup) block, in
    order. [] when the board has no stackup block."""
    return [d["material"] for d in board_dielectrics(pcb)]


def board_family(pcb: Path) -> str:
    """Family of the board's own stackup block (the board is the truth an
    order must match; architecture/stackup.md is prose)."""
    return family_of_names(board_materials(pcb))


def is_hybrid(family: str) -> bool:
    return str(family).startswith("hybrid:")


def not_offered(family: str, cap_file: Path | None = None) -> str | None:
    """The refusal text when JLC doesn't build this family, else None."""
    nots = not_offered_table(cap_file)
    key = "hybrid" if is_hybrid(family) else family
    row = nots.get(key)
    if row is None and family != FR4 and family not in materials(cap_file):
        return (f"material family {family!r} is not in jlc_capabilities.yaml "
                "materials: - no JLC product is known for it")
    if row is None:
        return None
    repl = ", ".join(row.get("replacements") or []) or "see jlc_capabilities.yaml"
    return (f"JLC does not build {family}: {row.get('reason', '').strip()} "
            f"Use: {repl}")


def capability_suffix(family: str | None) -> str:
    """'' for FR-4 (the original keys stay as they were), '_<family>'
    otherwise. A hybrid or not-offered family raises."""
    if not family or family == FR4:
        return ""
    why = not_offered(family)
    if why:
        raise MaterialError(why)
    return f"_{family}"


def row(family: str, cap_file: Path | None = None) -> dict:
    """The materials: row for a family (FR-4 included)."""
    why = not_offered(family, cap_file)
    if why:
        raise MaterialError(why)
    return dict(materials(cap_file).get(family) or {})


def _as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def order_problems(family: str, spec: dict,
                   cap_file: Path | None = None) -> list[str]:
    """Why JLC can't build this order on this material - [] when it can.
    `spec` is order_submit's spec snapshot (layers, thickness_mm,
    copper_weight_oz, surface_finish, solder_mask_color, width_mm,
    height_mm). Unset options are not problems: they are order-time
    choices the material's own defaults fill (order_defaults)."""
    why = not_offered(family, cap_file)
    if why:
        return [why]
    r = materials(cap_file).get(family) or {}
    label = r.get("label", family)
    out: list[str] = []

    def bad(key, allowed, got, unit=""):
        out.append(f"{label}: {key} {got}{unit} is not offered "
                   f"(JLC sells {', '.join(f'{a}{unit}' for a in allowed)})")

    checks = (("layers", "layers", int, ""),
              ("thickness_mm", "finished_thickness_mm", float, " mm"),
              ("copper_weight_oz", "copper_oz", float, " oz"))
    for key, rkey, cast, unit in checks:
        allowed = _as_list(r.get(rkey))
        got = spec.get(key)
        if allowed and got is not None and \
                not any(abs(cast(got) - cast(a)) < 1e-6 for a in allowed):
            bad(key, allowed, cast(got), unit)
    for key, rkey in (("surface_finish", "surface_finish"),
                      ("solder_mask_color", "solder_mask_colors")):
        allowed = _as_list(r.get(rkey))
        got = spec.get(key)
        if allowed and got is not None and \
                str(got).lower() not in {str(a).lower() for a in allowed}:
            bad(key, allowed, got)
    mx = r.get("max_size_mm")
    w, h = spec.get("width_mm"), spec.get("height_mm")
    if mx and w and h:
        a, b = sorted((float(w), float(h)), reverse=True)
        if a > max(mx) + 1e-6 or b > min(mx) + 1e-6:
            out.append(f"{label}: board {float(w):g} x {float(h):g} mm "
                       f"exceeds JLC's {mx[0]} x {mx[1]} mm maximum")
    mn = r.get("min_size_mm")
    if mn and w and h and min(float(w), float(h)) < min(mn) - 1e-6:
        out.append(f"{label}: board {float(w):g} x {float(h):g} mm is under "
                   f"JLC's {mn[0]} x {mn[1]} mm minimum")
    return out


def order_defaults(family: str, cap_file: Path | None = None) -> dict:
    """Options the material fixes when the spec leaves them open (e.g. a
    high-frequency board is ENIG + green only)."""
    r = row(family, cap_file)
    out = {}
    for key, rkey in (("surface_finish", "surface_finish"),
                      ("solder_mask_color", "solder_mask_colors")):
        allowed = _as_list(r.get(rkey))
        if len(allowed) == 1:
            out[key] = allowed[0]
    return out


def order_notes(family: str, stackup: dict | None, board_name: str,
                impedance: list[dict] | None = None,
                cap_file: Path | None = None) -> str:
    """The human-readable order notes for fab/ORDER-NOTES.md: what to pick
    on JLC's quote page so the board built is the board designed."""
    r = row(family, cap_file)
    lines = [f"# Order notes - {board_name}", ""]
    lines.append(f"- Material: **{r.get('label', family)}**"
                 + (f" ({r['product']} product, quote-page plateType "
                    f"{r['plate_type']})" if r.get("product") else ""))
    if stackup:
        lines.append(f"- Stackup: {stackup.get('name', '?')} - "
                     f"{stackup.get('description', '').strip()}")
        diel = [ly for ly in stackup.get("stack") or []
                if ly.get("type") != "copper"]
        for ly in diel:
            lines.append(f"  - {ly.get('name')}: {ly.get('material')} "
                         f"{ly.get('thickness_mm')} mm, design er "
                         f"{ly.get('epsilon_r')}, tan d {ly.get('loss_tangent')}")
        if stackup.get("thickness_mm"):
            lines.append(f"- Board thickness to select: "
                         f"{stackup['thickness_mm']} mm")
    for key, rkey in (("Surface finish", "surface_finish"),
                      ("Solder mask", "solder_mask_colors"),
                      ("Copper", "copper_oz")):
        allowed = _as_list(r.get(rkey))
        if allowed:
            lines.append(f"- {key}: {' / '.join(str(a) for a in allowed)}"
                         + (" (the only option for this material)"
                            if len(allowed) == 1 else ""))
    if r.get("impedance_control") is False:
        lines.append("- Impedance control: NOT OFFERED by JLC on this "
                     "material - the line widths below are solved by hwde "
                     "from the laminate's design Dk; JLC will not tune them.")
    if impedance:
        lines.append("- Controlled lines as designed:")
        for ci in impedance:
            geo = f"w {ci.get('width_mm')} mm"
            if ci.get("gap_mm"):
                geo += f", gap {ci['gap_mm']} mm"
            lines.append(f"  - {ci.get('profile')}: {ci.get('impedance_ohm')} "
                         f"ohm {ci.get('kind')} on {ci.get('layer')}, {geo}")
    for note in _as_list(r.get("order_notes")):
        lines.append(f"- {str(note).strip()}")
    lines.append("")
    return "\n".join(lines)
