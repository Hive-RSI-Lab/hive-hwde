"""Vendors beyond JLCPCB (docs/vendors.md): the profiles, fit, package, the
ledger's vendor fact, and dfm_check / order_quote --vendor."""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import dfm_check  # noqa: E402
import fabmaterial  # noqa: E402
import ledgerlib  # noqa: E402
import order_quote  # noqa: E402
import vendor  # noqa: E402
import vendors  # noqa: E402

LED = REPO / "boards" / "hwde-led-test"
LED_PCB = LED / "kicad" / "hwde-led-test.kicad_pcb"


def _profile(**over) -> dict:
    prof = {
        "meta": {"id": "testfab", "name": "TestFab",
                 "services": {"pcb": True, "pcba": True},
                 "quote_url": "https://example.invalid/quote",
                 "source_urls": ["https://example.invalid/caps"],
                 "verified": "2026-10-31"},
        "board": {"layers": [2, 4], "max_size_mm": [100, 80],
                  "min_size_mm": [5, 5], "thickness_options_mm": [1.6],
                  "castellated": False, "via_tenting": "supported"},
        "design_rules": {"2layer_1oz": {
            "min_trace_width_mm": 0.15, "min_clearance_mm": 0.15,
            "min_hole_diameter_mm": 0.3, "min_annular_ring_mm": 0.15,
            "min_copper_to_edge_mm": 0.3, "min_hole_to_hole_mm": 0.5,
            "min_hole_to_edge_mm": 0.5, "min_silk_width_mm": 0.15,
            "min_solder_mask_dam_mm": 0.1, "min_via_drill_mm": 0.3,
            "min_via_diameter_mm": 0.6}},
        "assembly": {"sides": ["top"], "min_board_mm": [10, 10],
                     "min_pitch_mm": 0.5, "part_number": "mpn",
                     "sourcing": "turnkey", "distributors": ["digikey"],
                     "rotation": "kicad",
                     "bom": {"columns": ["Item #", "Designator", "Qty",
                                         "Manufacturer", "Mfg Part #",
                                         "Package/Footprint", "Type",
                                         "Mystery"]},
                     "cpl": {"columns": ["Designator", "Mid X", "Mid Y",
                                         "Layer", "Rotation"],
                             "side_labels": {"top": "T", "bottom": "B"}}},
        "pricing": {"model": "per_area", "unit": "in2", "copies": 3,
                    "rate": {2: 5.0, 4: 10.0}},
    }
    for k, v in over.items():
        prof[k] = v
    return prof


@pytest.fixture
def vroot(tmp_path, monkeypatch):
    """A vendor folder holding the real profiles plus TestFab."""
    root = tmp_path / "vendors"
    shutil.copytree(vendors.VENDORS, root)
    # jlcpcb.yaml points at ../jlc_*.yaml
    for f in ("jlc_capabilities.yaml", "jlc_pricing.yaml"):
        shutil.copy(vendors.REFERENCE / f, tmp_path / f)
    (root / "testfab.yaml").write_text(yaml.safe_dump(_profile()),
                                       encoding="utf-8")
    monkeypatch.setattr(vendors, "VENDORS", root)
    return root


# ------------------------------------------------------------- profiles

@pytest.mark.parametrize("vid", vendors.ids())
def test_every_profile_is_sourced_and_usable(vid):
    prof = vendors.load(vid)
    m = prof["meta"]
    assert m["name"] and m.get("quote_url", "").startswith("http")
    assert m["source_urls"] and all(u.startswith("http")
                                    for u in m["source_urls"])
    assert str(m["verified"])[:2] == "20"            # a date it was copied
    assert m.get("services", {}).get("pcb") is not None
    assert prof["board"].get("via_tenting", "unknown") in (
        "supported", "not_supported", "unknown")
    for key, row in prof["capabilities"]["design_rules"].items():
        layers, oz = key.split("layer_")
        # a non-FR-4 laminate's row carries its family (lib/fabmaterial.py):
        # 2layer_1oz_ro4350b
        oz, _, family = oz.partition("oz")
        assert int(layers) >= 1 and float(oz) > 0, key
        assert not family or family.lstrip("_") in fabmaterial.materials(), key
        for r in vendors.REQUIRED_RULES:
            assert 0.02 < float(row[r]) < 2.0, (vid, key, r, row[r])
    asm = prof.get("assembly")
    if asm and m["services"].get("pcba"):
        assert asm.get("part_number") in ("mpn", "lcsc")
        assert asm.get("rotation") in ("kicad", "jlc")
        for part in ("bom", "cpl"):
            for col in asm.get(part, {}).get("columns", []):
                assert vendor.meaning(col), (vid, part, col)
    assert prof["pricing"]["model"] in ("table", "per_area", "quote_only")


