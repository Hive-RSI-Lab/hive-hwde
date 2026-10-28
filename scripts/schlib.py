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
import sys
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

    def __init__(self, name: str, title: str | None = None, paper: str = "A3",
                 rev: str = "1", date: str = "", company: str = "hwde",
                 pwr_base: int = 1):
        self.name = name
        with _quiet():
            self.sch = ksa.create_schematic(name)
        self.sch.set_paper_size(paper)
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
        self.sch.add_label(net, position=end)
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
        self.sch.add_label(net, position=label_at)
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
            self.sch.add_label(net, position=(x, y))
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
             place_fields: bool = True) -> Path:
        """Write the sheet. place_fields (default) runs schem_refdes on the
        saved file so Reference/Value land on the correct side and clear of
        wires/labels/bodies (ksa mirrors every field to the wrong side -
        T6, ladder row 166 L3). Electrically inert; see self.place_report."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.name}.kicad_sch"
        with _quiet():
            self.sch.save(str(path))
        import schem_refdes as sr  # sibling script; deferred (shapely)
        sr.strip_private_properties(path)   # KiCad-10 stock-lib notes ksa mangles (exit 3 at ERC)
        apply_pin_number_fixups(self._fixups, path)
        if place_fields:
            self._place_fields(path)
        # native dnp LAST: write_placements() may round-trip the file
        # through kicad-sch-api (schem_refdes.write_placements ->
        # ksa.Schematic.load().save() when any field actually moved), and
        # ksa's writer hard-codes `(dnp no)` on every symbol instance
        # regardless of what it parsed - an earlier dnp patch would be
        # silently wiped by that later whole-file re-save (measured: with
        # the call before place_fields, J3/J4 came back `(dnp no)`).
        apply_native_dnp(self._dnp, path)
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
            self.root.sch.add_label(root_label, position=end)
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
        if decoupling is not None:
            payload = {"associations": self.decoupling}
            p = Path(decoupling)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, indent=1) + "\n",
                         encoding="utf-8")
        return path


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
        sch = ksa.create_schematic("_pins")
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
