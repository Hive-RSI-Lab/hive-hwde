"""lib_pull on what one pull writes over or hands over broken (#412, #393).

#412: EasyEDA names footprints by package, so two parts can write the same
<lib>.pretty/<name>.kicad_mod; the second write replaced the first, and its
fpfix repairs with it. #393: EasyEDA numbers a USB-A receptacle's two shell
legs both "0" (C2346, pulled 2026-10-31: symbol pins "0","0", pads 0,0).
No network: easyeda2kicad is replaced by a fake that writes what it would.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import fplib  # noqa: E402
import lib_pull  # noqa: E402
import pullfix  # noqa: E402

HEAD = '''(kicad_symbol_lib
  (version 20211014)
  (generator https://github.com/uPesy/easyeda2kicad.py)
'''


def _sym(name, lcsc, fp, pins=(("1", "A"), ("2", "B"))):
    pin_txt = "".join(
        f'''      (pin unspecified line
        (at 0 {i * 2.54} 0)
        (length 2.54)
        (name "{nm}" (effects (font (size 1.27 1.27))))
        (number "{num}" (effects (font (size 1.27 1.27))))
      )
''' for i, (num, nm) in enumerate(pins))
    return f'''  (symbol "{name}"
    (property
      "Footprint"
      "aiee:{fp}"
      (id 2)
      (at 0 0 0)
    )
    (property "LCSC Part" "{lcsc}" (id 5) (at 0 0 0))
    (symbol "{name}_0_1"
{pin_txt}    )
  )
'''


def _fp(name, pads, crt=1.0):
    pad_txt = "".join(
        "\t(pad %s smd rect (at %s %s) (size 0.6 0.6) "
        "(layers F.Cu F.Paste F.Mask))\n" % (n or '""', x, y)
        for n, x, y in pads)
    return (f"(module easyeda2kicad:{name} (layer F.Cu)\n"
            f"\t(fp_line (start -{crt} -1) (end {crt} -1) (layer F.CrtYd) "
            f"(width 0.05))\n{pad_txt})\n")


SOT_A = [("1", -1, -0.95), ("2", -1, 0), ("3", -1, 0.95),
         ("4", 1, 0.95), ("5", 1, 0), ("6", 1, -0.95)]
SOT_B = [("1", -0.95, 1), ("2", 0, 1), ("3", 0.95, 1),      # turned 90 deg
         ("4", 0.95, -1), ("5", 0, -1), ("6", -0.95, -1)]
FPN = "SOT-23-6_X"


@pytest.fixture
def lib(tmp_path):
    base = tmp_path / "lib" / "aiee"
    (tmp_path / "lib" / "aiee.pretty").mkdir(parents=True)
    # C7519 was pulled earlier; fpfix grew its courtyard (crt 1.6)
    base.with_suffix(".kicad_sym").write_text(
        HEAD + _sym("USBLC6", "C7519", FPN) + ")\n", encoding="utf-8")
    (tmp_path / "lib" / "aiee.pretty" / f"{FPN}.kicad_mod").write_text(
        _fp(FPN, SOT_A, crt=1.6), encoding="utf-8")
    return base


def _fake(monkeypatch, symbol: str, fp_name: str, fp_text: str):
    """easyeda2kicad appending `symbol` and (over)writing fp_name."""
    def run(lcsc, base, no_3d, overwrite, only_3d=False, **kw):
        sym = base.with_suffix(".kicad_sym")
        t = sym.read_text(encoding="utf-8").rstrip()
        assert t.endswith(")")
        sym.write_text(t[:-1] + symbol + ")\n", encoding="utf-8")
        (Path(str(base) + ".pretty") / f"{fp_name}.kicad_mod").write_text(
            fp_text, encoding="utf-8")
        return SimpleNamespace(stdout="Created Kicad footprint", stderr="",
                               returncode=0)
    monkeypatch.setattr(lib_pull, "_run_easyeda", run)


def test_same_pads_puts_the_repaired_file_back(lib, monkeypatch):
    _fake(monkeypatch, _sym("SN74", "C10426", FPN), FPN, _fp(FPN, SOT_A))
    r = lib_pull._pull_one("C10426", lib, True, False)
    assert r["status"] == "pulled", r
    assert r["shared_footprint"]["action"] == "kept_existing"
    assert r["shared_footprint"]["shared_with"] == ["C7519"]
    text = (Path(str(lib) + ".pretty") / f"{FPN}.kicad_mod").read_text()
    assert "-1.6" in text            # fpfix's courtyard survived the re-pull


def test_different_pads_split_the_footprint(lib, monkeypatch):
    _fake(monkeypatch, _sym("TPS", "C55266", FPN), FPN, _fp(FPN, SOT_B))
    r = lib_pull._pull_one("C55266", lib, True, False)
    assert r["status"] == "pulled", r
    split = f"{FPN}_C55266"
    assert r["shared_footprint"] == {"footprint": FPN, "action": "split",
                                     "as": split, "shared_with": ["C7519"]}
    pretty = Path(str(lib) + ".pretty")
    old = pretty / f"{FPN}.kicad_mod"
    assert pullfix.same_pads(old.read_text(), _fp(FPN, SOT_A))
    new = (pretty / f"{split}.kicad_mod").read_text()
    assert new.startswith(f"(module easyeda2kicad:{split} ")
    assert pullfix.same_pads(new, _fp(FPN, SOT_B))
    idx = {e["lcsc"]: e["footprint"] for e in
           fplib.symbol_index(lib.with_suffix(".kicad_sym"))}
    assert idx == {"C7519": f"aiee:{FPN}", "C55266": f"aiee:{split}"}
    assert r["footprints"][0]["name"] == split


def test_a_new_name_is_left_alone(lib, monkeypatch):
    _fake(monkeypatch, _sym("X", "C1", "OTHER"), "OTHER", _fp("OTHER", SOT_B))
    r = lib_pull._pull_one("C1", lib, True, False)
    assert r["status"] == "pulled" and "shared_footprint" not in r


USB_PADS = [("4", -1.39, -3.48), ("3", -1.39, -0.98), ("2", -1.39, 1.02),
            ("1", -1.39, 3.52), ("0", 1.39, 7.0), ("0", 1.39, -7.0)]
USB_PINS = (("1", "VCC"), ("2", "D-"), ("3", "D+"), ("4", "GND"),
            ("0", "0"), ("0", "0"))


def test_renumber_duplicate_pins_usb_a_shell():
    sym = HEAD + _sym("USBA", "C2346", "USB-A", USB_PINS) + ")\n"
    fp = _fp("USB-A", USB_PADS)
    sym2, fp2, changes, warns = pullfix.renumber_duplicate_pins(sym, "USBA",
                                                                fp)
    assert changes == [{"symbol": "USBA", "from": "0", "to": "5"},
                       {"symbol": "USBA", "from": "0", "to": "6"}]
    assert not warns
    assert '(number "5"' in sym2 and '(number "6"' in sym2
    assert '(name "5"' in sym2 and '(number "0"' not in sym2
    assert "(pad 5 smd rect (at 1.39 7.0)" in fp2
    assert "(pad 6 smd rect (at 1.39 -7.0)" in fp2
    # idempotent
    again = pullfix.renumber_duplicate_pins(sym2, "USBA", fp2)
    assert again[2] == [] and again[0] == sym2 and again[1] == fp2


def test_renumber_count_mismatch_only_warns():
    sym = HEAD + _sym("USBA", "C2346", "USB-A", USB_PINS) + ")\n"
    fp = _fp("USB-A", USB_PADS[:5])
    sym2, fp2, changes, warns = pullfix.renumber_duplicate_pins(sym, "USBA",
                                                                fp)
    assert changes == [] and sym2 == sym and fp2 == fp
    assert "2 pins but 1 pads" in warns[0]


def test_pull_renumbers_duplicate_pins(tmp_path, monkeypatch):
    base = tmp_path / "lib" / "aiee"
    (tmp_path / "lib" / "aiee.pretty").mkdir(parents=True)
    base.with_suffix(".kicad_sym").write_text(HEAD + ")\n", encoding="utf-8")
    _fake(monkeypatch, _sym("USBA", "C2346", "USB-A", USB_PINS), "USB-A",
          _fp("USB-A", USB_PADS))
    r = lib_pull._pull_one("C2346", base, True, False)
    assert r["status"] == "pulled", r
    assert [c["to"] for c in r["renumbered_pins"]] == ["5", "6"]
    fp = (tmp_path / "lib" / "aiee.pretty" / "USB-A.kicad_mod").read_text()
    assert "(pad 0 " not in fp


def test_same_pads_matches_a_thermal_via_fpfix_numbered():
    raw = _fp("Q", [("1", 0, 0), ("", 0.5, 0.5)])
    fixed = _fp("Q", [("1", 0, 0), ("1", 0.5, 0.5)])
    assert pullfix.same_pads(fixed, raw)
    assert not pullfix.same_pads(_fp("Q", [("1", 0, 0), ("2", 0.5, 0.5)]),
                                 _fp("Q", [("2", 0, 0), ("1", 0.5, 0.5)]))


def test_progress_report_written_per_part(tmp_path, monkeypatch):
    """A run killed mid-batch leaves the parts it pulled on record (#412)."""
    seen = []

    def one(lcsc, base, no_3d, overwrite, timeout_s=None):
        return {"lcsc": lcsc, "status": "pulled"}
    monkeypatch.setattr(lib_pull, "_pull_one", one)
    monkeypatch.setattr(lib_pull, "_present", lambda l, b: (None, None))
    pacer = lib_pull._Pacer(0.0, None)
    lib_pull._pull_all(["C1", "C2"], tmp_path / "aiee", True, False, pacer,
                       on_result=lambda rs: seen.append(
                           [r["lcsc"] for r in rs]))
    assert seen == [["C1"], ["C1", "C2"]]
