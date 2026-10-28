"""CPL placement per part: the LCSC model's pin 1 on the board's pad 1.

Offline: the models are cached EasyEDA records in tests/fixtures/easyeda
(C116592 TPS563201 SOT-23-6, C106675 a THT electrolytic, C106245 a 0603 cap),
plus one synthetic LED model whose numbering is the reverse of KiCad's. The
board is a tiny hand-written .kicad_pcb, so nothing needs KiCad or a network.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bom_cpl  # noqa: E402
import cpl_render  # noqa: E402
import cpl_verify as cv  # noqa: E402
import dfm_check  # noqa: E402
import easyeda  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "easyeda"

# The imported (easyeda2kicad) footprint keeps the model's own geometry.
IMPORTED_SOT236 = [("1", 1.35, 0.95), ("2", 1.35, 0.0), ("3", 1.35, -0.95),
                   ("4", -1.35, -0.95), ("5", -1.35, 0.0), ("6", -1.35, 0.95)]
# KiCad's library SOT-23-6: pins 1-3 down the left, pin 1 top-left.
KICAD_SOT236 = [("1", -1.1375, -0.95), ("2", -1.1375, 0.0),
                ("3", -1.1375, 0.95), ("4", 1.1375, 0.95), ("5", 1.1375, 0.0),
                ("6", 1.1375, -0.95)]


def _fp(ref, fpid, x, y, r, pads, size=(1.1, 0.6), layer="F.Cu"):
    pad_txt = "".join(
        f'(pad "{n}" smd rect (at {px} {py} {r}) (size {size[0]} {size[1]}) '
        f'(layers "{layer}"))' for n, px, py in pads)
    silk = '(fp_line (start -1 -1) (end 1 -1) (layer "F.SilkS"))'
    return (f'(footprint "{fpid}" (layer "{layer}") (at {x} {y} {r}) '
            f'(property "Reference" "{ref}") {silk} {pad_txt})')


BOARD = "(kicad_pcb (version 20240108) " + " ".join([
    _fp("U1", "aiee:SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR", 10, 10, 90,
        IMPORTED_SOT236),
    _fp("U2", "Package_TO_SOT_SMD:SOT-23-6", 20, 10, 0, KICAD_SOT236),
    _fp("D1", "LED_SMD:LED_0603_1608Metric", 30, 10, 0,
        [("1", -0.7875, 0), ("2", 0.7875, 0)], size=(0.875, 0.95)),
    _fp("C1", "aiee:C0603", 40, 10, 0, [("1", -0.7, 0), ("2", 0.7, 0)],
        size=(0.8, 0.9)),
    _fp("C2", "aiee:CAP-TH_BD13.0-P5.00-D1.2-FD", 50, 10, 0,
        [("1", -2.54, 0), ("2", 2.54, 0)], size=(2.0, 2.0)),
    _fp("X1", "aiee:SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR", 60, 10, 0,
        IMPORTED_SOT236),
    _fp("U3", "aiee:SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR", 70, 10, 0,
        IMPORTED_SOT236, layer="B.Cu"),
]) + ")"

# A synthetic LED whose pad 1 is the ANODE (KiCad's LED pad 1 is the cathode).
LED_MODEL = {"lcsc": "C900001", "title": "TEST-LED", "package": "LED0603",
             "origin": [4000, 3000],
             "pads": ["PAD~RECT~3997.244~3000~3.4~3.7~1~~1~0~~0~a",
                      "PAD~RECT~4002.756~3000~3.4~3.7~1~~2~0~~0~b"],
             "silk": [],
             "pins": ["P~show~0~1~-30~0~180~g1~0^^-30~0^^M -30 0 h 10~#880000"
                      "^^0~-17~3~0~A~start~~~#0000FF^^0~-21~-1~0~1~end",
                      "P~show~0~2~30~0~0~g2~0^^30~0^^M 30 0 h -10~#880000"
                      "^^0~17~3~0~K~end~~~#0000FF^^0~21~-1~0~2~start"]}

LCSC = {"U1": "C116592", "U2": "C116592", "D1": "C900001", "C1": "C106245",
        "C2": "C106675", "U3": "C116592"}


@pytest.fixture
def ws(tmp_path):
    cache = tmp_path / "easyeda"
    shutil.copytree(FIX, cache)
    (cache / "C900001.json").write_text(json.dumps(LED_MODEL))
    pcb = tmp_path / "t.kicad_pcb"
    pcb.write_text(BOARD)
    return pcb, cache


def _cpl(**rots):
    """CPL rows at each footprint's own position (board y down -> CPL y up)."""
    pos = {"U1": 10, "U2": 20, "D1": 30, "C1": 40, "C2": 50, "X1": 60,
           "U3": 70}
    return {r: {"x": pos[r], "y": 10.0, "rot": float(v), "layer": "Top"}
            for r, v in rots.items()}


