#!/usr/bin/env python
"""check_via_stubs.py - via stubs that resonate inside a fast net's band, and
the back-drills declared to remove them (P8 verify; board only; hive-hwde
#478).

A through via taking a signal from L1 to L2 leaves the rest of the barrel
hanging as an open stub, whose quarter wave notches the channel (HyperLynx and
SIwave flag it; Altium and Allegro carry a max-stub rule). Each via on a net
in constraints `high_speed`, `diff_pairs`, `rf` or `backdrill` is measured
(lib/viastub.py): the layers the net really uses there, the stub each side,
f_res = c / (4 L sqrt(eps)), against the net's knee frequency:

  t_rise_ns / rise_ps   0.5 / t_r (Johnson & Graham)
  data_rate_mbps        the rate (NRZ first null)
  f_max_ghz             an RF net's top frequency
  else                  check_si's class edge for the net's name

A net with none of these is listed in `no_edge`, measured, and not judged.

Findings:
  via_stub_resonance  f_res < 3 x knee warns, < 1.5 x errors; the message names
                      the longest stub that clears and the fixes (blind via, a
                      layer pair with a shorter stub, back-drill). A back-
                      drilled side is judged at its worst case (+ tolerance).
  backdrill_missing   error: a via on a net listed in `backdrill` that a drill
                      could shorten has none on the board (run via_backdrill)
  backdrill_cuts_net  error: the drill removes a layer the net uses
  backdrill_too_deep  error: nominal remaining stub under the depth tolerance,
                      so a drill at the far end of it reaches the exit layer
  backdrill_stub_long warning: worst-case remaining stub over the entry's
                      max_stub_mm
  backdrill_not_offered error: the board's fab doesn't back-drill (JLC)
  backdrill_fab_unknown warning: the fab's profile doesn't say, or the board's
                      stack is no stackups.yaml build

Depth tolerance: backdrill entry `depth_tol_mm`, else the fab profile's
`backdrill.depth_tolerance_mm`, else viastub.DEFAULT_DEPTH_TOL_MM (reported as
`tolerance_source`).

CLI: --pcb board.kicad_pcb [--constraints constraints.json] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sexpdata

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import check_si  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402
import viastub  # noqa: E402

SCRIPT = "check_via_stubs"
SOURCE = SCRIPT


def net_specs(bg, cons: dict) -> dict:
    """net -> {t_rise_ns, data_rate_mbps, f_max_ghz} of every declared net."""
    out: dict = {}

    def add(net, e):
        if not net:
            return
        s = out.setdefault(net, {})
        rise = e.get("t_rise_ns")
        if rise is None and e.get("rise_ps"):
            rise = float(e["rise_ps"]) / 1000.0
        for k, v in (("t_rise_ns", rise),
                     ("data_rate_mbps", e.get("data_rate_mbps")),
                     ("f_max_ghz", e.get("f_max_ghz"))):
            if v is not None:
                s[k] = v

    for e in cons.get("high_speed") or []:
        if isinstance(e, dict):
            add(e.get("net"), e)
    for e in cons.get("diff_pairs") or []:
        if isinstance(e, dict):
            add(e.get("p"), e)
            add(e.get("n"), e)
    for e in cons.get("rf") or []:
        if isinstance(e, dict):
            add(e.get("net"), e)
    for e in viastub.backdrill_entries(cons):
        add(e.get("net"), e)
    classes = check_si.resolve(bg, cons) if not all(out.values()) else {}
    for net, s in out.items():
        if not s and classes.get(net, {}).get("t_rise_ns"):
            s["t_rise_ns"] = classes[net]["t_rise_ns"]
            s["from_class"] = classes[net].get("class")
    return out


def fixes(st, via, used, row, need_mm) -> str:
    """What would clear this stub, in the board's own layers."""
    better = []
    order = [la for la in st.layers if la in via.layers]
    for la in order:
        if row["side"] == "B":
            left = st.bottom[order[-1]] - st.bottom[la]
        else:
            left = st.top[la] - st.top[order[0]]
        if left <= need_mm and la != row["exit"]:
            better.append(la)
    pair = (f"route the run on {' or '.join(better)} (stub under {need_mm:.2f} mm); "
            if better else "")
    return (f"Fix: a blind via; {pair}or back-drill "
            f"(constraints backdrill + via_backdrill; not at JLC)")


