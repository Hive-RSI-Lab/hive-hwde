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
    storages = sorted({k.split("/")[0] for k in streams})  # "Name" alone: a root stream
    ents = [None]  # root first
    for i, st in enumerate(storages):
        sid = len(ents)
        ents.append(None)
        nxt = NONE
        if st in streams:  # a stream at the root (SchDoc's FileHeader)
            data = streams[st]
            nxt = sid + 1 if i < len(storages) - 1 else NONE
            ents[sid] = entry(st, 2, NONE, nxt, NONE, put(data), len(data))
            continue
        data = streams[st + "/Data"]
        start = put(data)
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
    # the embedded footprints come out as a library the board links to
    assert res["footprint_lib"].get("written"), res["footprint_lib"]
    assert (tmp_path / "o" / "mini.pretty").is_dir()
    assert '(footprint "mini:' in Path(res["board"]).read_text()
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


# ------------------------------------------------------- schematic import

_MINI_SCH = """<?xml version="1.0"?>
<eagle><drawing><schematic><libraries><library name="L"><devicesets>
<deviceset name="R"><gates><gate name="G$1" symbol="R"/></gates><devices>
<device name="" package="0402"><connects><connect gate="G$1" pin="1" pad="1"/>
<connect gate="G$1" pin="2" pad="2"/></connects></device></devices></deviceset>
<deviceset name="GND"><gates><gate name="1" symbol="GND"/></gates><devices>
<device name=""/></devices></deviceset></devicesets></library></libraries>
<parts><part name="R1" library="L" deviceset="R" device=""/>
<part name="R2" library="L" deviceset="R" device=""/>
<part name="GND1" library="L" deviceset="GND" device=""/></parts>
<sheets><sheet><nets>
<net name="!RST!"><segment><pinref part="R1" gate="G$1" pin="1"/><pinref part="R2" gate="G$1" pin="1"/></segment></net>
<net name="GND"><segment><pinref part="R1" gate="G$1" pin="2"/><pinref part="R2" gate="G$1" pin="2"/><pinref part="GND1" gate="1" pin="GND"/></segment></net>
</nets></sheet></sheets></schematic></drawing></eagle>"""


def test_eagle_sch_nets(tmp_path):
    from lib import schimport
    p = tmp_path / "m.sch"
    p.write_text(_MINI_SCH)
    assert schimport.detect(p) == "eagle"
    parts, nets = schimport.eagle_sch_nets(p)
    assert parts == {"R1", "R2"}  # the supply symbol has no package
    assert nets["~{RST}"] == {("R1", "1"), ("R2", "1")}
    assert nets["GND"] == {("R1", "2"), ("R2", "2")}


def test_sch_compare_finds_split_joined_missing():
    from lib import schimport
    src_nets = {"A": {("R1", "1"), ("R2", "1")}, "B": {("R1", "2"), ("R2", "2")},
                "N$3": {("R3", "1"), ("R4", "1")}}
    ok = {"/A": {("R1", "1"), ("R2", "1")}, "B": {("R1", "2"), ("R2", "2")},
          "Net-(R3-Pad1)": {("R3", "1"), ("R4", "1")}}
    assert schimport.compare({"R1", "R2"}, src_nets, {"R1", "R2", "#PWR1"}, ok) == []
    bad = {"A": {("R1", "1"), ("R2", "1"), ("R1", "2"), ("R2", "2")},
           "x": {("R3", "1")}, "y": {("R4", "1")}}
    got = {(l["level"], l["what"]) for l in
           schimport.compare({"R1", "R2", "R9"}, src_nets, {"R1", "R2"}, bad)}
    assert {("lost", "parts"), ("lost", "nets")} <= got
    texts = " ".join(l["text"] for l in schimport.compare(set(), src_nets, set(), bad))
    assert "split" in texts and "join" in texts


