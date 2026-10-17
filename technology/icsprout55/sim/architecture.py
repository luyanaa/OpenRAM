"""Bounded, sampled-cell SRAM timing screening from hash-bound ngspice data.

The four margins are research observations, not characterized sense-amplifier
specifications. Neither topology equivalence nor estimated RCC is signoff.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

# The existing standalone simulation modules use sibling imports.
SIM = Path(__file__).resolve().parent
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))
from check import value_at
from mkscs import read_trace, subckt_pins
from ngspice_pex import build_deck, check_waveforms
from pex_debug import select_probes
from compiler.architecture.contracts import ContractError, fields, number, validate_measurement


MARGINS = {"wen_pulse_ns": ("ns", "write"), "sa_setup_ns": ("ns", "read"),
           "bl_diff_v": ("V", "read"), "write_recovery_ns": ("ns", "write")}
UNMEASURED = ("wl_slew_ns", "bitcell_area_um2", "periphery_area_um2", "effective_bit_area_um2",
              "idle_power_uw", "peak_current_ma", "full_array_read_disturb", "full_array_write_margin",
              "full_array_hold", "vmin", "monte_carlo", "ir_drop", "congestion", "maximum_rc",
              "full_pvt_sweep", "read_energy_pj", "write_energy_pj")


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _digest(path)}


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dependencies(path, found=None):
    """Hash the include closure, including external .lib section references."""
    found = {} if found is None else found
    path = Path(path).resolve()
    if str(path) in found:
        return found
    found[str(path)] = _digest(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\s*\.(?:lib|include|inc)\b", line, re.I):
            continue
        tokens = shlex.split(line, comments=False, posix=True)
        # '.lib section' defines a section; '.lib file section' imports one.
        if tokens[0].lower() == ".lib" and len(tokens) == 2:
            continue
        if len(tokens) < 2:
            raise ValueError("malformed model include in " + str(path))
        child = Path(tokens[1]).expanduser()
        _dependencies(child if child.is_absolute() else path.parent / child, found)
    return found


def _tool_identity(ngspice):
    executable = shutil.which(ngspice)
    if executable is None:
        raise ValueError("ngspice executable is missing: " + ngspice)
    result = subprocess.run([executable, "--version"], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=15, check=True)
    return {"path": str(Path(executable).resolve()), "sha256": _digest(executable),
            "version": result.stdout.strip(), "mode": "execution-enabled"}


def _referenced_paths(value, base):
    """Follow artifact refs, not historical tool-identity metadata."""
    if isinstance(value, dict):
        if set(value) in ({"path", "sha256"}, {"path", "sha256", "size_bytes"}):
            if not isinstance(value["path"], str) or not isinstance(value["sha256"], str):
                raise ValueError("invalid transitive evidence reference")
            path = (base / value["path"]).resolve()
            if _digest(path) != value["sha256"]:
                raise ValueError("transitive evidence changed: " + str(path))
            yield path
        for child in value.values():
            yield from _referenced_paths(child, base)
    elif isinstance(value, list):
        for child in value:
            yield from _referenced_paths(child, base)


def fingerprint(admissions_or_registry, *, ngspice="ngspice", replay_only=True, timeout=3600):
    """Identity of implementation, tool (when executing), and all input bytes."""
    obj = admissions_or_registry
    paths = set()
    if hasattr(obj, "capabilities"):
        for entry in obj.capabilities.values():
            for implementation in entry["implementations"]:
                for model in implementation["models"].values():
                    if model["adapter"] == "ics55-ngspice":
                        paths.update((obj.path.parent / ref["path"]).resolve()
                                     for ref in model["artifacts"].values())
    else:
        admissions = [obj] if isinstance(obj, dict) else obj
        for admission in admissions:
            paths.update(Path(path).resolve() for path in admission["artifacts"].values())
    dependencies, visited = {}, set()
    pending = list(sorted(paths))
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        if path.suffix.lower() in (".lib", ".sp", ".spi", ".inc"):
            _dependencies(path, dependencies)
        else:
            dependencies[str(path)] = _digest(path)
        if path.suffix.lower() == ".json":
            pending.extend(_referenced_paths(_json(path), path.parent))
    sources = [SIM / name for name in ("architecture.py", "check.py", "mkscs.py",
                                       "ngspice_pex.py", "pex_debug.py", "charz.py", "tanalyze.py",
                                       "characterize.py", "mklib.py", "physical_evidence.py")]
    sources.extend(SIM.parents[2] / ("compiler/architecture/" + name)
                   for name in ("contracts.py", "search.py"))
    sources.extend(SIM.parent / "verify" / name for name in
                   ("opensource.py", "gds_pex_rc.py", "gds_pex_geometry.py", "gds_pex_topology.py"))
    tool = {"requested": ngspice, "version": None, "mode": "replay-only"}
    if not replay_only:
        tool = _tool_identity(ngspice)
    return {"adapter": "ics55-ngspice", "sources": [_ref(path) for path in sources],
            "dependencies": dependencies, "tool": tool, "python": sys.version,
            "execution_timeout_s": None if replay_only else number(timeout, "timeout", 0, strict=True)}


def _observation_bindings(paths, bundle):
    bindings = {name: _ref(paths[name]) for name in
                ("gds", "netlist", "trace", "probe_map", "model", "topology")}
    bindings.update(("observation_" + name, _ref(bundle[name]))
                    for name in ("run", "waves", "probes", "init", "deck", "extraction"))
    return bindings


def _validate_model_manifest(bundle, paths):
    manifest_path = bundle.get("models")
    if manifest_path is None:
        raise ValueError("observation lacks an execution-time model-dependency manifest")
    manifest = _json(manifest_path)
    fields(manifest, {"schema", "model_dependencies", "bindings", "execution"},
           label="observation model manifest")
    if manifest["schema"] != "openram-ngspice-observation/1":
        raise ValueError("unsupported observation model manifest")
    if manifest["model_dependencies"] != _dependencies(paths["model"]):
        raise ValueError("observation model-dependency closure differs from current models")
    if manifest["bindings"] != _observation_bindings(paths, bundle):
        raise ValueError("observation model manifest does not bind the exact inputs/outputs")
    execution = manifest["execution"]
    if (not isinstance(execution, dict) or execution.get("model_capture") != "before-launch"
            or execution.get("post_execution_unchanged") is not True
            or not isinstance(execution.get("tool"), dict)
            or not execution["tool"].get("version") or not execution["tool"].get("sha256")):
        raise ValueError("observation has no complete execution-time model/tool identity")
    if type(execution.get("returncode")) is not int or execution["returncode"] != 0:
        raise ValueError("observation simulator launcher did not exit successfully")
    return manifest


def _inputs(admission):
    paths = {name: Path(path).resolve() for name, path in admission["artifacts"].items()}
    for name, path in paths.items():
        if _digest(path) != admission["model"]["artifacts"][name]["sha256"]:
            raise ValueError("registered artifact changed: " + name)
    hashes = {name: _digest(paths[name]) for name in ("gds", "netlist", "trace", "probe_map", "extraction")}
    extraction, topology, mapping = (_json(paths[name]) for name in ("extraction", "topology", "probe_map"))
    for label, item in (("extraction", extraction), ("topology", topology), ("probe map", mapping)):
        if item.get("input_gds_sha256") != hashes["gds"]:
            raise ValueError(label + " GDS identity mismatch")
    for label, item in (("topology", topology), ("probe map", mapping)):
        if item.get("netlist_sha256") != hashes["netlist"]:
            raise ValueError(label + " netlist identity mismatch")
    selected = [item for item in extraction.get("corners", [])
                if item.get("netlist_sha256") == hashes["netlist"]
                and Path(item["netlist"]).resolve() == paths["netlist"]]
    if extraction.get("completed") is not True or len(selected) != 1:
        raise ValueError("extraction does not bind the completed netlist")
    if topology.get("completed") is not True or topology.get("equivalent") is not True or mapping.get("equivalent") is not True:
        raise ValueError("exact equivalent topology is required")
    return paths, hashes, extraction, mapping


def _initial_nodes(plan, policy):
    for record in (plan, *plan.get("initial_conditions", [])):
        if (record.get("initial_charge_c") is not None
                or record.get("charge_status") not in (None, "unknown")
                or record.get("initial_charge_status") not in (None, "unknown")):
            raise ValueError("initial charge must remain unknown; zero voltage is not zero charge")
    if "initial_conditions" in plan:
        items = plan["initial_conditions"]
        if any(item.get("voltage_v") != 0 or item.get("no_MOS_or_port_in_resistor_component") is not True for item in items):
            raise ValueError("legacy initialization is not explicit parasitic-only zero voltage")
        nodes = [item["node"] for item in items]
        actual_policy = "zero-voltage" if nodes else "uninitialized"
    else:
        actual_policy = plan.get("policy")
        nodes = plan.get("floating_nodes", [])
        if actual_policy == "zero-voltage" and plan.get("initial_voltage_v") != 0:
            raise ValueError("initialization has no explicit zero voltage")
    if actual_policy != policy or (policy == "zero-voltage" and not nodes):
        raise ValueError("initialization policy mismatch")
    if len(nodes) != len(set(nodes)) or any(not re.fullmatch(r"[A-Za-z0-9_.$\[\]:/+-]+", node) for node in nodes):
        raise ValueError("invalid initialization node inventory")
    return nodes


def _parasitic_only(netlist, name, nodes):
    """Prove each IC's resistor component has no MOS or top-level terminal."""
    parents = {}
    def root(node):
        node = node.casefold()
        parents.setdefault(node, node)
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node
    forbidden = [pin.casefold() for pin in subckt_pins(netlist, name)] + ["0"]
    present = set()
    with Path(netlist).open(encoding="utf-8") as stream:
        for line in stream:
            t = line.split()
            if not t or t[0][0] in "*.+":
                continue
            kind = t[0][0].upper()
            if kind == "R":
                a, b = root(t[1]), root(t[2])
                parents[a] = b
                present.update((t[1].casefold(), t[2].casefold()))
            elif kind in ("X", "M"):
                if len(t) < 6:
                    raise ValueError("unrecognized MOS record in initialization proof")
                forbidden.extend(node.casefold() for node in t[1:5])
            elif kind == "C":
                present.update((t[1].casefold(), t[2].casefold()))
            else:
                raise ValueError("unsupported device in initialization proof: " + t[0])
    forbidden_roots = {root(node) for node in forbidden}
    if any(node.casefold() not in present or root(node) in forbidden_roots for node in nodes):
        raise ValueError("initialization forces a missing/MOS/storage/port component")


