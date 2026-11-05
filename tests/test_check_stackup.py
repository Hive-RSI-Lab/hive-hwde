"""check_stackup (#728): copper thickness no fab builds, and outer layers no
order can state. Known answers on small synthetic stackups, the rf-kit-ro4350b
block among them."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import check_stackup  # noqa: E402
import fabmaterial  # noqa: E402


def _board(tmp_path, f_cu=0.035, b_cu=0.035, inner=(), block=True, core=1.5,
           general="sum", quote=None) -> Path:
    """A board whose stackup says these copper thicknesses (None: says none).
    core is the dielectric 1 thickness, general the (general (thickness)) and
    quote the fab/quote.json spec thickness_mm, when there is one."""
    def cu(name, mm):
        th = "" if mm is None else f" (thickness {mm})"
        return f'(layer "{name}" (type "copper"){th})'
    rows = [cu("F.Cu", f_cu),
            f'(layer "dielectric 1" (type "core") (thickness {core}))']
    for i, mm in enumerate(inner, 1):
        rows += [cu(f"In{i}.Cu", mm),
                 f'(layer "dielectric {i + 1}" (type "prepreg") (thickness 0.1))']
    rows.append(cu("B.Cu", b_cu))
    stack = "(setup (stackup\n  " + "\n  ".join(rows) + '\n  (copper_finish "ENIG")\n))\n'
    (tmp_path / "kicad").mkdir(exist_ok=True)
    pcb = tmp_path / "kicad" / "b.kicad_pcb"
    if quote is not None:
        (tmp_path / "fab").mkdir(exist_ok=True)
        (tmp_path / "fab" / "quote.json").write_text(
            json.dumps({"spec": {"thickness_mm": quote}}), encoding="utf-8")
    if general == "sum":  # a board that agrees with itself
        general = round(sum(float(x) for x in re.findall(
            r"thickness ([\d.]+)", stack)), 4)
    gen = "" if general is None else f"(general (thickness {general}))\n"
    pcb.write_text("(kicad_pcb\n" + gen
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


# ---- the three thicknesses: stack sum, general, the quote's (#745)
# The 48-board spread: 47 sit within 4.4 % of each other (stack sum 1.6742 with
# the mask against 1.6 general); rf-kit-ro4350b is 4.617 against 1.6.


def test_the_rf_kit_ro4350b_stack_sum_against_general_is_an_error(tmp_path):
    """4.6 mm of stack against a 1.6 mm board: the issue's known answer."""
    # 4.547 + 2 * 0.035 = 4.617
    payload = _run(_board(tmp_path, core=4.547, general=1.6))
    assert _kinds(payload) == [("error", "thickness_stack_vs_general", None)]
    msg = payload["violations"][0]["msg"]
    assert "4.617" in msg and "1.6" in msg and "10%" in msg


def test_the_quote_against_the_stack_sum_is_an_error(tmp_path):
    payload = _run(_board(tmp_path, general=1.57, quote=0.8))
    assert _kinds(payload) == [("error", "thickness_quote_differs", None)] * 2
    msgs = " ".join(v["msg"] for v in payload["violations"])
    assert "0.8" in msgs and "1.57" in msgs


def test_the_general_against_the_quote_alone_is_named(tmp_path):
    """Stack and quote agree, general is the odd one out."""
    payload = _run(_board(tmp_path, core=0.73, general=1.6, quote=0.8))
    assert sorted(k for _, k, _ in _kinds(payload)) == [
        "thickness_quote_differs", "thickness_stack_vs_general"]


@pytest.mark.parametrize("core,ok", [
    (1.5, True),         # 1.57 against 1.6
    (1.6042, True),      # 1.6742 with the mask: the worst of the 47
    (1.70, True),        # 1.77 against 1.6: 9.6 %
    (1.73, False),       # 1.80 against 1.6: 11.1 %
    (0.9, False)])
def test_the_tolerance_edge(tmp_path, core, ok):
    kinds = [k for _, k, _ in _kinds(_run(_board(tmp_path, core=core, general=1.6)))]
    assert (kinds == []) == ok


def test_the_rf_kit_ro4350b_corrected_passes(tmp_path):
    """Corrected stack 1.6, quote the nominal 1.52 core: 5 %, within."""
    assert _kinds(_run(_board(tmp_path, core=1.53, general=1.6, quote=1.52))) == []


def test_no_quote_or_no_general_is_not_a_finding(tmp_path):
    assert _kinds(_run(_board(tmp_path, general=None))) == []
