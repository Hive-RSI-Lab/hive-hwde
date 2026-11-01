"""viastub.py - via stub geometry, quarter-wave resonance and back-drill
planning (hive-hwde #478). Used by check_via_stubs, via_backdrill and
fab_export.

A via's barrel past the last layer the signal uses is an open stub. Its first
resonance is the quarter wave, f = c / (4 L sqrt(eps)), where L runs from the
exit layer's far face to the end of the barrel and eps is the thickness-
weighted er of the dielectric it crosses. The via's pads load it with extra
capacitance, so a real notch sits at or below this figure: it is an upper
bound on f_res, not a prediction of the notch.

KiCad 10 keeps a back-drill on the via as `(secondary_drill|tertiary_drill
(size D) (layers START END))`, START the outer layer drilled from. Its drill
file gives the depth from the outer face to the MIDDLE of END, so the plated
barrel stops there.
"""
from __future__ import annotations

import math
from pathlib import Path

import yaml
from shapely.geometry import Point

from geom import _kid, _kids, _nums, _strs

C_MM_GHZ = 299.792458          # c in mm * GHz
WARN_RATIO = 3.0               # warn when f_res < 3 x knee
ERROR_RATIO = 1.5              # error when f_res < 1.5 x knee
# Not a published fab figure: the wide end of the +/-0.1-0.15 mm the fab
# guides in #478 quote. No fab profile carries a tolerance yet, so this is
# what a board is held to until backdrill.depth_tol_mm or the profile says.
DEFAULT_DEPTH_TOL_MM = 0.15
DEFAULT_OVERSIZE_MM = 0.2
REFERENCE = Path(__file__).resolve().parents[2] / "reference"


def f_res_ghz(length_mm: float, eps: float) -> float | None:
    """Quarter-wave resonance of an open stub; None for no stub."""
    if length_mm <= 0:
        return None
    return C_MM_GHZ / (4.0 * length_mm * math.sqrt(eps))


def max_stub_mm(f_ghz: float, eps: float) -> float:
    """The longest stub that still resonates at or above f_ghz."""
    return C_MM_GHZ / (4.0 * f_ghz * math.sqrt(eps))


def knee_ghz(t_rise_ns=None, data_rate_mbps=None, f_max_ghz=None) -> float | None:
    """The highest frequency the net has to carry. An edge: Johnson & Graham's
    knee, 0.5 / t_r. A data rate: the NRZ spectrum's first null, f = rate
    (twice Nyquist). An RF net: its top frequency. The largest stated wins."""
    out = []
    if t_rise_ns:
        out.append(0.5 / float(t_rise_ns))
    if data_rate_mbps:
        out.append(float(data_rate_mbps) / 1000.0)
    if f_max_ghz:
        out.append(float(f_max_ghz))
    return max(out) if out else None


class Stack:
    """Copper faces measured from the top of the board."""

    def __init__(self, stk):
        self.stk = stk
        self.layers = list(stk.copper_layers)
        self.top: dict[str, float] = {}
        self.bottom: dict[str, float] = {}
        z = 0.0
        for i, name in enumerate(self.layers):
            self.top[name] = z
            z += stk.copper_thickness.get(name, 0.0)
            self.bottom[name] = z
            if i < len(stk.dielectrics):
                z += stk.dielectrics[i]["height"]
        self.thickness = z

    def mid(self, layer: str) -> float:
        return (self.top[layer] + self.bottom[layer]) / 2.0

    def eps(self, a: str, b: str) -> float:
        """Thickness-weighted er of the dielectric between two layers."""
        i, j = sorted((self.layers.index(a), self.layers.index(b)))
        gaps = self.stk.dielectrics[i:j] or self.stk.dielectrics
        h = sum(d["height"] for d in gaps)
        return sum(d["epsilon_r"] * d["height"] for d in gaps) / h


def used_layers(bg, via) -> list[str]:
    """Layers of the via's span where its net's copper joins it, top to
    bottom: a track reaching the barrel's pad, an SMD pad on it, a same-net
    pour around it."""
    centre = Point(via.at)
    r = via.diameter / 2.0
    found = set()
    for t in bg.tracks_of(via.net):
        if t.layer in via.layers and t.shape.distance(centre) <= r:
            found.add(t.layer)
    for p in bg.pads_of(via.net):
        if p.drill is None and p.poly.intersects(via.poly):
            found.update(la for la in p.layers if la in via.layers)
    for z in bg.zones_of(via.net):
        for la in z.layers:
            if la in via.layers and z.fill_on(la).contains(centre):
                found.add(la)
    return [la for la in bg.copper_layers if la in found]


