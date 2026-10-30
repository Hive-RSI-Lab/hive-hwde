"""core: plane plan in board-local mm -> constraints.json "planes" (absolute).
In1 GND everywhere; In2 PVDD under the amps (rear-left) and +3V3 elsewhere;
GND pours on both outer layers."""
import json
from pathlib import Path
B = Path(__file__).resolve().parents[2]
X0, Y0 = json.loads((B / "reports" / "board_init.json").read_text())["outline_origin"]
R = lambda u1, v1, u2, v2: [round(X0 + u1, 3), round(Y0 + v1, 3), round(X0 + u2, 3), round(Y0 + v2, 3)]
planes = [{"layer": "In1.Cu", "net": "GND"},
          {"layer": "In2.Cu", "net": "PVDD", "region": R(0, 0, 63, 47.5)},
          {"layer": "In2.Cu", "net": "PVDD", "region": R(17.5, 47.5, 27.5, 51.0)},
          {"layer": "In2.Cu", "net": "VBUS_IN", "region": R(0, 48.0, 12.8, 70.0)},
          {"layer": "In2.Cu", "net": "VBUS", "region": R(13.3, 51.5, 37.0, 62.0)},
          {"layer": "In2.Cu", "net": "+3V3", "region": R(63.5, 0, 120, 100)},
          {"layer": "In2.Cu", "net": "+3V3", "region": R(0, 70.5, 63.5, 100)},
          {"layer": "In2.Cu", "net": "+3V3", "region": R(37.5, 48.0, 63.5, 70.5)},
          {"layer": "In2.Cu", "net": "+3V3", "region": R(13.3, 62.5, 37.5, 70.5)},
          {"layer": "B.Cu", "net": "GND"}, {"layer": "F.Cu", "net": "GND"}]
for f in (B / "architecture" / "constraints.json", B / "kicad" / "constraints.json"):
    d = json.loads(f.read_text())
    d["planes"] = planes
    f.write_text(json.dumps(d, indent=1) + "\n")
print("planes", X0, Y0)