def _verdicts(pcb, cache, cpl):
    rep = cv.verify(pcb, cpl, LCSC, cache, fetch=False)
    return {p["ref"]: p for p in rep["parts"]}, rep


def test_imported_footprint_needs_no_correction(ws):
    """The owner's case: an easyeda2kicad SOT-23-6 at 90 deg must ship at 90.
    The '^SOT-23,180' table row shipped 270 - pin 1 opposite the mark."""
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(U1=90))
    assert v["U1"]["verdict"] == "ok" and v["U1"]["expected_rot"] == [90.0]
    v, rep = _verdicts(pcb, cache, _cpl(U1=270))
    assert v["U1"]["verdict"] == "wrong_rotation"
    assert rep["failed"] == ["U1"] and rep["status"] == "violations"


def test_kicad_library_footprint_needs_180_for_this_model(ws):
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(U2=180))
    assert v["U2"]["verdict"] == "ok"
    v, _ = _verdicts(pcb, cache, _cpl(U2=0))
    assert v["U2"]["verdict"] == "wrong_rotation"
    assert v["U2"]["expected_rot"] == [180.0]


def test_polarity_uses_pin_roles_not_numbers(ws):
    """The LED model's pad 1 is its anode. By number it fits at 0 deg, but
    that puts the anode on KiCad's cathode pad: the role fit wins."""
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(D1=0))
    assert v["D1"]["verdict"] == "wrong_polarity"
    assert v["D1"]["corrections"] == [180.0]
    assert v["D1"]["number_corrections"] == [0.0]


def test_nonpolar_two_pad_part_either_way_round(ws):
    pcb, cache = ws
    for rot in (0, 180):
        v, _ = _verdicts(pcb, cache, _cpl(C1=rot))
        assert v["C1"]["verdict"] == "ok", rot
    v, _ = _verdicts(pcb, cache, _cpl(C1=90))
    assert v["C1"]["verdict"] == "wrong_rotation"


def test_polar_cap_is_strict_about_180(ws):
    """The electrolytic's model names its pins 1/2, so polarity falls back to
    pad numbers - and a 180-degree turn is NOT accepted the way it is for C1."""
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(C2=0))
    assert v["C2"]["verdict"] == "ok" and v["C2"]["polar"]
    assert v["C2"]["basis"].startswith("pad number")
    v, _ = _verdicts(pcb, cache, _cpl(C2=180))
    assert v["C2"]["verdict"] == "wrong_rotation"


def test_no_data_fails_loudly(ws):
    pcb, cache = ws
    v, rep = _verdicts(pcb, cache, _cpl(X1=0, U3=0, U1=90))
    assert v["X1"]["verdict"] == "no_model"
    assert v["U3"]["verdict"] == "bottom_unverified"
    assert v["U1"]["verdict"] == "ok"
    assert sorted(rep["failed"]) == ["U3", "X1"]


def test_cache_is_read_without_network(ws, monkeypatch):
    pcb, cache = ws

    def boom(_):
        raise AssertionError("network touched")
    monkeypatch.setattr(easyeda, "_fetch_raw", boom)
    assert easyeda.get("C116592", cache, fetch=True).package.startswith(
        "SOT-23-6")
    assert easyeda.get("C999999", cache, fetch=False) is None


