"""Does every logic signal that crosses supply domains reach a valid level,
and stay inside the receiver's absolute maximum?

A 3.3 V MCU driving a part supplied from 5 V reaches 3.3 V, where a CMOS
input wants 0.7 x 5 = 3.5 V; a 5 V part driving a 3.3 V MCU pushes its pin
past VDD + 0.3. Both pass ERC (the pins are passive) and every layout gate,
and show only at bring-up (hifi-receiver-2 review-2: a TS5A23157 select at
5 V from 3.3 V logic, "none" in first-spin.md's table of catches).

A net is a crossing when it joins an MCU GPIO, or two digital-named IC
pins, of ICs whose logic supplies differ. Each IC's logic supply is its
first supply pin in doubtlib.SUPPLY_ORDER with a known rail; a pull-up on
the net to a rail sets the high level the same way. Thresholds come from
reference/doubts/logic_levels.yaml; a part with no row there is read as
plain CMOS and the answer is unknown until its datasheet row is added.
"""
import re
from pathlib import Path

import yaml

import doubtlib

QUESTION = ("Does every logic signal crossing supply domains reach a valid "
            "high level, and stay inside the receiver's absolute maximum?")
DOMAIN = "digital"
WHY = "a select or enable that never reaches VIH, or an input pushed past VDD"

TABLE = Path(doubtlib.HERE).parents[1] / "reference" / "doubts" / \
    "logic_levels.yaml"
MCU_PIN_RE = re.compile(r"^(IO\d+|P[A-K]\d+|P\d_?\d+|RA\d|RB\d|RC\d|GP\d+|"
                        r"GP[AB]\d|D\d+|TXD\d*|RXD\d*|U\dTXD|U\dRXD)$")
DIGITAL_RE = re.compile(
    r"^N?(SDA|SCL|SCK|SCLK|MOSI|MISO|SDI|SDO|DIN|DOUT|DATA|CS|SS|CE|EN|"
    r"SEL|IN\d|S\d|TX|RX|TXD|RXD|RST|RESET|INT|IRQ|MUTE|SMUTE|CMUTE|SHDN|"
    r"SD|SDZ|STBY|PDN|FAULT|CLIP|OTW|OTW_CLIP|BCK|BCLK|LRCK|LRCLK|WS|MCLK|"
    r"SCKI|FMT|DEMP|OE|CLK|PG|PGOOD|A\d|ADDR\d*)$")
# MCUs and I/O expanders: their GPIO pins drive whatever the firmware says.
MCU_VALUE_RE = re.compile(r"ESP32|STM32|RP2040|ATMEGA|ATTINY|NRF5|SAMD|"
                          r"CH32|GD32|PIC\d|TCA95|PCA95|PCAL95|PCF857|"
                          r"MCP230|AW95|XL95", re.I)


def _rows():
    if not TABLE.is_file():
        return []
    data = yaml.safe_load(TABLE.read_text()) or {}
    rows = []
    for row in data.get("parts") or []:
        rows.append(dict(row, _re=re.compile(row["match"], re.I)))
    return rows


def _row(board, ref, rows):
    for key in (board.mpn(ref), board.value(ref)):
        for row in rows:
            if key and row["_re"].search(key):
                return row
    return None


def _vih(row, vdd):
    v = (row or {}).get("vih")
    if v is None:
        return 0.7 * vdd, False
    if isinstance(v, str):
        m = re.match(r"^\s*([\d.]+)\s*\*\s*V\w*\s*$", v)
        return float(m.group(1)) * vdd, True
    return float(v), True


def _vin_max(row, vdd):
    v = (row or {}).get("vin_max")
    return (float(v), True) if v is not None else (vdd + 0.3, False)


def crossings(board):
    """[(net label, [(ref, pin, name, vdd)], pull_v)] for each net whose
    logic pins sit on more than one supply voltage."""
    out, seen = [], set()
    for net in board.nets:
        g = board.group(net)
        if g in seen or board.rail(net) is not None:
            continue
        seen.add(g)
        ends, pull = [], None
        for ref, pin in board.nodes(net):
            name = board.pins[ref][pin]["name"]
            if doubtlib.prefix(ref) == "R":
                other = [p["net"] for q, p in board.pins[ref].items()
                         if q != pin]
                v = board.rail(other[0]) if other else None
                if v:
                    pull = max(pull or 0, v)
                continue
            if doubtlib.prefix(ref) not in ("U", "IC"):
                continue
            mcu = MCU_VALUE_RE.search(board.mpn(ref)) and MCU_PIN_RE.match(name)
            if not (mcu or DIGITAL_RE.match(name)):
                continue
            sup = board.supply(ref)
            if sup:
                ends.append((ref, pin, name, sup[1], bool(mcu)))
        if len({e[3] for e in ends}) > 1 and (
                any(e[4] for e in ends) or len(ends) > 1):
            out.append((board.label(net), ends, pull))
    return out


def applies(board):
    return bool(crossings(board))


def _role(row, name):
    """in, out, od (open drain) or io (unknown: both) from the part's row."""
    for key, role in (("open_drain", "od"), ("outputs", "out"),
                      ("inputs", "in")):
        if name in ((row or {}).get(key) or []):
            return role
    return "io"


def answer(board):
    rows = _rows()
    fails, unknowns, ev = [], [], []
    for label, ends, pull in crossings(board):
        roles = [_role(_row(board, e[0], rows), e[2]) for e in ends]
        for i, (ref, pin, name, vdd, _) in enumerate(ends):
            if roles[i] not in ("in", "io"):
                continue
            row = _row(board, ref, rows)
            where = f"{label}: {ref} ({board.mpn(ref)}) {name} on {vdd} V"
            # what can drive this pin high: the other push-pull ends at
            # their own supply, and the pull-up
            levels = [(e[3], f"{e[0]} {e[2]}") for j, e in enumerate(ends)
                      if j != i and roles[j] in ("out", "io")]
            if pull:
                levels.append((pull, "the pull-up"))
            if not levels:
                continue
            vih, vih_cited = _vih(row, vdd)
            vmax, max_cited = _vin_max(row, vdd)
            lo, lo_by = min(levels)
            hi, hi_by = max(levels)
            if lo >= vih:
                ev.append(f"{where}: high {lo} V from {lo_by} >= VIH "
                          f"{vih:.2f} V ({'cited' if vih_cited else 'CMOS 0.7 VDD'})")
            elif vih_cited:
                fails.append({"refs": [ref], "nets": [label],
                              "msg": f"{where}: {lo_by} drives it to {lo} V, "
                                     f"VIH {vih:.2f} V ({row.get('source')})"})
            else:
                unknowns.append(
                    f"{where} is driven to {lo} V by {lo_by}, below a CMOS VIH "
                    f"of {vih:.2f} V: its datasheet VIH (logic_levels.yaml), "
                    "or a level shifter")
            if hi <= vmax:
                ev.append(f"{where}: sees at most {hi} V <= {vmax:.2f} V")
            elif max_cited:
                fails.append({"refs": [ref], "nets": [label],
                              "msg": f"{where}: {hi_by} drives it to {hi} V, "
                                     f"input max {vmax} V ({row.get('source')})"})
            else:
                unknowns.append(
                    f"{where} can see {hi} V from {hi_by}, past VDD + 0.3 = "
                    f"{vmax:.2f} V: is the pin 5 V tolerant, or is "
                    f"{hi_by} never a push-pull output? (logic_levels.yaml "
                    "vin_max or the driver's outputs/open_drain, with sources)")
    return doubtlib.combine(fails, unknowns, ev)
