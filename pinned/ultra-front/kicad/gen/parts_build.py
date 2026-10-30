"""Build parts/parts.json for ultra-front from saved parts_search results
(parts/search/*.json, written by parts_search.py --out). One line per part
with its refdes list, so bom_cpl maps ref -> LCSC; attributes kept for
check_ratings (LEARNINGS of hwde-led-test).

  python3 kicad/gen/parts_build.py     (from the board workspace)
"""
import json
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
S = WS / "parts" / "search"

# file stem, refdes, block, role, alternates
LINES = [
    ("C2856803", ["J1"], "B1 core link", "20-pin 0.5 mm FFC connector to the core board, bottom contact, hinged lid", []),
    ("C2856829", ["J2"], "B2 LCD", "12-pin 0.5 mm FPC connector for the HS20HS072RX LCD tail, double-sided contact", ["C2919494 0.5K-HX-12PWB"]),
    ("C22396388", ["U1"], "B4 expander", "TCA9535 16-bit I2C GPIO expander, 0x27, open-drain INT", ["C130204 TCA9535PWR (TI)"]),
    ("C91447", ["U2"], "B7 IR", "38 kHz IR remote receiver", []),
    ("C361165", ["SW4"], "B5 encoder", "EC11 rotary encoder with push switch, THT", ["C470754 EC11E15244B2"]),
    ("C318884", ["SW1", "SW2", "SW3"], "B6 buttons", "5.1 mm SMD tact switch, 1.5 mm, JLC basic", []),
    ("C273610", ["D1"], "B8 status LED", "RGB LED 1615, common anode", []),
    ("C20917", ["Q1"], "B3 backlight", "N-MOSFET low-side backlight switch", []),
    ("r10k", ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R19"], "pull-ups", "10 k pull-ups (inputs, INT, LCD_RST, IR out); R19 pulls the unused expander pins down", []),
    ("r100k", ["R11"], "B3 backlight", "Q1 gate pull-down", []),
    ("r100", ["R10", "R12"], "B7/B3", "IR supply filter; Q1 gate series", []),
    ("r1k", ["R14"], "B8 status LED", "red cathode resistor", []),
    ("r680", ["R15", "R16"], "B8 status LED", "green/blue cathode resistors", []),
    ("r120", ["R13", "R17", "R18"], "B3 backlight", "backlight series resistors, three 120 R in parallel (40 R)", []),
    ("c100n", ["C1", "C4"], "decoupling", "100 nF at U1 VCC and LCD VCC", []),
    ("c10u", ["C2", "C3"], "B1 core link", "10 uF 25 V bulk on +3V3 and +5V at J1", []),
    ("c4u7", ["C5"], "B7 IR", "IR supply filter cap (datasheet >= 2.2 uF)", []),
    ("c10n", ["C6", "C7"], "B5 encoder", "encoder A/B debounce", []),
]


def main():
    out = []
    for stem, refs, block, role, alts in LINES:
        r = json.loads((S / f"{stem}.json").read_text())["results"][0]
        out.append({
            "ref_prefix_hint": refs[0].rstrip("0123456789"),
            "block": block, "mpn": r["mpn"], "lcsc": r["lcsc"],
            "value": r["mpn"], "package": r.get("package"),
            "basic": r.get("basic"), "stock": r.get("stock"),
            "price": r.get("price"), "datasheet": r.get("datasheet"),
            "brand": r.get("brand"), "price_breaks": r.get("price_breaks", []),
            "alternates": [{"mpn": a.split(" ", 1)[1], "lcsc": a.split(" ")[0]} for a in alts],
            "role": role, "attributes": r.get("attributes", []),
            "description": r.get("description"), "refdes": refs,
        })
    (WS / "parts" / "parts.json").write_text(json.dumps({"parts": out}, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({"script": "parts_build", "status": "pass", "lines": len(out),
                      "refs": sum(len(l[1]) for l in LINES)}))


if __name__ == "__main__":
    main()
