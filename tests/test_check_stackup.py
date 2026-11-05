"""check_stackup (#728): copper thickness no fab builds, and outer layers no
order can state. Known answers on small synthetic stackups, the rf-kit-ro4350b
block among them."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import check_stackup  # noqa: E402
import fabmaterial  # noqa: E402


def _board(tmp_path, f_cu=0.035, b_cu=0.035, inner=(), block=True) -> Path:
    """A board whose stackup says these copper thicknesses (None: says none)."""
    def cu(name, mm):
        th = "" if mm is None else f" (thickness {mm})"
        return f'(layer "{name}" (type "copper"){th})'
    rows = [cu("F.Cu", f_cu), '(layer "dielectric 1" (type "core") (thickness 1.5))']
    for i, mm in enumerate(inner, 1):
        rows += [cu(f"In{i}.Cu", mm),
                 f'(layer "dielectric {i + 1}" (type "prepreg") (thickness 0.1))']
    rows.append(cu("B.Cu", b_cu))
    stack = "(setup (stackup\n  " + "\n  ".join(rows) + '\n  (copper_finish "ENIG")\n))\n'
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text("(kicad_pcb\n(general (thickness 1.6))\n"
                   + (stack if block else "(setup)\n") + ")\n", encoding="utf-8")
    return pcb


def _run(pcb):
    payload, _ = check_stackup.run(["--pcb", str(pcb)])
    return payload


def _kinds(payload):
    return [(v["severity"], v["kind"], v.get("layer")) for v in payload["violations"]]


def test_the_rf_kit_ro4350b_b_cu_is_an_error(tmp_path):
    """1.524 mm is the laminate's thickness landing in B.Cu: 43.5 oz."""
    payload = _run(_board(tmp_path, b_cu=1.524))
    assert payload["status"] == "violations"
    assert _kinds(payload) == [("error", "copper_thickness_range", "B.Cu")]
    msg = payload["violations"][0]["msg"]
    assert "1.524 mm" in msg and "43.5 oz" in msg and "0.009" in msg and "0.21" in msg


@pytest.mark.parametrize("mm,ok", [
    (0.0089, False), (0.009, True), (0.035, True), (0.21, True), (0.2101, False),
    (0, False)])
def test_the_range_edges_on_both_outer_layers(tmp_path, mm, ok):
    payload = _run(_board(tmp_path, f_cu=mm, b_cu=mm))
    want = [] if ok else [("error", "copper_thickness_range", "F.Cu"),
                          ("error", "copper_thickness_range", "B.Cu")]
    assert _kinds(payload) == want


def test_an_inner_layer_out_of_range_is_named(tmp_path):
    payload = _run(_board(tmp_path, inner=(0.0175, 0.5)))
    assert _kinds(payload) == [("error", "copper_thickness_range", "In2.Cu")]


def test_outer_layers_that_differ_name_both(tmp_path):
    payload = _run(_board(tmp_path, f_cu=0.035, b_cu=0.07))
    assert _kinds(payload) == [("error", "outer_copper_differs", None)]
    msg = payload["violations"][0]["msg"]
    assert "F.Cu" in msg and "1 oz" in msg and "B.Cu" in msg and "2 oz" in msg


def test_inner_layers_lighter_than_the_outer_pass(tmp_path):
    """The usual 4-layer build: 1 oz outside, 0.5 oz inside."""
    payload = _run(_board(tmp_path, inner=(0.0175, 0.0175)))
    assert payload["status"] == "pass"
    assert payload["copper_mm"] == {"F.Cu": 0.035, "In1.Cu": 0.0175,
                                    "In2.Cu": 0.0175, "B.Cu": 0.035}


def test_outer_layers_within_the_order_tolerance_pass(tmp_path):
    """The order compare's 0.05 oz, so the two refuse the same boards."""
    assert _kinds(_run(_board(tmp_path, f_cu=0.035, b_cu=0.0365))) == []
    assert _kinds(_run(_board(tmp_path, f_cu=0.035, b_cu=0.0375))) \
        == [("error", "outer_copper_differs", None)]


def test_no_stackup_block_is_nothing_to_judge_not_a_pass(tmp_path):
    payload = _run(_board(tmp_path, block=False))
    assert payload["status"] == "pass" and payload["violations"] == []
    assert "stackup" in payload["nothing_to_judge"]


def test_a_copper_layer_with_no_thickness_is_not_judged(tmp_path):
    payload = _run(_board(tmp_path, b_cu=None))
    assert _kinds(payload) == []
    assert "B.Cu" in " ".join(payload["not_judged"])


def test_the_reader_keeps_stack_order_and_a_zero(tmp_path):
    """geom swaps a zero for its default, which would hide this from a check."""
    pcb = _board(tmp_path, f_cu=0, inner=(0.0175,), b_cu=None)
    assert fabmaterial.board_copper_layers(pcb) == [
        ("F.Cu", 0.0), ("In1.Cu", 0.0175), ("B.Cu", None)]


def test_a_missing_board_is_a_check_error(tmp_path, capsys):
    code = check_stackup.main(["--pcb", str(tmp_path / "gone.kicad_pcb")])
    assert code == 2


def test_the_cli_writes_its_report(tmp_path, capsys):
    out = tmp_path / "r.json"
    code = check_stackup.main(["--pcb", str(_board(tmp_path, b_cu=1.524)),
                               "--out", str(out)])
    assert code == 1
    assert json.loads(out.read_text())["counts"]["by_severity"] == {"error": 1}
