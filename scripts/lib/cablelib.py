"""cablelib - which pin meets which across a cable between two boards (#485).

Used by check_mate_pins.py for a mating_pairs entry with mount "cable" and a
`cable` description, and by its --wireviz output.

FFC/FPC (kind ffc or fpc). A flat cable's end is a frame: t (the way the end
goes into the socket), n (the normal of its exposed contact face) and w (the
way conductor numbers grow). Its handedness is the sign of det(t, n, w).
Bending, folding or twisting the cable is a proper motion, so it keeps that
sign at each end: a Type A cable (contacts on the same side at both ends) has
ends of opposite handedness, a Type B cable (opposite sides) has ends of the
same handedness. A socket fixes t (into its slot), n (towards its contacts)
and its pin-1 -> pin-N direction p, so it has a handedness
s = sign det(t, n, p) of its own; a proper motion of the board keeps it, and
so does a B.Cu placement (KiCad's flip is a 180 degree turn about an in-plane
axis). So the pin that pin k meets depends only on the two sockets and the
cable type, never on the path the cable takes:

    straight (pin k -> pin k)      when  Type B and s1 == s2,
                                    or   Type A and s1 != s2
    reversed (pin k -> pin N+1-k)  otherwise.

The same part at both ends always has s1 == s2: Type B is straight and Type A
reversed. For two different parts the socket data is needed: `contact`
(top: contacts on the side away from the board; bottom: towards it; dual:
both, so the cable goes in either way and nothing is decided) and `entry`
(tabs: the slot opens on the side of the mounting tabs, away from the signal
leads; leads: it opens over the signal leads). It comes from
reference/connector_pinmap.yaml ffc_sockets or from the pair's cable.ends.

IDC ribbon, crimped harness, anything else (kind idc, discrete, ...): the map
is declared: pin_map straight | reversed | rows_swapped (2-row odd/even swap:
an IDC plug crimped turned over) | {near pin: far pin}.

All geometry is in KiCad's top view: x right, y down, z = x cross y (into the
screen). The outward normal of F.Cu is -z, of B.Cu +z.
"""
from __future__ import annotations

import math

KINDS_FLAT = ("ffc", "fpc")
MAPS = ("straight", "reversed", "rows_swapped")


class CableError(ValueError):
    """The cable can't be resolved; .kind names the violation."""

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind


def _num(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def signal_row(pads, tol: float = 0.05):
    """(row, others): the numbered pads on the line through pins 1 and 2,
    pins 1..N in order, and the rest (mounting tabs). None when pins 1-2 are
    missing or the row is not numbered 1..N."""
    at = {p[0]: p for p in pads}
    if "1" not in at or "2" not in at:
        return None
    x1, y1 = at["1"][2], at["1"][3]
    ux, uy = at["2"][2] - x1, at["2"][3] - y1
    ln = math.hypot(ux, uy)
    if ln == 0:
        return None
    ux, uy = ux / ln, uy / ln
    row, others = [], []
    for p in pads:
        off = abs((p[2] - x1) * uy - (p[3] - y1) * ux)
        (row if off <= tol and _num(p[0]) is not None else others).append(p)
    row.sort(key=lambda p: _num(p[0]))
    if [_num(p[0]) for p in row] != list(range(1, len(row) + 1)):
        return None
    return row, others


def socket_handedness(pads, side: str, contact: str, entry: str) -> int:
    """s = sign det(t, n, p) of an FFC socket as placed (see the module doc).
    pads: (num, net, x, y) top view; side front|back."""
    rs = signal_row(pads)
    if rs is None:
        raise CableError("cable_contact_unknown",
                         "its signal pads are not one row numbered 1..N")
    row, others = rs
    if not others:
        raise CableError("cable_contact_unknown",
                         "it has no mounting tabs to tell its entry side")
    px, py = row[-1][2] - row[0][2], row[-1][3] - row[0][3]
    rcx = sum(p[2] for p in row) / len(row)
    rcy = sum(p[3] for p in row) / len(row)
    ocx = sum(p[2] for p in others) / len(others)
    ocy = sum(p[3] for p in others) / len(others)
    ux, uy = ocx - rcx, ocy - rcy              # leads -> tabs
    pp = px * px + py * py
    k = (ux * px + uy * py) / pp
    ux, uy = ux - k * px, uy - k * py          # across the row only
    if math.hypot(ux, uy) < 0.05:
        raise CableError("cable_contact_unknown",
                         "its tabs sit on the signal row's line")
    if entry == "tabs":                       # cable comes in from the tabs
        tx, ty = -ux, -uy
    elif entry == "leads":
        tx, ty = ux, uy
    else:
        raise CableError("cable_contact_unknown", f"entry {entry!r} is not "
                         "tabs or leads")
    outward = -1.0 if side == "front" else 1.0
    if contact == "top":
        nz = outward
    elif contact == "bottom":
        nz = -outward
    else:
        raise CableError("cable_contact_unknown", f"contact {contact!r} is "
                         "not top or bottom")
    # det of rows (t, n, p) with n = (0, 0, nz)
    det = nz * (px * ty - py * tx)
    return 1 if det > 0 else -1


def flat_map_name(cable_type: str, s1: int, s2: int) -> str:
    t = str(cable_type).upper()
    if t not in ("A", "B"):
        raise CableError("cable_type_unknown",
                         f"FFC type {cable_type!r} is not A or B")
    return "straight" if (t == "B") == (s1 == s2) else "reversed"


def type_for(map_name: str, s1: int, s2: int) -> str:
    """The FFC type that gives map_name between sockets s1, s2."""
    return "B" if (map_name == "straight") == (s1 == s2) else "A"


def expand(map_spec, n: int) -> dict[str, str]:
    """{near pin: far pin} for a named or explicit map over pins 1..n."""
    if isinstance(map_spec, dict):
        return {str(k): str(v) for k, v in map_spec.items()}
    if map_spec == "straight":
        return {str(k): str(k) for k in range(1, n + 1)}
    if map_spec == "reversed":
        return {str(k): str(n + 1 - k) for k in range(1, n + 1)}
    if map_spec == "rows_swapped":
        if n % 2:
            raise CableError("cable_map_invalid",
                             f"rows_swapped needs an even pin count, not {n}")
        return {str(k): str(k + 1 if k % 2 else k - 1)
                for k in range(1, n + 1)}
    raise CableError("cable_map_invalid", f"pin_map {map_spec!r} is not one "
                     f"of {MAPS} or a {{pin: pin}} map")


def socket_data(fp, ends: dict, table_sockets: list) -> dict:
    """{contact, entry, source} for a socket: the pair's cable.ends entry
    first, then the first ffc_sockets row whose match fits the footprint."""
    got = dict(ends.get(fp.ref) or {})
    if got.get("contact") and got.get("entry"):
        got.setdefault("source", "constraints")
        return got
    name = fp.fpid.split(":", 1)[-1]
    for row in table_sockets:
        if row["re"].search(name):
            out = {k: row.get(k) for k in ("contact", "entry", "source")}
            out.update({k: v for k, v in got.items() if v})
            return out
    return got


def resolve(a_fp, b_fp, cable: dict, sockets: list) -> dict:
    """{map: {a pin: b pin}, name, type, note, s} for a declared cable, or
    raises CableError."""
    kind = str(cable.get("kind", "discrete")).lower()
    declared = cable.get("pin_map")
    a_pads = [p for p in a_fp.pad_centers_abs() if p[0]]
    b_pads = [p for p in b_fp.pad_centers_abs() if p[0]]
    if kind in KINDS_FLAT:
        ra, rb = signal_row(a_pads), signal_row(b_pads)
        if ra is None or rb is None:
            raise CableError("cable_pins_mismatch",
                             f"{(a_fp if ra is None else b_fp).ref} has no "
                             "signal row numbered 1..N")
        n = len(ra[0])
        if len(rb[0]) != n:
            raise CableError("cable_pins_mismatch",
                             f"{a_fp.ref} has {n} contacts, {b_fp.ref} "
                             f"{len(rb[0])}")
        ends = cable.get("ends") or {}
        da = socket_data(a_fp, ends, sockets)
        db = socket_data(b_fp, ends, sockets)
        if "dual" in (da.get("contact"), db.get("contact")):
            raise CableError(
                "cable_orientation_ambiguous",
                "a dual-contact socket takes the cable either way up, so "
                "which pin meets which is not set by the parts")
        same = a_fp.fpid.split(":")[-1] == b_fp.fpid.split(":")[-1]
        try:
            sa = socket_handedness(a_pads, a_fp.side, da.get("contact"),
                                   da.get("entry"))
            sb = socket_handedness(b_pads, b_fp.side, db.get("contact"),
                                   db.get("entry"))
            note = (f"{a_fp.ref} {da.get('contact')}-contact entry "
                    f"{da.get('entry')}, {b_fp.ref} {db.get('contact')}-"
                    f"contact entry {db.get('entry')}")
            if same and sa != sb:      # same part, data disagree: be loud
                raise CableError(
                    "cable_contact_unknown",
                    "the same part at both ends must have one handedness; "
                    "its socket data differ between the two ends")
        except CableError as e:
            if not same:
                miss = [f.ref for f, d in ((a_fp, da), (b_fp, db))
                        if not (d.get("contact") and d.get("entry"))]
                raise CableError(
                    "cable_contact_unknown",
                    f"which pin meets which needs each socket's contact side "
                    f"and entry ({', '.join(miss) or e}); add them to "
                    "reference/connector_pinmap.yaml ffc_sockets or the "
                    "pair's cable.ends") from None
            sa = sb = 1
            note = "the same part at both ends"
        ctype = cable.get("type")
        if ctype is None:
            raise CableError(
                "cable_type_unknown",
                f"FFC type not declared: {note}, so a Type "
                f"{type_for('straight', sa, sb)} cable is straight (pin k to "
                f"pin k) and a Type {type_for('reversed', sa, sb)} reversed")
        name = flat_map_name(ctype, sa, sb)
        if declared is not None and declared != name:
            raise CableError(
                "cable_map_mismatch",
                f"declared pin_map {declared!r}, but a Type "
                f"{str(ctype).upper()} cable here is {name} ({note}); a "
                f"{declared} map needs Type {type_for(declared, sa, sb)}"
                if declared in ("straight", "reversed") else
                f"declared pin_map {declared!r}, but a Type "
                f"{str(ctype).upper()} cable here is {name} ({note})")
        return {"map": expand(name, n), "name": name, "pins": n,
                "type": str(ctype).upper(), "note": note, "s": [sa, sb]}
    nums = sorted({p[0] for p in a_pads if _num(p[0]) is not None}, key=int)
    n = len(nums)
    spec = declared if declared is not None else "straight"
    m = expand(spec, n)
    b_nums = {p[0] for p in b_pads}
    a_nums = {p[0] for p in a_pads}
    bad = [f"{k}->{v}" for k, v in m.items()
           if k not in a_nums or v not in b_nums]
    if bad:
        raise CableError("cable_pins_mismatch",
                         f"pin_map names pins that are not there: "
                         f"{', '.join(bad[:6])}")
    return {"map": m, "name": spec if isinstance(spec, str) else "explicit",
            "pins": n, "type": None, "note": kind, "s": None}


def _label(net) -> str:
    if not net:
        return ""
    return net.rstrip("/").rsplit("/", 1)[-1]


def wireviz(entries: list[dict]) -> dict:
    """A WireViz harness document for resolved cables. Each entry:
    {name, a_board, a_fp, b_board, b_fp, cable, resolved}."""
    doc = {"connectors": {}, "cables": {}, "connections": []}
    for i, e in enumerate(entries, 1):
        a_id = f"{e['a_board']}_{e['a_fp'].ref}"
        b_id = f"{e['b_board']}_{e['b_fp'].ref}"
        for cid, fp in ((a_id, e["a_fp"]), (b_id, e["b_fp"])):
            pads = {p[0]: p[1] for p in fp.pad_centers_abs() if p[0]}
            pins = sorted((k for k in pads if _num(k) is not None), key=int)
            r = e["resolved"]
            if r["type"]:                      # flat: the signal row only
                pins = [str(k) for k in range(1, r["pins"] + 1)]
            doc["connectors"][cid] = {
                "type": fp.fpid.split(":", 1)[-1],
                "pins": [int(k) for k in pins],
                "pinlabels": [_label(pads.get(k)) for k in pins]}
        cable, r = e["cable"], e["resolved"]
        wid = cable.get("name") or f"W{i}"
        kind = str(cable.get("kind", "discrete")).lower()
        w = {"wirecount": len(r["map"]),
             "type": (f"{kind.upper()} Type {r['type']}" if r["type"]
                      else kind),
             "notes": f"{r['name']} ({r['note']})"}
        if cable.get("pitch_mm"):
            w["type"] += f", {cable['pitch_mm']} mm pitch"
        if cable.get("length_mm"):
            w["length"] = round(float(cable["length_mm"]) / 1000.0, 4)
        if cable.get("part"):
            w["pn"] = str(cable["part"])
        doc["cables"][wid] = w
        near = sorted(r["map"], key=lambda k: _num(k) or 0)
        doc["connections"].append([
            {a_id: [int(k) for k in near]},
            {wid: list(range(1, len(near) + 1))},
            {b_id: [int(r["map"][k]) for k in near]}])
    return doc
