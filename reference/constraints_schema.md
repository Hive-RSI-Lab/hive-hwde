# constraints.json - the merged, machine-readable constraint set

Written at P2 by the architect (from P1 research fragments), placed next to the
board (`kicad/constraints.json`) at P5. Every key is optional: a script whose
key is absent no-ops cleanly. Net names must match the FINAL netlist names
(local-label nets carry a leading `/` like `/MCO`; power nets are bare: `+3V3`,
`GND`, `VBUS`).

Consumers per key (script -> phase):

```jsonc
{
  // check_return_path (P8), rules_gen impedance (P5), planes_gen (P7 -
  // every referenced plane net/layer is guaranteed a pour)
  "high_speed": [{
    "net": "/USB_DP",
    "reference": "GND",              // or {"F.Cu": "GND", "B.Cu": "+3V3"}
    "k": 3,                          // corridor = k x trace width (default 3)
    "t_rise_ns": 1.0,                // sets return-via radius + stitch pitch
    "return_via_radius_mm": 2.0,     // fallback when t_rise_ns absent
    "impedance_ohm": 50              // optional; rules_gen sizes the width
  }],

  // check_current (P8), rules_gen per-net width rules (P5),
  // route_critical power routing (P7), place_anneal rule term (P6),
  // check_pdn decoupling inventory (P8)
  "power": [{
    "net": "+3V3", "current_a": 0.4, "dt_c": 10, "via_amps": 0.5,
    // overrides reach track segments, via clusters AND pour necks (T2).
    // Neck nuance: a neck is re-tested against an override only after it
    // FAILS at the full budget (a higher override never tightens a neck).
    "overrides": [{"near": [118.5, 108.0], "radius_mm": 5, "current_a": 0.2}],
    // plane_fed (T2): the rail's trunk is a plane - every via is a leaf tap,
    // so via-count/track findings outside override regions downgrade to
    // advisory WARNINGS (extras advisory:true); override regions stay ERROR
    // at their declared current (regulator feed taps). Pour necks stay ERROR
    // at the full budget. No zone fill on the net -> error plane_missing.
    // rules_gen sizes a plane_fed rail's tracks (DRU width + net class) for
    // one tap, not current_a: tap_current_a, else via_amps, else 0.5 A. So
    // declare the rail's REAL current and its pour in "planes"; rules_gen
    // warns when a plane_fed net has no plane there.
    "plane_fed": true, "tap_current_a": 0.5
  }, {
    // width-only entry: "pdn": false opts a net OUT of check_pdn's
    // decoupling inventory. Use for nets declared solely so rules_gen sizes
    // their trace (e.g. the raw-input stub BEFORE a reverse-polarity
    // element) - nothing decouples them by design.
    "net": "/VIN", "current_a": 0.3, "pdn": false
  }],

  // check_diffpair (P8), rules_gen gap rules (P5), route_critical (P7).
  // Omit entirely to auto-discover pairs by name suffix (_P/_N, DP/DM,
  // D+/D-); an EXPLICIT empty list disables the check.
  "diff_pairs": [{
    "p": "/USB_DP", "n": "/USB_DM", "base": "USB",
    "impedance_ohm": 90,             // differential target
    "gap_mm": 0.2, "max_skew_mm": 5, "max_uncoupled_mm": 5,
    "term_pair_mm": 2.5              // cross-ref terminal-match window (T2);
                                     // same-footprint P/N pads always match
  }, {
    // DC Kelvin / low-frequency shunt-sense pair: "kind" sense|kelvin|
    // dc_sense|low_frequency drops the skew, uncoupled-length and impedance
    // limits (kept: open trunk, via asymmetry); optional
    // "max_sense_uncoupled_mm" keeps a routed-together limit. Auto-discovered
    // pairs whose stem ends ISNS/ISENSE/ISEN/CSA/SHUNT/KELVIN get it by name.
    "p": "/ISNS_P", "n": "/ISNS_N", "kind": "sense", "gap_mm": 0.2
  }],

  // route_cleanup --snap-only (P7): nets routed to a matched length. The
  // snap pass leaves every net here (and every diff_pairs member, rf net
  // and high_speed net with impedance_ohm) as routed: re-bending one
  // changes its length.
  "length_match": [{"nets": ["/DQ0", "/DQ1", "/DQS"], "tol_mm": 1.0}],

  // check_creepage (P8) + rules_gen (P5, T6): IPC-2221 spacing for pairs
  // > 30 V apart. rules_gen emits named aiee_hv_* clearance DRU rules from
  // these (never hand-author HV rules); check_creepage audits the routed
  // copper. T2: reports EVERY violating item pair (not just the worst per
  // net pair); violation pos = the actual gap midpoint. check_ratings reads
  // these as each rail's worst case, ahead of the power tree and net names.
  // min (optional): the lowest the rail runs at (a USB-PD VBUS at its
  // lowest allowed contract, a battery at cut-off); sim_board re-runs its
  // DC scenarios there and judges MOSFET on-state drive (mosfet_underdrive).
  "voltages": [{"net": "HV_BUS", "voltage": 48},
               {"net": "VBUS", "voltage": 21, "min": 9}],
  // voltage_pairs (T2): explicit net-PAIR differential that node voltages
  // cannot express (bridge/AC inputs - two 57 V taps carry 114 V between
  // them). Overrides the derived difference for that pair; a pair declared
  // <= 30 V WAIVES the derived check (recorded in the report).
  "voltage_pairs": [{"a": "/poe/POE_TAP_A1", "b": "/poe/POE_TAP_A2",
                     "voltage": 114}],
  // coating (T2) selects the IPC-2221 Table 6-1 row per item type:
  // none -> B2 traces/vias, A6 exposed lands; soldermask -> B4 masked
  // traces/tented vias, A6 lands (mask relief exposes them); conformal ->
  // A5/A7. Inner layers are always B1. CLI --coating overrides.
  "coating": "soldermask",

  // check_si (P8): signal integrity of the nets that need it. Nets are
  // found by name and driver (clocks, RMII, I2S, SPI, USB, Ethernet MDI,
  // crystals); this block only declares a net the names miss, corrects a
  // class or edge, or sets a target. Every key is optional.
  "si": {"nets": {"/ETH_CLK": {"class": "osc_clock", "t_rise_ns": 1.0,
                               "z0_ohm": 50, "z0_tol_pct": 20,
                               "driver_ohm": 20, "swing_v": 3.3}},
         "ignore": ["/SLOW_CLK"],
         "victims": {"/VREF": "analog"},
         "groups": [{"name": "rmii_tx", "ui_ns": 20,
                     "nets": ["/RMII_TXD0", "/RMII_TXD1", "/RMII_TX_EN"]}]},

  // check_antenna (P8): which modules have a PCB antenna, for a part
  // reference/rf_modules.yaml does not know or names wrongly. "pcb" checks
  // the copper under its antenna end and its place at the board edge;
  // "external" (u.FL / IPEX) skips it.
  "antennas": {"U7": "pcb"},

  // check_thermal (P8), place_anneal spreading term (P6)
  "thermal": [{"ref": "U2", "power_w": 0.8, "net": "GND", "dt_c": 40,
               "min_vias": 4},
              {"ref": "L1", "power_w": 1.1, "net": "VL", "dt_c": 40,
               "airflow_m_s": 1.0,
               "rating": {"rise_c": 40, "current_a": 24.5, "dcr_ohm": 0.0028,
                          "source": "XAL1010-222 datasheet: Irms, 40 C rise"}},
              {"ref": "U3", "power_w": 3.0, "net": "GND", "dt_c": 70,
               "theta_ja_c_w": 29,
               "theta_source": "LMG2100R044 SNOSDF9B 5.4 Thermal Information"}],
  //        airflow_lfm | airflow_m_s (never both): forced air over the part.
  //        Declare it only when the board has the fan (a requirement, not
  //        a hope); theta is cut by TI SCBA017D's least QFN improvement
  //        (x0.876 at 150, x0.817 at 250, x0.771 at >= 500 LFM). Absent =
  //        still air. rating: the part's own datasheet self-heating rating
  //        (rise_c at current_a through dcr_ohm; source required) replaces
  //        the copper-area model - for inductors and other parts the
  //        datasheet rates by temperature rise. power_w stays the TOTAL
  //        loss (copper + core). theta_ja_c_w (+ theta_source, required):
  //        the datasheet's JEDEC 2s2p theta_JA, used as-is in place of the
  //        copper model (no area or via credit on top; optimistic on a
  //        crowded board). A part takes rating or theta_ja_c_w, not both;
  //        airflow multiplies either. theta_ja_c_w is the NO-heatsink
  //        figure: under 90 % of a known part's, under 10 C/W, or a
  //        theta_source that speaks of a heatsink, with no heatsink
  //        declared, is an error.
  //        slug: "top" | "bottom" - a pad-up package (exposed pad on top,
  //        for a heatsink). Known pad-up parts (TPA3221 by Value or LCSC
  //        C475500, TPA3116D2, TPA3255, a TI TPA/TAS MPN ending DDV/DAD/
  //        DKD/DKQ) need no slug key; their board path is the no-heatsink
  //        theta_JA, no via array is asked of them, and a pad-up part with
  //        no thermal entry is a warning.
  //        heatsink: {"theta_sa_c_w": 20, "source": "...", "part": "...",
  //        "hold": "clip|screw|pushpin|adhesive|lid", "mount_refs": "H5,H6",
  //        "theta_jc_c_w": 1.1, "interface_c_w": 1.1 | "interface_mm": 0.1
  //        + "interface_k_w_mk": 3 [+ "contact_mm2": 29],
  //        "spreading_c_w": 1, "ref": "HS1"} - in parallel with the board
  //        path; theta_jc and contact_mm2 default to a known pad-up part's
  //        RthetaJC(top) and slug area; clip/screw/pushpin need mount_refs
  //        on the board.
  //        tj_max_c: absolute junction limit, against the box air
  //        (enclosure) or ambient_c. power_max_w: continuous worst case;
  //        over budget without "governor" (the firmware limit) warns.

  // check_thermal (P8): the box the board sits in. Air = room_c (30) +
  // heat / (walls + stack-effect vents); heat = the thermal entries'
  // power_w + other_w. box_mm or "stl" (assembled box, bounding box).
  // Vent areas in series: inlet low, riser (edge gap past the board),
  // outlet high. material sets Tg (PLA PETG PCTG ABS ASA PC) or tg_c.
  // floor: feet (default, half the floor convects) | flat | open.
  "enclosure": {"box_mm": [106, 96, 46.8], "wall_mm": 2.4,
                "material": "PETG", "room_c": 30, "other_w": 1.4,
                "inlet_mm2": 3300, "riser_mm2": 228, "outlet_mm2": 3300,
                "stack_mm": 40, "floor": "feet"},
  // the air when there is no enclosure (C); tj_max_c and ripple life use it,
  // and check_ratings holds every part's operating range, resistor power
  // (derated from 70 C to 0 at 155 C, also in sim_board), fuse rerating
  // and PPTC hold current against it. With an enclosure and no ambient_c,
  // check_ratings takes the box air check_thermal works out; with neither,
  // 25 C, said so in its report's `ambient`.
  "ambient_c": 45,
  // check_ratings: the coldest the board starts in (C); a part whose
  // operating range starts above it is an error. Absent: not checked.
  "ambient_min_c": -20,

  // check_thermal (P8): bulk-capacitor ripple current and life
  "ripple": [{"ref": "C5", "irms_a": 0.55, "rated_irms_a": 0.6,
              "freq_multiplier": 1.2, "source": "Rubycon PX ripple table",
              "irms_max_a": 1.25, "life_h": 2000, "rated_c": 105,
              "life_min_h": 30000, "chem": "electrolytic"}],
  //        irms_a over rated_irms_a x freq_multiplier: > 100 % error,
  //        > 75 % warning; irms_max_a over the rating warns; life_h at
  //        rated_c scaled 2x per 10 K (polymer 10x per 20 K) to the air +
  //        5 K x share^2, under life_min_h warns.

  // placelib/place_seed/place_metrics/place_anneal (P6)
  "placement": {
    "edges": [{"ref": "J1", "edge": "left", "pos": 0.5, "rot": 90}],
    //        edge in left|right|top|bottom (render-oriented: top = min y);
    //        pos 0..1 along the edge: where the part's ORIGIN sits, as a
    //        fraction of the edge's length (omit = distributed); rot optional
    "groups": [{"name": "xtal", "anchor": "Y1", "members": ["C8", "C9"]}],
    "keepouts": [{"rect": [x1, y1, x2, y2], "side": "front",
                  "reason": "antenna"}],   // or "poly": [[x,y], ...]
    "fixed": ["H1", "H2"],
    "separation": [{"a": ["U3"], "b": ["U5"], "min_mm": 10}],
    "corridors": [{"a": "J1", "b": "U1", "width_mm": 5.5, "net": "VBUS"}],
    //        keep-clear swath between two parts; place_anneal charges any
    //        other body per mm2 of intrusion (T6 P6A-5)
    "sides": [{"ref": "J1", "side": "front", "reason": "mates from the top"}]
    //        U19: the side a part MUST be assembled on. place_anneal can
    //        otherwise move a cluster to the back when that measurably wins
    //        (it prices the second reflow side, so it will not do it
    //        gratuitously); a pinned ref is never flipped. Declare it for
    //        anything the enclosure, a mating direction or a heatsink fixes.
    "mates": [{"ref": "J2", "mate": "onboard_flex",
               "corridor": [[x1, y1, x2, y2], [x1, y1, x2, y2]],
               "mate_refs": ["DS1"], "reason": "LCD tail lies into J2"}]
    //        a connector whose mate lies on THIS board (an FPC socket taking
    //        the flex tail of a display mounted on the board), so its mouth
    //        rightly faces inward. check_mating and the place gate then drop
    //        the board-edge rule for it and check instead that the corridor
    //        (the rectangles the flex lies over, its real outline: the wide
    //        part at the panel too) crosses the mouth face
    //        (mating_mate_misaligned) and holds no part on the connector's
    //        side but mate_refs, the footprints that are the mate itself
    //        (mating_zone_blocked). mate: onboard_flex only. Declare the
    //        same rectangles as keepouts so place_anneal keeps them clear.
  },

  // planes_gen (P7). Defaults when absent: 2-layer -> B.Cu GND pour;
  // 4-layer -> In1 GND + In2 dominant-power; high_speed references are
  // always guaranteed a plane.
  "planes": [{"layer": "In2.Cu", "net": "+3V3",
              "region": [x1, y1, x2, y2]}],  // region optional (full board)

  // knowledge.py --select (U4): P2's machine-readable block list. Each
  // topology token keys reference/knowledge/records/ retrieval into the
  // P3/P6/P7 spawn prompts (deterministic - no declared block, no records).
  // name/block are labels only; topology is the retrieval key. U13:
  // operating_point = the block's design point as unit-suffixed dims
  // (numbers) and *_kind tokens; knowledge.py --coverage tests it against
  // each record's envelope (undeclared dim = record stays "provisional").
  // Declare what the block's mechanisms scale with: vin/vout/iout, fsw,
  // edge rate, dissipation, layer count, hard/soft switching, sync/async,
  // integrated-FET/controller, source class, control kind. Same optional
  // key on diff_pairs entries (interface slots also read impedance_ohm).
  // U14: the eight dims below are exactly what a buck block must declare to
  // reach `covered` against checklists/buck.yaml - board-level facts
  // (board_layers) are repeated per block, there are no board defaults.
  "blocks": [{"topology": "buck", "block": "B3", "name": "U1 AP64350 class",
              "operating_point": {"vin_v": 12, "vout_v": 5, "iout_a": 3,
                                  "pdiss_w": 0.8, "board_layers": 4,
                                  "switching_kind": "hard",
                                  "rectifier_kind": "sync",
                                  "integration_kind": "integrated-fet",
                                  "source_kind": "usb-pd"}}]
}
```

