"""order_quote on a castellated board: Standard PCBA, panel, via tier, placed
parts (hw-lab eda-crossover's review, finding B-1)."""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "lib")]

import order_quote  # noqa: E402

PRICING = yaml.safe_load((ROOT / "reference" / "jlc_pricing.yaml").read_text("utf-8"))


def board(tmp_path, size=(18, 35), via=(0.352, 0.2), parts=("C1", "C2"), art=("JP1",)):
    fps = ""
    for i, ref in enumerate((*parts, *art)):
        fps += (f'(footprint "t:r" (layer "F.Cu") (at {2 + i} 5)\n'
                f'(property "Reference" "{ref}" (at 0 0) (layer "F.SilkS"))\n'
                '(pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu") (net "A"))\n'
                '(pad "2" smd rect (at 0 1) (size 0.5 0.5) (layers "F.Cu") (net "B")))\n')
    fps += ('(footprint "t:row" (layer "F.Cu") (at 0 10)\n'
            '(property "Reference" "J3" (at 0 0) (layer "F.SilkS"))\n'
            '(pad "1" thru_hole circle (at 0 0) (size 1.4 1.4) (drill 0.8) '
            '(layers "*.Cu" "*.Mask") (net "A") (property pad_prop_castellated)))\n')
    w, h = size
    edges = "".join(f'(gr_line (start {a}) (end {b}) (stroke (width 0.1) (type solid)) '
                    '(layer "Edge.Cuts"))\n' for a, b in
                    ((f"0 0", f"{w} 0"), (f"{w} 0", f"{w} {h}"),
                     (f"{w} {h}", f"0 {h}"), (f"0 {h}", "0 0")))
    v = f'(via (at 5 20) (size {via[0]}) (drill {via[1]}) (layers "F.Cu" "B.Cu") (net "A"))\n' if via else ""
    f = tmp_path / "b.kicad_pcb"
    f.write_text('(kicad_pcb (version 20240108) (generator "t")\n'
                 '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))\n'
                 + edges + fps + v + ")\n")
    return f


def parts_json(tmp_path, refs=("C1", "C2")):
    f = tmp_path / "parts.json"
    f.write_text(json.dumps({"parts": [
        {"lcsc": "C9", "basic": True, "refdes": list(refs), "min_qty": 20, "price": 0.1,
         "price_breaks": [{"qty": 1, "price": 0.1}, {"qty": 10, "price": 0.05}]},
        {"lcsc": "C8", "basic": False, "refdes": ["J3"], "min_qty": 1, "price": 1.0,
         "price_breaks": [{"qty": 1, "price": 1.0}]}]}))
    return f


def q(tmp_path, **kw):
    return order_quote.run(kw.pop("pcb", None) or board(tmp_path), [2], ["ENIG"], ["green"],
                           assembly=True, parts_json=parts_json(tmp_path), thickness=0.8, **kw)


def test_castellated_goes_standard_and_says_it_needs_a_panel(tmp_path):
    rep = q(tmp_path)
    assert rep["spec"]["pcba"]["service"] == "standard"
    assert rep["spec"]["pcba"]["panel_needed"]
    asm = rep["matrix"][0]["assembly"]
    a = PRICING["assembly_standard"]
    assert asm["service"] == "standard"
    assert asm["setup"] == a["setup_fee"] and asm["stencil"] == a["stencil"]
    assert asm["feeders"] == pytest.approx(2 * a["per_part_feeder"])
    assert asm["fixture"] == pytest.approx(2 * a["fixture_fee"])


def test_big_enough_panel_is_not_told_to_panelise(tmp_path):
    rep = q(tmp_path, pcb=board(tmp_path, size=(91, 70)), boards_per_panel=4)
    assert not rep["spec"]["pcba"]["panel_needed"]
    row = rep["matrix"][0]
    assert row["boards"] == 8
    assert row["unit_cost"] == pytest.approx(row["total"] / 8, abs=0.01)


def test_n_parts_counts_placed_parts_only(tmp_path):
    rep = q(tmp_path)
    assert rep["spec"]["n_parts"] == 3      # C1 C2 J3, not the JP1 copper feature
    assert rep["spec"]["n_joints"] == 5


def test_no_parts_json_counts_every_footprint_as_before(tmp_path):
    rep = order_quote.run(board(tmp_path, size=(100, 100)), [5], ["HASL"], ["green"], assembly=False)
    assert rep["spec"]["n_parts"] == 0
    assert order_quote._assembly_counts(board(tmp_path), None)[0] == 4


def test_paid_via_tier_is_in_the_pcb_line(tmp_path):
    row = q(tmp_path)["matrix"][0]
    assert row["pcb"]["via_tier"] == PRICING["options"]["via_tier"]["0.2"]
    assert row["pcb"]["via_tier"] > 0


@pytest.mark.parametrize("via,want", [((0.6, 0.3), None), ((0.45, 0.2), None), ((0.352, 0.2), "0.2"),
                                      ((0.4, 0.25), "0.25"), ((0.3, 0.15), "0.15"), ((0.2, 0.1), "0.1")])
def test_via_tier_of_the_board(tmp_path, via, want):
    import geom
    assert order_quote.via_tier(geom.load_board(board(tmp_path, via=via))) == want


def test_free_vias_show_no_tier(tmp_path):
    rep = q(tmp_path, pcb=board(tmp_path, via=(0.6, 0.3)))
    assert "via_tier" not in rep["matrix"][0]["pcb"]
    assert "via_tier" not in rep["spec"]


def test_pofv_is_a_row_of_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(order_quote, "pcb_cost",
                        lambda *a, via_fill="none", **k: {"total": 10.0 if via_fill == "none" else 27.0})
    rows = q(tmp_path, via_fills=["none", "epoxy"])["matrix"]
    assert [r.get("via_fill") for r in rows] == [None, "epoxy"]
    assert rows[1]["pcb"]["total"] - rows[0]["pcb"]["total"] == 17.0


def test_pofv_price_by_layer_count():
    # 2 layers has no POFV price: refused, never free by accident
    with pytest.raises(ValueError):
        order_quote.pcb_cost(PRICING, 2, 5, 50, 50, "ENIG", "green", via_fill="epoxy")
    assert order_quote.pcb_cost(PRICING, 4, 5, 50, 50, "ENIG", "green", via_fill="epoxy")["via_fill"] == 17.33


def test_measured_pcb_replaces_the_table_and_pofv_stays_on_top():
    pc = order_quote.pcb_cost(PRICING, 4, 5, 91, 70, "ENIG", "green", castellated=True,
                              tier="0.2", via_fill="epoxy", measured=100.0)
    assert pc["measured"] == 100.0 and pc["total"] == 117.33


def test_components_respect_min_order_and_price_breaks(tmp_path):
    got = order_quote.components_cost(parts_json(tmp_path), 8)
    # C9: 2 x 8 = 16, min 20, at the 10-up break; C8: 8 at 1.0
    assert got["total"] == pytest.approx(20 * 0.05 + 8 * 1.0)


def test_a_panel_copys_refs_are_the_single_boards_parts(tmp_path):
    f = board(tmp_path, parts=("C1_B1", "C2_B1", "C1_B2", "C2_B2"), art=("JP1_B1",))
    assert order_quote._assembly_counts(f, parts_json(tmp_path))[0] == 5


def test_standard_needs_a_parts_list(tmp_path):
    with pytest.raises(ValueError, match="--parts"):
        order_quote.run(board(tmp_path), [2], ["ENIG"], ["green"], assembly=True, thickness=0.8)
