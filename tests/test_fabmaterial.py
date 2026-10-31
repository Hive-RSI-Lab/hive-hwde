"""Rogers / high-frequency laminates at JLC, carried from the stackup to the
order (issue #332).

JLC sells RO4350B (and ZYF PTFE) as a separate 2-layer high-frequency
product; RO4003C and Rogers + FR-4 hybrids are not sold. These tests pin:
the material tables, the stackup entries (solved geometry keyed on the
dielectric's thickness, not the layer count), the floors a Rogers board is
held to, and that an order for a Rogers board can't go out as FR-4 (SI-09).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import board_init  # noqa: E402
import dfm_check  # noqa: E402
import fab_export  # noqa: E402
import fabfloors  # noqa: E402
import fabmaterial  # noqa: E402
import impedance as imp  # noqa: E402
import order_quote  # noqa: E402
import order_submit  # noqa: E402
import rules_gen  # noqa: E402

REFERENCE = REPO / "reference"
STACKS = yaml.safe_load((REFERENCE / "stackups.yaml").read_text("utf-8"))
CAPS = yaml.safe_load((REFERENCE / "jlc_capabilities.yaml").read_text("utf-8"))
PRICING = yaml.safe_load((REFERENCE / "jlc_pricing.yaml").read_text("utf-8"))
BASE_BOARD = REPO / "tests" / "fixtures" / "mating" / "lipo_boost_after.kicad_pcb"

# A 2-layer stack gets solved controlled-impedance profiles when its outer
# dielectric is thin enough for the far copper to be a usable reference
# plane: at 0.8 mm, 50 ohm on RO4350B is already 1.6 mm wide; at 1.5 mm it
# is 3.3 mm (and 2.7 mm on FR-4). Keyed on h, never on the layer count.
REFERENCE_PLANE_MAX_H_MM = 0.8

RO = "JLC_HF2_RO4350B_0.508"


def _board(tmp_path: Path, stackup_name: str) -> Path:
    """lipo_boost's board with its stackup block replaced by a stackups.yaml
    entry's, exactly as board_init writes it."""
    text = BASE_BOARD.read_text(encoding="utf-8")
    m = re.search(r"\(stackup\b", text)
    depth = 0
    for j in range(m.start(), len(text)):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        if depth == 0:
            break
    text = text[:m.start()] + text[j + 1:]
    pcb = tmp_path / "rf.kicad_pcb"
    pcb.write_text(text, encoding="utf-8")
    board_init.inject_stackup(pcb, STACKS["stackups"][stackup_name])
    return pcb


# ------------------------------------------------------------ the tables

def test_every_material_has_a_floor_row_and_an_order_line():
    for fam, row in CAPS["materials"].items():
        if fam == "fr4":
            assert row["plate_type"] == 1
            continue
        assert row["plate_type"] == 5, fam          # JLC's HF quote link
        assert row["impedance_control"] is False, fam
        for layers in row["layers"]:
            for oz in row["copper_oz"]:
                key = fabfloors.capability_class(layers, oz, fam)
                assert key in CAPS["design_rules"], key
        assert fam in PRICING["pcb"]["high_frequency"], fam


def test_families_from_material_names():
    f = fabmaterial.family_of_name
    assert f("FR4") == "fr4"
    assert f("FR4 1080x3") == "fr4"
    assert f("Rogers RO4350B") == "ro4350b"
    assert f("RO4003C") == "ro4003c"
    assert f("ZYF255DA") == "ptfe_zyf"
    assert f("RO3003") == "rogers_other"
    assert fabmaterial.family_of_names(["Rogers RO4350B", "FR4"]) \
        == "hybrid:fr4+ro4350b"
    assert fabmaterial.family_of_names([]) == "fr4"


@pytest.mark.parametrize("family", ["ro4003c", "rogers_other",
                                    "hybrid:fr4+ro4350b"])
def test_materials_jlc_does_not_sell_are_refused_by_name(family):
    why = fabmaterial.not_offered(family)
    assert why and "JLC does not build" in why
    with pytest.raises(fabfloors.FabFloorError, match="does not build"):
        fabfloors.profile(2, 1.0, material=family)


def test_rogers_floors_are_the_hf_row_and_fr4_keys_are_unchanged():
    cls, cap = fabfloors.profile(2, 1.0, material="ro4350b")
    assert cls == "2layer_1oz_ro4350b"
    assert cap["min_trace_width_mm"] == 0.10 and cap["min_via_drill_mm"] == 0.2
    assert fabfloors.profile(2, 1.0)[0] == "2layer_1oz"
    assert fabfloors.profile(2, 1.0, material="fr4")[0] == "2layer_1oz"
    assert dfm_check.rule_key(2, 1.0, "ro4350b") == "2layer_1oz_ro4350b"
    assert dfm_check.rule_key(2, 1.0) == "2layer_1oz"


