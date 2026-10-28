"""Tests for the hwde module's own changes to the skill's scripts.

Run with the skill's venv (bin/hwde builds it):
  eval "$(bin/hwde env)"; $HWDE_PY -m pytest -q tests
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "hwde" / "scripts"
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]

LED_TEST = ROOT / "boards" / "hwde-led-test"
LED_PCB = LED_TEST / "kicad" / "hwde-led-test.kicad_pcb"
