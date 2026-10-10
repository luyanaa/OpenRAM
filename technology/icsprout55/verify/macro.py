#!/usr/bin/env python3
"""Compile an OpenRAM macro for ICsprout55 and check it with both Calibre decks.

Usage: venv/bin/python bin/macro.py CONFIG.py [--no-check] [--decks tape,os]
The config's output goes to runs/macro_<name>/.
"""
import argparse, os, subprocess, sys, time
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
HERE = os.environ.get("ICS55_WORK", os.path.join(os.getcwd(), "ics55_work"))
# The repository is imported as the "openram" package.
_pkg = os.path.join(HERE, "pkg")
os.makedirs(_pkg, exist_ok=True)
if not os.path.exists(os.path.join(_pkg, "openram")):
    os.symlink(REPO, os.path.join(_pkg, "openram"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cal

ap = argparse.ArgumentParser()
ap.add_argument("config")
ap.add_argument("--no-check", action="store_true")
ap.add_argument("--decks", default="tape,os")
ap.add_argument("--quiet", action="store_true")
a = ap.parse_args()
name = os.path.splitext(os.path.basename(a.config))[0]
out = os.path.join(HERE, "runs", "out_" + name)
os.makedirs(out, exist_ok=True)
cfg = os.path.join(out, name + "_cfg.py")
src = open(a.config).read()
with open(cfg, "w") as f:
    f.write(src + '\noutput_path = "{}/"\noutput_name = "{}"\n'.format(out, name))
env = dict(os.environ)
env["OPENRAM_HOME"] = os.path.join(REPO, "compiler")
env["OPENRAM_TECH"] = os.path.join(REPO, "technology")
env["PYTHONPATH"] = os.path.join(HERE, "pkg")
t0 = time.time()
r = subprocess.run([sys.executable, os.path.join(REPO, "sram_compiler.py"), "-v", cfg],
                   env=env, stdout=open(os.path.join(out, "compile.log"), "w"), stderr=subprocess.STDOUT)
print("compile {} rc={} in {:.0f}s (log {}/compile.log)".format(name, r.returncode, time.time() - t0, out))
if r.returncode != 0:
    os.system("tail -25 {}/compile.log".format(out))
    sys.exit(1)
if a.no_check:
    sys.exit(0)
st = cal.run_many([dict(gds=os.path.join(out, name + ".gds"), top=name,
                        cdl=os.path.join(out, name + ".lvs.sp"), tag="macro_" + name)],
                  decks=a.decks.split(","), quiet=a.quiet)
print("ALL CLEAN" if all(st.values()) else "NOT CLEAN")
