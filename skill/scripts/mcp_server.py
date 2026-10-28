"""mcp_server - hwde's verbs as MCP tools over stdio (backlog item 8).

A LOCAL stdio MCP server: newline-delimited JSON-RPC 2.0 on stdin/stdout, one
client, the lifetime of that client's process. It opens no socket and listens
on no port - anything that listens needs the owner (docs/competitive-research.md
item 8), so a network transport is not an option of this script.

Another agent (or another skill on the box) registers it in a Claude Code
`.mcp.json` and gets hwde's deterministic answers as tool results:

  hwde_route      task_router.py - route a task to a verb, or list the verbs;
                  returns the bound plan. Plans only, never executes.
  hwde_state      state.py show | resume | freshness on a workspace.
  hwde_gate       gate.py --gate <name> on a workspace (input resolved the way
                  task_router binds a gate step); no name lists the gates.
  hwde_dfm_check  the dfm gate (gate.py re-exports the fab set to scratch).
  hwde_review     the deterministic half of `review` on an EXISTING workspace:
                  state resume plus the erc, drc_routed, verify and dfm gates.
                  Importing a new board (intake) stays with /hwde.

Every tool is read-only by default: gates run with --no-record, so state.json
is not touched, and the .kicad_prl kicad-cli creates beside a board it loads
is removed again, so the workspace is left as it was. `write: true` in a call's arguments records the gate result in
state.json (U16), and `commit` (a message, gate tools only) needs `write: true`
as well; without it the call is refused before anything runs.

Each tool runs its script as a subprocess of this interpreter, so the script's
stdout never mixes with the protocol stream. The result's text is the script's
JSON; `structuredContent` wraps it with the exit code. Exit 2 (the script's
error) comes back as isError; exit 1 (a failing gate, a question) is a normal
result the caller reads.

  mcp_server.py            # serve on stdin/stdout until EOF
  mcp_server.py --tools    # print the tool table as JSON and exit

exit 0 on EOF / --tools, 2 on a startup error.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = "mcp_server.py"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "hwde", "version": "1"}
REVIEW_GATES = ("erc", "drc_routed", "verify", "dfm")
TOOL_TIMEOUT_S = 3600  # a verify or dfm gate on a big board runs minutes

_WS = {"type": "string",
       "description": "workspace directory, or a board name / part number "
                      "resolved under the boards root (HWDE_BOARDS_ROOT)"}
_WRITE = {"type": "boolean", "default": False,
          "description": "record gate results in the workspace state.json; "
                         "without it the call writes nothing"}

TOOLS = [
    {"name": "hwde_route",
     "description": "Route a PCB task to an hwde verb and return its bound "
                    "plan (task_router.py). Read-only: it plans, it never "
                    "executes. list=true returns the verb table.",
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "the request in plain words"},
         "verb": {"type": "string", "description": "force a verb"},
         "workspace": _WS,
         "args": {"type": "object", "additionalProperties": {"type": "string"},
                  "description": "recipe arguments, K -> V"},
         "findings": {"type": "string",
                      "description": "gate result / report path for fix-finding"},
         "list": {"type": "boolean", "default": False}},
         "additionalProperties": False}},
    {"name": "hwde_state",
     "description": "Read a workspace's state.json: show (all of it), resume "
                    "(phase, gates, next step) or freshness (stale gates).",
     "inputSchema": {"type": "object", "properties": {
         "workspace": _WS,
         "view": {"type": "string", "enum": ["show", "resume", "freshness"],
                  "default": "resume"}},
         "required": ["workspace"], "additionalProperties": False}},
    {"name": "hwde_gate",
     "description": "Run one named hwde gate (gates.yaml) on a workspace and "
                    "return pass/fail with the failing findings. No gate name "
                    "lists the gates. Read-only unless write=true.",
     "inputSchema": {"type": "object", "properties": {
         "workspace": _WS,
         "gate": {"type": "string", "description": "gate name, e.g. drc_routed"},
         "write": _WRITE,
         "commit": {"type": "string",
                    "description": "git commit message on pass; needs write=true"}},
         "additionalProperties": False}},
    {"name": "hwde_dfm_check",
     "description": "JLCPCB manufacturability check of a workspace's board "
                    "(the dfm gate; fab files go to scratch, not the "
                    "workspace). Read-only unless write=true.",
     "inputSchema": {"type": "object", "properties": {
         "workspace": _WS, "write": _WRITE,
         "commit": {"type": "string",
                    "description": "git commit message on pass; needs write=true"}},
         "required": ["workspace"], "additionalProperties": False}},
    {"name": "hwde_review",
     "description": "Review an existing hwde workspace: state resume plus the "
                    "erc, drc_routed, verify and dfm gates, one result each. "
                    "Read-only unless write=true.",
     "inputSchema": {"type": "object", "properties": {
         "workspace": _WS, "write": _WRITE},
         "required": ["workspace"], "additionalProperties": False}},
]


class ToolError(Exception):
    """A refusal or bad call: the tool answers isError with this text."""


# ---------------------------------------------------------------------------
# script runners
# ---------------------------------------------------------------------------
def _run_script(name: str, argv: list[str]) -> dict:
    """Run scripts/<name> with argv; its stdout JSON plus the exit code."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *argv],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=TOOL_TIMEOUT_S,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"script": name, "status": "error",
                   "error": "no JSON on stdout",
                   "stdout": proc.stdout[-2000:]}
    out = {"exit": proc.returncode, "result": payload}
    if proc.stderr.strip():
        out["stderr"] = proc.stderr.strip()[-2000:]
    return out


