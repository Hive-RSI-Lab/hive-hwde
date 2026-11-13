"""check_usb_phy (issue #315): a USB device's PHY needs, from the vendor's
own document, on synthetic netlists."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import check_usb_phy  # noqa: E402
import verify_all  # noqa: E402

from test_antenna_straps import _netlist  # noqa: E402


def _run(tmp_path, comps, nets):
    pcb = _netlist(tmp_path, comps, nets)
    payload, _ = check_usb_phy.run(["--pcb", str(pcb), "--netlist",
                                    str(tmp_path / "s.net")])
    return payload


def _kinds(payload):
    return sorted(v["kind"] for v in payload["violations"])


def _micro(phy, dp_pin, dm_pin, series=None, pullup=None, rail="+3V3"):
    """A micro-B J1 to `phy`: series {line: (ref, value)}, pullup
    (ref, value) on D+ to `rail`."""
    series = series or {}
    comps = {"J1": "USB_micro", "U1": phy}
    nets = {"+3V3": [("U1", "1", "VDD_1")], "GND": [("U1", "2", "VSS_2"),
                                                    ("J1", "5", "GND_5")],
            "VBUS": [("J1", "1", "VBUS_1")]}
    for line, jpin, upin in (("D+", "3", dp_pin), ("D-", "2", dm_pin)):
        net = f"/USB_{'DP' if line == 'D+' else 'DM'}"
        if line in series:
            ref, val = series[line]
            comps[ref] = val
            nets[net] = [("J1", jpin, f"{line}_{jpin}"), (ref, "1", "")]
            nets[net + "_U"] = [(ref, "2", ""), ("U1", upin[0], upin[1])]
        else:
            nets[net] = [("J1", jpin, f"{line}_{jpin}"),
                         ("U1", upin[0], upin[1])]
    if pullup:
        ref, val = pullup
        comps[ref] = val
        nets["/USB_DP"].append((ref, "1", ""))
        nets.setdefault(rail, []).append((ref, "2", ""))
    return comps, nets


F103 = ("STM32F103C8T6", ("33", "PA12_33"), ("32", "PA11_32"))


def test_f103_with_1k5_passes_and_without_fails(tmp_path):
    assert _kinds(_run(tmp_path, *_micro(F103[0], F103[1], F103[2],
                                         pullup=("R3", "1k5")))) == []
    p = _run(tmp_path, *_micro(F103[0], F103[1], F103[2]))
    assert _kinds(p) == ["usb_dp_pullup_missing"]
    assert "AN4879" in p["violations"][0]["msg"]


def test_f103_blue_pill_10k_and_5v_pullup_fail(tmp_path):
    p = _run(tmp_path, *_micro(F103[0], F103[1], F103[2],
                               pullup=("R3", "10k")))
    assert _kinds(p) == ["usb_dp_pullup_value"]
    assert "R3" in p["violations"][0]["refs"]
    p = _run(tmp_path, *_micro(F103[0], F103[1], F103[2],
                               pullup=("R3", "1k5"), rail="VBUS"))
    assert _kinds(p) == ["usb_dp_pullup_rail"]


def test_series_r_on_an_st_or_wch_phy_fails(tmp_path):
    p = _run(tmp_path, *_micro(F103[0], F103[1], F103[2],
                               series={"D+": ("R1", "22R"),
                                       "D-": ("R2", "22R")},
                               pullup=("R3", "1k5")))
    assert _kinds(p) == ["usb_series_r_unwanted"] * 2
    p = _run(tmp_path, *_micro("CH340C", ("5", "UD+_5"), ("6", "UD-_6"),
                               series={"D+": ("R1", "27R")}))
    assert _kinds(p) == ["usb_series_r_unwanted"]
    # 0 R is a short, not a series resistor
    p = _run(tmp_path, *_micro("CH340C", ("5", "UD+_5"), ("6", "UD-_6"),
                               series={"D+": ("R1", "0R")}))
    assert _kinds(p) == []


def test_extra_pullup_on_a_phy_with_its_own_warns(tmp_path):
    p = _run(tmp_path, *_micro("CH340B", ("5", "UD+_5"), ("6", "UD-_6"),
                               pullup=("R3", "1k5")))
    assert _kinds(p) == ["usb_dp_pullup_extra"]
    assert p["violations"][0]["severity"] == "warning"


def test_rp2040_needs_its_27r(tmp_path):
    rp = ("RP2040", ("47", "USB_DP_47"), ("46", "USB_DM_46"))
    p = _run(tmp_path, *_micro(*rp))
    assert _kinds(p) == ["usb_series_r_missing"] * 2
    p = _run(tmp_path, *_micro(*rp, series={"D+": ("R1", "27R"),
                                            "D-": ("R2", "27R")}))
    assert _kinds(p) == []
    p = _run(tmp_path, *_micro(*rp, series={"D+": ("R1", "47R"),
                                            "D-": ("R2", "27R")}))
    assert _kinds(p) == ["usb_series_r_value"]


def test_lpc_needs_33r_and_a_pullup(tmp_path):
    lpc = ("LPC11U35FBD48", ("29", "USB_DP_29"), ("30", "USB_DM_30"))
    p = _run(tmp_path, *_micro(*lpc, series={"D+": ("R1", "33R"),
                                             "D-": ("R2", "33R")},
                               pullup=("R3", "1k5")))
    assert _kinds(p) == []
    p = _run(tmp_path, *_micro(*lpc, pullup=("R3", "1k5")))
    assert _kinds(p) == ["usb_series_r_missing"] * 2


def test_esd_array_is_not_the_phy_and_unknown_ic_warns(tmp_path):
    comps, nets = _micro("CH340B", ("5", "UD+_5"), ("6", "UD-_6"))
    comps["U2"] = "USBLC6-2SC6"
    nets["/USB_DP"].append(("U2", "1", "I/O1_1"))
    nets["/USB_DM"].append(("U2", "3", "I/O2_3"))
    assert _kinds(_run(tmp_path, comps, nets)) == []
    p = _run(tmp_path, *_micro("FT232RL", ("15", "USBDP_15"),
                               ("16", "USBDM_16")))
    assert _kinds(p) == ["usb_phy_unknown"]


def _choked(phy, dp_pin, dm_pin, choke="DLW21HN900SQ2L", pins=None,
            pullup=None):
    """`_micro` with a 4-pad choke L1 between J1 and the PHY: pins
    {connector-side D+, PHY-side D+, connector-side D-, PHY-side D-},
    1/2/4/3 as hw-lab emc-lab's L501 (issue #647). The pull-up sits on the
    PHY side."""
    a, b, c, d = pins or ("1", "2", "4", "3")
    comps, nets = _micro(phy, dp_pin, dm_pin)
    comps["L1"] = choke
    nets["/USB_DP"] = [("J1", "3", "D+_3"), ("L1", a, "")]
    nets["/USB_DP_U"] = [("L1", b, ""), ("U1", dp_pin[0], dp_pin[1])]
    nets["/USB_DM"] = [("J1", "2", "D-_2"), ("L1", c, "")]
    nets["/USB_DM_U"] = [("L1", d, ""), ("U1", dm_pin[0], dm_pin[1])]
    if pullup:
        comps["R3"] = pullup
        nets["/USB_DP_U"].append(("R3", "1", ""))
        nets["+3V3"].append(("R3", "2", ""))
    return comps, nets


def test_a_known_choke_is_walked_through_to_the_phy(tmp_path):
    p = _run(tmp_path, *_choked(*F103, pullup="1k5"))
    assert _kinds(p) == []
    assert p["checked"][0]["phys"][0]["chokes"] == ["L1"]
    p = _run(tmp_path, *_choked(*F103, pullup="10k"))
    assert _kinds(p) == ["usb_dp_pullup_value"]
    rp = ("RP2040", ("47", "USB_DP_47"), ("46", "USB_DM_46"))
    assert _kinds(_run(tmp_path, *_choked(*rp))) == \
        ["usb_series_r_missing"] * 2


def test_an_unknown_choke_is_named_as_a_choke(tmp_path):
    p = _run(tmp_path, *_choked(*F103, choke="XYZ-CMC-90", pullup="10k"))
    assert _kinds(p) == ["usb_choke_unknown"]
    assert p["violations"][0]["refs"] == ["L1"]
    assert "windings" in p["violations"][0]["msg"]


def test_a_turned_choke_shorts_the_pair(tmp_path):
    """Turned 90 degrees on its footprint: D+ on 1, D- on 2 (one winding)."""
    p = _run(tmp_path, *_choked(*F103, pins=("1", "4", "2", "3"),
                                pullup="1k5"))
    assert _kinds(p) == ["usb_choke_shorts_pair"]
    assert p["violations"][0]["refs"] == ["J1", "L1"]


def test_dm_pullup_fails(tmp_path):
    comps, nets = _micro(F103[0], F103[1], F103[2], pullup=("R3", "1k5"))
    comps["R4"] = "1k5"
    nets["/USB_DM"].append(("R4", "1", ""))
    nets["+3V3"].append(("R4", "2", ""))
    assert _kinds(_run(tmp_path, comps, nets)) == ["usb_dm_pullup"]


def _typec(controller=None, rd=("5.1k", "5.1k"), dp2_open=False):
    comps = {"J1": "TYPE-C-31-M-12", "U8": "CH340B"}
    nets = {"GND": [("J1", "A1", "GND_A1")],
            "VBUS": [("J1", "A4", "VBUS_A4")],
            "/USB_DP": [("J1", "A6", "DP1_A6"), ("U8", "5", "UD+_5")],
            "/USB_DM": [("J1", "A7", "DN1_A7"), ("J1", "B7", "DN2_B7"),
                        ("U8", "6", "UD-_6")]}
    if dp2_open:
        nets["unconnected-(J1-DP2-PadB6)"] = [("J1", "B6", "DP2_B6")]
    else:
        nets["/USB_DP"].append(("J1", "B6", "DP2_B6"))
    for i, (jpin, upin) in enumerate((("A5", "7"), ("B5", "6"))):
        net = f"/CC{i + 1}"
        nets[net] = [("J1", jpin, f"CC{i + 1}_{jpin}")]
        if controller:
            comps["U1"] = controller
            nets[net].append(("U1", upin, f"CC{2 - i}_{upin}"))
        if rd and rd[i]:
            comps[f"R{10 + i}"] = rd[i]
            nets[net].append((f"R{10 + i}", "1", ""))
            nets["GND"].append((f"R{10 + i}", "2", ""))
    return comps, nets


def test_bare_typec_sink_needs_5k1_on_each_cc(tmp_path):
    assert _kinds(_run(tmp_path, *_typec())) == []
    p = _run(tmp_path, *_typec(rd=("5.1k", None)))
    assert _kinds(p) == ["usb_cc_rd_missing"]
    p = _run(tmp_path, *_typec(rd=("10k", "5.9k")))
    assert _kinds(p) == ["usb_cc_rd_tolerance", "usb_cc_rd_value"]


def test_ch224_internal_rd_and_doubled_rd(tmp_path):
    assert _kinds(_run(tmp_path, *_typec("CH224K", rd=None))) == []
    p = _run(tmp_path, *_typec("CH224A", rd=("5.1k", "5.1k")))
    assert _kinds(p) == ["usb_cc_rd_value"] * 2
    assert "parallel" in p["violations"][0]["msg"]
    # CH224D needs its two 5.1 k
    assert _kinds(_run(tmp_path, *_typec("CH224D", rd=None))) == \
        ["usb_cc_rd_missing"] * 2
    assert _kinds(_run(tmp_path, *_typec("CH224D"))) == []
    assert _kinds(_run(tmp_path, *_typec("HUSB238", rd=None))) == \
        ["usb_cc_unknown"] * 2


def test_source_port_with_rp_is_not_judged_as_a_sink(tmp_path):
    comps, nets = _typec(rd=None)
    for i in (1, 2):
        comps[f"R{20 + i}"] = "56k"
        nets[f"/CC{i}"].append((f"R{20 + i}", "1", ""))
        nets["VBUS"].append((f"R{20 + i}", "2", ""))
    assert _kinds(_run(tmp_path, comps, nets)) == []


def test_typec_dp_pins_not_joined_fail(tmp_path):
    p = _run(tmp_path, *_typec(dp2_open=True))
    assert _kinds(p) == ["usb_c_orientation"]


def test_cypd3177_is_its_own_rd(tmp_path):
    """Infineon 002-25383 Table 15: Rd and the dead-battery Rd are inside."""
    assert _kinds(_run(tmp_path, *_typec("CYPD3177-24LQXQ", rd=None))) == []
    p = _run(tmp_path, *_typec("CYPD3177-24LQXQ"))
    assert _kinds(p) == ["usb_cc_rd_value"] * 2


OSD = ("OSD6254-1G-IPM", ("V12", "USB0_DP_V12"), ("U12", "USB0_DM_U12"))


def test_am62x_and_osd62x_pm_connect_usb_directly(tmp_path):
    """TI SPRADO3B 7.4.2.1 and Octavo's checklist 62_USB_7: USBn_DP/DM go
    to the connector with nothing in series and no pull-up."""
    for soc in ("OSD6254-1G-IPM", "OSD6254-1G-IPM-ES", "AM6254ATCGGAALW",
                "AM6252ATCGHAALW"):
        p = _run(tmp_path, *_micro(soc, OSD[1], OSD[2]))
        assert _kinds(p) == [], soc
    p = _run(tmp_path, *_micro(*OSD, series={"D+": ("R1", "22R"),
                                             "D-": ("R2", "22R")}))
    assert _kinds(p) == ["usb_series_r_unwanted"] * 2
    assert "SPRADO3B" in p["violations"][0]["msg"]
    p = _run(tmp_path, *_micro(*OSD, pullup=("R3", "1k5")))
    assert _kinds(p) == ["usb_dp_pullup_extra"]
    # AM625SIP has a data sheet of its own; another Octavo SiP isn't the PM
    for other in ("AM625SIP", "OSD32MP157C-512M-BAA"):
        p = _run(tmp_path, *_micro(other, OSD[1], OSD[2]))
        assert _kinds(p) == ["usb_phy_unknown"], other


def _dual_usba(series=None, pullup=None, open2=False):
    """A stacked dual USB-A J1 (D+1/D-1 port 1, D+2/D-2 port 2) on a
    USB2514B's downstream ports 1 and 2. series and pullup: {line: (ref,
    value)}, line one of D+1, D-1, D+2, D-2; a pull-up goes to +3V3. open2:
    port 2's pins left unconnected."""
    series, pullup = series or {}, pullup or {}
    comps = {"J1": "UJ2-ADH-3-TH", "U1": "USB2514B-AEZG"}
    nets = {"+3V3": [("U1", "5", "VDD33_5")],
            "GND": [("J1", "4", "GND1_4"), ("J1", "8", "GND2_8")]}
    hub = {"D+1": ("2", "USBDP_DN1"), "D-1": ("1", "USBDM_DN1"),
           "D+2": ("4", "USBDP_DN2"), "D-2": ("3", "USBDM_DN2")}
    jpins = {"D+1": "3", "D-1": "2", "D+2": "7", "D-2": "6"}
    for line, (upin, uname) in hub.items():
        jp = ("J1", jpins[line], f"{line}_{jpins[line]}")
        if open2 and line.endswith("2"):
            nets[f"unconnected-(J1-{line}-Pad{jpins[line]})"] = [jp]
            continue
        net = f"/USBA{line[-1]}_{'DP' if line[1] == '+' else 'DM'}"
        nets[net] = [jp]
        u = ("U1", upin, f"{uname}_{upin}")
        if line in series:
            ref, val = series[line]
            comps[ref] = val
            nets[net].append((ref, "1", ""))
            nets[net + "_U"] = [(ref, "2", ""), u]
        else:
            nets[net].append(u)
        if line in pullup:
            ref, val = pullup[line]
            comps[ref] = val
            nets[net].append((ref, "1", ""))
            nets["+3V3"].append((ref, "2", ""))
    return comps, nets


