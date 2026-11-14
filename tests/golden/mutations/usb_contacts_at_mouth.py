"""Mutant: usb-contacts-at-mouth (usbbuck4).

The hit (laptop hub J6, #911/#952): a Molex 104031 microSD has its signal
tails at the card's mouth, the opposite of what the pad rule reads, so the
socket shipped facing into the board. Here J1's drawing (its extraction,
parts/C9900002.json, `mechanical.entry.contacts_at`) puts its contacts at
the mouth, so the receptacle as placed faces into the board (+x). Copper
is untouched; J1 gains its LCSC property. Must be caught by check_mating
(kind "mating_faces_inward", ref J1), reading the mouth from the
extraction rather than the pads.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

LCSC = "C9900002"
EXTRACTION = {
    "mpn": "USB-MICRO-INNER-LEAD", "lcsc": LCSC,
    "pinout": [{"pin": str(n), "name": f"P{n}", "type": "passive"}
               for n in range(1, 6)],
    "mechanical": {"entry": {"contacts_at": "mouth",
                             "source": "planted fault"}},
}


def surgery(text):
    s, e = mutlib.footprint_block(text, "J1")
    block = mutlib.replace_once(
        text[s:e], "(at 103.4 131.5 -90)",
        f'(at 103.4 131.5 -90)\n\t\t(property "LCSC" "{LCSC}"\n'
        '\t\t\t(at 0 0 -90)\n\t\t\t(layer "F.Fab")\n\t\t\t(hide yes)\n'
        '\t\t\t(uuid "9b1e0c7a-5d2f-4c61-9a0e-f00000000952")\n\t\t)',
        "J1 LCSC")
    return text[:s] + block + text[e:], {
        "ref": "J1", "lcsc": LCSC, "mouth": "+x (pads read -x)",
        "sidecars": {f"parts/{LCSC}.json": mutlib.dump_json(EXTRACTION)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("usb-contacts-at-mouth", "usbbuck4", surgery))
