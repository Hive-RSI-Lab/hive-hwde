"""schlib.py - kicad-sch-api helpers for generated schematics (SPEC 6.2, P4).

The generator-script pattern
----------------------------
A design's schematic SOURCE is Python: one generator per sheet at
`<project>/kicad/gen/<sheet>.py`, the `.kicad_sch` files are BUILD OUTPUT.
Each sheet generator builds a `schlib.Sheet`; the root generator stitches
child sheets with `schlib.Project` and saves everything:

    import schlib
    power = schlib.Sheet("power", pwr_base=20)   # unique #PWR/#FLG range
    power.add_component("Regulator_Linear:AMS1117-3.3", "U2", "AMS1117-3.3",
                        at=(81.28, 60.96), footprint="...")
    power.wire_pins("U2", {"1": "GND", "2": "+3V3"})    # stub + local label
    power.hier_pin("VIN", ref="U2", pad="3", shape="input")
    root = schlib.Sheet("board")
    proj = schlib.Project(root)
    proj.add_sheet(power, at=(127.0, 63.5), size=(25.4, 12.7), nets=["VIN"])
    proj.save(out_dir)

Conventions (S1-proven against kicad-cli 10.0.3 ERC, see LEARNINGS [python]):
- ALL coordinates on the 1.27 mm (50 mil) grid; off-grid input raises.
- Every used pin gets a short outward stub wire plus a LOCAL label; nets
  form by label name. kicad-sch-api 0.5.6 add_global_label writes NOTHING
  (silent no-op) - root-sheet local nets come out "/NAME" in netlists.
- Unused pins are marked "NC" (no-connect); unconnected inputs are errors.
- Pins connect at wire ENDPOINTS only (mid-span contact does not connect).
- Power rails: power SYMBOLS make a net global across the whole hierarchy
  (no sheet pin needed); undriven rails carry a PWR_FLAG. Signals cross
  sheets via hierarchical labels (child) + sheet pins (root).
- Component references must be unique across ALL sheets (each sheet is its
  own file; nothing re-annotates). Give each sheet its own numbering range,
  including pwr_base for the invisible #PWR/#FLG refs.
- Wiring/pinout facts come from datasheet-extract JSON, never from memory
  (SPEC section 5 grounding rule); `expect` pin-name checks are insurance.
- save() finishes with an automatic schem_refdes field-placement pass
  (electrically inert; place_fields=False opts out; see Sheet.place_report).
- save() also makes the sheet readable: stub labels read outward, away
  from their pin numbers (apply_label_justify), two-pin parts hide their
  pin names (hide_pin_names), and paper="auto" (the default) takes the
  smallest A-size the drawing fits, the drawing centred (fit_sheet;
  fit=False opts out). schem_refdes.audit_sheet flags a label on a part.
- Label anchors are guarded: one landing on a FOREIGN wire run would merge
  two nets and raises ValueError at generation time.

Decoupling metadata: `place_ic_with_decoupling` records cap<->pin
associations in the exact shape S4's check_decoupling.py consumes;
`emit_decoupling()` / `Project.save(decoupling=...)` writes the JSON.

CLI (grounding aid for P4 agents):
    python schlib.py --pins "Device:C" [--out pins.json]
prints the symbol's pin table (number/name/type) as JSON. Exit 0/2.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import re
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import env  # noqa: E402

env.export_kicad_symbol_dir()   # before kicad-sch-api's first library scan

# kicad-sch-api prints library-scan noise on import; keep stdout JSON-clean
# (LEARNINGS [python]).
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    import kicad_sch_api as ksa

GRID = 1.27          # KiCad schematic grid (50 mil)
STUB = 2.54          # pin stub wire length, two grid steps

_SCRIPT = "schlib"


def _stock_lib(lib_id: str) -> None:
    """Register lib_id's stock library with kicad-sch-api before placing it:
    a KiCad-10 .kicad_symdir library is flattened on first use (env)."""
    with _quiet():
        env.register_stock_libs(ksa.get_symbol_cache(), [lib_id])


@contextlib.contextmanager
def _quiet():
    """kicad-sch-api also prints at CALL time (emoji 'LOAD' lines on
    library misses); keep stdout JSON-clean around its entry points."""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        yield


def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def assert_on_grid(xy, what: str) -> None:
    for v in xy:
        if abs(v - snap(v)) > 1e-6:
            raise ValueError(f"{what} at {tuple(xy)} is off the 1.27 mm grid")


def stub_dir(pin_rotation: float, comp_rotation: float) -> tuple[int, int]:
    """Outward stub direction for a pin, in SHEET coords (+y down).

    KiCad pin rotation points from the connection point TOWARD the body
    (0=right, 90=up, ...). Outward is the opposite, with y inverted for
    sheet coordinates. T6 rotmirror fixture: this composition matches real
    KiCad at every rotation - the V19 "inward stubs" defect was ksa's pin
    POSITIONS at 90/270 (see pin_pos), never this sign."""
    r = math.radians((pin_rotation + comp_rotation) % 360)
    return (round(-math.cos(r)), round(math.sin(r)))


def _point_on_segment(pos, a, b, tol: float = 1e-6) -> bool:
    """Is pos on segment a-b (endpoints included)? Wires are axis-aligned
    grid stubs, but the cross/dot form costs nothing extra."""
    ax, ay = a
    bx, by = b
    px, py = pos
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > tol:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    return -tol <= dot <= (bx - ax) ** 2 + (by - ay) ** 2 + tol


def _label_rotation(d: tuple[int, int]) -> float:
    """Label/hier-label angle so the text extends outward from the wire."""
    return {(1, 0): 0.0, (-1, 0): 180.0, (0, -1): 90.0, (0, 1): 270.0}.get(d, 0.0)


class Sheet:
    """One schematic file under construction (root or hierarchical child)."""

    def __init__(self, name: str, title: str | None = None, paper: str = "auto",
                 rev: str = "1", date: str = "", company: str = "hwde",
                 pwr_base: int = 1):
        self.name = name
        with _quiet():
            self.sch = ksa.create_schematic(name)
        # "auto": save() picks the smallest paper the drawing fits (fit_sheet)
        self.paper = paper
        self.sch.set_paper_size("A4" if paper == "auto" else paper)
        self.fit_report: dict | None = None   # save-time paper fit
        self.sch.set_title_block(title=title or name, date=date, rev=rev,
                                 company=company)
        self.decoupling: list[dict] = []      # S4 association contract
        self.hier_pins: dict[str, str] = {}   # net -> shape (stitch contract)
        self.place_report: dict | None = None  # save-time field placement
        self._fixups: list[dict] = []         # saved-file pin-number repairs
        self._dnp: list[str] = []             # saved-file native-dnp repairs
        self._api_num: dict[tuple[str, str], str] = {}  # (lib_id, right)->wrong
        self._lib_ids: dict[str, str] = {}    # ref -> lib_id
        self._rotations: dict[str, float] = {}
        self._anchors: dict[str, tuple[float, float]] = {}
        self._wires: list[tuple] = []         # segments, for the label guard
        self._pin_nets: dict[tuple[str, str], str] = {}  # (ref, pad) -> net
        self._pwr_i = pwr_base - 1
        self._flg_i = pwr_base - 1
        self.uuid_map: dict[str, str] = {}    # save-time ksa uuid -> kept

    # ---------------------------------------------------------- components
    def add_component(self, lib_id: str, ref: str, value: str, at,
                      rotation: float = 0, footprint: str | None = None,
                      fields: dict | None = None, expect: dict | None = None,
                      fixups: list[dict] | None = None):
        """Place a symbol. `expect` maps pad number -> substring the library
        pin NAME must contain (wiring insurance against wrong symbols).
        `fixups` repair kicad-sch-api's silent renumbering of alphanumeric
        pin numbers on save: [{"pin_name": "Shield", "wrong": "6",
        "right": "SH"}] (LEARNINGS [python])."""
        assert_on_grid(at[:2], f"{ref} anchor")
        _stock_lib(lib_id)
        with _quiet():
            c = self.sch.components.add(lib_id, reference=ref, value=value,
                                        position=tuple(at[:2]),
                                        rotation=rotation)
        if footprint:
            c.footprint = footprint
        for key, val in (fields or {}).items():
            c.set_property(key, val)
        self._lib_ids[ref] = lib_id
        self._rotations[ref] = rotation
        self._anchors[ref] = (at[0], at[1])
        for fx in fixups or []:
            fx = dict(fx, lib_id=lib_id)
            self._fixups.append(fx)
            self._api_num[(lib_id, fx["right"])] = fx["wrong"]
        if expect:
            names = {p.number: p.name for p in c.pins}
            for pad, want in expect.items():
                got = names.get(pad, "<missing>")
                if want not in got:
                    raise ValueError(
                        f"{ref} pin {pad}: expected name ~'{want}', got '{got}'")
        return c

    def mark_dnp(self, ref: str) -> None:
        """Set KiCad's NATIVE do-not-populate flag on `ref`'s placed
        instance. kicad-sch-api 0.5.6 exposes no writable `dnp` field on a
        schematic symbol - its writer hard-codes `(dnp no)` on every
        instance (LEARNINGS [kicad-sch-api][schematic][bom] 2026-08-07) - so
        this only records intent here; `save()` repairs the saved file text
        via `apply_native_dnp`, the same saved-file-repair pattern
        `apply_pin_number_fixups` already uses for alphanumeric pin
        renumbering. Does not touch in_bom/on_board (still caller's choice
        via add_component fields) or any custom field (e.g. `Variant`)."""
        self._dnp.append(ref)

    def _api_pad(self, ref: str, pad: str) -> str:
        return self._api_num.get((self._lib_ids.get(ref, ""), pad), pad)

    def pin_pos(self, ref: str, pad: str) -> tuple[float, float]:
        p = self.sch.get_component_pin_position(ref, self._api_pad(ref, pad))
        if p is None:
            raise ValueError(f"{ref} pin {pad}: no such pin in symbol")
        x, y = p.x, p.y
        if self._rotations.get(ref, 0) % 360 in (90, 270):
            # V19 (T6, kicad-cli ERC-measured): kicad-sch-api 0.5.6 rotates
            # pin positions the WRONG WAY at 90/270 - its answer is the
            # true position mirrored through the anchor (R(-t) vs R(t);
            # correction R(2t) = -I at right angles). Real KiCad puts a
            # rot-90 Device:R pin1 (lib (0,+3.81)) at anchor x-3.81; ksa
            # says x+3.81. Reflect back through the anchor.
            ax, ay = self._anchors[ref]
            x, y = 2 * ax - x, 2 * ay - y
        return (round(x, 4), round(y, 4))

    def _pin_out_dir(self, ref: str, pad: str) -> tuple[int, int]:
        c = self.sch.components.get(ref)
        rot = {p.number: p.rotation for p in c.pins}[self._api_pad(ref, pad)]
        return stub_dir(rot, self._rotations.get(ref, 0))

    # ---------------------------------------------------------- wiring
    def _add_wire(self, start, end) -> tuple:
        """Add a wire and record its segment for the label guard."""
        self.sch.add_wire(start=start, end=end)
        seg = ((round(start[0], 4), round(start[1], 4)),
               (round(end[0], 4), round(end[1], 4)))
        self._wires.append(seg)
        return seg

    def _assert_label_clear(self, pos, net: str, own: tuple | None = None):
        """Labels attach anywhere ALONG a wire (LEARNINGS [erc]): an anchor
        landing on a FOREIGN wire run - interior or endpoint - silently
        merges two nets. Reject at generation time (ladder row 39).
        Segments geometrically identical to `own` are the label's own
        wire and are exempt."""
        p = (round(pos[0], 4), round(pos[1], 4))
        for seg in self._wires:
            if own is not None and seg == own:
                continue
            if _point_on_segment(p, seg[0], seg[1]):
                raise ValueError(
                    f"label '{net}' at {p} lands on an existing wire run "
                    f"{seg[0]}-{seg[1]} - it would merge nets")

    def wire_pin(self, ref: str, pad: str, net: str) -> None:
        """The auto-wire idiom: outward stub from the pin's actual position
        plus a local label naming the net. net == "NC" -> no-connect."""
        p = self.pin_pos(ref, pad)
        assert_on_grid(p, f"{ref} pin {pad}")
        if net == "NC":
            self.sch.no_connects.add(position=p)
            return
        d = self._pin_out_dir(ref, pad)
        end = (round(p[0] + d[0] * STUB, 4), round(p[1] + d[1] * STUB, 4))
        seg = self._add_wire(p, end)
        self._assert_label_clear(end, net, own=seg)
        # the text runs outward, away from the pin and its printed number
        self.sch.add_label(net, position=end, rotation=_label_rotation(d))
        self._pin_nets[(ref, pad)] = net

    def wire_pins(self, ref: str, pins: dict) -> None:
        """Wire every pad of `pins` ({pad: net}); deterministic order."""
        for pad, net in sorted(pins.items(), key=lambda kv: kv[0]):
            self.wire_pin(ref, pad, net)

    # ---------------------------------------------------------- power
    def power_flag(self, net: str, at, sym: str | None = None,
                   flag: bool = True) -> None:
        """Power-rail cluster: optional power symbol at the LEFT end,
        optional PWR_FLAG at the RIGHT end, label on the wire. Pins connect
        only at wire ENDPOINTS, so both symbols sit on endpoints and the
        label goes mid-wire when both ends are taken."""
        if not sym and not flag:
            raise ValueError(f"rail {net}: need a power symbol or PWR_FLAG")
        x, y = at
        assert_on_grid((x, y), f"rail {net}")
        end = (x + (2 if sym and flag else 1) * STUB, y)
        seg = self._add_wire((x, y), end)
        # every wire ENDPOINT must carry a pin or a label, or ERC flags it
        if sym and flag:
            label_at = (x + STUB, y)      # symbol start, flag end: label mid
        elif sym:
            label_at = end                # symbol start, label terminates end
        else:
            label_at = (x, y)             # label terminates start, flag end
        self._assert_label_clear(label_at, net, own=seg)
        # a label on the wire's start reads leftward, off the wire and flag
        self.sch.add_label(net, position=label_at,
                           rotation=180.0 if label_at == (x, y) else 0.0)
        if sym:
            self._pwr_i += 1
            # VALUE = the rail net: a power symbol's exported net name is
            # its VALUE, and the symbol WINS over a coincident label
            # (LEARNINGS [kicad]; ladder row 94) - equal by construction.
            self._place_pin1(sym, f"#PWR{self._pwr_i:02d}", net, (x, y))
        if flag:
            self._flg_i += 1
            self._place_pin1("power:PWR_FLAG", f"#FLG{self._flg_i:02d}",
                             "PWR_FLAG", end)

    def power_symbol_at_pin(self, ref: str, pad: str, sym: str) -> None:
        """Hang a power symbol off an existing pin stub end (no extra wire:
        avoids an unconnected-endpoint warning on driven rails that must not
        carry a PWR_FLAG). The symbol's VALUE - which names the exported
        global net - is taken from the net the pin was wired to, so the
        divergence class of ladder row 94 cannot exist. Wire the pin first."""
        net = self._pin_nets.get((ref, pad))
        if net is None:
            raise ValueError(
                f"{ref} pin {pad}: not wired yet - power_symbol_at_pin "
                f"derives the symbol VALUE (= global net name) from the "
                f"pin's wired net; call wire_pin/wire_pins first")
        p = self.pin_pos(ref, pad)
        d = self._pin_out_dir(ref, pad)
        tgt = (round(p[0] + d[0] * STUB, 4), round(p[1] + d[1] * STUB, 4))
        self._pwr_i += 1
        self._place_pin1(sym, f"#PWR{self._pwr_i:02d}", net, tgt)

    def _place_pin1(self, lib_id: str, ref: str, value: str, at) -> None:
        """Add a one-pin symbol with its PIN (not anchor) at `at`. The
        VALUE is enforced through the dedicated attribute too: on power
        symbols it names the exported global net, and set_property("Value")
        is a silent no-op on ksa 0.5.6 (LEARNINGS [kicad-sch-api])."""
        _stock_lib(lib_id)
        with _quiet():
            c = self.sch.components.add(lib_id, reference=ref, value=value,
                                        position=at)
            if getattr(c, "value", None) != value:
                c.value = value
        got = self.sch.get_component_pin_position(c.reference, "1")
        c.translate(round(at[0] - got.x, 4), round(at[1] - got.y, 4))

    # ---------------------------------------------------------- hierarchy
    def hier_pin(self, net: str, shape: str = "passive",
                 ref: str | None = None, pad: str | None = None,
                 at=None) -> None:
        """Expose `net` to the parent sheet. With ref/pad: stub from that
        pin with the hierarchical label on the stub end. With `at`: a small
        cluster in free area - wire with a LOCAL label of the net name at
        one end and the hierarchical label at the other, so the hier label
        joins the net by wire geometry (label name-merge not relied on)."""
        if ref is not None and pad is not None:
            p = self.pin_pos(ref, pad)
            assert_on_grid(p, f"{ref} pin {pad}")
            d = self._pin_out_dir(ref, pad)
            end = (round(p[0] + d[0] * STUB, 4), round(p[1] + d[1] * STUB, 4))
            seg = self._add_wire(p, end)
            self._assert_label_clear(end, net, own=seg)
            self.sch.add_hierarchical_label(net, position=end, shape=shape,
                                            rotation=_label_rotation(d))
            self._pin_nets[(ref, pad)] = net
        elif at is not None:
            x, y = at[:2]
            assert_on_grid((x, y), f"hier pin {net}")
            end = (round(x + STUB, 4), y)
            seg = self._add_wire((x, y), end)
            self._assert_label_clear((x, y), net, own=seg)
            self._assert_label_clear(end, net, own=seg)
            self.sch.add_label(net, position=(x, y), rotation=180.0)
            self.sch.add_hierarchical_label(net, position=end, shape=shape)
        else:
            raise ValueError(f"hier_pin {net}: give ref/pad or at")
        self.hier_pins[net] = shape

    # ---------------------------------------------------------- decoupling
    def place_ic_with_decoupling(self, ref: str, lib_id: str, value: str, at,
                                 pins: dict, footprint: str | None = None,
                                 rotation: float = 0,
                                 decoupling: list[dict] | None = None,
                                 caps_at=None, caps_dx: float = 12.7,
                                 expect: dict | None = None,
                                 fixups: list[dict] | None = None):
        """Place an IC, wire ALL its pins (stub+label / NC), and place one
        decoupling cap per `decoupling` entry in a row starting at `caps_at`
        (a free area the caller guarantees; caps connect by label, so their
        position is electrically free but must not collide with symbols).

        decoupling entries:
            {"cap": "C1", "pin": "48", "rail": "+3V3", "value": "100nF",
             "gnd": "GND",                       # optional, default GND
             "rail_net"/"gnd_net": "...",        # optional: exact NETLIST
                                                 #   names when they differ
                                                 #   from the wiring labels
             "lib_id": "Device:C",               # optional
             "footprint": "...",                 # optional
             "class"/"max_dist_mm"/"max_loop_nh"/"role": ...}  # passthrough
        Declare `"role": "reg_input"` on EVERY cap serving a switching
        regulator's input pin (buck/boost VIN) - check_decoupling then
        requires at least one HF ceramic (<= 1 uF) within 7.5 mm of that
        pin, the defect class that shipped lumina-carrier without U21's
        input ceramic (retro R1). The bulk input cap gets the role too;
        it does not tighten its own limits.
        Each entry is recorded as a cap<->pin association in EXACTLY the
        shape S4's check_decoupling.py consumes (see emit_decoupling).
        rail/gnd are the LOCAL wiring label names; the recorded metadata
        must carry the FINAL netlist names (S4: "exact board name"), which
        differ for root-local labels ("/NAME") and hier-crossed nets - give
        rail_net/gnd_net there. netlist_audit.py --decoupling verifies the
        recorded names against the real netlist."""
        ic = self.add_component(lib_id, ref, value, at, rotation=rotation,
                                footprint=footprint, expect=expect,
                                fixups=fixups)
        self.wire_pins(ref, pins)
        decoupling = decoupling or []
        if decoupling and caps_at is None:
            raise ValueError(f"{ref}: decoupling entries need caps_at")
        for i, ent in enumerate(decoupling):
            pin = str(ent["pin"])
            if pin not in pins:
                raise ValueError(f"{ref}: decoupling pin {pin} not in pins")
            rail = ent["rail"]
            if pins[pin] != rail:
                raise ValueError(
                    f"{ref} pin {pin}: wired to '{pins[pin]}' but decoupling "
                    f"entry says rail '{rail}'")
            gnd = ent.get("gnd", "GND")
            cx = round(caps_at[0] + i * caps_dx, 4)
            cy = round(caps_at[1], 4)
            assert_on_grid((cx, cy), f"decoupling cap {ent['cap']}")
            self.add_component(ent.get("lib_id", "Device:C"), ent["cap"],
                               ent["value"], (cx, cy),
                               footprint=ent.get("footprint"))
            self.wire_pin(ent["cap"], "1", rail)
            self.wire_pin(ent["cap"], "2", gnd)
            assoc = {"cap": ent["cap"], "ic": ref, "pin": pin,
                     "rail": ent.get("rail_net", rail),
                     "value": ent["value"]}
            gnd_net = ent.get("gnd_net", gnd)
            if gnd_net != "GND":
                assoc["gnd"] = gnd_net
            for opt in ("class", "max_dist_mm", "max_loop_nh", "role"):
                if opt in ent:
                    assoc[opt] = ent[opt]
            self.decoupling.append(assoc)
        return ic

    def emit_decoupling(self, path: Path | str,
                        extra: list[dict] | None = None) -> Path:
        """Write the cap<->pin association metadata (S4 contract)."""
        payload = {"associations": self.decoupling + (extra or [])}
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        return p

    # ---------------------------------------------------------- save
    def save(self, out_dir: Path | str, project: bool = True,
             place_fields: bool = True, keep_uuids: bool = True,
             fit: bool = True) -> Path:
        """Write the sheet. place_fields (default) runs schem_refdes on the
        saved file so Reference/Value land on the correct side and clear of
        wires/labels/bodies (ksa mirrors every field to the wrong side -
        T6, ladder row 166 L3). Electrically inert; see self.place_report.
        keep_uuids (default): regenerating keeps the UUIDs of the file it
        overwrites, and anything new gets one derived from what it is, so
        the board's footprint paths keep matching (see stable_uuids).
        fit (default) sizes the paper to the drawing and centres it
        (fit_sheet), after labels are justified away from their pins and
        two-pin symbols' pin names are hidden (hide_pin_names)."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.name}.kicad_sch"
        previous = (path.read_text(encoding="utf-8")
                    if keep_uuids and path.exists() else None)
        with _quiet():
            self.sch.save(str(path))
        import schem_refdes as sr  # sibling script; deferred (shapely)
        sr.strip_private_properties(path)   # KiCad-10 stock-lib notes ksa mangles (exit 3 at ERC)
        apply_pin_number_fixups(self._fixups, path)
        apply_label_justify(path)
        if place_fields:
            self._place_fields(path)
        # after field placement: its kicad-sch-api re-save drops pin_names
        hide_pin_names(path)
        if fit:
            self.fit_report = fit_sheet(path, self.paper)
        # native dnp LAST: write_placements() may round-trip the file
        # through kicad-sch-api (schem_refdes.write_placements ->
        # ksa.Schematic.load().save() when any field actually moved), and
        # ksa's writer hard-codes `(dnp no)` on every symbol instance
        # regardless of what it parsed - an earlier dnp patch would be
        # silently wiped by that later whole-file re-save (measured: with
        # the call before place_fields, J3/J4 came back `(dnp no)`).
        apply_native_dnp(self._dnp, path)
        if keep_uuids:
            self.uuid_map = stable_uuids(path, previous, self.name)
        if project:
            write_project(self.name, out_dir)
        return path

    def _place_fields(self, path: Path) -> None:
        """NON-FATAL field placement: a failure must never break
        generation - the file is restored to the valid un-placed state and
        the error recorded in self.place_report."""
        import schem_refdes as sr  # sibling script; deferred (shapely)
        snapshot = path.read_text(encoding="utf-8")
        try:
            res = sr.place_sheet(sr.Sheet(path))
            sr.write_placements(path, res)   # project libs + lib_symbols guard
            self.place_report = {
                "moved": sum(1 for p in res["placements"] if p["moved"]),
                "residue": res["residue"]}
        except Exception as exc:  # noqa: BLE001 - never break generation
            path.write_text(snapshot, encoding="utf-8")
            self.place_report = {"error": f"{type(exc).__name__}: {exc}"}


