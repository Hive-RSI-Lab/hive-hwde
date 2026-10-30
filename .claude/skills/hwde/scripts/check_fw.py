"""check_fw.py - the board against the firmware that runs on it.

hive-fw's `fw constraints` (with `fw pins` inside it) reads what the firmware
takes for granted about the hardware: the module (flash, PSRAM, revision),
the 3.3 V supply's peak current for the radio, the GPIOs it drives and the
ESP32's rules for each, I2C addresses and bus speed. This check runs it on
this board and adds what only the board side can check:

 - fw_problem (error): a problem fw itself found (the module's flash or
   PSRAM, a firmware pin on the wrong net, a GPIO the board can't give).
 - fw_supply_budget (error): the ESP32's supply net is budgeted below the
   firmware's peak current in constraints.json power[] (warning: not
   budgeted at all).
 - fw_strap_pull (error): a pull that sets a strapping pin wrong at reset:
   GPIO12 or GPIO2 pulled up, GPIO0 pulled down.
 - fw_input_floating (error): a GPIO 34-39 the firmware reads whose net
   reaches nothing but the ESP32 (no pull-ups inside those pins).
 - fw_i2c_pullup (error): an I2C line the firmware drives with no resistor
   to a supply.
 - fw_unavailable (error, exit 2): no fw, or one without `constraints`.

The board's firmware targets come from fw's targets.json: every target
whose constraints board is this board's netlist (core: hifi-plus and
hifi-ultra), with the firmware read beside the board when the board's
project holds it (an agent's checkout), else from the target's project.
--target names them instead. Needs the board side can't check from a
netlist (antenna keep-out, crystal, I2C address straps) come back under
"needs" for the agent to weigh.

fw is --fw, else $HWDE_FW, else hive-fw in $HIVE_MODULES (or
/hive/modules, /hive/tools). It is standard-library python and takes a few
seconds; nothing here runs KiCad.

CLI:
  check_fw.py --board boards/core [--target hifi-plus ...]
              [--netlist X.net] [--constraints c.json] [--fw PATH] [--out F]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))

import checklib  # noqa: E402
from netlist_audit import _is_ground_name, parse_netlist  # noqa: E402

SCRIPT = "check_fw"
SOURCE = "fw.constraints"
RADIO_PEAK_A = 0.5  # Espressif's module datasheets, when fw's need gives no number
_RAIL_RE = re.compile(r"^\+|^(VDD|VCC)|^3V3$|^5V$", re.I)


def find_fw(given: str | None) -> Path | None:
    cands = [given, os.environ.get("HWDE_FW")]
    for root in (os.environ.get("HIVE_MODULES"), "/hive/modules", "/hive/tools"):
        if root:
            cands.append(str(Path(root) / "hive-fw" / "bin" / "fw"))
    for c in cands:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return Path(c).resolve()
    return None


def has_constraints(fw: Path) -> bool:
    try:
        r = subprocess.run([str(fw), "help"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "fw constraints" in r.stdout


def board_files(board: Path) -> tuple[Path | None, Path | None]:
    """(netlist, constraints.json) of a board dir (its kicad/) or a .net file."""
    if board.is_file():
        c = board.parent / "constraints.json"
        return board, c if c.is_file() else None
    kicad = board / "kicad" if (board / "kicad").is_dir() else board
    nets = sorted(kicad.glob("*.net"))
    own = [n for n in nets if n.stem == board.name]
    c = kicad / "constraints.json"
    return (own or nets or [None])[0], c if c.is_file() else None


def targets_for(fw: Path, netlist: Path, names: list[str]) -> list[dict]:
    """[{name, project}] for the board: the named targets, or every target
    whose constraints/pinmap board is this netlist. project is the firmware
    dir beside the board when its project holds one, else None (fw finds
    the target's own)."""
    try:
        targets = json.loads((fw.parent.parent / "targets.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        targets = {}
    net = netlist.resolve()
    out = []
    for name, t in targets.items():
        pins = t.get("pins", {})
        cfg = {**pins.get("pinmap", {}), **pins.get("constraints", {})}
        rels = cfg.get("board") or []
        rels = [rels] if isinstance(rels, str) else rels
        sub = t.get("path", ".")
        match, project = False, None
        for rel in rels:
            # The same board when the path's own parts (boards/core/kicad/core.net)
            # end this netlist's path: an agent's checkout or the project's clone.
            tail = [x for x in Path(rel).parts if x not in ("..", ".")]
            if tail and list(net.parts[-len(tail):]) == tail:
                match = True
                for root in net.parents:
                    fwdir = root / sub
                    if fwdir.is_dir() and (fwdir / rel).resolve() == net:
                        project = fwdir
                        break
        if (name in names) if names else match:
            out.append({"name": name, "project": project})
    for n in names:
        if n not in targets:
            out.append({"name": n, "project": None})
    return out


def run_fw(fw: Path, target: dict, netlist: Path) -> dict:
    cmd = [str(fw), "constraints"]
    if target["project"]:
        cmd.append(str(target["project"]))
    cmd += ["--target", target["name"], "--board", str(netlist), "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        raise checklib.CheckError(
            f"fw constraints --target {target['name']} gave no report (exit {r.returncode}): "
            f"{(r.stderr or r.stdout).strip()[-400:]}") from exc


def _net_of(parsed: dict, name: str | None) -> str | None:
    """The board's net for fw's name: exact, else by its last path part
    (the netlist names a hierarchical label /I2C_SDA)."""
    if not name:
        return None
    if name in parsed["nets"]:
        return name
    hits = [n for n in parsed["nets"] if n.rsplit("/", 1)[-1] == name.rsplit("/", 1)[-1]]
    return hits[0] if len(hits) == 1 else None


def _resistors_to(parsed: dict, net: str) -> list[tuple[str, str]]:
    """[(ref, far net)] of the two-pin resistors on net."""
    pins: dict[str, dict[str, str]] = {}
    for n, members in parsed["nets"].items():
        for m in members:
            if m["ref"] and m["ref"][:1] == "R" and m["ref"][1:2].isdigit():
                pins.setdefault(m["ref"], {})[m["pin"]] = n
    out = []
    for ref, by_pin in pins.items():
        nets = list(by_pin.values())
        if len(by_pin) == 2 and net in nets and nets.count(net) == 1:
            out.append((ref, next(x for x in nets if x != net)))
    return out


def _is_rail(net: str, declared: set[str] = frozenset()) -> bool:
    """A supply: declared in constraints.json power[]/voltages[], or named
    like one (+3V3, VDD_x, 3V3); never a ground."""
    base = net.rsplit("/", 1)[-1]
    if _is_ground_name(base):
        return False
    return net in declared or bool(_RAIL_RE.search(base))


def _peak_a(res: dict) -> float | None:
    radio = (res.get("module") or {}).get("radio") or {}
    for n in res.get("needs", []):
        if n.get("about") == "power":
            m = re.search(r"(\d+(?:\.\d+)?)\s*mA", n.get("need", ""))
            if m:
                return float(m.group(1)) / 1000
    return RADIO_PEAK_A if any(radio.values()) else None


def board_checks(res: dict, parsed: dict, constraints: dict | None, tag: str) -> list[dict]:
    v: list[dict] = []

    def add(check, sev, net, refs, msg, **x):
        v.append(checklib.violation(check, sev, None, None, net, refs, f"[{tag}] {msg}",
                                    SOURCE, **x))

    boards = [b for b in res.get("boards", []) if b.get("ref")]
    esp = boards[0]["ref"] if boards else None
    rows = [r for r in ((boards[0].get("pins") or {}).get("pins") or [])] if boards else []

    # The ESP32's supply against the radio's peak.
    declared = {p.get("net") for k in ("power", "voltages") for p in (constraints or {}).get(k, [])
                if isinstance(p, dict)}
    gpio_nets = {_net_of(parsed, r.get("net")) for r in rows}
    gpio_nets |= {_net_of(parsed, n) for n in ((boards[0].get("pins") or {}).get("unused") or {}).values()} if boards else set()
    peak = _peak_a(res)
    if esp and peak:
        rails = sorted({n for n, ms in parsed["nets"].items()
                        if n not in gpio_nets and _is_rail(n, declared)
                        and any(m["ref"] == esp for m in ms)})
        power = {p.get("net"): p for p in (constraints or {}).get("power", [])}
        for rail in rails:
            p = power.get(rail)
            if p is None:
                add("fw_supply_budget", "warning", rail, [esp],
                    f"{esp}'s supply {rail} has no power[] budget in constraints.json; "
                    f"the firmware's radio peaks at {peak:g} A")
            elif float(p.get("current_a") or 0) < peak:
                add("fw_supply_budget", "error", rail, [esp],
                    f"constraints.json budgets {rail} at {p.get('current_a')} A; the firmware "
                    f"needs {peak:g} A or more at {esp} for the radio's transmit peaks",
                    budget_a=p.get("current_a"), need_a=peak)

    seen = set()
    for r in rows:
        gpio, net = r.get("gpio"), _net_of(parsed, r.get("net"))
        if net is None or (gpio, net) in seen:
            continue
        seen.add((gpio, net))
        refs = [esp] if esp else []
        pulls = _resistors_to(parsed, net)
        ups = [ref for ref, far in pulls if _is_rail(far, declared)]
        downs = [ref for ref, far in pulls if _is_ground_name(far.rsplit("/", 1)[-1])]
        if gpio in (2, 12) and ups:
            add("fw_strap_pull", "error", net, refs + ups,
                f"GPIO{gpio} ({r.get('name')}, net {net}) is pulled up by {', '.join(ups)}; "
                + ("it must be low at reset or a 3.3 V module selects 1.8 V flash"
                   if gpio == 12 else "it must be low or floating at reset for serial download"))
        if gpio == 0 and downs:
            add("fw_strap_pull", "error", net, refs + downs,
                f"GPIO0 ({r.get('name')}, net {net}) is pulled down by {', '.join(downs)}; "
                "low at reset enters download mode")
        if gpio is not None and 34 <= gpio <= 39:
            others = {m["ref"] for m in parsed["nets"][net] if m["ref"] != esp}
            if not others:
                add("fw_input_floating", "error", net, refs,
                    f"GPIO{gpio} ({r.get('name')}, net {net}) is input only with no pull-up "
                    "inside, and nothing on the board drives or pulls it")
        if re.search(r"SDA|SCL", str(r.get("name")), re.I) and not ups:
            add("fw_i2c_pullup", "error", net, refs,
                f"I2C line GPIO{gpio} ({r.get('name')}, net {net}) has no pull-up resistor "
                f"to a supply" + (f"; the firmware runs it at {res['peripherals']['i2c_hz'] // 1000} kHz"
                                  if (res.get("peripherals") or {}).get("i2c_hz") else ""))
    return v


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True, help="the board dir (boards/<name>) or its .net")
    ap.add_argument("--netlist", help="the netlist, instead of the board's kicad/*.net")
    ap.add_argument("--constraints", help="constraints.json, instead of the board's")
    ap.add_argument("--target", action="append", default=[], help="a firmware target in fw's targets.json")
    ap.add_argument("--fw", help="hive-fw's bin/fw")
    ap.add_argument("--out", help="write the JSON report here")
    a = ap.parse_args(argv)

    board = Path(a.board).resolve()
    netlist, cpath = board_files(board)
    if a.netlist:
        netlist = Path(a.netlist).resolve()
    if a.constraints:
        cpath = Path(a.constraints).resolve()
    if netlist is None or not netlist.is_file():
        raise checklib.CheckError(f"no netlist for {board} (export one: kicad-cli sch export netlist)")
    fw = find_fw(a.fw)
    if fw is None:
        raise checklib.CheckError("fw_unavailable: no hive-fw bin/fw (--fw, $HWDE_FW, $HIVE_MODULES/hive-fw)")
    if not has_constraints(fw):
        raise checklib.CheckError(f"fw_unavailable: {fw} has no `fw constraints` (hive-fw older than issue #22)")
    targets = targets_for(fw, netlist, a.target)
    if not targets:
        raise checklib.CheckError(f"no firmware target in {fw.parent.parent / 'targets.json'} names "
                                  f"{netlist.name} as its board: give --target")

    parsed = parse_netlist(netlist)
    constraints = checklib.load_json(cpath, "constraints") if cpath else None
    violations: list[dict] = []
    ran = []
    for t in targets:
        res = run_fw(fw, t, netlist)
        for x in res.get("problems", []):
            violations.append(checklib.violation(
                "fw_problem", "error", None, None, None, [],
                f"[{t['name']}] {x.get('about')}: {x.get('problem')}", SOURCE, where=x.get("from")))
        violations += board_checks(res, parsed, constraints, t["name"])
        ran.append({"target": t["name"], "firmware": str(t["project"] or "the target's project"),
                    "needs": res.get("needs", []), "fw_ok": res.get("ok")})
    payload = checklib.report(SCRIPT, str(netlist), violations, fw=str(fw),
                              constraints=str(cpath) if cpath else None, targets=ran)
    payload["status"] = ("violations" if any(x["severity"] == "error" for x in violations)
                         else "pass")
    return payload, a.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
