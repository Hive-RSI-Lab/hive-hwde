# JLC CPL rotation for a ball grid with no model

**Rule hwde uses:** with no LCSC model, CPL rotation = KiCad rotation + the turn that brings a part with A1 upper left and columns running right onto the footprint's ball names (bottom: 180 - KiCad rotation + that turn). `cpl_verify.footprint_a1` reads the turn from the names. It's `basis: footprint`. A match is `footprint_match`, which `dfm_check` reports as the warning `cpl_footprint_basis`. A mismatch is `cpl_rotation`, an error. An LCSC model, when there is one, always wins.

**Where it comes from. It isn't a JLC statement.**
- JLC publishes no BGA rule. Neither page below mentions BGA. Both read 2026-11-05.
- JLC, [PCB Assembly FAQs part 2](https://jlcpcb.com/help/article/pcb-assembly-faqs-part-2): "Zero orientation for PCB library should match the component orientation in the packaging(tape/reel)." And: "Even for the same package from the same manufacturer, they may have different orientations in the tape for different components". So JLC's zero belongs to the part, and a package can't fix it.
- Same page, on a silkscreen mark that disagrees with the CPL: "we will make the placement according to the silkscreen layer by default." With no mark to judge from, "we cannot recognize the correct polarity" and they confirm by email.
- JLC, [Common PCBA after-sales issues](https://jlcpcb.com/help/article/common-pcba-after-sales-issues-and-faq): placement is finalized "based on the CPL (Component Placement List) file provided by the customer and the PCB silkscreen markings", and it recommends "a silkscreen dot near Pin 1 of IC components."
- A1 upper left is IPC-7351 Level A, known here from secondary sources (Eurocircuits, the PCB Libraries forum), not from the standard itself. IEC 61188-7 Level B puts it lower left, so a footprint library or a tape can differ.

**Should a BGA with no model block an order?**
- A mismatch blocks, because the CPL then disagrees with the board's own ball names.
- A match doesn't block. It warns, because the check can't prove the part's tape zero. Confirm A1 in JLC's order preview against the part's tape spec, or give the part an LCSC number.
- A grid whose names give no A1 (`no_model`, for example a land pattern drawn mirrored) blocks: nothing has checked it.

**What settles it:** the part's tape spec and JLC's order preview, then a built board. Position isn't checked on this basis, only rotation.

**Evidence so far:**
- hw-lab hdi-bga U401-U404 (WLCSP-49, UFBGA-64, TFBGA-54, FBGA-153): the rotation this rule gives equals the LCSC model fit on all four. This shows the convention and those four models agree. It doesn't show what JLC does.
- JLC preview: none recorded yet.
- Built board: none yet.
