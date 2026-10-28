"""The ERC gate says when its 0/0 checked no pin conflicts.

lite-main (hifi-receiver-2) passed ERC 0/0 with every pulled pin typed
`passive` by lib_pin_types (no datasheet extracts), so ERC compared no
drivers or outputs at all; only a reviewer noticed (snag 13).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gate  # noqa: E402


def _sym(name: str, types: list[str]) -> str:
    pins = "".join(f'(pin {t} line (at 0 {i} 0) (length 2.54) (name "P{i}") (number "{i}"))'
                   for i, t in enumerate(types, 1))
    return f'(symbol "{name}" (symbol "{name.split(":")[1]}_1_1" {pins}))'


def _sch(path: Path, syms: list[str], sheets: list[str] = ()) -> Path:
    sub = "".join(f'(sheet (property "Sheetfile" "{s}"))' for s in sheets)
    path.write_text(f"(kicad_sch (version 20250114) (lib_symbols {''.join(syms)}) {sub})\n")
    return path


def test_all_passive_warns(tmp_path):
    sch = _sch(tmp_path / "a.kicad_sch", [
        _sym("aiee:CH224Q", ["passive"] * 11),
        _sym("aiee:TPA3118", ["passive"] * 33),
        _sym("Device:R", ["passive"] * 2),          # 2-pin: not counted
        _sym("power:GND", ["power_in"]),            # power: not counted
    ])
    cov = gate.pin_type_coverage(sch)
    assert cov["symbols"] == 2 and cov["pins"] == 44 and cov["typed"] == 0
    assert cov["untyped"] == ["aiee:CH224Q", "aiee:TPA3118"]
    assert "checked no pin conflicts" in cov["warning"]


def test_typed_pins_anywhere_in_the_hierarchy_clear_the_warning(tmp_path):
    _sch(tmp_path / "power.kicad_sch", [
        _sym("aiee:TPS54302", ["power_in", "passive", "power_out", "passive"])])
    sch = _sch(tmp_path / "root.kicad_sch",
               [_sym("aiee:CH224Q", ["passive"] * 11)], sheets=["power.kicad_sch"])
    cov = gate.pin_type_coverage(sch)
    assert cov["pins"] == 15 and cov["typed"] == 2
    assert cov["untyped"] == ["aiee:CH224Q"]
    assert "warning" not in cov


def test_golden_board_is_typed():
    cov = gate.pin_type_coverage(ROOT / "tests" / "golden" / "blinky2" / "blinky2.kicad_sch")
    assert cov["typed"] > 0 and "warning" not in cov
