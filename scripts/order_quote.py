#!/usr/bin/env python
"""order_quote.py - JLCPCB quote matrix + lead times (SPEC P10).

Builds the qty x surface-finish x mask-colour x assembly matrix the ordering
agent presents at human checkpoint 5, from the board's real geometry (area and
layer count come from the board file, not from a guess) and the transcribed
price points in reference/jlc_pricing.yaml.

HONESTY CONTRACT: every figure here is an ESTIMATE and is labelled as one.
JLC's authoritative price depends on panelisation, promotions, region and
shipping; only the instant-quote page (or the credentialed API) can produce a
real number. The report always carries `estimated: true` and the deep link to
the authoritative quote page, so the human checkpoint compares against reality
instead of trusting this table.

A board with castellated pads (lib/castellation.py) gets the per-order
castellated-holes adder from jlc_pricing.yaml in every row's pcb cost
(`pcb.castellated`) and `spec.castellated_pads`; a board without them shows
neither key. Assembled, it is priced as Standard PCBA, the only service that
builds it, and `spec.pcba` says so; under 70 mm a side it says a panel with
rails and fiducials is needed. Quote that panel with --boards-per-panel: --qty
stays panels, `unit_cost` is per board.

Vias under JLC's free size add the via-size option the order will need
(`pcb.via_tier`, `spec.via_tier`). --via-fill none,epoxy prices POFV as a
separate row. --measured-pcb replaces the table's PCB line with a figure read
from the cart, labelled as such (`pcb.measured`). `components` is the parts at
parts.json's price breaks, respecting each part's minimum order.

CLI:
  order_quote.py --pcb board.kicad_pcb [--qty 5,10,30] [--assembly]
                 [--parts parts.json] [--finish HASL,ENIG] [--colors green,black]
                 [--pricing jlc_pricing.yaml] [--out quote.json]
                 [--boards-per-panel N] [--via-fill none,epoxy]
                 [--measured-pcb USD]
Exit 0 ok / 2 error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import yaml  # noqa: E402

import castellation  # noqa: E402
import fabmaterial  # noqa: E402
import geom  # noqa: E402
import safelib  # noqa: E402

PRICING = SCRIPTS.parent / "reference" / "jlc_pricing.yaml"


def load_pricing(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "pcb" not in data:
        raise ValueError(f"{path} is not a pricing table")
    return data


def board_size(pcb: Path) -> tuple[float, float, float]:
    """(width_mm, height_mm, area_dm2) from the board outline."""
    bg = geom.load_board(pcb)
    outline = bg.outline          # geom exposes this as an attribute
    if outline is None or outline.is_empty:
        raise ValueError(f"{pcb.name} has no board outline (Edge.Cuts)")
    x0, y0, x1, y1 = outline.bounds
    w, h = x1 - x0, y1 - y0
    return w, h, (w * h) / 10000.0


def _nearest_qty_price(table: dict, qty: int) -> tuple[float, int]:
    """Price at the closest listed quantity >= qty (else the largest listed),
    scaled linearly when qty exceeds every listed break."""
    breaks = sorted(int(k) for k in table)
    for b in breaks:
        if qty <= b:
            return float(table[b]), b
    top = breaks[-1]
    return float(table[top]) * qty / top, top


AREA_UNIT_MM2 = {"mm2": 1.0, "cm2": 100.0, "dm2": 10000.0, "in2": 645.16}


def area_cost(pricing: dict, layers: int, qty: int, w: float,
              h: float) -> dict:
    """A per-area price (pricing.model per_area, docs/vendors.md): rate per
    unit of board area for one set of `copies` boards (or per board with
    per: board), plus a per-order setup, never under min_price."""
    rate = pricing.get("rate") or {}
    r = rate.get(layers, rate.get(str(layers)))
    if r is None:
        raise ValueError(f"no per-area rate for {layers}-layer boards")
    unit = pricing.get("unit", "in2")
    if unit not in AREA_UNIT_MM2:
        raise ValueError(f"unknown area unit {unit!r}")
    area = w * h / AREA_UNIT_MM2[unit]
    copies = max(1, int(pricing.get("copies", 1)))
    multiples = -(-qty // copies)
    # per: set (default) - the rate buys one set of `copies` boards (OSH
    # Park); per: board - each board pays it, sold in sets of `copies`
    # (Aisler)
    per_board = pricing.get("per", "set") == "board"
    boards = float(r) * area * multiples * (copies if per_board else 1)
    setup = float(pricing.get("setup", 0.0))
    total = max(boards + setup, float(pricing.get("min_price", 0.0)))
    return {"base": round(boards, 2), "base_at_qty": multiples * copies,
            "setup": round(setup, 2), "area": round(area, 3),
            "area_unit": unit, "oversize": 0.0, "options": 0.0,
            "total": round(total, 2)}


def via_tier(bg) -> str | None:
    """The via-size option this board's smallest paid via forces, as JLC's
    hole size; None when every via is at the free size. dfm_check.JLC_VIA_TIER
    is the rule: under 0.2 costs more, and 0.2/0.25 under 0.45 diameter."""
    from dfm_check import JLC_VIA_TIER
    t_drill, t_small, t_dia = JLC_VIA_TIER
    tier = None
    for v in bg.vias_of():
        if not v.drill or v.drill <= 0:
            continue
        dr = round(v.drill, 3)
        if dr < t_drill:
            name = "0.15" if dr >= 0.15 else "0.1"
        elif dr <= t_small and round(v.diameter, 3) < t_dia:
            name = "0.2" if dr < t_small else "0.25"
        else:
            continue
        tier = name if tier is None else min(tier, name, key=float)
    return tier


def pcb_cost(pricing: dict, layers: int, qty: int, w: float, h: float,
             finish: str, color: str, thickness: float = 1.6,
             castellated: bool = False, material: str = "fr4",
             tier: str | None = None, via_fill: str = "none",
             measured: float | None = None) -> dict:
    if pricing.get("model") == "per_area":
        return area_cost(pricing, layers, qty, w, h)
    pcb = pricing["pcb"]
    if material and material != fabmaterial.FR4:
        # a Rogers/PTFE board is JLC's high-frequency product: its own
        # price line, never the FR-4 promo table
        hf = (pcb.get("high_frequency") or {}).get(material) or {}
        base_table = hf.get(layers) or hf.get(str(layers))
        if base_table is None:
            raise ValueError(f"no price points for {layers}-layer "
                             f"{material} boards (jlc_pricing.yaml "
                             "pcb.high_frequency)")
    else:
        base_table = pcb["base"].get(layers) or pcb["base"].get(str(layers))
    if base_table is None:
        raise ValueError(f"no price points for {layers}-layer boards")
    base, at_qty = _nearest_qty_price(base_table, qty)

    promo = float(pcb.get("promo_max_mm", 100))
    oversize = 0.0
    if w > promo or h > promo:
        # The base table is the price of the whole order at promo size, so a
        # bigger board scales it by area (no per-board, per-dm^2 adder: that
        # priced a 4L 120x100 at qty 100 near 15x the table's own point).
        oversize = base * max(0.0, (w * h) / (promo * promo) - 1.0)

    opts = pricing.get("options", {})
    adders = (float(opts.get("surface_finish", {}).get(finish, 0.0))
              + float(opts.get("solder_mask_color", {}).get(color, 0.0))
              + float(opts.get("thickness_mm", {}).get(thickness, 0.0)))
    # the per-order castellated-holes option, only for a board that has them
    cast = float(opts.get("castellated_holes", 0.0)) if castellated else 0.0
    # a via tier the table has no price for is an order option all the same
    tier_usd = float((opts.get("via_tier") or {}).get(tier, 0.0)) if tier else 0.0
    fill = 0.0
    if via_fill != "none":
        table = opts.get("via_fill_epoxy") or {}
        got = table.get(layers, table.get(str(layers)))
        if got is None:
            raise ValueError(f"no POFV price for {layers}-layer boards")
        fill = float(got)
    total = base + oversize + adders + cast + tier_usd + fill
    out = {"base": round(base, 2), "base_at_qty": at_qty,
           "oversize": round(oversize, 2), "options": round(adders, 2),
           "total": round(total, 2)}
    if castellated:
        out["castellated"] = round(cast, 2)
    if tier:
        out["via_tier"] = round(tier_usd, 2)
    if via_fill != "none":
        out["via_fill"] = round(fill, 2)
    if measured is not None:
        # the cart's own figure for this board and these options; only POFV,
        # priced as its own row, stays on top
        out["measured"] = round(measured, 2)
        out["total"] = round(measured + fill, 2)
    return out


def _at_most(table: list, n: int) -> float:
    """The value of the first [limit, value] row n fits under (else the last)."""
    for limit, value in table:
        if n <= limit:
            return float(value)
    return float(table[-1][1])


def standard_assembly_cost(pricing: dict, panels: int, n_parts: int,
                           n_joints: int, n_distinct: int, n_xray: int,
                           boards_per_panel: int, area_cm2: float) -> dict:
    """Standard PCBA for `panels` panels. n_* are per panel, except n_distinct."""
    a = pricing.get("assembly_standard")
    if not a:
        raise ValueError("pricing has no assembly_standard table")
    pieces = panels * boards_per_panel
    joints = max(float(a["per_joint"]) * n_joints,
                 float(a["min_assembly_per_panel"])) * panels
    feeders = float(a["per_part_feeder"]) * n_distinct
    fixture = float(a["fixture_fee"]) * _at_most(a["fixtures"], pieces)
    packing = (float(a["packing_base"])
               + float(a["packing_per_cm2"]) * area_cm2 * panels)
    n_x = n_xray * panels
    xray = _at_most(a["xray"], n_x) * n_x if n_x else 0.0
    setup, stencil = float(a["setup_fee"]), float(a["stencil"])
    return {"service": "standard", "setup": round(setup, 2),
            "stencil": round(stencil, 2), "feeders": round(feeders, 2),
            "joints": round(joints, 2), "fixture": round(fixture, 2),
            "packing": round(packing, 2), "xray": round(xray, 2),
            "n_parts": n_parts, "n_joints": n_joints,
            "n_distinct_parts": n_distinct, "n_xray": n_xray,
            "total": round(setup + stencil + feeders + joints + fixture
                           + packing + xray, 2)}


def components_cost(parts_json: Path, boards: int) -> dict:
    """The placed parts for `boards` boards at parts.json's price breaks, each
    bought in at least its minimum order. None priced: `unpriced` lists them."""
    data = json.loads(Path(parts_json).read_text(encoding="utf-8"))
    items = data.get("parts", data) if isinstance(data, dict) else data
    total, unpriced = 0.0, []
    for ent in items if isinstance(items, list) else []:
        if not isinstance(ent, dict) or \
                ent.get("assembly_class", PLACED) not in ASSEMBLED:
            continue
        need = len(ent.get("refdes") or []) * boards
        if not need:
            continue
        buy = max(need, int(ent.get("min_qty") or 1))
        breaks = sorted(ent.get("price_breaks") or [], key=lambda b: b["qty"])
        price = ent.get("price")
        for b in breaks:
            if buy >= b["qty"]:
                price = b["price"]
        if price is None:
            unpriced.append(ent.get("lcsc") or str(ent.get("refdes")))
            continue
        total += float(price) * buy
    out = {"total": round(total, 2), "boards": boards}
    if unpriced:
        out["unpriced"] = unpriced
    return out


def assembly_cost(pricing: dict, qty: int, n_parts: int, n_joints: int,
                  n_extended: int, stencil: bool = True) -> dict:
    a = pricing.get("assembly", {})
    setup = float(a.get("setup_fee", 0.0))
    feeders = float(a.get("per_extended_part_feeder", 0.0)) * n_extended
    joints = float(a.get("per_joint", 0.0)) * n_joints * qty
    sten = float(a.get("stencil", 0.0)) if stencil else 0.0
    total = setup + feeders + joints + sten
    return {"setup": round(setup, 2), "feeders": round(feeders, 2),
            "joints": round(joints, 2), "stencil": round(sten, 2),
            "n_parts": n_parts, "n_joints": n_joints,
            "n_extended_parts": n_extended, "total": round(total, 2)}


PLACED = "smt_placed"
PANEL_REF = re.compile(r"_B\d+$")
ASSEMBLED = (PLACED, "tht")   # bom_cpl.ASSEMBLED_CLASSES: in the upload


LEADLESS = re.compile(r"QFN|DFN|BGA|LGA|SON", re.I)


def _xray_parts(bg) -> int:
    """Leadless packages on the board, which Standard PCBA X-rays."""
    return sum(1 for fp in bg.footprints.values()
               if LEADLESS.search(fp.get("lib") or ""))


def _placed_refs(parts_json: Path | None) -> set[str] | None:
    """Refs parts.json lists, when it lists them: a footprint in no entry
    (copper feature, artwork, a test point) is not placed. None without refs,
    so an older parts.json counts as before."""
    if parts_json is None or not Path(parts_json).exists():
        return None
    data = json.loads(Path(parts_json).read_text(encoding="utf-8"))
    items = data.get("parts", data) if isinstance(data, dict) else data
    refs: set[str] = set()
    for ent in items if isinstance(items, list) else []:
        if not isinstance(ent, dict):
            continue
        if not ent.get("refdes"):
            return None
        refs.update(str(r) for r in ent["refdes"])
    return refs or None


def _unplaced_refs(parts_json: Path | None) -> set[str]:
    """Refs parts.json declares as NOT machine-placed (bom_cpl's assembly
    classes: line `assembly_class`, per-ref `refdes_class`). JLC places,
    and charges joints for, only smt_placed and tht parts."""
    if parts_json is None or not Path(parts_json).exists():
        return set()
    data = json.loads(Path(parts_json).read_text(encoding="utf-8"))
    items = data.get("parts", data) if isinstance(data, dict) else data
    out: set[str] = set()
    for ent in items if isinstance(items, list) else []:
        if not isinstance(ent, dict):
            continue
        per_ref = ent.get("refdes_class") or {}
        for ref in ent.get("refdes") or []:
            if per_ref.get(ref, ent.get("assembly_class", PLACED)) \
                    not in ASSEMBLED:
                out.add(str(ref))
    return out


def _assembly_counts(pcb: Path,
                     parts_json: Path | None) -> tuple[int, int, int, str]:
    """(n_parts, n_joints, n_extended, n_extended_source) from the board's
    real pads + parts.json.

    JLC's feeder fee is charged per UNIQUE Extended part (jlc_pricing.yaml
    assembly.note), so n_extended counts DISTINCT parts, never refs. The
    pipeline's parts.json (S6) is the per-distinct-part shape: one entry per
    part with a `basic` flag and NO refs/ref key - the old ref-based mapping
    produced n_extended == 0 on every pipeline board (13 of 24 Extended on
    pd-trigger priced at $0)."""
    bg = geom.load_board(pcb)
    unplaced = _unplaced_refs(parts_json)
    listed = _placed_refs(parts_json)
    refs: dict[str, int] = {}
    for pad in bg.pads_of():
        if pad.net is None or pad.ref in unplaced:
            continue
        # a panel's copies are <ref>_B<n>; parts.json has the single board's
        if listed is not None and PANEL_REF.sub("", pad.ref) not in listed:
            continue
        refs[pad.ref] = refs.get(pad.ref, 0) + 1
    n_parts = len(refs)
    n_joints = sum(refs.values())
    n_extended = 0
    source = "no_parts_json"
    if parts_json is not None and Path(parts_json).exists():
        data = json.loads(Path(parts_json).read_text(encoding="utf-8"))
        items = data.get("parts", data) if isinstance(data, dict) else data
        flagged = (isinstance(items, list)
                   and any(isinstance(e, dict) and ("basic" in e or "type" in e)
                           for e in items))
        if flagged:
            # entry-level counting: one dict entry == one distinct part in
            # both list shapes (per-distinct S6, or per-ref entries carrying
            # a basic flag); de-dupe by lcsc in case a part repeats
            seen: set = set()
            n_extended = 0
            for i, ent in enumerate(items):
                if not isinstance(ent, dict):
                    continue
                key = ent.get("lcsc") or ent.get("LCSC") or f"#idx{i}"
                if key in seen:
                    continue
                seen.add(key)
                if ent.get("assembly_class", PLACED) not in ASSEMBLED:
                    continue    # dnp / board_feature / hand_install: no feeder
                if not ent.get("basic", ent.get("type", "") == "Basic"):
                    n_extended += 1
            source = "per_distinct_entries"
        else:
            # fallback for shapes without any basic/type flag: count unique
            # parts (by lcsc) and treat unflagged parts as Extended - the
            # conservative (higher-fee) direction for an estimate
            import bom_cpl
            pmap = bom_cpl.load_parts_map(Path(parts_json))
            n_extended = len({(v.get("lcsc") or r)
                              for r, v in pmap.items()})
            source = "per_ref_lcsc_fallback_unflagged_as_extended"
    return n_parts, n_joints, n_extended, source


def _distinct_placed(parts_json: Path | None) -> int:
    """Distinct placed parts: Standard PCBA loads a feeder for each one."""
    if parts_json is None or not Path(parts_json).exists():
        raise ValueError("Standard PCBA (a castellated board) needs --parts: "
                         "its feeder fee is per distinct part")
    data = json.loads(Path(parts_json).read_text(encoding="utf-8"))
    items = data.get("parts", data) if isinstance(data, dict) else data
    return len({(e.get("lcsc") or f"#{i}") for i, e in enumerate(items)
                if isinstance(e, dict)
                and e.get("assembly_class", PLACED) in ASSEMBLED})


def run(pcb: Path, qtys: list[int], finishes: list[str], colors: list[str],
        assembly: bool = False, parts_json: Path | None = None,
        pricing_path: Path | None = None, thickness: float | None = None,
        vendor: str | None = None, boards_per_panel: int = 1,
        via_fills: list[str] | None = None,
        measured_pcb: float | None = None) -> dict:
    meta_v: dict = {}
    lead_v: dict = {}
    if vendor and not pricing_path:
        import vendors
        prof = vendors.load(vendor)
        meta_v = prof["meta"]
        lead_v = prof.get("lead_time_days") or {}
        src_v = ("reference/" + Path(prof["pricing_file"]).name
                 if prof.get("pricing_file")
                 else f"reference/vendors/{vendor}.yaml")
        pricing = prof["pricing"]
        if pricing.get("model") == "quote_only":
            return quote_only(pcb, prof)
        if pricing.get("model") == "per_area":
            # one area rate covers every finish/colour the vendor offers
            finishes, colors = finishes[:1], colors[:1]
            if assembly and not pricing.get("assembly"):
                raise ValueError(f"{meta_v.get('name', vendor)} publishes no "
                                 "assembly prices: quote it at "
                                 f"{meta_v.get('quote_url')}")
    else:
        pricing = load_pricing(pricing_path or PRICING)
    w, h, area_dm2 = board_size(pcb)
    bg = geom.load_board(pcb)
    layers = len(bg.stackup.copper_layers)
    n_cast = len(castellation.pads(bg))
    tier = via_tier(bg)
    via_fills = via_fills or ["none"]
    # Standard PCBA prices are JLC's: another vendor's table has no such row
    standard = bool(assembly and n_cast and pricing.get("assembly_standard"))
    # the board's laminate decides the product line and the options it
    # has: a RO4350B board is ENIG + green only, at JLC's HF prices
    material = fabmaterial.board_family(pcb)
    why = fabmaterial.not_offered(material)
    if why:
        raise ValueError(f"board laminate {material}: {why}")
    if material != fabmaterial.FR4 and meta_v \
            and meta_v.get("id") not in (None, "jlcpcb"):
        # the HF rows, options and price line are JLC's; another vendor's
        # FR-4 table would quietly price a Rogers board as FR-4
        raise ValueError(f"board laminate {material}: only JLC's "
                         "high-frequency line is encoded; quote it at "
                         f"{meta_v.get('quote_url') or meta_v.get('id')}")
    if material != fabmaterial.FR4:
        mrow = fabmaterial.row(material)
        allowed_f = [str(f) for f in mrow.get("surface_finish") or []]
        allowed_c = [str(c) for c in mrow.get("solder_mask_colors") or []]
        finishes = [f for f in finishes
                    if not allowed_f or f.upper() in
                    {a.upper() for a in allowed_f}] or allowed_f
        colors = [c for c in colors
                  if not allowed_c or c.lower() in
                  {a.lower() for a in allowed_c}] or allowed_c
    if thickness is None:
        # FR-4 keeps its 1.6 mm default; an HF board's thickness is its
        # stackup's finished one (board_init wrote it into (general))
        thickness = (round(float(bg.stackup.total_thickness), 3)
                     if material != fabmaterial.FR4 else 1.6)

    n_parts, n_joints, n_extended = (0, 0, 0)
    n_extended_source = None
    if assembly:
        n_parts, n_joints, n_extended, n_extended_source = \
            _assembly_counts(pcb, parts_json)

    n_distinct, n_xray = 0, 0
    if standard:
        n_xray = _xray_parts(bg)
        n_distinct = _distinct_placed(parts_json)

    matrix = []
    for qty in qtys:
        for finish in finishes:
            for color in colors:
                for fill in via_fills:
                    pc = pcb_cost(pricing, layers, qty, w, h, finish, color,
                                  thickness, castellated=n_cast > 0,
                                  material=material, tier=tier, via_fill=fill,
                                  measured=measured_pcb)
                    row = {"qty": qty, "surface_finish": finish,
                           "solder_mask_color": color, "pcb": pc,
                           "total": pc["total"]}
                    if fill != "none":
                        row["via_fill"] = fill
                    if assembly:
                        ac = (standard_assembly_cost(
                                pricing, qty, n_parts, n_joints, n_distinct,
                                n_xray, boards_per_panel, w * h / 100.0)
                              if standard else
                              assembly_cost(pricing, qty, n_parts, n_joints,
                                            n_extended))
                        row["assembly"] = ac
                        row["total"] = round(pc["total"] + ac["total"], 2)
                        if standard and parts_json is not None:
                            comp = components_cost(parts_json,
                                                   qty * boards_per_panel)
                            row["components"] = comp
                            row["total"] = round(row["total"] + comp["total"],
                                                 2)
                    if boards_per_panel > 1:
                        row["boards"] = qty * boards_per_panel
                    row["unit_cost"] = round(
                        row["total"] / (qty * boards_per_panel), 2)
                    matrix.append(row)
    matrix.sort(key=lambda r: (r["qty"], r["total"]))

    lt = pricing.get("lead_time_days") or lead_v
    pcb_lt = (lt.get("pcb", {}).get(layers)
              or lt.get("pcb", {}).get(str(layers)) or {})
    if material != fabmaterial.FR4:
        pcb_lt = lt.get("pcb", {}).get("high_frequency") or pcb_lt
    lead = {"pcb_days": pcb_lt,
            "assembly_additional_days": lt.get("assembly_additional")
            if assembly else 0,
            "note": lt.get("note")}

    # measured estimate-vs-API bias (jlc_pricing.yaml meta.measured_vs_api):
    # surfaces the KNOWN underestimate magnitude in the artifact the human
    # reads at H5, instead of leaving it as prose in SKILL/LEARNINGS
    src = src_v if meta_v else "reference/jlc_pricing.yaml"
    disclaimer = (f"Estimates from {src} - NOT a quote. "
                  "Confirm at the authoritative quote URL before ordering.")
    calibration = None
    mva = pricing.get("meta", {}).get("measured_vs_api")
    if isinstance(mva, list):
        ratios = []
        for pt in mva:
            if not isinstance(pt, dict):
                continue
            est, api = pt.get("estimate_pcb"), pt.get("api_price")
            if est and api:
                ratios.append(float(api) / float(est))
        if ratios:
            lo, hi = min(ratios), max(ratios)
            calibration = {
                "observed_underestimate":
                    f"{lo:.1f}-{hi:.1f}x low vs live API calculate (measured)",
                "points": mva,
            }
            disclaimer += (f" Measured live API prices ran {lo:.1f}-{hi:.1f}x "
                           "HIGHER than this table - treat these figures as "
                           "a LOWER BOUND.")

    spec = {"layers": layers, "width_mm": round(w, 2),
            "height_mm": round(h, 2), "area_dm2": round(area_dm2, 4),
            "thickness_mm": thickness, "assembly": assembly,
            "n_parts": n_parts, "n_joints": n_joints,
            "n_extended_parts": n_extended}
    if n_cast:
        # only on a castellated board, so every other spec stays as it was
        spec["castellated_pads"] = n_cast
        if assembly:
            spec["pcba"] = castellation.needs_standard_pcba(w, h)
    if tier:
        spec["via_tier"] = tier
    if boards_per_panel > 1:
        spec["boards_per_panel"] = boards_per_panel
    if measured_pcb is not None:
        spec["pcb_measured"] = True
    if material != fabmaterial.FR4:
        # only off FR-4, for the same reason: order_submit binds it
        spec["material"] = material
    if assembly:
        # only in assembly runs, so the P10 bench spec stays byte-identical
        spec["n_extended_source"] = n_extended_source

    payload = {
        "script": "order_quote",
        "status": "pass",
        "board": pcb.name,
        "estimated": True,
        "disclaimer": disclaimer,
        "authoritative_quote_url":
            pricing.get("meta", {}).get("authoritative_quote_url")
            or meta_v.get("quote_url"),
        "pricing_verified": pricing.get("meta", {}).get("verified")
        or pricing.get("verified") or meta_v.get("verified"),
        "currency": pricing.get("meta", {}).get("currency")
        or pricing.get("currency", "USD"),
        "spec": spec,
        "lead_time": lead,
        "matrix": matrix,
        "cheapest": matrix[0] if matrix else None,
    }
    if calibration is not None:
        payload["calibration"] = calibration
    if meta_v:
        payload["vendor"] = meta_v.get("id")
    return payload


def quote_only(pcb: Path, prof: dict) -> dict:
    """A vendor that publishes no prices: the board's spec and where to get
    the real number, no estimate at all."""
    w, h, area_dm2 = board_size(pcb)
    layers = len(geom.load_board(pcb).stackup.copper_layers)
    meta = prof["meta"]
    return {"script": "order_quote", "status": "quote_only",
            "board": pcb.name, "vendor": meta["id"], "estimated": False,
            "disclaimer": f"{meta.get('name')} publishes no price table: get "
                          "the price from its quote page.",
            "authoritative_quote_url": meta.get("quote_url"),
            "spec": {"layers": layers, "width_mm": round(w, 2),
                     "height_mm": round(h, 2),
                     "area_dm2": round(area_dm2, 4)},
            "matrix": [], "cheapest": None}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--qty", default="5,10,30",
                    help="comma list of quantities (default 5,10,30)")
    ap.add_argument("--finish", default="HASL,ENIG")
    ap.add_argument("--colors", default="green")
    ap.add_argument("--thickness", type=float, default=None,
                    help="board thickness, mm (default 1.6 on FR-4; the "
                         "board's own on a high-frequency laminate)")
    ap.add_argument("--assembly", action="store_true",
                    help="include SMT assembly in the matrix")
    ap.add_argument("--parts", help="parts.json (Basic/Extended feeder fees)")
    ap.add_argument("--pricing", help="override reference/jlc_pricing.yaml")
    ap.add_argument("--vendor", help="price at this vendor "
                    "(reference/vendors/<id>.yaml; default JLC)")
    ap.add_argument("--boards-per-panel", type=int, default=1,
                    help="--pcb is a panel of N boards: --qty is panels, "
                         "unit_cost is per board")
    ap.add_argument("--via-fill", default="none",
                    help="comma list of none,epoxy: POFV as a row of its own")
    ap.add_argument("--measured-pcb", type=float,
                    help="the cart's PCB price for this order, replacing the "
                         "table's")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        rep = run(Path(args.pcb),
                  [int(q) for q in args.qty.split(",") if q.strip()],
                  [f.strip() for f in args.finish.split(",") if f.strip()],
                  [c.strip() for c in args.colors.split(",") if c.strip()],
                  assembly=args.assembly,
                  parts_json=Path(args.parts) if args.parts else None,
                  pricing_path=Path(args.pricing) if args.pricing else None,
                  thickness=args.thickness, vendor=args.vendor,
                  boards_per_panel=args.boards_per_panel,
                  via_fills=[f.strip() for f in args.via_fill.split(",")
                             if f.strip()],
                  measured_pcb=args.measured_pcb)
    except Exception as exc:  # noqa: BLE001
        err = {"script": "order_quote", "status": "error",
               "error": f"{type(exc).__name__}: {exc}"}
        text = json.dumps(err, indent=1)
        (safelib.atomic_write_text(Path(args.out), text) if args.out
         else print(text))
        return 2

    text = json.dumps(rep, indent=1)
    (safelib.atomic_write_text(Path(args.out), text) if args.out
     else print(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
