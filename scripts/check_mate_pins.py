"""check_mate_pins.py - do mated connectors meet net to net? (P8 verify suite)

Two failure modes, one script; the family table is
reference/connector_pinmap.yaml (see its header for keyed, mates and mounts).

MECH-05, a declared mated pair (constraints.json "mating_pairs"):
 - mate_pin_mismatch: two pads that touch when the pair is mated carry
   different nets (a mirrored pinout, swapped rows, a socket on the wrong
   side). The message says when the meeting is a supply short.
 - mate_family_mismatch: the two footprints are known families that do not
   plug into each other, or their pad patterns do not overlay.
 - mate_pair_unresolved: a declared ref, or the mate board, is not there.

MECH-06, on this board alone:
 - mate_unkeyed_rail_swap: two connectors with the same footprint in an
   unkeyed family carry different nets on a pin where either carries a supply
   rail, so a plug made for one, put in the other, meets the wrong rail.

constraints.json "mating_pairs" entries:
  {"ref": "J2",                 # connector on this board
   "mate_ref": "J1",            # connector it mates with
   "mate_pcb": "mate/d.kicad_pcb",  # the mate's board, relative to the
                                # constraints file; absent = this board
   "mount": "stack",            # stack | flip | cable (default stack)
   "rotation": 0,               # mate board turn, degrees CCW, top view
   "net_map": {"+3V3": "VDD"}}  # this board's net -> the mate's name for it
Nets are compared by their last path segment, case-insensitive, so "/SWDIO"
on one board meets "SWDIO" on the other. Pads with no net, or an
"unconnected-" net, on either side are not compared.

CLI: --pcb board.kicad_pcb [--constraints constraints.json] [--out report.json]
exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import placelib  # noqa: E402
import yaml  # noqa: E402

SCRIPT = "check_mate_pins"
TABLE = (Path(__file__).resolve().parents[1] / "reference"
         / "connector_pinmap.yaml")
MOUNTS = ("stack", "flip", "cable")

GND_RE = re.compile(r"^(A|D|P|S|C)?GND\w*$|^VSS\w*$|^0V$", re.I)
RAIL_RE = re.compile(r"^[+-]\d|^\+|^(VBUS|VCC|VDD|VIN|VSYS|VBAT|VM|VDDA|VREF)"
                     r"(\b|_|\d|$)|^V\d", re.I)


def load_table(path=TABLE) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    fams = []
    for f in doc.get("families") or []:
        fams.append({**f, "re": re.compile(f["match"], re.I)})
    return {"tol": float((doc.get("defaults") or {}).get("overlay_tol_mm", 0.2)),
            "families": fams}


def family(fpid: str, table: dict) -> dict | None:
    name = fpid.split(":", 1)[-1]
    for f in table["families"]:
        if f["re"].search(name):
            return f
    return None


def canon(net: str | None) -> str | None:
    """Comparable net name, or None for a pad that carries nothing."""
    if not net or net.lower().startswith("unconnected-"):
        return None
    return net.rstrip("/").rsplit("/", 1)[-1].upper()


def is_gnd(net: str | None) -> bool:
    name = canon(net)
    return bool(name) and bool(GND_RE.match(name))


def is_rail(net: str | None) -> bool:
    """A supply rail other than ground ("/VCC" and "VCC" alike)."""
    name = canon(net)
    return bool(name) and not is_gnd(name) and bool(RAIL_RE.match(name))


def is_supply(net: str | None) -> bool:
    return is_gnd(net) or is_rail(net)


def _pads(fp) -> list[tuple[str, str | None, float, float]]:
    """(number, net, x, y) of the connector's numbered pads, in top view."""
    return [p for p in fp.pad_centers_abs() if p[0]]


def _offsets(pads, mount="stack", rotation=0.0):
    """Pad offsets from the pattern centre, mate frame turned into ours."""
    cx = sum(p[2] for p in pads) / len(pads)
    cy = sum(p[3] for p in pads) / len(pads)
    a = math.radians(rotation)
    out = []
    for num, net, x, y in pads:
        dx, dy = x - cx, y - cy
        if mount == "flip":
            dx = -dx
        # CCW in top view with KiCad's y pointing down
        rx = dx * math.cos(a) + dy * math.sin(a)
        ry = -dx * math.sin(a) + dy * math.cos(a)
        out.append((num, net, rx, ry))
    return out


def meetings(a_fp, b_fp, mount, rotation, tol):
    """[(a_pad, b_pad)] that touch when mated, or None when the patterns do
    not line up (different count, pitch or rows)."""
    a_pads, b_pads = _pads(a_fp), _pads(b_fp)
    if not a_pads or len(a_pads) != len(b_pads):
        return None
    if mount == "cable":
        by_num = {p[0]: p for p in b_pads}
        if {p[0] for p in a_pads} != set(by_num):
            return None
        return [(p, by_num[p[0]]) for p in a_pads]
    a_off = _offsets(a_pads)
    b_off = _offsets(b_pads, mount, rotation)
    pairs = []
    for i, (_, _, ax, ay) in enumerate(a_off):
        hit = [j for j, (_, _, bx, by) in enumerate(b_off)
               if math.hypot(ax - bx, ay - by) <= tol]
        if len(hit) != 1:
            return None
        pairs.append((a_pads[i], b_pads[hit[0]]))
    return pairs