def test_jlc_profile_is_jlcs_own_tables():
    prof = vendors.load("jlcpcb")
    caps = yaml.safe_load((vendors.REFERENCE / "jlc_capabilities.yaml")
                          .read_text(encoding="utf-8"))
    assert prof["capabilities"]["design_rules"] == caps["design_rules"]
    assert prof["capabilities"]["castellated"] == caps["castellated"]
    assert prof["pricing"]["pcb"]["base"][2][5] == 2.0
    assert vendors.capabilities_path("jlcpcb").name == "jlc_capabilities.yaml"


def test_unknown_vendor_names_the_ones_there_are():
    with pytest.raises(vendors.VendorError, match="jlcpcb"):
        vendors.load("nosuchfab")


def test_inline_profile_is_its_own_capabilities_file(vroot):
    assert vendors.capabilities_path("testfab") == vroot / "testfab.yaml"
    assert "castellated" not in vendors.load("testfab")["capabilities"]


# --------------------------------------------------------------- prices

def test_per_area_price_counts_multiples_of_copies():
    pr = _profile()["pricing"]
    inch = 25.4
    one = order_quote.area_cost(pr, 2, 3, inch, inch)
    assert one["total"] == 5.0 and one["base_at_qty"] == 3
    assert order_quote.area_cost(pr, 2, 4, inch, inch)["total"] == 10.0
    assert order_quote.area_cost(pr, 4, 3, 2 * inch, inch)["total"] == 20.0
    floor = dict(pr, min_price=12.0, setup=1.0)
    assert order_quote.area_cost(floor, 2, 1, inch, inch)["total"] == 12.0
    with pytest.raises(ValueError, match="6-layer"):
        order_quote.area_cost(pr, 6, 3, inch, inch)


def test_order_quote_vendor_prices_from_its_profile(vroot):
    rep = order_quote.run(LED_PCB, [3, 6], ["HASL", "ENIG"], ["green"],
                          vendor="testfab")
    assert rep["vendor"] == "testfab"
    assert rep["authoritative_quote_url"] == "https://example.invalid/quote"
    assert [r["qty"] for r in rep["matrix"]] == [3, 6]   # one area rate
    assert "reference/vendors/testfab.yaml" in rep["disclaimer"]
    with pytest.raises(ValueError, match="assembly prices"):
        order_quote.run(LED_PCB, [3], ["HASL"], ["green"], assembly=True,
                        vendor="testfab")


def test_quote_only_vendor_gives_no_figure(vroot):
    (vroot / "testfab.yaml").write_text(yaml.safe_dump(
        _profile(pricing={"model": "quote_only"})), encoding="utf-8")
    rep = order_quote.run(LED_PCB, [5], ["HASL"], ["green"],
                          vendor="testfab")
    assert rep["status"] == "quote_only" and rep["matrix"] == []
    assert rep["estimated"] is False


# ------------------------------------------------------------------ fit

BOARD = {"layers": 2, "copper_oz": 1.0, "width_mm": 50.0, "height_mm": 40.0,
         "thickness_mm": 1.6, "castellated_pads": 0, "blind_buried_vias": [],
         "tented_vias": 0, "bottom_smd_refs": [], "min_pitch_mm": 0.65, "min_pitch_ref": "U1"}


def _status(rows):
    return {r["check"]: r["status"] for r in rows}


def test_limits_pass_a_board_inside_them(vroot):
    rows = vendor.limits(vendors.load("testfab"), BOARD, assembly=True)
    assert set(_status(rows).values()) == {"pass"}


@pytest.mark.parametrize("over,check,status", [
    ({"layers": 6}, "layers", "fail"),
    ({"layers": 4}, "rules", "fail"),        # offered, but no minimums
    ({"width_mm": 90.0, "height_mm": 90.0}, "max_size", "fail"),
    ({"width_mm": 75.0, "height_mm": 99.0}, "max_size", "pass"),  # rotated
    ({"thickness_mm": 1.0}, "thickness", "fail"),
    ({"copper_oz": 2.0}, "rules", "fail"),
    ({"castellated_pads": 4}, "castellated", "fail"),
    ({"blind_buried_vias": ["F.Cu-In1.Cu"]}, "blind_buried_vias", "unknown"),
    ({"bottom_smd_refs": ["C9"]}, "bottom_side", "fail"),
    ({"min_pitch_mm": 0.4}, "pitch", "fail"),
    ({"width_mm": 8.0, "height_mm": 30.0}, "assembly_min_size", "fail"),
])
def test_limits_catch_what_the_vendor_cant_build(vroot, over, check, status):
    rows = vendor.limits(vendors.load("testfab"), {**BOARD, **over},
                         assembly=True)
    assert _status(rows)[check] == status


