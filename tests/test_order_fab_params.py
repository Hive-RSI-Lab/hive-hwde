"""#716: --api-create sends the quote's pcbParam verbatim, so every fab
parameter in it must match the board, governed package or not. Mocked sessions
only: this file must never reach a live endpoint."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))

import fabmaterial  # noqa: E402
import order_submit  # noqa: E402
import test_jlcapi as tj  # noqa: E402
from test_order_ready_gate import _api_file, _create, _estimate, _order, _refused  # noqa: E402

api_env = tj.api_env


def _board_with_stackup(pcb: Path, oz_mm=0.035, finish="HASL", mask="",
                        back_mm=None) -> None:
    """make_fab's board with a stackup block saying copper, finish and mask.
    back_mm is B.Cu when it differs from F.Cu."""
    color = f' (color "{mask}")' if mask else ""
    block = f"""(setup (stackup
    (layer "F.Mask" (type "Top Solder Mask"){color} (thickness 0.01))
    (layer "F.Cu" (type "copper") (thickness {oz_mm}))
    (layer "dielectric 1" (type "core") (thickness 1.5) (material "FR4"))
    (layer "B.Cu" (type "copper") (thickness {back_mm or oz_mm}))
    (copper_finish "{finish}")
  ))
"""
    pcb.write_text(tj.BOARD_20X25.replace("  (gr_rect", block + "  (gr_rect"),
                   encoding="utf-8")


# ---- every field: a wrong value refuses, naming it and both values


@pytest.mark.parametrize("field,wrong,quote_text,board_text", [
    ("copperWeight", "2", "'2'", "1"),
    ("thickness", 0.8, "0.8", "1.6"),
    ("surfaceFinish", 2, "2", "1"),
    ("pcbColor", 5, "5", "0"),
    ("width", 1, "1", "20"),
    ("length", 1, "1", "25"),
    ("layer", 1, "1", "2"),
])
def test_wrong_field_is_refused_naming_field_and_values(
        field, wrong, quote_text, board_text, tmp_path, monkeypatch, api_env,
        capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    code, fake = _create(pcb, fab, qj, _api_file(fab, **{field: wrong}),
                         monkeypatch)
    assert code == 2
    assert "create_order" not in fake.names()
    assert (f"{field}: quote {quote_text}, board {board_text}"
            in _order(fab)["api"]["note"])
    assert "create_attempt" not in _order(fab)["api"]


def test_all_fields_match_reaches_create(tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 0
    assert fake.names() == ["create_order"]


def test_the_issue_repro_names_every_field(tmp_path, monkeypatch, api_env, capsys):
    """Issue #716: this exact file used to exit 0 and reach create_order."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    aq = _api_file(fab, copperWeight="2", thickness=0.8, surfaceFinish=99,
                   pcbColor=99, width=1, length=1)
    code, fake = _create(pcb, fab, qj, aq, monkeypatch)
    _refused(code, fake, fab, capsys, "does not match the board")
    note = _order(fab)["api"]["note"]
    for field in ("copperWeight", "thickness", "surfaceFinish", "pcbColor",
                  "width", "length"):
        assert field + ":" in note


@pytest.mark.parametrize("field", ["copperWeight", "thickness", "surfaceFinish",
                                   "pcbColor", "width", "length", "layer"])