class Project:
    """Root sheet + hierarchical children; stitches sheet pins and saves
    the whole set (root's name names the project)."""

    def __init__(self, root: Sheet):
        self.root = root
        self.children: list[Sheet] = []

    def add_sheet(self, child: Sheet, at, size, nets=(),
                  label_stub: float = 3 * STUB) -> None:
        """Instantiate `child` on the root at `at` (top-left) with `size`
        (w, h). `nets`: the child hier pins to expose, as names (root label
        = same name) or (child_net, root_label) pairs. Sheet pins stack on
        the LEFT edge top-down; each is wired outward to a root local label.
        Rails via power symbols are global and need no entry here."""
        assert_on_grid(at[:2], f"sheet {child.name}")
        entries = [(n, n) if isinstance(n, str) else tuple(n) for n in nets]
        missing = [n for n, _ in entries if n not in child.hier_pins]
        if missing:
            raise ValueError(
                f"sheet {child.name}: no hier_pin for {missing} "
                f"(has {sorted(child.hier_pins)})")
        suid = self.root.sch.add_sheet(child.name, f"{child.name}.kicad_sch",
                                       position=tuple(at[:2]),
                                       size=tuple(size[:2]))
        h = size[1]
        for i, (net, _root_label) in enumerate(entries):
            along = round(h - 2 * GRID * (i + 1), 4)
            if along <= 0:
                raise ValueError(f"sheet {child.name}: too many pins for "
                                 f"height {h}")
            self.root.sch.add_sheet_pin(suid, net, child.hier_pins[net],
                                        "left", along)
        sh = self.root.sch.sheets.get_sheet_by_name(child.name)
        for (net, root_label), sp in zip(entries, sh["pins"]):
            px, py = sp["position"]["x"], sp["position"]["y"]
            assert_on_grid((px, py), f"sheet pin {net}")
            end = (round(px - label_stub, 4), py)
            seg = self.root._add_wire((px, py), end)
            self.root._assert_label_clear(end, root_label, own=seg)
            self.root.sch.add_label(root_label, position=end, rotation=180.0)
        if child not in self.children:
            self.children.append(child)

    @property
    def decoupling(self) -> list[dict]:
        out = list(self.root.decoupling)
        for c in self.children:
            out.extend(c.decoupling)
        return out

    def save(self, out_dir: Path | str,
             decoupling: Path | str | None = None,
             place_fields: bool = True) -> Path:
        out_dir = Path(out_dir)
        for c in self.children:
            c.save(out_dir, project=False, place_fields=place_fields)
        path = self.root.save(out_dir, project=True,
                              place_fields=place_fields)
        # A child's instance paths name the root's and its sheet's UUIDs,
        # which the root's save just settled: carry them into every file.
        moved = {}
        for sh in (self.root, *self.children):
            moved.update(sh.uuid_map)
        for c in self.children:
            _rewrite_uuids(out_dir / f"{c.name}.kicad_sch", moved)
        if decoupling is not None:
            payload = {"associations": self.decoupling}
            p = Path(decoupling)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, indent=1) + "\n",
                         encoding="utf-8")
        return path


