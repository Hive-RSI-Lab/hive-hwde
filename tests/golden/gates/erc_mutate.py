import sys, re
src, out, mode = sys.argv[1], sys.argv[2], sys.argv[3]
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
drop = []
if "flag" in mode:
    drop += [b for b in blocks(t, "symbol") if 'power:PWR_FLAG' in t[b[0]:b[1]]]
if "wire" in mode:
    ws = blocks(t, "wire"); drop += [ws[3], ws[17], ws[30]]
for a, b in sorted(drop, reverse=True):
    t = t[:a] + t[b:]
open(out, "w").write(t)
print(len(drop))
