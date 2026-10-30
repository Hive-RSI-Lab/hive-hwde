"""netlist_audit.py - netlist vs constraints.json cross-check (SPEC P4).

Audits an exported KiCad netlist (kicadsexpr format) against the design's
constraints.json (and optionally the S7-emitted decoupling.json), catching
schematic-level structural problems BEFORE board work starts:

 - missing_net (error): a net referenced anywhere in constraints.json
   (high_speed[].net + reference nets, power[].net, voltages[].net,
   diff_pairs[].p/.n, thermal[].net) does not exist in the netlist.
 - missing_ref (error): a refdes referenced by constraints.json
   (placement edges/groups/fixed/separation, thermal[].ref) that is not a
   netlist component - a P4 refdes rename/split would otherwise silently
   mute the constraint (place_anneal drops unknown refs; ladder row 86).
 - diffpair_naming (warning): an explicit diff_pairs entry whose p/n names
   do not follow a recognized differential suffix convention (_P/_N, DP/DM,
   D+/D-; check_diffpair.py's conservative families).
 - diffpair_unpaired (warning): a netlist net carrying a strong differential
   suffix whose partner net is absent.
 - power_no_consumers (warning): a declared power net with no power_in pin
   AND no series escape - a 2-pin member whose far pin leaves the rail to a
   non-ground net (passive loads - LEDs, droppers, fuses - never carry
   pintype power_in; such rails are recorded as passive_fed, not warned).
 - pin_no_net (warning): a symbol pin (per the export's own libparts/units
   inventory) that appears in NO net, not even an unconnected-* singleton.
   Hierarchical exports DROP no-connect singleton nets, so these pads reach
   board_init with no net at all and nothing else reports them (row 68).
 - power_undeclared (warning): a net feeding power_in pins that is neither
   declared (power[] / voltages[]) nor ground-named (GND*/;*GND/VSS*/0V).
 - dangling_net (warning): a single-pin net not named unconnected-* - the
   classic label-typo signature (label misspelled on one stub).
 - metadata_mismatch (error): a decoupling association that contradicts the
   netlist (cap/ic missing, IC pin not on the stated rail, cap not spanning
   rail+gnd); value drift cap-vs-netlist is a warning.

Warnings alone do NOT fail the audit (fp_verify.py precedent): exit 1 only
on error-severity findings. Violations use the S2 normalized schema
(source "audit.netlist") so cluster_violations.py / gates merge them.

Compare mode (--compare OTHER.net): strict electrical-identity diff of two
netlists - same net names, same (ref, pin) memberships. Any difference
(missing/extra net, membership diff, rename) is an error violation
(kind netlist_diff). Used by the S7 acceptance (regenerated schematic must
be electrically identical to the golden) and by regen guards after
generator refactors.

CLI:
  netlist_audit.py --netlist X.net --constraints c.json
                   [--decoupling d.json] [--out report.json]
  netlist_audit.py --sch Y.kicad_sch --constraints c.json   (exports first)
  netlist_audit.py --netlist A.net --compare B.net [--out report.json]
Exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import sexpdata

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))

import checklib  # noqa: E402

SCRIPT = "netlist_audit"
SOURCE = "audit.netlist"

# Strong differential suffix families (subset of check_diffpair.SUFFIX_PAIRS:
# the bare +/- and P/N tokens are too ambiguous for unpaired-net warnings).
STRONG_SUFFIXES = [("_P", "_N"), ("DP", "DM"), ("D+", "D-")]

_GROUND_RE = re.compile(r"(^GND|GND$|^VSS|^0V$)")


# ------------------------------------------------------------ netlist parse

def _kids(node, key):
    return [x for x in node if isinstance(x, list) and x
            and str(x[0]) == key]


def _atom(node, key, default=None):
    for x in node:
        if isinstance(x, list) and x and str(x[0]) == key:
            return str(x[1]) if len(x) > 1 else default
    return default


def parse_netlist(path: Path | str) -> dict:
    """kicadsexpr netlist -> {"components": {ref: {value, footprint,
    libsource?, unit_pins?}}, "nets": {name: [{ref, pin, pintype}]},
    "libparts": {(lib, part): set(pin numbers)}}. Multiline or single-line
    formatting both parse (sexpdata, not regex - kicad-cli 10 pretty-prints
    netlists). unit_pins (kicad 10 per-comp units block) is the placed
    instance's own pin inventory; libparts is the shared per-symbol union
    (all units) used as the fallback."""
    p = Path(path)
    try:
        with open(p, encoding="utf-8") as fh:
            data = sexpdata.load(fh)
    except OSError as exc:
        raise checklib.CheckError(f"cannot read netlist {p}: {exc}") from exc
    except Exception as exc:
        raise checklib.CheckError(f"netlist {p} does not parse: {exc}") from exc
    if not isinstance(data, list) or not data or str(data[0]) != "export":
        raise checklib.CheckError(f"netlist {p}: not a kicadsexpr export")

    def _pin_nums(node) -> set[str]:
        out = set()
        for pblk in _kids(node, "pins"):
            for pin in _kids(pblk, "pin"):
                num = _atom(pin, "num")
                if num:
                    out.add(num)
        return out

    comps: dict[str, dict] = {}
    for blk in _kids(data, "components"):
        for c in _kids(blk, "comp"):
            ref = _atom(c, "ref")
            if not ref:
                continue
            comps[ref] = {"value": _atom(c, "value"),
                          "footprint": _atom(c, "footprint")}
            src = _kids(c, "libsource")
            if src:
                comps[ref]["libsource"] = (_atom(src[0], "lib") or "",
                                           _atom(src[0], "part") or "")
            unit_pins: set[str] = set()
            for ub in _kids(c, "units"):
                for u in _kids(ub, "unit"):
                    unit_pins |= _pin_nums(u)
            if unit_pins:
                comps[ref]["unit_pins"] = sorted(unit_pins)
    libparts: dict[tuple, set] = {}
    for blk in _kids(data, "libparts"):
        for lp in _kids(blk, "libpart"):
            key = (_atom(lp, "lib") or "", _atom(lp, "part") or "")
            libparts.setdefault(key, set()).update(_pin_nums(lp))
    nets: dict[str, list] = {}
    for blk in _kids(data, "nets"):
        for n in _kids(blk, "net"):
            name = _atom(n, "name")
            if name is None:
                continue
            nets[name] = [{"ref": _atom(nd, "ref"), "pin": _atom(nd, "pin"),
                           "pintype": _atom(nd, "pintype", "")}
                          for nd in _kids(n, "node")]
    return {"components": comps, "nets": nets, "libparts": libparts}


def _memberships(parsed: dict) -> dict[str, tuple]:
    return {name: tuple(sorted((m["ref"], m["pin"]) for m in members))
            for name, members in parsed["nets"].items()}


def _base_type(pintype: str) -> str:
    return (pintype or "").split("+", 1)[0]


def _is_ground_name(net: str) -> bool:
    return bool(_GROUND_RE.search(net.lstrip("/").upper()))


# ------------------------------------------------------------ compare mode

def compare_netlists(a: dict, b: dict) -> dict:
    """Strict electrical identity: same net names, same (ref, pin) members."""
    ma, mb = _memberships(a), _memberships(b)
    only_a = sorted(set(ma) - set(mb))
    only_b = sorted(set(mb) - set(ma))
    diffs = []
    for net in sorted(set(ma) & set(mb)):
        if ma[net] != mb[net]:
            diffs.append({
                "net": net,
                "only_in_a": sorted(set(ma[net]) - set(mb[net])),
                "only_in_b": sorted(set(mb[net]) - set(ma[net])),
            })
    by_members = {mb[n]: n for n in only_b}
    renamed = [{"a": n, "b": by_members[ma[n]], "members": len(ma[n])}
               for n in only_a if ma[n] in by_members]
    return {"identical": not (only_a or only_b or diffs),
            "nets_only_in_a": only_a, "nets_only_in_b": only_b,
            "membership_diffs": diffs, "renamed": renamed,
            "net_counts": {"a": len(ma), "b": len(mb)}}


def run_compare(net_a: Path, net_b: Path, out: str | None):
    a = parse_netlist(net_a)
    b = parse_netlist(net_b)
    cmp_result = compare_netlists(a, b)
    violations = []

    def v(kind, net, msg, **extra):
        violations.append(checklib.violation(
            SCRIPT, "error", None, None, net, [], msg, SOURCE,
            kind="netlist_diff", diff=kind, **extra))

    for net in cmp_result["nets_only_in_a"]:
        if any(r["a"] == net for r in cmp_result["renamed"]):
            continue
        v("missing_in_b", net, f"net '{net}' only in {net_a.name}")
    for net in cmp_result["nets_only_in_b"]:
        if any(r["b"] == net for r in cmp_result["renamed"]):
            continue
        v("missing_in_a", net, f"net '{net}' only in {net_b.name}")
    for r in cmp_result["renamed"]:
        v("renamed", r["a"],
          f"net '{r['a']}' ({net_a.name}) matches membership of "
          f"'{r['b']}' ({net_b.name}) - renamed")
    for d in cmp_result["membership_diffs"]:
        v("membership", d["net"],
          f"net '{d['net']}' membership differs: only in {net_a.name}: "
          f"{d['only_in_a']}, only in {net_b.name}: {d['only_in_b']}",
          only_in_a=d["only_in_a"], only_in_b=d["only_in_b"])

    payload = checklib.report(SCRIPT, str(net_a), violations,
                              mode="compare", a=str(net_a), b=str(net_b),
                              **cmp_result)
    return payload, out


# ------------------------------------------------------------ audit mode

def _constraint_nets(constraints: dict) -> dict[str, list[str]]:
    """Every net name constraints.json references, keyed by where."""
    where: dict[str, list[str]] = {}

    def add(net, src):
        if isinstance(net, str) and net:
            where.setdefault(net, []).append(src)

    for i, ent in enumerate(constraints.get("high_speed", []) or []):
        add(ent.get("net"), f"high_speed[{i}].net")
        ref = ent.get("reference")
        if isinstance(ref, str):
            add(ref, f"high_speed[{i}].reference")
        elif isinstance(ref, dict):
            for lay, rnet in ref.items():
                add(rnet, f"high_speed[{i}].reference[{lay}]")
    for i, ent in enumerate(constraints.get("power", []) or []):
        add(ent.get("net"), f"power[{i}].net")
    for i, ent in enumerate(constraints.get("voltages", []) or []):
        add(ent.get("net"), f"voltages[{i}].net")
    for i, ent in enumerate(constraints.get("thermal", []) or []):
        add(ent.get("net"), f"thermal[{i}].net")
    for i, ent in enumerate(constraints.get("diff_pairs", []) or []):
        add(ent.get("p"), f"diff_pairs[{i}].p")
        add(ent.get("n"), f"diff_pairs[{i}].n")
    return where


def _suffix_partner(name: str) -> str | None:
    """Expected partner net name if `name` carries a strong diff suffix."""
    up = name.upper()
    for a, b in STRONG_SUFFIXES:
        for tok, other in ((a, b), (b, a)):
            if up.endswith(tok):
                return name[: len(name) - len(tok)] + other
    return None


def _pair_follows_convention(p: str, n: str) -> bool:
    up_p, up_n = p.upper(), n.upper()
    for a, b in STRONG_SUFFIXES:
        if (up_p.endswith(a) and up_n.endswith(b)
                and up_p[: -len(a)] == up_n[: -len(b)]):
            return True
    return False


def _constraint_refs(constraints: dict) -> dict[str, list[str]]:
    """Every refdes constraints.json references, keyed by where."""
    where: dict[str, list[str]] = {}

    def add(ref, src):
        if isinstance(ref, str) and ref:
            where.setdefault(ref, []).append(src)

    placement = constraints.get("placement") or {}
    for i, e in enumerate(placement.get("edges") or []):
        add(e.get("ref"), f"placement.edges[{i}].ref")
    for i, g in enumerate(placement.get("groups") or []):
        add(g.get("anchor"), f"placement.groups[{i}].anchor")
        for j, m in enumerate(g.get("members") or []):
            add(m, f"placement.groups[{i}].members[{j}]")
    for i, r in enumerate(placement.get("fixed") or []):
        add(r, f"placement.fixed[{i}]")
    for i, s in enumerate(placement.get("separation") or []):
        for side in ("a", "b"):
            for j, r in enumerate(s.get(side) or []):
                add(r, f"placement.separation[{i}].{side}[{j}]")
    for i, t in enumerate(constraints.get("thermal") or []):
        add(t.get("ref"), f"thermal[{i}].ref")
    return where


def _series_escapes(rail: str, members: list[dict],
                    comp_pins: dict[str, dict[str, str]]) -> list[str]:
    """2-pin members whose far pin leaves the rail through a non-ground,
    non-NC net: current has a path out through a series/load element
    (polyfuse, dropper, LED resistor), which never carries pintype
    power_in. A typo'd dead rail (source + caps to GND only) has none."""
    out = set()
    for m in members:
        pins = comp_pins.get(m["ref"], {})
        if len(pins) != 2:
            continue
        other = next((n for p, n in pins.items() if p != m["pin"]), None)
        if (other and other != rail and not _is_ground_name(other)
                and not other.startswith("unconnected-")):
            out.add(m["ref"])
    return sorted(out)


