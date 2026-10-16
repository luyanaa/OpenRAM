"""Fail-closed, file-backed contracts for memory architecture searches.

This module has no dependency on OpenRAM's global options or on an EDA tool.
A capability declares exact implemented parameter tuples; a Cartesian product
of individually legal values is deliberately not proof of a legal implementation.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path


FIDELITIES = ("F0", "F1", "F2", "F3", "F4")
FAMILIES = ("sram", "register_file", "cam", "tcam", "gain_cell_edram")
ENVIRONMENT = {
    "corner", "voltage_v", "temperature_c", "period_ns", "cload_ff",
    "slew_ns", "skew1", "initialization", "fidelity",
}
OBJECTIVES = {"area_um2", "read_energy_pj", "write_energy_pj", "cycle_time_ns"}
MARGINS = {"wen_pulse_ns", "sa_setup_ns", "bl_diff_v", "write_recovery_ns"}


class ContractError(ValueError):
    """An input cannot establish the requested architecture/evidence contract."""


def fields(value, required, optional=(), label="object"):
    if not isinstance(value, dict):
        raise ContractError(label + " must be an object")
    missing, extra = set(required) - value.keys(), value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ContractError("{}: missing {}; unknown {}".format(label, sorted(missing), sorted(extra)))
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_json(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_pairs,
                             parse_constant=lambda value: _nonfinite(value))
    except (OSError, ValueError) as exc:
        raise ContractError("{}: {}".format(path, exc)) from exc


def _nonfinite(value):
    raise ContractError("non-finite JSON number: " + value)


def canonical_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (ValueError, TypeError) as exc:
        raise ContractError("not canonical JSON: " + str(exc)) from exc


def identity(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ContractError(str(exc)) from exc
    return digest.hexdigest()


def file_ref(path):
    path = Path(path).expanduser().resolve()
    return {"path": str(path), "sha256": file_digest(path)}


def resolve_artifact(reference, base):
    fields(reference, {"path", "sha256"}, label="artifact")
    name, digest = reference["path"], reference["sha256"]
    if not isinstance(name, str) or not name:
        raise ContractError("artifact path must be nonempty")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise ContractError("artifact requires a lowercase SHA-256 digest")
    path = Path(name).expanduser()
    path = (Path(base) / path).resolve() if not path.is_absolute() else path.resolve()
    if file_digest(path) != digest:
        raise ContractError("artifact hash drift: " + str(path))
    return path


def number(value, label, minimum=None, strict=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ContractError(label + " must be finite and numeric")
    if minimum is not None and (value <= minimum if strict else value < minimum):
        raise ContractError(label + " is below its valid bound")
    return 0.0 if value == 0 else float(value)


def _choice(value, rule, label):
    fields(rule, (), ("values", "minimum", "maximum"), label=label + " rule")
    if "values" in rule:
        if set(rule) != {"values"} or not isinstance(rule["values"], list) or not rule["values"]:
            raise ContractError(label + " requires nonempty discrete values")
        # JSON equality must not conflate True with 1, or an integer with a float.
        if canonical_bytes(value) not in [canonical_bytes(item) for item in rule["values"]]:
            raise ContractError(label + " is not declared by the PDK capability")
    else:
        if set(rule) != {"minimum", "maximum"}:
            raise ContractError(label + " requires both range endpoints")
        lower = number(rule["minimum"], label + " minimum")
        upper = number(rule["maximum"], label + " maximum")
        actual = number(value, label)
        if lower > upper or not lower <= actual <= upper:
            raise ContractError(label + " is outside the declared range")


def _environment(env):
    fields(env, ENVIRONMENT, label="environment")
    if env["fidelity"] not in FIDELITIES:
        raise ContractError("unsupported fidelity")
    if env["corner"] not in ("tt", "ss", "ff", "snfp", "fnsp"):
        raise ContractError("unknown process corner")
    if env["initialization"] not in ("uninitialized", "zero-voltage"):
        raise ContractError("unknown floating-node initialization policy")
    for key in ("voltage_v", "period_ns", "slew_ns"):
        env[key] = number(env[key], key, 0, strict=True)
    env["temperature_c"] = number(env["temperature_c"], "temperature_c", -273.15, strict=True)
    env["cload_ff"] = number(env["cload_ff"], "cload_ff", 0)
    env["skew1"] = number(env["skew1"], "skew1", 0)
    if env["skew1"] >= 0.5 or env["slew_ns"] >= env["period_ns"] / 4:
        raise ContractError("invalid clock skew or slew/period relationship")
    return env


def validate_candidate(candidate):
    fields(candidate, {"schema", "capability_id", "family", "backend", "access_contract",
                       "parameters", "environment"}, label="candidate")
    if candidate["schema"] != "openram-candidate/1" or candidate["family"] not in FAMILIES:
        raise ContractError("unsupported candidate schema or memory family")
    for key in ("capability_id", "backend"):
        if not isinstance(candidate[key], str) or not candidate[key]:
            raise ContractError("candidate " + key + " is empty")
    contract = fields(candidate["access_contract"],
                      {"ports", "clocking", "same_row_write", "read_during_write"}, label="access contract")
    if (not isinstance(contract["ports"], list) or not contract["ports"]
            or any(port not in ("RW", "R", "W", "SEARCH") for port in contract["ports"])):
        raise ContractError("invalid port roles")
    for key in ("clocking", "same_row_write", "read_during_write"):
        if not isinstance(contract[key], str) or not contract[key]:
            raise ContractError("missing access semantics: " + key)
    if not isinstance(candidate["parameters"], dict) or not candidate["parameters"]:
        raise ContractError("candidate requires explicit parameters")
    _environment(candidate["environment"])
    canonical_bytes(candidate)
    return candidate


def candidate_id(candidate):
    return identity(validate_candidate(deepcopy(candidate)))


def comparison_domain(candidate):
    """Period is an objective, not permission to mix PVT, loads or init policies."""
    candidate = validate_candidate(deepcopy(candidate))
    domain = deepcopy(candidate["environment"])
    domain.pop("period_ns")
    workload = {key: candidate["parameters"][key] for key in ("word_size", "num_words")
                if key in candidate["parameters"]}
    return identity({"family": candidate["family"], "access_contract": candidate["access_contract"],
                     "workload": workload, "environment": domain})


def _pointer(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ContractError("evidence check requires a JSON pointer")
    try:
        for part in pointer[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not key or any(char not in "0123456789" for char in key) or (len(key) > 1 and key[0] == "0"):
                    raise ContractError("invalid JSON Pointer array index: " + key)
                value = value[int(key)]
            else:
                value = value[key]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ContractError("evidence pointer is absent: " + pointer) from exc
    return value


class Registry:
    """A PDK declaration plus hash-checked, exact-tuple implementation evidence."""

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.data = fields(load_json(self.path), {"schema", "technology", "pdk", "capabilities"}, label="registry")
        self.source_digest = file_digest(self.path)
        if self.data["schema"] != "openram-capabilities/1":
            raise ContractError("unsupported capability registry schema")
        fields(self.data["pdk"], {"revision", "evidence"}, label="PDK identity")
        if not isinstance(self.data["pdk"]["revision"], str) or not self.data["pdk"]["revision"]:
            raise ContractError("PDK revision is required")
        resolve_artifact(self.data["pdk"]["evidence"], self.path.parent)
        if not isinstance(self.data["capabilities"], list) or not self.data["capabilities"]:
            raise ContractError("registry must declare capabilities")
        self.capabilities = {}
        for entry in self.data["capabilities"]:
            fields(entry, {"id", "family", "backend", "access_contract", "parameters", "environment",
                           "excludes", "implementations"}, label="capability")
            if not isinstance(entry["id"], str) or not entry["id"] or entry["id"] in self.capabilities:
                raise ContractError("duplicate or empty capability ID")
            if not isinstance(entry["parameters"], dict) or not entry["parameters"]:
                raise ContractError("capability needs parameter declarations")
            fields(entry["environment"], ENVIRONMENT, label="capability environment")
            if not isinstance(entry["excludes"], list) or not isinstance(entry["implementations"], list):
                raise ContractError("exclusions and implementations must be arrays")
            for exclusion in entry["excludes"]:
                fields(exclusion, {"when", "reason"}, label="exclusion")
                if not isinstance(exclusion["when"], dict) or not exclusion["when"]:
                    raise ContractError("exclusion requires explicit parameter conditions")
                if set(exclusion["when"]) - entry["parameters"].keys():
                    raise ContractError("exclusion names an undeclared parameter")
                if not isinstance(exclusion["reason"], str) or not exclusion["reason"]:
                    raise ContractError("exclusion requires a reason")
                for name, value in exclusion["when"].items():
                    _choice(value, entry["parameters"][name], name)
            seen = set()
            for implementation in entry["implementations"]:
                fields(implementation, {"parameters", "models"}, label="implementation")
                if set(implementation["parameters"]) != set(entry["parameters"]):
                    raise ContractError("implementation must specify every parameter")
                key = identity(implementation["parameters"])
                if key in seen:
                    raise ContractError("duplicate implementation parameter tuple")
                seen.add(key)
                for name, value in implementation["parameters"].items():
                    _choice(value, entry["parameters"][name], name)
                if not isinstance(implementation["models"], dict):
                    raise ContractError("implementation models must be an object")
                for fidelity, model in implementation["models"].items():
                    if fidelity not in FIDELITIES:
                        raise ContractError("unknown implementation fidelity")
                    fields(model, {"adapter", "artifacts", "basic_checks"}, label="model")
                    if not isinstance(model["artifacts"], dict) or not model["artifacts"]:
                        raise ContractError("model requires artifacts")
                    if not isinstance(model["basic_checks"], list) or not model["basic_checks"]:
                        raise ContractError("model requires basic validation evidence")
            self.capabilities[entry["id"]] = entry
        self.digest = identity(self.data)

    def candidate(self, capability_id, parameters, environment):
        try:
            entry = self.capabilities[capability_id]
        except KeyError as exc:
            raise ContractError("unknown capability: " + str(capability_id)) from exc
        return validate_candidate({"schema": "openram-candidate/1", "capability_id": capability_id,
                                   "family": entry["family"], "backend": entry["backend"],
                                   "access_contract": deepcopy(entry["access_contract"]),
                                   "parameters": deepcopy(parameters), "environment": deepcopy(environment)})

    def admit(self, candidate):
        if file_digest(self.path) != self.source_digest:
            raise ContractError("registry changed during search")
        resolve_artifact(self.data["pdk"]["evidence"], self.path.parent)
        candidate = validate_candidate(deepcopy(candidate))
        try:
            entry = self.capabilities[candidate["capability_id"]]
        except KeyError as exc:
            raise ContractError("unknown capability") from exc
        for key in ("family", "backend", "access_contract"):
            if canonical_bytes(entry[key]) != canonical_bytes(candidate[key]):
                raise ContractError("candidate changes declared " + key)
        parameters = candidate["parameters"]
        if set(parameters) != set(entry["parameters"]):
            raise ContractError("unknown or missing candidate parameter")
        for key, value in parameters.items():
            _choice(value, entry["parameters"][key], key)
        for key, value in candidate["environment"].items():
            rule = deepcopy(entry["environment"][key])
            if key not in ("corner", "initialization", "fidelity") and "values" in rule:
                rule["values"] = [number(item, key) for item in rule["values"]]
            _choice(value, rule, key)
        for exclusion in entry["excludes"]:
            if all(key in parameters and canonical_bytes(parameters[key]) == canonical_bytes(value)
                   for key, value in exclusion["when"].items()):
                raise ContractError("excluded combination: " + str(exclusion["reason"]))
        matches = [item for item in entry["implementations"]
                   if canonical_bytes(item["parameters"]) == canonical_bytes(parameters)]
        if len(matches) != 1:
            raise ContractError("parameter tuple has no basic-validated implementation")
        model = matches[0]["models"].get(candidate["environment"]["fidelity"])
        if model is None:
            raise ContractError("implementation has no evidence/model at requested fidelity")
        artifacts = {name: resolve_artifact(ref, self.path.parent)
                     for name, ref in model["artifacts"].items()}
        for check in model["basic_checks"]:
            fields(check, {"artifact", "pointer", "equals"}, label="basic evidence check")
            if check["artifact"] not in artifacts:
                raise ContractError("basic validation references absent artifact")
            actual = _pointer(load_json(artifacts[check["artifact"]]), check["pointer"])
            if canonical_bytes(actual) != canonical_bytes(check["equals"]):
                raise ContractError("basic validation failed: " + check["pointer"])
        return {"candidate": candidate, "model": deepcopy(model), "artifacts": artifacts,
                "registry_sha256": self.digest, "pdk": deepcopy(self.data["pdk"])}


def validate_request(request):
    fields(request, {"schema", "capability_id", "parameters", "environment", "search",
                     "objectives", "required_margins"}, label="search request")
    if request["schema"] != "openram-search/1":
        raise ContractError("unsupported search request schema")
    if not isinstance(request["capability_id"], str) or not request["capability_id"]:
        raise ContractError("request capability_id must be a nonempty string")
    if not isinstance(request["parameters"], dict) or not request["parameters"]:
        raise ContractError("request parameters must be a nonempty object")
    fields(request["environment"], ENVIRONMENT, label="request environment")
    if not isinstance(request["search"], dict):
        raise ContractError("search axes must be an object")
    for name, values in request["search"].items():
        if (not isinstance(name, str) or not name.startswith(("parameters.", "environment."))
                or name.count(".") != 1):
            raise ContractError("search axis must name a parameter or environment field")
        if not isinstance(values, list) or not values:
            raise ContractError("search axis requires nonempty values")
        if len({canonical_bytes(value) for value in values}) != len(values):
            raise ContractError("duplicate search values")
    objectives = request["objectives"]
    if (not isinstance(objectives, list) or not objectives
            or any(not isinstance(item, str) or item not in OBJECTIVES for item in objectives)
            or len(set(objectives)) != len(objectives)):
        raise ContractError("objectives must be distinct supported metrics")
    if not isinstance(request["required_margins"], dict) or set(request["required_margins"]) != MARGINS:
        raise ContractError("all write/sense margins must be explicitly specified")
    for key, value in request["required_margins"].items():
        number(value, key, 0)
    return request


def validate_measurement(record, base="."):
    fields(record, {"name", "value", "unit", "port", "operation", "domain", "fidelity", "evidence"},
           label="measurement")
    if not isinstance(record["name"], str) or not record["name"]:
        raise ContractError("measurement name is required")
    if record["value"] is not None:
        record["value"] = number(record["value"], record["name"])
    if record["fidelity"] not in FIDELITIES or record["operation"] not in ("read", "write", "idle", "macro"):
        raise ContractError("invalid measurement operation/fidelity")
    if record["port"] is not None and (type(record["port"]) is not int or record["port"] < 0):
        raise ContractError("invalid measurement port")
    if not isinstance(record["unit"], str) or not record["unit"]:
        raise ContractError("measurement requires units")
    _environment(record["domain"])
    if record["domain"]["fidelity"] != record["fidelity"]:
        raise ContractError("measurement domain and fidelity disagree")
    if not isinstance(record["evidence"], list) or not record["evidence"]:
        raise ContractError("measurement requires structured artifact evidence")
    for reference in record["evidence"]:
        resolve_artifact(reference, base)
    return record
