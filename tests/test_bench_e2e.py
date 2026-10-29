"""E2E bench stage (backlog item 1): bounds contract, scoring maths, the
bench's own SPICE corner decks, and the one cheap smoke case the default
check runs (the frozen bb-ldo workspace, offline).  The full e2e run -
calibration boards from the boards repo, held-out briefs against a finished
workspace - is opt-in: `bench.py --stage E2E --fixture e2e_<brief>`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
for _p in (SCRIPTS, SCRIPTS / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bench  # noqa: E402
import e2elib  # noqa: E402
import env  # noqa: E402

E2E_DIR = REPO / "tests" / "fixtures" / "stages" / "e2e"
BASELINES = REPO / "tests" / "fixtures" / "stages" / "baselines"


# ------------------------------------------------------------- bounds

def test_every_brief_has_loadable_hidden_bounds():
    briefs = sorted(p.parent.name for p in E2E_DIR.glob("*/brief.md"))
    # the brief asks for 5-10 HELD-OUT briefs; calibration twins of shipped
    # boards (args.calibration_board) check the scorer and do not count
    manifest = yaml.safe_load((E2E_DIR.parent / "manifest.yaml")
                              .read_text(encoding="utf-8"))["fixtures"]
    e2e = {f: e for f, e in manifest.items() if e["stage"] == "E2E"}
    assert sorted(f"e2e_{b}" for b in briefs) == sorted(e2e)
    held_out = [f for f, e in e2e.items()
                if not (e.get("args") or {}).get("calibration_board")]
    assert 5 <= len(held_out) <= 10, held_out
    for b in briefs:
        bounds = e2elib.load_bounds(E2E_DIR / b / "bounds.yaml")
        assert bounds["brief"] == f"e2e_{b}"
        # the brief the agent sees never quotes a hidden number's key
        text = (E2E_DIR / b / "brief.md").read_text(encoding="utf-8")
        assert "target_usd" not in text and "area_mm2" not in text


def _bounds(tmp_path, body):
    p = tmp_path / "bounds.yaml"
    p.write_text(body, encoding="utf-8")
    return p


GOOD = """nets: [{id: v, pattern: "V"}]
layout: {area_mm2_max: 100}
cost: {target_usd: 1.0}
"""


@pytest.mark.parametrize("body,why", [
    ("layout: {area_mm2_max: 100}\ncost: {target_usd: 1.0}\n", "net"),
    ('nets: [{id: v, pattern: "V"}]\ncost: {target_usd: 1.0}\n', "area"),
    ('nets: [{id: v, pattern: "V"}]\nlayout: {area_mm2_max: 1}\n', "target_usd"),
    (GOOD + "spice: [{kind: divider, top: nope, min: 1}]\n", "not a net id"),
    (GOOD + "spice: [{kind: bandpass, min: 1}]\n", "kind"),
    (GOOD + "spice: [{kind: divider, top: v}]\n", "min/max"),
    (GOOD + "extra: 1\n", "unknown keys"),
])
def test_bounds_refuse_a_hole_that_would_score_free(tmp_path, body, why):
    with pytest.raises(e2elib.E2EError, match=why):
        e2elib.load_bounds(_bounds(tmp_path, body))


AUTO_NAMED = ["Net-(J2-Pin_1)", "unconnected-(J1-Pin_3-Pad3)", "Net-(Q1-B)",
              "Net-(D1-K)"]


@pytest.mark.parametrize("brief", sorted(p.name for p in E2E_DIR.iterdir()
                                         if (p / "bounds.yaml").is_file()))
def test_net_patterns_never_match_kicad_auto_names(brief):
    """A pattern that finds KiCad's auto-named nets scores a missing net free."""
    bounds = e2elib.load_bounds(E2E_DIR / brief / "bounds.yaml")
    for net in bounds["nets"]:
        hit, _ = e2elib.match_net(AUTO_NAMED, net["pattern"])
        assert hit is None, f"{brief} net {net['id']} matches {hit}"


def test_relay_ctrl_is_not_the_supply():
    bounds = e2elib.load_bounds(E2E_DIR / "relay_driver" / "bounds.yaml")
    ctrl = next(n for n in bounds["nets"] if n["id"] == "ctrl")["pattern"]
    assert e2elib.match_net(["VIN", "/VIN", "Net-(J2-Pin_1)"], ctrl)[0] is None
    assert e2elib.match_net(["/CTRL"], ctrl)[0] == "/CTRL"


