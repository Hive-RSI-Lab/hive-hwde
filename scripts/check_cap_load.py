"""check_cap_load.py - an op-amp output driving a cable or a big capacitor
keeps its phase margin, or has the build-out resistor that gives it back
(P8 verify suite; reference/ingest/analog-audio.md rules A2/A6, gap 2).

The hit: a line-out jack driven straight from an NE5532 through its coupling
cap. 10 m of cable is about 1 nF; with the op-amp's open-loop output
impedance Ro that puts a pole near the crossover, the loop rings or
oscillates at a few MHz, and nothing in ERC, DRC or an audio-band sim sees
it. A 47-100 ohm build-out is the practitioner's fix (ESP, GroupDIY), but
whether it is needed, and how big, is the calculation's call, not the
constant's: this check runs lib/analogcalc.cap_load_pm (agrees with ngspice
loop gain within 0.2 deg, analog_verify) on every op-amp output it can name.

What it reads, from the board's own <board>.net beside the .kicad_pcb:
  - an op-amp: a part whose value or lib part matches a row of
    reference/opamp_drive.yaml (GBW, open-loop Ro at the crossover), or one
    whose pins name a + input, a - input and an output (no data: warned
    when it drives a load, so the row gets added)
  - its output net, followed through 0 ohm links, beads and inductors, and
    series caps of 100 nF and up (a coupling cap is a short at MHz)
  - the load: caps from those nets to ground or a rail, plus a cable on
    every connector pin reached (constraints.json `cable_pf` {"J3": 200}
    per connector, else 1000 pF: 10 m at 100 pF/m)
  - a build-out: a series resistor out of those nets (not the feedback
    one); the load beyond it is checked with that resistor as Riso
  - the noise gain: 1 + Rf/Rg from the feedback resistor to the - input
    and the resistors from the - input elsewhere; a follower, a cap across
    Rf or no feedback found is taken as 1, the worst case

Findings:
  cap_load_pm        error: phase margin under 45 deg (ringing, or an
                     oscillator); warning under 60 deg. Gives the margin,
                     crossover, load pole, the Riso that reaches 60 deg and
                     the part data it used (and that it is unverified)
  cap_load_unknown   warning: an op-amp with no opamp_drive.yaml row drives
                     a connector or 100 pF or more with no build-out
                     resistor: no margin computed
  no_netlist         warning: the board has a part from the table but no
                     netlist beside it

CLI: --pcb board.kicad_pcb [--netlist board.net] [--constraints c.json]
     [--cable-pf 1000] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import analogcalc  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402
from check_ratings import volts_from_name  # noqa: E402
from check_straps import find_netlist  # noqa: E402
from engcalc import parse_value  # noqa: E402
from netlist_audit import _is_ground_name, parse_netlist  # noqa: E402

SCRIPT = "check_cap_load"
SOURCE = SCRIPT
DRIVE_YAML = HERE.parent / "reference" / "opamp_drive.yaml"

PM_ERROR = 45.0
PM_WARN = 60.0
CABLE_PF = 1000.0          # 10 m of audio cable at ~100 pF/m
BIG_C = 100e-12            # a load worth naming with no part data
COUPLING_C = 100e-9        # a series cap this big is a short at MHz
LINK_OHMS = 0.5            # 0 R / jumpers join nets

_OUT_RE = re.compile(r"^(?:V?OUT_?[A-D1-4]?|[A-D1-4]OUT|OUT_?[A-D1-4]|~)$")
_INV_RE = re.compile(r"^(?:-|-IN_?[A-D1-4]?|[A-D1-4]?IN_?[A-D1-4]?-|INV|"
                     r"IN_?[A-D1-4]?N)$")
_NONINV_RE = re.compile(r"^(?:\+|\+IN_?[A-D1-4]?|[A-D1-4]?IN_?[A-D1-4]?\+|NI|"
                        r"IN_?[A-D1-4]?P)$")
AMP_PREFIXES = ("U", "IC", "A", "AR", "OA", "OP", "N")  # an amp's refdes
_CONN_RE = re.compile(r"^(J|P|CN|X|CON)\d")
_UNIT_RE = re.compile(r"([A-D1-4])$")


def load_table(path: Path = DRIVE_YAML) -> list[dict]:
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))["parts"]
    for r in rows:
        r["_re"] = re.compile(r["match"], re.I)
    return rows


def _row(comp: dict, table: list[dict]) -> dict | None:
    names = [comp.get("value") or "", (comp.get("libsource") or ("", ""))[1]]
    for r in table:
        if any(r["_re"].search(n.strip()) for n in names if n):
            return r
    return None


def _prefix(ref: str) -> str:
    return re.match(r"^[A-Za-z]*", ref or "").group(0).upper()


def _fixed(net: str) -> bool:
    """Ground or a supply rail: an AC ground for the load."""
    return _is_ground_name(net) or volts_from_name(net) is not None


class Board:
    def __init__(self, parsed: dict):
        self.comps = parsed["components"]
        self.nets = parsed["nets"]
        self.pins: dict[str, dict[str, tuple[str, str, str]]] = {}
        for name, members in self.nets.items():
            for m in members:
                self.pins.setdefault(m["ref"], {})[m["pin"]] = (
                    name, (m.get("pinfunction") or "").upper(),
                    (m.get("pintype") or "").split("+")[0])

    def two_pin(self, ref: str, pin: str, net: str) -> str | None:
        """The far net of a 2-pin part entered on `pin`, else None."""
        pins = self.pins.get(ref, {})
        if len(pins) != 2:
            return None
        return next(p[0] for k, p in pins.items() if k != pin)

    def value(self, ref: str) -> float | None:
        return parse_value(self.comps.get(ref, {}).get("value"))


def opamps(board: Board, table: list[dict]) -> list[tuple[str, dict | None]]:
    """(ref, table row or None) for every op-amp: a table match, or a U part
    whose pins name a + input, a - input and an output."""
    out = []
    for ref in sorted(board.comps):
        row = _row(board.comps[ref], table)
        if row is None:
            if _prefix(ref) not in AMP_PREFIXES:
                continue
            fns = {f for _, f, _ in board.pins.get(ref, {}).values()}
            if not (any(_INV_RE.match(f) for f in fns)
                    and any(_NONINV_RE.match(f) for f in fns)
                    and any(_OUT_RE.match(f) for f in fns)):
                continue
        out.append((ref, row))
    return out


def _outputs(board: Board, ref: str) -> list[tuple[str, str, str]]:
    """(pin, name, net) of the op-amp's outputs."""
    return [(pin, f, net) for pin, (net, f, t) in sorted(
        board.pins.get(ref, {}).items())
        if _OUT_RE.match(f) or (t == "output" and not _fixed(net))]


