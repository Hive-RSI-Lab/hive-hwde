"""Mutant: pair-understated (blinky2).

SIDECAR fault (copper is the golden's, copied unchanged): the +5V input is
declared a 100 V net in `voltages`, as in hv-rail-spacing, but a
`voltage_pairs` entry then declares +5V-to-GND at 24 V with no `why` (#651).
The pair overrides the derived 100 V difference and, being <= 30 V, waives
the sweep outright, so the golden's 0.50 mm +5V-to-GND gap at J1 pad 1
(IPC-2221 wants 0.60 mm at 100 V) passes silently. Must be caught by
check_creepage as kind "creepage_pair_understated" (a warning on the
constraints entry, not on the copper).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib


def surgery(text):
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["voltages"] = [{"net": "+5V", "voltage": 100}]
    cons["voltage_pairs"] = [{"a": "+5V", "b": "GND", "voltage": 24}]
    return text, {"sidecars": {"constraints.json": mutlib.dump_json(cons)},
                  "net": "+5V", "voltage": 100, "pair_voltage": 24}


if __name__ == "__main__":
    sys.exit(mutlib.run("pair-understated", "blinky2", surgery))
