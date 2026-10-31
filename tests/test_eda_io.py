"""Boards in and out of other EDA tools (eda_io.py, lib/edasrc.py, lib/cfb.py).

The hits, on KiCad's own QA boards with KiCad 10.0.6: kicad-cli pcb import
reports `warnings: []` while stderr lists the layers it skipped; it renames
Eagle's "PWR" to "PWR0" without a word; its Eagle importer writes texts on a
layer called UNDEFINED that kicad-cli then refuses to load. Everything here
runs on synthetic files; the end-to-end import of tests/fixtures/eda/mini.brd
(an Eagle board written for this test) needs kicad-cli and is slow.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import eda_io  # noqa: E402
from lib import cfb, edasrc  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "eda"


# ------------------------------------------------------- a compound file

def _cfb_write(streams: dict[str, bytes]) -> bytes:
    """A v3 compound file holding storage/Data streams, every stream in the
    big FAT (cutoff 0), the directory a right-sibling chain. Enough for
    cfb.read, not for Altium."""
    ss = 512
    sectors: list[bytes] = []
    fat: list[int] = []

    def put(data: bytes) -> int:
        start = len(sectors)
        n = max(1, -(-len(data) // ss))
        for i in range(n):
            sectors.append(data[i * ss:(i + 1) * ss].ljust(ss, b"\0"))
            fat.append(start + i + 1 if i < n - 1 else 0xFFFFFFFE)
        return start

    def entry(name, typ, left, right, child, start, size):
        n = (name + "\0").encode("utf-16le")
        return (n.ljust(64, b"\0") + struct.pack("<HBB", len(n), typ, 1)
                + struct.pack("<3I", left, right, child) + b"\0" * 36
                + struct.pack("<IQ", start, size))

    NONE = 0xFFFFFFFF
    storages = sorted({k.split("/")[0] for k in streams})
    ents = [None]  # root first
    for i, st in enumerate(storages):
        data = streams[st + "/Data"]
        start = put(data)
        sid = len(ents)
        ents.append(None)
        did = len(ents)
        ents.append(entry("Data", 2, NONE, NONE, NONE, start, len(data)))
        nxt = sid + 2 if i < len(storages) - 1 else NONE
        ents[sid] = entry(st, 1, NONE, nxt, did, 0, 0)
    ents[0] = entry("Root Entry", 5, NONE, NONE, 1 if storages else NONE, 0xFFFFFFFE, 0)
    dir_bytes = b"".join(ents)
    dir0 = put(dir_bytes)
    fat_sec = len(sectors)
    fat.append(0xFFFFFFFD)
    sectors.append(b"")  # placeholder for the FAT itself
    fat_bytes = b"".join(struct.pack("<I", x) for x in fat).ljust(ss, b"\xff")
    sectors[fat_sec] = fat_bytes
    hdr = bytearray(512)
    hdr[:8] = cfb.MAGIC
    struct.pack_into("<HHHH", hdr, 24, 0x3E, 3, 0xFFFE, 9)
    struct.pack_into("<H", hdr, 32, 6)
    struct.pack_into("<8I", hdr, 44, 1, dir0, 0, 0, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
    difat = [fat_sec] + [NONE] * 108
    struct.pack_into("<109I", hdr, 76, *difat)
    return bytes(hdr) + b"".join(sectors)


def _props(d: dict) -> bytes:
    return ("|" + "|".join(f"{k}={v}" for k, v in d.items()) + "|").encode("latin1")


def _text_records(*recs: dict) -> bytes:
    out = b""
    for r in recs:
        body = _props(r) + b"\0"
        out += struct.pack("<I", len(body)) + body
    return out


def _track(layer: int) -> bytes:
    body = bytes([layer]) + b"\0" * 40
    return bytes([4]) + struct.pack("<I", len(body)) + body


def _altium(tmp: Path) -> Path:
    board = {"LAYER1NAME": "Top Layer", "LAYER1NEXT": "39",
             "LAYER39NAME": "Int1 (GND)", "LAYER39NEXT": "2",
             "LAYER2NAME": "Int2 (Sig)", "LAYER2NEXT": "32",
             "LAYER32NAME": "Bottom Layer", "LAYER32NEXT": "0",
             "LAYER40NAME": "Int5 (GND)", "LAYER40NEXT": "0"}
    streams = {
        "Board6/Data": struct.pack("<I", 0) + _props(board),
        "Tracks6/Data": _track(1) * 3 + _track(2) * 2 + _track(32) + _track(57),
        "Polygons6/Data": _text_records({"LAYER": "PLANE1", "NET": "1"}),
        "Components6/Data": _text_records({"SOURCEDESIGNATOR": "R1"}, {"SOURCEDESIGNATOR": "U1"}),
        "Nets6/Data": _text_records({"NAME": "GND"}, {"NAME": "VCC"}),
        "Vias6/Data": (bytes([3]) + struct.pack("<I", 4) + b"\0" * 4) * 2,
    }
    p = tmp / "b.PcbDoc"
    p.write_bytes(_cfb_write(streams))
    return p


def test_cfb_reads_back_what_was_written(tmp_path):
    data = _cfb_write({"A/Data": b"x" * 700, "B/Data": b"hello"})
    got = cfb.read(data)
    assert got == {"A/Data": b"x" * 700, "B/Data": b"hello"}
    with pytest.raises(cfb.CfbError):
        cfb.read(b"not a compound file")


def test_altium_reader_counts_copper_per_stack_layer(tmp_path):
    src = edasrc.read_source(_altium(tmp_path))
    assert src.format == "altium"
    assert src.refs == {"R1", "U1"} and src.nets == {"GND", "VCC"}
    assert src.copper_declared == 4
    # mechanical layer 57 isn't copper; Int5 is named, off the stack, empty
    assert src.copper == {"Top Layer": 3, "Int2 (Sig)": 2, "Bottom Layer": 1,
                          "Int1 (GND)": 1, "Int5 (GND)": 0}
    assert src.vias == 2


# ------------------------------------------------------------- detection

@pytest.mark.parametrize("head,suffix,fmt", [
    ('<?xml version="1.0"?>\n<!DOCTYPE eagle SYSTEM "eagle.dtd">\n<eagle>', ".brd", "eagle"),
    ('{"head":{"docType":"3"},"shape":[]}', ".json", "easyeda"),
    ("A!REFDES!COMP_CLASS!\n", ".txt", "fabmaster"),
    ("!PADS-POWERPCB-V9.0-MILS!\n", ".asc", "pads"),
    ("(kicad_pcb (version 1))", ".kicad_pcb", "kicad"),
    ("\x00\x01binary allegro", ".brd", "allegro-brd"),
])
def test_detect_reads_the_bytes(tmp_path, head, suffix, fmt):
    p = tmp_path / ("f" + suffix)
    p.write_bytes(head.encode("latin1"))
    assert edasrc.detect(p) == fmt


def test_unimportable_formats_say_what_to_do(tmp_path):
    p = tmp_path / "x.brd"
    p.write_bytes(b"\x00\x00allegro")
    with pytest.raises(edasrc.Unreadable, match="Fabmaster extract"):
        edasrc.read_source(p)
    rc, res = eda_io.do_import(p, tmp_path / "out")
    assert rc == 2 and "Extracts" in res["error"]


# ---------------------------------------------------------------- readers

def test_eagle_reader():
    src = edasrc.read_source(FIX / "mini.brd")
    assert src.refs == {"R1", "R2"} and src.nets == {"A", "B"}
    # the package's own pads and >NAME text aren't board copper
    assert src.copper == {"Top": 1, "Route2": 1, "Bottom": 1}
    assert src.kicad_names["Route2"] == "In1.Cu" and src.kicad_names["Bottom"] == "B.Cu"
    assert src.vias == 1


def test_eagle_inner_layers_pack_in_order(tmp_path):
    """KiCad numbers Eagle's active inner layers In1, In2, ... in order: a
    4-layer Eagle board on layers 2 and 15 (SparkFun's) has In2.Cu, not In14."""
    brd = (FIX / "mini.brd").read_text().replace(
        '<wire x1="4.2" y1="5" x2="4.2" y2="2" width="0.25" layer="2"/>',
        '<wire x1="4.2" y1="5" x2="4.2" y2="2" width="0.25" layer="2"/>'
        '<wire x1="4.2" y1="2" x2="6" y2="2" width="0.25" layer="15"/>')
    p = tmp_path / "m.brd"
    p.write_text(brd)
    src = edasrc.read_source(p)
    assert src.kicad_names["Route2"] == "In1.Cu" and src.kicad_names["Route15"] == "In2.Cu"


def test_eagle_overbar_names():
    ob = edasrc.eagle_overbar
    assert ob("!RESET!") == "~{RESET}" and ob("21/SPI_!CS!") == "21/SPI_~{CS}"
    assert ob("!WR") == "~{WR}" and ob("A!") == "A" and ob("GND") == "GND"


def test_easyeda_reader(tmp_path):
    doc = {"head": {"docType": "3"},
           "layers": ["1~TopLayer~#f~true~true~true~", "2~BottomLayer~#f~true~false~true~",
                      "21~Inner1~#f~true~false~true~0~Signal", "22~Inner2~#f~false~false~false~~"],
           "shape": ["TRACK~0.5~1~GND~0 0 1 1~g1~0", "TRACK~0.5~21~VCC~0 0 1 1~g2~0",
                     "TRACK~0.5~3~~0 0 1 1~g3~0",  # silk, not copper
                     "SOLIDREGION~2~GND~M0,0L1,1Z~solid~g4~~~~0",
                     "SOLIDREGION~2~~M0,0L1,1Z~npth~g5~~~~0",
                     "VIA~1~1~1~GND~0.3~g6~0",
                     "LIB~0~0~package`R0603`~0~~g7~~#@$TEXT~P~0~0~0.6~0~0~3~~4.5~R1~M~~g8"
                     "#@$PAD~RECT~0~0~1~1~1~SIG~1~0~~0~g9~0"]}
    p = tmp_path / "b.json"
    p.write_text(json.dumps(doc))
    src = edasrc.read_source(p)
    assert src.copper == {"TopLayer": 1, "BottomLayer": 1, "Inner1": 1}
    assert src.copper_declared == 3
    assert src.refs == {"R1"} and src.nets == {"GND", "VCC", "SIG"} and src.vias == 1


def test_fabmaster_reader(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("A!REFDES!COMP_CLASS!SYM_NAME!\nS!U1!IC!SOIC8!\nS!R1!DISCRETE!R0603!\n"
                 "A!NET_NAME!REFDES!PIN_NUMBER!\nS!GND!U1!4!\nS!GND!R1!2!\nS!VCC!U1!8!\n"
                 "A!CLASS!SUBCLASS!\nS!ETCH!TOP!\nS!ETCH!GND_IN2!\n"  # the subclass list
                 "A!CLASS!SUBCLASS!GRAPHIC_DATA_NAME!NET_NAME!\n"
                 "S!ETCH!TOP!LINE!GND!\nS!ETCH!TOP!LINE!GND!\nS!ETCH!IN2!LINE!VCC!\n"
                 "S!BOARD GEOMETRY!FAB_NOTES!LINE!!\n"
                 "A!VIA_X!VIA_Y!PAD_STACK_NAME!NET_NAME!\nS!1!1!V!GND!\n")
    src = edasrc.read_source(p)
    assert src.refs == {"U1", "R1"} and src.nets == {"GND", "VCC"}
    assert src.copper == {"IN2": 1, "TOP": 2}
    assert src.other == {"FAB_NOTES": 1} and src.vias == 1


def test_pads_reader(tmp_path):
    p = tmp_path / "x.asc"
    p.write_text("!PADS-POWERPCB-V9.0-MILS!\n*PCB*        GENERAL\nMAXIMUMLAYER 4   x\n"
                 "*PART*       ITEMS\n*REMARK* REFNM PTYPENM X Y ORI GLUE MIRROR\n\n"
                 "C4              0402 2100000 -21300000 90.000 U M 0 -1 0 -1 2\n"
                 "VALUE    -1545000     -240000   0.000  1     1800000      150000 N   LEFT     UP\n"
                 "U1              SO8 0 0 0.000 G N 0 -1 0 -1 2\n"
                 "*SIGNAL* GND 0 -2\n*SIGNAL* +5V 0 -2\n*END*\n")
    src = edasrc.read_source(p)
    assert src.refs == {"C4", "U1"} and src.nets == {"GND", "+5V"}
    assert src.copper is None and src.copper_declared == 4


# ------------------------------------------------------- the KiCad side

BOARD = '''(kicad_pcb
\t(version 20260206)
\t(layers
\t\t(0 "F.Cu" signal "TOP")
\t\t(4 "In1.Cu" signal "GND1_IN2")
\t\t(2 "B.Cu" signal "BOTTOM")
\t\t(25 "Edge.Cuts" user)
\t)
\t(footprint "x:R"
\t\t(layer "F.Cu")
\t\t(property "Reference" "R1"
\t\t)
\t\t(attr smd)
\t\t(pad "1" smd rect
\t\t\t(net "GND")
\t\t)
\t\t(pad "2" smd rect
\t\t)
\t\t(model "r.wrl")
\t)
\t(footprint "x:U"
\t\t(layer "B.Cu")
\t\t(property "Reference" "U1"
\t\t)
\t\t(attr smd)
\t\t(pad "1" smd rect
\t\t\t(net "VCC")
\t\t)
\t)
\t(segment
\t\t(layer "F.Cu")
\t\t(net "GND")
\t)
\t(via
\t\t(layers "F.Cu" "B.Cu")
\t)
\t(zone
\t\t(net "GND")
\t\t(layer "In1.Cu")
\t\t(filled_polygon x)
\t)
\t(gr_line
\t\t(layer "Edge.Cuts")
\t)
)
'''


def test_read_board(tmp_path):
    p = tmp_path / "b.kicad_pcb"
    p.write_text(BOARD)
    b = edasrc.read_board(p)
    assert b.copper_layers == ["F.Cu", "In1.Cu", "B.Cu"]
    assert b.layer_names["In1.Cu"] == "GND1_IN2"
    assert b.refs == ["R1", "U1"] and b.footprints_bottom == 1
    assert b.nets == {"GND", "VCC"} and b.netless_pads == 1
    assert b.copper == {"F.Cu": 1, "In1.Cu": 1} and b.vias == 1
    assert b.zones == 1 and b.zones_unfilled == 0
    assert b.no_model == ["U1"] and b.edge_items == 1


def _board(tmp_path) -> edasrc.Board:
    p = tmp_path / "b.kicad_pcb"
    p.write_text(BOARD)
    return edasrc.read_board(p)


def _levels(losses):
    return [(l.level, l.what) for l in losses]


def test_compare_names_a_dropped_copper_layer(tmp_path):
    """The Allegro case: a copper subclass with content KiCad 'has no layer
    for' is a loss, said with its count; empty skipped ones are a note."""
    src = edasrc.Source("fabmaster", refs={"R1", "U1"}, nets={"GND", "VCC"},
                        copper={"TOP": 5, "GND1_IN2": 3, "SIG_IN3": 7, "BOTTOM": 0},
                        vias=1)
    log = ("12:00:00: Warning: No user layer to put layer SIG_IN3\n"
           "12:00:00: Warning: No user layer to put layer PWR_IN5\n")
    losses = edasrc.compare(src, _board(tmp_path), {"layer_mapping": {}}, log)
    lost = [l for l in losses if l.level == "lost"]
    assert len(lost) == 1 and "'SIG_IN3' held 7" in lost[0].text and "skipped" in lost[0].text
    # PWR_IN5 isn't in the source's counts: said, not called a loss
    assert any("PWR_IN5" in l.text and l.level == "check" for l in losses)


def test_compare_copper_mapped_to_an_empty_layer(tmp_path):
    src = edasrc.Source("altium", copper={"Top Layer": 4, "Int9": 2})
    rep = {"layer_mapping": {"Top Layer": {"kicad_layer": "F.Cu"},
                             "Int9": {"kicad_layer": "B.Cu"}}}
    losses = edasrc.compare(src, _board(tmp_path), rep, "")
    assert any(l.level == "lost" and "landed on B.Cu, which is empty" in l.text for l in losses)


def test_compare_parts_renamed_and_missing(tmp_path):
    src = edasrc.Source("eagle", refs={"R1", "U", "Q7"})
    b = _board(tmp_path)
    b.refs = ["R1", "U0"]
    losses = edasrc.compare(src, b, {}, "")
    texts = {l.level: l.text for l in losses if l.what == "parts"}
    assert "U -> U0" in texts["check"]
    assert "1 of 3 parts" in texts["lost"] and "Q7" in texts["lost"]


def test_compare_groups_empty_skipped_layers(tmp_path):
    src = edasrc.Source("altium", copper={"Top Layer": 1, "Int5 (GND)": 0,
                                          "Internal Plane 9": 0})
    rep = {"layer_mapping": {"Top Layer": {"kicad_layer": "F.Cu"}}}
    log = ("Warning: Layer 'Int5 (GND)' could not be mapped and will be skipped.\n"
           "Warning: Layer 'Internal Plane 9' could not be mapped and will be skipped.\n"
           "Warning: Font 'X' not found\n")
    losses = edasrc.compare(src, _board(tmp_path), rep, log)
    notes = [l for l in losses if l.what == "layer"]
    assert len(notes) == 1 and notes[0].level == "note" and "2 empty layers" in notes[0].text
    assert any(l.what == "importer" and "Font 'X'" in l.text for l in losses)
    assert not any(l.level == "lost" for l in losses)


# ------------------------------------------------------------------ export

IPC = '''<?xml version="1.0"?>
<IPC-2581 xmlns="http://webstds.ipc.org/2581">
 <Ecad><CadData>
  <Layer name="F.Cu" layerFunction="CONDUCTOR"/>
  <Layer name="In1.Cu" layerFunction="PLANE"/>
  <Layer name="B.Cu" layerFunction="CONDUCTOR"/>
  <Layer name="F.SilkS" layerFunction="SILKSCREEN"/>
  <Step><Component refDes="R1"/>{u1}
   <LayerFeature><Set net="GND"/>{vcc}</LayerFeature></Step>
 </CadData></Ecad>
</IPC-2581>'''


def test_check_ipc2581(tmp_path):
    b = _board(tmp_path)
    x = tmp_path / "a.xml"
    x.write_text(IPC.format(u1='<Component refDes="U1"/>', vcc='<Set net="VCC"/>'))
    assert eda_io.check_ipc2581(x, b) == []
    x.write_text(IPC.format(u1="", vcc=""))
    texts = " ".join(f["text"] for f in eda_io.check_ipc2581(x, b))
    assert "parts missing from the IPC-2581: U1" in texts
    assert "nets missing from the IPC-2581: VCC" in texts


def test_netlist_nets(tmp_path):
    a = tmp_path / "a.net"
    a.write_text("(NETLIST)\n$PACKAGES\n! 'r' ! '1k' ; R1\n$NETS\n'/SIG_A' ; R1.1 U1.3\n"
                 "GND ; R1.2,\n\tU1.4\n$END\n")
    assert eda_io._netlist_nets(a, "allegro") == {"/SIG_A", "GND"}
    p = tmp_path / "p.net"
    p.write_text("*PADS-PCB*\n*PART*\nR1 x\n*NET*\n*SIGNAL* GND\nR1.2 U1.4\n*END*\n")
    assert eda_io._netlist_nets(p, "pads") == {"GND"}


def test_every_target_says_how_and_what_is_lost():
    for t in eda_io.TARGETS:
        assert eda_io.EXPORT[t]["how"] and eda_io.EXPORT[t]["lost"] and eda_io.EXPORT[t]["files"]


# --------------------------------------------------------- live toolchain

def _cli_or_skip():
    from lib import env
    try:
        cli = env.find_kicad_cli()
    except env.EnvError:
        cli = None
    if not cli:
        pytest.skip("no kicad-cli")


def test_import_eagle_end_to_end(tmp_path):
    """mini.brd through kicad-cli: the UNDEFINED-layer texts are moved so
    KiCad loads its own import, the inner Route2 lands on In1.Cu, the zones
    are filled, and losses.md says it all."""
    _cli_or_skip()
    rc, res = eda_io.do_import(FIX / "mini.brd", tmp_path / "o")
    assert rc == 0, res
    assert res["board_counts"]["parts"] == 2
    assert res["board_counts"]["copper"].get("In1.Cu") == 1
    assert not any(l["level"] == "lost" for l in res["losses"])
    assert eda_io._loads(Path(res["board"])) is None
    md = (tmp_path / "o" / "losses.md").read_text()
    assert "Board only" in md and "Verdict" in md


def test_export_round_trips_parts_and_nets(tmp_path):
    """Export the imported mini board to every target; the IPC-2581 and
    ODB++ hold every part and net."""
    _cli_or_skip()
    rc, res = eda_io.do_import(FIX / "mini.brd", tmp_path / "o")
    assert rc == 0, res
    rc, res = eda_io.do_export(Path(res["board"]), ["neutral", "easyeda", "altium"],
                               tmp_path / "x")
    assert rc == 0, res["findings"]
    assert {"ipc2581", "odb", "kicad", "kicad-zip", "gencad", "ipcd356"} <= set(res["files"])
    assert (tmp_path / "x" / "losses.md").read_text().count("###") == 3