@pytest.mark.parametrize("key,status", [
    ("supported", "pass"), ("not_supported", "fail"), ("unknown", "unknown"),
    (None, "unknown"),                       # left out of the profile
])
def test_tented_vias_are_held_to_the_vendor(vroot, key, status):
    prof = _profile()
    prof["board"].pop("via_tenting")
    if key:
        prof["board"]["via_tenting"] = key
    (vroot / "testfab.yaml").write_text(yaml.safe_dump(prof), "utf-8")
    prof = vendors.load("testfab")
    rows = vendor.limits(prof, {**BOARD, "tented_vias": 3}, assembly=True)
    assert _status(rows)["via_tenting"] == status
    assert "via_tenting" not in _status(vendor.limits(prof, BOARD, assembly=True))


def test_tented_vias_are_read_from_the_board(tmp_path):
    text = LED_PCB.read_text("utf-8")
    assert vendor.board_facts(LED_PCB)["tented_vias"] > 0   # KiCad's default
    open_vias = tmp_path / "open.kicad_pcb"
    open_vias.write_text(text.replace(
        "(setup", "(setup (tenting (front no) (back no))", 1), "utf-8")
    assert vendor.board_facts(open_vias)["tented_vias"] == 0


def test_aisler_refuses_tented_vias():
    rows = vendor.limits(vendors.load("aisler"),
                         {**BOARD, "tented_vias": 1}, assembly=False)
    assert _status(rows)["via_tenting"] == "fail"


def test_no_assembly_service_fails_an_assembled_build(vroot):
    prof = _profile()
    prof["meta"]["services"]["pcba"] = False
    (vroot / "testfab.yaml").write_text(yaml.safe_dump(prof), "utf-8")
    rows = vendor.limits(vendors.load("testfab"), BOARD, assembly=True)
    assert _status(rows)["service_pcba"] == "fail"
    assert "service_pcba" not in _status(
        vendor.limits(vendors.load("testfab"), BOARD, assembly=False))


def test_fit_runs_dfm_at_each_vendors_minimums(vroot, monkeypatch):
    monkeypatch.setenv("HWDE_LEDGER_WRITE", "0")
    tight = _profile()
    row = tight["design_rules"]["2layer_1oz"]
    row["min_trace_width_mm"] = row["min_clearance_mm"] = 0.6
    (vroot / "testfab.yaml").write_text(yaml.safe_dump(tight), "utf-8")
    rep = vendor.fit(LED_PCB, LED / "fab", ["jlcpcb", "testfab"],
                     assembly=True, qtys=(5,))
    by = {r["vendor"]: r for r in rep["vendors"]}
    assert by["jlcpcb"]["verdict"] == "fits" and rep["fits"] == ["jlcpcb"]
    assert by["testfab"]["verdict"] == "fail"
    assert by["testfab"]["dfm"]["errors"]
    assert any("TestFab minimum" in m for m in by["testfab"]["dfm"]["examples"])
    assert by["testfab"]["price"]["boards_only"]  # no PCBA prices
    assert "| TestFab | fail |" in vendor.fit_table(rep)


def test_dfm_check_vendor_without_castellation_rules_flags_castellations(
        vroot, monkeypatch):
    import castellation
    monkeypatch.setattr(castellation, "holes", lambda bg: [(1.0, 1.0, 0.5)])
    monkeypatch.setattr(castellation, "refs", lambda pcb: ["J1"])
    rep = dfm_check.run(LED_PCB, fab_dir=LED / "fab", polarity=False,
                        skip=("zone_fill", "placement", "copper", "drill",
                              "silk"), vendor="testfab")
    kinds = [v["kind"] for v in rep["violations"]]
    assert kinds == ["castellated_rules_unknown"]
    assert rep["vendor"] == "testfab"


# ------------------------------------------------ the gate's dfm vendor

STACKUPS = yaml.safe_load((REPO / "reference" / "stackups.yaml")
                          .read_text(encoding="utf-8"))["stackups"]