def read_backdrills(root) -> dict:
    """{(x, y): {"F"|"B": {"size", "end", "start"}}} from the board's vias."""
    out: dict = {}
    for v in _kids(root, "via"):
        at = _kid(v, "at")
        if at is None:
            continue
        x, y = _nums(at)[:2]
        for tok in ("secondary_drill", "tertiary_drill"):
            n = _kid(v, tok)
            lay = _kid(n, "layers") if n is not None else None
            size = _kid(n, "size") if n is not None else None
            if lay is None or size is None:
                continue
            names = _strs(lay)
            if len(names) < 2 or not _nums(size):
                continue
            side = "F" if names[0] == "F.Cu" else "B"
            out.setdefault((round(x, 4), round(y, 4)), {})[side] = {
                "size": _nums(size)[0], "start": names[0], "end": names[1]}
    return out


def stubs(st: Stack, via, used: list[str], drills: dict | None = None,
          tol: float = 0.0) -> list[dict]:
    """The via's open stubs, one per side that has any, for a via with at
    least two used layers. Past a back-drill, length_mm is what the drill
    leaves and worst_mm adds the depth tolerance. cuts_used names a used
    layer the drill removes."""
    out = []
    drills = drills or {}
    order = [la for la in st.layers if la in via.layers]
    for side, end, exit_ in (("F", order[0], used[0]), ("B", order[-1], used[-1])):
        drill = drills.get(side)
        if side == "F":
            full = st.top[exit_] - st.top[end]
        else:
            full = st.bottom[end] - st.bottom[exit_]
        row = {"side": side, "exit": exit_, "end": end,
               "eps": round(st.eps(exit_, end), 4), "full_mm": round(full, 4)}
        if drill is None:
            if full <= 1e-9:
                continue
            row.update(length_mm=round(full, 4), worst_mm=round(full, 4))
            out.append(row)
            continue
        stop = drill["end"]
        if side == "F":
            left = st.top[exit_] - st.mid(stop)
            depth = st.mid(stop) - st.top[end]
        else:
            left = st.mid(stop) - st.bottom[exit_]
            depth = st.bottom[end] - st.mid(stop)
        i_stop = st.layers.index(stop)
        cut = [la for la in used
               if (st.layers.index(la) <= i_stop if side == "F"
                   else st.layers.index(la) >= i_stop)]
        row.update(backdrill=drill, depth_mm=round(depth, 4),
                   barrel_left_mm=round(st.thickness - depth, 4),
                   length_mm=round(max(left, 0.0), 4),
                   worst_mm=round(max(left, 0.0) + tol, 4),
                   margin_mm=round(left - tol, 4), cuts_used=cut)
        out.append(row)
    return out


def plan_side(st: Stack, via, used: list[str], side: str, tol: float) -> dict | None:
    """The drill that leaves the shortest stub on one side and can't reach the
    exit layer at the far end of the depth tolerance. End layers are copper
    layers (KiCad's model), so it stops in the first layer far enough past
    the exit. Outer layers don't count: stopping there removes nothing."""
    order = [la for la in st.layers if la in via.layers]
    if side == "B":
        exit_, cand = used[-1], order[order.index(used[-1]) + 1:-1]
        left = lambda la: st.mid(la) - st.bottom[exit_]
    else:
        exit_ = used[0]
        cand = list(reversed(order[1:order.index(exit_)]))
        left = lambda la: st.top[exit_] - st.mid(la)
    for la in cand:
        if left(la) >= tol:
            return {"side": side, "exit": exit_, "end": la,
                    "left_mm": round(left(la), 4)}
    return None


def load_vendor_backdrill(vendor: str | None) -> dict:
    """A fab's `backdrill` block in reference/vendors/<id>.yaml ({} when it
    has none: unknown, not refused)."""
    p = REFERENCE / "vendors" / f"{vendor or 'jlcpcb'}.yaml"
    if not p.is_file():
        return {}
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("backdrill") or {}


def board_vendor(stk) -> tuple[str | None, bool]:
    """(vendor id, known). A stackups.yaml build names its fab (JLC's entries
    name none); a stack nobody listed leaves the fab unknown."""
    import check_vias
    import fabfloors
    data = yaml.safe_load(check_vias.STACKUPS.read_text(encoding="utf-8"))
    _, entry = check_vias.match_entry(stk, data.get("stackups") or {})
    if entry is None:
        return None, False
    return fabfloors.vendor_of(entry) or "jlcpcb", True


def backdrill_entries(cons: dict) -> list[dict]:
    return [e for e in (cons or {}).get("backdrill") or [] if isinstance(e, dict)]