The board-adjacent `decoupling.json` (associations of cap -> IC power pin) is
a SEPARATE file emitted by the schematic generators (schlib
`place_ic_with_decoupling` + `Project.save(decoupling=...)`); shape:
`{"associations": [{"cap": "C1", "ic": "U1", "pin": "48", "rail": "+3V3",
"value": "100nF", "gnd": "GND", "class": "hf", "max_dist_mm": 5,
"max_loop_nh": 6, "role": "reg_input"}]}` (class/max_*/role optional;
consumed by check_decoupling, check_pdn, place_seed satellite clustering,
place_metrics). `"role": "reg_input"` goes on EVERY cap serving a switching
regulator's input pin (buck/boost VIN): check_decoupling then errors
(kind=reg_input_no_hf) unless one of them is an HF ceramic (<= 1 uF, or
explicit class "hf") within 7.5 mm of the pin. Value classes alone cannot
see a MISSING cap - a lone 22 uF at a buck VIN reads as a well-placed bulk
cap (lumina-carrier R1, rework on shipped boards). Intake of an external
board should declare the role while authoring decoupling.json wherever the
BOM shows a switching regulator.

Sources for the numbers: interface budgets from research (P1 interface-spec),
currents from the power tree (P1 power-architect), impedances from
`reference/stackups.yaml` controlled_impedance tables (P2 stackup selection).