def test_hub_ports_on_a_dual_usba_are_read_port_by_port(tmp_path):
    """DS00001692C section 1.0: every resistor is inside the USB251xB. Each
    D+ is judged with its own port's D-, so a fault on port 2's D- shows."""
    p = _run(tmp_path, *_dual_usba())
    assert _kinds(p) == []
    assert p["checked"][0]["connectors"] == ["J1"]
    assert len(p["checked"][0]["phys"]) == 2
    p = _run(tmp_path, *_dual_usba(series={"D-2": ("R1", "22R")}))
    assert _kinds(p) == ["usb_series_r_unwanted"]
    assert p["violations"][0]["refs"] == ["R1", "U1"]
    p = _run(tmp_path, *_dual_usba(pullup={"D-2": ("R4", "1k5")}))
    assert _kinds(p) == ["usb_dm_pullup"]


def test_a_dual_usba_is_not_a_typec_receptacle(tmp_path):
    """D+1 and D+2 are two ports, not one port's two rows of pins."""
    assert _kinds(_run(tmp_path, *_dual_usba(open2=True))) == []


def test_partner_pairs_by_the_phy_pin_name():
    assert check_usb_phy.partner("USBDP_DN2", "USBDM_DN2")
    assert not check_usb_phy.partner("USBDP_DN2", "USBDM_DN1")
    assert check_usb_phy.partner("USB0_DP", "USB0_DM")
    assert check_usb_phy.partner("UD+", "UD-")
    assert not check_usb_phy.partner("PA12", "PA11")


def test_no_netlist_passes(tmp_path):
    pcb = _netlist(tmp_path, {}, {})
    (tmp_path / "s.net").unlink()
    payload, _ = check_usb_phy.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass"


def test_joins_verify_all():
    assert "check_usb_phy" in {c["name"] for c in verify_all.CHECKS}


def test_every_row_names_its_source():
    kb = check_usb_phy.load_kb()
    for row in kb["phys"] + kb["cc_sinks"] + kb["chokes"]:
        assert row.get("source"), row["name"]
    for row in kb["chokes"]:
        pads = [p for w in row["windings"] for p in w]
        assert sorted(pads) == ["1", "2", "3", "4"], row["name"]