def _resolve_mate(pair: dict, pcb: Path, cons_dir: Path | None, model,
                  cache: dict):
    rel = pair.get("mate_pcb")
    if not rel:
        return model, pcb
    path = Path(rel)
    if not path.is_absolute():
        path = (cons_dir or pcb.parent) / path
    if path not in cache:
        cache[path] = placelib.PlaceModel(path) if path.is_file() else None
    return cache[path], path


def pair_violations(model, pcb: Path, pairs: list, cons_dir: Path | None,
                    table: dict) -> tuple[list[dict], list[dict]]:
    out, facts, cache = [], [], {}
    for pair in pairs:
        ref, mref = pair.get("ref"), pair.get("mate_ref")
        mount = pair.get("mount", "stack")
        if mount not in MOUNTS:
            raise checklib.CheckError(
                f"mating_pairs {ref}: mount {mount!r} not in {MOUNTS}")
        a_fp = model.footprints.get(ref)
        mate, mpath = _resolve_mate(pair, pcb, cons_dir, model, cache)
        b_fp = mate.footprints.get(mref) if mate is not None else None
        if a_fp is None or b_fp is None:
            what = (f"{ref} is not on this board" if a_fp is None else
                    f"mate board {mpath.name} not found" if mate is None else
                    f"{mref} is not on {mpath.name}")
            out.append(checklib.violation(
                "mate_pins", "error",
                a_fp.center_abs() if a_fp is not None else None, None, None,
                [ref] if ref else [],
                f"declared mated pair {ref} <-> {mref}: {what}", SCRIPT,
                kind="mate_pair_unresolved", connector=ref, mate=mref))
            continue
        fa, fb = family(a_fp.fpid, table), family(b_fp.fpid, table)
        met = meetings(a_fp, b_fp, mount, float(pair.get("rotation", 0)),
                       table["tol"])
        fam_bad = fa and fb and fb["name"] not in (fa.get("mates") or [])
        if met is None or fam_bad:
            why = (f"{fa['name']} does not plug into {fb['name']}" if fam_bad
                   else f"pad patterns do not line up ({mount} mount)")
            out.append(checklib.violation(
                "mate_pins", "error", a_fp.center_abs(), None, None, [ref],
                f"{ref} ({a_fp.fpid.split(':')[-1]}) cannot mate with "
                f"{mref} ({b_fp.fpid.split(':')[-1]}) on {mpath.name}: {why}",
                SCRIPT, kind="mate_family_mismatch", connector=ref, mate=mref))
            continue
        nmap = {canon(k): canon(v) for k, v in
                (pair.get("net_map") or {}).items()}
        bad = 0
        for (anum, anet, ax, ay), (bnum, bnet, _, _) in met:
            ca, cb = canon(anet), canon(bnet)
            if ca is None or cb is None or nmap.get(ca, ca) == cb:
                continue
            bad += 1
            short = (is_supply(anet) and is_supply(bnet))
            out.append(checklib.violation(
                "mate_pins", "error", (ax, ay), None, anet, [ref],
                f"{ref} pin {anum} ({anet}) meets {mref} pin {bnum} ({bnet}) "
                f"on {mpath.name}" + (": a supply short when mated"
                                      if short else ""),
                SCRIPT, kind="mate_pin_mismatch", connector=ref, mate=mref,
                pin=anum, mate_pin=bnum, mate_net=bnet, supply_short=short))
        facts.append({"ref": ref, "mate_ref": mref, "mate_pcb": mpath.name,
                      "mount": mount, "pins": len(met), "mismatched": bad})
    return out, facts


def unkeyed_violations(model, table: dict) -> list[dict]:
    groups: dict[str, list] = {}
    for fp in sorted(model.footprints.values(), key=lambda f: f.ref):
        fam = family(fp.fpid, table)
        if fam is not None and not fam.get("keyed", True):
            groups.setdefault(fp.fpid, []).append(fp)
    out = []
    for fps in groups.values():
        for i, a in enumerate(fps):
            a_nets = {p[0]: p for p in _pads(a)}
            for b in fps[i + 1:]:
                for num, bnet, _, _ in _pads(b):
                    if num not in a_nets:
                        continue
                    _, anet, ax, ay = a_nets[num]
                    if canon(anet) == canon(bnet) or not (
                            is_rail(anet) or is_rail(bnet)):
                        continue
                    out.append(checklib.violation(
                        "mate_pins", "error", (ax, ay), None, anet,
                        [a.ref, b.ref],
                        f"{a.ref} and {b.ref} are the same unkeyed connector "
                        f"but pin {num} carries {anet or 'nothing'} on "
                        f"{a.ref} and {bnet or 'nothing'} on {b.ref}: a plug "
                        "in the wrong one meets the wrong rail", SCRIPT,
                        kind="mate_unkeyed_rail_swap", connector=a.ref,
                        mate=b.ref, pin=num, mate_net=bnet))
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (mating_pairs list); optional")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    cons = checklib.load_json(args.constraints, "constraints") \
        if args.constraints else {}
    pairs = cons.get("mating_pairs") or []
    if not isinstance(pairs, list) or not all(
            isinstance(p, dict) and p.get("ref") and p.get("mate_ref")
            for p in pairs):
        raise checklib.CheckError(
            "constraints mating_pairs must be a list of {ref, mate_ref, ...}")
    table = load_table()
    model = placelib.PlaceModel(pcb)
    cons_dir = Path(args.constraints).parent if args.constraints else None
    violations, facts = pair_violations(model, pcb, pairs, cons_dir, table)
    violations += unkeyed_violations(model, table)
    for v in violations:
        v["check"] = "mate_pins"
    return checklib.report(SCRIPT, str(pcb), violations,
                           pairs=facts), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
