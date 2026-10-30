# Brief: core board (Plus/Ultra)

Task 261028-145641-7c4786f95c, project hifi-receiver-2, issue #7.

Build the core board that docs/product.md defines for **Plus**, with Ultra's
zone B and optical input as population options, through hwde to a JLC
package: ERC and DRC clean, hwde's verify suite and JLCPCB DFM pass on the
exported Gerbers, BOM and CPL with LCSC part numbers, and a note of every
hwde snag hit. Nothing is ordered.

From the person (2026-10-28): "Done = fab files complete"; "we just build, no
need to follow protocols". hwde sign-offs (H1/H2/H4): go on with best
judgement and record the choice. Firmware is out of scope. Ordering stays
with the person.

The product definition is copied beside this file as `product.md`
(hifi-receiver-2 docs/product.md at b118554).
