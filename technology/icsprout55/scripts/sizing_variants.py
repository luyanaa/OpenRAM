#!/usr/bin/env python3
"""Materialize the three fixed 32x8 SC9T/2RW sizing experiments.

Generation is opt-in and uses one fresh macro.py --no-check subprocess per
variant, serially. A generated manifest is not electrical or physical evidence.
The compiler options are immutable within each child; changing OPTS across a
complete cached hierarchy in one process is deliberately unsupported.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[3]
SCHEMA = "openram-ics55-sizing-variants/1"
VARIANTS = ((9, 4, 1.0), (9, 4, 0.75), (9, 3, 1.0))
BASELINE = REPO / "technology/icsprout55/configs/ics55_2rw_32x8.py"
MACRO = REPO / "technology/icsprout55/verify/macro.py"
FIXED_OPTIONS = {
    "tech_name": "icsprout55", "bitcell": "pbitcell",
    "analytical_delay": True, "nominal_corner_only": True,
    "check_lvsdrc": False, "inline_lvsdrc": False, "use_nix": False,
    "netlist_only": False, "word_size": 8, "num_words": 32,
    "words_per_row": 1, "num_banks": 1, "num_spare_rows": 0,
    "num_spare_cols": 0, "write_size": 8, "local_array_size": 0,
    "stdcell_library": "SC9T", "num_rw_ports": 2,
    "num_r_ports": 0, "num_w_ports": 0, "ics55_column_mux_style": "nmos",
    "control_logic": "control_logic", "decoder": "hierarchical_decoder",
    "replica_bitline": "replica_bitline", "delay_chain": "delay_chain",
    "rbl_delay_percentage": 0.5,
    "route_supplies": True, "perimeter_pins": True,
}
SOURCE_FILES = (
    "sram_compiler.py", "compiler/options.py", "compiler/sram_factory.py",
    "compiler/modules/port_data.py", "compiler/modules/write_driver_array.py",
    "compiler/modules/write_mask_and_array.py", "compiler/modules/delay_chain.py",
    "compiler/modules/control_logic.py", "technology/icsprout55/tech/tech.py",
    "technology/icsprout55/custom/ics55_gen.py",
    "technology/icsprout55/custom/ics55_write_driver.py",
    "technology/icsprout55/verify/macro.py",
    "technology/icsprout55/scripts/sizing_variants.py",
)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _artifact(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _digest(path.read_bytes()),
            "size_bytes": path.stat().st_size}


def _sources():
    return {name: _artifact(REPO / name) for name in SOURCE_FILES}


def variant_name(stages, fanout, strength):
    if (type(stages) is not int or type(fanout) is not int
            or isinstance(strength, bool) or not isinstance(strength, (int, float))
            or (stages, fanout, strength) not in VARIANTS):
        raise ValueError("unsupported sizing tuple")
    return "ics55_2rw_32x8_d{}_f{}_w{}".format(
        stages, fanout, "075" if strength == 0.75 else "100")


def _config(options):
    return "".join("{} = {!r}\n".format(key, value) for key, value in options.items())


def _save(path, manifest):
    # Only callers that have established ownership may update this file.
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("x") as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def materialize(out_dir):
    """Return an owned, hash-bound manifest; conflicting files fail closed."""
    root = Path(out_dir).resolve()
    manifest_path = root / "sizing_variants.json"
    sources = _sources()
    baseline = _artifact(BASELINE)
    records = []
    contents = {}
    for stages, fanout, strength in VARIANTS:
        name = variant_name(stages, fanout, strength)
        config_path = root / "configs" / (name + ".py")
        output_dir = root / "work" / "runs" / ("out_" + name)
        options = dict(FIXED_OPTIONS, delay_chain_stages=stages,
                       delay_chain_fanout_per_stage=fanout,
                       write_driver_strength=strength,
                       output_name=name, output_path=str(output_dir) + os.sep)
        content = _config(options)
        contents[config_path] = content
        records.append({
            "name": name,
            "parameters": {"delay_chain_stages": stages,
                           "delay_chain_fanout": fanout,
                           "write_driver_strength": strength},
            "options": options,
            "strength_scope": "eight output MOS only; four local inversion MOS fixed",
            "output_width_um": 0.45 if strength == 0.75 else 0.60,
            "inverter_width_um": 0.60,
            "config": {"path": str(config_path), "sha256": _digest(content.encode()),
                       "size_bytes": len(content.encode())},
            "output_dir": str(output_dir), "status": "materialized",
            "command": [sys.executable, str(MACRO), str(config_path), "--no-check"],
            "cwd": str(REPO), "environment": {"ICS55_WORK": str(root / "work")},
            "generation": None, "electrical_pass": None, "physical_pass": None,
            "signoff": False,
        })
    manifest = {"schema": SCHEMA, "root": str(root), "baseline_config": baseline,
                "interpreter": _artifact(sys.executable),
                "source_scope": "selected sizing/generator control files, not a complete source closure",
                "sources": sources, "option_mapping": {
                    "delay_chain_fanout": "delay_chain_fanout_per_stage"},
                "variants": records, "signoff": False}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        for key in ("schema", "root", "baseline_config", "interpreter",
                    "source_scope", "sources", "option_mapping"):
            if previous.get(key) != manifest[key]:
                raise ValueError("existing manifest conflicts at {}".format(key))
        old_records = previous.get("variants", [])
        if len(old_records) != len(records):
            raise ValueError("existing manifest has conflicting variant set")
        for old, new in zip(old_records, records):
            for key, value in new.items():
                if key not in ("status", "generation") and old.get(key) != value:
                    raise ValueError("existing variant conflicts at {}".format(key))
            if old.get("status") not in ("materialized", "generated", "failed"):
                raise ValueError("invalid existing generation status")
        for path, content in contents.items():
            if not path.is_file() or path.read_text() != content:
                raise ValueError("preexisting config conflicts: {}".format(path))
        return manifest_path, previous
    if root.exists() and any(root.iterdir()):
        raise ValueError("destination is not empty and has no owned sizing manifest: {}".format(root))
    root.mkdir(parents=True, exist_ok=True)
    (root / "configs").mkdir()
    for path, content in contents.items():
        with path.open("x") as stream:
            stream.write(content)
    _save(manifest_path, manifest)
    return manifest_path, manifest


def generate(manifest_path, manifest):
    """Run actual generation serially, preserving failures and partial outputs."""
    manifest_path = Path(manifest_path).resolve()
    # Preflight every destination before starting any child. Never reuse stale
    # macro outputs, even if a prior failed run happened to emit usable files.
    for variant in manifest["variants"]:
        if variant["status"] == "generated":
            for artifact in variant["generation"]["outputs"].values():
                if _artifact(artifact["path"]) != artifact:
                    raise ValueError("generated artifact changed: {}".format(artifact["path"]))
            continue
        if variant["status"] != "materialized":
            raise ValueError("failed variant requires a new empty destination")
        if Path(variant["output_dir"]).exists():
            raise ValueError("refusing preexisting generator output: {}".format(variant["output_dir"]))
        if (manifest_path.parent / (variant["name"] + ".launcher.log")).exists():
            raise ValueError("refusing preexisting launcher log")
    for variant in manifest["variants"]:
        if variant["status"] == "generated":
            continue
        if _sources() != manifest["sources"]:
            raise ValueError("generator inputs changed after materialization")
        if _artifact(variant["config"]["path"]) != variant["config"]:
            raise ValueError("materialized config changed")
        log_path = manifest_path.parent / (variant["name"] + ".launcher.log")
        run = {"command": variant["command"], "cwd": variant["cwd"],
               "environment": variant["environment"], "returncode": None,
               "outputs": {}, "error": None}
        try:
            env = dict(os.environ, **variant["environment"])
            with log_path.open("x") as log:
                result = subprocess.run(variant["command"], cwd=variant["cwd"], env=env,
                                        stdout=log, stderr=subprocess.STDOUT, check=False)
            run["returncode"] = result.returncode
            output = Path(variant["output_dir"])
            name = variant["name"]
            expected = {"config": output / (name + "_cfg.py"),
                        "gds": output / (name + ".gds"),
                        "lvs_spice": output / (name + ".lvs.sp"),
                        "spice": output / (name + ".sp"),
                        "compile_log": output / "compile.log"}
            for key, path in expected.items():
                if path.is_file():
                    run["outputs"][key] = _artifact(path)
            run["compiler_command"] = [sys.executable, str(REPO / "sram_compiler.py"),
                                       "-v", str(expected["config"])]
            run["compiler_environment"] = {
                "OPENRAM_HOME": str(REPO / "compiler"),
                "OPENRAM_TECH": str(REPO / "technology"),
                "PYTHONPATH": str(Path(variant["environment"]["ICS55_WORK"]) / "pkg"),
                "ICS55_WORK": variant["environment"]["ICS55_WORK"],
            }
            missing = [key for key in expected if key not in run["outputs"]
                       or run["outputs"][key]["size_bytes"] == 0]
            if result.returncode != 0:
                run["error"] = "generator returned {}".format(result.returncode)
            elif missing:
                run["error"] = "missing/empty generator outputs: " + ", ".join(missing)
            elif _sources() != manifest["sources"]:
                run["error"] = "generator sources changed during execution"
            else:
                copied = expected["config"].read_text()
                original = Path(variant["config"]["path"]).read_text()
                suffix = '\noutput_path = "{}/"\noutput_name = "{}"\n'.format(output, name)
                if copied != original + suffix:
                    run["error"] = "actual compiler config differs from planned input"
        except (OSError, subprocess.SubprocessError) as exc:
            run["error"] = str(exc)
        if log_path.is_file():
            run["outputs"]["launcher_log"] = _artifact(log_path)
        variant["generation"] = run
        variant["status"] = "failed" if run["error"] else "generated"
        _save(manifest_path, manifest)
    return all(item["status"] == "generated" for item in manifest["variants"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="new empty experiment destination")
    parser.add_argument("--generate", action="store_true",
                        help="run real serial generation (no DRC/LVS, extraction or simulation)")
    args = parser.parse_args(argv)
    try:
        path, manifest = materialize(args.out_dir)
        ok = generate(path, manifest) if args.generate else True
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, "sizing variants: {}\n".format(exc))
    print(path)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
