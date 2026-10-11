#!/usr/bin/env python3
"""Corner / clock-period sweep of an OpenRAM macro with Spectre on the VM.

Usage: sweep.py MACRO_NAME --periods 10,6,4 [--corners all|tt_1p2_25,...]
                [--cycles 20] [--jobs 2] [--mt 4] [--skew1 0.0] [--tag X]
Results: $ICS55_WORK/sim/<tag>/results.jsonl (one line per run) and a table on stdout.
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from simenv import WORK, SSH, VM

CORNERS = {
    "tt_1p2_25":     ("tt", 1.20, 25),
    "tt_1p2_85":     ("tt", 1.20, 85),
    "tt_1p2_125":    ("tt", 1.20, 125),
    "ss_1p08_125":   ("ss", 1.08, 125),
    "ss_1p08_m40":   ("ss", 1.08, -40),
    "ff_1p32_m40":   ("ff", 1.32, -40),
    "ff_1p32_125":   ("ff", 1.32, 125),
    "snfp_1p08_125": ("snfp", 1.08, 125),
    "fnsp_1p08_125": ("fnsp", 1.08, 125),
    "snfp_1p32_m40": ("snfp", 1.32, -40),
    "fnsp_1p32_m40": ("fnsp", 1.32, -40),
}


def sh(cmd, **kw):
    return subprocess.run(cmd, check=False, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("macro")
    ap.add_argument("--periods", required=True)
    ap.add_argument("--corners", default="all")
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--mt", type=int, default=4)
    ap.add_argument("--skew1", type=float, default=0.0)
    ap.add_argument("--tag")
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--detach", action="store_true", help="start on the VM and return")
    ap.add_argument("--collect", action="store_true", help="only check existing results")
    ap.add_argument("--mdir", help="macro output dir (default runs/out_<macro>)")
    ap.add_argument("--sp", help="netlist to simulate (default <mdir>/<macro>.sp)")
    ap.add_argument("--postlayout", action="store_true", help="--sp is an extracted netlist")
    ap.add_argument("--diag", action="store_true", help="save control nets for timing analysis")
    ap.add_argument("--save", default="", help="extra nets to save, comma separated")
    a = ap.parse_args()
    mdir = os.path.abspath(a.mdir) if a.mdir else os.path.join(WORK, "runs", "out_" + a.macro)
    tag = a.tag or a.macro
    tdir = os.path.join(WORK, "sim", tag)
    os.makedirs(tdir, exist_ok=True)
    trace = os.path.join(tdir, "trace.txt")
    if not os.path.exists(trace):
        sh([sys.executable, os.path.join(HERE, "tbgen.py"), os.path.join(mdir, a.macro + ".v"),
            tdir, "--cycles", str(a.cycles)], stdout=subprocess.DEVNULL)
        sh(SSH + ["sh {} {} {}".format(VM(os.path.join(HERE, "vmrun.sh")), VM(tdir),
                                        VM(os.path.join(mdir, a.macro + ".v")))])
    corners = [c for c in CORNERS if c not in ("tt_1p2_85", "tt_1p2_125")] if a.corners == "all" else a.corners.split(",")
    periods = [float(x) for x in a.periods.split(",")]
    jobs = []
    for c in corners:
        proc, vdd, temp = CORNERS[c]
        for T in periods:
            name = "{}_T{}".format(c, str(T).replace(".", "p"))
            if a.skew1:
                name += "_sk{}".format(str(a.skew1).replace(".", "p"))
            scs = os.path.join(tdir, name + ".scs")
            sh([sys.executable, os.path.join(HERE, "mkscs.py"),
                os.path.abspath(a.sp) if a.sp else os.path.join(mdir, a.macro + ".sp"),
                trace, scs, "--name", a.macro, "--period", str(T), "--corner", proc,
                "--vdd", str(vdd), "--temp", str(temp), "--skew1", str(a.skew1)]
               + (["--postlayout"] if a.postlayout else []) + (["--diag"] if a.diag else []) + (["--save", a.save] if a.save else []))
            # liberal error preset (checked against moderate: < 2 ps difference)
            txt = open(scs).read()
            import re
            txt = re.sub(r"(tran tran stop=\S+) maxstep=\S+", r"\1 errpreset=liberal", txt)
            open(scs, "w").write(txt)
            done = os.path.exists(os.path.join(tdir, name + ".raw", "tran.tran.tran"))
            jobs.append((name, c, T, vdd, done))
    todo = [j[0] for j in jobs if a.rerun or not j[4]]
    if todo and not a.collect:
        # one job list per launch: several sweeps may share a tag directory
        listf = os.path.join(tdir, "jobs_{}.txt".format(os.getpid()))
        open(listf, "w").write("\n".join(todo) + "\n")
        cmd = "cd {d} && xargs -P {p} -I{{}} sh {s} {d} {{}} +mt={mt} < {j}".format(
            d=VM(tdir), p=a.jobs, s=VM(os.path.join(HERE, "vmspectre.sh")), mt=a.mt,
            j=os.path.basename(listf))
        if a.detach:
            cmd = "cd {d} && rm -f DONE && (nohup sh -c '{c} >> xargs.log 2>&1; touch DONE' < /dev/null > /dev/null 2>&1 &)".format(
                d=VM(tdir), c=cmd.replace("'", ""))
            sh(SSH + [cmd], stdin=subprocess.DEVNULL)
            print("detached:", len(todo), "jobs; wait for", os.path.join(tdir, "DONE"))
            return
        sh(SSH + [cmd])
    out = open(os.path.join(tdir, "results.jsonl"), "a")
    print("{:16s} {:>6s} {:>6s} {:>7s} {:>7s} {:>8s}".format("corner", "T(ns)", "pass", "reads", "tacc", "slack"))
    for name, c, T, vdd, _ in jobs:
        raw = os.path.join(tdir, name + ".raw")
        r = subprocess.run([sys.executable, os.path.join(HERE, "check.py"), raw, trace,
                            "--period", str(T), "--vdd", str(vdd), "--skew1", str(a.skew1)],
                           capture_output=True, text=True)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            res = dict(passed=False, reads=0, read_errors=None, tacc_ns=None, slack_ns=None,
                       error="no result")
        res.update(corner=c, period=T, run=name, macro=a.macro)
        out.write(json.dumps(res) + "\n")
        print("{:16s} {:6.2f} {:>6s} {:>7} {:>7} {:>8}".format(
            c, T, "PASS" if res.get("passed") else "FAIL",
            "{}/{}".format(res.get("reads", 0) - (res.get("read_errors") or 0), res.get("reads", 0)),
            res.get("tacc_ns"), res.get("slack_ns")))


if __name__ == "__main__":
    main()
