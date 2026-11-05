# Escaped BGAs for the route_finish maze

One small 10-layer `JLCH101611Y2-1078L` board per package: the BGA, escaped by
`route_escape --refs U1`, and a connector J1 at (25, 9) with one F.Cu pad per
net. What `route_finish` has left to do is the run from each escape stub to J1.
`tests/test_hdi_maze.py` holds the answers.

| directory | part | pitch |
|---|---|---|
| `wlcsp49_0.4` | WLCSP-49, 7 x 7 | 0.4 mm |
| `fbga153_0.5` | FBGA-153 eMMC | 0.5 mm |
| `tfbga54_0.8` | TFBGA-54 | 0.8 mm |

Built from `CASES` and `make_board` in `tests/test_hdi_route.py`, then
`rules_gen --layers 10 --stackup JLCH101611Y2-1078L` and `route_escape`. J1's
pads sit at `((i % 40 - 19.5) * 0.8, (i // 40) * 1.2)`, size 0.5 x 0.8 mm, one
for each net in sorted order.

They are frozen on purpose: a change to the escape would otherwise move the
maze's answers too, and this pins the maze alone.