def test_fetch_caches_trimmed_record(tmp_path):
    raw = {"title": "X", "dataStr": {"shape": LED_MODEL["pins"]},
           "packageDetail": {"title": "LED0603", "dataStr": {
               "head": {"x": 4000, "y": 3000},
               "shape": LED_MODEL["pads"] + ["SVGNODE~huge"]}}}
    m = easyeda.get("c900001", tmp_path, fetcher=lambda _: raw)
    assert m.pin_names == {"1": "A", "2": "K"}
    cached = json.loads((tmp_path / "C900001.json").read_text())
    assert "SVGNODE~huge" not in json.dumps(cached)
    # A part EasyEDA does not know is not cached, so a later run asks again.
    assert easyeda.get("C1", tmp_path, fetcher=lambda _: {}) is None
    assert not (tmp_path / "C1.json").exists()


def test_bom_cpl_uses_the_model_and_falls_back_to_the_table(ws):
    pcb, cache = ws
    fps = cv.board_footprints(pcb)
    rules = bom_cpl.load_rotations(bom_cpl.REF_ROTATIONS)
    parts = [{"ref": r, "x": fps[r].x, "y": -fps[r].y, "rot": fps[r].rot,
              "side": "top", "package": fps[r].name}
             for r in ("U1", "U2", "X1")]
    derive = bom_cpl.model_deriver(
        pcb, {r: {"lcsc": c} for r, c in LCSC.items()}, cache, fetch=False)
    cpl, audit = bom_cpl.build_cpl(parts, rules, derive)
    got = {a["ref"]: a for a in audit}
    assert got["U1"]["source"] == "lcsc_model" and got["U1"]["final_rot"] == 90
    assert got["U2"]["source"] == "lcsc_model" and got["U2"]["final_rot"] == 180
    # No model: the table decides (an imported footprint name -> 0).
    assert got["X1"]["source"] == "table" and got["X1"]["final_rot"] == 0


def test_table_no_longer_turns_imported_sot23_5_and_6():
    rules = bom_cpl.load_rotations(bom_cpl.REF_ROTATIONS)
    for name in ("SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL",
                 "SOT-23-5_L2.9-W1.6-P0.95-LS2.8-BL",
                 "LQFP-48_L7.0-W7.0-P0.50-LS9.0-BL"):
        assert bom_cpl.correct_rotation(name, 90.0, rules)[0] == 90.0, name
    assert bom_cpl.correct_rotation("SOT-23", 0.0, rules)[0] == 180.0


def test_merge_visual_verdicts():
    parts = [{"ref": "U1", "verdict": "ok"}, {"ref": "U2", "verdict": "ok"},
             {"ref": "D1", "verdict": "wrong_polarity"},
             {"ref": "C1", "verdict": "ok"}, {"ref": "C2", "verdict": "ok"}]
    vis = {"model": "claude-sonnet-5-5", "parts": {
        "U1": {"pin1": "match", "polarity": "n/a"},
        "U2": {"pin1": "mismatch", "polarity": "n/a"},
        "D1": {"pin1": "match", "polarity": "mismatch"},
        "C1": {"pin1": "unclear", "polarity": "n/a"}}}
    got = {r["ref"]: r["agreement"] for r in cv.merge_visual(parts, vis)}
    assert got == {"U1": "agree", "U2": "disagree", "D1": "agree",
                   "C1": "unclear", "C2": "missing"}
    assert {r["agreement"] for r in cv.merge_visual(parts, None)} == {
        "not_run"}


