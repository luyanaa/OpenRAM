"""Input-bound research physical acceptance; never foundry signoff.

The compact ``ics55-physical-evidence/1`` manifest contains ``top``,
``signoff:false``, ``bindings`` (gds/netlist/reference/extraction hash refs),
``drc`` and ``lvs`` hash refs to fresh verifier stage records, and ``pex``:
``{corner, geometry:<ref>, process:<ref>, parasitics:<ref>, calibration:<ref>}``.
Every ref is ``{path,sha256}``; relative paths are relative to their containing
JSON file. All transitively referenced files are returned as flat artifacts.
Missing historical runtime identity is deliberately not reconstructed.

Calibration schema ``ics55-rc-calibration/1`` binds those same inputs plus
geometry/process/parasitics, an independent RC corner, disclosed idealizations,
and quantitative observations. Each observation is a hash ref to
``ics55-rc-observation/1``: kind, layers, rc_corner, length_unit='um', unit,
positive value, structure:<native geometry ref>, method ('measurement' or
'independent-field-solver'), and nonempty sources:[<independent data refs>].
At least one source is a dimensional CSV with the exact columns id, kind,
layers (colon-separated), rc_corner, length_unit, unit, value,
structure_sha256; ``source_observation`` selects its unique row. Additional
original reports/tool records may be referenced alongside that source. A
ratios-only calibration file is not a dimensional observation source.
Kinds are resistance, via_resistance, ground_capacitance,
lateral_capacitance, cross_capacitance. ``limits`` maps each kind to a finite
relative-error limit in [0,1). Predictions are recomputed with the actual PEX
implementation, not trusted from claimed pass/error fields. Resistance coupons
must be two-terminal unbranched chains so their summed R is measurable end-to-end
resistance, not an arbitrary sum over a branched mesh. The isolated test fixture
is an example only, not production calibration or physical evidence.
"""
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET

VERIFY = Path(__file__).resolve().parents[1] / "verify"
if str(VERIFY) not in sys.path:
    sys.path.insert(0, str(VERIFY))
import opensource


SCHEMA = "ics55-physical-evidence/1"
CALIBRATION_SCHEMA = "ics55-rc-calibration/1"
OBSERVATION_SCHEMA = "ics55-rc-observation/1"
KINDS = {"resistance": "ohm", "via_resistance": "ohm",
         "ground_capacitance": "pF", "lateral_capacitance": "pF",
         "cross_capacitance": "pF"}


class EvidenceError(ValueError):
    """Evidence is absent, stale, incompatible, or insufficient."""


def _require(condition, message):
    if not condition:
        raise EvidenceError(message)


def _number(value, label, *, positive=False):
    _require(type(value) in (int, float) and math.isfinite(value)
             and (not positive or value > 0), label + " must be finite" + (" and positive" if positive else ""))
    return value


class _References:
    def __init__(self):
        self.artifacts = {}
        self.visited = set()

    def ref(self, value, base):
        _require(isinstance(value, dict) and isinstance(value.get("path"), str)
                 and value["path"] and isinstance(value.get("sha256"), str)
                 and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]), "invalid hash reference")
        path = (base / value["path"]).expanduser().resolve()
        _require(path.is_file(), "referenced file missing: " + str(path))
        actual = opensource._artifact_ref(path)
        key = "physical__" + hashlib.sha256(str(path).encode()).hexdigest()
        self.artifacts[key] = actual
        _require(actual["sha256"] == value["sha256"], "referenced file changed: " + str(path))
        return path

    def document(self, value, base, *, transitive=True):
        path = self.ref(value, base)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise EvidenceError("invalid JSON evidence: " + str(path)) from exc
        _require(isinstance(data, dict), "evidence must be a JSON object: " + str(path))
        if transitive:
            self.walk(data, path.parent)
        return data, path.parent

    def walk(self, value, base):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                path = self.ref(value, base)
                if path.suffix.lower() == ".json" and path not in self.visited:
                    self.visited.add(path)
                    self.document(value, base)
            else:
                for item in value.values():
                    self.walk(item, base)
        elif isinstance(value, list):
            for item in value:
                self.walk(item, base)

    def exact(self, value, base, expected, label):
        path = self.ref(value, base)
        _require(path == Path(expected).resolve(), label + " path differs from the registered input")
        return path


