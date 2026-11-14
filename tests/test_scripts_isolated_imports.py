"""Every script finds the modules it imports under `python -I`.

`python -I script.py` leaves the script's own folder off sys.path, so a
script that imports another scripts/ module, at the top or lazily inside a
function, has to put scripts/ on sys.path itself, and one that imports a
scripts/lib module has to put lib/ there. check_ratings put only lib/ there
and died on its lazy `import datasheet_extract` (rc 2); check_creepage and
check_pdn died at their first line. A lib module that imports a scripts/
module puts scripts/ on sys.path itself, as geom, statelib and doubtlib do.

Static, so it sees the lazy imports a run would only reach on some boards:
each module is parsed, and every import of a scripts/ or lib/ module must
have a `sys.path.insert`/`append` of that folder before it, at module level
above it or, for an import inside a function, at module level anywhere or
earlier in that function. The insert's argument is evaluated over the
module's own `Path(__file__)` assignments, path operations only. A run under
`-I` then checks the case that was reported.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LIB = SCRIPTS / "lib"

TOP = ({p.stem for p in SCRIPTS.glob("*.py")}
       | {p.parent.name for p in SCRIPTS.glob("*/__init__.py")})
LIBS = {p.stem for p in LIB.glob("*.py")} - {"__init__"}

# what an insert's argument may do: build a path from __file__, nothing else
_ATTRS = {"resolve", "parent", "parents", "absolute", "joinpath", "path",
          "dirname", "abspath", "realpath", "join"}
_NODES = (ast.Expression, ast.Call, ast.Attribute, ast.Name, ast.Constant,
          ast.BinOp, ast.Div, ast.Add, ast.Subscript, ast.Tuple, ast.List,
          ast.Load)


def _eval(expr: ast.expr, env: dict):
    for n in ast.walk(expr):
        if (not isinstance(n, _NODES)
                or isinstance(n, ast.Attribute) and n.attr not in _ATTRS
                or isinstance(n, ast.Name) and n.id not in env):
            return None
    try:
        return eval(compile(ast.Expression(expr), "<insert>", "eval"),
                    {"__builtins__": {}}, dict(env))
    except Exception:
        return None


def _is_path_call(node) -> bool:
    return (isinstance(node, ast.Call) and node.args
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("insert", "append")
            and ast.unparse(node.func.value) == "sys.path")


def _inserts(body: list, env: dict, out: list) -> None:
    """(line, folder) of each sys.path insert in `body`, in order, binding
    the simple assignments and `for` loops over literal tuples on the way.
    Nested defs are their own scopes."""
    for st in body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)):
            continue
        if (isinstance(st, ast.Assign) and len(st.targets) == 1
                and isinstance(st.targets[0], ast.Name)):
            v = _eval(st.value, env)
            if v is not None:
                env[st.targets[0].id] = v
        elif isinstance(st, ast.For) and isinstance(st.target, ast.Name):
            for item in _eval(st.iter, env) or ():
                _inserts(st.body, {**env, st.target.id: item}, out)
        elif isinstance(st, ast.If):
            _inserts(st.body, env, out)
            _inserts(st.orelse, env, out)
        elif isinstance(st, ast.Try):
            for b in (st.body, st.orelse, st.finalbody,
                      *[h.body for h in st.handlers]):
                _inserts(b, env, out)
        elif isinstance(st, ast.With):
            _inserts(st.body, env, out)
        elif isinstance(st, ast.Expr) and _is_path_call(st.value):
            folder = _eval(st.value.args[-1], env)
            if isinstance(folder, (str, Path)):
                out.append((st.lineno, str(Path(folder).resolve())))


def _homes(name: str, module: Path) -> set[str]:
    """The folders that let `module` import `name` (empty: not ours, or
    nothing to check)."""
    if module.parent == LIB and name == "lib":
        return set()     # its own package: found beside it, as `lib`'s parent
    homes = set()
    if name in TOP and not (module.parent == SCRIPTS and name == module.stem):
        homes.add(str(SCRIPTS))
    if name in LIBS and module.parent == SCRIPTS:
        homes.add(str(LIB))
    if name in LIBS and module.parent == LIB:
        return set()     # lib to lib: found beside it
    return homes


def unreachable_imports(module: Path, text: str | None = None) -> list[str]:
    """One line per import in `module` (source `text`, default the file's)
    whose folder nothing puts on sys.path before it."""
    if text is None:
        text = module.read_text(encoding="utf-8")
    tree = ast.parse(text, str(module))
    env = {"Path": Path, "os": os, "str": str, "__file__": str(module)}
    top: list = []
    _inserts(tree.body, env, top)
    bad: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[list] = []   # the inserts of each enclosing def

        def visit_FunctionDef(self, node):
            own: list = []
            _inserts(node.body, dict(env), own)
            self.stack.append(own)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def check(self, node, name):
            homes = _homes(name, module)
            if not homes:
                return
            if self.stack:
                ok = any(d in homes for _, d in top) or any(
                    d in homes and line < node.lineno
                    for scope in self.stack for line, d in scope)
            else:
                ok = any(d in homes and line < node.lineno for line, d in top)
            if not ok:
                where = "scripts/" if str(SCRIPTS) in homes else "lib/"
                bad.append(f"{module.relative_to(SCRIPTS)}:{node.lineno} "
                           f"imports {name} with no {where} on sys.path")

        def visit_Import(self, node):
            for a in node.names:
                self.check(node, a.name.split(".")[0])

        def visit_ImportFrom(self, node):
            if node.level == 0 and node.module:
                self.check(node, node.module.split(".")[0])

    Visitor().visit(tree)
    return bad


def test_every_script_puts_the_folders_it_imports_from_on_sys_path():
    modules = sorted(SCRIPTS.glob("*.py")) + sorted(LIB.glob("*.py"))
    assert len(modules) > 100
    bad = [line for m in modules for line in unreachable_imports(m)]
    assert not bad, "\n".join(bad)


def _scan_source(name: str, text: str, where=SCRIPTS):
    """Scan a made-up module as if it sat in `where`."""
    return unreachable_imports(where / name, text)


@pytest.mark.parametrize("text,count", [
    # the reported shape: lib only, a lazy sibling import
    ('import sys\nfrom pathlib import Path\n'
     'HERE = Path(__file__).resolve().parent\n'
     'sys.path.insert(0, str(HERE / "lib"))\n'
     'import checklib\n'
     'def f():\n    import check_thermal\n', 1),
    # the fix
    ('import sys\nfrom pathlib import Path\n'
     'HERE = Path(__file__).resolve().parent\n'
     'sys.path.insert(0, str(HERE))\n'
     'sys.path.insert(0, str(HERE / "lib"))\n'
     'import checklib\n'
     'def f():\n    import check_thermal\n', 0),
    # an insert after a top-level import comes too late
    ('import sys\nfrom pathlib import Path\n'
     'import check_thermal\n'
     'sys.path.insert(0, str(Path(__file__).resolve().parent))\n', 1),
    # a loop over both folders, and an insert inside the function
    ('import sys\nfrom pathlib import Path\n'
     'S = Path(__file__).resolve().parent\n'
     'for p in (S, S / "lib"):\n'
     '    if str(p) not in sys.path:\n        sys.path.insert(0, str(p))\n'
     'import checklib, check_thermal\n', 0),
    ('import sys\nfrom pathlib import Path\n'
     'def f():\n    here = Path(__file__).resolve().parent\n'
     '    sys.path.insert(0, str(here))\n    import check_thermal\n', 0),
    # an insert that isn't a path from __file__ is not credited
    ('import sys, os\nsys.path.insert(0, os.getcwd())\n'
     'import check_thermal\n', 1),
])
def test_the_scan_reads_each_shape(text, count):
    assert len(_scan_source("made_up_check.py", text)) == count


def test_a_lib_module_needs_scripts_but_not_lib():
    text = ('import checklib\n'
            'def f():\n    import check_thermal\n')
    (line,) = _scan_source("made_up_lib.py", text, where=LIB)
    assert "check_thermal with no scripts/" in line


def test_check_ratings_runs_under_python_isolated(tmp_path):
    """The reported failure: under -I check_ratings reached its lazy
    `import datasheet_extract` and exited 2."""
    board = ROOT / "tests" / "fixtures" / "ratings" / "blinky"
    out = tmp_path / "ratings.json"
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "check_ratings.py"),
         "--pcb", str(board / "kicad" / "board.kicad_pcb"),
         "--out", str(out)],
        capture_output=True, text=True, timeout=300, cwd=tmp_path)
    assert "ModuleNotFoundError" not in r.stderr, r.stderr
    assert r.returncode in (0, 1), r.stderr[-2000:]
    assert json.loads(out.read_text(encoding="utf-8"))["status"] in (
        "pass", "violations")
