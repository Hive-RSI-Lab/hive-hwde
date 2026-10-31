"""solvers.py - the open solvers hwde drives, from an IIC-OSIC-TOOLS tree.

The Hive keeps a read-only, unpacked IIC-OSIC-TOOLS image (the chip-flow
module's toolchain) that already holds the open simulators a board needs:

  ngspice    circuit (SPICE), the standalone binary
  xyce       circuit (Sandia Xyce, a second SPICE engine to cross-check ngspice)
  openems    full-wave 3D FDTD (openEMS + CSXCAD), driven by XML decks
  palace     full-wave 3D FEM (AWS Palace: eigenmode, driven S-params,
             electrostatic / magnetostatic extraction), driven by JSON + gmsh
  gmsh       the mesher Palace reads
  fasthenry  quasi-static inductance / resistance extraction (FastHenry2)
  fastercap  quasi-static capacitance extraction (FasterCap)

Every binary there is an Ubuntu 24.04 ELF built against glibc 2.39, and the
host's libc is newer, so each one runs through the image's own loader
(`ld-linux-x86-64.so.2 --library-path ...`), never the kernel's PT_INTERP
(the same approach as hive-chip-flow's bin/eda, see there for the long
account). Two traps of the tree that bin/eda's generic path does not cover:

  * usr/lib/x86_64-linux-gnu/liblapack.so.3 and libblas.so.3 are absolute
    symlinks into /etc/alternatives, so they resolve to the HOST's lapack,
    which wants the host's libm (GLIBC_2.43) and kills Xyce at load. The
    library path therefore names the image's openblas-pthread/ directory
    first, which holds real copies.
  * Xyce and Palace are MPI programs. Started bare, Open MPI's singleton
    init spawns `orted` by name, outside the loader, and dies. OPAL_PREFIX
    points Open MPI at the image, and ess_singleton_isolated=1 keeps the
    singleton from spawning anything; one rank is all hwde asks of either.

The tree is found at HWDE_EDA_TOOLCHAIN (or EDA_TOOLCHAIN, chip-flow's name),
else the newest /opt/eda-toolchain/iic-osic-tools-* or
~/.cc/toolchains/iic-osic-tools-*. No tree: every tool reports missing and
the callers say so; nothing here installs anything.

HFSS (Ansys) is the commercial full-wave reference. It needs a licence,
which is money and the person's call, so hwde does not drive it: Palace and
openEMS are the open stand-ins, each validated against closed-form answers
(sim_solve.py validate, docs/solvers.md).
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ENV_VARS = ("HWDE_EDA_TOOLCHAIN", "EDA_TOOLCHAIN")
SEARCH = ("/opt/eda-toolchain/iic-osic-tools-*",
          "~/.cc/toolchains/iic-osic-tools-*")

# tool -> (path in the tree, args that print its version, version regex)
TOOLS = {
    "ngspice": ("foss/tools/ngspice/bin/ngspice", ["-v"], r"ngspice-(\S+)"),
    "xyce": ("foss/tools/xyce/bin/Xyce", ["-v"], r"Xyce Release (\S+)"),
    "openems": ("foss/tools/openems/bin/openEMS", [], r"version (v\S+)"),
    "palace": ("foss/tools/palace/bin/palace-x86_64.bin", ["--version"],
               r"(?i)palace[^\n]*?(v?\d+\.\d+[\w.\-+]*)"),
    "gmsh": ("usr/bin/gmsh", ["--version"], r"(\d+\.\d+\.\d+)"),
    "fasthenry": ("foss/tools/rftoolkit/bin/fasthenry", [],
                  r"FastHenry (\S+)"),
    "fastercap": ("foss/tools/rftoolkit/bin/FasterCap", ["-v"],
                  r"(?i)FasterCap[^\n]*?(\d+\.\d+\.\d+)"),
}

# What each solver is for, for probe's table and docs/solvers.md.
KIND = {
    "ngspice": "circuit",
    "xyce": "circuit",
    "openems": "full-wave EM (FDTD)",
    "palace": "full-wave EM (FEM) + electrostatic/magnetostatic",
    "gmsh": "mesher",
    "fasthenry": "quasi-static L/R extraction",
    "fastercap": "quasi-static C extraction",
}


class SolverError(RuntimeError):
    pass


def toolchain_root() -> Path | None:
    for var in ENV_VARS:
        val = os.environ.get(var)
        if val:
            p = Path(val).expanduser()
            if not p.is_dir():
                raise SolverError(f"{var}={val} is not a directory")
            return p
    found: list[str] = []
    for pat in SEARCH:
        found += glob.glob(os.path.expanduser(pat))
    found = [f for f in found if Path(f, "lib64/ld-linux-x86-64.so.2").exists()]
    return Path(sorted(found)[-1]) if found else None


def _libpath(t: Path) -> str:
    u = t / "usr/lib/x86_64-linux-gnu"
    dirs = [u / "openblas-pthread", u, t / "usr/lib", u / "openmpi/lib",
            t / "foss/tools/openems/lib", t / "foss/tools/xyce/lib",
            t / "foss/tools/palace/lib"]
    return ":".join(str(d) for d in dirs)


def tool_path(tool: str, root: Path | None = None) -> Path | None:
    t = root or toolchain_root()
    if t is None or tool not in TOOLS:
        return None
    p = t / TOOLS[tool][0]
    return p if p.exists() else None


def command(tool: str, args: list[str], root: Path | None = None) -> tuple[list[str], dict]:
    """argv and environment that run `tool args` through the image's loader."""
    t = root or toolchain_root()
    if t is None:
        raise SolverError("no IIC-OSIC-TOOLS tree (set HWDE_EDA_TOOLCHAIN)")
    exe = tool_path(tool, t)
    if exe is None:
        raise SolverError(f"{tool} not in {t}")
    env = dict(os.environ)
    env.update({
        "OPAL_PREFIX": str(t / "usr"),
        "OMPI_MCA_ess_singleton_isolated": "1",
        "OMPI_MCA_btl": "self,vader",
        "OMPI_MCA_pml": "ob1",
        "OMPI_MCA_plm": "isolated",
        "OMP_NUM_THREADS": env.get("OMP_NUM_THREADS", "4"),
        # openblas-pthread's pool busy-waits; on a loaded machine Xyce's
        # 4 s RC deck ran past 300 s with it (load 244, 10-30). hwde's decks
        # are small, so one BLAS thread costs nothing.
        "OPENBLAS_NUM_THREADS": env.get("OPENBLAS_NUM_THREADS", "1"),
        # ngspice looks for its code models and init file under its prefix.
        "SPICE_LIB_DIR": str(t / "foss/tools/ngspice/share/ngspice"),
        "SPICE_SCRIPTS": str(t / "foss/tools/ngspice/share/ngspice/scripts"),
        "HWLOC_COMPONENTS": "-linux",
    })
    env.pop("PYTHONHOME", None)
    ld = t / "lib64/ld-linux-x86-64.so.2"
    return [str(ld), "--library-path", _libpath(t), str(exe), *args], env


@dataclass
class Run:
    tool: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(tool: str, args: list[str], cwd: Path | str | None = None,
        timeout: float = 1800, stdin: str | None = None,
        root: Path | None = None) -> Run:
    argv, env = command(tool, args, root)
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, input=stdin, text=True,
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise SolverError(f"{tool} ran past {timeout:.0f} s") from e
    return Run(tool, argv, p.returncode, p.stdout, p.stderr)


def version(tool: str, root: Path | None = None) -> str | None:
    """The tool's version, or None when it isn't there or won't start."""
    if tool_path(tool, root) is None:
        return None
    _, vargs, pat = TOOLS[tool]
    try:
        r = run(tool, vargs, timeout=60, stdin="", root=root)
    except SolverError:
        return None
    m = re.search(pat, r.stdout + r.stderr)
    if m:
        return m.group(1)
    return "present" if r.ok else None


def probe(root: Path | None = None) -> dict:
    t = root or toolchain_root()
    return {"toolchain": str(t) if t else None,
            "tools": {k: {"kind": KIND[k], "version": version(k, t) if t else None}
                      for k in TOOLS}}