def _schema(record, schema):
    _require(record.get("schema") == schema and record.get("signoff") is False,
             "unsupported evidence schema or signoff claim: " + schema)


def _captured_sources(runtime, base, refs):
    sources = runtime.get("sources")
    _require(isinstance(sources, list) and sources, "missing captured source closure")
    return {refs.ref(item, base) for item in sources}


def _runtime(stage, base, refs, paths, top, kind):
    runtime, runtime_base = refs.document(stage.get("runtime"), base)
    _schema(runtime, "ics55-klayout-runtime/1")
    _require(type(runtime.get("returncode")) is int and runtime["returncode"] == 0,
             kind + " tool did not complete successfully")
    _require(runtime.get("inputs_unchanged") is True, kind + " inputs changed during execution")
    start, end = runtime.get("started_ns"), runtime.get("ended_ns")
    _require(type(start) is int and type(end) is int and 0 < start <= end,
             kind + " has no captured execution interval")
    refs.exact(runtime.get("producer"), runtime_base, opensource.__file__, "verification producer")
    tool = runtime.get("tool", {})
    selection, args = tool.get("selection"), runtime.get("args")
    _require(isinstance(selection, list) and len(selection) == 2 and selection[0] == "executable"
             and isinstance(selection[1], str), "actual KLayout binary identity was not captured")
    executable = refs.ref(tool.get("executable"), runtime_base)
    selected = shutil.which(selection[1]) or selection[1]
    _require(executable == Path(selected).resolve(), "KLayout executable differs from captured command")
    _require(isinstance(tool.get("version"), str)
             and re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?", tool["version"]),
             "missing actual KLayout version")
    _require(isinstance(args, list) and all(isinstance(item, str) for item in args)
             and runtime.get("command") == opensource._klayout_command(selection, args),
             "runtime command/arguments differ")
    parameters = {}
    for index, item in enumerate(args):
        if item == "-rd":
            _require(index + 1 < len(args) and "=" in args[index + 1], "invalid runtime parameter")
            key, value = args[index + 1].split("=", 1)
            _require(key not in parameters, "duplicate runtime parameter: " + key)
            parameters[key] = value
    _require(runtime.get("parameters") == parameters, "runtime parameters differ from command")
    _require(parameters.get("top") == top, kind + " top differs")
    inputs = runtime.get("inputs", {})
    refs.exact(inputs.get("input"), runtime_base, paths["gds"], kind + " GDS")
    _require(parameters.get("input") == str(Path(paths["gds"]).resolve()), kind + " command GDS differs")
    if kind == "LVS":
        refs.exact(inputs.get("schematic"), runtime_base, paths["reference"], "LVS reference")
        _require(parameters.get("schematic") == str(Path(paths["reference"]).resolve()), "LVS command reference differs")
    deck = refs.ref(runtime.get("deck"), runtime_base)
    _require(args.count("-r") == 1 and args[args.index("-r") + 1] == str(deck), "runtime deck differs")
    _require(deck.name == ("ics55_drc.drc" if kind == "DRC" else "ics55.lvs"), "unexpected verification deck")
    sources = _captured_sources(runtime, runtime_base, refs)
    required_sources = {Path(item["path"]) for item in opensource._deck_sources(deck)}
    if kind == "LVS":
        required_sources.update(Path(item["path"]) for item in opensource._spice_sources(paths["reference"]))
    _require(required_sources == sources, kind + " captured deck/input closure differs")
    report = refs.ref(stage.get("report"), base)
    output = runtime.get("outputs", {}).get("report", {})
    _require(output.get("fresh") is True, kind + " report was not freshly produced")
    refs.exact(output, runtime_base, report, kind + " report")
    _require(parameters.get("report") == str(report), kind + " command report differs")
    _require(report.stat().st_size > 0, kind + " report is empty")
    log = refs.ref(runtime.get("log"), runtime_base)
    text = log.read_text(encoding="utf-8")
    errors = [line.strip() for line in text.splitlines()
              if re.match(r"^\s*(?:ERROR|FATAL)\s*:", line, re.IGNORECASE)]
    _require(all(kind == "LVS" and line == "ERROR: netlists do not match." for line in errors),
             kind + " log contains a tool error, not a completed physical verdict")
    return runtime, runtime_base, parameters, deck, report, text


