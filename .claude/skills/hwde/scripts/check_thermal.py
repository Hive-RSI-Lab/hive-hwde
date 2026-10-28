"""check_thermal.py - dissipation vs copper heatsink area (SPEC 6.3, P8).

One concern: a regulator / FET that dumps more heat than its copper can shed.
For each dissipating part named in constraints.json, estimate the junction-to-
ambient rise from the heatsink-net copper area and compare to the allowed rise.

Model (calibrated to datasheet anchors 2026-07; PROGRESS S5). theta_JA falls
off exponentially with copper pour area toward a floor:
    theta_JA(A) = theta_floor + (theta_0 - theta_floor) * exp(-A / tau)
    1 oz / 2-layer : theta_0=174, theta_floor=55, tau=350 mm^2
    2 oz / 4-layer : theta_0=140, theta_floor=45, tau=235 mm^2   (planes spread)
Rise ~= power_w * theta_JA(effective_area); a copper pour saturates near
~1 in^2 (645 mm^2), so past A_sat more copper barely helps - the fix there is
thermal vias to an inner/back plane, which the check flags separately. Every
number is +/-30%; this is a screen, not a sign-off (JEDEC JESD51 / TI SLOA122).

2-layer boards: the area curve alone floors at theta(A_SAT) ~74 C/W however
the layout is built, so a part with a thermal-via array into a back pour
(TI SLMA002 PowerPAD practice) could never meet a tight budget. The 2-layer
estimate is therefore the better of the area screen and a two-path network:
    top path : theta_top = theta_JA(A_top)  - the pour connected to the part's
               pads on its own side, through the same calibrated curve
    via path : R_vias + theta_JA(A_bot)     - the thermal vias / plated pad
               drills of the net under the part in parallel, into the back
               pour they land in, through the same curve again
    theta_2L = min(theta_JA(A_top + A_bot),  1 / (1/theta_top + 1/via_path))
One via conducts along its plated barrel only: R = t_board / (k_Cu * A_barrel),
A_barrel = pi*((d/2 + t_p)^2 - (d/2)^2), k_Cu = 380 W/m-K, t_p = 18 um (under
IPC-6012 class 2's 20 um average), unfilled (solder wicking ignored) - e.g.
~230 C/W for a 0.3 mm drill in 1.6 mm FR4; FR4 between vias is ignored. The
array is capped at VIA_BENEFIT_CAP. Conservative on purpose: re-using the
whole curve (package and convection included) on each path, and ignoring
solder fill and FR4 conduction, overstates theta. Example: 16 x 0.3 mm vias,
~120 mm2 connected top + ~640 mm2 bottom pour -> ~54 C/W (TPA3118D2 HTSSOP),
against TI's 22 C/W measured on its 2-layer EVM (SLOS708G 6.4). A bare pad with no vias gets the
area screen unchanged. 4-layer boards keep the area screen (it already sums
the inner planes). Thermal vias and copper weight on 4 layers are NOT
credited: the barrel network above needs a back pour that convects through
its own calibrated curve, and an inner plane has no such curve - re-running
MODEL_ML on each plane would count the planes twice, since that curve already
includes them. The one cited 4-layer via anchor found (TI SCBA017D Fig. 15:
a QFN's theta_JA falls 46.8 -> 29.9 C/W from JESD51-7 to JESD51-5, i.e. with
vias into the planes) is for a 2 mm logic pad and has not been checked
against a power package, so it is not applied here.

The via path needs a back pour to land in: when the back-side copper of the
net that those vias touch is smaller than the part's own pad hull (the vias
end on their own lands, or on a trace stub), the path is dropped and the part
gets the top pour alone. The threshold is a judgement, not a cited figure:
SLMA002 lands its 2-layer via arrays in a bottom-side copper area, and the
curve's theta_JA(0) = 174 C/W stands for a package on its pads, not for a
few via rings, so crediting that would be optimistic.

When a 2-layer part still fails, the error compares the budget against the
screen's best 2-layer case: VIA_BENEFIT_CAP vias (0.3 mm, SLMA002's drill, or
the largest drill found) into saturated pours on both sides, ~38 C/W on
1.6 mm FR4. A part over budget even there gets "cut the dissipation, add a
heatsink, or move to 4 layers" instead of "add thermal vias", because more
vias can't fix it within this model.

A part rated by its own datasheet for self-heating (an inductor's "Irms:
current for a 40 C rise", typically) is judged on that rating, not on the
board copper: the area curve is an IC-package curve (theta_JA(0) = 140-174
C/W for a part on its pads), and it says nothing about a 10 mm wound body
that sheds its heat from its own surface. The rating gives the part's own
theta, rise_c / (current_a^2 * dcr_ohm), and the entry's power_w (copper AND
core loss) rides on it. DCR is the 25 C value; at the rated rise the copper
is ~16 % more resistive, so the real rating-point loss is higher and this
theta errs high (conservative). Example: Coilcraft XAL1010-222, 2.8 mOhm,
24.5 A for 40 C -> 23.8 C/W, so 1.1 W -> 26 C (the area curve said 58 C/W).

A part may instead carry its datasheet theta_JA (theta_ja_c_w, with a
required theta_source naming the datasheet and its table/page). That figure
is measured on a JEDEC 2s2p board (JESD51-7: 76 x 114 mm, two solid inner
planes, the part alone dissipating), so it is used AS-IS: it replaces the
copper-area curve and the 2-layer via network, and is never combined with
area or via credit. It is optimistic on a crowded board whose other parts
heat the same copper, and on a layout without the vendor's thermal vias;
the via-count warning still runs against the copper floor for that reason.
A part takes a rating or a theta_ja_c_w, not both.

Forced air: an entry may declare the airflow over the part (airflow_lfm, or
airflow_m_s at 196.85 LFM per m/s; the board must say it has a fan). theta
is multiplied by the airflow factor, the LEAST improvement among TI's
modeled QFN tables (SCBA017D, Feb 2004, Tables 3-5, theta_JA C/W at 0 / 150
/ 250 / 500 LFM):
    20-pin JESD51-5  29.9 23.1 21.2 19.5     JESD51-7  46.8 40.5 38.2 36.0
    16-pin JESD51-5  31.2 24.4 22.5 20.7     JESD51-7  49.6 42.4 40.1 37.8
    14-pin JESD51-5  31.6 25.1 23.1 21.4     JESD51-7  52.5 46.0 42.9 40.5
The worst ratio at each speed is the 14-pin JESD51-7 row: x0.876 at 150,
x0.817 at 250, x0.771 at 500 LFM. Between points the factor is linear in
LFM (real convection gains are front-loaded, h ~ sqrt(v), so linear errs
high); past 500 LFM it stays at x0.771 (no extrapolation). The same factor
applies to a rated part and to a datasheet theta_JA (JEDEC publishes
forced-air theta_JA on the same 2s2p board). No airflow declared = factor 1.0 = still air,
exactly the model without it.

The corpus carries no thermal constraints, so this check is clean on all
goldens; supply parts via constraints.json to exercise it (a synthetic fixture
is tested).

CLI: --pcb board.kicad_pcb --constraints constraints.json [--out report.json]
     exit 0/1/2 per SPEC section 6.

constraints.json["thermal"] entries:
    {"ref": "U2",           # dissipating part refdes (its pads locate it)
     "power_w": 0.6,        # estimated dissipation
     "net": "GND",          # heatsink net (thermal-pad / tab net)
     "dt_c": 40,            # allowed junction-to-ambient rise (default 40)
     "min_vias": 9,         # optional explicit thermal-via floor
     "airflow_lfm": 200,    # optional forced air over the part (or
                            #   "airflow_m_s"; never both)
     "rating": {"rise_c": 40, "current_a": 24.5, "dcr_ohm": 0.0028,
                "source": "Coilcraft XAL1010 datasheet, Irms 40 C rise"}}
                            # optional datasheet self-heating rating;
                            #   source is required
     "theta_ja_c_w": 29,    # OR the datasheet JEDEC theta_JA, used as-is,
     "theta_source": "LMG2100R044 SNOSDF9B 5.4 Thermal Information"}
                            #   with its required source (not with rating)
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "check_thermal"
A_SAT_MM2 = 645.0             # ~1 in^2: copper pour saturates here
MODEL_2L = (174.0, 55.0, 350.0)   # theta_0, theta_floor, tau
MODEL_ML = (140.0, 45.0, 235.0)
DEFAULT_DT_C = 40.0
VIA_PITCH_MM = 1.1           # recommended thermal-via pitch
VIA_BENEFIT_CAP = 36         # array benefit flattens past ~36 vias
REACH_MM = (A_SAT_MM2 / math.pi) ** 0.5   # ~14.3 mm: heat spreads only so far
K_CU_W_MK = 380.0            # copper thermal conductivity
PLATING_MM = 0.018           # via barrel plating (IPC-6012 cl.2 avg is 0.020)
DEFAULT_BOARD_MM = 1.6
BEST_VIA_DRILL_MM = 0.3      # SLMA002's recommended thermal-via drill
LFM_PER_M_S = 196.85
# TI SCBA017D Tables 3-5: modeled theta_JA (C/W) at AIRFLOW_LFM (module doc)
AIRFLOW_LFM = (0.0, 150.0, 250.0, 500.0)
AIRFLOW_ANCHORS = {
    "QFN-20 JESD51-5": (29.9, 23.1, 21.2, 19.5),
    "QFN-16 JESD51-5": (31.2, 24.4, 22.5, 20.7),
    "QFN-14 JESD51-5": (31.6, 25.1, 23.1, 21.4),
    "QFN-20 JESD51-7": (46.8, 40.5, 38.2, 36.0),
    "QFN-16 JESD51-7": (49.6, 42.4, 40.1, 37.8),
    "QFN-14 JESD51-7": (52.5, 46.0, 42.9, 40.5),
}
# least improvement at each speed across the anchors (conservative)
AIRFLOW_FACTORS = tuple(max(t[i] / t[0] for t in AIRFLOW_ANCHORS.values())
                        for i in range(len(AIRFLOW_LFM)))


def airflow_factor(lfm: float) -> float:
    """theta multiplier for forced air at `lfm`; 1.0 in still air."""
    if lfm < 0:
        raise CheckError(f"airflow must be >= 0 LFM, got {lfm}")
    pts = list(zip(AIRFLOW_LFM, AIRFLOW_FACTORS))
    for (x0, f0), (x1, f1) in zip(pts, pts[1:]):
        if lfm <= x1:
            return f0 + (f1 - f0) * (lfm - x0) / (x1 - x0)
    return AIRFLOW_FACTORS[-1]


def entry_airflow_lfm(entry: dict) -> float:
    """The entry's declared forced air in LFM (0 = still air)."""
    lfm, ms = entry.get("airflow_lfm"), entry.get("airflow_m_s")
    if lfm is not None and ms is not None:
        raise CheckError(f"thermal {entry.get('ref')}: give airflow_lfm or "
                         "airflow_m_s, not both")
    try:
        if lfm is not None:
            return float(lfm)
        return float(ms) * LFM_PER_M_S if ms is not None else 0.0
    except (TypeError, ValueError):
        raise CheckError(f"thermal {entry.get('ref')}: airflow must be a "
                         f"number: {lfm if lfm is not None else ms!r}") from None