_UUID_NS = uuid.UUID("5b0c2a8e-6f3d-4f53-9a8e-6877646573c1")  # hwde schematics
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SEXP_TOK = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')


def _sexp(text: str) -> list:
    """The file's forms as nested lists; quoted strings keep their quotes
    (so `"uuid"` the string and `uuid` the head stay apart)."""
    stack: list[list] = [[]]
    for tok in _SEXP_TOK.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(tok)
    return stack[0]


def _unq(tok) -> str:
    return tok[1:-1] if isinstance(tok, str) and tok[:1] == '"' else str(tok)


def _child(form: list, head: str) -> list | None:
    return next((c for c in form[1:] if isinstance(c, list) and c
                 and c[0] == head), None)


def _prop(form: list, name: str) -> str | None:
    for c in form[1:]:
        if isinstance(c, list) and len(c) > 2 and c[0] == "property" \
                and _unq(c[1]) == name:
            return _unq(c[2])
    return None


def _uuid_of(form: list) -> str | None:
    u = _child(form, "uuid")
    return _unq(u[1]) if u and len(u) > 1 else None


def _at(form: list) -> tuple:
    a = _child(form, "at")
    return tuple(round(float(v), 4) for v in a[1:3]) if a else ()


def uuid_keys(text: str) -> list[tuple[tuple, str]]:
    """(key, uuid) for every UUID-bearing object of a .kicad_sch, the key
    being what the object IS, not where ksa happened to number it: the
    file; a symbol by Reference (+unit) and its pins by number; a wire/bus
    by its end points; a label, junction, no-connect, text by name and
    place; a sheet by Sheetname and its pins by name. Duplicate keys get an
    occurrence index, in file order."""
    top = next((f for f in _sexp(text) if isinstance(f, list) and f
                and f[0] == "kicad_sch"), None)
    if top is None:
        raise ValueError("not a kicad_sch file")
    out: list[tuple[tuple, str]] = []
    for f in top[1:]:
        if not (isinstance(f, list) and f):
            continue
        head = f[0]
        if head == "uuid":
            out.append((("file",), _unq(f[1])))
            continue
        u = _uuid_of(f)
        if head == "symbol":
            unit = _child(f, "unit")
            base = (_prop(f, "Reference") or "?",
                    _unq(unit[1]) if unit else "1")
            key = ("symbol",) + base
            pins = [(("pin",) + base + (_unq(p[1]),), _uuid_of(p))
                    for p in f[1:] if isinstance(p, list) and p
                    and p[0] == "pin"]
        elif head == "sheet":
            name = _prop(f, "Sheetname") or _prop(f, "Sheet name") or "?"
            key = ("sheet", name)
            pins = [(("sheet_pin", name, _unq(p[1])), _uuid_of(p))
                    for p in f[1:] if isinstance(p, list) and p
                    and p[0] == "pin"]
        else:
            pts = _child(f, "pts")
            where = (tuple(sorted(tuple(round(float(v), 4) for v in xy[1:3])
                                  for xy in pts[1:] if isinstance(xy, list)))
                     if pts else _at(f))
            name = _unq(f[1]) if len(f) > 1 and not isinstance(f[1], list) \
                else ""
            key = (head, name, where)
            pins = []
        if u:
            out.append((key, u))
        out.extend((k, pu) for k, pu in pins if pu)
    seen: dict[tuple, int] = {}
    numbered = []
    for k, u in out:
        n = seen.get(k, 0)
        seen[k] = n + 1
        numbered.append((k + (n,) if n else k, u))
    return numbered