def _drc(value, base, refs, paths, top):
    stage, base = refs.document(value, base)
    _schema(stage, "ics55-drc-evidence/1")
    _require(stage.get("top") == top and stage.get("mode") == "full", "DRC is not full hierarchy for this top")
    profile = stage.get("profile")
    _require(profile in ("macro", "diagnostic"), "DRC profile is not explicit")
    runtime, _, parameters, deck, report, _ = _runtime(stage, base, refs, paths, top, "DRC")
    expected = {"input": str(Path(paths["gds"]).resolve()), "top": top,
                "report": str(report), "run_mode": "deep"}
    if profile == "macro":
        expected.update(no_density="true", no_antenna="true")
    _require(parameters == expected, "DRC command disables rules or changes its declared profile")
    args = ["-b", "-r", str(deck), "-rd", "input=" + expected["input"], "-rd", "top=" + top,
            "-rd", "report=" + str(report), "-rd", "run_mode=deep", "-z"]
    if profile == "macro":
        args.extend(["-rd", "no_density=true", "-rd", "no_antenna=true"])
    _require(runtime["args"] == args, "DRC command differs from the verifier producer")
    _require(ET.parse(report).getroot().findtext("top-cell") == top, "DRC report top differs")
    _require("waivers" in stage, "DRC has no captured waiver policy")
    waivers = refs.ref(stage["waivers"], base) if stage.get("waivers") is not None else None
    actual = opensource._parse_drc_report(report, waivers)
    summary, _ = refs.document(stage.get("summary"), base)
    _require(summary == actual, "DRC summary differs from actual report/waivers")
    return actual["unwaived"] == 0


def _lvs(value, base, refs, paths, top):
    stage, base = refs.document(value, base)
    _schema(stage, "ics55-lvs-evidence/1")
    _require(stage.get("top") == top and stage.get("profile") == "openram-macro",
             "LVS is not strict openram-macro for this top")
    mode = stage.get("mode")
    _require(mode in ("flat", "hierarchical"), "unsupported strict LVS mode")
    runtime, runtime_base, parameters, deck, report, output = _runtime(stage, base, refs, paths, top, "LVS")
    expected = {"input": str(Path(paths["gds"]).resolve()), "top": top,
                "schematic": str(Path(paths["reference"]).resolve()), "report": str(report),
                "target_netlist": parameters.get("target_netlist"),
                "no_well_ties": "true", "strict_topology": "true"}
    if mode == "hierarchical":
        expected["strict_hierarchy"] = "true"
    _require(parameters == expected, "LVS settings are not the exact strict comparison")
    args = ["-b", "-r", str(deck), "-rd", "input=" + expected["input"], "-rd", "top=" + top,
            "-rd", "schematic=" + expected["schematic"], "-rd", "report=" + str(report),
            "-rd", "target_netlist=" + str(expected["target_netlist"])]
    if mode == "hierarchical":
        args.extend(["-rd", "strict_hierarchy=true"])
    args.extend(["-rd", "no_well_ties=true", "-rd", "strict_topology=true"])
    _require(runtime["args"] == args, "LVS command differs from the verifier producer")
    extracted = runtime.get("outputs", {}).get("target_netlist", {})
    _require(extracted.get("fresh") is True, "LVS extracted netlist was not freshly produced")
    extracted_path = refs.ref(extracted, runtime_base)
    _require(str(extracted_path) == expected["target_netlist"] and extracted_path.stat().st_size > 0,
             "LVS extracted netlist differs")
    actual_capability = opensource._validate_strict_capability(deck.parents[2])
    capability, _ = refs.document(stage.get("capability"), base)
    _require(capability == actual_capability, "LVS capability differs from validated deck closure")
    _require(runtime["tool"]["version"] == capability["klayout_version"], "LVS tool version differs from strict capability")
    _require("ICS55_LVS_COMPARE=INCONCLUSIVE" not in output, "LVS comparison is inconclusive")
    if "ERROR: netlists do not match." in output:
        _require(stage.get("verdict") == "mismatch", "LVS recorded verdict differs from report")
        return False
    _require("CONGRATULATIONS! Netlists match." in output and stage.get("verdict") == "matched",
             "LVS has no genuine matched verdict")
    return True


