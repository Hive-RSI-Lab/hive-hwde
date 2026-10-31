"""fpattr.py - a footprint's placement type, `(attr smd|through_hole)`, against
its own pads.

KiCad reads the footprint's `(attr ...)` as how the part is fitted: the
position export, `kicad-cli pcb export stats` and bom_cpl's assembly class all
take it as given. EasyEDA's XR0850 power inductor (lite-main L1-L4) ships
`(attr through_hole)` on two SMD pads, so bom_cpl sent four reflowed
inductors to JLC's THT step (KiCad's KLC F6.1/F7.1 check the same thing in
the stock library). This module says what the pads make the part:

  smd           SMD pads and no plated through-hole pin
  through_hole  plated through-hole pins and no SMD pad
  (either)      both: an SMT part anchored by THT legs (USB-C shells,
                pin-in-paste) or a THT part with SMD tabs. Its own attr
                stands, unless it has none.
A plated hole whose centre lies inside an SMD pad is that pad's thermal via
(fpfix rule F numbers them so), not a pin: an exposed-pad HTSSOP stays smd.
np_thru_hole, connect (edge fingers) and pads without copper don't count.
A footprint marked `board_only`, `exclude_from_bom` or `virtual` isn't a
fitted part and is never judged; one with no pads either.

  judge(node, rot_offset=0.0) -> dict   the verdict on a parsed footprint
  set_type(text, new) -> str            rewrite the footprint text's (attr)
"""
from __future__ import annotations

import sys
from pathlib import Path

import sexpdata
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geom  # noqa: E402

TYPES = ("smd", "through_hole")
NOT_FITTED = frozenset({"board_only", "exclude_from_bom", "virtual"})


def _tok(x):
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def _kid(node, name):
    for c in node[1:] if isinstance(node, list) else []:
        if isinstance(c, list) and c and _tok(c[0]) == name:
            return c
    return None


def _nums(node) -> list[float]:
    return [float(x) for x in node[1:] if isinstance(x, (int, float))] \
        if node else []


def _has_copper(pad) -> bool:
    k = _kid(pad, "layers")
    return k is not None and any(str(_tok(t)).endswith(".Cu") for t in k[1:])


def _poly(pad, rot_offset: float):
    at, size = _nums(_kid(pad, "at")), _nums(_kid(pad, "size"))
    if len(at) < 2 or len(size) < 2 or len(pad) < 4:
        return None
    rr = _nums(_kid(pad, "roundrect_rratio"))
    rot = (at[2] if len(at) >= 3 else 0.0) + rot_offset
    return geom._pad_polygon(str(_tok(pad[3])), size[0], size[1],
                             rr[0] if rr else 0.25, (at[0], at[1]), rot)


def attrs_of(node) -> list[str]:
    a = _kid(node, "attr")
    return [str(_tok(t)) for t in a[1:]] if a is not None else []


def by_pads(smd: int, tht: int) -> str | None:
    """The placement type the pad counts give, None when both or neither."""
    if smd and not tht:
        return "smd"
    if tht and not smd:
        return "through_hole"
    return None


def judge(node, rot_offset: float = 0.0) -> dict:
    """The verdict on a parsed footprint (a board's or a library's).

    On a board a pad's angle is absolute: rot_offset (minus the footprint's
    angle) takes it back to the footprint's frame, as fpfix.fix_board does.
    Returns {attrs, declared, smd, tht, thermal_vias, pads_say, mismatch,
    fix}: `declared` is the attr's type (None when unspecified), `pads_say`
    the type the pads give, `mismatch` whether the two disagree, `fix` the
    type to write (None when only the part's drawing can settle it: both
    kinds of pad and no type at all)."""
    attrs = attrs_of(node)
    declared = next((t for t in TYPES if t in attrs), None)
    smd_polys, holes = [], []
    for c in node[1:]:
        if not (isinstance(c, list) and len(c) >= 3 and _tok(c[0]) == "pad"):
            continue
        ptype = str(_tok(c[2]))
        if not _has_copper(c):
            continue
        if ptype == "smd":
            p = _poly(c, rot_offset)
            smd_polys.append(p)
        elif ptype == "thru_hole":
            holes.append(_nums(_kid(c, "at")))
    thermal = 0
    for at in holes:
        if len(at) >= 2 and any(p is not None and p.contains(Point(at[0], at[1]))
                                for p in smd_polys):
            thermal += 1
    smd, tht = len(smd_polys), len(holes) - thermal
    says = by_pads(smd, tht)
    out = {"attrs": attrs, "declared": declared, "smd": smd, "tht": tht,
           "thermal_vias": thermal, "pads_say": says,
           "mismatch": False, "fix": None}
    if NOT_FITTED & set(attrs) or not (smd or tht):
        return out
    if says and says != declared:
        out.update(mismatch=True, fix=says)
    elif says is None and declared is None:
        out["mismatch"] = True          # mixed pads, no type: dropped from pos
    return out


def why(v: dict) -> str:
    """One line on a mismatch verdict."""
    decl = (f"(attr {v['declared']})" if v["declared"]
            else "no placement type (attr)")
    pads = []
    if v["smd"]:
        pads.append(f"{v['smd']} SMD pad(s)")
    if v["tht"]:
        pads.append(f"{v['tht']} plated through-hole pin(s)")
    if v["thermal_vias"]:
        pads.append(f"{v['thermal_vias']} thermal via(s) in SMD pads")
    s = f"{decl} but {' and '.join(pads) or 'no pads'}"
    if v["pads_say"] is None:
        return s + " - set smd (reflowed, THT legs anchor it) or " \
                   "through_hole (fitted in the THT step) by the part's drawing"
    return s + f" - the pads make it {v['pads_say']}"


def set_type(text: str, new: str) -> str:
    """Rewrite the placement type of one footprint's text (a .kicad_mod file
    or a board's footprint block), keeping every other attr flag. A
    footprint with no (attr) gets one after its (layer ...)."""
    import fpfix  # noqa: PLC0415  (fpfix imports this module)
    kids = fpfix.top_level_nodes(text)
    for head, s, e in kids:
        if head != "attr":
            continue
        toks = [t for t in text[s + 1:e - 1].split()[1:] if t not in TYPES]
        return text[:s] + "(attr " + " ".join([new] + toks) + ")" + text[e:]
    layer = next(((s, e) for h, s, e in kids if h == "layer"), None)
    if layer is None:
        raise ValueError("footprint has neither (attr) nor (layer)")
    s, e = layer
    indent = text[text.rfind("\n", 0, s) + 1:s]
    if indent.strip():          # (layer) shares its line: stay on it
        return text[:e] + f" (attr {new})" + text[e:]
    return text[:e] + f"\n{indent}(attr {new})" + text[e:]
