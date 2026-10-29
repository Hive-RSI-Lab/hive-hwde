"""engcalc.py - deterministic engineering calculations (no engine, no board).

The hand calculations a reviewer does on a schematic, as small pure
functions, so sim_board.py and eng_calc.py give the same number every time
and a test pins each one to a worked example:

  parse_value         "4k7" -> 4700, "2.2uF" -> 2.2e-6, "100R" -> 100,
                      "10uH 8A" -> 1e-5 (first token); "1M" is mega here
  package_code        footprint -> imperial chip size ("0603"), or None
  resistor_rating_w   the usual thick-film rating for a chip size
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
