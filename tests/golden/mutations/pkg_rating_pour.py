"""Mutant: pkg-rating-pour (blinky2).

SIDECAR fault (copper is the golden's, copied unchanged), #649: the +5V
input is declared a 100 V net and J1 carries a `package_ratings` entry
(0.2 mm, "rated at its own pitch"). The board's .kicad_dru is what
rules_gen emits for that entry. The golden's 0.50 mm +5V-to-GND pour gap
at J1 pad 1 (B.Cu) is under IPC-2221's 0.60 mm for 100 V; the pour is not
J1's own pin pitch, so the package rating must not cover it. Must be
caught by check_creepage (kind "creepage", an error, not a
dru_footprint_scope warning).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

sys.path.insert(0, str(mutlib.REPO / "scripts"))
import rules_gen  # noqa: E402


def surgery(text):
    cons = mutlib.golden_json("blinky2", "constraints.json")
    cons["voltages"] = [{"net": "+5V", "voltage": 100}]
    cons["package_ratings"] = [{"ref": "J1", "clearance_mm": 0.2,
                                "why": "connector rated at its own pitch"}]
    rules, _ = rules_gen.package_rules(cons)
    return text, {"sidecars": {"constraints.json": mutlib.dump_json(cons),
                               "blinky2.kicad_dru": rules_gen.render_dru(rules)},
                  "net": "+5V", "voltage": 100, "ref": "J1"}


if __name__ == "__main__":
    sys.exit(mutlib.run("pkg-rating-pour", "blinky2", surgery))