def _canonical_deck(text):
    """Ignore comments/formatting, but no electrical statement or control option."""
    result, initial = [], []
    number = re.compile(r"(?<![\w.])[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?(?:meg|[fpnumkgt])?(?![\w.])", re.I)
    def normalize(match):
        token = match.group().lower()
        suffix = re.search(r"(meg|[fpnumkgt])$", token)
        scales = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
                  "m": 1e-3, "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}
        value = float(token[:suffix.start()]) * scales[suffix.group()] if suffix else float(token)
        return format(value, ".11g")
    for index, line in enumerate(text.splitlines()):
        line = line.strip()
        if index == 0 or not line or line.startswith("*"):
            continue
        line = number.sub(normalize, " ".join(line.lower().split()).replace("'", '"'))
        if line.startswith(".ic "):
            initial.extend(line[4:].split())
        else:
            result.append(line)
    return result, sorted(initial)


def _validate_bundle(bundle, paths, hashes, extraction, mapping, candidate):
    _validate_model_manifest(bundle, paths)
    run, probes, plan = (_json(bundle[name]) for name in ("run", "probes", "init"))
    capture_current = run.get("capture_current", False)
    if type(capture_current) is not bool:
        raise ValueError("observation current-capture mode must be boolean")
    env = candidate["environment"]
    for key in ("corner", "voltage_v", "temperature_c", "period_ns", "cload_ff"):
        if run.get(key) != env[key]:
            raise ValueError("observation environment mismatch: " + key)
    for key in ("netlist", "trace", "probe_map"):
        if key == "probe_map" and key + "_sha256" not in run:
            continue  # Native probes.json carries this binding as well.
        if run.get(key + "_sha256") != hashes[key]:
            raise ValueError("observation input mismatch: " + key)
    if run.get("input_gds_sha256") != hashes["gds"]:
        raise ValueError("observation GDS mismatch")
    if run.get("extraction_summary_sha256") != _digest(bundle["extraction"]):
        raise ValueError("observation extraction mismatch")
    if run.get("model_library_sha256", _digest(paths["model"])) != _digest(paths["model"]):
        raise ValueError("observation model hash mismatch")
    if Path(run.get("model", "")).resolve() != paths["model"]:
        raise ValueError("observation model path mismatch")
    if run.get("deck_sha256", _digest(bundle["deck"])) != _digest(bundle["deck"]):
        raise ValueError("actual executed deck hash mismatch")
    if run.get("initialization_policy", env["initialization"]) != env["initialization"]:
        raise ValueError("observation initialization mismatch")
    for key in ("corner", "voltage_v", "temperature_c", "period_ns", "cload_ff"):
        if key in plan and plan[key] != env[key]:
            raise ValueError("initialization domain mismatch: " + key)
    for key, expected_hash in (("netlist_sha256", hashes["netlist"]),
                               ("trace_sha256", hashes["trace"]),
                               ("input_gds_sha256", hashes["gds"]),
                               ("probe_map_sha256", hashes["probe_map"]),
                               ("extraction_summary_sha256", _digest(bundle["extraction"]))):
        if key in plan and plan[key] != expected_hash:
            raise ValueError("initialization input mismatch: " + key)
    for key, expected in (("input_gds_sha256", hashes["gds"]), ("netlist_sha256", hashes["netlist"]),
                          ("probe_map_sha256", hashes["probe_map"])):
        if probes.get(key) != expected:
            raise ValueError("probe identity mismatch: " + key)
    nodes = _initial_nodes(plan, env["initialization"])
    _parasitic_only(paths["netlist"], extraction["design"], nodes)
    pins = subckt_pins(paths["netlist"], extraction["design"])
    cycles, expected = read_trace(paths["trace"])
    if not probes.get("targets"):
        raise ValueError("observation has no sampled-cell targets")
    legal = {}
    for target in probes["targets"]:
        rc, rp, wc, wp, bit = (target[key] for key in ("read_cycle", "read_port", "write_cycle", "write_port", "bit"))
        read_bits = expected.get(rc, {}).get("dout" + str(rp), "")
        write = cycles.get(wc, {})
        read = cycles.get(rc, {})
        if (not 0 <= bit < len(read_bits) or int(read_bits[-1-bit]) != target["expected"]
                or wc >= rc or write.get("csb" + str(wp)) != "0"
                or write.get("web" + str(wp)) != "0"
                or write.get("addr" + str(wp)) != target["address_bits"]
                or read.get("addr" + str(rp)) != target["address_bits"]
                or int(write["din" + str(wp)][-1-bit]) != target["written"]
                or target["written"] != target["expected"]):
            raise ValueError("sampled target does not match trace operations")
        selected = select_probes(mapping, target, candidate["parameters"]["words_per_row"])
        if any(selected["target"][key] != target[key] for key in ("row", "column")):
            raise ValueError("target physical row/column mismatch")
        legal.update((item["name"], item) for item in selected["signals"])
    for signal in probes["signals"]:
        if signal["node"] in pins:
            continue
        if signal.get("stage") == "floating_metal":
            if signal["node"] not in nodes:
                raise ValueError("unbound floating-metal probe")
            continue
        item = legal.get(signal["name"])
        if item is None or item["node"] != signal["node"]:
            raise ValueError("probe has no exact physical mapping: " + signal["name"])
    internal = [deepcopy(item) for item in probes["signals"] if item["node"] not in pins]
    profile = dict(profile="debug-readwrite", targets=probes["targets"], signals=internal)
    with tempfile.TemporaryDirectory(prefix="openram-deck-binding-") as directory:
        deck, _, outputs, _, generated = build_deck(
            paths["netlist"], paths["trace"], extraction["design"], paths["model"], Path(directory),
            env["period_ns"], env["voltage_v"], env["temperature_c"], env["corner"], env["skew1"],
            env["cload_ff"], env["slew_ns"], profile, env["initialization"],
            {"floating_conductors": [{"nodes": nodes}]}, capture_current=capture_current)
        if _canonical_deck(deck.read_text()) != _canonical_deck(Path(bundle["deck"]).read_text()):
            raise ValueError("executed deck differs from exact candidate inputs/options/initialization")
        if generated["vectors"] != probes["vectors"]:
            raise ValueError("probe vectors differ from executed deck")
        if generated.get("supply_current") != probes.get("supply_current"):
            raise ValueError("typed supply-current identity mismatch")
        generated_signals = {item["name"]: item for item in generated["signals"]}
        for signal in probes["signals"]:
            actual = generated_signals[signal["name"]]
            if any(actual.get(key) != signal.get(key) for key in ("column", "vector", "constant_v")):
                raise ValueError("probe column identity mismatch")
    return probes, outputs, expected


