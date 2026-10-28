"""Toolchain discovery for hwde scripts.

Every hwde script resolves external tools through this module so that:

1. The whole pipeline agrees on ONE KiCad install. File formats are not
   forward-compatible (a KiCad-10-format board is unreadable by kicad-cli 9,
   exit 3), so mixing versions between steps silently breaks gates.
2. Tests and CI can redirect discovery with environment variables instead of
   mutating the machine.

Environment overrides (all optional; a set-but-invalid override is an error,
never silently ignored - a wrong pin must fail loudly):

  HWDE_KICAD_CLI        full path to kicad-cli executable (pins the install)
  HWDE_KICAD_ROOT       KiCad install root containing bin/
  HWDE_JAVA             full path to a java executable (for Freerouting)
  HWDE_FREEROUTING_JAR  full path to a freerouting jar
  HWDE_PDFLATEX         full path to a pdflatex executable (for report_gen)
  HWDE_NGSPICE_DLL      full path to a shared ngspice library (for sim_run)
  HWDE_BOARDS_ROOT      where board workspaces live (default ~/dev/boards, the
                        boards repo; hwde itself holds no boards)
  HWDE_EASYEDA_CACHE    LCSC model cache (default: the board's easyeda/ dir if it
                        exists, else ~/.cache/hwde/easyeda)
  HWDE_TOOLS_DIR        the tools/ dir holding jre/, freerouting/ and krt/
                        (default: this checkout's tools/ when it has one, else
                        ~/dev/ai-ee/tools - a vendored copy of the skill, as in
                        the boards repo, has no tools/ of its own)

The skill was ai-ee before it was hwde, so the pre-rename AIEE_* spelling of
every one of these is still read (HWDE_ wins when both are set) - an existing
shell profile or CI job keeps working untouched. Errors name whichever
variable is actually set.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Minimum KiCad per SPEC.md section 1.
MIN_KICAD = (9, 0, 5)
# The exact KiCad the box host runs and CI runs (the image tag in
# .github/workflows/checks.yml and docker/Dockerfile; tests/test_kicad_pin.py
# holds them equal). check_env warns, never fails, on any other version:
# 10.0.x share one file format, but DRC/render numbers move between patches.
KICAD_PIN = (10, 0, 6)
# The pipeline authors all boards in KiCad 10 format and
# formats are not forward-compatible: a 9.x kicad-cli exits 3 on them. An
# EXPLICIT pin (HWDE_KICAD_CLI / HWDE_KICAD_ROOT) below major 10 is therefore
# always a stale-pin mistake and is rejected loudly (ladder row 18). Plain
# discovery still accepts >= MIN_KICAD, so 9.0.5 stays reachable for manual
# experiments by NOT pinning.
PIN_MIN_MAJOR = 10
# Preference order among installed majors. 10.x preferred: it adds
# `pcb drc --refill-zones --save-board` (headless zone refill without SWIG)
# and `sch upgrade`. 9.0.5 is the verified fallback.
KICAD_PREFERENCE = ("10.", "9.")
# Freerouting 2.2.4 requires Java 25 (class file v69). Vendor docs saying
# "Java 21+" are stale for 2.2.x - verified on this machine (Java 24 fails
# with UnsupportedClassVersionError).
MIN_JAVA_FOR_FREEROUTING = 25

_TIMEOUT = 30

# Linux headless: KiCad's SWIG Specctra export/import (route_swig) and other
# wx-backed pcbnew paths refuse to run without an X display ("Unable to access
# the X Display"). The container starts an Xvfb on :99 (docker/project-init);
# hand it to every child process when the caller has no display of its own.
if (sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
        and Path("/tmp/.X11-unix/X99").exists()):
    os.environ["DISPLAY"] = ":99"


class EnvError(RuntimeError):
    """An explicit override (HWDE_*/AIEE_*) points at something unusable."""


def skill_env(suffix: str) -> tuple[str | None, str]:
    """Read the skill's HWDE_<suffix> variable, falling back to pre-rename AIEE_<suffix>.

    Returns (value, varname). varname is the variable that supplied the value,
    so an error message points at the spelling the user actually set; when
    neither is set it is the current HWDE_ name.
    """
    for prefix in ("HWDE_", "AIEE_"):
        val = os.environ.get(prefix + suffix)
        if val:
            return val, prefix + suffix
    return None, "HWDE_" + suffix


def repo_root() -> Path:
    """Repo root = nearest ancestor of this file containing SPEC.md or .git."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "SPEC.md").exists() or (parent / ".git").exists():
            return parent
    # Fallback: fixed relative position .claude/skills/hwde/scripts/lib/env.py
    return p.parents[5]