def _rewrite_uuids(path: Path, mapping: dict[str, str]) -> None:
    if not mapping:
        return
    text = path.read_text(encoding="utf-8")
    new = _UUID_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)
    if new != text:
        path.write_text(new, encoding="utf-8")


def stable_uuids(path: Path, previous: str | None, name: str) -> dict[str, str]:
    """Give the just-saved schematic at `path` stable UUIDs, in place.
    kicad-sch-api draws fresh random UUIDs on every run, which would cut the
    board's footprints (path "/<sheet>/<symbol>") off the schematic each
    time a generator is re-run. Each object keeps the UUID it had in
    `previous` (the file this save overwrote) under the same key
    (uuid_keys); anything new gets uuid5(key), so a fresh generation is
    the same every time too. Returns {ksa uuid: kept uuid} (instance paths
    in child sheets need it, see Project.save)."""
    old = dict(uuid_keys(previous)) if previous else {}
    mapping: dict[str, str] = {}
    for key, u in uuid_keys(path.read_text(encoding="utf-8")):
        want = old.get(key) or str(uuid.uuid5(
            _UUID_NS, json.dumps([name, *key], default=str)))
        if want != u:
            mapping[u] = want
    _rewrite_uuids(path, mapping)
    return mapping


def apply_pin_number_fixups(fixups: list[dict], sch_path: Path) -> None:
    """kicad-sch-api renumbers alphanumeric pin numbers (e.g. USB shield
    "SH" -> "6"), breaking netlist-vs-footprint pad matching. Repair the
    saved file text. Entries: {"lib_id", "pin_name", "wrong", "right"}.
    (Proven at S1: tests/golden/generators/sch_build.py.)"""
    if not fixups:
        return
    text = sch_path.read_text(encoding="utf-8")
    for fx in fixups:
        # 1. embedded lib symbol: the (number "wrong") right after the pin
        #    whose (name "pin_name") matches
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
            nxt = text.find('(lib_id "', li + len(needle))
            end = nxt if nxt > 0 else len(text)
            seg = text[li:end].replace(
                f'(pin "{fx["wrong"]}"', f'(pin "{fx["right"]}"')
            text = text[:li] + seg + text[end:]
            pos = li + len(seg)
    sch_path.write_text(text, encoding="utf-8")