def test_good_bounds_load(tmp_path):
    assert e2elib.load_bounds(_bounds(tmp_path, GOOD))["nets"][0]["id"] == "v"


# ------------------------------------------------------------- maths

@pytest.mark.parametrize("value,bound,want", [
    (5, 10, 1.0), (10, 10, 1.0), (15, 10, 0.5), (20, 10, 0.0), (40, 10, 0.0),
    (0, 0, 1.0), (1, 0, 0.5), (3, 0, 0.25)])
def test_le_score(value, bound, want):
    assert e2elib.le_score(value, bound) == pytest.approx(want)


def test_price_at_picks_the_break_at_or_under_the_quantity():
    br = [{"qty": 1, "price": 1.0}, {"qty": 50, "price": 0.5},
          {"qty": 500, "price": 0.2}]
    assert e2elib.price_at(br, 1) == 1.0
    assert e2elib.price_at(br, 49) == 1.0
    assert e2elib.price_at(br, 50) == 0.5
    assert e2elib.price_at(br, 10_000) == 0.2
    assert e2elib.price_at([], 10, flat=0.3) == 0.3
    assert e2elib.price_at([], 10) is None


def test_cost_counts_refs_under_either_key_and_zero_priced_scores_zero(tmp_path):
    bounds = {"cost": {"qty": 10, "target_usd": 1.0}}
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"parts": [
        {"refs": ["R1", "R2"], "price_breaks": [{"qty": 1, "price": 0.1}]},
        {"refdes": ["U1"], "price": 0.5}]}), encoding="utf-8")
    checks, facts = e2elib.check_cost(bounds, pj)
    assert facts["bom_usd"] == pytest.approx(0.7)
    assert checks[0]["score"] == 1.0
    pj.write_text(json.dumps({"parts": [{"refs": ["U1"], "mpn": "X"}]}),
                  encoding="utf-8")
    checks, facts = e2elib.check_cost(bounds, pj)
    assert checks[0]["score"] == 0.0 and facts["unpriced"] == ["X"]
    # a cheap priced line beside an unpriced one must not score a full bill
    pj.write_text(json.dumps({"parts": [
        {"refs": ["R1"], "price": 0.01},
        {"refs": ["U1"], "mpn": "REG"}]}), encoding="utf-8")
    checks, facts = e2elib.check_cost(bounds, pj)
    assert facts["bom_usd"] == pytest.approx(0.01)
    assert checks[0]["score"] == pytest.approx(0.5)
    assert checks[0]["priced_lines"] == 1 and facts["unpriced"] == ["REG"]
    # the bare arm's shape: a top-level list, priced as unit_price
    pj.write_text(json.dumps([
        {"refs": ["R1", "R2"], "unit_price": 0.1},
        {"refs": ["U1"], "price": 0.5}]), encoding="utf-8")
    checks, facts = e2elib.check_cost(bounds, pj)
    assert facts["bom_usd"] == pytest.approx(0.7)
    assert checks[0]["priced_lines"] == 2


@pytest.mark.parametrize("value,volts", [
    ("TPS54302DDCR", 0.596), ("TPS5430DDAR", 1.221), ("TPS5450DDAR", 1.221),
    ("TPS54360DDAR", 0.8), ("TPS54540DDAR", 0.8), ("TPS54331DR", 0.8),
    ("LMR33630ADDAR", 1.0), ("TPS54202DDCR", None), ("TPS543620", None)])
def test_vref_table_matches_whole_part_numbers(value, volts):
    """An unanchored TPS543 prefix gave a TPS54302 (0.596 V) 1.221 V."""
    for brief in ("buck_5v", "buck_3v3"):
        spec = e2elib.load_bounds(E2E_DIR / brief / "bounds.yaml")["spice"][0]
        got, _ = e2elib._vref(spec, {"components": {"U1": {"value": value}}})
        assert got == volts, (brief, value)