def rated_theta_cw(entry: dict) -> float:
    """The part's own theta from its datasheet self-heating rating."""
    r = entry["rating"]
    ref = entry.get("ref")
    if not isinstance(r, dict) or not str(r.get("source", "")).strip():
        raise CheckError(f"thermal {ref}: rating needs rise_c, current_a, "
                         "dcr_ohm and a source")
    try:
        rise, amps, dcr = (float(r[k]) for k in
                           ("rise_c", "current_a", "dcr_ohm"))
    except (KeyError, TypeError, ValueError):
        raise CheckError(f"thermal {ref}: rating needs numeric rise_c, "
                         f"current_a and dcr_ohm: {r}") from None
    if min(rise, amps, dcr) <= 0:
        raise CheckError(f"thermal {ref}: rating values must be > 0: {r}")
    return rise / (amps ** 2 * dcr)


def datasheet_theta_cw(entry: dict) -> float:
    """The datasheet's JEDEC theta_JA for the part, validated."""
    ref = entry.get("ref")
    if "rating" in entry:
        raise CheckError(f"thermal {ref}: give rating or theta_ja_c_w, "
                         "not both")
    if not str(entry.get("theta_source", "")).strip():
        raise CheckError(f"thermal {ref}: theta_ja_c_w needs a theta_source "
                         "(datasheet and table/page)")
    try:
        theta = float(entry["theta_ja_c_w"])
    except (TypeError, ValueError):
        raise CheckError(f"thermal {ref}: theta_ja_c_w must be a number: "
                         f"{entry['theta_ja_c_w']!r}") from None
    if not theta > 0:
        raise CheckError(f"thermal {ref}: theta_ja_c_w must be > 0: {theta}")
    return theta