def boards_root() -> Path:
    """Where board workspaces live: HWDE_BOARDS_ROOT, else ~/dev/boards.

    Boards are their own repo (ihsan-sa/boards), not part of the skill, so
    every default workspace location, sweep and render resolves through here.
    Not validated: a fresh box may not have cloned the boards repo yet, and
    the caller that needs a workspace says so when it is missing.
    """
    val, _ = skill_env("BOARDS_ROOT")
    return Path(val or "~/dev/boards").expanduser()


def easyeda_cache(board_dir: Path | None = None) -> Path:
    """The EasyEDA model cache: HWDE_EASYEDA_CACHE, else `<board_dir>/easyeda`
    when that directory exists, else ~/.cache/hwde/easyeda (the shared cache).

    The board-local dir only counts when it is there: a worktree checkout has
    none, and defaulting to a missing dir made every part look model-less.
    """
    val, _ = skill_env("EASYEDA_CACHE")
    if val:
        return Path(val).expanduser()
    if board_dir is not None and (Path(board_dir) / "easyeda").is_dir():
        return Path(board_dir) / "easyeda"
    return Path.home() / ".cache" / "hwde" / "easyeda"


def tools_dir() -> Path:
    """Where the vendored JRE, Freerouting and KRT live (tools/jre, ...).

    HWDE_TOOLS_DIR wins (a set-but-missing dir is an error). Otherwise the
    checkout's own tools/ when it exists, else the hwde checkout's at
    ~/dev/ai-ee/tools: a copy of the skill vendored into another repo (the
    boards repo) resolves repo_root() to that repo, which carries no tools/.
    """
    pin, var = skill_env("TOOLS_DIR")
    if pin:
        p = Path(pin).expanduser()
        if not p.is_dir():
            raise EnvError(f"{var} does not exist: {pin}")
        return p
    own = repo_root() / "tools"
    return own if own.is_dir() else Path("~/dev/ai-ee/tools").expanduser()


def is_boards_dir(path: Path) -> bool:
    """True when `path` is a directory that holds board workspaces: the
    configured boards_root(), or any directory named `boards` (the layout a
    test fixture or an older checkout uses)."""
    p = Path(path)
    if p.name == "boards":
        return True
    try:
        return p.resolve() == boards_root().resolve()
    except OSError:
        return False


def _run(cmd: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def parse_version(text: str) -> tuple[int, ...]:
    """First dotted integer group in text -> tuple. '9.0.5-1' -> (9, 0, 5)."""
    m = re.search(r"(\d+(?:\.\d+)+)", text)
    if not m:
        return ()
    return tuple(int(x) for x in m.group(1).split("."))


# ---------------------------------------------------------------- KiCad

def kicad_installs() -> list[Path]:
    """Discover KiCad install roots (dirs containing bin/kicad-cli[.exe])."""
    roots: list[Path] = []
    if sys.platform == "win32":
        for pf in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),):
            base = Path(pf) / "KiCad"
            if base.is_dir():
                roots += [d for d in base.iterdir()
                          if (d / "bin" / "kicad-cli.exe").exists()]
    elif sys.platform == "darwin":
        for app in Path("/Applications").glob("KiCad*/KiCad.app"):
            d = app / "Contents" / "MacOS"
            if (d / "kicad-cli").exists():
                roots.append(app / "Contents")
    else:
        w = shutil.which("kicad-cli")
        if w:
            roots.append(Path(w).parent.parent)
    return roots


def _cli_name() -> str:
    return "kicad-cli.exe" if sys.platform == "win32" else "kicad-cli"


def _cli_of_root(root: Path) -> Path:
    # Windows/Linux: <root>/bin/kicad-cli; macOS .app bundle: Contents/MacOS.
    for sub in ("bin", "MacOS"):
        c = root / sub / _cli_name()
        if c.exists():
            return c
    raise EnvError(f"KiCad root pin has no bin/{_cli_name()}: {root}")


