#!/usr/bin/env python3
"""Run ICsprout55 KLayout screening and open-source parasitic extraction.

GDS PEX extracts MOS connectivity and metal/via geometry directly from layout.
Normalized routed JSON remains available for interconnect-only RCX. Model
estimates are recorded separately from screening acceptance; neither is signoff.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[3]
DEFAULT_PDK_ROOT = REPO.parent / "icsprout55-pdk"
DEFAULT_LIBRELANE_ROOT = REPO.parent / "librelane"
DEFAULT_DRC_WAIVERS = Path(__file__).with_name("drc_waivers.json")
TOP_INTERCONNECT_SCRIPT = r'''layout = RBA::Layout.new
layout.read($input)
top = layout.cell($top)
raise "top cell not found: #{$top}" if top.nil?
output = RBA::Layout.new
output.dbu = layout.dbu
out_top = output.create_cell(top.name)
layout.layer_indices.each do |layer_index|
  out_layer = output.layer(layout.get_info(layer_index))
  top.shapes(layer_index).each do |shape|
    out_top.shapes(out_layer).insert(shape)
  end
end
output.write($output)
puts "wrote direct top-cell shapes only: #{$output}"
'''


class OpenSourceError(RuntimeError):
    """Raised when an open-source verification run cannot produce a verdict."""


def _resolve_roots(pdk_root=None, librelane_root=None):
    pdk = Path(pdk_root or os.environ.get("ICS55_PDK_ROOT", DEFAULT_PDK_ROOT)).expanduser().resolve()
    librelane = Path(librelane_root or os.environ.get("ICS55_LIBRELANE_ROOT", DEFAULT_LIBRELANE_ROOT)).expanduser().resolve()
    if not pdk.is_dir():
        raise OpenSourceError("ICS55 PDK root does not exist: {}".format(pdk))
    return pdk, librelane


def _run_klayout(args, librelane_root, log_path):
    explicit = os.environ.get("ICS55_KLAYOUT")
    klayout = explicit or shutil.which("klayout")
    if klayout:
        command = [klayout] + list(args)
    else:
        nix_shell = shutil.which("nix-shell")
        if not nix_shell:
            raise OpenSourceError("KLayout is not on PATH and nix-shell is unavailable")
        if not (Path(librelane_root) / "shell.nix").is_file():
            raise OpenSourceError("LibreLane shell.nix not found: {}".format(librelane_root))
        command = [nix_shell, str(librelane_root), "--run", shlex.join(["klayout"] + list(args))]
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, check=False)
    except OSError as exc:
        raise OpenSourceError("cannot start KLayout: {}".format(exc)) from exc
    Path(log_path).write_text(result.stdout, encoding="utf-8", errors="replace")
    return result.returncode, result.stdout


def _load_drc_waivers(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data["version"] != 1:
            raise ValueError("unsupported waiver version")
        waivers = {}
        for entry in data["waivers"]:
            cell, category, reason = (entry[key] for key in ("cell", "category", "reason"))
            values = entry["values"]
            if (not all(isinstance(value, str) and value for value in (cell, category, reason))
                    or not isinstance(values, list) or not values
                    or not all(isinstance(value, str) and value for value in values)):
                raise ValueError("waivers require a cell, category, marker values and reason")
            if any(char in cell + category for char in "*?[]"):
                raise ValueError("waiver cells and categories must be exact names")
            signature = (cell, category, tuple(sorted(values)))
            if signature in waivers:
                raise ValueError("duplicate waiver signature")
            waivers[signature] = reason
        return waivers
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OpenSourceError("invalid DRC waiver file {}: {}".format(path, exc)) from exc


def _parse_drc_report(path, waiver_file=DEFAULT_DRC_WAIVERS):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise OpenSourceError("KLayout DRC report is missing or malformed: {}".format(path)) from exc
    items = root.find(".//items")
    if items is None:
        raise OpenSourceError("KLayout DRC report has no <items> collection: {}".format(path))
    waivers = _load_drc_waivers(waiver_file) if waiver_file is not None else {}
    top = root.findtext("top-cell")
    summary = {"raw": 0, "waived": 0, "unwaived": 0, "categories": {}, "cells": {},
               "waiver_file": str(waiver_file) if waiver_file is not None else None}
    for item in items.findall("item"):
        multiplicity = item.findtext("multiplicity")
        try:
            count = int(multiplicity) if multiplicity is not None else 1
            if count <= 0:
                raise ValueError("multiplicity must be positive")
        except ValueError as exc:
            raise OpenSourceError("invalid DRC item multiplicity in {}".format(path)) from exc
        category = (item.findtext("category") or item.get("category") or "uncategorized").strip("'")
        cell = item.findtext("cell") or "unknown"
        values = tuple(sorted(value.text or "" for value in item.findall("./values/value")))
        waived = bool(top) and cell != top and (cell, category, values) in waivers
        disposition = "waived" if waived else "unwaived"
        summary["raw"] += count
        summary[disposition] += count
        for key, name in (("categories", category), ("cells", cell)):
            counts = summary[key].setdefault(name, {"raw": 0, "waived": 0, "unwaived": 0})
            counts["raw"] += count
            counts[disposition] += count
    return summary


def summarize_drc_report(path, waiver_file=DEFAULT_DRC_WAIVERS, summary_path=None, out=print):
    summary = _parse_drc_report(path, waiver_file)
    if summary_path is not None:
        summary_path = Path(summary_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out("KLayout DRC: {} raw; {} known leaf markers waived; {} unwaived; report {}".format(
        summary["raw"], summary["waived"], summary["unwaived"], path))
    for category, counts in sorted(summary["categories"].items()):
        out("  {}: {} raw, {} waived, {} unwaived".format(
            category, counts["raw"], counts["waived"], counts["unwaived"]))
    return summary


def _run_drc(gds, top, pdk_root, librelane_root, out_dir, mode, waiver_file, profile):
    deck = pdk_root / "libs.tech" / "drc" / "ics55_drc.drc"
    if not deck.is_file():
        raise OpenSourceError("KLayout DRC deck not found: {}".format(deck))
    gds = Path(gds).resolve()
    if not gds.is_file():
        raise OpenSourceError("layout not found: {}".format(gds))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "drc.lyrdb"
    run_gds = gds
    args = ["-b", "-r", str(deck), "-rd", "input={}".format(gds),
            "-rd", "top={}".format(top), "-rd", "report={}".format(report), "-rd", "run_mode=deep", "-z"]
    if profile == "macro":
        # Match the established macro Calibre run: density is checked after
        # chip fill; the open antenna ratio is a provisional diagnostic.
        args.extend(["-rd", "no_density=true", "-rd", "no_antenna=true"])
    if mode == "top-interconnect":
        script = out_dir / "top_interconnect.rb"
        script.write_text(TOP_INTERCONNECT_SCRIPT, encoding="utf-8")
        run_gds = out_dir / "top_interconnect.gds"
        extraction_log = out_dir / "top_interconnect.log"
        rc, _ = _run_klayout(["-b", "-r", str(script), "-rd", "input={}".format(gds),
                                   "-rd", "top={}".format(top), "-rd", "output={}".format(run_gds), "-z"],
                                  librelane_root, extraction_log)
        if rc != 0 or not run_gds.is_file():
            raise OpenSourceError("top-interconnect GDS preparation failed; see {}".format(extraction_log))
        args[args.index("input={}".format(gds))] = "input={}".format(run_gds)
        args.extend(["-rd", "no_feol=true", "-rd", "no_mom=true"])
        if profile != "macro":
            args.extend(["-rd", "no_density=true", "-rd", "no_antenna=true"])
    rc, _ = _run_klayout(args, librelane_root, out_dir / "drc.log")
    if rc != 0:
        raise OpenSourceError("KLayout DRC exited {} (see {})".format(rc, out_dir / "drc.log"))
    return summarize_drc_report(report, waiver_file, out_dir / "drc_summary.json", out=lambda _: None), report


def _run_lvs(gds, top, netlist, pdk_root, librelane_root, out_dir):
    deck = pdk_root / "libs.tech" / "lvs" / "ics55.lvs"
    if not deck.is_file():
        raise OpenSourceError("KLayout LVS deck not found: {}".format(deck))
    gds = Path(gds).resolve()
    netlist = Path(netlist).resolve()
    if not gds.is_file():
        raise OpenSourceError("layout not found: {}".format(gds))
    if not netlist.is_file():
        raise OpenSourceError("schematic netlist not found: {}".format(netlist))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "lvs.lvsdb"
    extracted = out_dir / "extracted.spice"
    args = ["-b", "-r", str(deck), "-rd", "input={}".format(gds),
            "-rd", "top={}".format(top), "-rd", "schematic={}".format(netlist),
            "-rd", "report={}".format(report), "-rd", "target_netlist={}".format(extracted),
            "-rd", "no_well_ties=true", "-z"]
    rc, output = _run_klayout(args, librelane_root, out_dir / "lvs.log")
    if rc != 0:
        raise OpenSourceError("KLayout LVS exited {} (see {})".format(rc, out_dir / "lvs.log"))
    if "CONGRATULATIONS! Netlists match." not in output:
        verdict = "mismatch" if "ERROR: netlists do not match." in output else "no LVS verdict"
        return False, verdict, report
    return True, "matched", report


def verify_one(gds, top, netlist=None, pdk_root=None, librelane_root=None,
               out_dir="opensource", mode="full", run_drc=True, run_lvs=True, out=print,
               drc_waivers=DEFAULT_DRC_WAIVERS, drc_profile="macro"):
    if mode not in ("full", "top-interconnect"):
        raise ValueError("mode must be 'full' or 'top-interconnect'")
    if drc_profile not in ("macro", "diagnostic"):
        raise ValueError("drc_profile must be 'macro' or 'diagnostic'")
    if not run_drc and not run_lvs:
        raise ValueError("at least one of DRC or LVS must be enabled")
    pdk, librelane = _resolve_roots(pdk_root, librelane_root)
    out_dir = Path(out_dir).resolve()
    results = {}
    if run_drc:
        try:
            summary, report = _run_drc(gds, top, pdk, librelane, out_dir / "drc", mode,
                                       drc_waivers, drc_profile)
            results["drc"] = summary["unwaived"] == 0
            label = "full hierarchy" if mode == "full" else "top interconnect only"
            out("KLayout DRC ({}, {} profile): {} raw, {} known leaf markers waived, {} unwaived; report {}".format(
                label, drc_profile, summary["raw"], summary["waived"], summary["unwaived"], report))
            if summary["unwaived"]:
                out("  unwaived categories: {}".format(
                    ", ".join("{}={}".format(k, v["unwaived"])
                              for k, v in sorted(summary["categories"].items()) if v["unwaived"])))
        except OpenSourceError as exc:
            results["drc"] = False
            out("KLayout DRC: ERROR: {}".format(exc))
    if run_lvs:
        if netlist is None:
            raise ValueError("LVS requires a schematic netlist")
        try:
            ok, verdict, report = _run_lvs(gds, top, netlist, pdk, librelane, out_dir / "lvs")
            results["lvs"] = ok
            out("KLayout LVS: {}; report {}".format(verdict, report))
        except OpenSourceError as exc:
            results["lvs"] = False
            out("KLayout LVS: ERROR: {}".format(exc))
    return results


def run_many(items, pdk_root=None, librelane_root=None, out_dir=".", mode="full",
             run_drc=True, run_lvs=True, quiet=False, out=print, drc_waivers=DEFAULT_DRC_WAIVERS,
             drc_profile="macro"):
    results = {}
    for item in items:
        tag = item["tag"]
        emit = (lambda message: None) if quiet else out
        results[tag] = all(verify_one(
            gds=item["gds"], top=item["top"], netlist=item.get("cdl"),
            pdk_root=pdk_root, librelane_root=librelane_root,
            out_dir=Path(out_dir) / tag / "opensource", mode=mode,
            run_drc=run_drc, run_lvs=run_lvs, out=emit, drc_waivers=drc_waivers,
            drc_profile=drc_profile).values())
        if quiet:
            out("{} KLayout {}: {}".format(tag, mode, "PASS" if results[tag] else "FAIL"))
    return results


def run_rcx(input_json, pdk_root=None, out_dir="pex", process_json=None,
            corners=None, require_coupling=False, out=print):
    pdk, _ = _resolve_roots(pdk_root, None)
    rcx = pdk / "libs.tech" / "pex" / "rcx.py"
    lef = pdk / "prtech" / "techLEF" / "N551P6M_ecos.lef"
    source = Path(input_json).expanduser().resolve()
    out_dir = Path(out_dir).resolve()
    if not rcx.is_file():
        raise OpenSourceError("ICS55 routed-layout RCX script not found: {}".format(rcx))
    if not source.is_file():
        raise OpenSourceError("normalized routed-geometry JSON not found: {}".format(source))
    if not lef.is_file():
        raise OpenSourceError("released ICS55 technology LEF not found: {}".format(lef))
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = out_dir / "rcx_summary.json"
    command = [sys.executable, str(rcx), "--input", str(source), "--lef", str(lef),
               "--out-dir", str(out_dir), "--summary", str(summary)]
    if process_json:
        command.extend(["--process-json", str(Path(process_json).expanduser().resolve())])
    for corner in corners or []:
        command.extend(["--corner", corner])
    if require_coupling:
        command.append("--require-coupling")
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    (out_dir / "rcx.log").write_text(result.stdout, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        out("OpenRCX: ERROR (see {})".format(out_dir / "rcx.log"))
        return False
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
        extracted = data["corners"]
        if not extracted:
            raise ValueError("no corner results")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        out("OpenRCX: ERROR: invalid or missing summary: {}".format(exc))
        return False
    for corner in extracted:
        out("OpenRCX {}: {}; SPEF {}".format(corner.get("corner", "?"),
                                               corner.get("status", "unknown"),
                                               corner.get("spef", "?")))
        for warning in corner.get("warnings", []):
            out("  warning: {}".format(warning))
    accepted = all(corner.get("status") == "explicit_coupling" for corner in extracted)
    if not accepted:
        out("OpenRCX: incomplete coupling model; macro PEX acceptance failed")
    return accepted


def run_gds_pex(gds, top=None, pdk_root=None, out_dir="pex", source="lef",
                corners=None, process_json=None, mode="c", out=print):
    """Extract a source-free layout netlist and parasitics, retaining provenance.

    ``completed`` means the artifacts were produced. ``accepted`` requires
    explicit coupling models; estimated/seed-only runs never pass that gate.
    Even an accepted screening run is not foundry signoff.
    """
    try:
        from .gds_pex_geometry import extract_geometry
        from .gds_pex_rc import build_process, extract_parasitics, write_spice
    except ImportError:
        from gds_pex_geometry import extract_geometry
        from gds_pex_rc import build_process, extract_parasitics, write_spice

    pdk, _ = _resolve_roots(pdk_root, None)
    destination = Path(out_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    geometry = extract_geometry(gds, top)
    (destination / "geometry.json").write_text(
        json.dumps(geometry, indent=2) + "\n", encoding="utf-8")
    out("GDS PEX: {} MOS, {} nets, {} ports".format(
        len(geometry["devices"]), len(geometry["nets"]), len(geometry["ports"])))
    results = []
    for corner in dict.fromkeys(corners or ["typ"]):
        process = build_process(pdk, source, corner, process_json)
        parasitics = extract_parasitics(geometry, process, mode)
        tag = "{}_{}".format(geometry["design"], corner)
        if Path(tag).name != tag or tag in (".", ".."):
            raise OpenSourceError("design and corner must be safe artifact names")
        netlist = destination / (tag + ".pex.sp")
        write_spice(geometry, parasitics, netlist)
        (destination / (tag + ".process.json")).write_text(
            json.dumps(process, indent=2) + "\n", encoding="utf-8")
        (destination / (tag + ".parasitics.json")).write_text(
            json.dumps(parasitics, indent=2) + "\n", encoding="utf-8")
        result = {key: value for key, value in parasitics.items()
                  if key not in ("capacitors", "resistors", "terminal_nodes", "port_nodes")}
        result.update(corner=corner, netlist=str(netlist),
                      netlist_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest(),
                      accepted=parasitics["status"] == "explicit_coupling", signoff=False)
        results.append(result)
        out("GDS PEX {}: {}; netlist {}".format(corner, parasitics["status"], netlist))
    summary = dict(schema_version=1, kind="ics55_gds_pex", design=geometry["design"],
                   input_gds_sha256=geometry["input_gds_sha256"], completed=True,
                   accepted=all(result["accepted"] for result in results), signoff=False,
                   geometry=geometry["summary"], corners=results,
                   seconds=round(time.monotonic() - start, 3))
    (destination / "pex_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if not summary["accepted"]:
        out("GDS PEX: artifacts completed; model is insufficient for macro PEX acceptance")
    return summary


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="run KLayout DRC/LVS on a macro GDS")
    verify.add_argument("gds")
    verify.add_argument("top")
    verify.add_argument("--netlist")
    verify.add_argument("--pdk-root")
    verify.add_argument("--librelane-root")
    verify.add_argument("--out-dir", default="opensource_run")
    verify.add_argument("--mode", choices=("full", "top-interconnect"), default="full")
    verify.add_argument("--no-drc", action="store_true")
    verify.add_argument("--no-lvs", action="store_true")
    verify.add_argument("--drc-waivers", default=str(DEFAULT_DRC_WAIVERS), help="exact known leaf marker waiver JSON")
    verify.add_argument("--drc-profile", choices=("macro", "diagnostic"), default="macro",
                        help="macro excludes chip-fill density and provisional antenna; diagnostic includes them")
    report = subparsers.add_parser("drc-report", help="classify an existing DRC report without rerunning KLayout")
    report.add_argument("report")
    report.add_argument("--drc-waivers", default=str(DEFAULT_DRC_WAIVERS))
    report.add_argument("--summary", help="write raw/waived/unwaived counts as JSON")
    pex = subparsers.add_parser("pex", help="run clean-room RCX from normalized routed geometry JSON")
    pex.add_argument("input_json")
    pex.add_argument("--pdk-root")
    pex.add_argument("--out-dir", default="pex_run")
    pex.add_argument("--process-json")
    pex.add_argument("--corner", action="append")
    pex.add_argument("--require-coupling", action="store_true")
    gds_pex = subparsers.add_parser("gds-pex", help="extract transistor and RC netlists from actual GDS")
    gds_pex.add_argument("gds")
    gds_pex.add_argument("--top")
    gds_pex.add_argument("--pdk-root")
    gds_pex.add_argument("--out-dir", default="gds_pex_run")
    gds_pex.add_argument("--source", choices=("lef", "starrc", "pdk-estimate"), default="lef")
    gds_pex.add_argument("--corner", action="append")
    gds_pex.add_argument("--process-json")
    gds_pex.add_argument("--mode", choices=("c", "rcc"), default="c")
    args = parser.parse_args(argv)
    if args.command == "drc-report":
        try:
            summary = summarize_drc_report(args.report, args.drc_waivers, args.summary)
        except OpenSourceError as exc:
            parser.error(str(exc))
        return 0 if summary["unwaived"] == 0 else 1
    if args.command == "verify":
        run_lvs = not args.no_lvs
        if run_lvs and not args.netlist:
            parser.error("verify requires --netlist unless --no-lvs is set")
        if args.no_drc and args.no_lvs:
            parser.error("enable at least one of DRC or LVS")
        results = verify_one(args.gds, args.top, args.netlist, args.pdk_root,
                             args.librelane_root, args.out_dir, args.mode,
                             not args.no_drc, run_lvs, drc_waivers=args.drc_waivers,
                             drc_profile=args.drc_profile)
        return 0 if all(results.values()) else 1
    if args.command == "gds-pex":
        try:
            summary = run_gds_pex(args.gds, args.top, args.pdk_root, args.out_dir,
                                  args.source, args.corner, args.process_json, args.mode)
        except (OpenSourceError, OSError, ValueError) as exc:
            parser.error(str(exc))
        return 0 if summary["accepted"] else 1
    ok = run_rcx(args.input_json, args.pdk_root, args.out_dir,
                 args.process_json, args.corner, args.require_coupling)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main())