def test_dfm_placement_errors_and_visual_disagreement(ws, tmp_path):
    pcb, cache = ws
    cpl = tmp_path / "CPL.csv"
    with cpl.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        w.writerow(["U1", 10, -10, "Top", 270])   # the owner's 180-off part
        w.writerow(["U2", 20, -10, "Top", 180])   # right
    vis = tmp_path / "cpl_visual.json"
    vis.write_text(json.dumps({"parts": {
        "U1": {"pin1": "mismatch", "polarity": "n/a"},
        "U2": {"pin1": "mismatch", "polarity": "n/a"}}}))
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U1": {"lcsc": "C116592"},
                              "U2": {"lcsc": "C116592"}}))
    vios: list = []
    facts = dfm_check.check_placement(pcb, cpl, pj, vis, cache, False, vios)
    kinds = sorted((v["kind"], v["refs"][0]) for v in vios)
    assert kinds == [("cpl_rotation", "U1"), ("cpl_visual_disagree", "U2")]
    assert all(v["severity"] == "error" for v in vios)
    assert facts["failed"] == ["U1"]
    # Both layers say U1 is wrong: that is agreement, not a second error.
    assert {p["ref"]: p["agreement"] for p in facts["parts"]} == {
        "U1": "agree", "U2": "disagree"}


def test_render_puts_pin1_dot_on_pad1_only_when_right(ws, tmp_path):
    pcb, cache = ws
    fp = cv.board_footprints(pcb)["U1"]
    model = easyeda.get("C116592", cache, fetch=False)
    pad1 = next(p["center"] for p in cpl_render.board_shapes(fp)["pads"]
                if p["number"] == "1")
    right = cpl_render.model_shapes(model, _cpl(U1=90)["U1"])["pin1"]
    wrong = cpl_render.model_shapes(model, _cpl(U1=270)["U1"])["pin1"]
    assert abs(right[0] - pad1[0]) < 0.05 and abs(right[1] - pad1[1]) < 0.05
    assert abs(wrong[0] - pad1[0]) > 1.0 or abs(wrong[1] - pad1[1]) > 1.0

    idx = cpl_render.render(pcb, _cpl(U1=90, U2=180, D1=0, X1=0), LCSC,
                            cache, tmp_path / "r", fetch=False, per_image=3)
    assert [i["refs"] for i in idx["images"]] == [["D1", "U1", "U2"], ["X1"]]
    for i in idx["images"]:
        png = tmp_path / "r" / i["file"]
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert idx["parts"]["X1"]["has_model"] is False
    assert idx["parts"]["D1"]["polar"] is True


# ---------------------------------------------- cache resolution, fetch errors

def test_cache_order_env_then_board_dir_then_shared(tmp_path, monkeypatch):
    home = tmp_path / "home"
    board = tmp_path / "board"
    board.mkdir()
    parts = board / "parts.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HWDE_EASYEDA_CACHE", raising=False)
    monkeypatch.delenv("AIEE_EASYEDA_CACHE", raising=False)
    shared = home / ".cache" / "hwde" / "easyeda"
    # No board-local dir (a worktree checkout): the shared cache.
    assert cv.default_cache(parts) == shared
    assert cv.default_cache(None) == shared
    # A board-local dir that exists wins over the shared one.
    (board / "easyeda").mkdir()
    assert cv.default_cache(parts) == board / "easyeda"
    # The env var wins over both.
    monkeypatch.setenv("HWDE_EASYEDA_CACHE", str(tmp_path / "env"))
    assert cv.default_cache(parts) == tmp_path / "env"
    assert cv.default_cache(None) == tmp_path / "env"


def test_failed_fetch_is_fetch_failed_not_no_model(ws, tmp_path, monkeypatch):
    """A fetch that raises must not read as a missing model (and so not as a
    pin-1 result); an API with no data for the part stays no_model."""
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)

    def boom(lcsc):
        raise OSError("HTTP Error 403: rate limited")
    monkeypatch.setattr(easyeda, "_fetch_raw", boom)
    rep = cv.verify(pcb, _cpl(U1=90), LCSC, tmp_path / "empty", fetch=True)
    row = rep["parts"][0]
    assert row["verdict"] == "fetch_failed"
    assert "403" in row["why"] and "pin-1" in row["why"]
    assert rep["failed"] == ["U1"] and rep["status"] == "violations"
    assert "fetch_failed" in cv.FAIL_VERDICTS

    # The 403 latched the rate limit for this process; a fresh run starts
    # without it.
    easyeda.reset_rate_limit()
    monkeypatch.setattr(easyeda, "_fetch_raw", lambda lcsc: {})
    rep = cv.verify(pcb, _cpl(U1=90), LCSC, tmp_path / "empty2", fetch=True)
    assert rep["parts"][0]["verdict"] == "no_model"


