"""vendors.py - fab and assembly vendor profiles (reference/vendors/*.yaml).

docs/vendors.md is the schema. A profile either points at JLC-style files
(capabilities_file, pricing_file: JLC's own, kept where the pipeline always
read them) or carries the same blocks inline. load() resolves both shapes into
one dict:

  profile["capabilities"]  {"board", "design_rules", "castellated"?} - the
                           shape dfm_check and fabfloors read
  profile["pricing"]       a pricing dict with "model" (table | per_area |
                           quote_only)

capabilities_path(id) is a file dfm_check --capabilities takes as-is: the
profile itself when its rules are inline (its top-level design_rules /
castellated blocks are the same keys), else the file it points at.

Library only (vendor.py is the CLI).
"""
from __future__ import annotations

from pathlib import Path

import yaml

REFERENCE = Path(__file__).resolve().parents[2] / "reference"
VENDORS = REFERENCE / "vendors"
DEFAULT = "jlcpcb"
# rule keys a design_rules row must carry for dfm_check's gerber families
REQUIRED_RULES = ("min_trace_width_mm", "min_clearance_mm",
                  "min_hole_diameter_mm", "min_annular_ring_mm",
                  "min_copper_to_edge_mm")


class VendorError(ValueError):
    """No such vendor / a profile that can't be used."""


def ids(root: Path | None = None) -> list[str]:
    return sorted(p.stem for p in Path(root or VENDORS).glob("*.yaml"))


def path(vendor: str, root: Path | None = None) -> Path:
    p = Path(root or VENDORS) / f"{vendor}.yaml"
    if not p.is_file():
        raise VendorError(f"no vendor profile '{vendor}' "
                          f"(have: {', '.join(ids(root)) or 'none'})")
    return p


def _yaml(p: Path) -> dict:
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise VendorError(f"cannot read {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise VendorError(f"{p} is not a mapping")
    return data


def capabilities_path(vendor: str, root: Path | None = None) -> Path:
    p = path(vendor, root)
    ref = _yaml(p).get("capabilities_file")
    return (p.parent / ref).resolve() if ref else p


def load(vendor: str, root: Path | None = None) -> dict:
    p = path(vendor, root)
    prof = _yaml(p)
    meta = prof.get("meta") or {}
    if meta.get("id", vendor) != vendor:
        raise VendorError(f"{p}: meta.id {meta.get('id')!r} is not {vendor!r}")
    prof["meta"] = {**meta, "id": vendor}
    prof["path"] = str(p)

    cap_src = capabilities_path(vendor, root)
    caps = _yaml(cap_src) if cap_src != p else prof
    rows = caps.get("design_rules")
    if not isinstance(rows, dict) or not rows:
        raise VendorError(f"{cap_src} has no design_rules table")
    for k, row in rows.items():
        missing = [r for r in REQUIRED_RULES if r not in (row or {})]
        if missing:
            raise VendorError(f"{cap_src}: design_rules.{k} lacks {missing}")
    # the profile's own board block is the vendor-level truth; the
    # capabilities file's board block (JLC's) fills what it leaves out
    board = {**(caps.get("board") or {}), **(prof.get("board") or {})}
    prof["capabilities"] = {"board": board, "design_rules": rows}
    if isinstance(caps.get("castellated"), dict):
        prof["capabilities"]["castellated"] = caps["castellated"]
    prof["capabilities_file"] = str(cap_src)

    if prof.get("pricing_file"):
        pr = _yaml((p.parent / prof["pricing_file"]).resolve())
        pr.setdefault("model", "table")
        prof["pricing"] = pr
    else:
        pr = prof.get("pricing") or {"model": "quote_only"}
        if pr.get("model") not in ("table", "per_area", "quote_only"):
            raise VendorError(f"{p}: pricing.model must be table, per_area "
                              f"or quote_only, not {pr.get('model')!r}")
        prof["pricing"] = pr
    return prof


def load_all(names: list[str] | None = None,
             root: Path | None = None) -> list[dict]:
    return [load(v, root) for v in (names or ids(root))]


def rule_key(layers: int, copper_oz: float) -> str:
    return f"{int(layers)}layer_{float(copper_oz):g}oz"