def _used_layers(geometry):
    return {item["layer"] for net in geometry.get("nets", []) for item in net.get("rectangles", [])}


def _used_vias(geometry):
    return {item["layer"] for net in geometry.get("nets", []) for item in net.get("vias", [])}


def _chain_resistance(parasitics):
    edges, degrees = {}, {}
    for resistor in parasitics["resistors"]:
        left, right = resistor["node1"], resistor["node2"]
        pair = tuple(sorted((left, right)))
        _require(left != right and pair not in edges, "resistance coupon is not an unbranched chain")
        edges[pair] = _number(resistor["value_ohm"], "coupon R", positive=True)
        for node in pair:
            degrees[node] = degrees.get(node, 0) + 1
    endpoints = {node for node, degree in degrees.items() if degree == 1}
    ports = set(parasitics["port_nodes"].values())
    _require(len(ports) == 2 and endpoints == ports and all(value <= 2 for value in degrees.values())
             and len(edges) == len(degrees) - 1, "resistance coupon must be a two-terminal chain")
    reached = set()
    pending = [next(iter(ports))]
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        pending.extend(right if left == node else left for left, right in edges if node in (left, right))
    _require(len(reached) == len(degrees), "resistance coupon contains disconnected conductors")
    return sum(edges.values())


def _calibration(value, base, refs, bindings, geometry, process, parasitics, corner):
    from gds_pex_rc import extract_parasitics

    calibration, base = refs.document(value, base)
    _schema(calibration, CALIBRATION_SCHEMA)
    _require(calibration.get("classification") == "quantitative-independent"
             and calibration.get("rc_corner") == corner, "calibration is not quantitative at the extracted RC corner")
    _require(isinstance(calibration.get("idealizations"), list) and calibration["idealizations"]
             and all(isinstance(item, str) and item.strip() for item in calibration["idealizations"]),
             "calibration must disclose remaining idealizations")
    limitations = set(process.get("limitations", [])) | set(parasitics.get("summary", {}).get("limitations", []))
    _require(limitations <= set(calibration["idealizations"]), "calibration hides extraction idealizations")
    for name, path in bindings.items():
        refs.exact(calibration.get("bindings", {}).get(name), base, path, "calibration " + name)
    limits, observations = calibration.get("limits", {}), calibration.get("observations")
    _require(isinstance(limits, dict) and isinstance(observations, list) and observations,
             "calibration has no dimensional quantitative observations")
    used = _used_layers(geometry)
    _require(used and all(isinstance(layer, str) and layer for layer in used), "missing used-layer geometry")
    required = {(kind, (layer,)) for layer in used
                for kind in ("resistance", "ground_capacitance", "lateral_capacitance")}
    required.update(("cross_capacitance", tuple(sorted(pair))) for pair in itertools.combinations(sorted(used), 2))
    required.update(("via_resistance", (via,)) for via in _used_vias(geometry))
    covered = set()
    for observation_ref in observations:
        observation, observation_base = refs.document(observation_ref, base)
        _schema(observation, OBSERVATION_SCHEMA)
        kind, layers = observation.get("kind"), observation.get("layers")
        _require(kind in KINDS and isinstance(layers, list) and layers
                 and all(isinstance(layer, str) and layer for layer in layers)
                 and len(set(layers)) == len(layers), "invalid calibration observation coverage")
        _require(observation.get("rc_corner") == corner and observation.get("length_unit") == "um"
                 and observation.get("unit") == KINDS[kind], "calibration units/corner are unresolved or incompatible")
        _require(observation.get("method") in ("measurement", "independent-field-solver"),
                 "calibration observation is not an independent dimensional reference")
        sources = observation.get("sources")
        _require(isinstance(sources, list) and sources, "calibration observation lacks source data")
        source_paths = {refs.ref(source, observation_base) for source in sources}
        _require(not source_paths.intersection(bindings.values()), "calibration reference is an extraction output, not independent source data")
        coupon, _ = refs.document(observation.get("structure"), observation_base)
        coupon_path = refs.ref(observation["structure"], observation_base)
        rows = []
        for source in sorted(source_paths):
            if source.suffix.lower() == ".csv":
                with source.open(encoding="utf-8", newline="") as stream:
                    rows.extend(row for row in csv.DictReader(stream)
                                if row.get("id") == observation.get("source_observation"))
        _require(isinstance(observation.get("source_observation"), str)
                 and observation["source_observation"] and len(rows) == 1,
                 "calibration lacks a unique dimensional source observation")
        row = rows[0]
        _require(all(row.get(name) == str(observation.get(name))
                     for name in ("kind", "rc_corner", "length_unit", "unit"))
                 and row.get("layers") == ":".join(layers)
                 and row.get("structure_sha256") == opensource._sha256(coupon_path),
                 "calibration source units/corner/structure differ")
        source_value = float(row.get("value", "nan"))
        _number(source_value, "dimensional source observation", positive=True)
        _require(source_value == observation.get("value"), "calibration observation differs from source data")
        coupon_layers = _used_layers(coupon)
        _require(coupon.get("schema_version") == 1 and not coupon.get("devices"),
                 "calibration coupon must be native dimensional interconnect geometry")
        _require((kind == "via_resistance" and _used_vias(coupon) == set(layers))
                 or (kind != "via_resistance" and coupon_layers == set(layers) and not _used_vias(coupon)),
                 "calibration coupon layers differ from observation")
        _require((kind == "cross_capacitance" and len(layers) == 2)
                 or (kind != "cross_capacitance" and len(layers) == 1), "invalid layer count for observation")
        actual = extract_parasitics(coupon, process, "rcc" if kind in ("resistance", "via_resistance") else "c")
        if kind in ("resistance", "via_resistance"):
            prediction = _chain_resistance(actual)
        elif kind == "ground_capacitance":
            _require(len(coupon.get("nets", [])) == 1, "ground capacitance coupon is not isolated")
            prediction = actual["ground_capacitance_pf"]
        else:
            _require(len(coupon.get("nets", [])) == 2, "coupling coupon is not an isolated two-net structure")
            if kind == "cross_capacitance":
                net_layers = [{rect["layer"] for rect in net.get("rectangles", [])}
                              for net in coupon["nets"]]
                _require(all(len(item) == 1 for item in net_layers)
                         and net_layers[0].isdisjoint(net_layers[1]),
                         "cross-layer coupon contains lateral coupling")
            prediction = actual["coupling_capacitance_pf"]
        _number(prediction, "calibration prediction", positive=True)
        measured = _number(observation.get("value"), "calibration observation", positive=True)
        limit = _number(limits.get(kind), "calibration relative-error limit")
        _require(0 <= limit < 1, "calibration relative-error limit must be in [0,1)")
        _require(abs(prediction - measured) / measured <= limit, "calibration comparison exceeds " + kind + " error acceptance")
        covered.add((kind, tuple(sorted(layers))))
    _require(required <= covered, "calibration lacks used-layer/lateral/cross-layer/via coverage: "
             + repr(sorted(required - covered)))