def _validate_pin(path: Path, var: str = "HWDE_KICAD_CLI",
                  ver: tuple[int, ...] | None = None) -> Path:
    """Reject an explicit KiCad pin whose major is below the pipeline format.

    ver is probed from the binary when not given (tests pass a stub tuple)."""
    if ver is None:
        ver = kicad_cli_version(path)
    if not ver:
        raise EnvError(
            f"{var} points at {path} but its version could not be determined "
            "(not a working kicad-cli?); unset the pin or point it at the "
            "10.0.3 install")
    if ver[0] < PIN_MIN_MAJOR:
        vs = ".".join(str(x) for x in ver)
        raise EnvError(
            f"{var} pins KiCad {vs}: pipeline file formats are 10.x "
            "(9.x cannot load them); unset the stale pin or point it at the "
            "10.0.3 install")
    return path


def find_kicad_cli() -> Path | None:
    """Resolve the pipeline's kicad-cli. Explicit pin > PATH > preferred install."""
    pin, var = skill_env("KICAD_CLI")
    if pin:
        p = Path(pin)
        if not p.exists():
            raise EnvError(f"{var} does not exist: {pin}")
        return _validate_pin(p, var)
    root_pin, root_var = skill_env("KICAD_ROOT")
    if root_pin:
        return _validate_pin(_cli_of_root(Path(root_pin)), root_var)
    w = shutil.which("kicad-cli")
    if w:
        return Path(w)
    installs = kicad_installs()
    if not installs:
        return None
    scored: list[tuple[int, tuple[int, ...], Path]] = []
    for root in installs:
        cli = _cli_of_root(root)
        ver = kicad_cli_version(cli)
        if ver < MIN_KICAD:
            continue
        vs = ".".join(str(x) for x in ver)
        pref = next((len(KICAD_PREFERENCE) - i
                     for i, p in enumerate(KICAD_PREFERENCE) if vs.startswith(p)), 0)
        scored.append((pref, ver, cli))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[-1][2]


def kicad_cli_version(cli: Path) -> tuple[int, ...]:
    try:
        return parse_version(_run([str(cli), "version"]).stdout)
    except (OSError, subprocess.TimeoutExpired):
        return ()


_SYM_ENV_RE = re.compile(r"^KICAD(\d*)_SYMBOL_DIR$")


def kicad_symbol_dir() -> Path | None:
    """Stock symbol libraries of the pipeline's KiCad, or None.

    KiCad's own KICAD<N>_SYMBOL_DIR wins, highest major first (the same rule
    as board_swig's footprint root). Else the pinned kicad-cli's install:
    <root>/share/kicad/symbols (container, Windows) or, for the no-container
    Linux host's unpacked debs (bin/kicad-cli is a wrapper beside usr/),
    <root>/usr/share/kicad/symbols.
    """
    hits = []
    for k, v in os.environ.items():
        m = _SYM_ENV_RE.match(k)
        if m and v:
            hits.append((int(m.group(1)) if m.group(1) else -1, v))
    for _, v in sorted(hits, key=lambda kv: -kv[0]):
        if Path(v).is_dir():
            return Path(v)
    try:
        cli = find_kicad_cli()
    except EnvError:
        return None
    if cli is None:
        return None
    root = cli.parents[1]
    for cand in (root / "share" / "kicad" / "symbols",
                 root / "usr" / "share" / "kicad" / "symbols"):
        if cand.is_dir():
            return cand
    return None


def export_kicad_symbol_dir() -> None:
    """Point kicad-sch-api at the pinned KiCad's symbol libraries.

    It reads only KICAD_SYMBOL_DIR and KICAD7-9_SYMBOL_DIR, then fixed system
    paths, so on the no-container host (libs under ~/.local/kicad10/usr) it
    finds none and every lib_id lookup fails. Call before its first
    get_symbol_cache(); an existing KICAD_SYMBOL_DIR is left alone.
    """
    if os.environ.get("KICAD_SYMBOL_DIR"):
        return
    d = kicad_symbol_dir()
    if d is not None:
        os.environ["KICAD_SYMBOL_DIR"] = str(d)


def find_kicad_python(cli: Path) -> Path | None:
    """KiCad's BUNDLED python (has the SWIG pcbnew module; the venv does not).

    Needed for Specctra DSN export / SES import and KiCad-9 zone refill.
    """
    name = "python.exe" if sys.platform == "win32" else "python3"
    for cand in (cli.parent / name, cli.parent.parent / "bin" / name):
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------- Java / Freerouting