def test_prune_symbol_lib(tmp_path):
    from lib import schimport
    (tmp_path / "b.kicad_sch").write_text('(kicad_sch (symbol (lib_id "b-eagle-import:R") (at 0 0 0)))')
    lib = tmp_path / "b-eagle-import.kicad_sym"
    lib.write_text('(kicad_symbol_lib\n\t(version 20241209)\n'
                   '\t(symbol "R"\n\t\t(property "Value" "a (b) \\"c)" )\n\t)\n'
                   '\t(symbol "C"\n\t\t(pin passive line)\n\t)\n)\n')
    assert schimport.prune_symbol_lib(tmp_path / "b.kicad_sch") == {lib.name: {"kept": 1, "cut": 1}}
    text = lib.read_text()
    assert '(symbol "R"' in text and '(symbol "C"' not in text
    # the kept form ends where its parens balance, quoted parens aside
    assert '(property "Value" "a (b) \\"c)" )\n\t)' in text and text.endswith("\t)\n)\n")


def test_eagle_milling_slots(tmp_path):
    """A Milling (46) wire through a package's drilled pad is that pad's slot
    (SparkFun's USB-C shell tabs); one elsewhere is counted, not a slot."""
    brd = """<?xml version="1.0"?><eagle><drawing><layers>
<layer number="1" name="Top" active="yes"/><layer number="16" name="Bottom" active="yes"/>
</layers><board><plain><wire x1="0" y1="0" x2="1" y2="0" width="0.5" layer="46"/></plain>
<libraries><library name="L"><packages><package name="USBC">
<pad name="SHLD1" x="-4.318" y="-0.915" drill="0.4" diameter="0.6" shape="long" rot="R90"/>
<pad name="P2" x="2" y="2" drill="0.4"/>
<wire x1="-4.318" y1="-0.365" x2="-4.318" y2="-1.465" width="0.6" layer="46"/>
<wire x1="9" y1="9" x2="9" y2="8" width="0.6" layer="46"/>
</package></packages></library></libraries>
<elements><element name="J5" library="L" package="USBC" x="0" y="0"/></elements>
<signals/></board></drawing></eagle>"""
    p = tmp_path / "s.brd"
    p.write_text(brd)
    src = edasrc.read_source(p)
    assert src.slots == [{"ref": "J5", "pad": "SHLD1", "x1": -4.318, "y1": -0.365,
                          "x2": -4.318, "y2": -1.465, "w": 0.6}]
    assert src.milling_other == 2  # the board-level wire and the stray one


