# hifi-receiver-2: the product

A cheap all-in-one hi-fi streaming amplifier in three tiers. You plug in a
USB-C charger and your speakers, connect it to Wi-Fi or Ethernet, and play to
it from Spotify, AirPlay, Bluetooth or a home music server. You control it
from a web app on your phone, with buttons, or with a remote. It is meant to
be in the class of the WiiM Ultra/WiiM Amp, at a fraction of the price.

The brief is the person's message in `.hive/goals.md`. Every decision below
follows from it, and each says why. Prices come from live JLC/LCSC searches
on 2026-10-28, run with hwde's `parts_search.py`. The line-by-line BOM is
[`bom-estimate.csv`](bom-estimate.csv) and `tools/bom_estimate.py` totals it.
Later tasks keep this page current: when a decision changes, change it here
and say why.

| | **Lite** (barebones) | **Plus** (medium) | **Ultra** (full) |
|---|---|---|---|
| Who it is for | the cheapest way to stream to a pair of speakers | the person's own system: Viennas driven hard, two subs, FLX4 | a WiiM-Ultra-like box with a screen and a second speaker zone |
| Power in | USB-C PD 20 V (65–100 W charger) | USB-C PD 28 V EPR (140 W charger) | USB-C PD 28 V EPR (140 W charger) |
| Amplifier | TPA3118D2, 2 ch | TPA3221, 2 ch | 2 × TPA3221: zone A + zone B, 4 ch |
| Power per channel, 4 Ω, 1% THD | **43 W** | **77 W** | **77 W** (the 140 W is shared across 4 ch) |
| DAC | ES8388 codec (≈96 dB) | **ES9018K2M Sabre** mains + PCM5102A subs | same as Plus |
| Sub outputs (RCA) | 2, full range (sub's own crossover) | 2, bass-managed (crossover, level, delay, phase) | same as Plus |
| RCA line in (FLX4) | yes, with an analog zero-latency bypass | yes, ADC + analog "DJ direct" switch | yes + optical (TOSLINK) in |
| Network | Wi-Fi 2.4 GHz | Wi-Fi + Ethernet | Wi-Fi + Ethernet |
| Bluetooth | A2DP sink (SBC) | same | same |
| Streaming | Spotify Connect, AirPlay 1, Bluetooth, Lyrion (LMS) | same | same |
| Control | web app, 3 buttons, IR (bring your own remote) | web app, knob + 2 buttons, IR remote in box | web app, 2" colour screen, knob + 3 buttons, IR remote |
| Boards | 1: `lite-main` (2-layer) | 1: `core` (4-layer) | 2: `core` fully populated + `ultra-front` |
| Unit cost at 100, board + assembly + PCB | **$12.3** | **$25.0** | **$34.5** |
| Target retail (charger not included / with charger) | **$59 / $79** | **$129 / $159** | **$199 / $229** |
| Build order | **first** | second | third |

## The decisions, one by one

### What the speakers need

The brief names Vienna Acoustics speakers but not the model. Vienna Acoustics
rates its current range at **4 Ω nominal**: the Haydn Grand SE is 4 Ω and
88.5 dB, the Mozart Grand SE is 4 Ω and 90 dB, and the Beethoven Baby Grand
and Concert Grand are 4 Ω and 89–91 dB. The makers recommend 30–50 W minimum
up to 180–300 W
([SoundStage](https://www.soundstagehifi.com/index.php/equipment-reviews/308-vienna-acoustics-mozart-grand-se-loudspeakers),
[Stereophile](https://www.stereophile.com/content/vienna-acoustics-beethoven-concert-grand-loudspeaker),
[HomeTheaterHifi](https://hometheaterhifi.com/reviews/speaker/floor-standing/vienna-acoustics-beethoven-baby-grand-reference-speakers-review/)).
So every tier is designed to drive 4 Ω, and the 6 Ω and 8 Ω figures are given
for other speakers. **Open item:** the person's exact model. It changes
nothing above unless it is a 3 Ω or lower design.

### Power per channel, against the 10.9 W board already made

The board already made is hwde's `stereo-class-d-amp`: a TPA3118D2 on 12 V. It
measured **10.9 W per channel into 6 Ω at 0.37% THD**. Into 4 Ω, the same 12 V
board would give about 15.6 W.

The figures below are worked out from two sources:

- **TPA3118D2:** calibrated on the measured board. 10.9 W into 6 Ω from 12 V is
  an 11.44 V peak, so the outputs swing 95.3% of the supply, which is the same
  as an effective 0.30 Ω loss in series with the speaker.
- **TPA3221:** taken from TI's datasheet (SLASEE9C): 88 W into 4 Ω at 1% THD
  from 30 V. That is an 88.5% swing, or an effective 0.52 Ω loss.

P = (V·R/(R+R_loss))² / 2R.

| Supply, amp | 4 Ω | 6 Ω | 8 Ω | × the made board |
|---|---|---|---|---|
| 12 V, TPA3118D2 (the made board) | 15.6 W | **10.9 W** (measured) | 8.3 W | 1× |
| 20 V, TPA3118D2 (**Lite**) | **43 W** | 30 W | 23 W | 2.8× |
| 20 V, TPA3221 (Plus/Ultra on a 100 W non-EPR charger) | 39 W | 28 W | 22 W | 2.6× |
| 28 V, TPA3221 (**Plus/Ultra**) | **77 W** (92 W at 10% THD) | 55 W | 43 W | 5× |

These are peak (clipping) powers per channel, both channels driven, and the
supply sets how long the amp can hold them (next section). At 77 W, a Haydn
(88.5 dB/W/m) reaches about 107 dB at 1 m, and the pair about 101 dB at a 3 m
listening seat. That is far past loud listening, so the Viennas can be driven
hard.

**Why not more power?** 77 W into 4 Ω already uses the whole 140 W USB-C
budget. More watts would need a bigger supply, and USB-C cannot provide one
(see below).

**"So we can add more speakers too":**

- **Subwoofers:** two powered subs on the RCA sub outputs, in every tier.
- **A second pair:** Ultra has a speaker zone B, a second TPA3221 with its own
  terminals. It shares the same 140 W, so all four channels together hold
  about 28 W each continuous, and each one still peaks at 77 W.
- **Not supported:** wiring two pairs in parallel on one output. That gives 2 Ω
  per channel, which the TPA3221 drives in BTL only below 3 Ω.
- **Other rooms:** more units join over the network. Squeezelite/LMS sync
  groups players.

### Power input: USB-C PD, and whether 140 W is enough

**Decision:** USB-C Power Delivery only, no AC side, as the brief hoped.

- **Lite** asks for **20 V**: any 65–100 W laptop charger.
- **Plus and Ultra** ask for **28 V EPR**: a 140 W PD 3.1 charger such as the
  Apple 140 W, Anker or Ugreen, plus a **240 W (5 A, EPR) cable**.

On a charger without 28 V they fall back to 20 V and give 39 W per channel.
On a 5 V-only port, a laptop for example, the board still boots with the
amplifier held off, so it can be flashed and tested from that port.

The PD sink is the WCH **CH224Q** (C46061833, $0.27): PD 3.2, EPR, 5–28 V,
140 W. hwde has already built CH224-class boards, so the part is known.

**Is 140 W enough?**

- **Usable audio power:** take 140 W, keep 5% margin and 4 W for the
  electronics, and allow 88% amp efficiency. That leaves about **113 W of
  audio, continuous**.
- **Pure sine on both channels:** that is 2 × 56 W held indefinitely. 2 × 77 W
  would need about 175 W in, so it cannot be held, but music is not a sine.
- **Music:** at clipping, music averages about 1/8 of peak power (a crest
  factor of 9–15 dB). Two channels peaking at 77 W average about 20–25 W.
  The 140 W budget covers that about 5 times over, and the bulk capacitors
  carry the peaks.
- **So for one pair of 4 Ω speakers driven hard, yes, 140 W is enough.** It is
  not enough to drive two pairs (Ultra zone A + B) flat out continuously, so
  the firmware shares the budget.

The rule that makes it safe: **a firmware power governor.**

- **What it measures:** an INA180 shunt amp reads VBUS current (Plus/Ultra).
  Lite uses a level-based estimate instead.
- **What it does:** a limiter in the DSP holds the 1-second average input
  power under 90% of the negotiated PDO (126 W at 28 V/5 A, 90 W at 20 V/5 A,
  58 W at 20 V/3.25 A), with the amps' clip pins as a second input. A PD
  charger that goes over its current shuts the output off, and you hear that
  as a dropout.

**Other power facts the boards must follow:**

- **Inrush:** the USB PD inrush limit means the PVDD bulk capacitors connect
  only after negotiation, through a soft-started P-MOSFET (AO4485, 40 V).
- **28 V headroom is tight:** the TPA3221 is rated 7–30 V recommended and
  32 V absolute. EPR 28 V may sit at 29.4 V (+5%). So the switch also opens
  above about 30.5 V, and a 30 V TVS (SMBJ30A) sits on the connector.
- **Fallback amp:** the TPA3251 (36 V, $4.90, 96 in stock) is the fallback if
  bring-up shows overshoot. Check in the schematic task whether its pinout
  matches.
- **Connector:** the USB-C receptacle is rated 48 V/5 A (GCT USB4105-GF-A-120,
  C5184243). The common 20 V-rated ones are not acceptable at 28 V.

**What was rejected:**

- **A barrel-jack brick at 32–48 V with a TPA3251/3255:** this is the only way
  past 2 × 80 W. Rejected for now: it brings back the AC side the person wanted
  to avoid, and 77 W is already more than the speakers need.
- **Two USB-C inputs:** rejected on cost and complexity.

### Amplifier

- **Lite: TPA3118D2** (C46497, $0.81 at 100). It is the chip on the board
  already made and measured. hwde has its layout and lessons, and its thermal
  pad is underneath, so it needs no heatsink. At 20 V it gives 43 W into 4 Ω
  peak. **Unchecked:** the made board's chip temperature under load was never
  measured (hwde README). The Lite bring-up must measure it at 1/8 power into
  4 Ω before calling Lite done.
- **Plus/Ultra: TPA3221** (C475500, $1.65 at 100, 1,107 in stock), analog
  input, 88 W into 4 Ω at 1%, 108 dB SNR. It is the cheapest TI part that
  runs at 28 V: the TPA3116/3118 and the TAS58xx stop at 26 V. The
  alternatives cost more or have almost no stock:
  - TPA3251: 36 V, $4.90, 96 in stock
  - TPA3250: 36 V, pad underneath, $4.43, 23 in stock
  - TPA3244: 30 V, pad underneath, $4.89, 99 in stock

  The TPA3221's heat slug is **on top**, so it takes a bolted aluminium
  heatsink. hwde avoided that on the 3118 board; here the power demands it.
  - **Music:** at 1/8 power it dissipates about 3 W.
  - **Continuous sine:** at 2 × 56 W it dissipates about 12 W, which needs
    about 4 °C/W, a 40 × 40 × 20 mm heatsink or the enclosure's metal plate.
  - **Protection:** the governor and the chip's own over-temperature warning
    bound both cases.

**Rejected: an I²S-input amp with built-in DSP (TAS5825M/TAS5827/MA12070).**
It would drop the DAC, but they all stop at 26 V, and the person asked for a
Sabre DAC.

### DAC and the audio path

- **Plus/Ultra mains: ESS ES9018K2M Sabre** (C2688377, $2.40 at 100, 706 in
  stock), 127 dB DNR, with a 100 MHz oscillator and an OPA1678 output stage.
  - **Why this Sabre:** the person asked for "some sabre dac". The WiiM
    Ultra uses the ES9038Q2M, but LCSC has 0 in stock; it is listed at
    $4.91. The ES9033Q, ES9039Q2M and ES9028Q2M are all 0 in stock.
  - **Stock risk:** at 706 the stock is thin. Buy before the order, or keep
    a 2-line alternative (AK4493SEQ, $8.19) in the schematic notes.
- **Plus/Ultra subs: TI PCM5102A** (C107671, $0.94). It has its own 2.1 Vrms
  line driver, so it drives the two sub RCAs directly.
  - **Bass management:** the ESP32 does it, with a crossover, level, delay and
    phase per sub.
  - **High-pass on the mains:** optional. It takes the deepest bass off the
    Vienna woofers so they play louder cleanly.
- **Plus/Ultra line in: ES7243E ADC** (C2929446, $0.32). It needs no MCLK,
  which matters because Ethernet takes the ESP32's only MCLK-capable pin.
  - **Zero-latency path:** a TS5A23157 analog switch gives a **"DJ direct"**
    path from RCA in straight to the amp. A deck run through ADC → DSP →
    DAC is 5–15 ms late, and a DJ cueing on headphones can hear that.
- **Lite: Everest ES8388 codec** (C365736, $0.71): stereo DAC (≈96 dB), ADC,
  two output pairs and an analog bypass, all in one chip. It is the codec in
  the ESP32-A1S audio kits, so squeezelite-esp32 already supports it.
  - **Output pairs:** one pair drives the amp, the other the sub RCAs, each
    with its own level.
  - **Line in:** goes through the bypass, so it has zero latency.
- **Hi-res:** the DACs take up to 32-bit/384 kHz, but the ESP32 streaming
  core is the limit. The target is **24-bit/96 kHz**, to be confirmed at
  firmware bring-up. The person said "hi-res" and this is the honest figure
  at this price. 24/192 needs a Linux SoC (below).

### Streaming and control platform

**Decision:** one platform for all three tiers, the **ESP32-WROVER-E-N8R8**
module (C2973667, $3.73). It has Wi-Fi 4 (2.4 GHz), Bluetooth 4.2 Classic and
BLE, and 8 MB of PSRAM.

**The firmware** is a fork of
[squeezelite-esp32](https://github.com/sle118/squeezelite-esp32). It already
does Spotify Connect (cspot), AirPlay 1, a Bluetooth A2DP sink, Lyrion
(LMS) multi-room sync, IR, buttons, a rotary encoder, a display, Ethernet
(LAN8720) and OTA updates, all on exactly this module. Forking it instead of
writing the stacks from scratch is the single biggest saving in the project.

**Why this chip:**

- It is the only cheap chip with **Bluetooth Classic**, and A2DP needs it.
- The ESP32-S3 ($3.66) and ESP32-C5 (5 GHz Wi-Fi) are BLE-only, so they have
  no A2DP.
- Linux SoCs on LCSC cost $17–25 for the chip alone (T113-S3 $17.47, RV1106G3
  $22.49), plus a Wi-Fi/BT chip and a board-support package to bring up.
  Their gain is AirPlay 2 off MFi, UPnP, Roon Bridge and 24/192, none of
  which the person asked for by name.

**Rejected: a Linkplay module (what WiiM itself runs).** It brings certified
Spotify Connect, AirPlay 2, Tidal Connect and Chromecast, but it is not on
LCSC, so JLC cannot assemble it, and it would replace the firmware and app
work the brief asks the Hive to do. It is the right path if this is ever sold
with certified services.

| Feature | Tiers | How | Licence/certification cost |
|---|---|---|---|
| Wi-Fi 2.4 GHz | all | ESP32 module (FCC/CE modular approval) | $0 to build. To sell: FCC Part 15B SDoC test for the whole product (~$1.5–3k) and CE RED/EMC (~$5–10k) |
| Ethernet 100 Mb | Plus, Ultra | LAN8720A RMII PHY + HR911105A magjack | $0 |
| Bluetooth A2DP sink, SBC | all | ESP32 Classic BT | $0 to build. To sell under the Bluetooth name: SIG Adopter **product qualification $12,000** per design (fee schedule of 1 March 2026, [bluetooth.com/fee-schedule](https://www.bluetooth.com/fee-schedule/)) |
| AAC, aptX, LDAC | cut | | aptX: Qualcomm licence. LDAC sink: Sony licence. AAC: patent pool and not in the ESP-IDF sink. SBC sounds fine at this level. |
| Spotify Connect | all | cspot (open source, Zeroconf, Premium account) | $0 for the person's own units. **Not certified.** Selling it as "Spotify Connect" needs a Spotify partner agreement (Spotify Embedded); small makers usually get it through a certified module vendor such as Linkplay. |
| AirPlay 1 | all | squeezelite-esp32's RAOP | $0 (unofficial; iOS still plays to AirPlay 1 receivers). **AirPlay 2 cut:** Apple MFi licence. |
| Tidal, Qobuz, Deezer, radio, local files, multi-room | all | Lyrion Music Server (free) on any always-on computer, plus AirPlay from the services' apps | $0 |
| Chromecast, Tidal Connect, Qobuz Connect, Alexa, Roon Ready | cut | | Each needs its own licence and certification. |
| HDMI eARC | cut | | HDMI adopter fees (~$5k/yr + royalty) and an HDMI receiver chip. Ultra's optical input covers the TV. |
| App | all | **Web app (PWA)** served by the device at `http://hifi.local`, installable on the phone home screen: volume, source, EQ, sub crossover/level, power mode, updates, Wi-Fi setup. LMS control apps (Material Skin and others) also work. | $0. Native store apps cut: Apple developer programme $99/yr plus review, for nothing the PWA can't do. |
| Wi-Fi setup | all | BLE or SoftAP captive portal from the phone; Ethernet on Plus/Ultra just works | $0 |
| Remote | all read IR; Plus/Ultra ship one | IRM-H638T receiver; the app has a "learn" mode, so any TV or universal remote works | $0 (NEC 21-key remote, ~$0.80, AliExpress) |
| Buttons | all | Lite: vol−, vol+, play/source (hold = BT pairing). Plus: EC11 knob (push = play) + source + preset. Ultra: knob + 3 buttons on the front board | $0 |
| Display | Ultra | 2.0" 320×240 ST7789 IPS (C5329582, $2.41): now playing, source, volume, input power | $0 |
| Optical in | Ultra | PLR237 TOSLINK receiver into the Sabre's S/PDIF input (check the ES9018K2M input-mux registers in the schematic task) | $0 |
| Headphone out | cut | The FLX4 has headphones for cueing, and a headphone amp is cost the brief didn't ask for. | |
| Phono, microphone/voice | cut | The FLX4 is line level. Voice needs licences and is not ease of use here. | |

**Plugging in for bring-up and test** (the brief's "connect it to a laptop or
the network so it can talk to the server"):

- **USB-C to a laptop:** the one USB-C port carries power and data. A CH340C
  on D+/D− gives a serial console and flashing. With the amp held off, a
  laptop alone powers it.
- **On the network:** the firmware advertises `_hifi._tcp` over mDNS and
  exposes an HTTP test API for the Hive's bench script. The API reports the
  PD contract (V/A), VBUS current, amp fault/clip/over-temperature, firmware
  version and the network. It can also play built-in test tones and sweeps,
  and run a self-test.

### Bill of materials and assembly cost

Per unit, from [`bom-estimate.csv`](bom-estimate.csv)
(`python3 tools/bom_estimate.py`):

| | Lite ×10 | Lite ×100 | Plus ×10 | Plus ×100 | Ultra ×10 | Ultra ×100 |
|---|---|---|---|---|---|---|
| Parts on the board | $11.74 | $9.99 | $22.70 | $18.57 | $31.70 | $25.91 |
| JLC assembly (setup, stencil, $3 per unique extended part, joints) | $7.68 | $1.65 | $11.17 | $2.71 | $14.19 | $3.48 |
| PCB (hwde price points × 2.5 calibration) | $0.50 | $0.62 | $2.97 | $2.31 | $3.47 | $2.93 |
| Off-board (heatsink, remote, FFC cable) | – | – | $2.10 | $1.40 | $3.20 | $2.15 |
| **Per unit** | **$19.92** | **$12.26** | **$38.95** | **$24.99** | **$52.56** | **$34.47** |
| Unique extended parts / SMT joints / THT joints | 17 / 394 / 18 | | 26 / 646 / 39 | | 29 / 861 / 48 | |

**Basic vs extended:** nearly every IC is an **extended** part, $3 per unique
part per order. That is why the per-unit cost falls so far from ×10 to ×100.
Only the passives, the tactile switch, the 25 MHz crystal and the 1 µF filter
capacitors are basic.

**Every main part, with its LCSC number and price at 100:**

| Block | Part | Price at 100 |
|---|---|---|
| Wi-Fi/BT | ESP32-WROVER-E-N8R8 (C2973667) | $3.73 |
| PD sink | CH224Q (C46061833) | $0.27 |
| USB-C, 48 V/5 A | USB4105-GF-A-120 (C5184243) | $0.53 |
| USB-UART | CH340C (C84681) | $0.33 |
| Buck to 5 V | SY8303AIC (C97054) | $0.38 |
| Buck to 3.3 V | SY8089A1AAC (C479074) | $0.07 |
| Audio LDO | LP5907-3.3 (C80670) | $0.13 |
| Codec (Lite) | ES8388 (C365736) | $0.71 |
| Amp (Lite) | TPA3118D2DAPR (C46497) | $0.81 |
| Sabre DAC | ES9018K2M (C2688377) | $2.40 |
| 100 MHz oscillator | C496694 | $0.39 |
| Sub DAC | PCM5102APWR (C107671) | $0.94 |
| ADC | ES7243E (C2929446) | $0.32 |
| Op-amp | OPA1678IDR (C192421) | $0.47 |
| Analog switch | TS5A23157 (C11133) | $0.28 |
| Amp (Plus/Ultra) | TPA3221DDVR (C475500) | $1.65 |
| Current sense | INA180A1 (C48533472) | $0.12 |
| Output inductor | XR0850-100M 10 µH 8 A (C48391614) | $0.15 |
| Ethernet PHY | LAN8720A (C45223) | $0.52 |
| RJ45 magjack | HR911105A (C12074) | $1.02 |
| Encoder | EC11E1834403 (C361165) | $1.40 |
| IR receiver | IRM-H638T (C91447) | $0.20 |
| Dual RCA | XDJK-0271-1650 (C7527522) | $0.20 |
| 4 mm banana socket | GC4010-F (C99865) | $0.15 |
| LCD | HS20HS072RX (C5329582) | $2.41 |
| TOSLINK receiver | PLR237/T8BK (C17223620) | $1.25 |

**How sure these numbers are:**

- **Parts:** live LCSC prices.
- **Passives:** an estimate, about 80/150/200 parts at about $0.004 each.
- **Assembly:** hwde's `jlc_pricing.yaml` model, plus JLC's hand-soldered
  through-hole rate. Expect ±30%, and more if JLC requires Standard PCBA
  ($25 setup) for the through-hole parts.
- **PCB:** hwde measured its own price table as 1.9–3.1× low against real JLC
  quotes, hence the ×2.5.
- **Real price:** the authoritative figure is JLC's quote on the finished
  board. The board tasks re-run `bom_estimate.py` with their real BOMs.

**Not in the per-unit figure:**

- **Enclosure:** FDM-printed PETG/ASA is about $3–6 in material. JLC MJF/SLA
  printing is about $25–45.
- **Charger:** a 65 W charger is about $10–12 wholesale, a 140 W EPR charger
  plus 240 W cable about $25–30.
- **Box and manual:** about $2.

**Placing any JLC or parts order spends the person's money, so it goes to the
person.** No worker places one.

### Target retail price

Retail is set at about 3–4.5× the landed cost at 100 units (board + enclosure + box: about $18, $33 and $45),
which is where cheap direct-to-consumer audio sits:

- **Lite: $59 without a charger, $79 with a 65 W charger.** Against
  Bluetooth-only amps like the Fosi BT20A (about $70–90), it adds Wi-Fi
  streaming, the app and sub outs.
- **Plus: $129 / $159 with a 140 W charger.** Against the WiiM Amp ($299) and
  Arylic A50+ (about $180), it adds more power into 4 Ω, a Sabre DAC, two
  bass-managed sub outs and a DJ input.
- **Ultra: $199 / $229.** The WiiM Ultra is $329 and has no amplifier. Ultra
  gives a screen, optical in, a second speaker zone and the Plus feature set.

Selling any tier needs the certification in the platform table: FCC/CE, the
$12k Bluetooth qualification and, for Spotify branding, a partnership. The
units the Hive builds for the person need none of it.

### Feature cuts, with reasons

**Lite**, against Plus:

- **Ethernet:** Wi-Fi covers streaming, and bring-up works over USB. Saves
  $1.6, the RMII pins and a 4-layer board.
- **Sabre DAC:** the ES8388 does DAC, ADC and the zero-latency bypass for
  $0.71. The Sabre path costs about $4.5 with its oscillator, sub DAC and ADC.
- **28 V EPR:** the TPA3118 tops out at 26 V. Staying at 20 V lets any laptop
  charger work and keeps the proven amp.
- **Bass management:** the subs use their own crossover. The ES8388 still
  gives a separate sub level.
- **Remote in the box:** the IR receiver stays at $0.20, and any learned
  remote works.
- **Rotary knob:** three buttons and the app do the same job.

**Plus**, against Ultra:

- **Display:** the app shows everything.
- **Optical in:** the person didn't ask for it.
- **Zone B:** one pair plus two subs is the person's system.
- **Front board:** the knob and IR sit on the core board's front edge.

**All tiers:**

- AirPlay 2, Chromecast, Tidal/Qobuz Connect, Alexa, Roon Ready, HDMI eARC,
  aptX/LDAC/AAC, a native app, a headphone out, phono and 5 GHz Wi-Fi.
- The reasons are in the platform table. Mostly it is a licence, or something
  nobody asked for that adds cost.

### Board split

- **`lite-main`: Lite, one 2-layer board** of about 100 × 80 mm. Everything on
  one board is the cheapest to make and assemble, and the ESP32-A1S-style
  circuit plus the TPA3118 are known to work on 2 layers.
- **`core`: Plus and Ultra, one 4-layer board** of about 120 × 100 mm, the same
  PCB for both tiers.
  - **What is on it:** power, PD, ESP32, Ethernet, Sabre/sub DAC/ADC, one
    TPA3221 (zone A) with a second TPA3221 footprint (zone B), the optical
    footprint, an FFC connector to a front board, and the knob/IR footprints
    on its front edge.
  - **Plus vs Ultra:** Plus leaves zone B, optical and the FFC unpopulated.
    Ultra leaves the knob/IR footprints unpopulated, because the front board
    carries them.
  - **Why 4 layers:** for Ethernet RMII, 5 A at 28 V and two class-D stages
    beside a 127 dB DAC.
  - **Why one layout:** one layout, one bring-up and one firmware board file
    serve two tiers.
- **`ultra-front`: Ultra only, a 2-layer board** of about 100 × 40 mm with the
  LCD, knob, 3 buttons, IR and a status LED, on a 20-pin 0.5 mm FFC. Ultra's
  front panel is what makes it feel finished, and keeping it off the core
  stops the core from depending on the enclosure.

### Which tier is built first, and why

**Lite first.** It is the lowest-risk path to a product that plays, and every
later tier reuses what it proves:

1. **Lowest risk:** its amp is the measured TPA3118D2, its codec is the one
   squeezelite-esp32 already supports, its PD is the CH224 family hwde has
   built, and it is one 2-layer board.
2. **It tests the whole pipeline the brief is about:** schematic and layout in
   hive-hwde, JLC package, bring-up over USB and the network, firmware,
   web app, enclosure and manual, at the smallest bill.
3. **Its firmware and app carry over unchanged:** the power governor, the test
   API, Wi-Fi setup and the web app all move to `core`. So when Plus comes, its
   new work is only the 28 V power stage, the Sabre path and Ethernet.
4. **It is already a big step for the person:** 2.8× the power of the board
   already made (43 W into the Viennas' 4 Ω against 15.6 W), plus sub outs and
   the FLX4 input.

**Plus second.** It is the person's own system, and needs the `core` board.
**Ultra third.** It adds `ultra-front` and populates zone B and optical on
`core`.

### Enclosure

The brief asks for a 3D-printable enclosure made in Onshape.

- **Unchecked:** whether the Hive has Onshape access. The step-back of
  2026-10-28 put that question to the person.
- **What the enclosure work needs:**
  - an Onshape account and an API key pair (access + secret) made available to
    the enclosure task. That is a credential, so it goes to the person.
  - the board outlines and connector/heatsink positions, exported from KiCad
    (STEP), from each board task.
  - a printer or JLC's 3D-printing service. Ordering is the person's money.
- **Material:** **PETG or ASA, not PLA,** because the TPA3221 heatsink can
  reach 60–80 °C and PLA softens at about 55 °C.
- **Plus/Ultra lid:** vent slots over the heatsink.
- **Fallback without Onshape:** the enclosure task can model it in
  CadQuery/OpenSCAD inside the Hive and export STL/STEP, so nothing waits on
  access.

### Open items

1. **The speaker model:** for the impedance curve. 4 Ω is assumed.
2. **Stock:**
   - ES9018K2M: 706 in stock.
   - PLR237: 240 in stock.
   - TPA3221: 1,107 in stock, enough for 10 units but worth buying ahead.
3. **Checks for the schematic tasks:**
   - **CH224Q:** the I²C readout and how it behaves when a requested voltage
     isn't on offer. The LCSC listing gives only "PD 3.2, 28 V, 140 W"; the
     WCH datasheet could not be fetched from here.
   - **ES7243E:** confirm it runs without MCLK.
   - **ES9018K2M:** its S/PDIF input mux.
   - **TPA3251:** whether it is pin-compatible with the TPA3221.
4. **Banana sockets:** whether a real 4 mm banana plug fits the GC4010-F
   socket. It is listed as a 4 mm banana female, but it is an RC-style part.
   If not, use panel-mount 5-way binding posts on the enclosure, wired to a
   screw terminal.
5. **Thermal:** the made TPA3118 board's temperature under load was never
   measured.
6. **Questions with the person** (step-back 2026-10-28):
   - what "done" means
   - the power ceiling
   - official Spotify vs cspot
   - Onshape access

   The answers may move a decision above. Change it here when they do.
