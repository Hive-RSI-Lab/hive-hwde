"""core: hand placement of the structural parts (connectors, ICs, power
parts) in board-local mm (u from the left edge, v from the rear edge).
Writes anneal/manual.ops.json; place_anneal then places the passives
around these locked anchors."""
import json, sys
from pathlib import Path
_bi = Path(__file__).resolve().parents[2] / "reports" / "board_init.json"
X0, Y0 = json.loads(_bi.read_text())["outline_origin"]  # board top-left
A = {
    # rear edge: speaker terminals (wire entry to the rear), RCAs, TOSLINK
    "J6": (14.5, 5.5, 180), "J7": (27, 5.5, 180),
    "J10": (47.4, 5.5, 180), "J11": (59.9, 5.5, 180),
    "J4": (73, 14.5, 180), "J5": (91, 14.5, 180), "U19": (105.4, 10.5, 180),
    # zone A / zone B amps with 2x2 inductor blocks behind them
    "L1": (5.5, 18.8, 90), "L2": (5.5, 29.7, 90), "L3": (17.6, 29.7, 90), "L4": (17.6, 18.8, 90),
    "L5": (38.4, 18.8, 90), "L6": (38.4, 29.7, 90), "L7": (50.5, 29.7, 90), "L8": (50.5, 18.8, 90),
    "U15": (11.5, 41.0, 0), "U16": (44.4, 41.0, 0),
    "C5": (28.1, 19.8), "C6": (28.1, 30.8), "C311": (61, 30.8),
    # speaker-side filter caps + snubbers (1 uF, 4.7R + 1 nF) per output
    "C226": (4.5, 11.55, 90), "R227": (6.5, 11.55, 90), "C228": (8.2, 11.55, 90),
    "C229": (14.6, 11.55, 90), "R230": (16.6, 11.55, 90), "C231": (18.3, 11.55, 90),
    "C232": (21.4, 11.55, 90), "R233": (23.4, 11.55, 90), "C234": (25.1, 11.55, 90),
    "C235": (31.5, 11.55, 90), "R236": (33.5, 11.55, 90), "C237": (35.2, 11.55, 90),
    "C324": (37.4, 11.55, 90), "R325": (39.4, 11.55, 90), "C326": (41.1, 11.55, 90),
    "C327": (47.5, 11.55, 90), "R328": (49.5, 11.55, 90), "C329": (51.2, 11.55, 90),
    "C330": (54.3, 11.55, 90), "R331": (56.3, 11.55, 90), "C332": (58.0, 11.55, 90),
    "C333": (56.5, 16.1, 90), "R334": (58.5, 16.1, 90), "C335": (60.2, 16.1, 90),
    # PVDD decouplers at the ends of each amp's pin row (the row itself is
    # reserved for the output fan-in pours)
    "C210": (2.2, 36.9, 90), "C209": (2.2, 40.5, 90),
    "C208": (21.2, 36.9, 90), "C207": (21.2, 40.5, 90),
    "C310": (35.1, 36.9, 90), "C309": (35.1, 40.5, 90),
    "C308": (54.1, 36.9, 90), "C307": (54.1, 40.5, 90),
    # power entry on the left edge
    "J1": (3.5, 58, -90), "D1": (11, 52, 90), "R1": (13, 60, 0),
    "U3": (13, 65), "U1": (9, 67), "U2": (15, 69.5), "Q1": (22, 52, 0),
    "Q2": (22, 59), "Q3": (27, 59),
    "U4": (32, 50, 180), "L9": (38, 50), "U5": (32, 62), "L10": (38, 62),
    "U6": (92, 50),
    # Ethernet + USB-UART
    "J2": (12.5, 82, -90), "U9": (29, 82), "U8": (47, 68, 90),
    # DAC block behind the RCAs
    "U12": (77, 25), "U10": (86, 30), "Y1": (86, 22.5), "U11": (82, 42),
    "U13": (100, 31), "U14": (72, 48),
    # MCU, expander
    "U7": (102.6, 80, -90), "U17": (78, 62), "Y2": (36, 78),
    # front edge UI
    "J3": (20, 96.5), "D3": (33, 98.8), "U18": (42, 97),
    "SW1": (56, 91.5, 180), "SW2": (70, 97), "SW3": (78, 97),
}
ops = []
for ref, v in A.items():
    u, w = v[0], v[1]
    op = {"op": "place", "ref": ref, "x": round(X0 + u, 3), "y": round(Y0 + w, 3)}
    if len(v) > 2:
        op["deg"] = v[2]
    ops.append(op)
    ops.append({"op": "lock", "ref": ref, "locked": True})
out = Path(__file__).resolve().parents[2] / "anneal" / "manual.ops.json"
out.write_text(json.dumps({"version": 1, "ops": ops}, indent=1) + "\n")
print(out, len(A))
