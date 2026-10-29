"""Mutant: mech-06-unkeyed-rails (blinky2).

PLACEMENT fault (MECH-06, identical unkeyed connectors carrying different
rails, as on PCB-0011-A). A second 2-pin header J3, the same
PinHeader_1x02 footprint as the +5V input J1, is added in the free corner at
the bottom left for an AUX signal: its pin 1 carries /AUX where J1's pin 1
carries +5V, and its pin 2 is GND like J1's (it joins the GND pours). A plug
made for J3 fits J1 and meets +5V. /AUX is on J3's pin alone, so no new
connection is left unrouted. Must be caught by check_mate_pins (kind
"mate_unkeyed_rail_swap", ref J1, at J1 pin 1).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                     r"[0-9a-f]{12}")


def surgery(text):
    s, e = mutlib.footprint_block(text, "J1")
    block = text[s:e]
    seen: dict[str, str] = {}

    def fresh(m):
        return seen.setdefault(
            m.group(0), f"00000000-0000-4000-8000-0000000606{len(seen):02d}")

    block = UUID_RE.sub(fresh, block)
    block = mutlib.replace_once(block, "(at 102.8 103.5 90)",
                                "(at 102.8 131.0 90)", "move J3")
    block = mutlib.replace_once(block, '(property "Reference" "J1"',
                                '(property "Reference" "J3"', "name J3")
    block = mutlib.replace_once(block, '(property "Value" "PWR_5V"',
                                '(property "Value" "AUX"', "value J3")
    block = mutlib.replace_once(block, '(net "+5V")', '(net "/AUX")',
                                "J3 pin 1 net")
    # after the last footprint, so every other item keeps its place
    e_last = mutlib.footprint_block(text, _last_ref(text))[1]
    text = text[:e_last] + block + text[e_last:]
    return text, {"ref": "J3", "copy_of": "J1", "at": [102.8, 131.0],
                  "pad_nets": {"1": "/AUX", "2": "GND"}}


def _last_ref(text):
    i = text.rfind("\n\t(footprint")
    m = re.search(r'\(property "Reference" "([^"]+)"', text[i:])
    return m.group(1)


if __name__ == "__main__":
    sys.exit(mutlib.run("mech-06-unkeyed-rails", "blinky2", surgery))
