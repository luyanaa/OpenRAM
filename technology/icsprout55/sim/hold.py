#!/usr/bin/env python3
"""dout hold after the rising edge that ends a read cycle: earliest dout
transition (50 % crossing) after that edge, over all read cycles.
Usage: hold.py RAWDIR TRACE --period NS --vdd V"""
import argparse, glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import read_psf, crossings
ap = argparse.ArgumentParser()
ap.add_argument("raw"); ap.add_argument("trace")
ap.add_argument("--period", type=float, required=True)
ap.add_argument("--vdd", type=float, required=True)
a = ap.parse_args()
T = a.period * 1e-9
t, d = read_psf(glob.glob(os.path.join(a.raw, "*.tran*"))[0])
reads = sorted({int(l.split()[1]) for l in open(a.trace) if l.startswith("E ")})
best = None
for n in d:
    if not n.startswith("dout"):
        continue
    cr = crossings(t, d[n], a.vdd / 2)
    for k in reads:
        edge = (k + 2) * T
        after = [c for c in cr if edge <= c < edge + T / 2]
        if after:
            h = (after[0] - edge) * 1e9
            best = h if best is None else min(best, h)
print("hold_ns", None if best is None else round(best, 3))
