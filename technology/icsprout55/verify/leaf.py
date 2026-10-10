#!/usr/bin/env python3
"""Generate OpenRAM leaf modules for ICsprout55 and check them with Calibre.

Usage:
  venv/bin/python bin/leaf.py [--lib H7CR|SC9T] [--decks tape,os] [--no-lvs]
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--lib", default="SC9T")
    ap.add_argument("--decks", default="tape,os")
    ap.add_argument("--no-lvs", action="store_true")
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--margin", type=float, default=0.4,
                    help="implant margin around a standalone leaf (context stand-in)")
    ap.add_argument("--extra", default="", help="extra config lines, ';' separated")
    a = ap.parse_args()

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
        return
    st = cal.run_many(items, decks=a.decks.split(","), lvs=not a.no_lvs,
                      quiet=a.quiet, vconnect=True)
    print("ALL CLEAN" if all(st.values()) else "NOT CLEAN")


if __name__ == "__main__":
    main()