def _ws_board(root: Path, stackup: str | None = None,
              choice: str | None = None) -> Path:
    """A workspace's kicad/b.kicad_pcb carrying `stackup`'s (stackup) block
    (none when None), and a state.json naming `choice` when given."""
    import board_init
    import jlcstack
    s = STACKUPS[stackup] if stackup else None
    n = s["layers"] if s else 2
    ids = {"F.Cu": 0, "B.Cu": 2}
    ids.update({f"In{k}.Cu": 2 + 2 * k for k in range(1, n - 1)})
    layers = "\n".join(f'\t\t({ids[c]} "{c}" signal)' for c in
                       (jlcstack.copper_name(i, n) for i in range(1, n + 1)))
    setup = board_init.build_stackup_block(s) if s else ""
    pcb = root / "ws" / "kicad" / "b.kicad_pcb"
    pcb.parent.mkdir(parents=True)
    pcb.write_text(
        f'(kicad_pcb\n\t(version 20250114)\n\t(generator "pcbnew")\n'
        f'\t(general\n\t\t(thickness {s["thickness_mm"] if s else 1.6})\n\t)\n'
        f'\t(layers\n{layers}\n\t\t(44 "Edge.Cuts" user)\n\t)\n'
        f'\t(setup\n{setup}\t)\n\t(net 0 "")\n)\n', encoding="utf-8")
    if choice:
        (root / "ws" / "state.json").write_text(
            json.dumps({"vendor": choice}), encoding="utf-8")
    return pcb


@pytest.mark.parametrize("stackup,vid", [
    ("OSHPARK-4L-1.6-FR408HR", "oshpark"),
    ("AISLER-4L-1.6-1080", "aisler"),
    ("PCBWAY-4L-1.6-7628", "pcbway"),
])
def test_gate_dfm_holds_a_board_to_its_stackups_fab(tmp_path, stackup, vid):
    import gate
    assert gate.dfm_vendor(_ws_board(tmp_path, stackup)) == (vid, "stackup")


def test_gate_dfm_chosen_vendor_wins_over_the_stackup(tmp_path):
    import gate
    pcb = _ws_board(tmp_path, "OSHPARK-4L-1.6-FR408HR", choice="aisler")
    assert gate.dfm_vendor(pcb) == ("aisler", "state.json")


@pytest.mark.parametrize("stackup,choice,src", [
    ("JLC04161H-7628G", None, "stackup"),                # JLC's own build
    ("OSHPARK-4L-1.6-FR408HR", "jlcpcb", "state.json"),  # JLC chosen
    (None, None, "default"),                             # no stack block
])
def test_gate_dfm_jlc_is_the_default_run(tmp_path, stackup, choice, src):
    import gate
    pcb = _ws_board(tmp_path, stackup, choice=choice)
    assert gate.dfm_vendor(pcb) == (None, src)


def test_gate_dfm_vendor_of_an_unreadable_board_is_the_default(tmp_path):
    """dfm_check itself reports the board it can't read; the vendor lookup
    must not be what fails."""
    import gate
    pcb = tmp_path / "ws" / "kicad" / "b.kicad_pcb"
    assert gate.dfm_vendor(pcb) == (None, "default")
    pcb.parent.mkdir(parents=True)
    pcb.write_text("(kicad_pcb (version", encoding="utf-8")
    assert gate.dfm_vendor(pcb) == (None, "default")


def test_gate_run_dfm_passes_the_vendor_and_says_where_from(tmp_path,
                                                            monkeypatch):
    import gate
    seen: list[dict] = []
    monkeypatch.setattr(dfm_check, "run", lambda pcb, **kw:
                        seen.append(kw) or {"status": "pass"})
    rep = gate.run_dfm(_ws_board(tmp_path, "OSHPARK-4L-1.6-FR408HR"))
    assert seen[-1]["vendor"] == "oshpark"
    assert (rep["vendor"], rep["vendor_from"]) == ("oshpark", "stackup")
    rep = gate.run_dfm(_ws_board(tmp_path / "j", "JLC04161H-7628G"))
    assert "vendor" not in seen[-1]
    assert (rep["vendor"], rep["vendor_from"]) == ("jlcpcb", "stackup")


def test_every_stackup_fab_has_a_vendor_profile():
    """The gate's dfm takes a board's minimums from its stackup's fab, so
    every fab a stackups.yaml build names must have a profile to load."""
    named = {e["vendor"] for e in STACKUPS.values() if e.get("vendor")}
    assert named and named <= set(vendors.ids()), named - set(vendors.ids())


