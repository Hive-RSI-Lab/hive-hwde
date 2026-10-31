#!/usr/bin/env python
"""stackup_fetch.py - read JLC's laminations (any layer count, HDI) from its
public impedance calculator and write them as stackups.yaml entries.

  list   --prefix JLC08 [--thickness 1.6] [--inner-oz 0.5] [--outer-oz 1]
         every template of a family with JLC's tags (free, general, special,
         do_not_use) and the via spans an HDI build is made for
  entry  --name JLC08161H-2116 [--fixture DIR]
         the stackups.yaml entry for one template (YAML on stdout), its
         outer controlled-impedance table solved by impedance.py; --fixture
         keeps the raw template JSON (tests/fixtures/jlc_stackups/) so the
         conversion stays testable offline

A prefix is JLC's own naming: JLC + 2-digit layer count + thickness
(161 = 1.6 mm) + outer oz + inner oz (H = half), e.g. JLC08161H-2116;
HDI builds are JLCH..., with Y/N (buried core vias or not) and the HDI
order: JLCH081611Y2-1078A. See lib/jlcstack.py for what a template is and
is not (not an order-API read-back).

Exit 0 / 2 on error (network, unknown name), JSON error on stderr.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import impedance as imp  # noqa: E402
import jlcstack  # noqa: E402

PROFILES = [
    {"profile": "se_50", "kind": "single", "impedance_ohm": 50},
    {"profile": "diff_90", "kind": "diff", "impedance_ohm": 90,
     "note": "USB 2.0 / ULPI"},
    {"profile": "diff_100", "kind": "diff", "impedance_ohm": 100,
     "note": "Ethernet / LVDS / general 100R"},
]


def _post(payload: dict, timeout: float = 90.0) -> dict:
    req = urllib.request.Request(
        jlcstack.URL, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch(prefix: str, transport=_post) -> list[dict]:
    """Every template whose name matches `prefix` (JLC's filter is a
    substring match), all pages."""
    out, page = [], 1
    while True:
        resp = transport({"appointName": prefix, "pageNum": page,
                          "pageSize": 100})
        body = resp.get("body") or {}
        out += body.get("list") or []
        if not body.get("hasNextPage"):
            return out
        page += 1


def find(name: str, transport=_post) -> dict:
    """The one template named exactly `name`. JLC lists a few names twice
    (a '无要求' / no-requirement twin): the one whose display name carries
    the name wins."""
    hits = [t for t in fetch(name, transport) if t.get("appointName") == name]
    if not hits:
        raise LookupError(f"JLC has no template named {name!r}")
    hits.sort(key=lambda t: name not in (t.get("receptionDisplayName") or ""))
    return hits[0]


def solve_outer(entry: dict) -> list[dict]:
    """Outer coated-microstrip table for an entry, the same calls
    tests/test_board_setup.py checks every table against."""
    stack = entry["stack"]
    h = next(float(s["thickness_mm"]) for s in stack[1:] if s["type"] != "copper")
    er = next(float(s["epsilon_r"]) for s in stack[1:] if s["type"] != "copper")
    oz = stack[0]["copper_oz"]
    t = imp.CU_OZ_MM.get(oz, 0.035)
    rows = []
    for p in PROFILES:
        row = {k: p[k] for k in ("profile", "kind", "impedance_ohm")}
        row.update(layer="outer", reference="nearest_plane")
        if p["kind"] == "single":
            row["width_mm"] = round(imp.solve_width(p["impedance_ohm"], h, t, er,
                                                    method="field"), 4)
            row["closed_form"] = {"width_mm": round(imp.solve_width(
                p["impedance_ohm"], h, t, er, method="closed_form"), 4)}
        else:
            w, g = imp.diff_pair(float(p["impedance_ohm"]), h, t, er, method="field")
            wc, gc = imp.diff_pair(float(p["impedance_ohm"]), h, t, er,
                                   method="closed_form")
            row.update(width_mm=w, gap_mm=g,
                       closed_form={"width_mm": wc, "gap_mm": gc})
        if p.get("note"):
            row["note"] = p["note"]
        rows.append(row)
    return rows


def summary(t: dict) -> dict:
    n = int(t["plateLayerNumber"])
    disp = t.get("receptionDisplayName") or ""
    return {"name": t["appointName"], "layers": n,
            "thickness_mm": float(t["plateThickness"]),
            "outer_oz": float(t.get("cuprumThickness") or 0),
            "inner_oz": float(t.get("innerCopperThickness") or 0),
            "finished_mm": jlcstack.finished_thickness(disp),
            "tags": jlcstack.tags(disp),
            "vias": [f"{v['kind']} {v['from']}-{v['to']}"
                     for v in jlcstack.via_spans(t, n)]}


def to_yaml(name: str, entry: dict) -> str:
    import yaml
    return yaml.safe_dump({name: entry}, sort_keys=False, allow_unicode=True,
                          width=100)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--prefix", required=True)
    ls.add_argument("--thickness", type=float)
    ls.add_argument("--outer-oz", type=float)
    ls.add_argument("--inner-oz", type=float)
    en = sub.add_parser("entry")
    en.add_argument("--name", required=True)
    en.add_argument("--fixture", help="directory to keep the raw template in")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            rows = [summary(t) for t in fetch(args.prefix)]
            rows = [r for r in rows
                    if (args.thickness is None or abs(r["thickness_mm"] - args.thickness) < 1e-6)
                    and (args.outer_oz is None or r["outer_oz"] == args.outer_oz)
                    and (args.inner_oz is None or r["inner_oz"] == args.inner_oz)]
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        tpl = find(args.name)
        if args.fixture:
            d = Path(args.fixture)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{args.name}.json").write_text(
                json.dumps(jlcstack.trim(tpl), ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8")
        entry = jlcstack.entry_from_template(
            tpl, _dt.date.today().isoformat(), solve=solve_outer)
        sys.stdout.write(to_yaml(args.name, entry))
        return 0
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