def java_major(java: Path) -> int:
    """Major version of a java executable, 0 if undeterminable."""
    try:
        cp = _run([str(java), "-version"])
    except (OSError, subprocess.TimeoutExpired):
        return 0
    ver = parse_version(cp.stderr or cp.stdout)
    if not ver:
        return 0
    return ver[1] if ver[0] == 1 and len(ver) > 1 else ver[0]  # "1.8" -> 8


def find_java() -> tuple[Path, int] | None:
    """Best java for Freerouting: HWDE_JAVA > tools_dir()/jre > PATH.

    Returns (path, major). Callers decide if major is sufficient.
    """
    pin, var = skill_env("JAVA")
    if pin:
        p = Path(pin)
        if not p.exists():
            raise EnvError(f"{var} does not exist: {pin}")
        return p, java_major(p)
    name = "java.exe" if sys.platform == "win32" else "java"
    candidates = sorted(tools_dir().glob(f"jre/*/bin/{name}"))
    w = shutil.which("java")
    if w:
        candidates.append(Path(w))
    best: tuple[Path, int] | None = None
    for c in candidates:
        major = java_major(c)
        if best is None or major > best[1]:
            best = (c, major)
    return best


def find_freerouting_jar() -> Path | None:
    pin, var = skill_env("FREEROUTING_JAR")
    if pin:
        p = Path(pin)
        if not p.exists():
            raise EnvError(f"{var} does not exist: {pin}")
        return p
    jars = sorted(tools_dir().glob("freerouting/freerouting-*.jar"))
    return jars[-1] if jars else None


def find_krt() -> Path | None:
    """KiCadRoutingTools plugins dir (vendored under tools/krt, S11).

    Returns the newest tools_dir()/krt/KiCadRoutingTools-*/plugins dir (the
    scripts sys.path-insert relative dirs, so callers must run them with
    cwd=plugins). Override with HWDE_KRT_DIR (points at the plugins dir).
    """
    pin, var = skill_env("KRT_DIR")
    if pin:
        p = Path(pin)
        if not p.is_dir():
            raise EnvError(f"{var} does not exist: {pin}")
        return p
    dirs = sorted(tools_dir().glob("krt/KiCadRoutingTools-*/plugins"))
    return dirs[-1] if dirs else None


# ---------------------------------------------------------------- ngspice (optional)

def find_ngspice_dll() -> Path | None:
    """Shared ngspice library for the SPICE sim gate (sim_run.py).

    Ladder: HWDE_NGSPICE_DLL pin > the DLL KiCad bundles beside kicad-cli
    (KiCad >= 9 ships ngspice for its own simulator) > None (sim gate cannot
    run). The pipeline loads it in-process via InSpice's shared-library
    binding - there is no ngspice.exe on this ladder on purpose: only the
    shared library exposes the ngGet_Vec_Info API the runner needs.
    """
    pin, var = skill_env("NGSPICE_DLL")
    if pin:
        p = Path(pin)
        if not p.exists():
            raise EnvError(f"{var} does not exist: {pin}")
        return p
    cli = find_kicad_cli()  # EnvError from a bad KiCad pin stays loud
    if cli is None:
        return None
    if sys.platform == "win32":
        name = "ngspice.dll"
    elif sys.platform == "darwin":
        name = "libngspice.dylib"
    else:
        name = "libngspice.so"
    cand = cli.parent / name
    return cand if cand.exists() else None


# ---------------------------------------------------------------- pdflatex (optional)

def find_pdflatex() -> Path | None:
    """pdflatex for report_gen's design document (optional toolchain member).

    Ladder: HWDE_PDFLATEX pin > PATH > the Windows per-user MiKTeX default
    install location. None = not installed (report_gen degrades to --tex-only).
    """
    pin, var = skill_env("PDFLATEX")
    if pin:
        p = Path(pin)
        if not p.exists():
            raise EnvError(f"{var} does not exist: {pin}")
        return p
    w = shutil.which("pdflatex")
    if w:
        return Path(w)
    if sys.platform == "win32":
        cand = (Path(os.environ.get("LOCALAPPDATA", ""))
                / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64"
                / "pdflatex.exe")
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------- Docker (optional)

def docker_state() -> str:
    """'up' (daemon reachable), 'down' (client only), or 'absent'."""
    if not shutil.which("docker"):
        return "absent"
    try:
        cp = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
        return "up" if cp.returncode == 0 and cp.stdout.strip() else "down"
    except (OSError, subprocess.TimeoutExpired):
        return "down"