def check(pcb: Path, cons: dict) -> tuple[list[dict], dict]:
    bg = geom.load_board(pcb)
    root = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    st = viastub.Stack(bg.stackup)
    drills = viastub.read_backdrills(root)
    specs = net_specs(bg, cons)
    entries = {e.get("net"): e for e in viastub.backdrill_entries(cons)}
    vendor, known = viastub.board_vendor(bg.stackup)
    vendor = next((e["vendor"] for e in entries.values() if e.get("vendor")), vendor)
    known = known or bool(vendor and entries and any(
        e.get("vendor") for e in entries.values()))
    block = viastub.load_vendor_backdrill(vendor) if known else {}

    vios: list[dict] = []
    vias_out: list[dict] = []
    no_edge: set = set()
    single = 0
    drilled_any = False
    tol_source = None
    for net in sorted(set(specs) | {v.net for v in bg.vias_of()
                                    if (round(v.at[0], 4), round(v.at[1], 4)) in drills}):
        spec = specs.get(net, {})
        knee = viastub.knee_ghz(spec.get("t_rise_ns"), spec.get("data_rate_mbps"),
                                spec.get("f_max_ghz"))
        if knee is None:
            no_edge.add(net)
        entry = entries.get(net) or {}
        if entry.get("depth_tol_mm") is not None:
            tol, tol_source = float(entry["depth_tol_mm"]), "constraints"
        elif block.get("depth_tolerance_mm") is not None:
            tol, tol_source = float(block["depth_tolerance_mm"]), "vendor"
        else:
            tol = viastub.DEFAULT_DEPTH_TOL_MM
            tol_source = tol_source or "default"
        for v in bg.vias_of(net):
            used = viastub.used_layers(bg, v)
            here = drills.get((round(v.at[0], 4), round(v.at[1], 4))) or {}
            if len(used) < 2 and not here:
                single += 1       # no transition: DRC's via_dangling, not a stub
                continue
            if not used:
                used = [v.layers[0]]
            rows = viastub.stubs(st, v, used, here, tol)
            drilled_any = drilled_any or bool(here)
            vias_out.append({"net": net, "at": [checklib.rnd(c) for c in v.at],
                             "uuid": v.uuid, "used": used, "stubs": rows,
                             "knee_ghz": None if knee is None else round(knee, 3)})
            for row in rows:
                judge(vios, st, v, used, row, knee, entry, tol, here)
            if net in entries:
                missing(vios, st, v, used, here, tol)
    if drilled_any or entries:
        if known and block.get("offered") is False:
            vios.append(checklib.violation(
                SCRIPT, "error", None, None, None, [],
                f"back-drill on a board built by {vendor}, which doesn't offer "
                f"it; use a fab that does (PCBWay) or a blind via", SOURCE,
                kind="backdrill_not_offered", vendor=vendor))
        elif not known or block.get("offered") is None:
            vios.append(checklib.violation(
                SCRIPT, "warning", None, None, None, [],
                f"back-drill on this board, but "
                + (f"{vendor}'s profile doesn't say whether it offers it"
                   if known else "the stack matches no stackups.yaml build, so "
                   "the fab is unknown")
                + "; confirm the service and depth tolerance with the fab",
                SOURCE, kind="backdrill_fab_unknown", vendor=vendor))
    facts = {"vias": vias_out, "no_edge": sorted(no_edge),
             "single_layer_vias": single, "vendor": vendor,
             "tolerance_source": tol_source,
             "stackup_assumed": bool(bg.stackup.assumed),
             "board_thickness_mm": round(st.thickness, 4)}
    return vios, facts


def judge(vios, st, v, used, row, knee, entry, tol, here) -> None:
    layer = row["end"]
    if row.get("cuts_used"):
        vios.append(checklib.violation(
            SCRIPT, "error", v.at, layer, v.net, [],
            f"back-drill to {row['backdrill']['end']} removes "
            f"{', '.join(row['cuts_used'])}, which {v.net} uses at this via",
            SOURCE, kind="backdrill_cuts_net", cut=row["cuts_used"]))
        return
    if "backdrill" in row and row["margin_mm"] < 0:
        vios.append(checklib.violation(
            SCRIPT, "error", v.at, layer, v.net, [],
            f"back-drill leaves {row['length_mm']:.3f} mm past {row['exit']}, "
            f"under the +/-{tol} mm depth tolerance: the drill can reach the "
            f"signal layer. Stop one layer further out", SOURCE,
            kind="backdrill_too_deep", remaining_mm=row["length_mm"], tol_mm=tol))
    limit = entry.get("max_stub_mm")
    if "backdrill" in row and limit is not None and row["worst_mm"] > float(limit):
        vios.append(checklib.violation(
            SCRIPT, "warning", v.at, layer, v.net, [],
            f"back-drilled stub {row['worst_mm']:.3f} mm worst case (nominal "
            f"{row['length_mm']:.3f} + {tol}) over max_stub_mm {limit}", SOURCE,
            kind="backdrill_stub_long", worst_mm=row["worst_mm"], max_mm=limit))
    f = viastub.f_res_ghz(row["worst_mm"], row["eps"])
    row["f_res_ghz"] = None if f is None else round(f, 3)
    if f is None or knee is None:
        return
    ratio = f / knee
    row["ratio"] = round(ratio, 3)
    if ratio >= viastub.WARN_RATIO:
        return
    sev = "error" if ratio < viastub.ERROR_RATIO else "warning"
    need = viastub.max_stub_mm(viastub.WARN_RATIO * knee, row["eps"])
    vios.append(checklib.violation(
        SCRIPT, sev, v.at, layer, v.net, [],
        f"{v.net}: via leaves a {row['worst_mm']:.2f} mm stub from {row['exit']} "
        f"to {row['end']}: f_res {f:.1f} GHz is {ratio:.2f} x the {knee:.2f} GHz "
        f"knee (warn < {viastub.WARN_RATIO:g}, error < {viastub.ERROR_RATIO:g}). "
        + fixes(st, v, used, row, need), SOURCE,
        kind="via_stub_resonance", stub_mm=row["worst_mm"], f_res_ghz=round(f, 3),
        knee_ghz=round(knee, 3), ratio=round(ratio, 3), max_stub_mm=round(need, 3),
        side=row["side"], exit=row["exit"]))



def missing(vios, st, v, used, here, tol) -> None:
    if len(used) < 2:
        return
    for side in ("F", "B"):
        if side in here:
            continue
        p = viastub.plan_side(st, v, used, side, tol)
        if p:
            vios.append(checklib.violation(
                SCRIPT, "error", v.at, p["end"], v.net, [],
                f"{v.net} is listed in constraints backdrill, but this via has "
                f"no back-drill from {'top' if side == 'F' else 'bottom'} to "
                f"{p['end']}; run via_backdrill --apply", SOURCE,
                kind="backdrill_missing", side=side, end=p["end"]))


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", help="constraints.json")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    cons = checklib.load_json(args.constraints, "constraints") \
        if args.constraints and Path(args.constraints).is_file() else {}
    vios, facts = check(pcb, cons)
    return checklib.report(SCRIPT, pcb, vios, **facts), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