def apply_native_dnp(refs: list[str], sch_path: Path) -> None:
    """kicad-sch-api 0.5.6 hard-codes `(dnp no)` on every saved symbol
    instance - there is no writable `dnp` field on a schematic symbol in
    this library version (LEARNINGS [kicad-sch-api][schematic][bom]
    2026-08-07). Repair the saved file text for `refs`: flip that instance's
    own `(dnp no)` to `(dnp yes)`. Each symbol block emits `(dnp no)`
    shortly BEFORE its own `(property "Reference" "REF" ...)` (only
    `fields_autoplaced`/`uuid` in between, neither containing the substring
    `(dnp `), so the nearest preceding `(dnp no)` before that property
    belongs unambiguously to this instance - the same nearest-neighbour
    technique `apply_pin_number_fixups` uses. Leaves in_bom/on_board and
    every custom field (e.g. `Variant`) untouched."""
    if not refs:
        return
    text = sch_path.read_text(encoding="utf-8")
    for ref in refs:
        needle = f'(property "Reference" "{ref}"'
        ri = text.find(needle)
        if ri < 0:
            raise ValueError(f"dnp fixup: reference {ref} not found")
        if text.find(needle, ri + 1) >= 0:
            raise ValueError(f"dnp fixup: reference {ref} is not unique")
        old = "(dnp no)"
        dn = text.rfind(old, 0, ri)
        if dn < 0:
            raise ValueError(f"dnp fixup: no preceding '{old}' for {ref}")
        text = text[:dn] + "(dnp yes)" + text[dn + len(old):]
    sch_path.write_text(text, encoding="utf-8")


