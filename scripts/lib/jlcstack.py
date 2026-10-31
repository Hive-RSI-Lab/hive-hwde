"""jlcstack.py - JLC's published laminations (6 to 20+ layers, HDI) as
stackups.yaml entries.

JLC's impedance calculator serves every lamination it builds, without an
account:

  POST https://jlcpcb.com/api/jlcTools/impedance/selectPageImpedanceDefaultTemplate
       {"appointName": "<name or prefix>", "pageNum": N, "pageSize": 100}

A name prefix lists a family ("JLC08" every standard 8-layer, "JLCH08"
every 8-layer HDI); each template carries the layer-by-layer build
(basicDataList: copper foil, prepreg plies, cores with their copper, bare
cores), the dielectric constant of every ply, the finished thickness in its
display name, and for an HDI build the via spans the lamination is made
for (impedanceComponentStructResVO.viaHoleInfoList) and its lamination
cycles (massLaminationList). This module turns one template into the
stackups.yaml shape board_init, rules_gen and check_vias read; the network
is in stackup_fetch.py, so everything here runs offline on a fixture.

What a calculator template is and isn't: it is how JLC laminates that
build (the 4-layer entries' epsilon_r already come from here). It is not
proof the build can be ordered today: the order API's own list churns
(stackups.yaml header, JLC04161H-7628G). Entries made here say
`provenance.method: jlc_calculator_template` and `order_api_verified:
false` until someone with a JLC API account reads them back.

All lengths mm.
"""
from __future__ import annotations

import re

URL = ("https://jlcpcb.com/api/jlcTools/impedance/"
       "selectPageImpedanceDefaultTemplate")

# basicDataList materialType
FOIL, PREPREG, CORE, BARE_CORE = 1, 0, 2, 3
# viaHoleInfoList viaHoleType
VIA_LASER, VIA_MECH, VIA_THROUGH = 1, 2, 3

# JLC's tags in a template's display name (receptionDisplayName).
TAGS = {
    "免费": "free",              # no lamination surcharge
    "通用": "general",           # JLC's general-purpose default
    "普通": "standard",
    "特殊": "special",           # a special build: priced and reviewed
    "不要选用": "do_not_use",    # JLC: "do not select"
    "后续取消": "to_be_withdrawn",
    "叠构重复": "duplicate",
    "内层铜厚不一致": "mixed_inner_copper",
}
WITHDRAWN_TAGS = {"do_not_use", "to_be_withdrawn", "duplicate"}


def copper_name(i: int, n: int) -> str:
    """KiCad copper layer name of copper i (1-based, top down) of n."""
    if i == 1:
        return "F.Cu"
    if i == n:
        return "B.Cu"
    return f"In{i - 1}.Cu"


def copper_oz(t_mm: float) -> float:
    """Finished foil weight from its thickness: JLC writes 0.5 oz as 0.0152
    (inner) or 0.0175, 1 oz as 0.03-0.035, 2 oz as 0.07."""
    if t_mm < 0.025:
        return 0.5
    if t_mm < 0.05:
        return 1.0
    if t_mm < 0.085:
        return 2.0
    return round(t_mm / 0.035)


def tags(display: str) -> list[str]:
    return [v for k, v in TAGS.items() if k in (display or "")]


def finished_thickness(display: str) -> float | None:
    """'...(成品板厚1.59mm±10%)' -> 1.59 (JLC's finished-board figure)."""
    m = re.search(r"成品(?:板)?厚(?:度)?\s*([\d.]+)\s*mm", display or "", re.I)
    return float(m.group(1)) if m else None


def _series_er(plies: list[tuple[float, float]]) -> float:
    """Effective epsilon_r of stacked plies (h, er) between two coppers: the
    field crosses them in series, so 1/C adds: h/er_eff = sum(h_i/er_i)."""
    h = sum(p[0] for p in plies)
    return round(h / sum(p[0] / p[1] for p in plies), 3) if h else 4.5


