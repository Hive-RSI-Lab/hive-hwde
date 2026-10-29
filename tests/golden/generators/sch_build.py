"""Schematic builder for golden boards (runs in the repo venv).

Consumes a design module (see design_blinky2.py for the contract) and emits
<name>.kicad_sch via kicad-sch-api plus <name>.kicad_pro. The same design
module drives pcb_build.py (KiCad bundled python), so schematic and board
agree on references, values, footprints and net names by construction.

Conventions that keep ERC clean:
- ALL schematic coordinates on the 1.27 mm (50 mil) grid. kicad-sch-api
  snaps component anchors to that grid, so off-grid input silently shifts
  symbols and wires miss pins.
- Every used pin gets a short stub wire outward plus a local label with the
  net name; nets form by label name. Unused pins get no-connect markers.
- Each power rail gets a cluster: power symbol + optional PWR_FLAG + label
  on a short wire, so power_in pins see a driver on the net.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    import kicad_sch_api as ksa

GRID = 1.27
STUB = 2.54  # stub wire length, two grid steps


def _snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def _assert_on_grid(xy, what: str):
    for v in xy:
        if abs(v - _snap(v)) > 1e-6:
            raise ValueError(f"{what} at {xy} is off the 1.27 mm grid")


def _stub_dir(pin_rotation: float, comp_rotation: float):
    """Outward stub direction for a pin, in SHEET coords (+y down).

    KiCad pin rotation points from the connection point TOWARD the body
    (0=right, 90=up, ...). Outward is the opposite, with y inverted for
    sheet coordinates.
    """
    import math
    r = math.radians((pin_rotation + comp_rotation) % 360)
    dx = -math.cos(r)
    dy = math.sin(r)
    return (round(dx), round(dy))


def build_schematic(design: dict, out_dir: Path) -> Path:
    name = design["name"]
    sch = ksa.create_schematic(name)
    sch.set_paper_size(design.get("sch", {}).get("paper", "A3"))
    sch.set_title_block(
        title=design.get("title", name),
        date="2026-07-06",
        rev="1",
        company="ai-ee golden corpus",
    )

    # ---- components -------------------------------------------------
    for comp in design["components"]:
        ref = comp["ref"]
        pos = comp["sch"][:2]
        rot = comp["sch"][2] if len(comp["sch"]) > 2 else 0
        _assert_on_grid(pos, f"{ref} anchor")
        c = sch.components.add(
            comp["sym"], reference=ref, value=comp["value"],
            position=tuple(pos), rotation=rot,
        )
        c.footprint = comp["fp"]
        for key, val in comp.get("fields", {}).items():
            c.set_property(key, val)
        # optional pad-number -> pin-name sanity check (wiring insurance)
        expect = comp.get("expect", {})
        if expect:
            names = {p.number: p.name for p in c.pins}
            for pad, want in expect.items():
                got = names.get(pad, "<missing>")
                if want not in got:
                    raise ValueError(
                        f"{ref} pin {pad}: expected name ~'{want}', got '{got}'")

    # ---- pin stubs, labels, no-connects ------------------------------
    # design pins use the LIBRARY pin numbers; where kicad-sch-api has
    # renumbered (see _apply_pin_number_fixups), translate right -> wrong
    # for API lookups
    api_num = {}
    for fx in design.get("sch", {}).get("pin_number_fixups", []):
        api_num[(fx["lib_id"], fx["right"])] = fx["wrong"]
    for comp in design["components"]:
        ref = comp["ref"]
        c = sch.components.get(ref)
        rot = comp["sch"][2] if len(comp["sch"]) > 2 else 0
        pin_rot = {p.number: p.rotation for p in c.pins}
        for pad, net in sorted(comp["pins"].items(), key=lambda kv: kv[0]):
            pad = api_num.get((comp["sym"], pad), pad)
            ppos = sch.get_component_pin_position(ref, pad)
            if ppos is None:
                raise ValueError(f"{ref} pin {pad}: no such pin in symbol")
            p = (round(ppos.x, 4), round(ppos.y, 4))
            _assert_on_grid(p, f"{ref} pin {pad}")
            if net == "NC":
                sch.no_connects.add(position=p)
                continue
            d = _stub_dir(pin_rot[pad], rot)
            end = (round(p[0] + d[0] * STUB, 4), round(p[1] + d[1] * STUB, 4))
            sch.add_wire(start=p, end=end)
            # local labels (kicad-sch-api 0.5.5 add_global_label silently
            # writes NOTHING). Root-sheet local nets come out "/NAME" in the
            # netlist; pcb_build maps design names through the netmap.
            sch.add_label(net, position=end)

    # ---- power rail clusters -----------------------------------------
    # rails: list of {net, sym, pos, flag}. Pins connect only at wire
    # ENDPOINTS (mid-span pin contact does NOT connect), so: power symbol
    # pin on the left end, PWR_FLAG pin on the right end, label mid-wire.
    pwr_i = 0
    flg_i = 0
    for rail in design.get("sch", {}).get("rails", []):
        if "at_pin" in rail:
            # hang the power symbol off an existing pin stub end (no extra
            # wire: avoids an unconnected-wire-endpoint warning on driven
            # rails that must not carry a PWR_FLAG)
            ref, pad = rail["at_pin"]
            comp = next(c for c in design["components"] if c["ref"] == ref)
            rot = comp["sch"][2] if len(comp["sch"]) > 2 else 0
            cobj = sch.components.get(ref)
            prot = {p.number: p.rotation for p in cobj.pins}[pad]
            ppos = sch.get_component_pin_position(ref, pad)
            d = _stub_dir(prot, rot)
            tgt = (round(ppos.x + d[0] * STUB, 4), round(ppos.y + d[1] * STUB, 4))
            pwr_i += 1
            c = sch.components.add(
                rail["sym"], reference=f"#PWR{pwr_i:02d}",
                value=rail["sym"].split(":")[1], position=tgt,
            )
            got = sch.get_component_pin_position(c.reference, "1")
            c.translate(round(tgt[0] - got.x, 4), round(tgt[1] - got.y, 4))
            continue
        x, y = rail["pos"]
        _assert_on_grid((x, y), f"rail {rail['net']}")
        # without a PWR_FLAG the far end would dangle: shorten to one stub
        # and put the label on the endpoint
        has_flag = bool(rail.get("flag"))
        end = (x + (2 if has_flag else 1) * STUB, y)
        sch.add_wire(start=(x, y), end=end)
        sch.add_label(rail["net"], position=(x + STUB, y) if has_flag else end)
        if rail.get("sym"):
            pwr_i += 1
            c = sch.components.add(
                rail["sym"], reference=f"#PWR{pwr_i:02d}",
                value=rail["sym"].split(":")[1], position=(x, y),
            )
            got = sch.get_component_pin_position(c.reference, "1")
            c.translate(round(x - got.x, 4), round(y - got.y, 4))
        if rail.get("flag"):
            flg_i += 1
            c = sch.components.add(
                "power:PWR_FLAG", reference=f"#FLG{flg_i:02d}",
                value="PWR_FLAG", position=end,
            )
            got = sch.get_component_pin_position(c.reference, "1")
            c.translate(round(end[0] - got.x, 4), round(end[1] - got.y, 4))

    out_dir.mkdir(parents=True, exist_ok=True)
    sch_path = out_dir / f"{name}.kicad_sch"
    sch.save(str(sch_path))
    _apply_pin_number_fixups(design, sch_path)
    return sch_path


def _apply_pin_number_fixups(design: dict, sch_path: Path) -> None:
    """kicad-sch-api renumbers alphanumeric pin numbers (e.g. USB shield
    "SH" -> "6"), which breaks netlist-vs-footprint pad matching. Fix the
    saved file: design["sch"]["pin_number_fixups"] =
    [{"lib_id": "Connector:USB_B_Micro", "pin_name": "Shield",
      "wrong": "6", "right": "SH"}].
    """
    fixups = design.get("sch", {}).get("pin_number_fixups", [])
    if not fixups:
        return
    text = sch_path.read_text(encoding="utf-8")
    for fx in fixups:
        # 1. embedded lib symbol: the (number "wrong") right after the
        #    pin whose (name "pin_name") matches
        ni = text.find(f'(name "{fx["pin_name"]}"')
        if ni < 0:
            raise ValueError(f"pin fixup: name {fx['pin_name']} not found")
        old = f'(number "{fx["wrong"]}"'
        no = text.find(old, ni)
        if no < 0:
            raise ValueError(f"pin fixup: number {fx['wrong']} not found "
                             f"after name {fx['pin_name']}")
        text = text[:no] + f'(number "{fx["right"]}"' + text[no + len(old):]
        # 2. every instance of that lib_id: per-pin uuid entries (pin "wrong")
        pos = 0
        needle = f'(lib_id "{fx["lib_id"]}")'
        while True:
            li = text.find(needle, pos)
            if li < 0:
                break
            # instance block ends at the next (symbol or (wire top-level;
            # bounded search: next occurrence of '(lib_id' or end
            nxt = text.find('(lib_id "', li + len(needle))
            end = nxt if nxt > 0 else len(text)
            seg = text[li:end].replace(
                f'(pin "{fx["wrong"]}"', f'(pin "{fx["right"]}"')
            text = text[:li] + seg + text[end:]
            pos = li + len(seg)
    sch_path.write_text(text, encoding="utf-8")


def write_project(design: dict, out_dir: Path) -> Path:
    """Minimal .kicad_pro. Library-configuration checks are ignored on
    purpose: the corpus embeds its symbols/footprints and must stay hermetic
    across library updates. Keep this file MINIMAL - unexpected keys can make
    KiCad reject the whole file and silently fall back to defaults."""
    name = design["name"]
    pro = {
        "board": {
            "design_settings": {
                "rule_severities": {
                    "lib_footprint_issues": "ignore",
                    "lib_footprint_mismatch": "ignore",
                },
                # DRC minimums live in the PROJECT, not the board file.
                # Without this KiCad's 0.2 mm default track floor applies
                # (JLCPCB 2-4L capability is 0.127).
                "rules": {
                    "min_track_width": 0.127,
                },
            },
        },
        "erc": {
            "erc_exclusions": [],
            "meta": {"version": 0},
            "rule_severities": {
                "lib_symbol_issues": "ignore",
                "lib_symbol_mismatch": "ignore",
                "footprint_link_issues": "ignore",
            },
        },
        "meta": {"filename": f"{name}.kicad_pro", "version": 3},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.kicad_pro"
    path.write_text(json.dumps(pro, indent=2) + "\n", encoding="utf-8")
    return path