def audit(parsed: dict, constraints: dict,
          decoupling: dict | None) -> tuple[list[dict], dict]:
    nets = parsed["nets"]
    comps = parsed["components"]
    violations: list[dict] = []

    def v(sev, kind, net, refs, msg, **extra):
        violations.append(checklib.violation(
            SCRIPT, sev, None, None, net, refs, msg, SOURCE,
            kind=kind, **extra))

    # 1. every constraints-referenced net exists
    referenced = _constraint_nets(constraints)
    for net, sources in sorted(referenced.items()):
        if net not in nets:
            v("error", "missing_net", net, [],
              f"constraints net '{net}' ({', '.join(sources)}) not in "
              f"netlist", constraint_sources=sources)

    # 1b. every constraints-referenced refdes exists (ladder row 86: a
    # refdes rename/split must not silently mute a placement constraint)
    ref_where = _constraint_refs(constraints)
    for ref, sources in sorted(ref_where.items()):
        if ref not in comps:
            v("error", "missing_ref", None, [ref],
              f"constraints ref '{ref}' ({', '.join(sources)}) not in "
              f"netlist components", constraint_sources=sources)

    # 2. explicit diff pairs follow the _P/_N (or DP/DM, D+/D-) convention
    for i, ent in enumerate(constraints.get("diff_pairs", []) or []):
        p, n = ent.get("p"), ent.get("n")
        if p and n and not _pair_follows_convention(p, n):
            v("warning", "diffpair_naming", p, [],
              f"diff_pairs[{i}] ('{p}', '{n}') does not follow a "
              f"differential suffix convention (_P/_N, DP/DM, D+/D-)")

    # 3. unpaired differential-looking nets in the netlist
    for name in sorted(nets):
        if name.startswith("unconnected-"):
            continue
        partner = _suffix_partner(name)
        if partner and partner not in nets:
            v("warning", "diffpair_unpaired", name, [],
              f"net '{name}' looks differential but partner "
              f"'{partner}' does not exist")

    # per-component pin->net map over ALL nets (incl. unconnected-*):
    # serves the series-escape scan (4) and the pin inventory (4c)
    comp_pins: dict[str, dict[str, str]] = {}
    for name, members in nets.items():
        for m in members:
            if m["ref"]:
                comp_pins.setdefault(m["ref"], {})[m["pin"]] = name

    # 4. power tree: declared rails have consumers; feeders are declared
    declared_power = [ent.get("net")
                     for ent in constraints.get("power", []) or []]
    declared_all = set(declared_power) | {
        ent.get("net") for ent in constraints.get("voltages", []) or []}
    power_facts = []
    for net in declared_power:
        if net not in nets:
            continue  # already an error above
        members = nets[net]
        n_in = sum(1 for m in members if _base_type(m["pintype"]) == "power_in")
        n_out = sum(1 for m in members
                    if _base_type(m["pintype"]) == "power_out")
        fact = {"net": net, "nodes": len(members),
                "power_in": n_in, "power_out": n_out}
        if n_in == 0:
            # passive loads (LEDs, droppers, fuses) never carry power_in:
            # a series escape means the rail feeds SOMETHING - record the
            # fact instead of a false-positive warning (T6 calibration)
            escapes = _series_escapes(net, members, comp_pins)
            if escapes:
                fact["passive_fed_via"] = escapes
            else:
                v("warning", "power_no_consumers", net,
                  sorted({m["ref"] for m in members}),
                  f"declared power net '{net}' feeds no power_in pin")
        power_facts.append(fact)
    for name in sorted(nets):
        if name in declared_all or name.startswith("unconnected-"):
            continue
        if _is_ground_name(name):
            continue
        feeders = [m for m in nets[name]
                   if _base_type(m["pintype"]) == "power_in"]
        if feeders:
            v("warning", "power_undeclared", name,
              sorted({m["ref"] for m in feeders}),
              f"net '{name}' feeds power_in pins "
              f"({', '.join(f'{m['ref']}.{m['pin']}' for m in feeders)}) "
              f"but is not declared in constraints power[]/voltages[]")

    # 4c. pin inventory vs net membership (ladder row 68): hierarchical
    # exports DROP no-connect singleton nets; a flat export's NC pins live
    # in unconnected-* nets (counted as connected here) and must not fire.
    libparts = parsed.get("libparts") or {}
    pins_expected = pins_connected = 0
    for ref in sorted(comps):
        comp = comps[ref]
        exp = set(comp.get("unit_pins") or ())
        if not exp:
            exp = set(libparts.get(comp.get("libsource")) or ())
        if not exp:
            continue  # no inventory in this export (synthetic/old netlist)
        have = set(comp_pins.get(ref, ()))
        pins_expected += len(exp)
        pins_connected += len(exp & have)
        missing = sorted(exp - have,
                         key=lambda p: (0, int(p)) if p.isdigit() else (1, p))
        if missing:
            v("warning", "pin_no_net", None, [ref],
              f"pin(s) {', '.join(ref + '.' + p for p in missing)} exist "
              f"in the symbol but reach NO net (hierarchical exports drop "
              f"no-connect singletons; these pads arrive at board_init "
              f"with no net at all)", pins=missing)

    # 5. dangling single-pin nets (label-typo signature)
    for name in sorted(nets):
        if name.startswith("unconnected-"):
            continue
        if len(nets[name]) == 1:
            m = nets[name][0]
            v("warning", "dangling_net", name, [m["ref"]],
              f"net '{name}' has a single pin ({m['ref']}.{m['pin']}) - "
              f"label typo?")

    # 6. decoupling associations vs the netlist
    n_assoc = 0
    if decoupling is not None:
        assocs = decoupling.get("associations")
        if not isinstance(assocs, list):
            raise checklib.CheckError(
                "decoupling metadata has no associations[] list")
        n_assoc = len(assocs)
        member_index = {(m["ref"], m["pin"]): name
                        for name, members in nets.items() for m in members}
        for a in assocs:
            cap, ic = a.get("cap"), a.get("ic")
            pin, rail = str(a.get("pin")), a.get("rail")
            gnd = a.get("gnd", "GND")
            ctx = f"association {cap}->{ic}.{pin}"
            if cap not in comps:
                v("error", "metadata_mismatch", rail, [c for c in (ic,) if c],
                  f"{ctx}: cap '{cap}' not in netlist")
                continue
            if ic not in comps:
                v("error", "metadata_mismatch", rail, [cap],
                  f"{ctx}: ic '{ic}' not in netlist")
                continue
            if rail not in nets:
                v("error", "metadata_mismatch", rail, [cap, ic],
                  f"{ctx}: rail net '{rail}' not in netlist")
                continue
            if member_index.get((ic, pin)) != rail:
                v("error", "metadata_mismatch", rail, [cap, ic],
                  f"{ctx}: {ic}.{pin} is on net "
                  f"'{member_index.get((ic, pin), '<none>')}', not '{rail}'")
            cap_nets = {name for (r, _), name in member_index.items()
                        if r == cap}
            if rail not in cap_nets:
                v("error", "metadata_mismatch", rail, [cap],
                  f"{ctx}: no pin of '{cap}' is on rail '{rail}' "
                  f"(cap nets: {sorted(cap_nets)})")
            if gnd not in cap_nets:
                v("error", "metadata_mismatch", gnd, [cap],
                  f"{ctx}: no pin of '{cap}' is on return net '{gnd}' "
                  f"(cap nets: {sorted(cap_nets)})")
            want, have = a.get("value"), comps[cap]["value"]
            if want and have and want != have:
                v("warning", "metadata_mismatch", rail, [cap],
                  f"{ctx}: metadata value '{want}' != netlist value "
                  f"'{have}'")

    facts = {
        "nets": len(nets),
        "components": len(comps),
        "unconnected_pins": sum(1 for n in nets
                                if n.startswith("unconnected-")),
        "constraint_nets_checked": len(referenced),
        "constraint_refs_checked": len(ref_where),
        "pins_expected": pins_expected,
        "pins_connected": pins_connected,
        "decoupling_associations": n_assoc,
        "power": power_facts,
    }
    return violations, facts


