# See LICENSE for licensing information.
#
# Command line interface for the chip assembly layer.
#
#   chip_compiler.py list {macros,slots} [--json]
#   chip_compiler.py calc --slot S --macro M [--count N] [--json]
#   chip_compiler.py check <config.yaml> [--json]
#   chip_compiler.py gen <config.yaml> -o <dir> [--only verilog,librelane,docs]

import argparse
import json
import os
import sys

from .db import MacroDB, SlotDB, load_chip_config
from .fit import calculate_fit
from .generate import generate_all
from .models import ConfigError


def _print_json(data):
    print(json.dumps(data, indent=2, sort_keys=True))


def cmd_list(args):
    if args.kind in ("macros", "all"):
        macros = MacroDB()
        rows = []
        for name in macros.names():
            m = macros[name]
            rows.append({
                "name": name,
                "source": m.source,
                "size_um": [m.width_um, m.height_um],
                "organization": "{}x{}".format(m.words, m.data_width),
                "addr_width": m.addr_width,
                "description": m.description,
            })
        if args.json:
            _print_json({"macros": rows})
        else:
            print("{:<46} {:<9} {:>15} {:>10} {}".format(
                "MACRO", "SOURCE", "SIZE (um)", "ORGANIZATION", "DESCRIPTION"))
            for r in rows:
                print("{:<46} {:<9} {:>15} {:>10} {}".format(
                    r["name"], r["source"],
                    "{:.1f} x {:.1f}".format(*r["size_um"]),
                    r["organization"], r["description"] or ""))

    if args.kind in ("slots", "all"):
        slots = SlotDB()
        rows = []
        for name in slots.names():
            s = slots[name]
            rows.append({
                "name": name,
                "die_um": [s.die_width, s.die_height],
                "core_um": [s.core_width, s.core_height],
                "io_budget": {"dvdd": s.io_budget.dvdd,
                              "dvss": s.io_budget.dvss,
                              "input": s.io_budget.input,
                              "bidir": s.io_budget.bidir,
                              "analog": s.io_budget.analog},
                "reserved_area_um2": s.reserved_area_um2,
                "description": s.description,
            })
        if args.json:
            _print_json({"slots": rows})
        else:
            print()
            print("{:<10} {:>15} {:>15} {:>8} {}".format(
                "SLOT", "DIE (um)", "CORE (um)", "PADS", "DESCRIPTION"))
            for r in rows:
                pads = sum(r["io_budget"].values())
                print("{:<10} {:>15} {:>15} {:>8} {}".format(
                    r["name"],
                    "{:.0f} x {:.0f}".format(*r["die_um"]),
                    "{:.0f} x {:.0f}".format(*r["core_um"]),
                    pads, r["description"] or ""))
    return 0


def _parse_count(value):
    """argparse hands us a string; 'auto' stays a string, numbers become int."""
    if value is None:
        return "auto"
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text in ("auto", ""):
        return "auto"
    try:
        return int(text, 0)
    except ValueError:
        raise ConfigError(
            "--count must be 'auto' or an integer, got {!r}".format(value))


def cmd_calc(args):
    slot = SlotDB().get(args.slot)
    macro = MacroDB().get(args.macro)
    fit = calculate_fit(slot, macro, halo_um=args.halo,
                        count=_parse_count(args.count),
                        rows=args.rows, cols=args.cols, prefer=args.prefer)
    if args.json:
        data = fit.to_dict()
        data.update({"slot": slot.name, "macro": macro.name,
                     "macro_organization": "{}x{}".format(macro.words, macro.data_width)})
        _print_json(data)
    else:
        print("slot:  {}  core {:.1f} x {:.1f} um".format(
            slot.name, slot.core_width, slot.core_height))
        print("macro: {}  {} x {} um  {} words x {} bits".format(
            macro.name, macro.width_um, macro.height_um,
            macro.words, macro.data_width))
        print("fit:   {} macros in {} cols x {} rows".format(
            fit.count, fit.cols, fit.rows))
        print("words: {} ({} bits, {:.2f} KiB)".format(
            fit.total_words, fit.total_bits, fit.total_bytes / 1024.0))
        print("addr:  {} bits = {} sram + {} select".format(
            fit.address_bits, fit.sram_addr_bits, fit.select_bits))
        print("util:  {:.1f}% of core area".format(fit.utilization * 100.0))
    return 0


