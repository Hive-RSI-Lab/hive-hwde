"""check_surge.py - IEC 61000-4-5 surge and IEC 61000-4-4 EFT on declared
cable ports (hive-hwde #492).

check_esd looks at nanosecond strikes from a finger. A cable brings something
slower and far more energetic: the combination wave delivers 500 A per kV
into a short for 8/20 us. A clamp sized for ESD dies on it, and a clamp that
survives may still let through more than the part behind it can take.

Only ports declared in constraints are judged; the check says nothing about
a board that declares none, since what a port is cabled to isn't in the board:

  "surge": {
    "environment": "residential",        // or industrial
    "ports": {"J1": {"kind": "dc_power", // dc_power|signal|ethernet|mains
                     "cable_m": 2, "outdoor": false,
                     "earthed": false,   // le modes on dc_power/mains
                     "nets": ["VBUS"],   // default by kind (below)
                     "level_kv": 0.5,    // override the assumed level
                     "eft_kv": 0.5}},
    "clamps": {"D1": {"vbr": 23.3, "vc": 32.4, "ipp_a": 100}},
    "abs_max": {"U1": 32, "U2.3": 30}
  }

Levels come from reference/surge_levels.yaml by kind, environment and cable
length (below the table's min_cable_m and not outdoor the level doesn't
apply, unless level_kv forces one); every finding marks its level as an
assumption from the declaration, since the product's standard decides.
Nets: dc_power takes the port's power-named nets, signal and ethernet its
other nets, mains all; never ground-named or unconnected ones.

For each line and coupling mode, walking from the connector through series
parts (R <= SERIES_MAX_OHM, L, FB, fuse; a bigger R isolates what's behind):

    Z = 2 ohm + coupling + R_series up to the clamp
    I = (V - Vbr) / (Z + Rdyn),  Vc(I) = Vbr + Rdyn * I

(V/Z, the usual selection-guide figure, is reported too as i_simple_a.)

Findings:
  surge_unclamped           error: an applicable surge and no clamp on the
                            line (ethernet: warning surge_relies_on_magnetics,
                            the transformer takes it unless a clamp is there)
  surge_clamp_overstress    error: I over the clamp's Ipp(8/20) or P over its
                            Ppk(8/20)
  surge_clamp_unrated       warning: a clamp nothing gives ratings for
  surge_vc_over_abs_max     error: Vc(I) over the abs max of an IC pin on the
                            protected stretch. A secondary clamp behind a
                            series element is judged at its own Vc instead;
                            the message says which of the two is missing
  surge_eft_over_abs_max    warning: the EFT burst's Vc over a pin's abs max
                            (5/50 ns, 50 ohm; its energy is too small to judge
                            against an 8/20 rating, so no overstress finding)

Pin abs max: constraints surge.abs_max (ref or ref.pin) first, then the part's
extraction (<parts>/<LCSC>.json), then reference/pin_ratings.yaml's abs_max_v
by pin name, or by pad number (pin_numbers) when the board's pads carry no
pin names. Pins nothing rates go in the report as unrated_pins, not findings.

Not modelled: a unidirectional clamp's other polarity (its forward drop), bulk
capacitors on the line (they eat some of the 1.2/50 energy, so this errs
high), series element dissipation, 10/700 us telecom waves, a MOV + fuse
coordination. --spice runs the wave into each clamp's diode model in ngspice
and adds spice_i_a / spice_vc_v to the report; findings stay on the closed form.

CLI exit 0 pass / 1 violations / 2 error, per SPEC section 6.
    bin/hwde check_surge --pcb board.kicad_pcb --constraints c.json [--parts D]
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import check_esd  # noqa: E402
import checklib  # noqa: E402
import engcalc  # noqa: E402
import geom  # noqa: E402
import surgelib  # noqa: E402

SCRIPT = "check_surge"
SOURCE = SCRIPT
KINDS = ("dc_power", "signal", "ethernet", "mains")
SERIES_MAX_OHM = 100.0
IC_PREFIXES = ("U", "IC")


def _ref_letters(ref: str) -> str:
    return ref.rstrip("0123456789").upper()


def _texts(fp: dict, part: dict | None) -> list[str]:
    return [t for t in ((part or {}).get("mpn"), fp.get("value")) if t]


def load_parts(parts_dir: Path | None) -> dict:
    """ref -> parts.json entry."""
    pj = parts_dir / "parts.json" if parts_dir else None
    if not pj or not pj.is_file():
        return {}
    out = {}
    for p in checklib.load_json(pj, "parts.json").get("parts") or []:
        for r in p.get("refdes") or []:
            out[r] = p
    return out


def port_levels(port: dict, env_table: dict) -> tuple[list[dict], float | None]:
    """The port's surge modes [{mode, kv, z_ohm}] and its EFT kV after the
    table, cable length, earth and overrides. Empty / None = doesn't apply."""
    row = env_table[port["kind"]]
    cable = float(port.get("cable_m", 0))
    outdoor = bool(port.get("outdoor"))
    forced = port.get("level_kv")
    modes = []
    if forced is not None or outdoor or cable > row["surge"]["min_cable_m"] \
            or row["surge"]["min_cable_m"] == 0:
        for m in row["surge"]["modes"]:
            if m["mode"] == "le" and port["kind"] in ("dc_power", "mains") \
                    and not port.get("earthed"):
                continue
            modes.append({**m, "kv": float(forced if forced is not None
                                           else m["kv"])})
    eft = port.get("eft_kv")
    if eft is None and (outdoor or cable > row["eft"]["min_cable_m"]
                        or row["eft"]["min_cable_m"] == 0):
        eft = row["eft"]["kv"]
    return modes, None if eft is None else float(eft)


