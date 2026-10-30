"""pdsource.py - a USB PD source as SPICE, and the scenarios a sink's power
path meets from one.

The source is a voltage step generator behind the cable: a PWL source
that can only deliver current (an ideal diode in series: a charger does not
sink its sink's bulk back), CABLE_R and CABLE_L in series (1 m of USB-C, as
hifi-receiver-2's power review benched it), and a DISCHARGE_OHMS bleeder
switched on only while the source is off in a hard reset (how a source
brings VBUS to vSafe0V). The sink's own bulk, inrush and switch see the
edges a real charger gives them:

  attach      vSafe5V comes up at ATTACH_SLEW (Type-C sets no slew; 10 V/ms
              is a fast charger), firmware off, PG never asserted.
  step_<s>    attach, then the negotiated step 5 V -> contract at s V/ms:
              0.5, 3 and 30 (vSrcSlewPos max, 30 mV/us in USB PD 3.x),
              firmware off, PG asserted PG_DELAY after the new voltage
              settles - the window where a switch held off only by its
              gate network must stay off.
  fw_on       the contract voltage settled, PG asserted, firmware drives
              every I/O high at T_ON: the turn-on a load switch makes into
              its bulk every power-up.
  hard_reset  fw_on, then a hard reset: the source drops to vSafe0V,
              stays off RECOVER_S (tSrcRecover's 0.66 s minimum: a switch
              whose gate takes longer than that to turn off is still on
              when the source comes back), returns at vSafe5V and steps to
              the contract again at 30 V/ms - with firmware still on and PG
              released while the contract is gone. Whatever the switch
              does here, the source sees.

OCP and the hard reset a source answers it with are judged on the result,
not modelled inside the source: a sink drawing more than its contract for
longer than OCP_S (or more than OCP_PEAK_X times it at any instant) gets a
finding that names the scenario, because a source that trips there drops
VBUS and starts the hard_reset scenario over (an attach-reset loop when the
same path repeats).
"""
from __future__ import annotations

CABLE_R = 0.1
CABLE_L = 0.6e-6
ATTACH_SLEW = 10.0     # V/ms
STEP_SLEWS = (0.5, 3.0, 30.0)
SAFE5V = 5.0
T_ATTACH = 1e-3
T_STEP = 150e-3
PG_DELAY = 5e-3
T_ON = 20e-3
WINDOW = 400e-3
RECOVER_S = 0.66
RESET_SLEW = 30.0      # V/ms, falling to vSafe0V and stepping back up
DISCHARGE_OHMS = 100.0
OCP_S = 1e-3
OCP_PEAK_X = 2.0


def _pwl(points: list[tuple[float, float]]) -> str:
    return "PWL(" + " ".join(f"{t:.6g} {v:.6g}" for t, v in points) + ")"


def scenarios(vc: float, slews=STEP_SLEWS, window: float = WINDOW,
              recover: float = RECOVER_S) -> list[dict]:
    """[{name, src: [(t, v)], discharge, fw, pg: [(t, 0|1)], tstop,
    judge_from, fw_off: bool}] for a contract voltage vc. fw and pg are
    1 when firmware drives I/O high / PG is asserted (pulled low);
    discharge is 1 while the source's bleeder is on."""
    out = []
    t_up = T_ATTACH + SAFE5V / ATTACH_SLEW * 1e-3
    attach = [(0.0, 0.0), (T_ATTACH, 0.0), (t_up, SAFE5V)]
    out.append({"name": "attach", "src": attach + [(60e-3, SAFE5V)],
                "discharge": [(0.0, 0)],
                "fw": [(0.0, 0)], "pg": [(0.0, 0)], "tstop": 60e-3,
                "judge_from": 0.0, "fw_off": True})
    for s in slews:
        t_end = T_STEP + (vc - SAFE5V) / s * 1e-3
        out.append({
            "name": f"step_{s:g}", "slew_v_per_ms": s,
            "src": attach + [(T_STEP, SAFE5V), (t_end, vc)],
            "discharge": [(0.0, 0)], "fw": [(0.0, 0)],
            "pg": [(0.0, 0), (t_end + PG_DELAY, 0), (t_end + PG_DELAY, 1)],
            "tstop": T_STEP + window, "judge_from": T_STEP - 1e-3,
            "fw_off": True})
    out.append({"name": "fw_on", "src": [(0.0, vc)], "discharge": [(0.0, 0)],
                "fw": [(0.0, 0), (T_ON, 0), (T_ON, 1)], "pg": [(0.0, 1)],
                "tstop": T_ON + window, "judge_from": T_ON, "fw_off": False})
    t_hr = T_ON + window
    t_zero = t_hr + vc / RESET_SLEW * 1e-3
    t_back = t_zero + recover
    t_5 = t_back + SAFE5V / ATTACH_SLEW * 1e-3
    t_step = t_5 + 100e-3
    t_vc = t_step + (vc - SAFE5V) / RESET_SLEW * 1e-3
    out.append({
        "name": "hard_reset",
        "src": [(0.0, vc), (t_hr, vc), (t_zero, 0.0), (t_back, 0.0),
                (t_5, SAFE5V), (t_step, SAFE5V), (t_vc, vc)],
        "discharge": [(0.0, 0), (t_hr, 0), (t_hr, 1), (t_back, 1),
                      (t_back, 0)],
        "fw": [(0.0, 0), (T_ON, 0), (T_ON, 1)],
        "pg": [(0.0, 1), (t_hr, 1), (t_hr, 0), (t_vc + PG_DELAY, 0),
               (t_vc + PG_DELAY, 1)],
        "tstop": t_vc + 200e-3, "judge_from": t_hr, "fw_off": False})
    return out


def source_lines(node: str, sc: dict) -> list[str]:
    """The source, cable and its sense for one scenario at `node`."""
    return [f"VPD pd_src 0 {_pwl(_edges(sc['src']))}",
            "DPD pd_src pd_out D_PDSRC",
            ".model D_PDSRC D(IS=1e-6 N=0.05 RS=1m)",
            f"RPDCAB pd_out pd_cab {CABLE_R:g}",
            f"LPDCAB pd_cab {node} {CABLE_L:g}",
            f"VPDDC pd_dc 0 {drive_pwl(sc['discharge'], 1.0)}",
            "SPDDC pd_out pd_bleed pd_dc 0 SW_PDDC",
            f"RPDDC pd_bleed 0 {DISCHARGE_OHMS:g}",
            ".model SW_PDDC SW(Ron=1m Roff=1e9 Vt=0.5 Vh=0.1)"]


def drive_pwl(points: list[tuple[float, float]], v_on: float,
              v_off: float = 0.0, edge: float = 10e-6) -> str:
    """A 0/1 schedule as a PWL between v_off and v_on with `edge` ramps."""
    return _pwl(_edges([(t, v_on if s else v_off) for t, s in points],
                       edge))


def _edges(points: list[tuple[float, float]], edge: float = 10e-6
           ) -> list[tuple[float, float]]:
    """A PWL whose time must strictly increase: a jump at the same instant
    becomes a ramp of `edge`."""
    out: list[tuple[float, float]] = []
    for t, v in points:
        if out and t <= out[-1][0]:
            t = out[-1][0] + edge
        out.append((t, v))
    return out
