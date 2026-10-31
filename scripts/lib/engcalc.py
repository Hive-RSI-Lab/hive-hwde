"""engcalc.py - deterministic engineering calculations (no engine, no board).

The hand calculations a reviewer does on a schematic, as small pure
functions, so sim_board.py and eng_calc.py give the same number every time
and a test pins each one to a worked example:

  parse_value         "4k7" -> 4700, "2.2uF" -> 2.2e-6, "100R" -> 100,
                      "10uH 8A" -> 1e-5 (first token); "1M" is mega here
  package_code        footprint -> imperial chip size ("0603"), or None
  resistor_rating_w   the usual thick-film rating for a chip size (P70)
  resistor_umax_v     a chip size's limiting element voltage (Umax)
  resistor_derate     P(T)/P70: flat to 70 C, linear to 0 at 155 C
  fuse_rerate         a fuse's current rerate k_T at an ambient
  pptc_hold_factor    a PPTC's Ihold(T)/Ihold(23 C)
  parse_amps          '500mA' -> 0.5, '1.1A' -> 1.1
  parse_temp_range    '-40℃~+85℃' -> (-40, 85, False); '@(Tj)' -> True
  divider_out         Vout of a two-resistor divider
  fb_vout             a regulator's Vout from its FB divider and Vref,
                      with the worst-case spread over R and Vref tolerance
  rc_corner_hz        1 / (2 pi R C)
  rc_cross_s          time for an RC step to reach a fraction of its swing
  i2c_pullup          Rp min (sink current) / max (rise time) and tr
  crystal_load_pf     the load a crystal sees from its two caps + stray
  led_vf              a forward voltage by colour, when no datasheet says
  ldo_tj              an LDO's dissipation and junction temperature
  holdup_s            how long a bulk cap holds a rail above a floor

Every default here (package ratings, LED Vf by colour, I2C pin
capacitance, crystal stray) is a rule of thumb, not a datasheet: callers
report which inputs were assumed so a review can see what it doesn't know.
"""
from __future__ import annotations

import math
import re

# ------------------------------------------------------------------ values

_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
       "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "R": 1.0,
       "r": 1.0, "Ω": 1.0}
_VAL = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(meg|MEG|Meg|[pnuµμmkKMGRrΩ])?(\d+)?"
    r"\s*(?:[FHfh]|ohms?|Ohms?|Ω)?$")


def parse_value(text) -> float | None:
    """A component value as a number in base units, or None.

    The first token is read: "10uH 8A" -> 1e-5, "470uF/50V" -> 4.7e-4. The SI letter may
    sit in the decimal point's place ("4k7" = 4.7k, "2R2" = 2.2). "M" is mega
    and "m" milli, as on a schematic (SPICE's own "M" = milli trap is
    simlib's to handle). "0R" is zero ohms."""
    if text is None:
        return None
    tok = re.split(r"[\s/,;]+", str(text).strip())
    if not tok or not tok[0]:
        return None
    m = _VAL.match(tok[0])
    if not m:
        return None
    num, unit, frac = m.group(1), m.group(2), m.group(3)
    if frac:
        if "." in num or not unit:
            return None
        num = f"{num}.{frac}"
    mult = 1e6 if unit and unit.lower() == "meg" else _SI.get(unit or "", 1.0)
    return float(num) * mult


_VOLT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*V(?![a-zA-Z])")


def parse_volts(text) -> float | None:
    """A voltage written in free text ("10uF 25V", "50V") -> 25.0, or None."""
    m = _VOLT.search(str(text or ""))
    return float(m.group(1)) if m else None


_WATT = re.compile(r"(\d+(?:\.\d+)?)\s*(m?)W\b")


def parse_watts(text) -> float | None:
    """'100mW' -> 0.1, '1/4W' -> 0.25, '0.125W' -> 0.125, else None."""
    s = str(text or "")
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*W\b", s)
    if m and float(m.group(2)):
        return float(m.group(1)) / float(m.group(2))
    m = _WATT.search(s)
    if not m:
        return None
    return float(m.group(1)) * (1e-3 if m.group(2) else 1.0)


