"""order_quote's PCB price past the promo size (issue #12).

The base table prices the whole order at 100x100 mm; a bigger board scales it
by area. The old per-board oversize_per_dm2 adder priced a 4-layer 120x100 mm
board at qty 100 at about $14/board against the table's own $0.77/board.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

from order_quote import pcb_cost  # noqa: E402

PRICING = yaml.safe_load((REPO / "reference" / "jlc_pricing.yaml").read_text())
BASE = PRICING["pcb"]["base"]


def cost(layers, qty, w, h):
    return pcb_cost(PRICING, layers, qty, w, h, "HASL", "green")


def test_4l_120x100_scales_the_base_by_area():
    c = cost(4, 100, 120, 100)
    assert c["total"] == pytest.approx(BASE[4][100] * 1.2, abs=0.01)
    assert c["total"] / 100 < 2 * BASE[4][100] / 100


def test_2l_200x150_is_three_times_the_base():
    assert cost(2, 5, 200, 150)["total"] == pytest.approx(BASE[2][5] * 3.0, abs=0.01)


@pytest.mark.parametrize("w,h", [(100, 100), (150, 20)])
def test_no_oversize_at_or_under_the_promo_area(w, h):
    assert cost(4, 100, w, h)["oversize"] == 0.0