def _workspace(arg) -> tuple[Path, dict]:
    """Resolve a workspace argument to its dir and task_router slot bindings.
    A workspace without state.json is refused: every tool here reads one."""
    if not isinstance(arg, str) or not arg.strip():
        raise ToolError("workspace is required")
    import boardreg
    import env
    import statelib
    import task_router
    ws = boardreg.locate(arg, env.boards_root())
    if not (ws / "state.json").is_file():
        raise ToolError(f"{ws} is not an hwde workspace (no state.json)")
    ctx = task_router.workspace_context(ws, None, statelib.load_map())
    return ws, ctx["slots"]


def _write_flags(args: dict) -> list[str]:
    """--no-record unless the call said write=true; commit only with it."""
    # brief: "Read-only verbs by default; any verb that writes a workspace
    # needs an explicit flag in the call."
    write = args.get("write", False)
    if not isinstance(write, bool):
        raise ToolError("write must be true or false")
    commit = args.get("commit")
    if commit is not None and not write:
        raise ToolError("commit writes the workspace: it needs write=true")
    flags = [] if write else ["--no-record"]
    if commit:
        flags += ["--commit", str(commit)]
    return flags


def _gate(gate: str, ws: Path, slots: dict, flags: list[str]) -> dict:
    import task_router
    step = task_router._gate_step(gate, slots)
    if not step.get("input_exists", False):
        return {"exit": None, "gate": gate, "skipped": True,
                "result": {"status": "skipped",
                           "reason": f"input {step.get('input')} does not exist"}}
    # kicad-cli drops a <project>.kicad_prl (its UI-local settings) beside
    # the board it loads. A read-only call removes the ones it created, so
    # the workspace tree is byte-identical afterwards; pre-existing ones stay.
    kdir = Path(step["input"]).parent
    before = set(kdir.glob("*.kicad_prl"))
    run = _run_script("gate.py", ["--gate", gate, step["input"],
                                  "--workspace", str(ws), *flags])
    if "--no-record" in flags:
        for prl in set(kdir.glob("*.kicad_prl")) - before:
            prl.unlink(missing_ok=True)
    return {"gate": gate, **run}


def tool_route(a: dict) -> dict:
    argv: list[str] = []
    if a.get("list"):
        argv.append("--list")
    for key in ("task", "verb", "findings"):
        if a.get(key):
            argv += [f"--{key}", str(a[key])]
    if a.get("workspace"):
        argv += ["--workspace", str(a["workspace"])]
    for k, v in (a.get("args") or {}).items():
        argv += ["--arg", f"{k}={v}"]
    return _run_script("task_router.py", argv)