def parse_tolerance(text) -> float | None:
    """'+-1%' / '±1%' / '1%' -> 0.01, else None."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(text or ""))
    return float(m.group(1)) / 100.0 if m else None


# ---------------------------------------------------------------- packages

_METRIC = {"0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805",
           "3216": "1206", "3225": "1210", "5025": "2010", "6332": "2512"}
# Common thick-film chip resistor ratings at 70 C (Yageo RC / UniOhm series);
# a part's own datasheet or parts.json "Power(Watts)" wins over these.
RESISTOR_RATING_W = {"01005": 0.031, "0201": 0.05, "0402": 0.0625,
                     "0603": 0.1, "0805": 0.125, "1206": 0.25,
                     "1210": 0.5, "2010": 0.75, "2512": 1.0}


def package_code(footprint: str | None) -> str | None:
    """The imperial chip size a footprint name carries, or None.
    'Resistor_SMD:R_0603_1608Metric' -> '0603'; 'aiee:R0402' -> '0402';
    an EasyEDA 'R0603' or a metric '1608Metric' alone also reads."""
    fp = str(footprint or "")
    name = fp.split(":")[-1]
    m = re.search(r"(\d{4})Metric", name)
    if m and m.group(1) in _METRIC:
        return _METRIC[m.group(1)]
    m = re.search(r"(?<!\d)(01005|0201|0402|0603|0805|1206|1210|2010|2512)"
                  r"(?!\d)", name)
    return m.group(1) if m else None


def resistor_rating_w(footprint: str | None) -> float | None:
    code = package_code(footprint)
    return RESISTOR_RATING_W.get(code) if code else None


# Limiting element voltage (Umax, max continuous working voltage) per chip
# size: Vishay D/CRCW e3 (doc 20035) for 0402-2512; 0201 and 01005 from
# Yageo RC (25 V, 15 V), which D/CRCW does not list. A resistor's voltage is
# min(sqrt(P*R), Umax): above the critical resistance Umax^2/P70 (56 kOhm
# for an 0603) voltage limits the part before power does
# (docs/ingest/safety-components.md R-RAT-1).
RESISTOR_UMAX_V = {"01005": 15.0, "0201": 25.0, "0402": 50.0, "0603": 75.0,
                   "0805": 150.0, "1206": 200.0, "1210": 200.0,
                   "2010": 200.0, "2512": 500.0}


def resistor_umax_v(footprint: str | None) -> float | None:
    code = package_code(footprint)
    return RESISTOR_UMAX_V.get(code) if code else None


def resistor_derate(t_amb: float | None) -> float:
    """Thick-film rated power at an ambient as a fraction of P70: full to
    70 C, falling linearly to 0 at 155 C (D/CRCW e3 derating chart,
    R-RAT-2). 85 C -> 0.82."""
    if t_amb is None or t_amb <= 70.0:
        return 1.0
    return max(0.0, (155.0 - t_amb) / 85.0)


def _interp(table: list[tuple[float, float]], t: float) -> float:
    if t <= table[0][0]:
        return table[0][1]
    for (t0, k0), (t1, k1) in zip(table, table[1:]):
        if t <= t1:
            return k0 + (k1 - k0) * (t - t0) / (t1 - t0)
    return table[-1][1]


# A fuse's current rerate with ambient: Littelfuse 881 / chip-fuse curves
# (0.90 at 70 C); no credit below 25 C. On top of it the fuse runs at no more
# than 75 % of its rating (Littelfuse Fuseology guide; R-RAT-3).
FUSE_RERATE = [(25.0, 1.0), (70.0, 0.90), (85.0, 0.85), (100.0, 0.80),
               (125.0, 0.70)]
FUSE_LOAD_FRACTION = 0.75
# A PPTC's hold current against ambient, as a fraction of Ihold at 23 C: the
# typical table of Bourns Multifuse MF-MSMF/MF-NSMF and Littelfuse
# PolySwitch chip devices (S8, S9). The part's own table wins; Ihold(T)
# should clear the operating current by 10 % (R-RAT-4).
PPTC_HOLD = [(-40.0, 1.48), (-20.0, 1.33), (0.0, 1.17), (23.0, 1.0),
             (40.0, 0.87), (50.0, 0.80), (60.0, 0.73), (70.0, 0.68),
             (85.0, 0.56)]
PPTC_MARGIN = 1.1


def fuse_rerate(t_amb: float | None) -> float:
    return 1.0 if t_amb is None else _interp(FUSE_RERATE, t_amb)


def pptc_hold_factor(t_amb: float | None) -> float:
    return 1.0 if t_amb is None else _interp(PPTC_HOLD, t_amb)


_AMP = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(m|u|µ)?A(?![a-zA-Z])")


def parse_amps(text) -> float | None:
    """'500mA' -> 0.5, '1.1A' -> 1.1, '750 mA' -> 0.75, else None."""
    m = _AMP.search(str(text or ""))
    if not m:
        return None
    return float(m.group(1)) * {"m": 1e-3, "u": 1e-6, "µ": 1e-6}.get(
        m.group(2) or "", 1.0)


_TRANGE = re.compile(r"([+-]?\s*\d+(?:\.\d+)?)\s*(?:℃|°\s*C|C)?\s*(?:~|to|\.\.|"
                     r"\s-\s|-(?=\s*[+-]?\d))\s*([+-]?\s*\d+(?:\.\d+)?)"
                     r"\s*(?:℃|°\s*C|C)", re.I)


def parse_temp_range(text) -> tuple[float, float, bool] | None:
    """An operating temperature range as LCSC writes it: '-40℃~+85℃' ->
    (-40.0, 85.0, False); '-40℃~+125℃@(TJ)' -> (-40.0, 125.0, True), the
    flag saying the bound is the junction's alone, not the air's ('@(TA/TJ)'
    binds the air too). None when the
    text names no range."""
    s = str(text or "")
    m = _TRANGE.search(s)
    if not m:
        return None
    lo, hi = (float(g.replace(" ", "")) for g in m.groups())
    if lo > hi:
        return None
    tj = bool(re.search(r"T\s*J\b|junction", s, re.I)) and \
        not re.search(r"T\s*A\b|ambient", s, re.I)
    return lo, hi, tj


# ---------------------------------------------------------------- circuits

def divider_out(vin: float, r_top: float, r_bot: float) -> float:
    return vin * r_bot / (r_top + r_bot)


def fb_vout(vref: float, r_top: float, r_bot: float, r_tol: float = 0.01,
            vref_tol: float = 0.02) -> dict:
    """Vout = Vref (1 + Rtop/Rbot), and its worst-case corners: Vref and
    both resistors each at the tolerance edge that pushes Vout out."""
    nom = vref * (1 + r_top / r_bot)
    lo = vref * (1 - vref_tol) * (1 + r_top * (1 - r_tol) / (r_bot * (1 + r_tol)))
    hi = vref * (1 + vref_tol) * (1 + r_top * (1 + r_tol) / (r_bot * (1 - r_tol)))
    return {"nom": nom, "min": lo, "max": hi}


def rc_corner_hz(r: float, c: float) -> float:
    return 1.0 / (2 * math.pi * r * c)


def rc_cross_s(tau: float, fraction: float) -> float:
    """Time for a first-order RC step to cover `fraction` of its swing."""
    return -tau * math.log(1.0 - fraction)


# I2C (NXP UM10204 rev 7, 7.1): Rp(min) = (VDD - VOL) / IOL with VOL 0.4 V
# at IOL 3 mA (Sm/Fm) or 20 mA (Fm+); Rp(max) = tr / (0.8473 Cb), the 30-70%
# rise of an RC; tr max 1000 ns (Sm 100 kHz), 300 ns (Fm 400 kHz), 120 ns
# (Fm+ 1 MHz); Cb max 400 pF (Sm/Fm), 550 pF (Fm+).
I2C_MODES = {100: {"tr_ns": 1000.0, "iol_a": 3e-3, "cb_max_pf": 400.0},
             400: {"tr_ns": 300.0, "iol_a": 3e-3, "cb_max_pf": 400.0},
             1000: {"tr_ns": 120.0, "iol_a": 20e-3, "cb_max_pf": 550.0}}
I2C_PIN_PF = 10.0     # UM10204 Ci max per I/O pin
I2C_TRACE_PF = 10.0   # a short on-board trace, a guess


def i2c_pullup(vdd: float, rp: float, cb_pf: float, khz: int = 400) -> dict:
    mode = I2C_MODES.get(khz) or I2C_MODES[400]
    rmin = (vdd - 0.4) / mode["iol_a"]
    rmax = mode["tr_ns"] * 1e-9 / (0.8473 * cb_pf * 1e-12)
    tr_ns = 0.8473 * rp * cb_pf * 1e-12 * 1e9
    return {"rmin": rmin, "rmax": rmax, "tr_ns": tr_ns,
            "tr_max_ns": mode["tr_ns"], "sink_ma": (vdd - 0.4) / rp * 1e3,
            "ok": rmin <= rp <= rmax}


CRYSTAL_STRAY_PF = 4.0  # pin + trace, the usual 2-5 pF guess


def crystal_load_pf(c1_pf: float, c2_pf: float,
                    stray_pf: float = CRYSTAL_STRAY_PF) -> float:
    return c1_pf * c2_pf / (c1_pf + c2_pf) + stray_pf


# LED forward voltage by colour at a few mA, when no datasheet figure is
# known (typical InGaN for green/blue/white, AlInGaP for red..yellow).
LED_VF = {"ir": 1.3, "red": 1.9, "orange": 2.0, "amber": 2.0, "yellow": 2.0,
          "yellow-green": 2.1, "green": 2.9, "blue": 3.0, "white": 3.0,
          "uv": 3.3}


def led_color(*texts) -> str | None:
    s = " ".join(str(t or "") for t in texts).lower()
    if "yellow-green" in s or "yellowgreen" in s:
        return "yellow-green"
    for c in ("white", "blue", "green", "yellow", "amber", "orange", "red",
              "uv", "ir"):
        if re.search(rf"(?<![a-z]){c}(?![a-z])", s):
            return c
    return None


def led_vf(color: str | None) -> float:
    return LED_VF.get(color or "", 2.0)


def ldo_tj(vin: float, vout: float, iout: float, theta_ja: float,
           t_amb: float = 25.0, iq: float = 0.0) -> dict:
    pd = (vin - vout) * iout + vin * iq
    return {"pd_w": pd, "tj_c": t_amb + pd * theta_ja}


def holdup_s(c: float, v_start: float, v_min: float, i_load: float) -> float:
    """Time a capacitor alone holds a constant-current load above v_min."""
    return c * (v_start - v_min) / i_load