def _port_nets(bg, ref: str, port: dict) -> list[tuple]:
    """(net, connector pad) for the lines the declaration takes."""
    out, seen = [], set()
    for cp in bg.pads_of(ref=ref):
        net = cp.net
        if not net or net in seen or check_esd.is_ground(net) \
                or "unconnected" in net.lower():
            continue
        if not [p for p in bg.pads_of(net) if p.ref != ref]:
            continue
        if port.get("nets"):
            if not any(fnmatch.fnmatch(net, g) for g in port["nets"]):
                continue
        elif port["kind"] == "dc_power" and not check_esd.is_power(net):
            continue
        elif port["kind"] in ("signal", "ethernet") and check_esd.is_power(net):
            continue
        seen.add(net)
        out.append((net, cp))
    return out


def _series_ohms(ref: str, fp: dict) -> float | None:
    """A series part's resistance, 0 for an inductor, bead or fuse; None when
    it isn't a series part or is too big to carry the surge onward."""
    letters = _ref_letters(ref)
    if letters in ("L", "FB", "F"):
        return 0.0
    if letters == "R":
        v = engcalc.parse_value(fp.get("value"))
        if v is not None and v <= SERIES_MAX_OHM:
            return v
    return None


def walk(bg, start: str, stop_refs: set) -> dict:
    """net -> (ohms from the connector, [series refs]) across series parts."""
    best = {start: (0.0, [])}
    todo = [start]
    while todo:
        todo.sort(key=lambda n: best[n][0])
        net = todo.pop(0)
        ohms, via = best[net]
        for p in bg.pads_of(net):
            if p.ref in stop_refs or p.ref in via:
                continue
            r = _series_ohms(p.ref, bg.footprints[p.ref])
            pads = bg.pads_of(ref=p.ref)
            if r is None or len(pads) != 2:
                continue
            other = next(q for q in pads if q.number != p.number).net
            if not other or other == net or check_esd.is_ground(other):
                continue
            if other not in best or ohms + r < best[other][0]:
                best[other] = (ohms + r, via + [p.ref])
                todo.append(other)
    return best


def _extraction_ratings(part: dict | None, parts_dir: Path | None) -> dict:
    lcsc = (part or {}).get("lcsc")
    ext = parts_dir / f"{lcsc}.json" if parts_dir and lcsc else None
    if not ext or not ext.is_file():
        return {}
    import check_ratings
    return check_ratings.part_ratings(checklib.load_json(ext, "extraction"))


def pin_limit(pad, fp, part, parts_dir, table, overrides) -> tuple:
    """(abs max volts, source) for an IC pad, or (None, "")."""
    for key in (f"{pad.ref}.{pad.number}", pad.ref):
        if key in overrides:
            return float(overrides[key]), "constraints surge.abs_max"
    rows = [r for r in (_extraction_ratings(part, parts_dir).get(pad.number)
                        or {}).get(("voltage", "abs")) or []
            if r["ref"] is None and isinstance(r["hi"], (int, float))]
    if rows:
        r = min(rows, key=lambda r: r["hi"])
        return float(r["hi"]), f"{part.get('lcsc')}.json {r['src']}"
    names = _texts(fp, part)
    for r in table:
        if not any(r["_m"].search(x) for x in names):
            continue
        if (pad.pinfunction and r["_p"].match(pad.pinfunction)) or (
                not pad.pinfunction and r.get("_n")
                and r["_n"].match(pad.number)):
            return float(r["abs_max_v"]), ("reference/pin_ratings.yaml: "
                                           f"{str(r.get('source', '')).strip()}")
    return None, ""