def _inv_inputs(board: Board, ref: str) -> list[tuple[str, str, str]]:
    return [(pin, f, net) for pin, (net, f, _) in sorted(
        board.pins.get(ref, {}).items()) if _INV_RE.match(f)]


def noise_gain(board: Board, ref: str, out_name: str, out_net: str
               ) -> tuple[float, str, str | None]:
    """(noise gain, how it was found, the - input's net or None)."""
    invs = _inv_inputs(board, ref)
    unit = _UNIT_RE.search(out_name.replace("OUT", "", 1) or "")
    if unit:
        same = [i for i in invs if unit.group(1) in i[1].replace("IN", "")]
        invs = same or invs
    for _, _, inv_net in invs:
        if inv_net == out_net:
            return 1.0, "follower", inv_net
    for _, _, inv_net in invs:
        rf, cf, rg = [], [], []
        for m in board.nets.get(inv_net, []):
            far = board.two_pin(m["ref"], m["pin"], inv_net)
            if far is None or far == inv_net:
                continue
            p = _prefix(m["ref"])
            if far == out_net and p == "R":
                rf.append(m["ref"])
            elif far == out_net and p == "C":
                cf.append(m["ref"])
            elif p == "R":
                rg.append(m["ref"])
        if not rf:
            continue
        if cf:
            return 1.0, f"{'/'.join(cf)} across {rf[0]}: 1 at the crossover", \
                inv_net
        rfv = board.value(rf[0])
        gs = [board.value(r) for r in rg]
        if rfv is None or not gs or any(g is None or g <= 0 for g in gs):
            return 1.0, f"feedback {rf[0]} with no readable Rg: taken as 1", \
                inv_net
        rgv = 1.0 / sum(1.0 / g for g in gs)
        return 1.0 + rfv / rgv, f"1 + {rf[0]}/{'||'.join(rg)}", inv_net
    return 1.0, "no feedback found: taken as 1", None