def theta_ja(area_mm2: float, multilayer: bool) -> float:
    t0, tfloor, tau = MODEL_ML if multilayer else MODEL_2L
    a = max(0.0, min(area_mm2, A_SAT_MM2))
    return tfloor + (t0 - tfloor) * math.exp(-a / tau)


def via_r_cw(drill_mm: float, board_mm: float = DEFAULT_BOARD_MM) -> float:
    """Barrel conduction of one plated, unfilled via (C/W); inf if no drill."""
    if drill_mm <= 0:
        return math.inf
    r = drill_mm / 2.0
    a_m2 = math.pi * ((r + PLATING_MM) ** 2 - r ** 2) * 1e-6
    return (board_mm * 1e-3) / (K_CU_W_MK * a_m2)


def via_array_r_cw(drills_mm: list[float],
                   board_mm: float = DEFAULT_BOARD_MM) -> float:
    """Parallel vias (the VIA_BENEFIT_CAP largest drills); inf if none."""
    best = sorted((d for d in drills_mm if d > 0), reverse=True)
    g = sum(1.0 / via_r_cw(d, board_mm) for d in best[:VIA_BENEFIT_CAP])
    return 1.0 / g if g > 0 else math.inf


def theta_2l_network(a_top: float, a_bot: float, r_vias: float,
                     min_bot: float = 0.0) -> float:
    """Top pour in parallel with (via array + back pour); see module doc.
    No vias, or a back pour smaller than `min_bot`, leaves the top pour alone."""
    top = theta_ja(a_top, False)
    if math.isinf(r_vias) or a_bot <= 0 or a_bot < min_bot:
        return top
    return 1.0 / (1.0 / top + 1.0 / (r_vias + theta_ja(a_bot, False)))


