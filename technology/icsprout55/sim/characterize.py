#!/usr/bin/env python3
"""Two-load, exact-domain ngspice numeric characterization (not STA closure).

Source checkout: python technology/icsprout55/sim/characterize.py REGISTRY REQUEST
--out-dir DIR [--execute]. Replay never launches a simulator. The request must
select one implementation/environment; only the two explicitly admitted loads
are varied. No Liberty is emitted implicitly.
"""
from copy import deepcopy
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from compiler.architecture.contracts import (Registry, canonical_bytes, load_json,
                                            number, resolve_artifact, validate_request)
from technology.icsprout55.sim import architecture as native
from technology.icsprout55.sim.charz import measure_port, port_access_cycles, trace_cycles
from technology.icsprout55.sim.mklib import normalize_stdcell_library

SCHEMA = "openram-ngspice-characterization/1"
BUNDLE_KINDS = ("run", "waves", "probes", "init", "deck", "extraction", "models")
MISSING_CONSTRAINTS = ["measured macro setup/hold boundaries", "measured minimum pulse widths",
                       "measured minimum period", "measured input and clock capacitance",
                       "waveform-replayed macro boundary verification",
                       "accepted nonestimated calibrated physical evidence"]


def _domain(candidate):
    env = candidate["environment"]
    if env["skew1"] != 0:
        raise ValueError("characterization requires explicitly zero skew1")
    return dict(period_ns=env["period_ns"], load_ff=env["cload_ff"],
                input_slew_ns=env["slew_ns"], corner=env["corner"],
                vdd=env["voltage_v"], temp=env["temperature_c"])


def _same(left, right):
    return canonical_bytes(left) == canonical_bytes(right)


def aggregate_energy(ports, roles, functional):
    result = {}
    for operation, eligible_roles in (("read", ("R", "RW")), ("write", ("W", "RW"))):
        eligible = [str(port) for port, role in enumerate(roles) if role in eligible_roles]
        values = [ports.get(port, {}).get("e_" + operation + "_pj") for port in eligible]
        result[operation + "_energy_pj"] = (max(values) if functional and values
            and all(value is not None for value in values) else None)
    return result


