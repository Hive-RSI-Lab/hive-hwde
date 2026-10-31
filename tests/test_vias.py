"""Many-layer and HDI boards (issue #317): JLC's laminations read into
stackups.yaml (lib/jlcstack.py, stackup_fetch.py) and check_vias holding
blind, buried and micro vias to them, on synthetic boards."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import board_init  # noqa: E402
import check_vias  # noqa: E402
import fabfloors  # noqa: E402
import geom  # noqa: E402
import jlcstack  # noqa: E402
import stackup_fetch  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "jlc_stackups"
STACKUPS = yaml.safe_load((REPO / "reference" / "stackups.yaml").read_text("utf-8"))


def _tpl(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))


# ------------------------------------------------------------ jlcstack

@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_every_fixture_is_its_stackups_entry(path):
    """Each 6-20 layer / HDI entry in stackups.yaml is the conversion of its
    kept JLC template, so it can be regenerated and re-checked offline."""
    name = path.stem
    tpl = _tpl(name)
    ent = STACKUPS["stackups"][name]
    got = jlcstack.entry_from_template(tpl, ent["provenance"]["verified"])
    assert got["stack"] == ent["stack"]
    assert got["layers"] == ent["layers"]
    assert got.get("vias", []) == ent.get("vias", [])
    assert got["available"] is ent["available"] is True
    assert ent["provenance"]["method"] == "jlc_calculator_template"
    assert ent["provenance"]["order_api_verified"] is False


def test_conversion_reads_cores_foils_and_bare_cores():
    tpl = _tpl("JLCH081611N2-2116")
    stack = jlcstack.stack_from_template(tpl)
    cu = [s["name"] for s in stack if s["type"] == "copper"]
    assert cu == ["F.Cu"] + [f"In{i}.Cu" for i in range(1, 7)] + ["B.Cu"]
    # copper and dielectric alternate
    kinds = [s["type"] == "copper" for s in stack]
    assert kinds == [i % 2 == 0 for i in range(len(stack))]
    spans = {(v["kind"], v["from"], v["to"]) for v in jlcstack.via_spans(tpl, 8)}
    assert ("micro", "F.Cu", "In1.Cu") in spans
    assert ("micro", "In1.Cu", "In2.Cu") in spans
    assert ("through", "F.Cu", "B.Cu") in spans


def test_plies_of_one_gap_merge_with_series_er():
    tpl = {"appointName": "T", "plateLayerNumber": 2, "basicDataList": [
        {"materialType": 1, "topConductorThick": 0.035},
        {"materialType": 0, "dielectricThick": 0.1, "dielectricConstant": 4.0,
         "materialName": "2116 RC54%"},
        {"materialType": 0, "dielectricThick": 0.1, "dielectricConstant": 3.0,
         "materialName": "1080 RC67%"},
        {"materialType": 1, "topConductorThick": 0.035}]}
    st = jlcstack.stack_from_template(tpl)
    d = st[1]
    assert d["thickness_mm"] == pytest.approx(0.2)
    # series: 0.2 / (0.1/4 + 0.1/3)
    assert d["epsilon_r"] == pytest.approx(0.2 / (0.1 / 4 + 0.1 / 3), abs=1e-3)
    assert d["plies"] == "2116 0.1 + 1080 0.1"


def test_tags_and_finished_thickness():
    assert jlcstack.tags("JLC06161H-3313A(叠构重复/不要选用/后续取消)") == \
        ["do_not_use", "to_be_withdrawn", "duplicate"]
    assert jlcstack.finished_thickness("JLC08161H-2116(通用免费/成品板厚1.6mm±10%)") == 1.6
    assert jlcstack.finished_thickness("JLC10161H-2116(通用免费/成品厚度1.57mm±10%)") == 1.57


def test_fetch_pages_and_find_prefers_the_named_twin():
    pages = [
        {"body": {"list": [{"appointName": "X", "receptionDisplayName": "无要求"}],
                  "hasNextPage": True}},
        {"body": {"list": [{"appointName": "X", "receptionDisplayName": "X(通用免费)"},
                           {"appointName": "XA", "receptionDisplayName": "XA"}],
                  "hasNextPage": False}}]
    calls = []

    def transport(payload):
        calls.append(payload["pageNum"])
        return pages[payload["pageNum"] - 1]
    assert len(stackup_fetch.fetch("X", transport)) == 3
    assert stackup_fetch.find("X", transport)["receptionDisplayName"] == "X(通用免费)"
    with pytest.raises(LookupError):
        stackup_fetch.find("Y", transport)


# ------------------------------------------------------------ reference data

def test_every_layer_count_has_a_default_and_a_capability_row():
    for n in range(6, 22, 2):
        name = STACKUPS["defaults"][n]
        s = STACKUPS["stackups"][name]
        assert s["layers"] == n and s["available"] and not s.get("vias")
        cls, row = fabfloors.profile(n, 1.0)
        assert row["min_trace_width_mm"] > 0
    for n in range(6, 34, 2):
        fabfloors.profile(n, 2.0)


def test_calculator_stackup_always_warns_it_is_not_order_verified():
    s = STACKUPS["stackups"]["JLC08161H-2116"]
    f = board_init.stackup_freshness(s)
    assert f and "order API" in f["warning"] and "JLC08161H-2116" in f["warning"]


# ------------------------------------------------------------ check_vias

def _board(tmp_path: Path, stackup: str | None, vias: list[tuple], n: int | None = None) -> Path:
    """A board of `stackup`'s copper count with its (stackup) block, and
    vias (kind, from, to, x, y, drill, size, net)."""
    s = STACKUPS["stackups"][stackup] if stackup else None
    n = n or s["layers"]
    cu = [jlcstack.copper_name(i, n) for i in range(1, n + 1)]
    ids = {"F.Cu": 0, "B.Cu": 2}
    ids.update({f"In{k}.Cu": 2 + 2 * k for k in range(1, n - 1)})
    layers = "\n".join(f'\t\t({ids[c]} "{c}" signal)' for c in cu)
    setup = board_init.build_stackup_block(s) if s else ""
    body = []
    for kind, a, b, x, y, drill, size, net in vias:
        word = "" if kind == "through" else f" {kind}"
        body.append(f'\t(via{word}\n\t\t(at {x} {y})\n\t\t(size {size})\n'
                    f'\t\t(drill {drill})\n\t\t(layers "{a}" "{b}")\n'
                    f'\t\t(net "{net}")\n\t)')
    text = (f'(kicad_pcb\n\t(version 20250114)\n\t(generator "pcbnew")\n'
            f'\t(general\n\t\t(thickness {s["thickness_mm"] if s else 1.6})\n\t)\n'
            f'\t(layers\n{layers}\n\t\t(44 "Edge.Cuts" user)\n\t)\n'
            f'\t(setup\n{setup}\t)\n'
            f'\t(net 0 "")\n\t(net 1 "GND")\n\t(net 2 "SIG")\n'
            + "\n".join(body) + "\n)\n")
    p = tmp_path / "b.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def _kinds(pcb, **kw) -> list[str]:
    viols, _ = check_vias.check(pcb, **kw)
    return sorted(v["kind"] for v in viols)


def test_geom_reads_the_via_type(tmp_path):
    p = _board(tmp_path, "JLCH081611N2-2116", [
        ("through", "F.Cu", "B.Cu", 1, 1, 0.3, 0.6, "GND"),
        ("micro", "F.Cu", "In1.Cu", 2, 1, 0.12, 0.3, "GND"),
        ("buried", "In2.Cu", "In5.Cu", 3, 1, 0.2, 0.4, "GND"),
        ("blind", "F.Cu", "In2.Cu", 4, 1, 0.2, 0.4, "GND")])
    bg = geom.load_board(p)
    assert [v.kind for v in bg.vias_of()] == ["through", "micro", "buried", "blind"]
    assert bg.vias_of()[2].layers == ("In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu")
    kinds = [d["kind"] for d in bg.stackup.dielectrics]
    assert "core" in kinds and "prepreg" in kinds


def test_a_buildable_hdi_board_passes(tmp_path):
    """Through vias plus a stacked F.Cu-In1-In2 microvia pair, as the
    JLCH081611N2-2116 build is laminated for."""
    p = _board(tmp_path, "JLCH081611N2-2116", [
        ("through", "F.Cu", "B.Cu", 10, 10, 0.3, 0.6, "GND"),
        ("micro", "F.Cu", "In1.Cu", 20, 10, 0.12, 0.3, "SIG"),
        ("micro", "In1.Cu", "In2.Cu", 20, 10, 0.12, 0.3, "SIG"),
        ("micro", "In6.Cu", "B.Cu", 30, 10, 0.12, 0.3, "GND")])
    viols, facts = check_vias.check(p)
    assert viols == []
    assert facts["stackup"] == "JLCH081611N2-2116" and facts["hdi_entry"]
    assert facts["hdi_order"] == 2


def test_hdi_faults(tmp_path):
    p = _board(tmp_path, "JLCH081611N2-2116", [
        ("micro", "F.Cu", "In2.Cu", 30, 10, 0.12, 0.3, "SIG"),    # 2 dielectrics
        ("buried", "In2.Cu", "In5.Cu", 40, 10, 0.2, 0.4, "SIG"),  # not laminated
        ("micro", "In6.Cu", "B.Cu", 50, 10, 0.05, 0.3, "GND"),    # tiny hole
        ("micro", "In6.Cu", "B.Cu", 50.2, 10, 0.12, 0.2, "SIG")])  # ring, spacing
    k = _kinds(p)
    assert k.count("via_type_span") == 1
    assert k.count("via_span_unbuilt") == 2
    assert "via_drill" in k and "via_ring" in k and "via_hole_spacing" in k
    assert "via_aspect" in k


def test_blind_and_buried_on_a_standard_lamination(tmp_path):
    p = _board(tmp_path, "JLC06161H-3313", [
        ("through", "F.Cu", "B.Cu", 10, 10, 0.3, 0.6, "GND"),
        ("blind", "F.Cu", "In2.Cu", 20, 10, 0.2, 0.45, "GND"),
        ("buried", "In1.Cu", "In3.Cu", 30, 10, 0.2, 0.45, "GND")])
    k = _kinds(p)
    assert k == ["via_needs_hdi", "via_needs_hdi", "via_spans_cross"]


def test_a_6_layer_buried_core_via_names_the_build_that_makes_it(tmp_path):
    p = _board(tmp_path, "JLC06161H-3313", [
        ("buried", "In1.Cu", "In4.Cu", 30, 10, 0.2, 0.5, "GND")])
    viols, _ = check_vias.check(p)
    assert [v["kind"] for v in viols] == ["via_needs_hdi"]
    assert "JLCH061611Y1-1078" in viols[0]["builds"]
    # on that build it is fine
    p2 = _board(tmp_path, "JLCH061611Y1-1078", [
        ("buried", "In1.Cu", "In4.Cu", 30, 10, 0.2, 0.5, "GND")])
    assert _kinds(p2) == []


def test_no_lamination_named(tmp_path):
    p = _board(tmp_path, None, [
        ("blind", "F.Cu", "In1.Cu", 30, 10, 0.3, 0.6, "GND")], n=4)
    assert _kinds(p) == ["via_no_lamination"]


def test_through_only_boards_raise_nothing(tmp_path):
    for name in ("JLC2313_1.6", "JLC04161H-1080B", "JLC08161H-2116",
                 "JLC16161H-2313", "JLC20201H-2313"):
        p = _board(tmp_path, name, [("through", "F.Cu", "B.Cu", 1, 1, 0.3, 0.6, "GND")])
        assert _kinds(p) == [], name


def test_deep_through_hole(tmp_path):
    p = _board(tmp_path, "JLC20201H-2313", [
        ("through", "F.Cu", "B.Cu", 1, 1, 0.1, 0.3, "GND")])
    assert _kinds(p) == ["via_aspect"]


def test_every_default_and_hdi_stack_is_balanced(tmp_path):
    for name, s in STACKUPS["stackups"].items():
        if s.get("available") is False:
            continue
        p = _board(tmp_path, name, [])
        assert _kinds(p) == [], name


def test_unbalanced_stack_warns(tmp_path):
    s = json.loads(json.dumps(STACKUPS["stackups"]["JLC06161H-3313"]))
    s["stack"][1]["thickness_mm"] = 0.3
    p = tmp_path / "st.yaml"
    p.write_text(yaml.safe_dump({"stackups": {"X": s}}), encoding="utf-8")
    b = _board(tmp_path, "JLC06161H-3313", [])
    t = b.read_text().replace("(thickness 0.0994)", "(thickness 0.3)", 1)
    b.write_text(t)
    viols, _ = check_vias.check(b, stackups_file=p)
    assert [(v["kind"], v["severity"]) for v in viols] == [("stackup_unbalanced", "warning")]
