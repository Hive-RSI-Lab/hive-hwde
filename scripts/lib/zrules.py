"""zrules - the per-layer impedance geometry rules_gen writes into a
.kicad_dru, read back for the routers (#489).

rules_gen sizes a controlled-impedance net per stripline layer (#323) and
writes it as layer-scoped DRU rules:

  aiee_diff_w_<pair>_<tag>     track_width (min/opt/max)  (layer "In2.Cu")
  aiee_diff_gap_<pair>_<tag>   diff_pair_gap (min/opt)    (layer "In2.Cu")
  aiee_z<ohm>_w_<tag>          track_width (min/opt/max)  (layer "In2.Cu")

KiCad's DRC grades against exactly these rules, so the routers read the
same text instead of re-solving: route_critical (KRT) and route_auto's DSN
then draw each layer at the width DRC wants, and can never disagree with
rules_gen or with DRC.

layer_geometry(dru_text) -> {net: {layer: {"width_mm", "width_min_mm",
"width_max_mm", "gap_mm"?, "gap_min_mm"?}}}; the opt value is the width
(or gap) solved for the target impedance.
"""
from __future__ import annotations

import re
from pathlib import Path

_RULE_RE = re.compile(r'\(rule\s+"([^"]+)"(.*?)\n\)', re.S)
_LAYER_RE = re.compile(r'\(layer\s+"([^"]+)"\s*\)')
_CONSTRAINT_RE = re.compile(r"\(constraint\s+(track_width|diff_pair_gap)\s+(.*?)\)\s*\n")
_VAL_RE = re.compile(r"\((min|opt|max)\s+([\d.]+)\s*mm\)")
_NET_RE = re.compile(r"\.NetName\s*==\s*'([^']+)'")
_NAME_RE = re.compile(r"^aiee_(diff_w|diff_gap|z[0-9p]+_w)_")


def layer_geometry(dru_text: str) -> dict[str, dict[str, dict]]:
    """Per-net, per-layer impedance geometry from rules_gen's layer-scoped
    rules. Only rules named aiee_diff_w_/aiee_diff_gap_/aiee_z*_w_ that carry
    a quoted (layer "X") and an (opt ...) value count; a later rule for the
    same net/layer wins, as in KiCad."""
    out: dict[str, dict[str, dict]] = {}
    for m in _RULE_RE.finditer(dru_text or ""):
        name, body = m.group(1), m.group(2)
        kind = _NAME_RE.match(name)
        lay = _LAYER_RE.search(body)
        con = _CONSTRAINT_RE.search(body + "\n")
        if not (kind and lay and con):
            continue
        vals = {k: float(v) for k, v in _VAL_RE.findall(con.group(2))}
        if "opt" not in vals:
            continue
        cond = body[body.find("(condition"):] if "(condition" in body else ""
        # the BGA neck-down clause names courtyards, not nets: only the
        # NetName terms before it are members
        nets = _NET_RE.findall(cond.split("&& !(")[0])
        layer = lay.group(1)
        for net in nets:
            g = out.setdefault(net, {}).setdefault(layer, {})
            if con.group(1) == "track_width":
                g["width_mm"] = vals["opt"]
                g["width_min_mm"] = vals.get("min", vals["opt"])
                g["width_max_mm"] = vals.get("max", vals["opt"])
            else:
                g["gap_mm"] = vals["opt"]
                g["gap_min_mm"] = vals.get("min", vals["opt"])
    return out


def board_layer_geometry(pcb: Path) -> dict[str, dict[str, dict]]:
    """layer_geometry of the board's sibling .kicad_dru ({} when absent)."""
    dru = Path(pcb).with_suffix(".kicad_dru")
    if not dru.is_file():
        return {}
    return layer_geometry(dru.read_text(encoding="utf-8"))


def layer_widths(geo: dict[str, dict[str, dict]], nets) -> dict[str, float]:
    """{layer: width} the nets share (a pair's two halves carry the same
    rules). A layer whose widths differ between the nets takes the widest
    (a wider leg stays inside a +-10 % window better than a narrower one
    below the floor); a layer only one net has still counts."""
    out: dict[str, float] = {}
    for net in nets:
        for layer, g in (geo.get(net) or {}).items():
            if "width_mm" in g:
                out[layer] = max(out.get(layer, 0.0), g["width_mm"])
    return out