# ------------------------------------------------------------ the stackups

def test_rogers_stackups_are_the_hf_products_jlc_sells():
    ro = {n: s for n, s in STACKS["stackups"].items()
          if s.get("material_family") == "ro4350b"}
    assert sorted(s["thickness_mm"] for s in ro.values()) == \
        CAPS["materials"]["ro4350b"]["finished_thickness_mm"]
    for name, s in ro.items():
        assert fabmaterial.stackup_family(s) == "ro4350b", name
        assert s["layers"] == 2 and s["copper_finish"] == "ENIG", name
        core = [ly for ly in s["stack"] if ly["type"] != "copper"]
        assert core[0]["thickness_mm"] in \
            CAPS["materials"]["ro4350b"]["core_thickness_mm"], name
        assert core[0]["epsilon_r"] == CAPS["materials"]["ro4350b"]["dk_design"]
        assert core[0]["loss_tangent"] == CAPS["materials"]["ro4350b"]["df"]


def test_solved_profiles_are_keyed_on_dielectric_thickness():
    """The old rule ('a 2-layer board has no reference plane') was true of
    1.6 mm FR-4 and false of a 0.508 mm RO4350B board: every available
    stack gets profiles exactly when its outer dielectric is thin."""
    for name, s in STACKS["stackups"].items():
        if s.get("available") is False:
            continue
        h, _, _ = rules_gen.outer_microstrip_params(s)
        has = bool(s.get("controlled_impedance"))
        assert has == (h <= REFERENCE_PLANE_MAX_H_MM), \
            f"{name}: h {h} mm, profiles {has}"


def test_rogers_50_ohm_line_is_solved_at_design_dk():
    s = STACKS["stackups"][RO]
    se = next(c for c in s["controlled_impedance"] if c["profile"] == "se_50")
    # 20 mil RO4350B, 1 oz, mask-coated, er 3.66: ~1.05 mm (Rogers' own
    # calculator puts bare 20 mil at ~1.1 mm)
    assert 0.95 < se["width_mm"] < 1.15
    z = imp.microstrip_z0(se["width_mm"], 0.508, 0.035, 3.66)
    assert abs(z - 50) < 0.5


def test_stackup_declaring_another_family_is_an_error():
    s = dict(STACKS["stackups"][RO], material_family="fr4")
    with pytest.raises(fabmaterial.MaterialError):
        fabmaterial.stackup_family(s)


# ------------------------------------------------------------ the board

def test_board_family_reads_the_boards_own_stackup(tmp_path):
    pcb = _board(tmp_path, RO)
    assert fabmaterial.board_materials(pcb) == ["Rogers RO4350B"]
    assert fabmaterial.board_family(pcb) == "ro4350b"
    assert fabmaterial.board_family(BASE_BOARD) == "fr4"
    # KiCad's own multi-line save reads the same
    multi = tmp_path / "multi.kicad_pcb"
    multi.write_text(pcb.read_text().replace(") (", ")\n\t\t\t\t("),
                     encoding="utf-8")
    assert fabmaterial.board_dielectrics(multi)[0]["thickness_mm"] == 0.508


def test_rules_gen_gives_a_rogers_stackup_its_floors_and_geometry(tmp_path):
    out = tmp_path / "r.json"
    rc = rules_gen.main(["--layers", "2", "--stackup", RO,
                         "--out-dru", str(tmp_path / "b.kicad_dru"),
                         "--out", str(out)])
    rep = json.loads(out.read_text())
    assert rc == 0, rep
    assert rep["capability_class"] == "2layer_1oz_ro4350b"
    assert rep["material"] == "ro4350b"


def test_rules_gen_refuses_fr4_rules_for_a_rogers_board(tmp_path, capsys):
    pcb = _board(tmp_path, RO)
    rc = rules_gen.main(["--layers", "2", "--pcb", str(pcb),
                         "--out-dru", str(tmp_path / "b.kicad_dru"),
                         "--out", str(tmp_path / "r.json")])
    err = capsys.readouterr().out
    assert rc == 2
    assert "ro4350b" in err and RO in err      # names the stackup to pass