def test_board_without_an_outline_scores_no_area(tmp_path):
    import geom
    # the frozen bb-ldo board with its Edge.Cuts drawing moved to Dwgs.User
    src = E2E_DIR / "ldo_3v3" / "ws" / "kicad" / "PCB-0012-A_bb-ldo.kicad_pcb"
    text = src.read_text(encoding="utf-8")
    assert '(layer "Edge.Cuts")' in text
    pcb = tmp_path / "x.kicad_pcb"
    pcb.write_text(text.replace('(layer "Edge.Cuts")', '(layer "Dwgs.User")'),
                   encoding="utf-8")
    got = e2elib.check_area({"layout": {"area_mm2_max": 100}},
                            geom.BoardGeom.from_file(pcb))
    assert got[0]["score"] == 0.0 and got[0]["note"] == "no closed board outline"


def test_category_mean_skips_unscored_checks():
    checks = [e2elib._check("a", "electrical", 1.0),
              e2elib._check("b", "electrical", 0.0),
              e2elib._check("c", "electrical", None),
              e2elib._check("d", "layout", 0.5)]
    cats = e2elib.category_scores(checks)
    assert cats == {"electrical": 0.5, "layout": 0.5, "cost": 1.0}


# ------------------------------------------------------------- SPICE decks

def _netlist(tmp_path, comps, nets):
    """A minimal kicadsexpr export: comps {ref: value}, nets {name: [ref.pin]}."""
    c = " ".join(f'(comp (ref "{r}") (value "{v}"))' for r, v in comps.items())
    n = " ".join(
        f'(net (code "{i}") (name "{name}") '
        + " ".join(f'(node (ref "{p.split(".")[0]}") (pin "{p.split(".")[1]}"))'
                   for p in pins) + ")"
        for i, (name, pins) in enumerate(nets.items(), 1))
    p = tmp_path / "t.net"
    p.write_text(f"(export (version \"E\") (components {c}) (nets {n}))",
                 encoding="utf-8")
    return e2elib.simlib.parse_netlist(p)


def _batt(tmp_path):
    # 4S monitor: 150k/33k (1 %) divider, 1 uF (10 %) across the bottom leg,
    # and a pull-up to another rail the stop set must keep out of the deck
    return _netlist(tmp_path,
                    {"R1": "150k 1%", "R2": "33k 1%", "C1": "1uF 10%",
                     "R9": "10k", "J1": "CONN"},
                    {"+VBAT": ["R1.1", "J1.1"], "/SENSE": ["R1.2", "R2.1",
                                                            "C1.1", "R9.1"],
                     "GND": ["R2.2", "C1.2", "J1.2"], "+3V3": ["R9.2"]})


def test_divider_deck_holds_every_corner_and_keeps_other_rails_out(tmp_path):
    parsed = _batt(tmp_path)
    deck = e2elib.build_divider(parsed, "+VBAT", "/SENSE", "GND", {"+3V3"}, 1)
    assert [p["ref"] for p in deck["parts"]] == ["R1", "R2"]
    assert len(deck["measures"]) == 4
    assert "R9" not in deck["cir"]
    open_deck = e2elib.build_divider(parsed, "+VBAT", "/SENSE", "GND", set(), 1)
    assert "R9" in open_deck["cir"]          # without the stop it leaks in


def test_rc_deck_needs_a_shunt_cap(tmp_path):
    parsed = _netlist(tmp_path, {"R1": "10k"},
                      {"A": ["R1.1"], "B": ["R1.2"], "GND": []})
    assert "error" in e2elib.build_rc(parsed, "A", "B", "GND", set(), 1, 10)