def _known_unaccepted(extraction):
    if not isinstance(extraction, dict):
        return False
    corners = extraction.get("corners", [])
    corners = corners if isinstance(corners, list) else []
    return any(item.get("accepted") is False or item.get("estimated") is True
               or item.get("status") in ("estimated", "seed_only")
               or item.get("source") == "pdk-estimate"
               for item in [extraction] + corners if isinstance(item, dict))


def _pex(value, base, refs, paths, extraction, top):
    _require(isinstance(value, dict), "missing calibrated PEX evidence")
    refs.walk(value, base)
    _require(extraction.get("completed") is True and extraction.get("accepted") is True
             and not _known_unaccepted(extraction) and extraction.get("signoff") is False,
             "PEX is incomplete, estimated, unaccepted, or claims signoff")
    corner = value.get("corner")
    _require(isinstance(corner, str) and corner, "PEX RC corner is not explicit")
    selected = [item for item in extraction.get("corners", [])
                if item.get("corner") == corner and item.get("netlist_sha256") == opensource._sha256(paths["netlist"])]
    _require(len(selected) == 1 and selected[0].get("accepted") is True
             and selected[0].get("status") == "explicit_coupling" and selected[0].get("mode") == "rcc",
             "PEX netlist/corner is estimated or unaccepted")
    refs.exact({"path": selected[0]["netlist"], "sha256": selected[0]["netlist_sha256"]},
               Path(paths["extraction"]).parent, paths["netlist"], "PEX corner netlist")
    documents, bound = {}, dict(paths)
    bound = {key: bound[key] for key in ("gds", "netlist", "reference", "extraction")}
    for name in ("geometry", "process", "parasitics"):
        documents[name], _ = refs.document(value.get(name), base)
        bound[name] = refs.ref(value[name], base)
    geometry, process, parasitics = (documents[key] for key in ("geometry", "process", "parasitics"))
    _require(geometry.get("schema_version") == 1 and geometry.get("design") == top
             and geometry.get("input_gds_sha256") == opensource._sha256(paths["gds"]), "PEX geometry is not bound to GDS/top")
    _require(process.get("kind") == "ics55_gds_pex_process" and process.get("schema_version") == 1
             and process.get("corner") == corner and process.get("status") == "explicit_coupling"
             and process.get("signoff") is False, "PEX process/corner is not explicit non-signoff evidence")
    _require(process.get("source") in ("lef", "starrc") and not any("pdk-estimate" in str(item)
             for item in process.get("provenance", {}).get("assumptions", [])), "PEX inherits pdk-estimate coefficients")
    _require(parasitics.get("design") == top and parasitics.get("status") == "explicit_coupling"
             and parasitics.get("mode") == "rcc" and parasitics.get("signoff") is False
             and parasitics.get("provenance") == process.get("provenance"), "PEX parasitics/process provenance differs")
    _require(_used_layers(geometry) <= set(process.get("explicit_layers", [])), "PEX lacks explicit used-layer coverage")
    evidence, evidence_base = refs.document(extraction.get("evidence"), Path(paths["extraction"]).parent)
    _schema(evidence, "ics55-gds-pex-evidence/1")
    _require(evidence.get("top") == top and evidence.get("mode") == "rcc"
             and evidence.get("source") == process.get("source") and evidence.get("inputs_unchanged") is True,
             "PEX generation provenance is absent or incompatible")
    _require(type(evidence.get("started_ns")) is int and type(evidence.get("ended_ns")) is int
             and 0 < evidence["started_ns"] <= evidence["ended_ns"], "missing actual PEX generation interval")
    refs.exact(evidence.get("input"), evidence_base, paths["gds"], "PEX generation GDS")
    outputs = evidence.get("outputs", {})
    refs.exact(outputs.get("geometry"), evidence_base, bound["geometry"], "PEX generated geometry")
    for name in ("process", "parasitics", "netlist"):
        refs.exact(outputs.get(corner, {}).get(name), evidence_base, bound[name], "PEX generated " + name)
    sources = _captured_sources(evidence, evidence_base, refs)
    _require({(VERIFY / name).resolve() for name in
              ("opensource.py", "gds_pex_geometry.py", "gds_pex_rc.py")} <= sources,
             "PEX has no captured actual extractor source closure")
    for name in ("generator", "process_json"):
        source = process.get("provenance", {}).get(name)
        _require(isinstance(source, str) and Path(source).is_absolute() and Path(source).resolve() in sources,
                 "PEX has no captured " + name + " source identity")
    raw_process = json.loads(Path(process["provenance"]["process_json"]).read_text(encoding="utf-8"))
    def estimated_ancestry(item):
        if isinstance(item, dict):
            return any((key in ("source", "base_source", "inherits", "status") and "estimate" in str(child).lower())
                       or (key == "estimated" and child is True) or estimated_ancestry(child)
                       for key, child in item.items())
        if isinstance(item, list):
            return any(estimated_ancestry(child) for child in item)
        return False
    _require(not estimated_ancestry(raw_process), "PEX process input inherits estimated calibration")
    _calibration(value.get("calibration"), base, refs, bound, geometry, process, parasitics, corner)
    return True