def _connected_area(copper, seeds, reach) -> float:
    """Area of the copper pieces within reach that touch any seed shape."""
    local = copper.intersection(reach)
    pieces = getattr(local, "geoms", [local])
    return sum(g.area for g in pieces
               if not g.is_empty and any(g.intersects(s) for s in seeds))


def part_region(bg: geom.BoardGeom, ref: str):
    """(centroid, side_layer, footprint_area, pads) for a refdes."""
    pads = bg.pads_of(ref=ref)
    if not pads:
        raise CheckError(f"thermal part {ref!r} has no pads on board")
    cx = sum(p.center[0] for p in pads) / len(pads)
    cy = sum(p.center[1] for p in pads) / len(pads)
    side = "F.Cu" if sum("F.Cu" in p.layers for p in pads) >= \
        sum("B.Cu" in p.layers for p in pads) else "B.Cu"
    hull = geom._union([p.poly for p in pads]).convex_hull
    return (cx, cy), side, hull.area, pads


def check_part(bg: geom.BoardGeom, entry: dict):
    ref = entry.get("ref")
    net = entry.get("net")
    if not ref or not net or "power_w" not in entry:
        raise CheckError(f"thermal entry needs ref/net/power_w: {entry}")
    if net not in bg.nets:
        raise CheckError(f"thermal net {net!r} not on board")
    power = float(entry["power_w"])
    dt = float(entry.get("dt_c", DEFAULT_DT_C))
    (cx, cy), side, fp_area, _ = part_region(bg, ref)
    multilayer = len(bg.copper_layers) >= 4

    # heatsink copper = the net's copper WITHIN reach of the part (heat spreads
    # ~1 in radius; a distant pour of the same net elsewhere on the board is not
    # a heatsink for this part). Summed over layers because thermal vias tie the
    # part's pad to inner/back planes; capped where copper stops helping.
    reach = Point(cx, cy).buffer(REACH_MM)
    a_eff = min(A_SAT_MM2, sum(bg.net_copper(net, layer).intersection(reach).area
                               for layer in bg.copper_layers))
    theta = theta_ja(a_eff, multilayer)

    # thermal vias of the net under the part (needed once copper saturates):
    # board vias plus the plated drills of the net's own pads there (a
    # footprint's thermal-via pads, a THT tab) - both tie the layers together
    region = Point(cx, cy).buffer(max(2.0, math.sqrt(fp_area / math.pi) + 1.5))
    vias = [v for v in bg.vias_of(net) if region.contains(Point(v.at))]
    drilled = [p for p in bg.pads_of(net=net) if p.drill and len(p.layers) > 1
               and region.contains(Point(p.center))]
    n_vias = len(vias) + len(drilled)

    network = {}
    if not multilayer and len(bg.copper_layers) == 2:
        bot = next(lyr for lyr in bg.copper_layers if lyr != side)
        board_mm = bg.stackup.total_thickness or DEFAULT_BOARD_MM
        r_vias = via_array_r_cw(
            [v.drill for v in vias] + [min(p.drill) for p in drilled], board_mm)
        seeds = [v.poly for v in vias] + [p.poly for p in drilled]
        a_top = min(A_SAT_MM2, _connected_area(
            bg.net_copper(net, side),
            [p.poly for p in bg.pads_of(net=net, ref=ref)], reach))
        a_bot = min(A_SAT_MM2, _connected_area(
            bg.net_copper(net, bot), seeds, reach)) if seeds else 0.0
        theta_net = theta_2l_network(a_top, a_bot, r_vias, min_bot=fp_area)
        best_drill = max([BEST_VIA_DRILL_MM] + [v.drill for v in vias]
                         + [min(p.drill) for p in drilled])
        theta_best = theta_2l_network(
            A_SAT_MM2, A_SAT_MM2,
            via_array_r_cw([best_drill] * VIA_BENEFIT_CAP, board_mm))
        network = {"area_top_mm2": checklib.rnd(a_top),
                   "area_bottom_mm2": checklib.rnd(a_bot),
                   "via_array_r_cw": (None if math.isinf(r_vias)
                                      else checklib.rnd(r_vias)),
                   "back_pour_reached": bool(seeds) and a_bot >= fp_area,
                   "theta_area_cw": checklib.rnd(theta),
                   "theta_network_cw": checklib.rnd(theta_net),
                   "theta_best_2l_cw": checklib.rnd(theta_best)}
        theta = min(theta, theta_net)

    # a datasheet self-heating rating replaces the copper model; declared
    # forced air scales whichever theta is used (module doc, SCBA017D)
    lfm = entry_airflow_lfm(entry)
    af = airflow_factor(lfm)
    rated = "rating" in entry
    extra = {}
    if rated:
        extra["theta_model_cw"] = checklib.rnd(theta)
        theta = rated_theta_cw(entry)
        extra["theta_rated_cw"] = checklib.rnd(theta)
    datasheet = "theta_ja_c_w" in entry
    if datasheet:
        extra["theta_model_cw"] = checklib.rnd(theta)
        theta = datasheet_theta_cw(entry)
        extra["theta_datasheet_cw"] = checklib.rnd(theta)
    if lfm > 0:
        extra.update(theta_still_air_cw=checklib.rnd(theta),
                     airflow_lfm=checklib.rnd(lfm),
                     airflow_factor=checklib.rnd(af))
        theta *= af
    rise = power * theta

    # copper alone bottoms out at theta_ja(A_SAT) (the clamp), NOT the model's
    # asymptotic floor - if the target is below that, only vias/planes reach it.
    # A rated part's heat path is its own body, so no via array is asked of it.
    floor_cw = theta_ja(A_SAT_MM2, multilayer) * af
    need_vias = (dt / power) < floor_cw if power > 0 and not rated else False
    min_vias = int(entry.get("min_vias",
                             min(VIA_BENEFIT_CAP,
                                 max(4, math.ceil(fp_area / VIA_PITCH_MM ** 2)))))

    violations: list[dict] = []
    if rise > dt + 1e-6:
        saturated = a_eff >= A_SAT_MM2 - 1e-6
        remedy = ("add thermal vias to an inner/back plane" if saturated or
                  need_vias else f"grow the {net} pour")
        where = f"into {a_eff:.0f} mm2 of {net} copper"
        if rated:
            r = entry["rating"]
            where = (f"by its own rating ({float(r['rise_c']):.0f} C at "
                     f"{float(r['current_a']):g} A)")
            remedy = ("cut the loss, pick a part rated for more current, "
                      "or add forced air")
        elif datasheet:
            where = (f"on its datasheet theta_JA "
                     f"({extra['theta_datasheet_cw']:g} C/W, JEDEC board)")
            remedy = "cut the loss, add a heatsink, or add forced air"
        elif network:
            where = (f"through {network['area_top_mm2']:.0f} mm2 top + "
                     f"{network['area_bottom_mm2']:.0f} mm2 back {net} copper "
                     f"and {n_vias} thermal via(s)")
            best = network["theta_best_2l_cw"] * af
            if power * best > dt + 1e-6:
                remedy = (f"even the best 2-layer case ({VIA_BENEFIT_CAP} vias "
                          f"into full pours, ~{best:.0f} C/W) gives "
                          f"~{power * best:.0f} C: cut the dissipation, add a "
                          "heatsink, or move to 4 layers")
            elif not network["back_pour_reached"] or n_vias < VIA_BENEFIT_CAP:
                remedy = f"add thermal vias into a back-side {net} pour"
            else:
                remedy = f"grow the top and back {net} pours"
        if lfm > 0:
            where += f" at {lfm:.0f} LFM"
        violations.append(violation(
            SCRIPT, "error", (cx, cy), side, net, [ref],
            f"{ref} dissipates {power:.2f} W {where}: "
            f"~{rise:.0f} C rise (theta_JA ~{theta:.0f} C/W) "
            f"> {dt:.0f} C allowed; {remedy}", SCRIPT, kind="thermal_area",
            power_w=power, area_mm2=checklib.rnd(a_eff),
            theta_ja=checklib.rnd(theta), rise_c=checklib.rnd(rise),
            dt_allowed_c=dt))
    if need_vias and n_vias < min_vias:
        violations.append(violation(
            SCRIPT, "warning", (cx, cy), side, net, [ref],
            f"{ref} ({power:.2f} W) needs a thermal-via array to {net} "
            f"(copper alone tops out ~{floor_cw:.0f} C/W); found {n_vias} "
            f"via(s), want >= {min_vias}", SCRIPT, kind="thermal_vias",
            vias=n_vias, required=min_vias, power_w=power))

    facts = {"ref": ref, "net": net, "power_w": power,
             "area_mm2": checklib.rnd(a_eff), "theta_ja": checklib.rnd(theta),
             "rise_c": checklib.rnd(rise), "dt_allowed_c": dt,
             "vias_near_part": n_vias, "multilayer": multilayer, **network,
             **extra}
    return violations, facts


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Dissipation vs copper heatsink area (theta_JA screen).")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--constraints", required=True,
                    help="constraints.json with a thermal list")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    cons = checklib.load_json(args.constraints, "constraints")
    bg = geom.load_board(Path(args.pcb))
    bg.assert_fresh()

    violations: list[dict] = []
    checked: list[dict] = []
    for entry in cons.get("thermal", []):
        vs, facts = check_part(bg, entry)
        violations.extend(vs)
        checked.append(facts)

    payload = checklib.report(SCRIPT, args.pcb, violations, checked=checked)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