def run_audit(netlist: Path, constraints_path: Path,
              decoupling_path: Path | None, out: str | None):
    parsed = parse_netlist(netlist)
    constraints = checklib.load_json(constraints_path, "constraints")
    decoupling = (checklib.load_json(decoupling_path, "decoupling metadata")
                  if decoupling_path else None)
    violations, facts = audit(parsed, constraints, decoupling)
    payload = checklib.report(SCRIPT, str(netlist), violations,
                              mode="audit", **facts)
    # warnings alone do not fail the audit (fp_verify precedent)
    has_error = any(x["severity"] == "error" for x in violations)
    payload["status"] = "violations" if has_error else "pass"
    return payload, out


# ------------------------------------------------------------ CLI

def _export_netlist(sch: Path) -> Path:
    """Export the schematic's netlist via kc.py into a temp file."""
    import kc  # noqa: PLC0415  (deferred: only the --sch path needs kicad-cli)
    from lib import env  # noqa: PLC0415
    cli = env.find_kicad_cli()
    out = Path(tempfile.mkdtemp(prefix="aiee_audit_")) / (sch.stem + ".net")
    r = kc.export_netlist(cli, sch, out)
    if r.get("status") != "pass":
        raise checklib.CheckError(
            f"netlist export failed for {sch}: {r.get('error') or r}")
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--netlist", help="kicadsexpr netlist to audit")
    ap.add_argument("--sch", help="schematic; netlist is exported first "
                                  "(needs kicad-cli)")
    ap.add_argument("--constraints", help="constraints.json (audit mode)")
    ap.add_argument("--decoupling", help="decoupling.json metadata to "
                                         "cross-check (audit mode)")
    ap.add_argument("--compare", help="second netlist: strict electrical "
                                      "identity diff instead of the audit")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)

    if not args.netlist and not args.sch:
        raise checklib.CheckError("give --netlist or --sch")
    netlist = Path(args.netlist) if args.netlist else None
    if args.sch and not netlist:
        netlist = _export_netlist(Path(args.sch))

    if args.compare:
        return run_compare(netlist, Path(args.compare), args.out)
    if not args.constraints:
        raise checklib.CheckError("audit mode needs --constraints "
                                  "(or use --compare)")
    return run_audit(netlist, Path(args.constraints),
                     Path(args.decoupling) if args.decoupling else None,
                     args.out)


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