def _waveforms(path, probes):
    count = len(probes["vectors"])
    times, columns = [], [[] for _ in range(count)]
    with Path(path).open(encoding="utf-8") as stream:
        if [word.lower() for word in stream.readline().split()] != ["time"] + [word.lower() for word in probes["vectors"]]:
            raise ValueError("waveform header/vector identity mismatch")
        for line in stream:
            values = [float(word) for word in line.split()]
            if len(values) != count + 1 or not all(math.isfinite(value) for value in values):
                raise ValueError("incomplete/nonfinite waveform")
            time = values[0] * 1e9
            if times and time < times[-1]:
                raise ValueError("nonmonotonic waveform")
            if times and time == times[-1]:
                if values[1:] != [column[-1] for column in columns]:
                    raise ValueError("ambiguous equal-time waveform values")
                continue
            times.append(time)
            for column, value in zip(columns, values[1:]):
                column.append(value)
    if len(times) < 2:
        raise ValueError("waveform has no transient interval")
    return times, columns


def _intervals(times, values, threshold, start, end):
    """Closed PWL superlevel intervals; retain pulses absent at end sample."""
    if start < times[0] or end > times[-1] or end <= start:
        return None
    points = [start] + [time for time in times if start < time < end] + [end]
    result = []
    previous = None
    for a, b in zip(points, points[1:]):
        va, vb = value_at(times, values, a), value_at(times, values, b)
        if va < threshold and vb < threshold:
            continue
        left = a if va >= threshold else a + (threshold-va) * (b-a) / (vb-va)
        right = b if vb >= threshold else a + (threshold-va) * (b-a) / (vb-va)
        if right <= left:
            continue
        if previous is not None and math.isclose(previous[1], left, abs_tol=1e-12):
            previous[1] = right
        else:
            previous = [left, right]
            result.append(previous)
    return result


