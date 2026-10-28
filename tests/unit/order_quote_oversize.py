"""order_quote's PCB price past the promo size (issue #12).

The base table prices the whole order at 100x100 mm; a bigger board scales it
by area. The old per-board oversize_per_dm2 adder priced a 4-layer 120x100 mm
board at qty 100 at about $14/board against the table's own $0.77/board.
Exits 0 on pass (bin/check_all.py runs it with the skill's scripts on
PYTHONPATH).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "hwde" / "scripts"))
from order_quote import pcb_cost  # noqa: E402

pricing = yaml.safe_load((ROOT / ".claude" / "skills" / "hwde" / "reference"
                          / "jlc_pricing.yaml").read_text())
fails = []


def want(what: str, got: float, exp: float):
    if abs(got - exp) > 0.01:
        fails.append(f"{what}: got {got}, want {exp}")


def cost(layers, qty, w, h):
    return pcb_cost(pricing, layers, qty, w, h, "HASL", "green")


base4 = pricing["pcb"]["base"][4][100]
c = cost(4, 100, 120, 100)
want("4L 120x100 qty 100 total", c["total"], round(base4 * 1.2, 2))
if c["total"] / 100 > 2 * base4 / 100:
    fails.append(f"4L 120x100 qty 100 is ${c['total'] / 100:.2f}/board, "
                 f"over 2x the 100x100 point ${base4 / 100:.2f}")
want("4L 100x100 qty 100 has no oversize", cost(4, 100, 100, 100)["oversize"], 0.0)
want("2L 150x20 (one side over, small area) has no oversize",
     cost(2, 5, 150, 20)["oversize"], 0.0)
want("2L 200x150 qty 5 total", cost(2, 5, 200, 150)["total"],
     round(pricing["pcb"]["base"][2][5] * 3.0, 2))

if fails:
    print("FAIL: " + "; ".join(fails))
    sys.exit(1)
print(f"pass: 4L 120x100 qty 100 = ${c['total']} (${c['total'] / 100:.2f}/board)")