# ------------------------------------- rate limit: 403/429 stop the fetching

def _http(code):
    import urllib.error

    def fetch(lcsc):
        raise urllib.error.HTTPError(easyeda.API.format(lcsc=lcsc), code,
                                     "Forbidden", None, None)
    return fetch


class _Calls:
    """A fetcher that records each LCSC number it is asked for."""

    def __init__(self, fetch):
        self.fetch, self.asked = fetch, []

    def __call__(self, lcsc):
        self.asked.append(lcsc)
        return self.fetch(lcsc)


@pytest.mark.parametrize("code", [403, 429])
def test_rate_limit_latches_and_later_parts_are_not_fetched(tmp_path, code,
                                                             capsys):
    calls = _Calls(_http(code))
    errs: dict = {}
    assert easyeda.get("C1", tmp_path, fetcher=calls, errors=errs) is None
    assert errs["C1"] == f"HTTP {code} from EasyEDA (rate limited)"
    assert easyeda.rate_limited() == errs["C1"]
    # Every later uncached part is skipped, not asked again.
    for c in ("C2", "C3"):
        assert easyeda.get(c, tmp_path, fetcher=calls, errors=errs) is None
        assert errs[c].startswith("not fetched: HTTP") and "rate" in errs[c]
    assert calls.asked == ["C1"]
    # Said once on stderr, not per part.
    assert capsys.readouterr().err.count("rate limited") == 1
    # A cached part is still read.
    shutil.copy(FIX / "C106245.json", tmp_path / "C106245.json")
    assert easyeda.get("C106245", tmp_path, fetcher=calls) is not None


def test_404_or_empty_is_no_model_and_network_error_is_not(tmp_path):
    import urllib.error
    errs: dict = {}
    assert easyeda.get("C1", tmp_path, fetcher=_http(404), errors=errs) is None
    assert easyeda.get("C2", tmp_path, fetcher=lambda _: {},
                       errors=errs) is None
    assert errs == {} and easyeda.rate_limited() is None

    def down(lcsc):
        raise urllib.error.URLError("Temporary failure in name resolution")
    calls = _Calls(down)
    for c in ("C3", "C4"):
        assert easyeda.get(c, tmp_path, fetcher=calls, errors=errs) is None
    assert errs["C3"].startswith("network error: URLError")
    # A network error is not a rate limit: the next part is still asked.
    assert calls.asked == ["C3", "C4"] and easyeda.rate_limited() is None
    assert easyeda.get("C5", tmp_path, fetcher=_http(500), errors=errs) is None
    assert errs["C5"] == "HTTP 500 from EasyEDA"


