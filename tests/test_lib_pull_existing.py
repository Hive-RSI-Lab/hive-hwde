"""lib_pull on parts already in the library (hwde-led-test snag 15).

A fresh clone has every part's symbol and footprint but none of its 3D models
(the 3dshapes are gitignored). easyeda2kicad stops at an existing symbol, so a
plain re-pull fetched none, and --overwrite undid the footprint repairs. Now an
existing part is not handed to easyeda2kicad at all, only `--3d` runs for a
footprint whose named model is missing, and nothing in the symbol library or
the footprints changes. No network: easyeda2kicad is replaced by a fake.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import lib_pull  # noqa: E402

SYM = '''(kicad_symbol_lib
  (version 20211014)
  (generator https://github.com/uPesy/easyeda2kicad.py)
  (symbol "R_A"
    (property "Reference" "R" (id 0) (at 0 0 0))
    (property "Footprint" "aiee:R0603" (id 2) (at 0 0 0))
    (property "LCSC Part" "C1" (id 5) (at 0 0 0))
  )
  (symbol "R_B"
    (property "Reference" "R" (id 0) (at 0 0 0))
    (property "Footprint" "aiee:R0603" (id 2) (at 0 0 0))
    (property "LCSC Part" "C2" (id 5) (at 0 0 0))
  )
)'''

FP = '''(module easyeda2kicad:R0603 (layer F.Cu)
\t(pad 1 smd rect (at -0.8 0) (size 0.8 0.9) (layers F.Cu F.Paste F.Mask))
\t(pad 2 smd rect (at 0.8 0) (size 0.8 0.9) (layers F.Cu F.Paste F.Mask))
\t(model "${KIPRJMOD}/../lib/aiee.3dshapes/R0603.wrl"
\t\t(offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
\t(model "${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.wrl")
)
'''


@pytest.fixture
def base(tmp_path) -> Path:
    b = tmp_path / "lib" / "aiee"
    (tmp_path / "lib" / "aiee.pretty").mkdir(parents=True)
    b.with_suffix(".kicad_sym").write_text(SYM, encoding="utf-8")
    (tmp_path / "lib" / "aiee.pretty" / "R0603.kicad_mod").write_text(FP, encoding="utf-8")
    return b


@pytest.fixture
def calls(monkeypatch):
    """Record easyeda2kicad runs; a --3d run writes the model it would fetch."""
    seen: list[tuple[str, bool]] = []

    def fake(lcsc, base, no_3d, overwrite, only_3d=False):
        seen.append((lcsc, only_3d))
        if only_3d:
            d = Path(str(base) + ".3dshapes")
            d.mkdir(exist_ok=True)
            for ext in ("wrl", "step"):
                (d / f"R0603.{ext}").write_text("model", encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(lib_pull, "_run_easyeda", fake)
    return seen


def _snapshot(base: Path) -> dict:
    return {p: p.read_bytes() for p in base.parent.rglob("*.kicad_*")}


def test_existing_parts_are_not_re_pulled_and_their_models_come_back(base, calls):
    before = _snapshot(base)
    pacer = lib_pull._Pacer(0)
    results, _ = lib_pull._pull_all(["C1", "C2"], base, False, False, pacer)
    assert [r["status"] for r in results] == ["exists", "exists"]
    rep = lib_pull._restore_models(results, base, pacer)
    # one --3d run: the second part shares the footprint and finds it present
    assert calls == [("C1", True)]
    assert rep == {"restored": ["R0603.wrl"], "still_missing": []}
    assert _snapshot(base) == before, "symbol lib and footprints must not change"
    # and a second run touches the network not at all
    calls.clear()
    results, _ = lib_pull._pull_all(["C1", "C2"], base, False, False, pacer)
    assert lib_pull._restore_models(results, base, pacer)["restored"] == []
    assert calls == []


def test_models_outside_the_library_are_not_its_to_fill(base):
    fp = base.parent / "aiee.pretty" / "R0603.kicad_mod"
    models = Path(str(base) + ".3dshapes")
    assert lib_pull._missing_models(fp, models) == ["R0603.wrl"]


def test_a_model_that_never_arrives_is_a_warning(base, monkeypatch):
    monkeypatch.setattr(lib_pull, "_run_easyeda", lambda *a, **k: SimpleNamespace(
        stdout="No 3D model available", stderr="", returncode=0))
    results, _ = lib_pull._pull_all(["C1"], base, False, False, lib_pull._Pacer(0))
    rep = lib_pull._restore_models(results, base, lib_pull._Pacer(0))
    assert rep["still_missing"] == ["R0603.wrl"]
    assert results[0]["status"] == "exists"
    assert any("still missing" in w for w in results[0]["warnings"])


def test_overwrite_still_hands_the_part_to_easyeda2kicad(base, calls):
    lib_pull._pull_all(["C1"], base, False, True, lib_pull._Pacer(0))
    assert calls == [("C1", False)]


def test_dedupe_leaves_no_blank_line_where_the_duplicate_was(tmp_path):
    # easyeda2kicad inserts a new symbol as "\n  (symbol ...)\n" before the
    # closing paren; a name with a space is re-inserted on every pull.
    block = '  (symbol "A B"\n    (property "Reference" "D" (id 0))\n  )\n'
    lib = tmp_path / "aiee.kicad_sym"
    clean = "(kicad_symbol_lib\n  (version 20211014)\n" + block + ")"
    lib.write_text(clean[:-1] + "\n" + block + ")", encoding="utf-8")
    assert lib_pull._dedupe_symbols(lib)["removed"] == 1
    assert lib.read_text(encoding="utf-8") == clean