def validate_physical_evidence(manifest_path, paths, extraction):
    """Return tri-state DRC/LVS/PEX checks, estimated status, refs, and reasons.

    Invalid/incomplete execution is unknown, not a completed mismatch. A known
    unaccepted/estimated extraction is false even when no manifest is available.
    This function validates evidence only and never launches EDA or writes files.
    """
    refs = _References()
    known_bad_pex = _known_unaccepted(extraction)
    result = {"checks": {"drc": None, "lvs": None, "pex_accepted": False if known_bad_pex else None},
              "estimated": True, "artifacts": refs.artifacts, "reasons": []}
    if manifest_path is None or not Path(manifest_path).is_file():
        result["reasons"].append("physical evidence manifest missing; historical tool state is unknown")
        if known_bad_pex:
            result["reasons"].append("PEX is explicitly estimated or unaccepted")
        return result
    errors = (EvidenceError, opensource.OpenSourceError, OSError, ValueError, KeyError, TypeError,
              AttributeError, IndexError, csv.Error, ET.ParseError)
    try:
        manifest_path = Path(manifest_path).resolve()
        manifest, base = refs.document(opensource._artifact_ref(manifest_path), manifest_path.parent, transitive=False)
        _schema(manifest, SCHEMA)
        top = manifest.get("top")
        _require(isinstance(top, str) and top and top == extraction.get("design"), "physical top differs from extraction")
        for name in ("gds", "netlist", "reference", "extraction"):
            _require(name in paths, "registered physical input missing: " + name)
            refs.exact(manifest.get("bindings", {}).get(name), base, paths[name], "physical " + name)
        _require(extraction == json.loads(Path(paths["extraction"]).read_text(encoding="utf-8")), "extraction object differs from bound input")
        _require(extraction.get("input_gds_sha256") == opensource._sha256(paths["gds"]), "physical extraction GDS differs")
    except errors as exc:
        result["reasons"].append("physical bindings: " + str(exc))
        return result
    for key, check in (("drc", _drc), ("lvs", _lvs)):
        try:
            result["checks"][key] = check(manifest.get(key), base, refs, paths, top)
            if result["checks"][key] is False:
                result["reasons"].append(key + ": completed physical violation/mismatch")
        except errors as exc:
            result["reasons"].append(key + ": " + str(exc))
    try:
        result["checks"]["pex_accepted"] = _pex(manifest.get("pex"), base, refs, paths, extraction, top)
        result["estimated"] = False
    except errors as exc:
        result["checks"]["pex_accepted"] = False
        result["reasons"].append("pex_accepted: " + str(exc))
    return result
