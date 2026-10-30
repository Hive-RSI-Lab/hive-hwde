"""core: the design as data. One dict per part (see build.py).

Pins map by number, or by symbol pin NAME with an 'n:' prefix. Ultra-only
parts carry dnp=True (the board's default build is Plus); Plus-only parts
carry plus_only=True (left off in the Ultra BOM variant).
"""
from __future__ import annotations

RAILS = ["GND", "VBUS_IN", "VBUS", "PVDD", "+5V", "+5VA", "+3V3", "+3V3A",
         "+3V3E"]

SHEETS = [
    ("power_in", ("A3", 420.0)),
    ("rails", ("A3", 420.0)),
    ("mcu", ("A2", 594.0)),
    ("ethernet", ("A3", 420.0)),
    ("dac", ("A2", 594.0)),
    ("linein", ("A2", 594.0)),
    ("amp_a", ("A2", 594.0)),
    ("amp_b", ("A2", 594.0)),
    ("ui", ("A3", 420.0)),
]
SHEET_TITLES = {
    "power_in": "USB-C PD input, current sense, PVDD switch",
    "rails": "5 V buck, 3.3 V buck, analog LDO",
    "mcu": "ESP32-WROVER-E, USB-UART, I/O expander",
    "ethernet": "LAN8720A 100 Mb Ethernet",
    "dac": "ES9018K2M Sabre mains DAC, PCM5102A sub DAC",
    "linein": "RCA line in, ES7243E ADC, DJ-direct switch, TOSLINK",
    "amp_a": "TPA3221 zone A",
    "amp_b": "TPA3221 zone B (Ultra)",
    "ui": "knob, buttons, IR, status LED, front-board FFC",
}

# (kind, value, package) -> (lcsc, basic, footprint, mpn)
PASSIVES = {
    ("R", "10k", "0402"): ("C25744", True),
    ("R", "100k", "0402"): ("C25741", True),
    ("R", "1k", "0402"): ("C11702", True),
    ("R", "2.2k", "0402"): ("C25879", True),
    ("R", "47k", "0402"): ("C25792", True),
    ("R", "20k", "0402"): ("C25765", True),
    ("R", "22k", "0402"): ("C25768", True),
    ("R", "100R", "0402"): ("C25076", True),
    ("R", "10R", "0402"): ("C25077", True),
    ("R", "33R", "0402"): ("C25105", True),
    ("R", "470R", "0402"): ("C25117", True),
    ("R", "1.5k", "0402"): ("C25867", True),
    ("R", "2k", "0402"): ("C4109", True),
    ("R", "12k", "0402"): ("C25752", True),
    ("R", "49.9R", "0402"): ("C25120", True),
    ("R", "200k", "0402"): ("C25764", True),
    ("R", "51k", "0402"): ("C25794", True),
    ("R", "15k", "0402"): ("C25756", True),
    ("R", "4.7k", "0402"): ("C25900", True),
    ("R", "330R", "0402"): ("C25104", True),
    ("R", "210k", "0402"): ("C2998176", False),
    ("R", "75k", "0402"): ("C25798", False),
    ("R", "4.7R", "0603"): ("C23164", True),
    ("R", "10m", "2512"): ("C393070", False),
    ("C", "100nF", "0402"): ("C1525", True),
    ("C", "100nF/50V", "0402"): ("C307331", True),
    ("C", "1uF", "0402"): ("C52923", True),
    ("C", "10nF", "0402"): ("C15195", True),
    ("C", "1nF", "0402"): ("C1523", True),
    ("C", "22nF", "0402"): ("C1532", True),
    ("C", "2.2uF", "0402"): ("C12530", True),
    ("C", "10uF", "0603"): ("C19702", True),
    ("C", "4.7uF", "0603"): ("C19666", True),
    ("C", "2.2uF", "0603"): ("C23630", True),
    ("C", "33nF/50V", "0603"): ("C21117", True),
    ("C", "100nF/50V", "0603"): ("C14663", True),
    ("C", "1nF/50V", "0603"): ("C1588", True),
    ("C", "22uF", "0805"): ("C45783", True),
    ("C", "1uF/50V", "0805"): ("C28323", True),
    ("C", "10uF/25V", "0805"): ("C15850", True),
    ("C", "4.7uF/25V", "0805"): ("C1779", True),
    ("C", "10uF/50V", "1206"): ("C13585", True),
    ("FB", "120R", "0603"): ("C14709", True),
    # review-2 (2026-10-29)
    ("R", "470k", "0402"): ("C25790", True),
    ("R", "1.3k", "0402"): ("C25866", True),
    ("R", "150k", "0603"): ("C22807", True),
    ("C", "470pF", "0603"): ("C1620", True),
    ("C", "2.2uF/50V", "0805"): ("C377773", True),
}
FP = {"R0402": "aiee:R0402", "R0603": "aiee:R0603", "R2512": "aiee:R2512",
      "C0402": "aiee:C0402", "C0603": "aiee:C0603", "C0805": "aiee:C0805",
      "C1206": "aiee:C1206", "FB0603": "aiee:L0603"}

_parts: list[dict] = []
DECOUPLING: list[dict] = []
_sheet = ["power_in"]
_dnp = [False]


def sheet(name, dnp=False):
    _sheet[0] = name
    _dnp[0] = dnp


def P(ref, sym, value, fp, pins, lcsc="", basic=False, mpn="", dnp=None,
      plus_only=False, role="", nc_rest=False):
    _parts.append({"ref": ref, "sym": sym, "value": value, "fp": fp,
                   "pins": pins, "lcsc": lcsc, "basic": basic, "mpn": mpn,
                   "dnp": _dnp[0] if dnp is None else dnp,
                   "plus_only": plus_only, "role": role, "nc_rest": nc_rest,
                   "sheet": _sheet[0]})


def _passive(kind, ref, value, pkg, a, b, **kw):
    lcsc, basic = PASSIVES[(kind, value, pkg)]
    sym = {"R": "Device:R", "C": "Device:C", "FB": "Device:FerriteBead"}[kind]
    fpkey = {"R": "R", "C": "C", "FB": "FB"}[kind] + pkg
    P(ref, sym, value.split("/")[0], FP[fpkey], {"1": a, "2": b},
      lcsc=lcsc, basic=basic, **kw)


def R(ref, value, a, b, pkg="0402", **kw):
    _passive("R", ref, value, pkg, a, b, **kw)


def C(ref, value, a, b="GND", pkg="0402", ic=None, pin=None, **kw):
    _passive("C", ref, value, pkg, a, b, **kw)
    if ic:
        DECOUPLING.append({"cap": ref, "ic": ic, "pin": str(pin), "rail": a,
                           "value": value.split("/")[0]})


def FB(ref, a, b, **kw):
    _passive("FB", ref, "120R", "0603", a, b, **kw)


def parts() -> list[dict]:
    if not _parts:
        import blocks  # noqa: F401  (fills _parts)
    return _parts