# -------------------------------------------------------------- package

@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_LEDGER_WRITE", "1")
    w = tmp_path / "boards" / "hwde-led-test"
    for d in ("fab", "kicad", "parts"):
        shutil.copytree(LED / d, w / d)
    shutil.copy(LED / "state.json", w / "state.json")
    return w


def _csv(p: Path) -> list[dict]:
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_package_writes_the_vendors_columns(vroot, ws):
    rep = vendor.package(ws, "testfab")
    out = ws / "fab" / "testfab"
    assert rep["files"] == ["gerbers.zip", "BOM.csv", "CPL.csv", "ORDER.md"]
    bom = _csv(out / "BOM.csv")
    full = _csv(ws / "fab" / "BOM-full.csv")
    placed = [r for r in full if r["Assembly Class"] in ("smt_placed", "tht")]
    assert len(bom) == len(placed)
    c1 = next(r for r in bom if r["Designator"] == "C1")
    assert c1["Mfg Part #"] == "CL10A475KO8NNNC" and c1["Type"] == "SMD"
    assert c1["Manufacturer"].startswith("Samsung")
    assert c1["Mystery"] == ""
    assert any("Mystery" in w for w in rep["warnings"])
    cpl = {r["Designator"]: r for r in _csv(out / "CPL.csv")}
    assert cpl["C1"]["Layer"] == "T"
    # KiCad's own rotation (-90 -> 270), not JLC's corrected one
    assert float(cpl["C1"]["Rotation"]) == 270.0
    order = (out / "ORDER.md").read_text(encoding="utf-8")
    assert "placement review" in order and "vendor fit" in order


def test_package_for_a_jlc_rotation_vendor_takes_jlcs_cpl(vroot, ws):
    prof = _profile()
    prof["assembly"]["rotation"] = "jlc"
    (vroot / "testfab.yaml").write_text(yaml.safe_dump(prof), "utf-8")
    vendor.package(ws, "testfab")
    jlc = {r["Designator"]: r["Rotation"] for r in _csv(ws / "fab" / "CPL.csv")}
    got = {r["Designator"]: r["Rotation"]
           for r in _csv(ws / "fab" / "testfab" / "CPL.csv")}
    assert got == jlc


def test_bare_board_vendor_gets_gerbers_only(vroot, ws):
    prof = _profile()
    prof["meta"]["services"]["pcba"] = False
    del prof["assembly"]
    (vroot / "testfab.yaml").write_text(yaml.safe_dump(prof), "utf-8")
    assert vendor.package(ws, "testfab")["files"] == ["gerbers.zip",
                                                      "ORDER.md"]


# --------------------------------------------------------------- ledger

def test_choosing_a_vendor_makes_its_fit_a_release_fact(vroot, ws):
    key = ledgerlib.key("testfab", "vendor")
    assert not any(e["kind"] == "vendor"
                   for e in ledgerlib.expected(ws).values())
    rep = vendor.choose(ws, "testfab")
    assert rep["fact"]["status"] == "unknown"
    assert json.loads((ws / "state.json").read_text())["vendor"] == "testfab"
    assert ledgerlib.expected(ws)[key]["basis"]

    vendor.fit(ws / "kicad" / "hwde-led-test.kicad_pcb", ws / "fab",
               ["testfab"], qtys=(3,))
    fact = ledgerlib.report(ws)
    row = next(r for r in fact["facts"] if r["key"] == key)
    assert row["status"] == "verified", row

    # the board moves on: the fit no longer speaks for it
    pcb = ws / "kicad" / "hwde-led-test.kicad_pcb"
    pcb.write_text(pcb.read_text(encoding="utf-8").replace(
        "13.2", "13.3", 1), encoding="utf-8")
    row = next(r for r in ledgerlib.report(ws)["facts"] if r["key"] == key)
    assert row["status"] == "unknown" and "older board" in row["why"]


def test_choose_refuses_an_unknown_vendor(ws):
    with pytest.raises(vendors.VendorError):
        vendor.choose(ws, "nosuchfab")


def test_per_board_area_price_sells_in_sets():
    pr = {"model": "per_area", "unit": "cm2", "copies": 3, "per": "board",
          "setup": 12.0, "rate": {2: 0.1}}
    # 7 x 5 cm, qty 4 -> 6 boards billed
    got = order_quote.area_cost(pr, 2, 4, 70.0, 50.0)
    assert got["base_at_qty"] == 6 and got["total"] == round(12 + 35 * 0.1 * 6, 2)
