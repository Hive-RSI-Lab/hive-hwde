#!/usr/bin/env python
"""smoke_ipc.py - probe whether the KiCad IPC API is reachable headless here.

Attempts, in order (stops at first success):

1. `kicad-cli api-server` subcommand (what kipy.server drives). Absent in
   KiCad 9.0.5 / 10.0.3 - present only in newer builds.
2. Connect to an already-running KiCad instance (default socket).
3. Sandboxed GUI launch: pcbnew.exe with KICAD_CONFIG_HOME pointing at a
   scratch config that pre-enables api.enable_server and seeds empty lib
   tables (skips first-run dialogs). The user's real KiCad config is never
   touched. A PCB editor window appears briefly.

Emits JSON to stdout:
  {"verdict": "headless-ok" | "running-instance-ok" | "gui-sandboxed-ok"
             | "unavailable",
   "attempts": [...]}

Exit 0 = probe completed (any verdict), 2 = probe itself crashed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import env  # noqa: E402

TRIVIAL_BOARD_SRC = """\
import sys, pcbnew
b = pcbnew.CreateEmptyBoard()
b.Save(sys.argv[1])
"""


def try_connect(timeout_ms: int = 3000) -> dict:
    """Connect via kipy and read what we can. Raises on failure."""
    from kipy import KiCad
    k = KiCad(timeout_ms=timeout_ms)
    info: dict = {"kicad_version": str(k.get_version())}
    try:
        b = k.get_board()
        info["board"] = getattr(b, "name", repr(b))[:100]
    except Exception as e:
        info["board_read"] = f"{type(e).__name__}: {e}"[:200]
    return info


def attempt_api_server(cli: Path) -> dict:
    a: dict = {"method": "kicad-cli api-server"}
    try:
        r = subprocess.run([str(cli), "api-server", "--help"],
                           capture_output=True, text=True, timeout=30)
        present = r.returncode == 0 and "api-server" in (r.stdout + r.stderr)
    except Exception as e:
        a.update(ok=False, error=f"{type(e).__name__}: {e}")
        return a
    if not present:
        a.update(ok=False, error="subcommand not present in this KiCad")
        return a
    # Subcommand exists: use kipy's own manager to run it and connect.
    try:
        from kipy.server import KiCadServer  # name per kipy 0.7 server helper
        with KiCadServer(kicad_cli_path=str(cli)) as srv:  # type: ignore[call-arg]
            a.update(ok=True, info=try_connect(), socket=getattr(srv, "socket_path", None))
    except Exception as e:
        a.update(ok=False, error=f"{type(e).__name__}: {e}"[:300])
    return a


def attempt_running_instance() -> dict:
    a: dict = {"method": "connect to running instance"}
    try:
        a.update(ok=True, info=try_connect())
    except Exception as e:
        a.update(ok=False, error=f"{type(e).__name__}: {e}"[:200])
    return a


def _seed_config(cfg: Path, version_dirs: tuple[str, ...]) -> None:
    common = json.dumps({"api": {"enable_server": True}})
    for sub in ("",) + version_dirs:
        d = cfg / sub if sub else cfg
        d.mkdir(parents=True, exist_ok=True)
        (d / "kicad_common.json").write_text(common, encoding="utf-8")
        (d / "fp-lib-table").write_text("(fp_lib_table\n  (version 7)\n)\n",
                                        encoding="utf-8")
        (d / "sym-lib-table").write_text("(sym_lib_table\n  (version 7)\n)\n",
                                         encoding="utf-8")


def attempt_gui_sandboxed(cli: Path) -> dict:
    a: dict = {"method": "sandboxed pcbnew.exe + IPC connect"}
    kpy = env.find_kicad_python(cli)
    name = "pcbnew.exe" if sys.platform == "win32" else "pcbnew"
    pcbnew_bin = cli.parent / name
    if kpy is None or not pcbnew_bin.exists():
        a.update(ok=False, error=f"missing bundled python or {pcbnew_bin}")
        return a
    ver = ".".join(map(str, env.kicad_cli_version(cli)[:2]))
    with tempfile.TemporaryDirectory(prefix="aiee_ipc_") as td:
        board = str(Path(td) / "trivial.kicad_pcb")
        mk = subprocess.run([str(kpy), "-c", TRIVIAL_BOARD_SRC, board],
                            capture_output=True, text=True, timeout=180)
        if mk.returncode != 0:
            a.update(ok=False, error="board fixture creation failed: "
                                     + mk.stderr[-200:])
            return a
        cfg = Path(td) / "cfg"
        _seed_config(cfg, (ver,))
        environ = dict(os.environ, KICAD_CONFIG_HOME=str(cfg))
        proc = subprocess.Popen([str(pcbnew_bin), board], env=environ,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 45
            last_err = "never attempted"
            while time.time() < deadline:
                time.sleep(3)
                if proc.poll() is not None:
                    a.update(ok=False,
                             error=f"pcbnew exited early rc={proc.returncode}")
                    return a
                try:
                    a.update(ok=True, info=try_connect())
                    return a
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"[:200]
            a.update(ok=False, error=f"timeout; last connect error: {last_err}")
            return a
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cli = env.find_kicad_cli()
    result: dict = {"kicad_cli": str(cli) if cli else None, "attempts": []}
    if cli is None:
        result["verdict"] = "unavailable"
        print(json.dumps(result))
        return 0

    a1 = attempt_api_server(cli)
    result["attempts"].append(a1)
    if a1.get("ok"):
        result["verdict"] = "headless-ok"
        print(json.dumps(result))
        return 0

    a2 = attempt_running_instance()
    result["attempts"].append(a2)
    if a2.get("ok"):
        result["verdict"] = "running-instance-ok"
        print(json.dumps(result))
        return 0

    a3 = attempt_gui_sandboxed(cli)
    result["attempts"].append(a3)
    result["verdict"] = "gui-sandboxed-ok" if a3.get("ok") else "unavailable"
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        print(json.dumps({"verdict": "error", "error": traceback.format_exc()}))
        sys.exit(2)
