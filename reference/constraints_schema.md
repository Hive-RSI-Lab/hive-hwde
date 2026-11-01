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
    "data_rate_mbps": 10000,         // check_via_stubs knee (also on diff_pairs)
    "f_max_ghz": 6.0,                // check_via_stubs knee, RF nets (also on rf)
    "return_via_radius_mm": 2.0,     // fallback when t_rise_ns absent
    "impedance_ohm": 50              // optional; rules_gen reports the outer
                                     // microstrip width (impedance facts) and
                                     // writes a track_width rule per inner
                                     // stripline layer (pours above + below
                                     // in "planes"); no netclass, the board
                                     // keeps its own (RF boards)
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
    "term_pair_mm": 2.5,             // cross-ref terminal-match window (T2);
                                     // same-footprint P/N pads always match
    // running skew (#476): flagged where it stays over max_phase_skew_mm
    // (default max_skew_mm) for phase_window_mm (else rise_ps / 10 as
    // distance, else 5 mm); route_tune bumps the short leg there
    "max_phase_skew_mm": 0.15, "phase_window_mm": 5, "rise_ps": 500
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

  // check_length_groups (P8) and route_tune (P7): each net's routed length
  // (track + via barrel, pad to pad) held to its group's reference. nets,
  // ref_nets, not_longer_than take fnmatch globs, with or without the
  // sheet's leading "/". No ref_net(s): matched to the group's longest
  // member. ref_stat min|max|mean of the reference nets (default min).
  // window_mm [lo, hi] (length - reference) replaces the symmetric tol_mm;
  // max_mm / min_mm / not_longer_than (no net longer than the shortest of
  // these) are absolute bounds. terminals limits the path ends to these
  // refs' pads. Nets here are also left alone by route_cleanup's snap.
  "length_groups": [{
    "name": "BYTE0", "nets": ["DRAM_D[0-7]", "DRAM_DQM0"],
    "ref_nets": ["DRAM_SDQS0_P", "DRAM_SDQS0_N"], "tol_mm": 0.635,
    "not_longer_than": ["DRAM_SDCLK0_*"]
  }, {
    "name": "ADDR_CMD", "nets": ["DRAM_A*", "DRAM_BA*"],
    "ref_nets": ["DRAM_SDCLK0_*"], "window_mm": [-5.08, 0],
    "terminals": ["U1", "U3"], "severity": "error"
  }],

  // place_replicate (P6 place / P7 route, #482): one layout for N
  // identical channels. source = a sheet instance name ("ch1") or path
  // ("/ch1/"), or a ref list; targets = sheet names, or one ref list per
  // target (paired by position). Sheet parts pair by symbol uuid (the
  // netlist's tstamps: the same in every instance of a sheet file), else
  // by ref number mod 100 (R101 <-> R201). transform grid: target i sits
  // at (i+1) * pitch; explicit: one {dx, dy, rot 0|90|180|270} per target,
  // rotated about origin (default the centre of the source parts). route
  // false = placement only. margin_mm (1.0): slack round the source
  // courtyards' hull inside which shared nets' (GND) copper is copied.
  // place_metrics / route_quality report channel_symmetry.
  "channels": [{"name": "afe", "source": "ch1", "targets": ["ch2", "ch3"],
                "transform": "grid", "pitch": [0, 10]}],

  // check_ddr_timing (P8, #487): the timing budget over the length groups
  // above. groups name length_groups entries: dq = members vs their
  // ref_nets (the DQS pair), addr = members vs ref_nets (CK), dqs = the
  // strobe pair's nets vs the `clock` length group. Parts come from
  // controller_part / dram_part or the refs' MPN/Value field and are
  // looked up in reference/ddr_timing.yaml. Optional: input_levels
  // {data, addr} (AC160 | AC135), slew_vns {dq, dqs, addr, ck} (default
  // 1/2/1/2, the datasheet base point), allowances_ps {isi, xtalk, vref},
  // controller_terms {out_skew_ps, write_calibration, cal_step_tck,
  // write_leveling, leveling_range_tck, max_rate_mts}, timing 1T | 2T.
  "ddr": {
    "controller": "U100", "dram": "U200", "rate_mts": 800,
    "groups": [{"name": "BYTE0", "kind": "dq"},
               {"name": "ADDR_CMD", "kind": "addr"},
               {"name": "DQS0", "kind": "dqs", "clock": "CK"}]
  },

  // check_testpoints (P8, #470): test-point access and coverage. All
  // optional: nets (globs; default every net with 2+ pads), probe_mm 0.5
  // (flying probe), fixture_mm 0.8, pitch_mm 1.27, body_mm 1.0 and
  // body_h_ratio 0.3 (clearance to part bodies: the larger), edge_mm 3.0,
  // max_via_drill_mm 0.5; min_coverage_pct warns under it; fixture true
  // warns on every test point a bed-of-nails pin can't use.
  "testpoints": {"nets": ["+*", "GND", "/NRST"], "min_coverage_pct": 90,
                 "fixture": false},

  // check_surge (P8, #492): IEC 61000-4-5 surge / 61000-4-4 EFT on cable
  // ports. Only declared ports are judged. kind picks the assumed level from
  // reference/surge_levels.yaml (environment residential | industrial, by
  // cable_m and outdoor); level_kv / eft_kv force one. earthed adds the
  // line-earth modes on dc_power and mains. nets (globs) narrows the lines
  // (a USB-C port: VBUS). clamps overrides a clamp's ratings {vbr, vc, ipp_a
  // (8/20), ppk_w, vrwm}; abs_max a pin's limit (ref or ref.pin).
  "surge": {"environment": "residential",
            "ports": {"J1": {"kind": "dc_power", "cable_m": 2, "nets": ["VBUS"],
                             "level_kv": 0.5}},
            "abs_max": {"U2.3": 30}},

  // check_isolation (P8, #353): isolation domains and barriers against IEC
  // 60664-1 / 62368-1 (reference/iso_tables.yaml, every row marked read or
  // not). Not judged without this block. Every barrier value is required: a
  // missing one is exit 2, none is assumed. impulse_v may be mains_v_rms +
  // overvoltage_category. standard iec60664-1 | iec62368-1; grade functional
  // | basic | supplementary | reinforced; material_group I | II | IIIa | IIIb.
  // Optional: withstand_v_rms / peak_v (Table F.7a), min_clearance_mm /
  // min_creepage_mm (the board's own floor over the tables), inner_min_mm (same
  // inner layer, as solid insulation), solid_min_mm (through the laminate),
  // column general | printed_wiring. parts: the ONLY parts allowed pins in
  // both domains, each with the ratings that must cover the barrier. floating:
  // nets known to be in no domain (globs); their copper still counts as
  // shortening the path. Mask earns no credit.
  "isolation": {
    "domains": {"HOST": {"nets": ["VBUS", "GND_USB", "/usb/*"]},
                "LOGIC": {"nets": ["+3V3", "GND", "/mcu/*"]}},
    "barriers": [{"name": "usb", "between": ["HOST", "LOGIC"],
                  "standard": "iec60664-1", "grade": "basic",
                  "working_v_rms": 250, "impulse_v": 4000,
                  "withstand_v_rms": 5000, "pollution_degree": 2,
                  "material_group": "IIIa", "altitude_m": 2000}],
    "parts": {"U1": {"kind": "digital_isolator", "grade": "basic",
                     "working_v_rms": 400, "impulse_v": 8000,
                     "withstand_v_rms": 5000, "creepage_mm": 8.0,
                     "source": "datasheet, rev"}},
    "floating": []},

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
  // pin_modes (sim_board): how its io scenarios treat an IC pin, by refdes
  // and pin number or function name. io/output/gpio: driven low and high;
  // od: an open-drain status line; analog/adc/input/nc: never driven (an
  // ADC input on a peak detector, an unused pad). Without it, pins with a
  // GPIO name (PB11, IO5) or an SoC pad function (ENET2_TX_EN,
  // UART4_TX_DATA) are driven, unless the net is named as analog (ADC_x,
  // x_SNS, x_DIV, x_MON). `--pin-mode U1.PB11=analog` does the same.
  "pin_modes": {"U1": {"PB11": "analog", "PC3": "analog"},
                "U100": {"B15": "output"}},
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
  // package_ratings: a part rated for its nets at its own pin pitch (a
  // 48 V controller's 0.5 mm pins, a 100 V 0402). rules_gen emits one
  // clearance rule scoped to that courtyard after the HV rules;
  // check_creepage keeps the pairs as dru_footprint_scope warnings.
  "package_ratings": [{"ref": "U2", "clearance_mm": 0.1,
                       "why": "LM5069 VIN pin, 0.5 mm pitch"}],

  // check_si (P8): signal integrity of the nets that need it. Nets are
  // found by name and driver (clocks, RMII, I2S, SPI, USB, Ethernet MDI,
  // crystals); this block only declares a net the names miss, corrects a
  // class or edge, or sets a target. Every key is optional.
  // via_backdrill + check_via_stubs (#478): opt-in controlled-depth drilling.
  // Off JLC only. depth_tol_mm: from the fab's quote (default 0.15).
  "backdrill": [{"net": "/SIG", "drill_mm": 0.5, "depth_tol_mm": 0.1,
                 "max_stub_mm": 0.4, "vendor": "pcbway"}],
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

  // check_rf_match (P8, #488): a matching network's populated values vs
  // the load it feeds. load (a ref; or net, the feed) and zin "R+jX@f"
  // (datasheet / measured) or s1p (Touchstone one-port, relative to this
  // file); band_hz [lo, hi] (default: the zin frequency); z0 50, rl_db 10,
  // model "generic0402" | "ideal". Design a network: hwde rf_synth match.
  "rf_match": [{"load": "AE1", "zin": "22-j31@2.45G",
                "band_hz": [2.4e9, 2.4835e9], "rl_db": 10}],

  // check_cap_load (P8): the cable capacitance in pF an op-amp sees through
  // each connector, for its phase margin. A connector not named gets 1000 pF
  // (10 m of audio cable at ~100 pF/m); a board-to-board header is ~20.
  "cable_pf": {"J3": 1000, "J7": 20},

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

  // planes_gen (P7); rules_gen reads the layers too: an inner layer with a
  // pour on the copper above and below is stripline and gets solved
  // per-layer impedance widths (#323). Defaults when absent: 2-layer -> B.Cu GND pour;
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