def _minimum(values):
    return min(values) if values and all(value is not None for value in values) else None


def _aggregate_margins(targets, ports):
    required = {"read": {index for index, role in enumerate(ports) if role in ("R", "RW")},
                "write": {index for index, role in enumerate(ports) if role in ("W", "RW")}}
    sampled = {operation: {item["target"][operation + "_port"] for item in targets}
               for operation in required}
    return {name: (_minimum([item[name] for item in targets])
                   if required[operation] and sampled[operation] == required[operation] else None)
            for name, (_, operation) in MARGINS.items()}


def _target_metrics(times, columns, probes, target, env, threshold, mapping, cycles):
    signals = probes["signals"]
    def wave(signal):
        return ([signal["constant_v"]] * len(times) if signal.get("column") is None
                else columns[signal["column"]])
    def net(name):
        return [signal for signal in signals if signal.get("reference_net", "").upper() == name.upper()]
    def local_gates(prefix, logical):
        record = mapping["nets"].get(logical.upper(), {})
        members = set(record.get("nodes", []))
        return [signal for signal in signals if signal.get("reference_device", "").upper().startswith(prefix.upper() + ".")
                and signal.get("terminal") == "G" and signal["node"] in members]
    def first(signal, start, end, level):
        intervals = _intervals(times, wave(signal), level, start, end)
        if intervals is None:
            return None, "incomplete_window"
        if not intervals:
            return None, "no_activation"
        if intervals[0][0] <= start:
            return None, "already_high"
        return intervals[0][0], "measured"
    period, vdd = env["period_ns"], env["voltage_v"]
    rp, wp, bit = target["read_port"], target["write_port"], target["bit"]
    edge = lambda cycle, port: (cycle + 1 + (env["skew1"] if port else 0)) * period
    rs, ws = edge(target["read_cycle"], rp), edge(target["write_cycle"], wp)
    re_, we = rs + period - 0.01, ws + period - 0.01
    sa_prefix = "BANK0.PORT_DATA{}.SENSE_AMP_ARRAY{}.SA_D{}".format(rp, rp, bit)
    wd_prefix = "BANK0.PORT_DATA{}.WRITE_DRIVER_ARRAY{}.WRITE_DRIVER{}".format(wp, wp, bit)
    sa = local_gates(sa_prefix, "S_EN" + str(rp))
    wen = local_gates(wd_prefix, "W_EN" + str(wp))
    later_writes = [edge(cycle, wp) for cycle, inputs in cycles.items()
                    if cycle > target["write_cycle"] and inputs.get("csb"+str(wp)) == "0"
                    and inputs.get("web"+str(wp)) == "0"]
    gate_end = min([times[-1], ws+2*period] + later_writes)
    pulses, releases = [], []
    pulse_details = []
    for signal in wen:
        intervals = _intervals(times, wave(signal), .8*vdd, ws, gate_end)
        # Multiple/chopped or clipped pulses do not establish a valid width.
        width = (intervals[0][1]-intervals[0][0] if intervals and len(intervals) == 1
                 and intervals[0][0] > ws and intervals[0][1] < gate_end
                 else (0.0 if intervals == [] else None))
        low = _intervals(times, [-value for value in wave(signal)], -.2*vdd, ws, gate_end)
        release = next((a for a, b in (low or []) if intervals and a >= intervals[-1][1]), None)
        pulses.append(width)
        releases.append(release)
        pulse_details.append({"probe": signal["name"], "full_swing_intervals_ns": intervals, "release_ns": release})
    # Earliest later actual clock access on either port is the recovery endpoint.
    accesses = [edge(cycle, int(name[3:])) for cycle, inputs in cycles.items()
                for name, selected in inputs.items() if re.fullmatch(r"csb\d+", name) and selected == "0"
                and edge(cycle, int(name[3:])) > ws + 1e-10]
    next_access = min(accesses) if accesses else None
    recovery = (next_access - max(releases) if next_access is not None and releases
                and all(value is not None for value in releases) else None)
    def bitline(leg):
        logical = "BANK0.{}_{}_{}".format(leg, rp, target["column"])
        members = set(mapping["nets"].get(logical, {}).get("nodes", []))
        local = [signal for signal in signals
                 if signal.get("reference_device", "").upper().startswith(sa_prefix + ".")
                 and signal["node"] in members]
        return net(logical) + local
    bl, br = bitline("BL"), bitline("BR")
    # Q=1 leaves BL high and discharges BR; Q=0 discharges BL.
    sign = 1 if target["expected"] else -1
    differentials = [[sign*(a-b) for a, b in zip(wave(left), wave(right))] for left in bl for right in br]
    activations, setup, diffs = [], [], []
    for signal in sa:
        active = _intervals(times, wave(signal), .5*vdd, rs, re_)
        # Clipped-at-start gates have no observed activation event.
        if active is None or not active or active[0][0] <= rs:
            activations.append(None)
            setup.append(None)
            diffs.append(None)
            continue
        for activation, _ in active:
            activations.append(activation)
            for differential in differentials:
                diffs.append(value_at(times, differential, activation))
                ready = _intervals(times, differential, threshold, rs, re_) if threshold is not None else None
                containing = next((a for a, b in (ready or []) if a <= activation <= b), None)
                future = next((a for a, b in (ready or []) if a > activation), None)
                readiness = containing if containing is not None else future
                setup.append(activation-readiness if readiness is not None else None)
    storage = [signal for signal in signals if signal.get("reference_net", "").upper().endswith(
        ".BITCELL_ARRAY.BIT_R{}_C{}.Q".format(target["row"], target["column"]))]
    completion = []
    for signal in storage:
        values = wave(signal)
        values = values if target["written"] else [vdd-value for value in values]
        settled = _intervals(times, values, .8*vdd, ws, we)
        completion.append(settled[-1][0] - ws if settled and settled[-1][1] >= we else None)
    output_valid, output_status = None, "missing_probe"
    output = [signal for signal in signals if signal["name"] == "dout{}[{}]".format(rp, bit)]
    if len(output) == 1:
        values = wave(output[0])
        values = values if target["expected"] else [vdd-value for value in values]
        settled = _intervals(times, values, .8*vdd, rs, re_)
        if settled and settled[-1][1] >= re_:
            output_status = "already_at_expected_level" if settled[-1][0] == rs else "measured"
            output_valid = settled[-1][0]-rs if output_status == "measured" else None
        else:
            output_status = "not_settled"
    wl_delays, wl_events = {}, {}
    for port, start, end in ((rp, rs, re_), (wp, ws, we)):
        logical_wl = "BANK0.WL_{}_{}".format(port, target["row"])
        storage_prefixes = {signal.get("reference_device", "").rsplit(".", 1)[0]
                            for signal in signals if signal.get("stage") == "storage"
                            and ".BIT_R{}_C{}.".format(target["row"], target["column"])
                            in signal.get("reference_device", "")}
        selected = [signal for prefix in storage_prefixes
                    for signal in local_gates(prefix, logical_wl)]
        events = [dict(zip(("time_ns", "status"), first(signal, start, end, .5*vdd)),
                       probe=signal["name"]) for signal in selected]
        values = [event["time_ns"] for event in events]
        key = str(port) + ("_read" if start == rs else "_write")
        wl_delays[key] = max(values)-start if values and all(value is not None for value in values) else None
        wl_events[key] = events
    controls = {}
    for name, level, direction in (("RBL_BL"+str(rp), .5*vdd, -1),
                                   ("CONTROL{}.RBL_BL_DELAY".format(rp), .5*vdd, 1)):
        values = []
        for signal in net(name):
            intervals = _intervals(times, [direction*x for x in wave(signal)], direction*level, rs, re_)
            values.append(intervals[0][0]-rs if intervals and intervals[0][0] > rs else None)
        controls[name] = max(values) if values and all(value is not None for value in values) else None
    return {"target": target, "write_window_ns": [ws, we], "read_window_ns": [rs, re_],
            "wen_pulse_ns": _minimum(pulses), "sa_setup_ns": _minimum(setup) if differentials else None,
            "bl_diff_v": _minimum(diffs) if differentials else None, "write_recovery_ns": recovery,
            "write_gate_pulses": pulse_details, "sa_activation_ns": activations,
            "next_access_ns": next_access, "storage_completion_ns": (
                max(completion) if completion and all(value is not None for value in completion) else None),
            "selected_wl_delay_ns": wl_delays, "selected_wl_events": wl_events,
            "replica_control_delay_ns": controls,
            "output_valid_delay_ns": output_valid, "output_valid_status": output_status,
            "sa_gate_probes": [signal["name"] for signal in sa],
            "bl_probe_pairs": [[left["name"], right["name"]] for left in bl for right in br]}