def _schdoc(tmp: Path) -> Path:
    """A one-sheet Altium SchDoc: R1/R2 tied by a polyline wire carrying net
    label SIG; R4 joins it by a wire ending mid-segment (a T); R3's wire
    crosses it with no junction (not joined); R1.2/R2.2 meet by power port
    name only; U9 has no footprint; R2's part-2 pin isn't placed."""
    recs: list[dict] = []

    def add(**kv) -> int:
        recs.append({k.replace("_", "."): str(v) for k, v in kv.items()})
        return len(recs) - 1

    def part(ref, pins, packaged=True):
        c = add(RECORD=1, CURRENTPARTID=1, OWNERPARTID=-1)
        for num, (x, y, d, owner_part) in pins.items():
            add(RECORD=2, OWNERINDEX=c, OWNERPARTID=owner_part, PINCONGLOMERATE=d,
                PINLENGTH=10, LOCATION_X=x, LOCATION_Y=y, DESIGNATOR=num)
        add(RECORD=34, OWNERINDEX=c, TEXT=ref, NAME="Designator")
        if packaged:
            lst = add(RECORD=44, OWNERINDEX=c)
            add(RECORD=45, OWNERINDEX=lst, MODELTYPE="PCBLIB", MODELNAME="R0603")

    def wire(*pts):
        kv = {"RECORD": 27, "LOCATIONCOUNT": len(pts)}
        for k, (x, y) in enumerate(pts, 1):
            kv[f"X{k}"], kv[f"Y{k}"] = x, y
        add(**kv)

    part("R1", {"1": (100, 100, 2, 1), "2": (110, 100, 0, 1)})       # ends (90,100) (120,100)
    part("R2", {"1": (200, 100, 2, 1), "2": (210, 100, 0, 1),        # ends (190,100) (220,100)
                "3": (400, 400, 0, 2)})                               # part 2: not placed
    part("R3", {"1": (150, 90, 3, 1), "2": (300, 300, 0, 1)})        # end (150,80)
    part("R4", {"1": (170, 40, 3, 1), "2": (600, 600, 0, 1)})        # end (170,30)
    part("U9", {"1": (140, 60, 3, 1)}, packaged=False)               # end (140,50), on SIG
    wire((90, 100), (90, 50), (190, 50), (190, 100))
    add(RECORD=25, LOCATION_X=140, LOCATION_Y=50, TEXT="SIG")
    wire((150, 0), (150, 80))                                         # crosses SIG at (150,50)
    wire((170, 30), (170, 50))                                        # T onto SIG
    wire((120, 100), (120, 150))
    add(RECORD=17, LOCATION_X=120, LOCATION_Y=150, TEXT="GND")
    wire((220, 100), (220, 160))
    add(RECORD=17, LOCATION_X=220, LOCATION_Y=160, TEXT="GND")
    add(RECORD=25, LOCATION_X=405, LOCATION_Y=400, TEXT="GND")        # on the unplaced pin
    head = {"HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0"}
    p = tmp / "m.SchDoc"
    p.write_bytes(_cfb_write({"FileHeader": _text_records(head, *recs)}))
    return p


def test_altium_sch_nets(tmp_path):
    from lib import schimport
    p = _schdoc(tmp_path)
    assert schimport.detect(p) == "altium"
    parts, nets, notes = schimport.altium_sch_nets(p)
    assert parts == {"R1", "R2", "R3", "R4"} and notes == []
    assert nets["SIG"] == {("R1", "1"), ("R2", "1"), ("R4", "1")}
    assert nets["GND"] == {("R1", "2"), ("R2", "2")}
    alone = [n for n, pins in nets.items() if ("R3", "1") in pins]
    assert alone and alone[0].startswith("N$") and nets[alone[0]] == {("R3", "1")}
    assert not any(("R2", "3") in pins for pins in nets.values())
    # the KiCad side agreeing gives no losses; a split gives one
    assert schimport.compare(parts, nets, parts, nets) == []
    bad = dict(nets, SIG={("R1", "1"), ("R2", "1")}, X={("R4", "1")})
    assert any(l["level"] == "lost" for l in schimport.compare(parts, nets, parts, bad))


def _text6(comp: int, text: str, designator: bool) -> bytes:
    b1 = bytearray(230)
    struct.pack_into("<H", b1, 7, comp)
    b1[41] = 1 if designator else 0
    b2 = bytes([len(text)]) + text.encode("latin1")
    return (bytes([5]) + struct.pack("<I", len(b1)) + bytes(b1)
            + struct.pack("<I", len(b2)) + b2)


def test_altium_pcb_designator_differs_from_schematic(tmp_path):
    """The PCB shows '1K5' for schematic R1 (renamed on the PCB, never
    annotated back): KiCad imports '1K5'. That's a rename to undo, not a lost
    part."""
    streams = {
        "Board6/Data": struct.pack("<I", 0) + _props({"LAYER1NAME": "Top Layer",
                                                      "LAYER1NEXT": "32", "LAYER32NEXT": "0"}),
        "Components6/Data": _text_records({"SOURCEDESIGNATOR": "R1"}, {"SOURCEDESIGNATOR": "U1"}),
        "Texts6/Data": _text6(0, "Thick Film", False) + _text6(0, "1K5", True)
        + _text6(1, "U1", True) + _text6(0xFFFF, "V1.0", False),
    }
    p = tmp_path / "b.PcbDoc"
    p.write_bytes(_cfb_write(streams))
    src = edasrc.read_source(p)
    assert src.pcb_refs == {"R1": "1K5"}
    board = edasrc.Board(refs=["1K5", "U1"])
    got = edasrc.compare(src, board, {}, "")
    assert not any(l.level == "lost" and l.what == "parts" for l in got)
    assert any("R1 shown as 1K5" in l.text for l in got)