def test_rules_gen_explains_a_stackup_jlc_does_not_sell(tmp_path, capsys):
    rc = rules_gen.main(["--layers", "2", "--stackup", "JLC_HF2_RO4003C_0.508",
                         "--out", str(tmp_path / "r.json")])
    assert rc == 2
    assert "RO4003C" in capsys.readouterr().out


# ------------------------------------------------------------ the order

def _spec(**kw):
    spec = {"layers": 2, "qty": 5, "width_mm": 40, "height_mm": 30,
            "thickness_mm": 0.65, "copper_weight_oz": 1,
            "material": "ro4350b"}
    spec.update(kw)
    return spec


def test_rogers_order_goes_out_as_the_hf_product():
    p = order_submit.build_pcb_param(_spec())
    assert p["plateType"] == 5
    assert p["surfaceFinish"] == 2 and p["goldThickness"] == 2   # ENIG
    assert p["pcbColor"] == 0                                    # green
    assert "materialDetails" not in p
    assert p["impedanceFlag"] == "no"
    assert "RO4350B" in p["orderDetailsRemark"]


def test_fr4_order_is_unchanged():
    p = order_submit.build_pcb_param(_spec(material="fr4", thickness_mm=1.6))
    assert p["plateType"] == 1 and p["materialDetails"] == 0
    assert p["surfaceFinish"] == 1 and p["orderDetailsRemark"] == ""


@pytest.mark.parametrize("bad,match", [
    ({"surface_finish": "HASL"}, "surface_finish HASL"),
    ({"thickness_mm": 1.6}, "thickness_mm 1.6"),
    ({"layers": 4}, "layers 4"),
    ({"solder_mask_color": "black"}, "solder_mask_color black"),
    ({"width_mm": 600}, "exceeds"),
    ({"material": "hybrid:fr4+ro4350b"}, "does not build"),
])
def test_rogers_order_jlc_cant_build_is_refused(bad, match):
    with pytest.raises(order_submit.ApiRefused, match=match):
        order_submit.build_pcb_param(_spec(**bad))


def test_fr4_quote_for_a_rogers_board_is_not_order_ready(tmp_path):
    """SI-09: the order carries the board's laminate, and a quote priced on
    FR-4 for a Rogers-designed board stops the package."""
    pcb = _board(tmp_path, RO)
    fab = tmp_path / "fab"
    fab.mkdir()
    quote = tmp_path / "quote.json"
    q = order_quote.run(BASE_BOARD, [5], ["ENIG"], ["green"])   # an FR-4 board
    quote.write_text(json.dumps(q), encoding="utf-8")
    man = order_submit.run(pcb, fab, quote=quote, qty=5)
    assert man["status"] == "not_order_ready"
    assert man["spec_snapshot"]["material"] == "ro4350b"
    assert any("MATERIAL MISMATCH" in s for s in man["human_steps"])
    with pytest.raises(order_submit.ApiRefused, match="MATERIAL MISMATCH"):
        order_submit._api_quote(None, man, fab)


def test_rogers_quote_prices_the_hf_line_and_its_options(tmp_path):
    pcb = _board(tmp_path, RO)
    q = order_quote.run(pcb, [5], ["HASL", "ENIG"], ["green", "black"])
    assert q["spec"]["material"] == "ro4350b"
    assert q["spec"]["thickness_mm"] == 0.65
    assert {r["surface_finish"] for r in q["matrix"]} == {"ENIG"}
    assert {r["solder_mask_color"] for r in q["matrix"]} == {"green"}
    assert q["matrix"][0]["pcb"]["base"] == 47.0
    # and the order built from that quote carries the material through
    fab = tmp_path / "fab"
    fab.mkdir()
    quote = tmp_path / "quote.json"
    quote.write_text(json.dumps(q), encoding="utf-8")
    man = order_submit.run(pcb, fab, quote=quote, qty=5)
    assert "material_problems" not in man
    assert any(s.startswith("HIGH-FREQUENCY MATERIAL")
               for s in man["human_steps"])
    p = order_submit.build_pcb_param(order_submit._merged_spec(man))
    assert p["plateType"] == 5 and p["surfaceFinish"] == 2


def test_order_notes_name_the_material_and_the_solved_lines(tmp_path):
    pcb = _board(tmp_path, RO)
    path = fab_export.write_order_notes(pcb, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Rogers RO4350B" in text and RO in text
    assert "0.65 mm" in text and "ENIG" in text
    assert "NOT OFFERED" in text                      # impedance control
    assert "se_50" in text and "1.0497" in text
    assert fab_export.write_order_notes(BASE_BOARD, tmp_path / "x") is None