def tool_state(a: dict) -> dict:
    view = a.get("view", "resume")
    if view not in ("show", "resume", "freshness"):
        raise ToolError(f"view must be show, resume or freshness, not {view!r}")
    ws, _ = _workspace(a.get("workspace"))
    # a read: resume must not migrate the workspace layout from here
    extra = ["--no-migrate"] if view == "resume" else []
    return _run_script("state.py", [view, "--workspace", str(ws)] + extra)


def tool_gate(a: dict) -> dict:
    if not a.get("gate"):
        return _run_script("gate.py", ["--list"])
    flags = _write_flags(a)
    ws, slots = _workspace(a.get("workspace"))
    return _gate(str(a["gate"]), ws, slots, flags)


def tool_dfm_check(a: dict) -> dict:
    return tool_gate({**a, "gate": "dfm"})


def tool_review(a: dict) -> dict:
    flags = _write_flags(a)
    ws, slots = _workspace(a.get("workspace"))
    state = _run_script("state.py", ["resume", "--workspace", str(ws)]
                        + (["--no-migrate"] if "--no-record" in flags else []))
    gates = [_gate(g, ws, slots, flags) for g in REVIEW_GATES]
    table = {g["gate"]: g["result"].get("status") for g in gates}
    worst = max((g["exit"] for g in gates if g["exit"] is not None), default=0)
    return {"exit": max(worst, state["exit"]),
            "result": {"script": SCRIPT, "tool": "hwde_review",
                       "workspace": str(ws), "recorded": "--no-record" not in flags,
                       "gates": table, "state": state["result"],
                       "gate_results": gates}}


HANDLERS = {"hwde_route": tool_route, "hwde_state": tool_state,
            "hwde_gate": tool_gate, "hwde_dfm_check": tool_dfm_check,
            "hwde_review": tool_review}


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------
def _call_tool(params: dict) -> dict:
    name = params.get("name")
    handler = HANDLERS.get(name)
    if handler is None:
        raise KeyError(name)
    args = params.get("arguments") or {}
    try:
        if not isinstance(args, dict):
            raise ToolError("arguments must be an object")
        out = handler(args)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}],
                "isError": True}
    except Exception as exc:  # noqa: BLE001  (a tool never kills the server)
        return {"content": [{"type": "text",
                             "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True}
    return {"content": [{"type": "text",
                         "text": json.dumps(out["result"], indent=1)}],
            "structuredContent": out,
            "isError": out.get("exit") == 2}


def handle(msg) -> dict | None:
    """One JSON-RPC message in, its response out (None for a notification)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    mid, method = msg.get("id"), msg.get("method")
    if "id" not in msg:
        return None  # notifications (initialized, cancelled) need no answer
    params = msg.get("params") or {}
    if method == "initialize":
        want = params.get("protocolVersion")
        return _ok(mid, {
            "protocolVersion": want if want in PROTOCOL_VERSIONS
            else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "hwde PCB tools. Read-only unless a call passes "
                            "write=true."})
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        try:
            return _ok(mid, _call_tool(params))
        except KeyError:
            return _error(mid, -32602, f"unknown tool {params.get('name')!r}")
    return _error(mid, -32601, f"method not found: {method}")


def _ok(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": code, "message": message}}


def serve(inp=None, out=None) -> int:
    inp = inp or sys.stdin
    out = out or sys.stdout
    for line in inp:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = _error(None, -32700, "parse error")
        else:
            reply = handle(msg)
        if reply is not None:
            out.write(json.dumps(reply) + "\n")
            out.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tools", action="store_true",
                    help="print the tool table as JSON and exit")
    args = ap.parse_args(argv)
    if args.tools:
        print(json.dumps({"script": SCRIPT, "tools": TOOLS}, indent=1))
        return 0
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
