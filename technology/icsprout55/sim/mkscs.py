#!/usr/bin/env python3
"""Spectre deck from an OpenRAM netlist and a tbgen trace.

Inputs for cycle k change at the falling clock edge before it (half a period
of setup and hold to the DFFs).  Clocks: rising edge of cycle k at (k+1)*T.
Usage: mkscs.py MACRO.sp TRACE OUT.scs --period NS --corner tt --vdd 1.2
               --temp 25 [--skew1 FRACTION] [--cload FF]
"""
import argparse, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simenv import MODEL, VM



def subckt_pins(spfile, name):
    with open(spfile, encoding="utf-8") as stream:
        txt = stream.read()
    m = re.search(r"^\.SUBCKT\s+%s\b(.*?)\n(?!\+)" % re.escape(name), txt, re.M | re.S | re.I)
    body = m.group(1).replace("\n+", " ")
    return body.split()


def nd(p):
    return p.replace("[", "_").replace("]", "")


def read_trace(tr):
    cyc, exp = {}, {}
    with open(tr, encoding="utf-8") as stream:
        for line in stream:
            t = line.split()
            if not t:
                continue
            if t[0] == "C":
                cyc[int(t[1])] = dict(x.split("=") for x in t[2:])
            elif t[0] == "E":
                k, v = t[2].split("=")
                exp.setdefault(int(t[1]), {})[k] = v
    return cyc, exp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sp"); ap.add_argument("trace"); ap.add_argument("out")
    ap.add_argument("--name", required=True)
    ap.add_argument("--period", type=float, required=True)   # ns
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--vdd", type=float, default=1.2)
    ap.add_argument("--temp", type=float, default=25)
    ap.add_argument("--skew1", type=float, default=0.0, help="clk1.. delay as fraction of T")
    ap.add_argument("--cload", type=float, default=5.0)       # fF on outputs
    ap.add_argument("--tr", type=float, default=0.03)         # ns edge time
    ap.add_argument("--postlayout", action="store_true", help="extracted netlist (no pre_layout)")
    ap.add_argument("--diag", action="store_true", help="also save the control nets (s_en, w_en, ...)")
    ap.add_argument("--save", default="", help="extra nets to save, comma separated")
    a = ap.parse_args()
    T, tr, V = a.period, a.tr, a.vdd
    pins = subckt_pins(a.sp, a.name)
    cyc, exp = read_trace(a.trace)
    n = max(cyc) + 1
    L = ["// OpenRAM {} {} {}V {}C T={}ns".format(a.name, a.corner, V, a.temp, T),
         "simulator lang=spectre"]
    if not a.postlayout:
        # contact-to-poly edge capacitance for schematic (pre-layout) sims only
        L.append('include "{}" section=pre_layout'.format(MODEL))
    L += [
         'include "{}" section={}_mos'.format(MODEL, a.corner),
         "simulator lang=spice",
         '.include "{}"'.format(VM(a.sp)),
         "simulator lang=spectre",
         "VDD (vdd 0) vsource dc={}".format(V),
         "VGND (gnd 0) vsource dc=0"]
    # clocks
    clocks = [p for p in pins if re.match(r"clk\d+$", p)]
    for c in clocks:
        port = int(c[3:])
        d = T + (a.skew1 * T if port > 0 else 0)
        L.append("V{0} ({0} 0) vsource type=pulse val0=0 val1={1} delay={2}n rise={3}n fall={3}n "
                 "width={4}n period={5}n".format(c, V, d, tr, T / 2 - tr, T))
    # data inputs: pin -> list of (time, value)
    bus = {}
    for p in pins:
        m = re.match(r"(\w+)\[(\d+)\]$", p)
        if m:
            bus.setdefault(m.group(1), {})[int(m.group(2))] = p
    def bitval(field, idx):
        return int(field[len(field) - 1 - idx])
    srcs = 0
    for p in pins:
        if p in clocks or p in ("vdd", "gnd") or p.startswith("dout"):
            continue
        m = re.match(r"(\w+)\[(\d+)\]$", p)
        base, idx = (m.group(1), int(m.group(2))) if m else (p, 0)
        port = int(re.search(r"(\d+)$", base).group(1))
        sk = a.skew1 * T if port > 0 else 0
        pts = []
        prev = None
        for k in range(n):
            f = cyc[k].get(base)
            v = bitval(f, idx) if f is not None else 1
            t0 = (k + 1) * T - T / 2 + sk      # half a period before edge k at (k+1)T
            if prev is None:
                pts.append((0.0, v))
            elif v != prev:
                pts.append((t0, prev)); pts.append((t0 + tr, v))
            prev = v
        wave = " ".join("{:.4f}n {}".format(t, v * V) for t, v in pts)
        L.append("Vi{} ({} 0) vsource type=pwl wave=[{}]".format(srcs, nd(p), wave)); srcs += 1
    for p in pins:
        if p.startswith("dout"):
            L.append("C_{0} ({0} 0) capacitor c={1}f".format(nd(p), a.cload))
    L.append("XSRAM ({}) {}".format(" ".join(nd(p) for p in pins), a.name))
    stop = (n + 1.25) * T
    saves = " ".join([c for c in clocks] + [nd(p) for p in pins if p.startswith("dout")])
    if a.diag:
        ctrl = ["s_en{}", "w_en{}", "wl_en{}", "rbl_bl{}", "p_en_bar{}", "clk_buf{}"]
        nports = len(clocks)
        saves += " " + " ".join("XSRAM." + c.format(i) for c in ctrl for i in range(nports))
        saves += " " + " ".join("XSRAM.Xcontrol{}.rbl_bl_delay".format(i)
                                for i in range(nports))
    if a.save:
        saves += " " + " ".join(a.save.split(","))
    L.append("save {} VDD:p".format(saves))
    L.append("simOpts options temp={} tnom=25 reltol=1e-3 vabstol=1e-6 iabstol=1e-12 "
             "rawfmt=psfascii".format(a.temp))
    L.append("tran tran stop={}n maxstep={}n".format(stop, T / 50))
    open(a.out, "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