@pytest.mark.smoke
def test_spice_checks_at_corners(tmp_path):
    dll = env.find_ngspice_dll()
    if dll is None:
        pytest.skip("no ngspice library on this host")
    parsed = _batt(tmp_path)
    bounds = {"nets": [{"id": "vbat", "pattern": "VBAT"},
                       {"id": "vs", "pattern": "SENSE"},
                       {"id": "gnd", "pattern": "^GND$"},
                       {"id": "v33", "pattern": "3V3"}],
              "spice": [
                  {"id": "full", "kind": "divider", "top": "vbat",
                   "mid": "vs", "bottom": "gnd", "scale": 16.8,
                   "min": 2.97, "max": 3.30},
                  {"id": "aa", "kind": "rc_lowpass", "in": "vbat",
                   "out": "vs", "gnd": "gnd", "min": 5, "max": 7}]}
    _, netmap = e2elib.check_nets(bounds, parsed)
    div, rc = e2elib.check_spice(bounds, parsed, netmap, tmp_path, dll)
    # nominal 16.8 * 33/183 = 3.0295 V; the 1 % corners span about +/-1.6 %
    assert div["corners"] == 4 and div["score"] == 1.0
    assert div["value"]["min"] == pytest.approx(3.0295 * 0.984, rel=2e-3)
    assert div["value"]["max"] == pytest.approx(3.0295 * 1.016, rel=2e-3)
    # fc = 1/(2 pi (150k||33k) 1u) = 5.87 Hz nominal; the -10 % cap corners
    # land near 6.6 Hz and the +10 % ones near 5.3 Hz: all inside 5..7
    assert rc["corners"] == 8 and rc["score"] == 1.0
    assert 5.2 < rc["value"]["min"] < 5.4 and 6.5 < rc["value"]["max"] < 6.7
    bounds["spice"][1]["max"] = 6.0          # now the low-C corners fail
    _, rc = e2elib.check_spice(bounds, parsed, netmap, tmp_path, dll)
    assert rc["score"] == 0.5


# ------------------------------------------------------------- the stage

def test_smoke_case_scores_offline(monkeypatch):
    """The default check's cheap case: the frozen bb-ldo workspace against
    its brief, no kicad-cli (so no ERC/DRC/SPICE legs)."""
    monkeypatch.setattr(bench, "_find_cli", lambda: None)
    payload, _ = bench.run(["--stage", "E2E", "--fixture", "e2e_ldo_3v3"])
    assert payload["composite_inputs"] == "offline"
    assert payload["metrics_live"] is None
    got = {c["id"]: c["score"] for c in payload["metrics"]["checks"]}
    assert got["net:vin"] == got["net:vout"] == got["net:gnd"] == 1.0
    assert got["layout:legality"] == 1.0
    assert 0 < got["layout:area"] < 1          # 1201 mm2 against 700
    assert 0 < got["cost:bom"] < 1             # $1.05 against $0.60
    cats = payload["e2e"]["categories"]
    pen = payload["penalties"]
    assert pen["cost_shortfall"] == pytest.approx(1 - cats["cost"], abs=1e-4)
    assert payload["composite"] == pytest.approx(
        100 - 45 * pen["electrical_shortfall"] - 20 * pen["layout_shortfall"]
        - 35 * pen["cost_shortfall"], abs=0.01)


def test_held_out_brief_without_a_workspace_scores_zero(monkeypatch):
    monkeypatch.setattr(bench, "_find_cli", lambda: None)
    payload, _ = bench.run(["--stage", "E2E", "--fixture", "e2e_buck_3v3"])
    assert payload["composite"] == 0.0
    assert payload["metrics"]["workspace"] is None


def test_workspace_override_is_scored(monkeypatch, tmp_path):
    """--artifact is how a driver scores its run of a held-out brief."""
    monkeypatch.setattr(bench, "_find_cli", lambda: None)
    ws = tmp_path / "ws"
    (ws / "kicad").mkdir(parents=True)
    src = E2E_DIR / "ldo_3v3" / "ws" / "kicad" / "PCB-0012-A_bb-ldo.net"
    (ws / "kicad" / "x.net").write_bytes(src.read_bytes())
    payload, _ = bench.run(["--stage", "E2E", "--fixture", "e2e_usbc_ldo",
                            "--artifact", str(ws)])
    got = {c["id"]: c["score"] for c in payload["metrics"]["checks"]}
    assert got["net:vout"] == 1.0 and got["net:cc1"] == 0.0
    assert got["layout:area"] == 0.0 and got["cost:bom"] == 0.0


@pytest.mark.smoke
def test_smoke_case_live_matches_baseline():
    if bench._find_cli() is None:
        pytest.skip("no kicad-cli")
    payload, _ = bench.run(["--stage", "E2E", "--fixture", "e2e_ldo_3v3"])
    base = json.loads((BASELINES / "e2e_ldo_3v3.score.json")
                      .read_text(encoding="utf-8"))
    assert payload["composite_inputs"] == "full"
    assert payload["composite"] == base["composite"]
