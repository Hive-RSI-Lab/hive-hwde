"""schlib finds a project's own symbol library through its sym-lib-table.

kicad-sch-api reads only the stock libraries, so every `aiee:` lib_id a
lib_pull'd board uses was "not found" until the board's generator called
ksa.get_symbol_cache().add_library_path itself (hifi-receiver-2 lite-main,
snag 12).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

SYM = textwrap.dedent("""\
    (kicad_symbol_lib (version 20211014) (generator test)
      (symbol "PART1" (in_bom yes) (on_board yes)
        (property "Reference" "U" (id 0) (at 0 3.81 0) (effects (font (size 1.27 1.27))))
        (property "Value" "PART1" (id 1) (at 0 -3.81 0) (effects (font (size 1.27 1.27))))
        (symbol "PART1_0_1"
          (rectangle (start -2.54 2.54) (end 2.54 -2.54) (stroke (width 0)) (fill (type none))))
        (symbol "PART1_1_1"
          (pin passive line (at -5.08 0 0) (length 2.54) (name "A" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
          (pin passive line (at 5.08 0 180) (length 2.54) (name "B" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27))))))
      )
    )
    """)

TABLE = textwrap.dedent("""\
    (sym_lib_table
      (version 7)
      (lib (name "{nick}")(type "KiCad")(uri "${{KIPRJMOD}}/../lib/{stem}.kicad_sym")(options "")(descr ""))
    )
    """)


def _board(tmp: Path, nick: str = "tlib", stem: str = "tlib") -> Path:
    (tmp / "lib").mkdir()
    (tmp / "lib" / f"{stem}.kicad_sym").write_text(SYM)
    (tmp / "kicad" / "gen").mkdir(parents=True)
    (tmp / "kicad" / "sym-lib-table").write_text(TABLE.format(nick=nick, stem=stem))
    return tmp


def _run(code: str, script: Path, cwd: Path) -> subprocess.CompletedProcess:
    script.write_text(textwrap.dedent(code))
    env = {**os.environ, "PYTHONPATH": f"{SCRIPTS}:{SCRIPTS / 'lib'}"}
    return subprocess.run([sys.executable, str(script)], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)


def test_lib_table_reads_kiprjmod(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import schlib
    b = _board(tmp_path)
    got = schlib.lib_table(b / "kicad")
    assert got == {"tlib": (b / "kicad").resolve() / ".." / "lib" / "tlib.kicad_sym"}
    assert schlib.lib_table(tmp_path / "nowhere") == {}


@pytest.mark.parametrize("nick,stem", [("tlib", "tlib"), ("proj", "tlib")])
def test_generator_finds_its_projects_library(tmp_path, nick, stem):
    """A generator in kicad/gen/ places a project-library part with no
    registration of its own, from any working directory."""
    b = _board(tmp_path, nick, stem)
    cp = _run(f"""
        import schlib
        s = schlib.Sheet("t")
        c = s.add_component("{nick}:PART1", "U1", "x", (50.8, 50.8))
        print("pins", sorted(p.number for p in c.pins))
        """, b / "kicad" / "gen" / "root.py", cwd=Path("/"))
    assert cp.returncode == 0, cp.stderr[-800:]
    assert "pins ['1', '2']" in cp.stdout


def test_explicit_project_and_clear_miss(tmp_path):
    b = _board(tmp_path)
    cp = _run(f"""
        import schlib
        s = schlib.Sheet("t", project=r"{b / 'kicad'}")
        s.add_component("tlib:PART1", "U1", "x", (50.8, 50.8))
        try:
            schlib.Sheet("u").add_component("nolib:PART1", "U2", "x", (50.8, 50.8))
        except ValueError as e:
            print("miss:", e)
        """, tmp_path / "gen.py", cwd=tmp_path)
    assert cp.returncode == 0, cp.stderr[-800:]
    assert "miss: nolib:PART1: no library 'nolib'" in cp.stdout
    assert "sym-lib-table" in cp.stdout
