"""cfb.py - read the streams of an OLE compound file (Microsoft CFB).

Altium's .PcbDoc / .SchDoc / .PcbLib are compound files: storages such as
"Board6" and "Tracks6", each with a "Data" stream. eda_io reads them to count
what the source board holds before KiCad's importer touches it, so a layer
the importer drops is seen against what was on it. Standard library only (the
venv has no olefile); read-only, no writer.

    read(data: bytes) -> {"Board6/Data": b"...", ...}

Raises CfbError on a file that isn't a compound file or whose chains run
off the end.
"""
from __future__ import annotations

import struct

MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_END = 0xFFFFFFFA  # FREESECT/ENDOFCHAIN/FATSECT/DIFSECT are all >= this


class CfbError(ValueError):
    pass


def is_cfb(data: bytes) -> bool:
    return data[:8] == MAGIC


def read(data: bytes) -> dict[str, bytes]:
    if not is_cfb(data):
        raise CfbError("not an OLE compound file")
    ss = 1 << struct.unpack_from("<H", data, 30)[0]
    mss = 1 << struct.unpack_from("<H", data, 32)[0]
    nfat, dir0, _, cutoff, minifat0, nminifat, difat0, ndifat = \
        struct.unpack_from("<8I", data, 44)
    hdr = 512 if ss == 512 else ss  # v4 files pad the header to a sector

    def sector(s: int) -> bytes:
        off = hdr + s * ss
        if off + ss > len(data):
            raise CfbError(f"sector {s} past the end of the file")
        return data[off:off + ss]

    difat = list(struct.unpack_from("<109I", data, 76))
    s = difat0
    for _ in range(ndifat):
        if s >= _END:
            break
        v = struct.unpack("<%dI" % (ss // 4), sector(s))
        difat += v[:-1]
        s = v[-1]
    fat: list[int] = []
    for sec in difat[:nfat]:
        fat += struct.unpack("<%dI" % (ss // 4), sector(sec))

    def chain(start: int, table: list[int]) -> list[int]:
        out, seen = [], set()
        while start < _END:
            if start in seen or start >= len(table):
                raise CfbError("broken sector chain")
            seen.add(start)
            out.append(start)
            start = table[start]
        return out

    def big(start: int) -> bytes:
        return b"".join(sector(x) for x in chain(start, fat))

    d = big(dir0)
    ents = []
    for i in range(len(d) // 128):
        e = d[i * 128:(i + 1) * 128]
        nl = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(nl - 2, 0)].decode("utf-16le", errors="replace")
        left, right, child = struct.unpack_from("<3I", e, 68)
        start = struct.unpack_from("<I", e, 116)[0]
        size = struct.unpack_from("<Q", e, 120)[0]
        if ss == 512:
            size &= 0xFFFFFFFF  # v3: high dword is undefined
        ents.append((name, e[66], left, right, child, start, size))
    if not ents:
        raise CfbError("no directory entries")

    minifat: list[int] = []
    if nminifat:
        for x in chain(minifat0, fat):
            minifat += struct.unpack("<%dI" % (ss // 4), sector(x))
    ministream = big(ents[0][5]) if ents[0][5] < _END else b""

    def stream(start: int, size: int) -> bytes:
        if size < cutoff:
            return b"".join(ministream[x * mss:(x + 1) * mss]
                            for x in chain(start, minifat))[:size]
        return big(start)[:size]

    files: dict[str, bytes] = {}
    seen: set[int] = set()

    def walk(i: int, prefix: str) -> None:
        if i >= _END or i >= len(ents) or i in seen:
            return
        seen.add(i)
        name, typ, left, right, child, start, size = ents[i]
        walk(left, prefix)
        walk(right, prefix)
        if typ == 2:
            files[prefix + name] = stream(start, size)
        elif typ == 1:
            walk(child, prefix + name + "/")
        elif typ == 5:
            walk(child, prefix)

    walk(0, "")
    return files
