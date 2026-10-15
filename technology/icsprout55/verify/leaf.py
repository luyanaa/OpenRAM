#!/usr/bin/env python3
"""Generate ICsprout55 OpenRAM leaf modules and run selected verification decks.

Usage:
  venv/bin/python bin/leaf.py [--lib H7CR|SC9T] [--decks tape,os,klayout] [--no-lvs]
                              [--quiet] 'ptx(width=0.3,tx_type="nmos")' 'pinv(size=1)' ...

Each spec is module_type(kwargs).  Outputs go to runs/leaf_<name>/.
"""
import argparse
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
HERE = os.environ.get("ICS55_WORK", os.path.join(os.getcwd(), "ics55_work"))
# The repository is imported as the "openram" package.
_pkg = os.path.join(HERE, "pkg")
os.makedirs(_pkg, exist_ok=True)
if not os.path.exists(os.path.join(_pkg, "openram")):
    os.symlink(REPO, os.path.join(_pkg, "openram"))
sys.path.insert(0, os.path.join(HERE, "pkg"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENRAM_HOME", os.path.join(REPO, "compiler"))
os.environ.setdefault("OPENRAM_TECH", os.path.join(REPO, "technology"))

import cal  # noqa: E402
import opensource  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--lib", default="SC9T")
    ap.add_argument("--decks", default="tape,os")
    ap.add_argument("--pdk-root")
    ap.add_argument("--librelane-root")
    ap.add_argument("--drc-waivers", default=str(opensource.DEFAULT_DRC_WAIVERS))
    ap.add_argument("--drc-profile", choices=("macro", "diagnostic"), default="macro")
    ap.add_argument("--no-lvs", action="store_true")
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--margin", type=float, default=0.4,
                    help="implant margin around a standalone leaf (context stand-in)")
    ap.add_argument("--extra", default="", help="extra config lines, ';' separated")
    a = ap.parse_args()
    decks = [deck.strip() for deck in a.decks.split(",") if deck.strip()]
    unknown = set(decks) - {"tape", "os", "klayout"}
    if unknown:
        ap.error("unknown deck(s): {}".format(", ".join(sorted(unknown))))
    if not decks and not a.no_check:
        ap.error("select at least one verification deck")

    cfgdir = os.path.join(HERE, "runs", "leafcfg")
    os.makedirs(cfgdir, exist_ok=True)
    cfg = os.path.join(cfgdir, "leaf_{}.py".format(os.getpid()))
    with open(cfg, "w") as f:
        f.write('tech_name = "icsprout55"\nstdcell_library = "{}"\n'.format(a.lib))
        f.write('bitcell = "pbitcell"\nanalytical_delay = True\nnominal_corner_only = True\n')
        f.write('check_lvsdrc = False\nuse_nix = False\nword_size = 2\nnum_words = 16\n')
        f.write('output_path = "{}/"\n'.format(cfgdir))
        for line in a.extra.split(";"):
            if line.strip():
                f.write(line.strip() + "\n")

    import openram
    openram.init_openram(config_file=cfg, is_unit_test=False)
    from openram import OPTS
    from openram.sram_factory import factory
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ics55_finalize", os.path.join(REPO, "technology", "icsprout55", "tech", "ics55_finalize.py"))
    fin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fin)
    from openram import tech

    items = []
    generation_failed = False
    for spec in a.specs:
        m = re.match(r"(\w+)\((.*)\)$", spec.strip())
        mtype, kw = (m.group(1), m.group(2)) if m else (spec.strip(), "")
        kwargs = eval("dict({})".format(kw))
        try:
            mod = factory.create(module_type=mtype, **kwargs)
        except BaseException as e:  # debug.error raises AssertionError
            import traceback
            traceback.print_exc()
            print("GENERATION FAILED:", spec, repr(e))
            generation_failed = True
            continue
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", spec)[:60].strip("_")
        d = os.path.join(HERE, "runs", "gen_" + safe)
        os.makedirs(d, exist_ok=True)
        raw = os.path.join(d, mod.name + "_raw.gds")
        gds = os.path.join(d, mod.name + ".gds")
        sp = os.path.join(d, mod.name + ".sp")
        mod.sp_write(sp, lvs=True)
        mod.gds_write(raw)
        info = fin.finalize_gds(raw, gds, mod.name, tech.lib_cell_names(),
                                report=lambda s: print(safe, s), margin=a.margin)
        print("{}: {} {:.3f} x {:.3f} um  {}".format(safe, mod.name, mod.width, mod.height, info))
        items.append(dict(gds=gds, top=mod.name, cdl=sp, tag="leaf_" + safe))
    openram.end_openram()
    if a.no_check:
        return 1 if generation_failed else 0
    checks = []
    commercial = [deck for deck in decks if deck in ("tape", "os")]
    if commercial:
        checks.extend(cal.run_many(items, decks=commercial, lvs=not a.no_lvs,
                                  quiet=a.quiet, vconnect=True).values())
    if "klayout" in decks:
        checks.extend(opensource.run_many(
            items, pdk_root=a.pdk_root, librelane_root=a.librelane_root,
            out_dir=HERE, run_lvs=not a.no_lvs, quiet=a.quiet,
            drc_waivers=a.drc_waivers, drc_profile=a.drc_profile).values())
    ok = not generation_failed and bool(items) and all(checks)
    print("ALL CHECKS PASS" if ok else "CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