def cmd_check(args):
    cfg, slot, macro = load_chip_config(args.config)
    fit = calculate_fit(slot, macro, halo_um=cfg.halo_um, count=cfg.count,
                        rows=cfg.rows, cols=cfg.cols, prefer=cfg.prefer,
                        reserved_um2=cfg.reserved_area_um2)
    missing = macro.missing_files()
    problems = []
    for label, path in missing:
        problems.append("missing macro {} artifact: {}".format(label, path))

    if args.json:
        _print_json({
            "config": cfg.to_dict(),
            "slot": slot.name,
            "macro": macro.name,
            "fit": fit.to_dict(),
            "missing_artifacts": [{"kind": k, "path": p} for k, p in missing],
            "ok": not problems,
        })
    else:
        print("config OK: {}".format(args.config))
        print("  chip:  {}".format(cfg.name))
        print("  slot:  {}".format(slot.name))
        print("  macro: {} x {}".format(fit.count, macro.name))
        print("  fit:   {} cols x {} rows, {} words x {} bits, addr {} bits".format(
            fit.cols, fit.rows, fit.total_words, macro.data_width, fit.address_bits))
        print("  util:  {:.1f}%".format(fit.utilization * 100.0))
        if problems:
            print("WARNING:")
            for problem in problems:
                print("  - {}".format(problem))
        else:
            print("  artifacts: all present")
    return 0 if not problems else 1


def cmd_gen(args):
    cfg, slot, macro = load_chip_config(args.config)
    fit = calculate_fit(slot, macro, halo_um=cfg.halo_um, count=cfg.count,
                        rows=cfg.rows, cols=cfg.cols, prefer=cfg.prefer,
                        reserved_um2=cfg.reserved_area_um2)
    only = [item.strip() for item in args.only.split(",") if item.strip()]
    artifacts = generate_all(cfg, slot, macro, fit, args.output, only=only)
    for path in artifacts:
        print("wrote {}".format(path))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="chip_compiler.py",
        description="Assemble OpenRAM/PDK memory macros into a chip project.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list available macros and slots")
    p_list.add_argument("kind", nargs="?", default="all",
                        choices=["macros", "slots", "all"])
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_calc = sub.add_parser("calc", help="calculate macro fit for a slot")
    p_calc.add_argument("--slot", required=True)
    p_calc.add_argument("--macro", required=True)
    p_calc.add_argument("--count", default="auto",
                        help="'auto' or a power-of-two macro count")
    p_calc.add_argument("--rows", type=int)
    p_calc.add_argument("--cols", type=int)
    p_calc.add_argument("--halo", type=float, default=10.0,
                        help="routing halo around each macro (um)")
    p_calc.add_argument("--prefer", default="square",
                        choices=["rows", "columns", "square"])
    p_calc.add_argument("--json", action="store_true")
    p_calc.set_defaults(func=cmd_calc)

    p_check = sub.add_parser("check", help="validate a chip YAML config")
    p_check.add_argument("config")
    p_check.add_argument("--json", action="store_true")
    p_check.set_defaults(func=cmd_check)

    p_gen = sub.add_parser("gen", help="generate a chip project")
    p_gen.add_argument("config")
    p_gen.add_argument("-o", "--output", required=True)
    p_gen.add_argument("--only", default="all",
                       help="comma list of all|verilog|librelane|docs")
    p_gen.set_defaults(func=cmd_gen)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print("configuration error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