def _execute(paths, candidate, out_dir, ngspice, timeout, *, capture_current=False):
    destination = Path(tempfile.mkdtemp(prefix="ngspice-", dir=out_dir))
    env = candidate["environment"]
    extraction = paths.get("execution_extraction", paths["extraction"])
    execution_metadata = _json(extraction)
    selected = [item for item in execution_metadata.get("corners", [])
                if item.get("netlist_sha256") == _digest(paths["netlist"])
                and Path(item.get("netlist", "")).resolve() == paths["netlist"]]
    if (execution_metadata.get("completed") is not True or len(selected) != 1
            or execution_metadata.get("input_gds_sha256") != _digest(paths["gds"])):
        raise ValueError("execution extraction does not bind completed registered GDS/netlist")
    model = paths["model"]
    pdk_root = model.parents[4]
    if (pdk_root / "libs.tech/spice/foundry/ngspice/ICsprout_55LLULP1225_V1p1_hsp.lib").resolve() != model:
        raise ValueError("native launcher model path differs from registered model")
    command = [sys.executable, str(SIM / "ngspice_pex.py"), str(paths["netlist"]), str(paths["trace"]),
               "--out-dir", str(destination), "--pex-summary", str(extraction),
               "--pdk-root", str(pdk_root), "--probe-profile", "debug-readwrite",
               "--probe-map", str(paths["probe_map"]), "--words-per-row", str(candidate["parameters"]["words_per_row"]),
               "--ngspice", ngspice, "--timeout", str(timeout), "--initialization", env["initialization"]]
    if capture_current:
        command.append("--capture-current")
    for key, flag in (("period_ns", "period"), ("corner", "corner"), ("voltage_v", "vdd"),
                      ("temperature_c", "temp"), ("skew1", "skew1"), ("cload_ff", "cload"), ("slew_ns", "tr")):
        command.extend(("--"+flag, str(env[key])))
    before = {"model_dependencies": _dependencies(model), "tool": _tool_identity(ngspice)}
    (destination / "model-dependencies-before.json").write_text(
        json.dumps(before, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout+30, check=False)
    launch_log = destination / "adapter-launch.log"
    launch_log.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise ValueError("simulator launcher exited with status {}; log: {}".format(
            result.returncode, launch_log))
    if (_dependencies(model) != before["model_dependencies"]
            or _digest(before["tool"]["path"]) != before["tool"]["sha256"]):
        raise ValueError("model dependencies or simulator binary changed during execution")
    bundle = {"run": destination / "simulation.json", "waves": destination / "waves.dat",
              "probes": destination / "probes.json", "init": destination / "initialization_plan.json",
              "deck": destination / "transient.sp", "extraction": extraction}
    incomplete = [name for name, path in bundle.items()
                  if not path.is_file() or path.stat().st_size == 0]
    if incomplete:
        raise ValueError("simulator output bundle is incomplete ({}); log: {}".format(
            ", ".join(incomplete), launch_log))
    if _json(bundle["run"]).get("error"):
        raise ValueError("simulator execution unavailable: {}; log: {}".format(
            _json(bundle["run"])["error"], launch_log))
    manifest = {"schema": "openram-ngspice-observation/1",
                "model_dependencies": before["model_dependencies"],
                "bindings": _observation_bindings(paths, bundle),
                "execution": {"model_capture": "before-launch", "post_execution_unchanged": True,
                              "tool": before["tool"], "timeout_s": timeout,
                              "command": command, "returncode": result.returncode}}
    bundle["models"] = destination / "observation-manifest.json"
    bundle["models"].write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return bundle


def evaluate(admission, out_dir, *, execute=False, ngspice="ngspice", timeout=3600, required_margins=None):
    """Replay exact observations, or explicitly authorize a fresh local child."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"measurements": [], "checks": {name: None for name in
              ("functional", "topology", "drc", "lvs", "pex_accepted", "characterization")},
              "artifacts": {}, "estimated": True, "signoff": False,
              "details": {"scope": "sampled cells/ports/cycles only; not full-array stability",
                          "energy_status": "unknown: no isolated-operation supply-current evidence",
                          "characterization_status": "unknown: macro boundary and input/clock loading evidence missing",
                          "constraints": "explicit research thresholds, not characterized SA specifications"}}
    result["details"]["coverage"] = {
        "sampled_cells": [], "ports": [], "cycles": [], "status": "not yet measured",
        "scope": "no full-array stability guarantee",
        "unmeasured": dict.fromkeys(UNMEASURED)}
    try:
        paths, hashes, extraction, mapping = _inputs(admission)
        candidate, env = admission["candidate"], admission["candidate"]["environment"]
        evidence = [_ref(path) for path in paths.values()]
        result["artifacts"] = {name: _ref(path) for name, path in paths.items()}
        from physical_evidence import validate_physical_evidence
        physical = validate_physical_evidence(paths.get("physical"), paths, extraction)
        result["checks"].update(topology=True, **physical["checks"])
        result["estimated"] = physical["estimated"]
        result["artifacts"].update(physical["artifacts"])
        result["details"]["physical_evidence"] = physical["reasons"]
        evidence.extend(physical["artifacts"].values())
        energies = {"read_energy_pj": None, "write_energy_pj": None}
        sample = None
        if "characterization" in paths:
            from characterize import load_characterization
            characterized = load_characterization(paths["characterization"], admission, required_margins)
            result["checks"]["functional"] = characterized["functional"]
            result["details"].update(characterized["details"])
            result["details"]["observation"] = "exact-load-characterization"
            result["details"]["energy_status"] = (
                "signed whole-macro supply energy; includes background clocks and load charging")
            result["artifacts"].update(("characterization_" + name, ref)
                                       for name, ref in characterized["artifacts"].items())
            evidence.extend(characterized["evidence"])
            targets, aggregate = characterized["details"]["targets"], characterized["margins"]
            energies, sample = characterized["energy"], characterized["sample"]
            result["details"]["coverage"]["unmeasured"] = {
                name: None for name in UNMEASURED if name not in energies or energies[name] is None}
        else:
            bundle, rejected = None, []
            for name in sorted(paths):
                if not name.startswith("run__"):
                    continue
                suffix = name[5:]
                proposed = {kind: paths.get(kind+"__"+suffix) for kind in ("run", "waves", "probes", "init", "deck")}
                proposed["extraction"] = paths.get("extraction__" + suffix, paths["extraction"])
                proposed["models"] = paths.get("models__" + suffix)
                if any(path is None for kind, path in proposed.items() if kind != "models"):
                    rejected.append({"observation": suffix, "reason": "incomplete registered observation bundle"})
                    continue
                try:
                    probes, outputs, expected = _validate_bundle(proposed, paths, hashes, extraction, mapping, candidate)
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    rejected.append({"observation": suffix, "reason": str(exc)})
                    continue
                bundle = proposed
                result["details"]["observation"] = suffix
                break
            result["details"]["rejected_observations"] = rejected
            if bundle is None:
                if not execute:
                    raise ValueError("no exact hash-bound observation; local ngspice execution not authorized")
                bundle = _execute(paths, candidate, out_dir, ngspice, timeout)
                actual_extraction = _json(bundle["extraction"])
                if actual_extraction.get("input_gds_sha256") != hashes["gds"] or not any(
                        item.get("netlist_sha256") == hashes["netlist"] for item in actual_extraction.get("corners", [])):
                    raise ValueError("execution extraction does not bind registered GDS/netlist")
                probes, outputs, expected = _validate_bundle(bundle, paths, hashes, extraction, mapping, candidate)
                result["details"]["observation"] = "fresh-local-ngspice"
            result["details"]["observation_model_identity"] = _validate_model_manifest(bundle, paths)["execution"]
            for name, path in bundle.items():
                result["artifacts"]["observation_"+name] = _ref(path)
            evidence.extend(_ref(path) for path in bundle.values())
            times, columns = _waveforms(bundle["waves"], probes)
            checked = check_waveforms(bundle["waves"], outputs, expected, env["period_ns"], env["voltage_v"], env["skew1"], probes)
            result["checks"]["functional"] = checked["passed"]
            result["details"]["functional"] = checked
            threshold = None if required_margins is None else required_margins.get("bl_diff_v")
            cycles, _ = read_trace(paths["trace"])
            targets = [_target_metrics(times, columns, probes, target, env, threshold, mapping, cycles)
                       for target in probes["targets"]]
            aggregate = _aggregate_margins(targets, candidate["access_contract"]["ports"])
            result["details"]["targets"] = targets
            result["details"]["coverage"] = {
                "sampled_cells": [{"row": item["target"]["row"], "column": item["target"]["column"],
                                   "bit": item["target"]["bit"]} for item in targets],
                "ports": sorted({item["target"][key] for item in targets for key in ("read_port", "write_port")}),
                "cycles": [{"write": item["target"]["write_cycle"], "read": item["target"]["read_cycle"]}
                           for item in targets],
                "scope": "sampled physical cells and trace operations, not full-array stability",
                "observed": ["output valid levels/transition delay", "local write-driver EN pulse/release",
                             "local SA tail-gate ordering", "signed BL differential at SA",
                             "selected WL gates", "replica control", "sampled Q completion"],
                "unmeasured": dict.fromkeys(UNMEASURED)}
        def measurement(name, value, unit, operation, port=None):
            record = dict(name=name, value=value, unit=unit, operation=operation, port=port,
                          domain=deepcopy(env), fidelity=env["fidelity"], evidence=evidence)
            result["measurements"].append(validate_measurement(record))
        for name, (unit, operation) in MARGINS.items():
            measurement(name, aggregate[name], unit, operation)
        for index, target in enumerate(targets):
            for name, (unit, operation) in MARGINS.items():
                port = target["target"]["read_port" if operation == "read" else "write_port"]
                measurement("target{}_{}".format(index, name), target[name], unit, operation, port)
        measurement("cycle_time_ns", env["period_ns"], "ns", "macro")
        measurement("read_energy_pj", energies["read_energy_pj"], "pJ", "read")
        measurement("write_energy_pj", energies["write_energy_pj"], "pJ", "write")
        if sample is not None:
            for port, metrics in sample["ports"].items():
                role = candidate["access_contract"]["ports"][int(port)]
                for operation in ("read", "write", "idle"):
                    if (operation == "read" and role not in ("R", "RW")
                            or operation == "write" and role not in ("W", "RW")):
                        continue
                    measurement("port{}_{}_energy_pj".format(port, operation),
                                metrics["e_{}_pj".format(operation)], "pJ", operation, int(port))
        try:
            import klayout.db as kdb
            layout = kdb.Layout()
            layout.read(str(paths["gds"]))
            top = layout.cell(extraction["design"])
            area = top.bbox().area() * layout.dbu**2 if top is not None else None
            result["details"]["area_scope"] = "actual GDS top-cell bounding rectangle, including empty bbox space"
        except ImportError:
            area = None
            result["details"]["area_scope"] = "unknown: klayout.db unavailable"
        measurement("area_um2", area, "um^2", "macro")
    except (ValueError, ContractError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        result["details"]["error"] = str(exc)
        if not result["measurements"] and result["artifacts"]:
            env = admission["candidate"]["environment"]
            for name, (unit, operation) in {**MARGINS, "area_um2": ("um^2", "macro"),
                                          "cycle_time_ns": ("ns", "macro"),
                                          "read_energy_pj": ("pJ", "read"),
                                          "write_energy_pj": ("pJ", "write")}.items():
                result["measurements"].append(validate_measurement(dict(
                    name=name, value=None, unit=unit, operation=operation, port=None,
                    domain=deepcopy(env), fidelity=env["fidelity"],
                    evidence=list(result["artifacts"].values()))))
    return result
