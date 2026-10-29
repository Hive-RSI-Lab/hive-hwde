# PCB-0017-A stereo-class-d-amp — brief

Owner, #ai-ee thread 1790482921.525089: rebuild and compare against a published 44 x 34 mm reference design.

- TPA3116D2 stereo class-D amplifier on a single 12 V supply, stereo input and output, SMD parts only,
  2-layer board as small as possible. Target: match or beat the reference design's 44 x 34 mm.
- Correctness and the TPA3116D2's thermal pad / heat-sinking come before size; say where we deviate.
- The reference design's reported parts: TPA3116D2; 2.2 uF X7R input coupling; 100 uF + 100 nF + 1 nF supply bypass;
  10 uH LC output filters per channel with damping branches; 5.6k/100k config and pull-up resistors;
  wire pads J1-J4 and TP1. About 40 SMD parts, 45 footprints.
- Source: https://jlcpcb.com/blog/gpt-6-astra-pcb-design-in-kicad
- Stop at a DRC/ERC-clean fab package. Do NOT order. End with COMPARISON.md: our size, layers,
  part count and DRC result against the reference design's.