def _measure(admission, bundle, required_margins):
    paths, hashes, extraction, mapping = native._inputs(admission)
    candidate = admission["candidate"]
    domain = _domain(candidate)
    env = candidate["environment"]
    probes, outputs, expected = native._validate_bundle(
        bundle, paths, hashes, extraction, mapping, candidate)
    current = probes.get("supply_current")
    if not isinstance(current, dict) or current.get("vector") != "i(vdd)" or (
            current.get("unit") != "A" or current.get("positive_direction") != "into_source"):
        raise ValueError("observation lacks typed supply-current capture")
    index = current.get("column")
    if (type(index) is not int or not 0 <= index < len(probes["vectors"])
            or probes["vectors"][index] != "i(vdd)"):
        raise ValueError("supply-current column/vector mismatch")
    times_ns, columns = native._waveforms(bundle["waves"], probes)
    times = [time * 1e-9 for time in times_ns]
    data = {}
    for signal in probes["signals"]:
        data[signal["name"]] = ([signal["constant_v"]] * len(times)
                               if signal.get("column") is None else columns[signal["column"]])
    data["i(vdd)"] = columns[index]
    checked = native.check_waveforms(bundle["waves"], outputs, expected,
                                    env["period_ns"], env["voltage_v"], env["skew1"], probes)
    functional = checked["passed"] is True
    cycles = trace_cycles(paths["trace"])
    roles = candidate["access_contract"]["ports"]
    trace_ports = {int(name[3:]) for values in cycles.values() for name in values
                   if name.startswith("csb") and name[3:].isdigit()}
    if trace_ports != set(range(len(roles))):
        raise ValueError("trace ports differ from exact access contract")
    per_port = {}
    for port, role in enumerate(roles):
        if port_access_cycles(cycles, port)[0] != role:
            raise ValueError("trace role differs from exact access contract")
        per_port[str(port)] = measure_port(times, data, cycles, port, env["period_ns"],
                                           env["voltage_v"], "i(vdd)", skew1=env["skew1"])
    library = normalize_stdcell_library(candidate["parameters"]["stdcell_library"])
    sample = dict(schema_version=3, stdcell_library=library, ports=per_port, domain=domain,
                  functional=functional, signoff=False, complete_sta=False)
    # Raw crossing/energy observations remain diagnostic on a failed read; never objectives.
    if not functional:
        for metrics in per_port.values():
            for key in ("cell_rise", "cell_fall", "rise_transition", "fall_transition", "dout_fall",
                        "dout_tran", "e_read_pj", "e_write_pj", "e_idle_pj"):
                metrics[key] = None
    trace, _ = native.read_trace(paths["trace"])
    threshold = None if required_margins is None else required_margins.get("bl_diff_v")
    targets = [native._target_metrics(times_ns, columns, probes, target, env, threshold, mapping, trace)
               for target in probes["targets"]]
    margins = native._aggregate_margins(targets, roles)
    if not functional:
        margins = dict.fromkeys(margins)
    coverage = {"ports": {str(port): {
        "role": role,
        "both_output_polarities": (all(per_port[str(port)][key] is not None for key in
            ("cell_rise", "cell_fall", "rise_transition", "fall_transition")) if role in ("R", "RW") else None),
        "isolated_reads": len(per_port[str(port)]["raw_energy"]["read"]),
        "isolated_writes": len(per_port[str(port)]["raw_energy"]["write"]),
        "settled_idle_windows": len(per_port[str(port)]["raw_energy"]["idle"])}
        for port, role in enumerate(roles)},
        "scope": "trace operations and sampled physical targets only; no full-array/PVT guarantee"}
    return dict(sample=sample, candidate=deepcopy(candidate), functional=functional,
                energy=aggregate_energy(per_port, roles, functional), margins=margins,
                details=dict(functional=checked, targets=targets, coverage=coverage), signoff=False)


def _registered_bundle(paths, candidate, hashes, extraction, mapping):
    rejected = []
    for name in sorted(paths):
        if not name.startswith("run__"):
            continue
        suffix = name[5:]
        bundle = {kind: paths.get(kind + "__" + suffix) for kind in BUNDLE_KINDS}
        bundle["extraction"] = bundle["extraction"] or paths["extraction"]
        try:
            if any(path is None for path in bundle.values()):
                raise ValueError("incomplete observation bundle")
            probes, _, _ = native._validate_bundle(bundle, paths, hashes, extraction, mapping, candidate)
            if "supply_current" not in probes:
                raise ValueError("no supply-current capture")
            return bundle
        except (ValueError, KeyError, TypeError, OSError) as exc:
            rejected.append(suffix + ": " + str(exc))
    raise ValueError("no exact current-capture observation; execution not authorized; " + "; ".join(rejected))


def _evidence(admission, bundle):
    refs = {"input_" + key: native._ref(admission["artifacts"][key])
            for key in ("gds", "netlist", "trace", "model", "extraction", "topology", "probe_map",
                        "execution_extraction") if key in admission["artifacts"]}
    refs.update({"observation_" + kind: native._ref(path) for kind, path in bundle.items()})
    model_manifest = native._json(bundle["models"])
    refs["simulator_binary"] = {key: model_manifest["execution"]["tool"][key] for key in ("path", "sha256")}
    for index, (path, digest) in enumerate(sorted(model_manifest["model_dependencies"].items())):
        refs["model_dependency_" + str(index)] = {"path": path, "sha256": digest}
    for index, name in enumerate(("characterize.py", "charz.py", "mklib.py", "architecture.py",
                                   "ngspice_pex.py", "check.py", "mkscs.py", "pex_debug.py", "tanalyze.py")):
        refs["source_" + str(index)] = native._ref(Path(__file__).parent / name)
    return refs