def stack_from_template(tpl: dict) -> list[dict]:
    """The template's build as stackups.yaml `stack` rows, top to bottom:
    copper and dielectric layers, prepreg plies of one gap merged (KiCad
    models one dielectric per gap), with `plies` keeping JLC's breakdown."""
    rows: list[tuple[str, dict]] = []   # ("cu", {...}) / ("d", {...})
    for b in tpl["basicDataList"]:
        mt = b.get("materialType")
        if mt == FOIL:
            rows.append(("cu", {"t": float(b.get("topConductorThick") or 0.035)}))
        elif mt == CORE:
            rows.append(("cu", {"t": float(b.get("topConductorThick") or 0.0152)}))
            rows.append(("d", {"kind": "core", "h": float(b["dielectricThick"]),
                               "er": float(b["dielectricConstant"]),
                               "name": b.get("materialName") or ""}))
            rows.append(("cu", {"t": float(b.get("botConductorThick") or 0.0152)}))
        elif mt in (PREPREG, BARE_CORE):
            rows.append(("d", {"kind": "core" if mt == BARE_CORE else "prepreg",
                               "h": float(b["dielectricThick"]),
                               "er": float(b["dielectricConstant"]),
                               "name": b.get("materialName") or ""}))
        else:
            raise ValueError(f"{tpl.get('appointName')}: unknown materialType "
                             f"{mt!r} ({b.get('material')})")
    ncu = sum(1 for k, _ in rows if k == "cu")
    stack: list[dict] = []
    i_cu, i_d = 0, 0
    gap: list[dict] = []

    def flush():
        nonlocal i_d, gap
        if not gap:
            return
        i_d += 1
        h = round(sum(g["h"] for g in gap), 4)
        kinds = {g["kind"] for g in gap}
        row = {"name": f"dielectric {i_d}",
               "type": "core" if kinds == {"core"} else "prepreg",
               "thickness_mm": h,
               "material": "FR4 " + "+".join(_ply_short(g["name"]) for g in gap),
               "epsilon_r": _series_er([(g["h"], g["er"]) for g in gap]),
               "loss_tangent": 0.02}
        if len(gap) > 1 or row["type"] == "prepreg":
            row["plies"] = " + ".join(f"{_ply_short(g['name'])} {g['h']:g}"
                                      for g in gap)
        stack.append(row)
        gap = []

    for k, r in rows:
        if k == "cu":
            flush()
            i_cu += 1
            t = round(r["t"], 4)
            stack.append({"name": copper_name(i_cu, ncu), "type": "copper",
                          "thickness_mm": t, "copper_oz": copper_oz(t)})
        else:
            gap.append(r)
    if gap:
        raise ValueError(f"{tpl.get('appointName')}: dielectric below the "
                         f"last copper")
    return stack


def _ply_short(name: str) -> str:
    """'1080  RC67% 3.3mil' -> '1080'; '0.55mm H/H不含铜' -> 'core 0.55mm'."""
    name = (name or "").strip()
    m = re.match(r"(\d{3,4})(?![\d.])", name)
    if m:
        return m.group(1)
    m = re.match(r"([\d.]+mm)", name)
    return f"core {m.group(1)}" if m else (name.split()[0] if name else "?")