def test_a_field_the_quote_does_not_carry_refuses(field, tmp_path, monkeypatch,
                                                  api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    aq = _api_file(fab)
    q = json.loads(aq.read_text(encoding="utf-8"))
    del q["calculate_request"]["pcbParam"][field]
    aq.write_text(json.dumps(q), encoding="utf-8")
    code, fake = _create(pcb, fab, qj, aq, monkeypatch)
    _refused(code, fake, fab, capsys, f"{field}: quote None")


def test_size_rounding_of_the_quote_is_not_a_mismatch(tmp_path, monkeypatch,
                                                      api_env, capsys):
    """order_quote rounds the outline to 2 dp, so 319.602 is quoted as 319.6."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    pcb.write_text(tj.BOARD_20X25.replace("(end 20 25)", "(end 20.004 25.004)"),
                   encoding="utf-8")
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 0 and fake.names() == ["create_order"]


# ---- thickness: the stack sum when it and the general thickness disagree (#745)


def _stack_board(pcb: Path, core: float, general: float = 1.6) -> None:
    """tj.BOARD_20X25 with this general thickness and a stack of F.Mask 0.01,
    F.Cu 0.035, one core, B.Cu 0.035."""
    text = tj.BOARD_20X25.replace("(general (thickness 1.6))",
                                  f"(general (thickness {general}))")
    block = f"""(setup (stackup
    (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness {core}) (material "FR4"))
    (layer "B.Cu" (type "copper") (thickness 0.035))
    (copper_finish "HASL")
  ))
"""
    pcb.write_text(text.replace("  (gr_rect", block + "  (gr_rect"),
                   encoding="utf-8")


def test_the_stack_sum_is_the_thickness_when_it_disagrees_with_general(
        tmp_path, monkeypatch, api_env, capsys):
    """General says 1.6, the layers sum to 0.88: the order is for the stack, so
    a quote of 0.88 matches and 1.6 is the one refused."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    _stack_board(pcb, core=0.8)
    fields = order_submit.board_fab_params(pcb, fab)
    assert fields["thickness"] == [0.88, "its stackup layer sum"]
    code, fake = _create(pcb, fab, qj, _api_file(fab, thickness=0.88),
                         monkeypatch)
    assert code == 0 and fake.names() == ["create_order"]


def test_general_thickness_is_refused_when_the_stack_disagrees(
        tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _stack_board(pcb, core=0.8)
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)  # 1.6
    assert code == 2 and "create_order" not in fake.names()
    assert "thickness: quote 1.6, board 0.88 (its stackup layer sum)" \
        in _order(fab)["api"]["note"]


def test_a_stack_within_the_tolerance_leaves_general_in_charge(tmp_path):
    """1.5 core: the sum is 1.58, 1.3 % under general: general stays."""
    pcb, fab, _ = tj.make_fab(tmp_path)
    _stack_board(pcb, core=1.5)
    assert order_submit.board_fab_params(pcb, fab)["thickness"] \
        == [1.6, "its general thickness"]


# ---- what the board can't say refuses, or falls to the human's choice