def run_characterization(registry, request, out_dir, *, loads_ff=(5, 50), execute=False,
                         ngspice="ngspice", timeout=3600):
    """Admit every load independently, serially execute/replay, retain failures."""
    if type(execute) is not bool:
        raise ValueError("execute must be an explicit boolean")
    number(timeout, "timeout", 0, strict=True)
    request = validate_request(deepcopy(request))
    if request["search"]:
        raise ValueError("characterization request must select exactly one tuple/environment (empty search)")
    if len(loads_ff) != 2:
        raise ValueError("exactly two loads are required")
    loads = sorted(number(load, "load_ff", 0, strict=True) for load in loads_ff)
    if loads[0] == loads[1]:
        raise ValueError("characterization loads must be distinct")
    base = registry.candidate(request["capability_id"], request["parameters"], request["environment"])
    _domain(base)
    admissions = []
    for load in loads:
        candidate = deepcopy(base)
        candidate["environment"]["cload_ff"] = load
        admissions.append(registry.admit(candidate))
    destination = Path(out_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = dict(schema=SCHEMA, base_candidate=base, loads_ff=loads,
                    registry_sha256=registry.digest, observations=[], signoff=False,
                    complete_sta=False, missing_constraints=MISSING_CONSTRAINTS,
                    energy_scope="signed whole-macro supply energy including clocks/load charging; not intrinsic pin power",
                    required_margins=deepcopy(request["required_margins"]))
    samples = {}
    for admission in admissions:
        candidate = admission["candidate"]
        record = dict(candidate=candidate, domain=_domain(candidate), status="unavailable", signoff=False)
        bundle = None
        previous_attempts = set(destination.glob("ngspice-*")) if execute else set()
        try:
            paths, hashes, extraction, mapping = native._inputs(admission)
            # Explicit execution always creates fresh attempts, never silently reuses old data.
            bundle = (native._execute(paths, candidate, destination, ngspice, timeout, capture_current=True)
                      if execute else _registered_bundle(paths, candidate, hashes, extraction, mapping))
            record["bundle"] = {kind: native._ref(path) for kind, path in bundle.items()}
            record["artifacts"] = _evidence(admission, bundle)
            measured = _measure(admission, bundle, request["required_margins"])
            record.update(measured)
            record["status"] = "measured" if measured["functional"] else "functional-failure"
            samples["m5" if candidate["environment"]["cload_ff"] == loads[0] else "m50"] = measured["sample"]
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
            record["error"] = str(exc)
        if execute:
            record["attempt_artifacts"] = {
                str(path.relative_to(destination)): native._ref(path)
                for attempt in sorted(set(destination.glob("ngspice-*")) - previous_attempts)
                for path in sorted(attempt.rglob("*")) if path.is_file()}
        manifest["observations"].append(record)
    if len(samples) == 2:
        env = base["environment"]
        measurements = {env["corner"]: dict(vdd=env["voltage_v"], temp=env["temperature_c"], **samples)}
        sample_path = destination / "measurements.json"
        sample_path.write_text(json.dumps(measurements, indent=2, sort_keys=True, allow_nan=False) + "\n")
        manifest["measurements"] = native._ref(sample_path)
    path = destination / "characterization.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return manifest


def load_characterization(path, admission, required_margins):
    """Validate all hashes and recompute only the EXACT selected-load observation."""
    path = Path(path).expanduser().resolve()
    manifest = load_json(path)
    if manifest.get("schema") != SCHEMA or manifest.get("signoff") is not False or manifest.get("complete_sta") is not False:
        raise ValueError("invalid characterization schema/publication status")
    loads = manifest.get("loads_ff", [])
    if len(loads) != 2 or any(type(value) not in (int, float) for value in loads):
        raise ValueError("invalid characterization loads")
    for load in loads:
        number(load, "load_ff", 0, strict=True)
    if loads[0] >= loads[1]:
        raise ValueError("invalid characterization load order")
    base = deepcopy(manifest["base_candidate"])
    selected = deepcopy(admission["candidate"])
    _domain(selected)
    base["environment"]["cload_ff"] = selected["environment"]["cload_ff"]
    if not _same(base, selected) or selected["environment"]["cload_ff"] not in loads:
        raise ValueError("characterization candidate/domain mismatch")
    observations = manifest.get("observations", [])
    if len(observations) != 2 or sorted(item["candidate"]["environment"]["cload_ff"] for item in observations) != loads:
        raise ValueError("characterization observations do not cover exact loads")
    for item in observations:
        expected = deepcopy(base)
        expected["environment"]["cload_ff"] = item["candidate"]["environment"]["cload_ff"]
        if not _same(item["candidate"], expected) or not _same(item["domain"], _domain(expected)):
            raise ValueError("characterization mixes tuple/environment domains")
        for ref in item.get("artifacts", {}).values():
            resolve_artifact(ref, path.parent)
        for ref in item.get("bundle", {}).values():
            resolve_artifact(ref, path.parent)
        for ref in item.get("attempt_artifacts", {}).values():
            resolve_artifact(ref, path.parent)
    matches = [item for item in observations if _same(item["candidate"], selected)]
    if len(matches) != 1 or matches[0].get("status") not in ("measured", "functional-failure"):
        raise ValueError("selected load has no measured characterization")
    record = matches[0]
    bundle = {kind: resolve_artifact(record["bundle"][kind], path.parent) for kind in BUNDLE_KINDS}
    measured = _measure(admission, bundle, required_margins)
    if not _same(record["sample"], measured["sample"]) or not _same(record["energy"], measured["energy"]):
        raise ValueError("characterization numeric sample differs from exact waveform replay")
    if record["functional"] is not measured["functional"]:
        raise ValueError("characterization functional verdict differs from replay")
    refs = _evidence(admission, bundle)
    recorded_refs = {name: native._ref(resolve_artifact(ref, path.parent))
                     for name, ref in record.get("artifacts", {}).items()}
    if recorded_refs != refs:
        raise ValueError("characterization evidence closure differs from replay")
    refs["characterization"] = native._ref(path)
    if all(item.get("status") in ("measured", "functional-failure") for item in observations) and "measurements" not in manifest:
        raise ValueError("complete numeric observations lack hash-bound mklib sample data")
    if "measurements" in manifest:
        sample_path = resolve_artifact(manifest["measurements"], path.parent)
        corner_data = load_json(sample_path)[selected["environment"]["corner"]]
        for item, label in zip(sorted(observations, key=lambda item: item["domain"]["load_ff"]), ("m5", "m50")):
            if not _same(corner_data[label], item["sample"]):
                raise ValueError("mklib raw sample differs from observation sample")
        refs["measurements"] = manifest["measurements"]
    for index, item in enumerate(observations):
        for group in ("artifacts", "attempt_artifacts", "bundle"):
            for name, ref in item.get(group, {}).items():
                refs["load{}_{}_{}".format(index, group, name)] = native._ref(resolve_artifact(ref, path.parent))
    measured.update(artifacts=refs, evidence=list(refs.values()))
    return measured


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry")
    parser.add_argument("request")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--loads-ff", type=float, nargs=2, default=(5, 50))
    parser.add_argument("--execute", action="store_true", help="authorize fresh serial native simulator attempts")
    parser.add_argument("--ngspice", default="ngspice")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args(argv)
    try:
        result = run_characterization(Registry(args.registry), load_json(args.request), args.out_dir,
                                     loads_ff=args.loads_ff, execute=args.execute,
                                     ngspice=args.ngspice, timeout=args.timeout)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(str(Path(args.out_dir).resolve() / "characterization.json"))
    return 0 if all(item["status"] == "measured" for item in result["observations"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