def test_eagle_design_rules(tmp_path):
    """An Eagle board's <designrules> and net classes are read in mm (KiCad's
    importer drops them); the via ring follows Eagle's restring clamp."""
    brd = """<?xml version="1.0"?><eagle><drawing><layers>
<layer number="1" name="Top" active="yes"/><layer number="16" name="Bottom" active="yes"/>
</layers><board><libraries/><elements/>
<designrules name="x"><param name="mdWireWire" value="4mil"/><param name="mdSmdSmd" value="0.09mm"/>
<param name="msWidth" value="4mil"/><param name="msDrill" value="0.2mm"/>
<param name="rvViaOuter" value="0.25"/><param name="rlMinViaOuter" value="3mil"/>
<param name="rlMaxViaOuter" value="20mil"/><param name="mdCopperDimension" value="6mil"/>
<param name="mdDrill" value="6mil"/></designrules>
<classes><class number="0" name="default" width="0" drill="0"/>
<class number="1" name="Power" width="0.127" drill="0"><clearance class="1" value="0.1778"/></class></classes>
<signals><signal name="RAW" class="1"/><signal name="!RST!"/></signals></board></drawing></eagle>"""
    p = tmp_path / "r.brd"
    p.write_text(brd)
    src = edasrc.read_source(p)
    assert src.rules == {"clearance": 0.09, "track_width": 0.1016, "via_drill": 0.2,
                         "via_annular": 0.0762, "via_diameter": 0.3524,
                         "copper_edge": 0.1524, "hole_clearance": 0.1524}
    power = next(c for c in src.classes if c["name"] == "Power")
    assert power["width"] == 0.127 and power["clearance"] == 0.1778 and power["nets"] == ["RAW"]
    # into a project file
    pcb = tmp_path / "r.kicad_pcb"
    pcb.with_suffix(".kicad_pro").write_text('{"net_settings": {"classes": [{"name": "Default"}]}}')
    out = eda_io.apply_source_rules(pcb, src)
    pro = json.loads(pcb.with_suffix(".kicad_pro").read_text())
    assert pro["board"]["design_settings"]["rules"]["min_via_diameter"] == 0.3524
    assert {"netclass": "Power", "pattern": "RAW"} in pro["net_settings"]["netclass_patterns"]
    assert out["classes"] == ["Power (1 nets)"]


def test_board_only_footprints_are_not_parts(tmp_path):
    """Silk art / logos with (attr board_only) are no component: ODB++ lists
    none, so the export check mustn't call them lost."""
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text('(kicad_pcb\n\t(footprint "L:LOGO"\n\t\t(layer "F.Cu")\n'
                   '\t\t(property "Reference" "LOGO1")\n\t\t(attr board_only exclude_from_pos_files exclude_from_bom)\n\t)\n'
                   '\t(footprint "L:R"\n\t\t(layer "F.Cu")\n\t\t(property "Reference" "R1")\n\t\t(attr smd)\n'
                   '\t\t(pad "1" smd rect\n\t\t\t(at 0 0)\n\t\t)\n\t)\n'
                   '\t(footprint "L:FRAME"\n\t\t(layer "F.Cu")\n\t\t(property "Reference" "FRAME2")\n\t)\n)\n')
    b = edasrc.read_board(pcb)
    # a frame with a symbol but no pads has nothing to place either
    assert b.board_only == {"LOGO1", "FRAME2"} and set(b.refs) == {"LOGO1", "R1", "FRAME2"}