def via_spans(tpl: dict, n: int) -> list[dict]:
    """The via spans an HDI build is laminated for: [{kind, from, to,
    order}], kind micro (laser, one dielectric), buried or blind (a
    mechanical hole not reaching both outers) or through. [] for a standard
    build, which drills through holes only."""
    info = ((tpl.get("impedanceComponentStructResVO") or {})
            .get("viaHoleInfoList") or [])
    out = []
    for h in info:
        a, b = sorted((int(h["startPosition"]), int(h["endPosition"])))
        t = h.get("viaHoleType")
        if t == VIA_LASER:
            kind = "micro"
        elif t == VIA_THROUGH or (a == 1 and b == n):
            kind = "through"
        elif a == 1 or b == n:
            kind = "blind"
        else:
            kind = "buried"
        row = {"kind": kind, "from": copper_name(a, n), "to": copper_name(b, n)}
        if t == VIA_LASER:
            row["order"] = int(h.get("viaStages") or 0)
        out.append(row)
    out.sort(key=lambda r: (r["kind"] != "through", r["kind"],
                            r["from"], r["to"]))
    # one row per span
    seen, uniq = set(), []
    for r in out:
        k = (r["kind"], r["from"], r["to"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def laminations(tpl: dict, n: int) -> list[dict]:
    """Lamination cycles, innermost first: [{cycle, from, to, thickness_mm}]."""
    ml = ((tpl.get("impedanceComponentStructResVO") or {})
          .get("massLaminationList") or tpl.get("massLaminationList") or [])
    out = [{"cycle": int(m.get("massLaminationNo") or 0),
            "from": copper_name(int(m["startPosition"]), n),
            "to": copper_name(int(m["endPosition"]), n),
            "thickness_mm": float(m.get("massLaminationThickness") or 0)}
           for m in ml]
    return sorted(out, key=lambda r: r["cycle"])


def entry_from_template(tpl: dict, verified: str, solve=None) -> dict:
    """A stackups.yaml entry for one calculator template.

    `solve(stack_entry) -> controlled_impedance rows` fills the outer
    microstrip table (stackup_fetch passes impedance.py's solver); without
    it the table is left empty."""
    name = tpl["appointName"]
    n = int(tpl["plateLayerNumber"])
    stack = stack_from_template(tpl)
    ncu = sum(1 for s in stack if s["type"] == "copper")
    if ncu != n:
        raise ValueError(f"{name}: {ncu} copper layers in the build, "
                         f"template says {n}")
    display = tpl.get("receptionDisplayName") or name
    tg = tags(display)
    fin = finished_thickness(display)
    total = round(sum(s["thickness_mm"] for s in stack), 4)
    outer = stack[0]["copper_oz"]
    inner = float(tpl.get("innerCopperThickness") or 0.5)
    vias = via_spans(tpl, n)
    hdi = any(v["kind"] != "through" for v in vias)
    ent = {
        "description": (f"JLCPCB {n}-layer{' HDI' if hdi else ''}, "
                        f"{float(tpl['plateThickness']):g} mm nominal, "
                        f"{outer:g} oz outer / {inner:g} oz inner. "
                        f"JLC's own name: {display}."),
        "available": not (set(tg) & WITHDRAWN_TAGS),
        "provenance": {
            "method": "jlc_calculator_template",
            "endpoint": URL,
            "request": {"appointName": name},
            "template_name": name,
            "template_access_id": tpl.get("impedanceDefaultTemplateAccessId"),
            "display_name": display,
            "jlc_tags": tg,
            "live_verified": True,
            "order_api_verified": False,
            "verified": verified,
            "note": ("Every thickness and epsilon_r is JLC's calculator "
                     "template's; a gap of several plies carries their "
                     "series epsilon_r. Not yet read back from the order "
                     "API (no JLC account in the Hive): re-verify before "
                     "ordering controlled impedance."),
        },
        "layers": n,
        "thickness_mm": float(tpl["plateThickness"]),
        "stack_total_mm": total,
        "copper_finish": "ENIG" if hdi else "HASL",
        "dielectric_constraints": True,
        "epsilon_r_assumed": False,
        "epsilon_r_source": "jlc_calculator_template",
        "stack": stack,
    }
    if fin is not None:
        ent["finished_thickness_mm"] = fin
    if vias:
        ent["vias"] = vias
        lam = laminations(tpl, n)
        if lam:
            ent["laminations"] = lam
        ent["hdi_order"] = max([v.get("order", 0) for v in vias] + [0])
    ent["controlled_impedance"] = solve(ent) if solve else []
    return ent


_TPL_KEYS = ("appointName", "plateLayerNumber", "plateThickness",
             "cuprumThickness", "innerCopperThickness", "receptionDisplayName",
             "impedanceDefaultTemplateAccessId", "laminationType")
_PLY_KEYS = ("materialType", "material", "materialName", "dielectricConstant",
             "dielectricThick", "topConductorThick", "botConductorThick",
             "copperThickness")


def trim(tpl: dict) -> dict:
    """The part of a template this module reads (a fixture's shape): the
    raw one also carries JLC's internal ids, prices and audit fields."""
    out = {k: tpl.get(k) for k in _TPL_KEYS}
    out["basicDataList"] = [{k: b.get(k) for k in _PLY_KEYS}
                            for b in tpl.get("basicDataList") or []]
    s = tpl.get("impedanceComponentStructResVO") or {}
    out["impedanceComponentStructResVO"] = {
        "viaHoleInfoList": [{k: h.get(k) for k in (
            "viaHoleType", "startPosition", "endPosition", "viaStages",
            "viaHoleRadius", "adornBestrow")} for h in s.get("viaHoleInfoList") or []],
        "massLaminationList": [{k: m.get(k) for k in (
            "massLaminationNo", "startPosition", "endPosition",
            "massLaminationThickness")} for m in s.get("massLaminationList") or []],
    }
    return out
