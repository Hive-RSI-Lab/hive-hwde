import sys
src, out = sys.argv[1], sys.argv[2]
t = open(src).read()
def blocks(t, head):
    i = 0; res = []
    while True:
        j = t.find("\n\t(" + head, i)
        if j < 0: return res
        d = 0; k = j + 1
        while True:
            c = t[k]
            if c == "(": d += 1
            elif c == ")":
                d -= 1
                if d == 0: break
            k += 1
        res.append((j, k + 1)); i = k + 1
segs = blocks(t, "segment")
fps = [b for b in blocks(t, "footprint") if '"Reference" "R2"' in t[b[0]:b[1]]]
drop = [segs[len(segs)//2]] + fps[:1]
for a, b in sorted(drop, reverse=True):
    t = t[:a] + t[b:]
open(out, "w").write(t); print(len(segs), len(fps))