def _forms(text: str, start: int = 0, end: int | None = None):
    """(head, open, close) of each top-level form in text[start:end],
    `close` one past its ")". Quoted strings are skipped."""
    end = len(text) if end is None else end
    depth, i, open_at = 0, start, -1
    while i < end:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < end and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "(":
            if depth == 0:
                open_at = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                m = re.match(r"\((\S+)", text[open_at:open_at + 40])
                yield (m.group(1) if m else "", open_at, i + 1)
            if depth < 0:
                return
        i += 1


def _body(text: str):
    """(start, end) of the inside of the kicad_sch form."""
    top = next(_forms(text))
    return top[1] + 1, top[2] - 1


def apply_label_justify(sch_path: Path) -> int:
    """kicad-sch-api writes `(justify left bottom)` on every label whatever
    its angle, while KiCad writes a 180/270-degree label right-justified:
    left-justified, its text runs back over its own wire and pin. Repair
    the saved file text; returns how many labels changed."""
    text = sch_path.read_text(encoding="utf-8")
    n = 0

    def fix(m):
        nonlocal n
        n += 1
        return m.group(1) + "(justify right"

    new = re.sub(r'(\((?:label|hierarchical_label) "(?:[^"\\]|\\.)*"\s*'
                 r'\(at \S+ \S+ (?:180|270)(?:\.0+)?\)[^()]*'
                 r'\(effects[^()]*\(font[^()]*\([^()]*\)[^()]*\)\s*)'
                 r'\(justify left', fix, text)
    if n:
        sch_path.write_text(new, encoding="utf-8")
    return n