def _pin_table() -> list[dict]:
    import sim_board
    rows = [r for r in sim_board.load_pin_ratings() if r.get("abs_max_v")]
    return [{**r, "_n": re.compile(r["pin_numbers"])} if r.get("pin_numbers")
            else r for r in rows]


def _clamps_on(bg, reach: dict, clamps: set, ratings) -> list[dict]:
    """The clamps on the reached nets with a pad to ground, nearest first."""
    out = {}
    for net, (ohms, via) in reach.items():
        for p in bg.pads_of(net):
            if p.ref not in clamps:
                continue
            if not any(check_esd.is_ground(q.net)
                       for q in bg.pads_of(ref=p.ref)):
                continue
            if p.ref not in out or ohms < out[p.ref]["ohms"]:
                out[p.ref] = {"ref": p.ref, "net": net, "ohms": ohms,
                              "via": via, "pos": p.copper_center,
                              "model": ratings(p.ref)}
    return sorted(out.values(), key=lambda c: (c["ohms"], c["ref"]))


def judge_mode(ctx, ref, net, cp, mode, cls, reach, facts) -> None:
    """One coupling mode of one line: the clamp's stress, then the pins."""
    v_kv, z_mode = mode["kv"], mode["z_ohm"]
    v = v_kv * 1000.0
    vios = ctx["vios"]
    assumed = (f"{v_kv:g} kV {mode['mode'].upper()} is the level assumed from "
               f"{ref}'s declaration as {ctx['ports'][ref]['kind']}, not a "
               "requirement")
    row = {"mode": mode["mode"], "level_kv": v_kv, "z_mode_ohm": 2 + z_mode}
    facts["modes"].append(row)
    if not cls:
        kind = ctx["ports"][ref]["kind"]
        relies = kind == "ethernet"
        vios.append(checklib.violation(
            SCRIPT, "warning" if relies else "error", cp.copper_center, None,
            net, [ref],
            f"{ref} pin {cp.number} ({net}): no clamp on the line; "
            + ("the surge rests on the transformer's isolation alone"
               if relies else
               f"a surge drives {v / (2 + z_mode):.0f} A into whatever is "
               "there: add a TVS rated for it")
            + f" ({assumed})", SOURCE,
            kind="surge_relies_on_magnetics" if relies else "surge_unclamped",
            level_kv=v_kv, assumption=True))
        return
    rated = [c for c in cls if c["model"]]
    if not rated:
        return
    first = rated[0]
    z = surgelib.GENERATOR_OHM + z_mode + first["ohms"]
    got = surgelib.clamped(v, z, first["model"])
    m = first["model"]
    row.update(clamp=first["ref"], z_ohm=checklib.rnd(z, 3),
               i_a=checklib.rnd(got["i_a"], 1),
               i_simple_a=checklib.rnd(v / z, 1),
               vc_v=checklib.rnd(got["vc_v"], 1),
               p_w=checklib.rnd(got["p_w"], 0),
               ipp_8_20_a=checklib.rnd(m["ipp_a"], 1),
               ppk_8_20_w=checklib.rnd(m["ppk_w"], 0),
               rating_estimated=bool(m.get("estimated")))
    if ctx["spice"] is not None:
        ctx["spice"].append((row, v_kv, z - surgelib.GENERATOR_OHM, m))
    if got["i_a"] > m["ipp_a"] or got["p_w"] > m["ppk_w"]:
        vios.append(checklib.violation(
            SCRIPT, "error", first["pos"], None, net, [first["ref"], ref],
            f"{first['ref']} on {ref} {net}: {got['i_a']:.0f} A / "
            f"{got['p_w'] / 1000:.1f} kW through Z = {z:g} ohm exceeds its "
            f"8/20 us rating {m['ipp_a']:.0f} A / {m['ppk_w'] / 1000:.1f} kW"
            + (" (rating estimated from the 10/1000 us line)"
               if m.get("estimated") else "")
            + f": the clamp fails short. {assumed}", SOURCE,
            kind="surge_clamp_overstress", level_kv=v_kv, assumption=True,
            i_a=row["i_a"], ipp_a=row["ipp_8_20_a"]))
    judge_pins(ctx, ref, net, reach, rated, got["vc_v"], assumed, row, vios,
               kind="surge_vc_over_abs_max", severity="error", kv=v_kv)


