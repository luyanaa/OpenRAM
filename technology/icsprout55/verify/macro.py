#!/usr/bin/env python3
"""Compile an OpenRAM macro for ICsprout55 and optionally check it.

Usage: macro.py CONFIG.py [--no-check] [--decks tape,os,klayout]
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
import opensource

ap = argparse.ArgumentParser()
ap.add_argument("config")
ap.add_argument("--no-check", action="store_true")
ap.add_argument("--decks", default="tape,os")
ap.add_argument("--quiet", action="store_true")
ap.add_argument("--klayout-mode", choices=("full", "top-interconnect"), default="full")
ap.add_argument("--drc-waivers", default=str(opensource.DEFAULT_DRC_WAIVERS),
                help="exact known leaf marker waiver JSON for KLayout")
ap.add_argument("--drc-profile", choices=("macro", "diagnostic"), default="macro")
ap.add_argument("--lvs-mode", choices=("flat", "hierarchical"), default="flat",
                help="strict OpenRAM comparison; hierarchy requires exact module correspondence")
ap.add_argument("--pdk-root")
ap.add_argument("--librelane-root")
pex_input = ap.add_mutually_exclusive_group()
pex_input.add_argument("--pex-json", help="normalized routed-geometry JSON for interconnect-only RCX")
pex_input.add_argument("--pex-gds", action="store_true", help="extract transistors and parasitics from the compiled GDS")
pex_input.add_argument("--pex-mos-only", action="store_true",
                       help="emit source-free GDS MOS devices on ideal logical nets (research/non-signoff)")
ap.add_argument("--pex-source", choices=("lef", "starrc", "pdk-estimate"), default="lef")
ap.add_argument("--pex-mode", choices=("c", "rcc"), default="c")
ap.add_argument("--pex-process-json")
ap.add_argument("--pex-corner", action="append")
ap.add_argument("--pex-require-coupling", action="store_true")
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
decks = [deck.strip() for deck in a.decks.split(",") if deck.strip()]
unknown = sorted(set(decks) - {"tape", "os", "klayout"})
if unknown:
    ap.error("unknown deck(s): {}".format(", ".join(unknown)))
if not decks and not (a.pex_json or a.pex_gds or a.pex_mos_only):
    ap.error("select at least one DRC/LVS deck or a PEX/topology input")

items = [dict(gds=os.path.join(out, name + ".gds"), top=name,
              cdl=os.path.join(out, name + ".lvs.sp"), tag="macro_" + name)]
checks = []
commercial = [deck for deck in decks if deck in ("tape", "os")]
if commercial:
    checks.extend(cal.run_many(items, decks=commercial, quiet=a.quiet).values())
if "klayout" in decks:
    klayout_results = opensource.run_many(
        items, pdk_root=a.pdk_root, librelane_root=a.librelane_root,
        drc_waivers=a.drc_waivers, drc_profile=a.drc_profile,
        lvs_profile="openram-macro", lvs_mode=a.lvs_mode)
    checks.extend(klayout_results.values())
if a.pex_json:
    pex_ok = opensource.run_rcx(
        a.pex_json, pdk_root=a.pdk_root, out_dir=os.path.join(out, "pex"),
        process_json=a.pex_process_json, corners=a.pex_corner,
        require_coupling=a.pex_require_coupling)
    checks.append(pex_ok)
if a.pex_gds:
    try:
        pex = opensource.run_gds_pex(
            items[0]["gds"], name, pdk_root=a.pdk_root, out_dir=os.path.join(out, "pex"),
            source=a.pex_source, corners=a.pex_corner, process_json=a.pex_process_json,
            mode=a.pex_mode)
    except (opensource.OpenSourceError, OSError, ValueError) as exc:
        ap.error(str(exc))
    checks.append(pex["accepted"])
if a.pex_mos_only:
    try:
        topology = opensource.run_gds_topology(
            items[0]["gds"], items[0]["cdl"], name,
            out_dir=os.path.join(out, "topology"))
    except (opensource.OpenSourceError, OSError, ValueError) as exc:
        ap.error(str(exc))
    checks.append(topology["equivalent"])
print("ALL CHECKS PASS" if all(checks) else "CHECKS FAILED")
sys.exit(0 if all(checks) else 1)
