"""FasterCap 2D microstrip: wide finite ground strip + slab + trace. Prints Z0."""
import math, re, subprocess, sys, tempfile
T = "/opt/eda-toolchain/iic-osic-tools-2026.09"
LP = f"{T}/lib/x86_64-linux-gnu:{T}/usr/lib/x86_64-linux-gnu:{T}/usr/lib"
FC = [f"{T}/lib64/ld-linux-x86-64.so.2", "--library-path", LP, f"{T}/foss/tools/rftoolkit/bin/FasterCap"]
C0 = 299792458.0
def S(f, n, a, b): f.write(f"S {n} {a[0]:.9g} {a[1]:.9g} {b[0]:.9g} {b[1]:.9g}\n")
def poly(f, n, pts):
    for a, b in zip(pts[:-1], pts[1:]): S(f, n, a, b)
def run(w, h, t, er, Lf=40, err=0.002):
    d = tempfile.mkdtemp(dir="/tmp/ingest-tools")
    L = Lf * max(h, w); g = t
    x0, x1 = -w/2, w/2
    def files(e):
        open(f"{d}/g_top.txt", "w").write("0\n"); 
        with open(f"{d}/g_top.txt", "w") as f: f.write("0 gnd top\n"); poly(f, "g", [(-L, 0), (L, 0)])
        with open(f"{d}/g_rest.txt", "w") as f: f.write("0 gnd rest\n"); poly(f, "g", [(L, 0), (L, -g), (-L, -g), (-L, 0)])
        with open(f"{d}/t_air.txt", "w") as f: f.write("0 tr air\n"); poly(f, "t", [(x0, h), (x0, h+t), (x1, h+t), (x1, h)])
        with open(f"{d}/t_die.txt", "w") as f: f.write("0 tr die\n"); poly(f, "t", [(x1, h), (x0, h)])
        with open(f"{d}/slab.txt", "w") as f:
            f.write("0 slab\n"); poly(f, "s", [(-L, 0), (-L, h), (x0, h)]); poly(f, "s", [(x1, h), (L, h), (L, 0)])
        with open(f"{d}/m.lst", "w") as f:
            f.write("* 2D microstrip\n")
            f.write(f"C g_top.txt {e} 0 0 +\nC g_rest.txt 1.0 0 0\n")
            f.write(f"C t_die.txt {e} 0 0 +\nC t_air.txt 1.0 0 0\n")
            if e != 1.0: f.write(f"D slab.txt 1.0 {e} 0 0 0 {h/2} -\n")
    caps = []
    for e in (er, 1.0):
        files(e)
        out = subprocess.run(FC + [f"{d}/m.lst", f"-a{err}"], capture_output=True, text=True, cwd=d).stdout
        blk = out.split("Capacitance matrix is:")[-1]
        v = re.findall(r"^g\d+_\S+\s+([-+0-9.e]+)\s*$", blk, re.M)
        caps.append(float(v[0]))  # 2D: last conductor is the reference, so 1x1 is C(trace, ground)
    Cd, Ca = caps
    return 1 / (C0 * math.sqrt(Cd * Ca)), Cd / Ca
if __name__ == "__main__":
    w, h, t, er = map(float, sys.argv[1:5])
    print(run(w, h, t, er))
