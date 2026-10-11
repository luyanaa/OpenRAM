#!/usr/bin/env python3
"""Extract Liberty-relevant numbers from one --diag Spectre run of a 1RW macro.

Times in ns, relative to the clock edges of the cycles in the trace:
  ins_rise      clk0 rising -> internal clk_buf rising (clock insertion, max)
  dout_fall     clock falling -> last dout 50 % crossing (max over reads)
  dout_tran     10-90 % transition of dout (max)
  wen_end       clock falling -> w_en falling (max over writes)
  chain_reset   rising edge after an access -> rbl_bl_delay falling (max;
                needs --save of that node), i.e. the minimum clock-high time
  leak_mw       supply power in the idle cycle 0 (mW)
  e_idle_pj / e_read_pj / e_write_pj   supply energy of an idle (csb0=1),
                read and write cycle (pJ)
Usage: charz.py RAWDIR TRACE --period NS --vdd V
"""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import read_psf, crossings, value_at
from tanalyze import _dir

ap = argparse.ArgumentParser()
ap.add_argument("raw"); ap.add_argument("trace")
ap.add_argument("--period", type=float, required=True)
ap.add_argument("--vdd", type=float, required=True)
a = ap.parse_args()
T = a.period * 1e-9
v = a.vdd
t, d = read_psf(glob.glob(os.path.join(a.raw, "*.tran*"))[0])
cyc = {}
for line in open(a.trace):
    f = line.split()
    if f and f[0] == "C":
        cyc[int(f[1])] = dict(x.split("=") for x in f[2:])
reads = sorted(k for k, c in cyc.items() if c.get("csb0") == "0" and c.get("web0") == "1")
writes = sorted(k for k, c in cyc.items() if c.get("csb0") == "0" and c.get("web0") == "0")
rise = lambda k: (k + 1) * T
fall = lambda k: (k + 1) * T + T / 2


def ups(n, th=v / 2):
    c, s = _dir(t, d[n], th); return [x for x, y in zip(c, s) if y > 0]


def dns(n, th=v / 2):
    c, s = _dir(t, d[n], th); return [x for x, y in zip(c, s) if y < 0]


def find(name):
    for n in d:
        if n == name or n.endswith(name):
            return n
    return None


res = {}
ck, cb = ups("clk0"), ups(find("clk_buf0"))
def mx(vals):
    vals = list(vals)
    return max(vals) if vals else None


res["ins_rise"] = mx(min(x for x in cb if x > r) - r for r in ck if any(x > r for x in cb)) * 1e9
douts = [n for n in d if n.startswith("dout")]
worst, tran = 0, 0
for k in reads:
    for n in douts:
        cr = [c for c in crossings(t, d[n], v / 2) if fall(k) <= c < rise(k) + T]
        if cr:
            worst = max(worst, cr[-1] - fall(k))
        lo = crossings(t, d[n], 0.1 * v); hi = crossings(t, d[n], 0.9 * v)
        for c in cr:
            l = [x for x in lo if abs(x - c) < 1e-9]; h = [x for x in hi if abs(x - c) < 1e-9]
            if l and h:
                tran = max(tran, abs(min(h, key=lambda x: abs(x - c)) - min(l, key=lambda x: abs(x - c))))
res["dout_fall"] = worst * 1e9
res["dout_tran"] = tran * 1e9
we = dns(find("w_en0"))
res["wen_end"] = mx((min(x for x in we if x > fall(k)) - fall(k)) * 1e9 for k in writes if any(x > fall(k) for x in we))
rd = find("rbl_bl_delay")
if rd:
    dd = dns(rd)
    # the replica bitline is discharged in the access's low phase and
    # restored after the NEXT rising edge; measure from that edge
    vals = [min(x for x in dd if x > rise(k) + T) - (rise(k) + T) for k in reads + writes
            if any(rise(k) + T < x < rise(k) + 2 * T for x in dd)]
    res["chain_reset"] = max(vals) * 1e9 if vals else None
ip = d["VDD:p"]


def energy(t0, t1):
    e = 0.0
    for i in range(1, len(t)):
        if t0 <= t[i] <= t1:
            e += -0.5 * (ip[i] + ip[i - 1]) * v * (t[i] - t[i - 1])
    return e


res["leak_mw"] = energy(rise(0) + 0.6 * T, rise(0) + 0.95 * T) / (0.35 * T) * 1e3
idle = [k for k, c in cyc.items() if c.get("csb0") == "1" and k >= 3 and cyc.get(k - 1, {}).get("csb0") == "1"]
res["e_idle_pj"] = (min(energy(rise(k), rise(k) + T) for k in idle) * 1e12) if idle else None
res["e_read_pj"] = mx(energy(rise(k), rise(k) + T) * 1e12 for k in reads)
res["e_write_pj"] = mx(energy(rise(k), rise(k) + T) * 1e12 for k in writes)
print(json.dumps({k: (round(x, 4) if isinstance(x, float) else x) for k, x in res.items()}))