def judge_pins(ctx, ref, net, reach, cls, vc, assumed, row, vios, kind,
               severity, kv) -> None:
    """Pins on the stretch against the voltage that reaches them: the first
    clamp's Vc, or a secondary clamp's when the pin sits on or behind it."""
    first = cls[0]
    second = next((c for c in cls[1:] if c["model"] and c["ohms"] > first["ohms"]),
                  None)
    v2 = None
    if second:
        dr = second["ohms"] - first["ohms"]
        v2 = surgelib.clamped(vc, dr, second["model"])["vc_v"]
        row["secondary"] = second["ref"]
        row["secondary_vc_v"] = checklib.rnd(v2, 1)
    pins = []
    for pnet, (ohms, via) in sorted(reach.items()):
        for p in ctx["bg"].pads_of(pnet):
            if p.ref in ctx["clamps"] or \
                    _ref_letters(p.ref) not in IC_PREFIXES:
                continue
            fp = ctx["bg"].footprints[p.ref]
            part = ctx["parts"].get(p.ref)
            lim, src = pin_limit(p, fp, part, ctx["parts_dir"], ctx["table"],
                                 ctx["abs_max"])
            behind = bool(second) and ohms >= second["ohms"]
            at_pin = v2 if behind else vc
            if lim is None:
                ctx["unrated"].add(f"{p.ref}.{p.number}")
                continue
            pins.append({"ref": p.ref, "pin": p.number, "net": pnet,
                         "abs_max_v": lim, "at_pin_v": checklib.rnd(at_pin, 1),
                         "source": src})
            if at_pin <= lim:
                continue
            behind_series = ohms > first["ohms"] or len(via) > len(first["via"])
            hint = ("the secondary clamp still lets it through" if behind else
                    "the series element has no clamp behind it: add a "
                    "secondary clamp" if behind_series else
                    "add a series element and a secondary clamp, or a "
                    "lower-Vc clamp")
            vios.append(checklib.violation(
                SCRIPT, severity, p.copper_center, None, pnet,
                [p.ref, first["ref"], ref],
                f"{p.ref} pin {p.number} ({pnet}): {at_pin:.1f} V during the "
                f"pulse on {ref} {net}, over its abs max {lim:g} V ({src}): "
                f"{hint}. {assumed}", SOURCE, kind=kind, level_kv=kv,
                assumption=True, at_pin_v=checklib.rnd(at_pin, 1),
                abs_max_v=lim, pin=p.number))
    row["pins"] = pins


def judge_eft(ctx, ref, net, kv, cls, reach, facts) -> None:
    facts["eft_kv"] = kv
    rated = [c for c in cls if c["model"]]
    if not rated:
        return
    first = rated[0]
    got = surgelib.clamped(kv * 1000.0, surgelib.EFT_OHM + first["ohms"],
                           first["model"])
    row = {"level_kv": kv, "clamp": first["ref"],
           "i_a": checklib.rnd(got["i_a"], 1),
           "vc_v": checklib.rnd(got["vc_v"], 1)}
    facts["eft"] = row
    assumed = (f"{kv:g} kV EFT is the level assumed from {ref}'s declaration "
               f"as {ctx['ports'][ref]['kind']}, not a requirement")
    judge_pins(ctx, ref, net, reach, rated, got["vc_v"], assumed, row,
               ctx["vios"], kind="surge_eft_over_abs_max", severity="warning",
               kv=kv)


def run_spice(items: list, timeout: float = 120.0) -> str | None:
    """The wave into each clamp's diode model; the peaks into its report row.
    -> the reason it couldn't run, or None."""
    if not items:
        return None
    import env as envlib
    import sim_board
    dll = envlib.find_ngspice_dll()
    if dll is None:
        return "no ngspice library"
    decks = [surgelib.clamp_deck(kv, z, m) for _r, kv, z, m in items]
    plots, _ver = sim_board.run_engine(decks, dll, timeout)
    for (row, _kv, _z, _m), plot in zip(items, plots):
        row["spice_i_a"] = checklib.rnd(max(plot["s"]) / 1e-3, 1)
        row["spice_vc_v"] = checklib.rnd(max(plot["c"]), 1)
    return None


