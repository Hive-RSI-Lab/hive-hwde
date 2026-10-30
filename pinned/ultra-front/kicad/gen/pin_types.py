"""Give the pulled symbols real pin electrical types.

EasyEDA symbols come in with every pin `unspecified`, which KiCad ERC
reports as a pin_to_pin warning on every net. lib_pin_types.py does this from
datasheet-extract JSON; this board has only two ICs, so the types are set
here from their datasheets (TCA9535: INT open-drain, SCL in, SDA bidir, A0-2
in, P bidir, VCC/GND power; IRM-H638T: OUT open-collector with internal
pull-up, VCC/GND power). Every other pulled symbol (connectors, switches,
LED, MOSFET) is passive. Idempotent.

  python3 kicad/gen/pin_types.py      (from the board workspace)
"""
import json
import re
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "lib" / "aiee.kicad_sym"
TYPES = {
    "TCA9535PWR": {"~{INT}": "open_collector", "SCL": "input",
                   "SDA": "bidirectional", "A0": "input", "A1": "input",
                   "A2": "input", "VCC": "power_in", "GND": "power_in",
                   "P*": "bidirectional"},
    "IRM-H638T_TR2": {"VOUT": "open_collector", "VCC": "power_in",
                      "GND": "power_in"},
}
SYM = re.compile(r'^  \(symbol "([^"]+)"', re.M)
PIN = re.compile(r'\(pin (\w+) (\w+)(\s+\(at [^\n]*\n\s+\(length [^\n]*\n\s+\(name "([^"]*)")')


def main():
    t = LIB.read_text()
    starts = [(m.start(), m[1]) for m in SYM.finditer(t)] + [(len(t), None)]
    out, n = [t[:starts[0][0]]], 0
    for (s, name), (e, _) in zip(starts, starts[1:]):
        body = t[s:e]
        table = TYPES.get(name, {})

        def sub(m):
            nonlocal n
            pin = m[4]
            new = table.get(pin) or (table.get("P*") if pin.startswith("P") and table else None) \
                or ("passive" if not table else m[1])
            if new != m[1]:
                n += 1
            return f"(pin {new} {m[2]}{m[3]}"
        out.append(PIN.sub(sub, body))
    LIB.write_text("".join(out))
    print(json.dumps({"script": "pin_types", "status": "pass", "retyped": n}))


if __name__ == "__main__":
    main()
