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
    "plane_fed": true
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
  "voltages": [{"net": "HV_BUS", "voltage": 48}],
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
  //        airflow multiplies either.

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