@pytest.mark.parametrize("text,field", [
    ("(kicad_pcb)", "layer"),
    (tj.BOARD_20X25.replace('(gr_rect (start 0 0) (end 20 25) '
                            '(layer "Edge.Cuts"))', ""), "width"),
    (tj.BOARD_20X25.replace("(general (thickness 1.6))", ""), "thickness"),
])
def test_a_board_that_cannot_say_refuses(text, field, tmp_path, monkeypatch,
                                         api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    pcb.write_text(text, encoding="utf-8")
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    assert f"{field}: quote" in _order(fab)["api"]["note"]
    assert "not stated" in _order(fab)["api"]["note"]


def test_layer_count_falls_back_to_fab_manifest(tmp_path, monkeypatch, api_env,
                                                capsys):
    """A board with no (layers) block: fab_export's layer_count still says."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    pcb.write_text(tj.BOARD_20X25.replace(
        '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))',
        ""), encoding="utf-8")
    (fab / "manifest.json").write_text(json.dumps({"layer_count": 4}),
                                       encoding="utf-8")
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    assert "layer: quote 2, board 4 (fab/manifest.json" in _order(fab)["api"]["note"]


def test_copper_comes_from_the_board_stackup_first(tmp_path, monkeypatch,
                                                   api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)         # stackup.md says 1 oz
    _board_with_stackup(pcb, oz_mm=0.07)
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    assert "copperWeight: quote '1', board 2" in _order(fab)["api"]["note"]


def test_outer_layers_that_differ_refuse_naming_both(tmp_path, monkeypatch,
                                                     api_env, capsys):
    """#728: copperWeight is one value for both outer layers, so a quote that
    matches F.Cu still misstates a B.Cu of another weight."""
    pcb, fab, qj = tj.make_fab(tmp_path)         # the quote says 1 oz
    _board_with_stackup(pcb, oz_mm=0.035, back_mm=0.07)
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    note = _order(fab)["api"]["note"]
    assert "copperWeight: quote '1'" in note
    assert "1 oz (F.Cu in its stackup)" in note
    assert "2 oz (B.Cu in its stackup)" in note


def test_a_quote_matching_only_b_cu_refuses_too(tmp_path, monkeypatch, api_env,
                                                capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)         # the quote says 1 oz
    _board_with_stackup(pcb, oz_mm=0.07, back_mm=0.035)
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    note = _order(fab)["api"]["note"]
    assert "2 oz (F.Cu in its stackup)" in note
    assert "1 oz (B.Cu in its stackup)" in note


def test_the_rf_kit_ro4350b_b_cu_refuses(tmp_path, monkeypatch, api_env, capsys):
    """The board that found this: a 1 oz F.Cu and a 1.524 mm B.Cu, quoted at 1 oz."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    _board_with_stackup(pcb, oz_mm=0.035, back_mm=1.524)
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    assert "43.54 oz (B.Cu in its stackup)" in _order(fab)["api"]["note"]


def test_a_b_cu_within_the_tolerance_passes():
    man = {"spec_snapshot": {}, "board_fab": {
        "copperWeight": [1.0, "F.Cu in its stackup"],
        "copperWeightBack": [1.03, "B.Cu in its stackup"]}}
    q = {"calculate_request": {"pcbParam": {"copperWeight": "1"}}}
    assert not [m for m in order_submit._quote_fab_mismatches(man, q)
                if m.startswith("copperWeight")]


def test_a_recorded_copper_oz_waives_both_layers_ungoverned_only():
    man = {"spec_snapshot": {}, "board_fab": {
        "copperWeight": [1.0, "F.Cu in its stackup"],
        "copperWeightBack": [2.0, "B.Cu in its stackup"]}}
    q = {"calculate_request": {"pcbParam": {"copperWeight": "2"}},
         "copper_weight_oz": 2.0, "copper_weight_source": "human override"}
    assert not [m for m in order_submit._quote_fab_mismatches(man, q)
                if m.startswith("copperWeight")]
    man["release"] = {"governed": True}
    assert [m for m in order_submit._quote_fab_mismatches(man, q)
            if m.startswith("copperWeight")]


def test_a_manifest_from_before_728_does_not_newly_refuse():
    """A package frozen without the B.Cu entry is judged on F.Cu, as it was."""
    man = {"spec_snapshot": {}, "board_fab": {
        "copperWeight": [1.0, "F.Cu in its stackup"]}}
    q = {"calculate_request": {"pcbParam": {"copperWeight": "1"}}}
    assert not [m for m in order_submit._quote_fab_mismatches(man, q)
                if m.startswith("copperWeight")]


def test_copper_falls_back_to_stackup_md(tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path, stackup="JLC2313_1.6_2oz")
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    assert "copperWeight: quote '1', board 2" in _order(fab)["api"]["note"]


def test_recorded_copper_override_stays_a_waiver(tmp_path, monkeypatch, api_env,
                                                 capsys):
    """--copper-oz is the person's written waiver on an ungoverned package; it
    covers that one number and only when the file says the same number."""
    pcb, fab, qj = tj.make_fab(tmp_path, stackup="JLC2313_1.6_2oz")
    aq = _api_file(fab, copperWeight="1")
    q = json.loads(aq.read_text(encoding="utf-8"))
    q["copper_weight_source"] = "human override (--copper-oz)"
    q["copper_weight_oz"] = 1.0
    aq.write_text(json.dumps(q), encoding="utf-8")
    code, fake = _create(pcb, fab, qj, aq, monkeypatch)
    assert code == 0 and fake.names() == ["create_order"]


def test_override_record_for_another_weight_is_no_waiver(tmp_path, monkeypatch,
                                                         api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path, stackup="JLC2313_1.6_2oz")
    aq = _api_file(fab, copperWeight="1")
    q = json.loads(aq.read_text(encoding="utf-8"))
    q["copper_weight_source"] = "human override (--copper-oz)"
    q["copper_weight_oz"] = 2.0
    aq.write_text(json.dumps(q), encoding="utf-8")
    code, fake = _create(pcb, fab, qj, aq, monkeypatch)
    _refused(code, fake, fab, capsys, "copperWeight: quote '1'")


def test_board_enig_refuses_a_hasl_quote(tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _board_with_stackup(pcb, finish="ENIG")
    code, fake = _create(pcb, fab, qj, _api_file(fab, surfaceFinish=1),
                         monkeypatch)
    _refused(code, fake, fab, capsys, "surfaceFinish: quote 1, board 2")


def test_template_hasl_on_the_board_does_not_outvote_a_chosen_finish(
        tmp_path, monkeypatch, api_env, capsys):
    """board_init writes HASL on every board and KiCad keeps it, so HASL there
    is no decision: every lab board has it, and their quotes choose ENIG. The
    finish chosen in quote.json is what the quote has to match."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    _board_with_stackup(pcb, finish="HASL")
    _estimate(qj, pcb)                           # row: ENIG, green
    code, fake = _create(pcb, fab, qj, _api_file(fab, surfaceFinish=2),
                         monkeypatch)
    assert code == 0 and fake.names() == ["create_order"]


def test_chosen_finish_differs_from_the_quote(tmp_path, monkeypatch, api_env,
                                              capsys):
    """Stale file: quote.json moved to ENIG after --api priced HASL."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    _board_with_stackup(pcb, finish="HASL")
    _estimate(qj, pcb)
    code, fake = _create(pcb, fab, qj, _api_file(fab, surfaceFinish=1),
                         monkeypatch)
    _refused(code, fake, fab, capsys, "surfaceFinish: quote 1, board 2")


def test_board_mask_colour_decides(tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _board_with_stackup(pcb, mask="Black")
    code, fake = _create(pcb, fab, qj, _api_file(fab, pcbColor=0), monkeypatch)
    _refused(code, fake, fab, capsys, "pcbColor: quote 0, board 5")


@pytest.mark.parametrize("key,value,field", [
    ("surface_finish", "OSP", "surfaceFinish"),
    ("solder_mask_color", "mauve", "pcbColor"),
    ("surface_finish", None, "surfaceFinish"),
    ("solder_mask_color", None, "pcbColor"),
])
def test_a_choice_with_no_order_code_refuses(key, value, field, tmp_path,
                                             monkeypatch, api_env, capsys):
    """build_pcb_param turns an unknown finish into HASL; create must not."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    q = json.loads(qj.read_text(encoding="utf-8"))
    q["matrix"][0][key] = value
    qj.write_text(json.dumps(q), encoding="utf-8")
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    assert f"{field}: quote" in _order(fab)["api"]["note"]


# ---- the reader


def test_stackup_facts_read_a_kicad_save(tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    _board_with_stackup(pcb, oz_mm=0.07, finish="ENIG", mask="Blue")
    assert fabmaterial.board_stackup_facts(pcb) == {
        "copper_finish": "ENIG", "mask_color": "Blue", "outer_copper_mm": 0.07,
        "back_copper_mm": 0.07}
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    assert fabmaterial.board_stackup_facts(pcb) == {
        "copper_finish": None, "mask_color": None, "outer_copper_mm": None,
        "back_copper_mm": None}