def hide_pin_names(sch_path: Path) -> list[str]:
    """Hide pin NAMES on embedded symbols where they only add clutter: a
    two-pin part (R, C, LED, a 2-pin header: KiCad's own Device symbols
    hide them too; the symbol's drawing and the pin numbers say all there
    is) and any symbol whose every pin name is empty or its own number.
    Left shown, an LED's "K" and "A" print over each other inside its
    body. Pin numbers stay. Returns the lib_ids changed."""
    text = sch_path.read_text(encoding="utf-8")
    b0, b1 = _body(text)
    lib = next((f for f in _forms(text, b0, b1) if f[0] == "lib_symbols"),
               None)
    if lib is None:
        return []
    edits, changed = [], []
    for head, s0, s1 in _forms(text, lib[1] + 1, lib[2] - 1):
        if head != "symbol":
            continue
        block = text[s0:s1]
        pins = re.findall(r'\(pin \S+ \S+.*?\(name "((?:[^"\\]|\\.)*)"'
                          r'.*?\(number "((?:[^"\\]|\\.)*)"', block, re.S)
        if not pins:
            continue
        redundant = all(nm in ("", "~", num) for nm, num in pins)
        if len(pins) > 2 and not redundant:
            continue
        lib_id = re.match(r'\(symbol "((?:[^"\\]|\\.)*)"', block).group(1)
        inner = list(_forms(text, s0 + 1, s1 - 1))
        pn = next((f for f in inner if f[0] == "pin_names"), None)
        if pn is None:
            at = inner[0][1] if inner else s1 - 1
            ind = text[text.rfind("\n", 0, at) + 1:at]
            new = (f"(pin_names\n{ind}\t(hide yes)\n{ind})\n{ind}"
                   if ind.isspace() else "(pin_names (hide yes)) ")
            edits.append((at, at, new))
        else:
            form = text[pn[1]:pn[2]]
            if re.search(r"\(hide yes\)|\bhide\b", form):
                continue
            edits.append((pn[2] - 1, pn[2] - 1, " (hide yes)"))
        changed.append(lib_id)
    for a, z, s in sorted(edits, reverse=True):
        text = text[:a] + s + text[z:]
    if edits:
        sch_path.write_text(text, encoding="utf-8")
    return changed


# Landscape paper, smallest first (mm), and KiCad's default page layout:
# a 10 mm frame margin, the title block in the bottom-right corner.
PAPERS = (("A5", 210.0, 148.0), ("A4", 297.0, 210.0), ("A3", 420.0, 297.0),
          ("A2", 594.0, 420.0), ("A1", 841.0, 594.0), ("A0", 1189.0, 841.0))
FRAME = 10.0
TITLE_BLOCK = (110.0, 36.0)   # w, h of the default title block, mm
FIT_MARGIN = 5.08             # clear space kept around the drawing, mm