def _walk(board: Board, start: str, skip: set[str], amp: str, stop: set[str],
          cable_pf: dict, default_pf: float, branches: list | None):
    """The load on `start` and the nets joined to it at MHz: caps to ground
    or a rail, connector pins (cable), and - when `branches` is a list -
    series resistors out (each (ref, ohms, far net))."""
    caps, conns, seen, todo = [], [], {start}, [start]
    while todo:
        n = todo.pop()
        for m in board.nets.get(n, []):
            ref = m["ref"]
            if ref == amp:
                continue
            p = _prefix(ref)
            if _CONN_RE.match(ref):
                if ref not in {c[0] for c in conns}:
                    pf = float(cable_pf.get(ref, default_pf))
                    conns.append((ref, pf * 1e-12, n))
                continue
            far = board.two_pin(ref, m["pin"], n)
            if far is None or far == n or far in skip:
                continue
            through = False
            if p == "R":
                ohms = board.value(ref)
                if ohms is None or _fixed(far):
                    continue
                if ohms <= LINK_OHMS:
                    through = True
                elif branches is not None and far not in stop:
                    branches.append((ref, ohms, far))
            elif p == "C":
                c = board.value(ref)
                if c is None:
                    continue
                if _fixed(far):
                    caps.append((ref, c, n))
                elif c >= COUPLING_C:
                    through = True
            elif p in ("L", "FB"):
                through = not _fixed(far)
            if through and far not in seen and far not in stop:
                seen.add(far)
                todo.append(far)
    return caps, conns, seen


def _fmt_c(c: float) -> str:
    return f"{c * 1e9:.3g} nF" if c >= 1e-9 else f"{c * 1e12:.3g} pF"


def check_output(board: Board, ref: str, row: dict | None, pin: str,
                 name: str, net: str, cable_pf: dict, default_pf: float):
    viols, facts = [], []
    ng, ng_how, inv_net = noise_gain(board, ref, name, net)
    skip = {inv_net} if inv_net else set()
    branches: list = []
    caps, conns, direct = _walk(board, net, skip, ref, set(), cable_pf,
                                default_pf, branches)
    loads = [(0.0, None, caps, conns, net)]
    for rref, ohms, far in branches:
        bc, bj, _ = _walk(board, far, skip | direct, ref, direct, cable_pf,
                          default_pf, None)
        if bc or bj:
            loads.append((ohms, rref, bc, bj, far))
    for riso, rref, cs, js, at in loads:
        cl = sum(c for _, c, _ in cs) + sum(c for _, c, _ in js)
        if cl <= 0:
            continue
        what = [f"{r} {_fmt_c(c)}" for r, c, _ in cs] + \
            [f"{r} cable {_fmt_c(c)}" for r, c, _ in js]
        refs = [ref] + [r for r, _, _ in cs] + [r for r, _, _ in js] + \
            ([rref] if rref else [])
        fact = {"ref": ref, "pin": pin, "output": name, "net": net,
                "load_net": at, "cl_f": cl, "load": what,
                "riso_ohm": riso, "riso_ref": rref,
                "noise_gain": checklib.rnd(ng, 3), "noise_gain_from": ng_how}
        facts.append(fact)
        if row is None:
            if not rref and (cl >= BIG_C or js):
                viols.append(checklib.violation(
                    SCRIPT, "warning", None, None, at, refs,
                    f"{ref}.{pin} ({name}) drives {_fmt_c(cl)} "
                    f"({', '.join(what)}) with no build-out resistor "
                    "but the op-amp has no row in reference/"
                    "opamp_drive.yaml (GBW, open-loop Ro), so its phase "
                    "margin wasn't computed: add the row from the datasheet",
                    SOURCE, kind="cap_load_unknown", pin=pin,
                    cl_pf=checklib.rnd(cl * 1e12, 1)))
            continue
        gbw, ro = float(row["gbw_hz"]), float(row["ro_ohm"])
        fp2 = float(row.get("fp2_hz") or float("inf"))
        res = analogcalc.cap_load_pm(gbw, ro, cl, ng, riso, fp2)
        pm = res["phase_margin_deg"]
        fact.update(phase_margin_deg=checklib.rnd(pm, 1),
                    crossover_hz=checklib.rnd(res["crossover_hz"], 0))
        if pm >= PM_WARN:
            continue
        need = analogcalc.riso_for_pm(gbw, ro, cl, ng, PM_WARN, fp2)
        fact["riso_for_60deg"] = checklib.rnd(need, 1) if need else None
        sev = "error" if pm < PM_ERROR else "warning"
        data = (f"GBW {gbw / 1e6:g} MHz, open-loop Ro {ro:g} ohm, noise "
                f"gain {ng:.3g} ({ng_how})"
                + ("" if row.get("verified") else
                   "; the part data is not yet re-read from the datasheet: "
                   "check Ro on its Zo plot"))
        stable = (f"; the datasheet says stable into {row['cl_stable_pf']} pF"
                  if row.get("cl_stable_pf") else "")
        fix = (f"a build-out of {need:.0f} ohm or more in series, feedback "
               "taken before it, reaches 60 deg" if need else
               "no build-out up to 10 kohm reaches 60 deg: buffer it or "
               "take the feedback after the load (in-loop isolation)")
        viols.append(checklib.violation(
            SCRIPT, sev, None, None, at, refs,
            f"{ref}.{pin} ({name}) drives {_fmt_c(cl)} ({', '.join(what)}) "
            + (f"through {rref} {riso:g} ohm" if rref else
               "with no build-out resistor")
            + f": phase margin {pm:.0f} deg at {res['crossover_hz'] / 1e6:.2g}"
            f" MHz (load pole {res['load_pole_hz'] / 1e6:.2g} MHz); {fix}. "
            f"Part data: {data}{stable}",
            SOURCE, kind="cap_load_pm", pin=pin,
            phase_margin_deg=checklib.rnd(pm, 1),
            cl_pf=checklib.rnd(cl * 1e12, 1), riso_ohm=riso,
            riso_for_60deg=checklib.rnd(need, 1) if need else None,
            noise_gain=checklib.rnd(ng, 3),
            part_verified=bool(row.get("verified"))))
    return viols, facts


