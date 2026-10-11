#!/usr/bin/env python3
"""Self-timing analysis of a --diag run.

All times are relative to the FALLING clock edge of the cycle in which the
operation happens (OpenRAM reads/writes in the low clock phase):
  sen     : s_en rising (sense amplifier fires)
  dout    : last 50% crossing of any dout bit after the falling edge
  wen_on  : w_en rising, wen_off: w_en falling (write pulse)
  pre_off : p_en_bar rising after the rising edge (end of precharge),
            relative to the RISING edge
Usage: tanalyze.py RAWDIR TRACE --period NS --vdd V
Prints JSON with the worst (max) values over all cycles.
"""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import read_psf, crossings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw"); ap.add_argument("trace")
    ap.add_argument("--period", type=float, required=True)
    ap.add_argument("--vdd", type=float, required=True)
    a = ap.parse_args()
    T = a.period * 1e-9
    th = a.vdd / 2
    t, d = read_psf(glob.glob(os.path.join(a.raw, "*.tran*"))[0])
    reads = set(); writes = set()
    for line in open(a.trace):
        f = line.split()
        if f and f[0] == "E":
            reads.add(int(f[1]))
        if f and f[0] == "C":
            kv = dict(x.split("=") for x in f[2:])
            # a write on any port: csbN=0 and (W port or webN=0)
            for k, v in kv.items():
                if k.startswith("csb") and v == "0":
                    p = k[3:]
                    if ("web" + p) in kv and kv["web" + p] == "0":
                        writes.add(int(f[1]))
                    elif ("din" + p) in kv and ("web" + p) not in kv:
                        writes.add(int(f[1]))
    res = {}

    def rel(name, kind, cycles, ref="fall"):
        if name not in d:
            return None
        cr_up = [c for c, s in zip(*_dir(t, d[name], th)) if s > 0]
        cr_dn = [c for c, s in zip(*_dir(t, d[name], th)) if s < 0]
        worst = None
        for k in cycles:
            rise = (k + 1) * T
            fall = rise + T / 2
            r0 = fall if ref == "fall" else rise
            lst = cr_up if kind == "up" else cr_dn
            ev = [c for c in lst if r0 - 1e-12 <= c < rise + T]
            if not ev:
                continue
            v = (ev[0] - r0) * 1e9
            worst = v if worst is None else max(worst, v)
        return None if worst is None else round(worst, 3)

    for n in d:
        base = n.split(".")[-1]
        if base.startswith("s_en"):
            res[base + "_rise"] = rel(n, "up", sorted(reads))
        if base.startswith("w_en"):
            res[base + "_rise"] = rel(n, "up", sorted(writes))
            res[base + "_fall"] = rel(n, "dn", sorted(writes))
        if base.startswith("p_en_bar"):
            res[base + "_end"] = rel(n, "up", sorted(reads | writes), ref="rise")
    # dout settle after the falling edge
    worst = None
    for k in sorted(reads):
        rise = (k + 1) * T; fall = rise + T / 2
        for n in d:
            if n.startswith("dout"):
                cr = [c for c in crossings(t, d[n], th) if fall - 1e-12 <= c < rise + T]
                if cr:
                    v = (cr[-1] - fall) * 1e9
                    worst = v if worst is None else max(worst, v)
    res["dout_after_fall"] = None if worst is None else round(worst, 3)
    print(json.dumps(res, sort_keys=True))


def _dir(times, vals, th):
    cs, ss = [], []
    for i in range(1, len(vals)):
        a, b = vals[i - 1], vals[i]
        if (a - th) * (b - th) < 0:
            cs.append(times[i - 1] + (th - a) / (b - a) * (times[i] - times[i - 1]))
            ss.append(1 if b > a else -1)
    return cs, ss


if __name__ == "__main__":
    main()