def drawn_bounds(sch_path: Path) -> tuple[float, float, float, float] | None:
    """(x0, y0, x1, y1) of everything drawn on the sheet: symbol bodies and
    their pin text, visible fields, wires, labels, junctions, sheets."""
    import schem_refdes as sr  # sibling script; deferred (shapely)
    from shapely.ops import unary_union
    sheet = sr.Sheet(sch_path)
    geoms = [g for _k, g, _n in sheet.statics]
    for sym in sheet.symbols:
        ob = sheet.body_obstacle(sym)
        if ob is not None:
            geoms.append(ob)
        for p in sym["props"].values():
            if p["hidden"] or p["at"] is None or not p["value"]:
                continue
            geoms.append(sr.text_box(p["value"], p["at"], p["size"],
                                     p["thick"], p["justify"], p["angle"]))
    geoms = [g for g in geoms if not g.is_empty]
    return unary_union(geoms).bounds if geoms else None


def _usable(w: float, h: float) -> tuple[float, float, float, float]:
    """The page area a drawing may use: inside the frame, above the title
    block (x0, y0, x1, y1)."""
    return (FRAME, FRAME, w - FRAME, h - FRAME - TITLE_BLOCK[1])


def fit_sheet(sch_path: Path, paper: str = "auto") -> dict:
    """Size the sheet to its drawing and centre the drawing on it. With
    paper "auto", the smallest landscape A-size whose area above the title
    block holds the drawing (plus FIT_MARGIN all round); any other paper is
    kept. Everything placed on the sheet moves by one grid-snapped offset,
    so connectivity is unchanged; the embedded library is not touched."""
    bb = drawn_bounds(sch_path)
    if bb is None:
        return {"paper": paper, "moved": [0.0, 0.0]}
    dw = bb[2] - bb[0] + 2 * FIT_MARGIN
    dh = bb[3] - bb[1] + 2 * FIT_MARGIN
    sizes = {n: (w, h) for n, w, h in PAPERS}
    if paper == "auto":
        paper = next((n for n, w, h in PAPERS
                      if dw <= w - 2 * FRAME
                      and dh <= h - 2 * FRAME - TITLE_BLOCK[1]),
                     PAPERS[-1][0])
    out = {"paper": paper, "drawing": [round(dw, 2), round(dh, 2)]}
    text = sch_path.read_text(encoding="utf-8")
    text = re.sub(r'\(paper "[^"]*"\)', f'(paper "{paper}")', text, count=1)
    dx = dy = 0.0
    if paper in sizes:
        ux0, uy0, ux1, uy1 = _usable(*sizes[paper])
        if dw <= ux1 - ux0 and dh <= uy1 - uy0:
            dx = snap((ux0 + ux1) / 2 - (bb[0] + bb[2]) / 2)
            dy = snap((uy0 + uy1) / 2 - (bb[1] + bb[3]) / 2)
    out["moved"] = [dx, dy]
    if dx or dy:
        text = _translate(text, dx, dy)
    sch_path.write_text(text, encoding="utf-8")
    return out


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _translate(text: str, dx: float, dy: float) -> str:
    """Move every placed item of a .kicad_sch by (dx, dy): each point form
    outside lib_symbols (library coordinates are the symbol's own)."""
    pt = re.compile(r"\((at|xy|start|end|center|mid) (-?[\d.]+) (-?[\d.]+)")

    def mv(m):
        return (f"({m.group(1)} {_num(float(m.group(2)) + dx)} "
                f"{_num(float(m.group(3)) + dy)}")

    b0, b1 = _body(text)
    out, pos = [], 0
    for head, f0, f1 in _forms(text, b0, b1):
        if head in ("lib_symbols", "title_block", "paper", "uuid",
                    "sheet_instances", "embedded_fonts"):
            continue
        out.append(text[pos:f0])
        out.append(pt.sub(mv, text[f0:f1]))
        pos = f1
    out.append(text[pos:])
    return "".join(out)


def write_project(name: str, out_dir: Path | str) -> Path:
    """Minimal .kicad_pro. The project file is the ERC/DRC severity
    authority; keep it MINIMAL - unexpected keys can make KiCad reject the
    whole file and silently fall back to defaults (LEARNINGS [kicad]).
    Library-configuration checks are ignored on purpose: generated designs
    embed their symbols and must not break on library updates."""
    out_dir = Path(out_dir)
    pro = {
        "board": {
            "design_settings": {
                "rule_severities": {
                    "lib_footprint_issues": "ignore",
                    "lib_footprint_mismatch": "ignore",
                },
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


def pin_table(lib_id: str, lib_paths: list | None = None) -> dict:
    """Symbol pin table for grounding (number/name/type where available).

    lib_paths: extra .kicad_sym files/dirs to register first - kicad-sch-api's
    global cache never reads a project's sym-lib-table (S14 finding: project
    libs like `aiee:...` were invisible to --pins without this).
    """
    with _quiet():
        if lib_paths:
            cache = ksa.get_symbol_cache()
            for p in lib_paths:
                cache.add_library_path(str(p))
        env.register_stock_libs(ksa.get_symbol_cache(), [lib_id])
        sch =ksa.create_schematic("_pins")
        c = sch.components.add(lib_id, reference="X1", value="x",
                               position=(127.0, 63.5))
    pins = []
    for p in c.pins:
        row = {"number": p.number, "name": p.name}
        for attr in ("electrical_type", "type", "pin_type"):
            v = getattr(p, attr, None)
            if v is not None:
                row["type"] = str(getattr(v, "value", v))
                break
        pins.append(row)
    return {"lib_id": lib_id, "pin_count": len(pins), "pins": pins}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pins", metavar="LIB_ID",
                    help="print the symbol's pin table as JSON")
    ap.add_argument("--lib", action="append", default=[],
                    help="extra .kicad_sym file/dir to register (repeatable; "
                    "project libs are invisible to the global cache)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)
    if not args.pins:
        ap.error("nothing to do: give --pins LIB_ID")
    try:
        payload = {"script": _SCRIPT, "status": "pass",
                   **pin_table(args.pins, lib_paths=args.lib)}
    except Exception as exc:  # noqa: BLE001  (contract: any error -> exit 2)
        print(json.dumps({"script": _SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