def test_cpl_verify_403_fails_first_part_and_skips_the_rest(ws, tmp_path,
                                                            monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    calls = _Calls(_http(403))
    monkeypatch.setattr(easyeda, "_fetch_raw", calls)
    lcsc = {"U1": "C116592", "C1": "C106245", "C2": "C106675"}
    rep = cv.verify(pcb, _cpl(U1=90, C1=0, C2=0), lcsc, tmp_path / "empty",
                    fetch=True)
    rows = {r["ref"]: r for r in rep["parts"]}
    assert {r["verdict"] for r in rows.values()} == {"fetch_failed"}
    assert calls.asked == ["C106245"]   # first in natural order, then none
    assert "HTTP 403" in rows["C1"]["fetch_error"]
    assert rows["U1"]["fetch_error"].startswith("not fetched")
    assert rep["rate_limited"] == "HTTP 403 from EasyEDA (rate limited)"
    assert rep["fetch_failed"] == ["C1", "C2", "U1"]
    assert rep["status"] == "violations"


def test_dfm_reports_a_refused_fetch_once_never_as_no_model(ws, tmp_path,
                                                            monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    monkeypatch.setattr(easyeda, "_fetch_raw", _http(403))
    cpl = tmp_path / "CPL.csv"
    with cpl.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        w.writerow(["U1", 10, -10, "Top", 90])
        w.writerow(["C1", 40, -10, "Top", 0])
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U1": {"lcsc": "C116592"},
                              "C1": {"lcsc": "C106245"}}))
    vios: list = []
    facts = dfm_check.check_placement(pcb, cpl, pj, None, tmp_path / "empty",
                                      True, vios)
    assert [v["kind"] for v in vios] == ["cpl_fetch_failed"]
    v = vios[0]
    assert v["severity"] == "error" and sorted(v["refs"]) == ["C1", "U1"]
    assert "HTTP 403" in v["msg"] and "rate limited" in v["msg"]
    assert "rerun" in v["msg"] and v["rate_limited"] is True
    assert facts["fetch_failed"] == ["C1", "U1"]
    assert "cpl_fetch_failed" in dfm_check.DFM_FAMILIES["placement"]


def test_bom_cpl_says_fetch_failed_not_none_and_fails_the_run(ws, tmp_path,
                                                              monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    calls = _Calls(_http(403))
    monkeypatch.setattr(easyeda, "_fetch_raw", calls)
    fps = cv.board_footprints(pcb)
    pos = tmp_path / "pos.csv"
    lines = ["Ref,Val,Package,PosX,PosY,Rot,Side"]
    for r in ("U1", "X1", "C1"):
        f = fps[r]
        lines.append(f"{r},v,{f.name},{f.x},{-f.y},{f.rot},top")
    pos.write_text("\n".join(lines) + "\n")
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U1": {"lcsc": "C116592"},
                              "X1": {"lcsc": "C116593"},
                              "C1": {"lcsc": "C106245"}}))
    rep = bom_cpl.run(pcb, tmp_path / "fab", pos=pos, parts_json=pj,
                      easyeda_cache=tmp_path / "empty", fetch_models=True)
    got = {a["ref"]: a for a in rep["rotation_audit"]}
    assert {a["source"] for a in got.values()} == {"fetch_failed"}
    assert "HTTP 403" in got["C1"]["fetch_error"]
    assert len(calls.asked) == 1
    assert rep["model_fetch_failed"] == ["C1", "U1", "X1"]
    assert rep["easyeda_rate_limited"].startswith("HTTP 403")
    assert rep["status"] == "violations"
    vio = [v for v in rep["violations"]
           if v["kind"] == "cpl_model_fetch_failed"]
    assert len(vio) == 1 and "rate limited" in vio[0]["message"]


def test_render_records_fetch_error_apart_from_no_model(ws, tmp_path,
                                                        monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    easyeda.reset_rate_limit()

    def forbidden(lcsc):
        raise OSError("HTTP Error 403: rate limited")
    monkeypatch.setattr(easyeda, "_fetch_raw", forbidden)
    idx = cpl_render.render(pcb, _cpl(U1=90), LCSC, tmp_path / "empty",
                            tmp_path / "r", fetch=True)
    part = idx["parts"]["U1"]
    assert part["has_model"] is False and "403" in part["fetch_error"]
    assert idx["fetch_failed"] == ["U1"] and idx["rate_limited"]
    assert "rerun" in idx["note"]
    easyeda.reset_rate_limit()

    monkeypatch.setattr(easyeda, "_fetch_raw", lambda lcsc: {})
    idx = cpl_render.render(pcb, _cpl(U1=90), LCSC, tmp_path / "empty2",
                            tmp_path / "r2", fetch=True)
    assert idx["parts"]["U1"]["has_model"] is False
    assert "fetch_error" not in idx["parts"]["U1"]
    assert idx["fetch_failed"] == [] and "note" not in idx
    easyeda.reset_rate_limit()
