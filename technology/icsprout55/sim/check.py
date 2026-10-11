#!/usr/bin/env python3
"""Check a Spectre run against the tbgen trace.

Each read issued at the rising edge of cycle k must show the expected data on
dout just before the rising edge of cycle k+1.  Also reports the access time
(rising edge of the read cycle -> last 50% crossing of any dout bit) and the
slack to the sampling edge.
Usage: check.py RAWDIR TRACE --period NS --vdd V [--skew1 F] [--json OUT]
"""
import argparse, glob, json, os, re


def read_psf(path):
    lines = open(path, errors="replace").read().splitlines()
    vi = lines.index("VALUE")
    data = {}
    t = None
    times = []
    for line in lines[vi + 1:]:
        if line.startswith('"time"'):
            t = float(line.split()[1]); times.append(t)
            continue
        if line.startswith('"'):
            name, rest = line[1:].split('"', 1)
            try:
                v = float(rest.split()[0])
            except (ValueError, IndexError):
                continue
            data.setdefault(name, []).append(v)
    return times, data


def crossings(times, vals, th):
    out = []
    for i in range(1, len(vals)):
        a, b = vals[i - 1], vals[i]
        if (a - th) * (b - th) < 0:
            out.append(times[i - 1] + (th - a) / (b - a) * (times[i] - times[i - 1]))
    return out


def value_at(times, vals, t):
    import bisect
    i = bisect.bisect_left(times, t)
    if i <= 0:
        return vals[0]
    if i >= len(times):
        return vals[-1]
    t0, t1 = times[i - 1], times[i]
    return vals[i - 1] + (vals[i] - vals[i - 1]) * (t - t0) / (t1 - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw"); ap.add_argument("trace")
    ap.add_argument("--period", type=float, required=True)
    ap.add_argument("--vdd", type=float, required=True)
    ap.add_argument("--skew1", type=float, default=0.0)
    ap.add_argument("--json")
    a = ap.parse_args()
    T = a.period * 1e-9
    files = glob.glob(os.path.join(a.raw, "*.tran*")) if os.path.isdir(a.raw) else [a.raw]
    times, data = read_psf(files[0])
    exp = {}
    for line in open(a.trace):
        t = line.split()
        if t and t[0] == "E":
            k, v = t[2].split("=")
            exp.setdefault(int(t[1]), {})[k] = v
    th = a.vdd / 2
    nread = nerr = 0
    worst_tacc = 0.0
    worst_slack = None
    errors = []
    tend = times[-1]
    for k, outs in sorted(exp.items()):
        for bus, bits in outs.items():
            port = int(bus[4:])
            sk = a.skew1 * T if port > 0 else 0
            t_edge = (k + 1) * T + sk               # rising edge of cycle k
            t_samp = t_edge + T - 10e-12
            if t_samp > tend:
                continue
            nread += 1
            bad = 0
            for i, ch in enumerate(reversed(bits)):
                name = "{}_{}".format(bus, i)
                if name not in data:
                    continue
                v = data[name]
                got = 1 if value_at(times, v, t_samp) > th else 0
                if ch in "01" and int(ch) != got:
                    bad += 1
                cr = [c for c in crossings(times, v, th) if t_edge < c < t_samp]
                if cr:
                    worst_tacc = max(worst_tacc, cr[-1] - t_edge)
                    sl = t_samp - cr[-1]
                    worst_slack = sl if worst_slack is None else min(worst_slack, sl)
            if bad:
                nerr += 1
                errors.append((k, bus, bad))
    res = dict(reads=nread, read_errors=nerr, errors=errors[:10],
               tacc_ns=round(worst_tacc * 1e9, 4),
               slack_ns=None if worst_slack is None else round(worst_slack * 1e9, 4),
               passed=(nerr == 0 and nread > 0))
    print(json.dumps(res))
    if a.json:
        json.dump(res, open(a.json, "w"))


if __name__ == "__main__":
    main()
