#!/usr/bin/env python3
"""Run ICsprout55 Calibre DRC/LVS on the EDA VM and summarize results.

Usage:
  cal.py GDS TOP [--cdl NETLIST] [--decks tape,os] [--tag NAME] [--no-lvs] [--no-drc]

Both decks are copies of the signoff decks with only these edits:
  * LAYOUT/SOURCE PATH/PRIMARY substituted,
  * Density_rules NO and CHIPEDGE_CHECK NO (chip-level checks, see PORT_STATUS),
  * tapeout LVS: the chip-specific lvs_box.list include is commented out.
The work directory lives under the VMware shared folder so the VM sees it.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

# Work area (decks/, runs/) -- must be visible to the Calibre host under the
# same tree, mapped LOCAL_ROOT -> VM_ROOT (e.g. a VMware shared folder).
HERE = os.environ.get("ICS55_WORK", os.path.join(os.getcwd(), "ics55_work"))
DECKS = os.path.join(HERE, "decks")
RUNS = os.path.join(HERE, "runs")
LOCAL_ROOT = os.environ.get("ICS55_LOCAL_ROOT", "/home/petergu/LoongSoC")
VM_ROOT = os.environ.get("ICS55_VM_ROOT", "/mnt/hgfs/LoongSoC")
SSH = ["ssh", "-F", "/dev/null", "-o", "LogLevel=ERROR"] + \
      os.environ.get("ICS55_SSH", "-i /home/petergu/.ssh/id_rsa_windows user@172.16.142.129").split()
CALIBRE = os.environ.get("ICS55_CALIBRE", "/home/user/calibre/aoi_cal_2020.2_14.12/bin/calibre")
LICENSE = os.environ.get("ICS55_MGLS_LICENSE_FILE", "28000@127.0.0.1")


def vm_path(p):
    p = os.path.abspath(p)
    assert p.startswith(LOCAL_ROOT), p
    return VM_ROOT + p[len(LOCAL_ROOT):]


def prepare(rundir, kind, deck, top, vconnect=False):
    d = os.path.join(rundir, "{}_{}".format(deck, kind))
    os.makedirs(d, exist_ok=True)
    text = open(os.path.join(DECKS, "{}_{}.svrf".format(deck, kind)),
                encoding="latin-1").read()
    text = text.replace("@TOP@", top).replace("@GDS@", "layout.gds")
    text = text.replace("@CDL@", "source.cdl")
    if kind == "lvs" and vconnect:
        # Leaf checks only: join same-named supply labels that the parent's
        # power grid would connect.  Never used for macro sign-off.
        text = text.replace("VIRTUAL CONNECT REPORT YES",
                            'VIRTUAL CONNECT REPORT YES\nVIRTUAL CONNECT NAME "vdd" "gnd" "VDD" "VSS"', 1)
    open(os.path.join(d, "runset"), "w", encoding="latin-1").write(text)
    if kind == "lvs":
        shutil.copy(os.path.join(DECKS, "subckt.sp"), d)
    return d


def parse_drc(path):
    res = {}
    if not os.path.exists(path):
        return None
    for line in open(path, errors="replace"):
        m = re.match(r"RULECHECK (\S+) \.+ TOTAL Result Count = (\d+)", line)
        if m and int(m.group(2)) > 0:
            res[m.group(1)] = int(m.group(2))
    return res


def parse_lvs(path):
    if not os.path.exists(path):
        return "NO REPORT"
    text = open(path, errors="replace").read()
    # Only the OVERALL COMPARISON banner counts (per-HCELL banners follow it).
    m = re.search(r"OVERALL COMPARISON RESULTS(.*?)(CELL\s+SUMMARY|$)", text, re.S)
    block = m.group(1) if m else text
    if re.search(r"#\s+INCORRECT\s+#", block):
        return "INCORRECT"
    if re.search(r"#\s+CORRECT\s+#", block):
        return "CORRECT"
    return "UNKNOWN"


def run_many(items, decks=("tape", "os"), lvs=True, drc=True, quiet=False,
             out=print, vconnect=False):
    """items: list of dict(gds, top, cdl=None, tag).  Returns {tag: ok}."""
    jobs = []
    for it in items:
        rundir = os.path.join(RUNS, it["tag"])
        if os.path.exists(rundir):
            shutil.rmtree(rundir)
        os.makedirs(rundir)
        shutil.copy(it["gds"], os.path.join(rundir, "layout.gds"))
        if it.get("cdl"):
            shutil.copy(it["cdl"], os.path.join(rundir, "source.cdl"))
        for deck in decks:
            if drc:
                jobs.append((it["tag"], "drc", rundir, prepare(rundir, "drc", deck, it["top"])))
            if lvs and it.get("cdl"):
                jobs.append((it["tag"], "lvs", rundir, prepare(rundir, "lvs", deck, it["top"], vconnect)))

    script = ["set -u", "export MGLS_LICENSE_FILE={}".format(LICENSE)]
    for n, (tag, kind, rundir, d) in enumerate(jobs):
        shm = "/dev/shm/ics55or_{}_{}".format(n, os.getpid())
        script += [
            "rm -rf {0}; mkdir -p {0}; cd {0}".format(shm),
            "cp {}/runset . ; cp {}/layout.gds .".format(vm_path(d), vm_path(rundir)),
        ]
        if kind == "lvs":
            script += ["cp {}/source.cdl . ; cp {}/subckt.sp .".format(vm_path(rundir), vm_path(d))]
            script += ["{} -lvs -hier -turbo 4 -nowait runset > calibre.log 2>&1".format(CALIBRE)]
            script += ["cp -f lvs.rep* calibre.log {}/ 2>/dev/null".format(vm_path(d))]
        else:
            script += ["{} -drc -hier -turbo 4 -nowait runset > calibre.log 2>&1".format(CALIBRE)]
            script += ["cp -f drc_CAL.SUM drc_CAL.OUT calibre.log {}/ 2>/dev/null".format(vm_path(d))]
        script += ["cd /; rm -rf {}".format(shm)]
    subprocess.run(SSH + ["bash -s"], input="\n".join(script), text=True,
                   check=False)

    status = {}
    for tag, kind, rundir, d in jobs:
        name = os.path.basename(d)
        ok = True
        if kind == "drc":
            res = parse_drc(os.path.join(d, "drc_CAL.SUM"))
            if res is None:
                out("{:28s} {:9s} DRC: NO SUMMARY (see {}/calibre.log)".format(tag, name, d))
                ok = False
            else:
                tot = sum(res.values())
                line = "{:28s} {:9s} DRC: {:5d}".format(tag, name, tot)
                if res:
                    line += "  " + " ".join("{}={}".format(r, n) for r, n in
                                            sorted(res.items(), key=lambda x: -x[1])[:(4 if quiet else 99)])
                out(line)
                ok = tot == 0
        else:
            verdict = parse_lvs(os.path.join(d, "lvs.rep"))
            out("{:28s} {:9s} LVS: {}".format(tag, name, verdict))
            ok = verdict == "CORRECT"
        status[tag] = status.get(tag, True) and ok
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gds")
    ap.add_argument("top")
    ap.add_argument("--cdl")
    ap.add_argument("--decks", default="tape,os")
    ap.add_argument("--tag")
    ap.add_argument("--no-lvs", action="store_true")
    ap.add_argument("--no-drc", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    tag = a.tag or "{}_{}".format(a.top, time.strftime("%m%d_%H%M%S"))
    st = run_many([dict(gds=a.gds, top=a.top, cdl=a.cdl, tag=tag)],
                  decks=a.decks.split(","), lvs=not a.no_lvs,
                  drc=not a.no_drc, quiet=a.quiet)
    print("ALL CLEAN" if all(st.values()) else "NOT CLEAN")
    sys.exit(0 if all(st.values()) else 1)


if __name__ == "__main__":
    main()
