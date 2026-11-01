#!/usr/bin/env python
"""soa_check.py - a MOSFET's peak junction temperature through one pulse.

Takes a power pulse and the part's thermal data (reference/fet_power.yaml,
lib/soa.py) and gives the junction's peak temperature by superposing the
part's single-pulse Zth - the reading a reviewer otherwise does by holding
a simulated peak power and energy against the datasheet's SOA curve.

  calculated hot-swap ramp (a switch charging bulk capacitance):
    soa_check.py --fet AO4485 --hotswap --c 940u --v 28 --t-ramp 10m
                 [--i-load 0.1] [--ta 25]
    soa_check.py --fet AO4485 --hotswap --c 940u --v 28 --i 2.2
  a simulated waveform (CSV with a header: time and p, or time, vds, id):
    soa_check.py --fet AO4485 --csv fet.csv [--ta 25]
  the part's single-pulse SOA power at a pulse width (at the row's
  soa_vds, or --vds to scale it):
    soa_check.py --fet AO4485 --at 10m [--vds 20]
A row with soa_vds (linear-mode SOA, lib/soa.py) scales p(t) by the pulse's
own VDS: the hot-swap ramp's, or the CSV's vds column.
  a part not in the table: --footprint SOIC-8 uses the package's generic
  row (warnings only), or give --zth "1m:2.5,10m:6.25,1:20" --tj-max 150.

Findings: fet_soa (error when Tj passes tj_max on the part's own data;
warning within 25 C of it, or over it on generic data).
Exit 0 pass / 1 violations / 2 error.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import engcalc  # noqa: E402
import soa  # noqa: E402

SCRIPT = "soa_check"


def _val(text: str, what: str) -> float:
    v = engcalc.parse_value(text)
    if v is None:
        raise checklib.CheckError(f"{what} {text!r} does not read as a value")
    return v


def read_csv(path: Path) -> tuple[list[float], list[float],
                                  list[float] | None]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 3:
        raise checklib.CheckError(f"{path}: want a header and 2+ rows")
    head = [h.strip().lower() for h in rows[0]]
    ti = next((i for i, h in enumerate(head) if h.startswith("t")), 0)
    ts, ps, vds = [], [], []
    for r in rows[1:]:
        if not r:
            continue
        vals = [float(x) for x in r]
        ts.append(vals[ti])
        if "vds" in head:
            vds.append(vals[head.index("vds")])
        if "p" in head:
            ps.append(abs(vals[head.index("p")]))
        elif "vds" in head and "id" in head:
            ps.append(abs(vals[head.index("vds")] * vals[head.index("id")]))
        else:
            raise checklib.CheckError(f"{path}: want columns t,p or "
                                      "t,vds,id")
    return ts, ps, vds or None


def _rounded(res: dict) -> dict:
    return {k: v if isinstance(v, bool) else checklib.rnd(v, 5)
            for k, v in res.items() if v is not None}


def thermal_row(a) -> dict:
    if a.zth:
        pts = []
        for item in a.zth.split(","):
            t, _, z = item.partition(":")
            pts.append([_val(t, "--zth time"), float(z)])
        return {"match": a.fet or "cli", "zth": pts,
                "tj_max": a.tj_max or 150.0, "generic": False,
                "source": "--zth", "verified": False}
    row = soa.find(a.fet or "", a.fet or "", a.footprint or "")
    if row is None:
        raise checklib.CheckError(
            f"no thermal data for {a.fet!r}: add it to "
            "reference/fet_power.yaml, or give --footprint (generic) or "
            "--zth")
    if a.tj_max:
        row["tj_max"] = a.tj_max
    return row


def run(a) -> dict:
    row = thermal_row(a)
    facts = {"fet": a.fet, "thermal": {k: row.get(k) for k in (
        "match", "tj_max", "rth_ja", "zth", "soa_vds", "soa_vds_exp",
        "soa_vds_knee", "source", "verified", "generic")}, "ta_c": a.ta}
    if a.at:
        t = _val(a.at, "--at")
        k = soa.vds_scale(row, a.vds) if a.vds is not None else 1.0
        return checklib.report(SCRIPT, "cli", [], **facts,
                               pulse_s=t, zth_c_per_w=soa.zth(row, t),
                               vds=a.vds if a.vds is not None
                               else row.get("soa_vds"),
                               soa_power_w=soa.soa_power(row, t, a.ta) / k)
    if a.hotswap:
        c = _val(a.c, "--c")
        ts, ps, vds = soa.hotswap_profile(
            c, a.v, t_ramp=_val(a.t_ramp, "--t-ramp") if a.t_ramp else None,
            i_a=_val(a.i, "--i") if a.i else None, i_load=a.i_load,
            with_vds=True)
        facts["pulse"] = {"kind": "hotswap", "c_f": c, "v": a.v,
                          "ramp_s": ts[-1], "i_charge_a": c * a.v / ts[-1],
                          "i_load_a": a.i_load}
    elif a.csv:
        ts, ps, vds = read_csv(Path(a.csv))
        facts["pulse"] = {"kind": "csv", "file": a.csv, "samples": len(ts)}
    else:
        raise checklib.CheckError("give --hotswap, --csv or --at")
    res = soa.junction(row, ts, ps, a.ta, vds=vds)
    sev, why = soa.judge(row, res)
    vios = []
    if sev:
        vios.append(checklib.violation(
            SCRIPT, sev, None, None, None, [], f"{a.fet}: {why} "
            f"({res['p_peak_w']:.3g} W peak, {res['energy_j']:.3g} J over "
            f"about {res['width_s'] * 1e3:.3g} ms, Ta {a.ta:g} C"
            f"{'; generic package data' if row.get('generic') else ''})",
            SCRIPT, kind="fet_soa", **_rounded(res)))
    return checklib.report(SCRIPT, "cli", vios, **facts, verdict=why,
                           result=_rounded(res))


def main(argv: list[str] | None = None) -> int:
    checklib.utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fet", help="MPN or value (AO4485)")
    ap.add_argument("--footprint", help="package, for a generic row")
    ap.add_argument("--zth", help="t:C/W,... single-pulse points")
    ap.add_argument("--tj-max", type=float)
    ap.add_argument("--ta", type=float, default=25.0,
                    help="ambient C (default 25, the SOA curve's basis)")
    ap.add_argument("--hotswap", action="store_true")
    ap.add_argument("--c", help="bulk capacitance (940u)")
    ap.add_argument("--v", type=float, help="volts the bulk charges to")
    ap.add_argument("--t-ramp", help="ramp time (10m)")
    ap.add_argument("--i", help="charging current (2.2)")
    ap.add_argument("--i-load", type=float, default=0.0)
    ap.add_argument("--csv")
    ap.add_argument("--at", help="pulse width for the SOA power (10m)")
    ap.add_argument("--vds", type=float,
                    help="with --at: the VDS (rows with soa_vds)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.hotswap and (not a.c or a.v is None or not (a.t_ramp or a.i)):
        ap.error("--hotswap needs --c, --v and --t-ramp or --i")
    return checklib.cli_wrap(SCRIPT, lambda: (run(a), a.out))


if __name__ == "__main__":
    sys.exit(main())