def check(pcb: Path, netlist: Path | None = None,
          constraints: dict | None = None, default_pf: float = CABLE_PF,
          table: list[dict] | None = None):
    table = load_table() if table is None else table
    cable_pf = (constraints or {}).get("cable_pf") or {}
    netlist = netlist or find_netlist(pcb)
    if netlist is None:
        bg = geom.load_board(pcb)
        amps = sorted(ref for ref, fp in bg.footprints.items()
                      if any(r["_re"].search(fp.get("value") or "")
                             for r in table))
        if not amps:
            return [], {"netlist": None, "opamps": []}
        return [checklib.violation(
            SCRIPT, "warning", None, None, None, amps,
            f"{', '.join(amps)}: no netlist beside {pcb.name} "
            f"({pcb.with_suffix('.net').name}), so the op-amps' capacitive "
            "loads weren't checked: export it (kicad-cli sch export netlist)",
            SOURCE, kind="no_netlist")], {"netlist": None, "opamps": amps}
    board = Board(parse_netlist(netlist))
    viols, outs = [], []
    found = opamps(board, table)
    for ref, row in found:
        for pin, name, net in _outputs(board, ref):
            v, f = check_output(board, ref, row, pin, name, net, cable_pf,
                                default_pf)
            viols += v
            outs += f
    return viols, {"netlist": str(netlist),
                   "opamps": [r for r, _ in found], "outputs": outs}


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--netlist")
    ap.add_argument("--constraints")
    ap.add_argument("--cable-pf", type=float, default=CABLE_PF,
                    help="cable capacitance on a connector with no "
                    "constraints.json cable_pf entry (default 1000)")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    nl = Path(args.netlist) if args.netlist else None
    if nl is not None and not nl.is_file():
        raise checklib.CheckError(f"netlist not found: {nl}")
    cons = None
    if args.constraints:
        cons = json.loads(Path(args.constraints).read_text(encoding="utf-8"))
    viols, seen = check(pcb, nl, cons, args.cable_pf)
    return checklib.report(SCRIPT, pcb, viols, checked=[seen],
                           nothing_to_judge=None if seen.get("opamps") else
                           "no op-amp found"), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
