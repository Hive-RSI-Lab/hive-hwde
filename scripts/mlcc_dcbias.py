#!/usr/bin/env python
"""mlcc_dcbias.py - what a board's ceramic capacitors are worth at their DC.

A 22 uF 0805 on a 5 V rail is about 11 uF; a buck's loop, an LDO's
stability floor and a hold-up time are all set by the derated value. This
lists every capacitor with its DC across it, its effective capacitance
(lib/mlcc.py: the part's own curve from reference/mlcc_dcbias.yaml by MPN,
else the generic class-II curve, typical and low), and each rail's total
nominal against effective.

  one part:  mlcc_dcbias.py --c 22u --package 0805 --rated 25 --vdc 5.2
             [--dielectric X5R] [--mpn GRM21BR61E226] [--temp 85]
  a board:   mlcc_dcbias.py --workspace boards/<b> [--temp 60]
             mlcc_dcbias.py --net x.net [--constraints c.json] [--parts p/]
             [--rail NET=V] [--min-c NET=22u]

The DC across a board capacitor comes from the rails sim_board resolves
(constraints.json voltages, power tree, regulator outputs, net names): a cap
between two known rails (ground included) sits at their difference; one on
any other net is listed as unknown. `--min-c NET=F` states a floor a rail's
effective capacitance must keep (a regulator's minimum Cout): below it on
the low curve is an error, below it on the typical curve too.

Findings: cap_dc_bias_low (warning: a class-II part keeps under half its
nominal), rail_c_below_min (error: the rail's effective capacitance under a
--min-c floor), cap_overvoltage (error: DC over the rating).
Exit 0 pass / 1 violations / 2 error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import engcalc  # noqa: E402
import mlcc  # noqa: E402
import sim_board  # noqa: E402

SCRIPT = "mlcc_dcbias"
LOW_FRACTION = 0.5


def _vio(kind, sev, refs, net, msg, **extra):
    return checklib.violation(SCRIPT, sev, None, None, net, refs, msg,
                              SCRIPT, kind=kind, **extra)


def _f(x: float) -> str:
    return sim_board._f(x, "F")


def part_text(part: dict | None) -> str:
    if not part:
        return ""
    return " ".join([part.get("description") or ""] +
                    list((part.get("attrs") or {}).values()))


def board_caps(board: dict, parts: dict, rails: dict, temp_c: float,
               table: list[dict]) -> list[dict]:
    grounds = {n for n in board["nets"] if sim_board.check_ratings.is_ground(n)}
    out = []
    for ref in sorted(board["comps"], key=sim_board._natural):
        c = board["comps"][ref]
        if c["kind"] != "C" or len(c["pins"]) != 2:
            continue
        nets = sorted({p["net"] for p in c["pins"].values()})
        if len(nets) != 2:
            continue
        part = parts.get(ref)
        cval = engcalc.parse_value(c["value"]) or engcalc.parse_value(
            sim_board.attr(part, r"^Capacit") or "")
        if cval is None:
            continue
        volts = [0.0 if n in grounds else rails[n][0] if n in rails else None
                 for n in nets]
        rated, _src = sim_board.cap_rating(c, part)
        row = {"ref": ref, "nets": nets, "value": c["value"],
               "footprint": c["footprint"], "rated_v": rated}
        if None in volts:
            row.update(vdc=None, c_nom=cval)
            out.append(row)
            continue
        vdc = abs(volts[0] - volts[1])
        r = mlcc.c_eff(cval, vdc, footprint=c["footprint"], value=c["value"],
                       text=part_text(part), mpn=(part or {}).get("mpn", ""),
                       rated_v=rated, temp_c=temp_c, table=table)
        row.update(r)
        row["rail"] = next((n for n in nets if n not in grounds), None) \
            if any(n in grounds for n in nets) else None
        out.append(row)
    return out


def findings(caps: list[dict], min_c: dict[str, float]) -> tuple[list, dict]:
    vios = []
    totals: dict[str, dict] = {}
    for r in caps:
        if r.get("vdc") is None:
            continue
        if r.get("rated_v") and r["vdc"] > r["rated_v"]:
            vios.append(_vio(
                "cap_overvoltage", "error", [r["ref"]], r["nets"][0],
                f"{r['ref']} sees {r['vdc']:.3g} V DC over its "
                f"{r['rated_v']:g} V rating", vdc=r["vdc"],
                rating_v=r["rated_v"]))
        if r["kind"] == "class2" and r["fraction"] < LOW_FRACTION:
            vios.append(_vio(
                "cap_dc_bias_low", "warning", [r["ref"]], r["nets"][0],
                f"{r['ref']} ({r['value']}, {r['package'] or '?'} "
                f"{r['dielectric']}{' assumed' if r['dielectric_assumed'] else ''}"
                f") at {r['vdc']:.3g} V keeps about {r['fraction']:.0%}: "
                f"{_f(r['c_eff'])} typical, {_f(r['c_eff_low'])} low "
                f"({r['curve']} curve)", c_nom=r["c_nom"], c_eff=r["c_eff"],
                c_eff_low=r["c_eff_low"], vdc=r["vdc"]))
        if r.get("rail"):
            t = totals.setdefault(r["rail"], {"c_nom": 0.0, "c_eff": 0.0,
                                              "c_eff_low": 0.0, "refs": []})
            t["c_nom"] += r["c_nom"]
            t["c_eff"] += r["c_eff"]
            t["c_eff_low"] += r["c_eff_low"]
            t["refs"].append(r["ref"])
    for net, floor in sorted(min_c.items()):
        t = totals.get(net)
        if t is None:
            vios.append(_vio(
                "rail_c_below_min", "error", [], net,
                f"{net} has no capacitor to ground with a known DC; "
                f"--min-c asks for {_f(floor)}", min_c=floor))
            continue
        if t["c_eff_low"] < floor:
            sev = "error" if t["c_eff"] < floor else "warning"
            vios.append(_vio(
                "rail_c_below_min", sev, t["refs"], net,
                f"{net}: {_f(t['c_nom'])} nominal is {_f(t['c_eff'])} "
                f"typical / {_f(t['c_eff_low'])} low at its DC, against a "
                f"{_f(floor)} floor", min_c=floor, **{
                    k: t[k] for k in ("c_nom", "c_eff", "c_eff_low")}))
    return vios, totals


def run(a) -> dict:
    table = mlcc.load_table()
    if a.c:
        c = engcalc.parse_value(a.c)
        if c is None:
            raise checklib.CheckError(f"--c {a.c!r} does not read as a value")
        fp = f"C_{a.package}" if a.package else ""
        r = mlcc.c_eff(c, a.vdc, footprint=fp, value=a.c,
                       text=a.dielectric or "", mpn=a.mpn or "",
                       rated_v=a.rated, temp_c=a.temp, table=table)
        return checklib.report(SCRIPT, "cli", [], part=r)
    a.rail = a.rail or []
    inp = sim_board.resolve_inputs(a)
    board = sim_board.load_board(inp["net"])
    parts = sim_board.load_parts(inp["parts"])
    cons = checklib.load_json(inp["constraints"], "constraints") \
        if inp["constraints"] else None
    rails = sim_board.resolve_rails(board, parts, cons, inp["power_tree"],
                                    sim_board._pairs(a.rail, "--rail"))
    caps = board_caps(board, parts, rails, a.temp, table)
    min_c = {}
    for item in a.min_c:
        net, _, v = item.partition("=")
        fv = engcalc.parse_value(v)
        if not net or fv is None:
            raise checklib.CheckError(f"--min-c {item!r}: want NET=value")
        min_c[net] = fv
    vios, totals = findings(caps, min_c)
    unknown = [r["ref"] for r in caps if r.get("vdc") is None]
    assumed = sorted(r["ref"] for r in caps if r.get("dielectric_assumed")
                     and r.get("kind") == "class2" and r.get("fraction", 1)
                     < 0.8)
    return checklib.report(
        SCRIPT, inp["net"], vios, temp_c=a.temp,
        rails={n: {"volts": v, "source": s} for n, (v, s) in
               sorted(rails.items())},
        rail_totals={n: {k: (checklib.rnd(x, 9) if isinstance(x, float)
                             else x) for k, x in t.items()}
                     for n, t in sorted(totals.items())},
        capacitors=caps, dc_unknown=unknown,
        known_unknowns=[{"ref": r, "what": "dielectric",
                         "why": "not named in value or parts.json: X5R "
                                "assumed for >= 1 uF"} for r in assumed])


def main(argv: list[str] | None = None) -> int:
    checklib.utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--c", help="one part: nominal capacitance (22u)")
    ap.add_argument("--package", help="one part: 0402/0603/0805/1206...")
    ap.add_argument("--rated", type=float, help="rated volts")
    ap.add_argument("--vdc", type=float, default=0.0)
    ap.add_argument("--dielectric", help="X5R/X7R/... (one part)")
    ap.add_argument("--mpn")
    ap.add_argument("--temp", type=float, default=25.0,
                    help="part temperature, C (default 25)")
    ap.add_argument("--workspace")
    ap.add_argument("--net")
    ap.add_argument("--constraints")
    ap.add_argument("--parts")
    ap.add_argument("--power-tree")
    ap.add_argument("--rail", action="append", default=[])
    ap.add_argument("--min-c", action="append", default=[],
                    help="NET=value: a rail's effective-capacitance floor")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if not a.c and not a.workspace and not a.net:
        ap.error("give --c (one part) or --workspace/--net (a board)")
    return checklib.cli_wrap(SCRIPT, lambda: (run(a), a.out))


if __name__ == "__main__":
    sys.exit(main())