def check(pcb: Path, constraints: dict | None = None,
          parts_dir: Path | None = None, spice: bool = False
          ) -> tuple[list, dict]:
    cfg = (constraints or {}).get("surge") or {}
    ports = cfg.get("ports") or {}
    if not ports:
        return [], {"ports_declared": 0, "lines": [],
                    "note": "no surge.ports in constraints: nothing to judge"}
    levels = surgelib.load_levels()
    env = cfg.get("environment", "residential")
    if env not in levels["environments"]:
        raise checklib.CheckError(f"surge.environment {env!r}: want one of "
                                  f"{sorted(levels['environments'])}")
    bg = geom.load_board(pcb)
    fps = bg.footprints
    parts = load_parts(parts_dir)
    table = surgelib.load_clamp_table()
    overrides = cfg.get("clamps") or {}
    clamps = {r for r, fp in fps.items()
              if check_esd.is_clamp(fp, set(overrides), r)}
    models: dict = {}

    def ratings(ref):
        if ref not in models:
            models[ref] = surgelib.find_clamp(
                _texts(fps[ref], parts.get(ref)), table, overrides.get(ref))
        return models[ref]

    ctx = {"bg": bg, "ports": ports, "clamps": clamps, "parts": parts,
           "parts_dir": parts_dir, "table": _pin_table(),
           "abs_max": cfg.get("abs_max") or {}, "vios": [], "unrated": set(),
           "spice": [] if spice else None}
    lines, unrated_clamps = [], set()
    for ref, port in sorted(ports.items()):
        if port.get("kind") not in KINDS:
            raise checklib.CheckError(f"surge.ports.{ref}.kind "
                                      f"{port.get('kind')!r}: want one of {KINDS}")
        if ref not in fps:
            raise checklib.CheckError(f"surge.ports.{ref}: no such part")
        modes, eft = port_levels(port, levels["environments"][env])
        for net, cp in _port_nets(bg, ref, port):
            reach = walk(bg, net, {ref})
            cls = _clamps_on(bg, reach, clamps, ratings)
            facts = {"port": ref, "kind": port["kind"], "net": net,
                     "environment": env, "assumption": True, "modes": [],
                     "clamps": [c["ref"] for c in cls],
                     "reach": {n: checklib.rnd(o, 3)
                               for n, (o, _v) in sorted(reach.items())}}
            lines.append(facts)
            for c in cls:
                if not c["model"] and c["ref"] not in unrated_clamps:
                    unrated_clamps.add(c["ref"])
                    ctx["vios"].append(checklib.violation(
                        SCRIPT, "warning", c["pos"], None, c["net"], [c["ref"]],
                        f"{c['ref']} clamps {ref} {net} but nothing gives its "
                        "8/20 us ratings: add it to reference/surge_clamps."
                        "yaml or constraints surge.clamps", SOURCE,
                        kind="surge_clamp_unrated"))
            for mode in modes:
                judge_mode(ctx, ref, net, cp, mode, cls, reach, facts)
            if eft is not None:
                judge_eft(ctx, ref, net, eft, cls, reach, facts)
    report = {"ports_declared": len(ports), "environment": env,
              "levels_source": str(levels.get("source", "")).strip(),
              "levels_verified": bool(levels.get("verified")),
              "lines": lines, "unrated_pins": sorted(ctx["unrated"]),
              "clamp_ratings": {
                  r: {k: (checklib.rnd(v, 3) if isinstance(v, float) else v)
                      for k, v in m.items()}
                  for r, m in sorted(models.items()) if m}}
    if spice:
        why = run_spice(ctx["spice"])
        if why:
            report["spice_skipped"] = why
    return ctx["vios"], report


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints")
    ap.add_argument("--parts", help="parts dir (parts.json, <LCSC>.json)")
    ap.add_argument("--spice", action="store_true",
                    help="also run the wave into each clamp in ngspice")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    cons = (checklib.load_json(args.constraints, "constraints")
            if args.constraints else None)
    viols, facts = check(pcb, cons, Path(args.parts) if args.parts else None,
                         args.spice)
    return checklib.report(SCRIPT, pcb, viols, checked=[facts],
                           nothing_to_judge=None if facts.get("ports_declared")
                           else "no surge.ports in constraints"), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
