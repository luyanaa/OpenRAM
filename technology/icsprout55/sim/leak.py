#!/usr/bin/env python3
"""Replica-bitline leakage check of a --diag run.

The replica bitline is precharged only by a short pulse after the clock rises
(p_en_bar = NAND(gated_clk_buf, rbl_bl_delay)); it then floats until the falling
edge.  If leakage pulls it below VDD/2 before the clock falls, w_en is
suppressed and s_en fires early.  Prints, per port, the lowest rbl voltage
seen just before a falling edge and the earliest droop crossing (ns after the
rising edge), i.e. the maximum safe clock-high time.
Usage: leak.py RAWDIR --period NS --vdd V
"""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import read_psf, value_at
from tanalyze import _dir

ap = argparse.ArgumentParser()
ap.add_argument("raw")
ap.add_argument("--period", type=float, required=True)
ap.add_argument("--vdd", type=float, required=True)
a = ap.parse_args()
T = a.period * 1e-9
t, d = read_psf(glob.glob(os.path.join(a.raw, "*.tran*"))[0])
res = {}
for n in sorted(d):
    base = n.split(".")[-1]
    if not base.startswith("rbl_bl"):
        continue
    cs, ss = _dir(t, d[n], a.vdd / 2)
    vmin, tdroop = None, None
    k = 0
    while (k + 1) * T + T / 2 < t[-1]:
        rise = (k + 1) * T; fall = rise + T / 2
        ups = [c for c, s in zip(cs, ss) if s > 0 and rise <= c < fall]
        if not ups:            # port idle: no precharge this cycle
            k += 1
            continue
        v = value_at(t, d[n], fall - 5e-12)
        vmin = v if vmin is None else min(vmin, v)
        dns = [c for c, s in zip(cs, ss) if s < 0 and ups and ups[-1] < c < fall]
        if dns:
            x = (dns[0] - rise) * 1e9
            tdroop = x if tdroop is None else min(tdroop, x)
        k += 1
    res[base] = dict(v_at_fall_min=None if vmin is None else round(vmin, 3), droop_cross_ns=None if tdroop is None else round(tdroop, 2))
print(json.dumps(res, sort_keys=True))
