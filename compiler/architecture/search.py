"""Deterministic, evidence-bound search over explicitly admitted implementations.

The frontiers are research comparisons, never signoff. Electrical screening and
physical feasibility are deliberately distinct, and neither invents missing PPA.
"""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path

from .contracts import (ContractError, ENVIRONMENT, FIDELITIES, MARGINS,
                        canonical_bytes, candidate_id, comparison_domain, fields,
                        identity, load_json, resolve_artifact, validate_measurement,
                        validate_request)


_METRICS = {
    "area_um2": ("um^2", "macro"),
    "read_energy_pj": ("pJ", "read"),
    "write_energy_pj": ("pJ", "write"),
    "cycle_time_ns": ("ns", "macro"),
    "wen_pulse_ns": ("ns", "write"),
    "sa_setup_ns": ("ns", "read"),
    "bl_diff_v": ("V", "read"),
    "write_recovery_ns": ("ns", "write"),
}
_CHECKS = ("functional", "topology", "drc", "lvs", "pex_accepted")
_MARGIN_ORDER = ("wen_pulse_ns", "sa_setup_ns", "bl_diff_v", "write_recovery_ns")


def enumerate_candidates(registry, request):
    """Return stable candidate records, including every unadmitted combination.

    Unknown fields are request errors, not extra implementation knobs. Invalid
    *values* remain visible as rejected tuples. Semantically identical normalized
    tuples are evaluated once, regardless of axis ordering or numeric spelling.
    """
    request = validate_request(deepcopy(request))
    try:
        capability = registry.capabilities[request["capability_id"]]
    except (KeyError, TypeError) as exc:
        raise ContractError("unknown capability: " + str(request["capability_id"])) from exc
    fields(request["parameters"], set(capability["parameters"]), label="request parameters")
    fields(request["environment"], ENVIRONMENT, label="request environment")
    for axis in request["search"]:
        section, name = axis.split(".")
        allowed = capability["parameters"] if section == "parameters" else ENVIRONMENT
        if name not in allowed:
            raise ContractError("unknown search axis: " + axis)
    base = {"schema": "openram-candidate/1", "capability_id": capability["id"],
            "family": capability["family"], "backend": capability["backend"],
            "access_contract": capability["access_contract"],
            "parameters": request["parameters"], "environment": request["environment"]}
    axes = sorted(request["search"])
    choices = [sorted(request["search"][axis], key=canonical_bytes) for axis in axes]
    records, seen = [], set()
    for values in product(*choices):
        candidate = deepcopy(base)
        for axis, value in zip(axes, values):
            section, name = axis.split(".")
            candidate[section][name] = deepcopy(value)
        error = None
        try:
            candidate = registry.candidate(candidate["capability_id"], candidate["parameters"],
                                           candidate["environment"])
            identifier = candidate_id(candidate)
            registry.admit(candidate)
        except ContractError as exc:
            # A malformed environment still has a deterministic JSON identity;
            # never disguise it as a valid normalized candidate.
            identifier = identity(candidate)
            error = str(exc)
        if identifier in seen:
            continue
        seen.add(identifier)
        record = {"candidate": candidate, "id": identifier}
        if error is not None:
            record["admission_error"] = error
        records.append(record)
    return records


def _save(path, value):
    canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _reference(reference, base):
    path = resolve_artifact(reference, base)
    return {"path": str(path), "sha256": reference["sha256"]}


def _validate_evaluation(evaluation, candidate, base):
    evaluation = deepcopy(evaluation)
    fields(evaluation, {"measurements", "checks", "artifacts", "estimated", "signoff", "details"},
           label="evaluation")
    fields(evaluation["checks"], set(_CHECKS), label="evaluation checks")
    for name, value in evaluation["checks"].items():
        if value is not None and type(value) is not bool:
            raise ContractError("check " + name + " must be boolean or null")
    if type(evaluation["estimated"]) is not bool or evaluation["signoff"] is not False:
        raise ContractError("evaluation requires boolean estimated and signoff:false")
    if not isinstance(evaluation["details"], dict):
        raise ContractError("evaluation details must be an object")
    if not isinstance(evaluation["artifacts"], dict):
        raise ContractError("evaluation artifacts must be an object")
    evaluation["artifacts"] = {name: _reference(ref, base)
                               for name, ref in evaluation["artifacts"].items()}
    if not isinstance(evaluation["measurements"], list):
        raise ContractError("evaluation measurements must be an array")
    seen = set()
    for record in evaluation["measurements"]:
        validate_measurement(record, base=base)
        if canonical_bytes(record["domain"]) != canonical_bytes(candidate["environment"]):
            raise ContractError("measurement environment differs from candidate: " + record["name"])
        if record["fidelity"] != candidate["environment"]["fidelity"]:
            raise ContractError("measurement fidelity differs from candidate")
        name, port, operation = record["name"], record["port"], record["operation"]
        if name in _METRICS:
            unit, expected_operation = _METRICS[name]
            if record["unit"] != unit or operation != expected_operation:
                raise ContractError("invalid unit/operation for " + name)
            if name not in MARGINS and record["value"] is not None and record["value"] < 0:
                raise ContractError("negative objective: " + name)
        if port is not None:
            ports = candidate["access_contract"]["ports"]
            if port >= len(ports):
                raise ContractError("measurement names absent port")
            if (operation == "read" and ports[port] not in ("R", "RW")
                    or operation == "write" and ports[port] not in ("W", "RW")):
                raise ContractError("measurement operation conflicts with port role")
        key = (name, port, operation)
        if key in seen:
            raise ContractError("duplicate measurement: " + name)
        seen.add(key)
        record["evidence"] = [_reference(ref, base) for ref in record["evidence"]]
    if (any(value is not None for value in evaluation["checks"].values())
            and not evaluation["artifacts"] and not evaluation["measurements"]):
        raise ContractError("known checks require hash-bound artifact or measurement evidence")
    canonical_bytes(evaluation)
    return evaluation


def _constraint(name, actual, required=True, reason=None):
    status = "unknown" if actual is None else ("pass" if actual is True else "fail")
    return {"name": name, "status": status, "actual": actual, "required": required,
            "reason": reason or (name + " is " + status)}


def _feasibility(constraints):
    if any(item["status"] == "fail" for item in constraints):
        return "fail"
    if any(item["status"] == "unknown" for item in constraints):
        return "unknown"
    return "pass"


def _assessment(candidate, evaluation, request):
    fidelity = candidate["environment"]["fidelity"]
    # Per-port values do not satisfy aggregate requirements. The adapter must
    # establish coverage and report an explicit aggregate, not copy port zero.
    metrics = {record["name"]: record["value"] for record in evaluation["measurements"]
               if record["port"] is None and record["name"] in _METRICS}
    checks = evaluation["checks"]
    screening = [_constraint("electrical_fidelity", True if fidelity in ("F2", "F3", "F4") else None,
                             reason="electrical screening requires F2 or higher"),
                 _constraint("functional", checks["functional"])]
    for name in _MARGIN_ORDER:
        actual, required = metrics.get(name), request["required_margins"][name]
        passed = None if actual is None or fidelity in ("F0", "F1") else actual >= required
        item = _constraint(name, passed, required,
                           ("{} is not electrical proof at {}".format(name, fidelity)
                            if fidelity in ("F0", "F1") else
                            "{}: measured {!r}, required >= {!r}".format(name, actual, required)))
        item["actual"] = actual
        screening.append(item)
    if fidelity in ("F3", "F4"):
        screening.append(_constraint("topology", checks["topology"]))
    physical_gates = [_constraint("physical_fidelity", True if fidelity == "F4" else None,
                                  reason="final physical feasibility requires F4"),
                      _constraint("drc", checks["drc"]), _constraint("lvs", checks["lvs"]),
                      _constraint("pex_accepted", checks["pex_accepted"]),
                      _constraint("not_estimated", not evaluation["estimated"],
                                  reason="estimated results cannot establish physical feasibility")]
    physical = screening + physical_gates
    # F4 is an extraction/physical claim, so its screening frontier must not
    # silently overlook failures at the very fidelity it requested.
    if fidelity == "F4":
        screening = physical[:]
    objectives = {name: metrics.get(name) for name in request["objectives"]}
    objective_constraints = [_constraint("objective:" + name,
                                        None if value is None else True,
                                        reason="aggregate objective is missing" if value is None
                                        else "aggregate objective is measured")
                             for name, value in objectives.items()]
    if fidelity == "F0":
        stage = [_constraint("basic_admission", True)] + objective_constraints
        stage += [_constraint(name, False) for name in _CHECKS
                  if name != "pex_accepted" and checks[name] is False]
        for name in _MARGIN_ORDER:
            actual, required = metrics.get(name), request["required_margins"][name]
            if actual is not None and actual < required:
                item = _constraint(name, False, required,
                                   reason="known F0 estimate violates required " + name)
                item["actual"] = actual
                stage.append(item)
    elif fidelity == "F1":
        stage = [_constraint("basic_admission", True), _constraint("functional", checks["functional"])]
        stage += [_constraint(name, False) for name in _CHECKS
                  if name not in ("functional", "pex_accepted") and checks[name] is False]
    else:
        stage = physical if fidelity == "F4" else screening
    constraints = stage + physical + objective_constraints
    failed = next((item for item in constraints if item["status"] == "fail"), None)
    unknown = next((item for item in constraints if item["status"] == "unknown"), None)
    return {"screening_constraints": screening, "physical_constraints": physical,
            "objective_constraints": objective_constraints,
            "stage_constraints": stage, "stage_feasibility": _feasibility(stage),
            "screening_feasibility": _feasibility(screening),
            "physical_feasibility": _feasibility(physical),
            "objective_values": objectives,
            "objectives_complete": all(value is not None for value in objectives.values()),
            "first_failed_constraint": failed["name"] if failed else None,
            "first_unknown_constraint": unknown["name"] if unknown else None,
            "first_failed_reason": failed["reason"] if failed else None,
            "first_unknown_reason": unknown["reason"] if unknown else None}


def _promotion(registry, record, evaluation):
    fidelity = record["candidate"]["environment"]["fidelity"]
    index = FIDELITIES.index(fidelity)
    next_fidelity = FIDELITIES[index + 1] if index + 1 < len(FIDELITIES) else None
    result = {"eligible": False, "next_fidelity": next_fidelity}
    if next_fidelity is None:
        result["reason"] = "already at highest supported fidelity"
    elif record["stage_feasibility"] != "pass":
        result["reason"] = "previous fidelity gates did not all pass"
    elif next_fidelity == "F4" and (evaluation["estimated"]
                                    or evaluation["checks"]["pex_accepted"] is not True):
        result["reason"] = "F4 promotion requires non-estimated, accepted extraction"
    else:
        candidate = deepcopy(record["candidate"])
        candidate["environment"]["fidelity"] = next_fidelity
        try:
            admission = registry.admit(candidate)
        except ContractError as exc:
            result["reason"] = "next fidelity is not admitted: " + str(exc)
        else:
            result.update(eligible=True, reason="previous gates pass and next model is admitted",
                          next_candidate_id=candidate_id(candidate),
                          next_model_sha256=identity(admission["model"]))
    return result


def _frontier(records, feasibility):
    eligible = [record for record in records if record.get(feasibility) == "pass"
                and record.get("objectives_complete")]
    frontier = []
    for record in eligible:
        values = record["objective_values"]
        dominated = any(other["comparison_domain"] == record["comparison_domain"]
                        and all(other["objective_values"][name] <= value
                                for name, value in values.items())
                        and any(other["objective_values"][name] < value
                                for name, value in values.items())
                        for other in eligible)
        if not dominated:
            frontier.append(record["id"])
    return frontier


def _scope(objectives):
    if set(objectives) == {"cycle_time_ns"}:
        return "timing-screen"
    if set(objectives) == {"area_um2", "read_energy_pj", "write_energy_pj", "cycle_time_ns"}:
        return "full-ppa"
    return "partial-ppa"


def _unevaluated(record, status, reason, request):
    gate = "admission" if status == "rejected" else status
    record.update(status=status, reason=reason, screening_feasibility="unknown",
                  stage_feasibility="unknown",
                  physical_feasibility="unknown", objective_values={name: None for name in request["objectives"]},
                  objectives_complete=False, estimated=None, signoff=False,
                  first_failed_constraint=gate if status == "rejected" else None,
                  first_failed_reason=reason if status == "rejected" else None,
                  first_unknown_constraint=gate if status != "rejected" else "evaluation",
                  first_unknown_reason=reason,
                  promotion={"eligible": False, "next_fidelity": None,
                             "reason": "candidate has no passed evaluation gates"})
    return record


def _lineage(candidate):
    candidate = deepcopy(candidate)
    del candidate["environment"]["fidelity"]
    return identity(candidate)


def run_search(registry, request, out_dir, evaluator, *, evaluator_fingerprint, max_evaluations=None):
    """Evaluate admitted tuples, retain uncertainty, and persist revalidated evidence.

    max_evaluations limits callback invocations, not enumeration or cache reads.
    Output directories are append-only for adapter attempts; no unknown files are
    removed. Repeated calls reuse only hash-validated measurements and artifacts.
    Multiple requested fidelities form a staged pipeline; a single fidelity is
    an explicit direct study and does not fabricate evidence at earlier stages.
    """
    if not isinstance(evaluator_fingerprint, dict) or not evaluator_fingerprint:
        raise ContractError("evaluator_fingerprint must be a nonempty JSON identity object")
    canonical_bytes(evaluator_fingerprint)
    if max_evaluations is not None and (type(max_evaluations) is not int or max_evaluations < 0):
        raise ContractError("max_evaluations must be a nonnegative integer or null")
    request = validate_request(deepcopy(request))
    records = enumerate_candidates(registry, request)
    levels = [fidelity for fidelity in FIDELITIES
              if any(row["candidate"]["environment"]["fidelity"] == fidelity for row in records)]
    records.sort(key=lambda row: FIDELITIES.index(row["candidate"]["environment"]["fidelity"])
                 if row["candidate"]["environment"]["fidelity"] in FIDELITIES else len(FIDELITIES))
    lineage_records = {(_lineage(row["candidate"]), row["candidate"]["environment"]["fidelity"]): row
                       for row in records if row["candidate"]["environment"]["fidelity"] in FIDELITIES}
    predecessor_frontiers = {}
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    provenance = {"registry_path": str(registry.path), "registry_sha256": registry.digest,
                  "registry_source_sha256": registry.source_digest, "pdk": deepcopy(registry.data["pdk"]),
                  "evaluator_fingerprint": deepcopy(evaluator_fingerprint)}
    evaluated = 0
    for record in records:
        directory = out_dir / record["id"]
        directory.mkdir(exist_ok=True)
        candidate_path = directory / "candidate.json"
        if candidate_path.exists() and canonical_bytes(load_json(candidate_path)) != canonical_bytes(record["candidate"]):
            raise ContractError("candidate output directory contains a different candidate: " + str(directory))
        _save(candidate_path, record["candidate"])
        record.update(schema="openram-search-result/1", objective_scope=_scope(request["objectives"]),
                      provenance=deepcopy(provenance), signoff=False)
        if "admission_error" in record:
            _unevaluated(record, "rejected", record["admission_error"], request)
            _save(directory / "result.json", record)
            continue
        try:
            admission = registry.admit(record["candidate"])
        except ContractError as exc:
            record["admission_error"] = str(exc)
            _unevaluated(record, "rejected", str(exc), request)
            _save(directory / "result.json", record)
            continue
        record["comparison_domain"] = comparison_domain(record["candidate"])
        record["provenance"]["model"] = deepcopy(admission["model"])
        fidelity = record["candidate"]["environment"]["fidelity"]
        if len(levels) > 1 and fidelity != levels[0]:
            previous_fidelity = FIDELITIES[FIDELITIES.index(fidelity) - 1]
            previous = lineage_records.get((_lineage(record["candidate"]), previous_fidelity))
            reason = None
            if previous is None:
                reason = "required predecessor " + previous_fidelity + " was not requested"
            elif previous.get("promotion", {}).get("eligible") is not True:
                reason = previous_fidelity + " did not pass promotion gates: " + previous.get(
                    "promotion", {}).get("reason", "no completed predecessor evidence")
            elif previous_fidelity != "F1":
                if previous_fidelity not in predecessor_frontiers:
                    predecessor_frontiers[previous_fidelity] = set(_frontier(
                        [row for row in records
                         if row["candidate"]["environment"]["fidelity"] == previous_fidelity],
                        "stage_feasibility"))
                if not previous.get("objectives_complete"):
                    reason = previous_fidelity + " has incomplete objectives for frontier selection"
                elif previous["id"] not in predecessor_frontiers[previous_fidelity]:
                    reason = previous_fidelity + " candidate is dominated before promotion"
            if reason is not None:
                _unevaluated(record, "not_promoted", reason, request)
                _save(directory / "result.json", record)
                continue
        inputs = {"engine_schema": "openram-search-cache/1", "candidate": record["candidate"],
                  "registry_sha256": registry.digest, "registry_source_sha256": registry.source_digest,
                  "pdk": admission["pdk"], "model": admission["model"],
                  "evaluator_fingerprint": evaluator_fingerprint,
                  "objectives": request["objectives"], "required_margins": request["required_margins"]}
        input_key = identity(inputs)
        record["provenance"]["input_key"] = input_key
        cache_path = directory / "cache" / (input_key + ".json")
        evaluation = None
        record["cache"] = {"hit": False, "input_key": input_key}
        if cache_path.exists():
            try:
                cache = load_json(cache_path)
                fields(cache, {"schema", "input_key", "cache_key", "evaluation"}, label="evaluation cache")
                if cache["schema"] != "openram-search-cache/1" or cache["input_key"] != input_key:
                    raise ContractError("cache input identity differs")
                if cache["cache_key"] != identity({"input_key": input_key, "evaluation": cache["evaluation"]}):
                    raise ContractError("cache output identity differs")
                evaluation = _validate_evaluation(cache["evaluation"], record["candidate"], directory)
                record["cache"].update(hit=True, cache_key=cache["cache_key"])
            except ContractError as exc:
                record["cache"]["rejection_reason"] = str(exc)
        if evaluation is None:
            if max_evaluations is not None and evaluated >= max_evaluations:
                _unevaluated(record, "limited", "explicit evaluation limit reached", request)
                _save(directory / "result.json", record)
                continue
            attempts = directory / "runs" / input_key
            attempts.mkdir(parents=True, exist_ok=True)
            # Use a fresh child even after a stale cache or interrupted callback.
            # Existing adapter files (including unknown files) remain untouched.
            attempt = 1
            while True:
                run_dir = attempts / ("attempt-{:04d}".format(attempt))
                try:
                    run_dir.mkdir()
                    break
                except FileExistsError:
                    attempt += 1
            evaluated += 1
            record["provenance"]["evaluation_directory"] = str(run_dir)
            try:
                evaluation = _validate_evaluation(evaluator(admission, run_dir), record["candidate"], run_dir)
                registry.admit(record["candidate"])
            except Exception as exc:
                _unevaluated(record, "evaluation_error", type(exc).__name__ + ": " + str(exc), request)
                _save(directory / "result.json", record)
                continue
            cache_key = identity({"input_key": input_key, "evaluation": evaluation})
            _save(cache_path, {"schema": "openram-search-cache/1", "input_key": input_key,
                               "cache_key": cache_key, "evaluation": evaluation})
            record["cache"]["cache_key"] = cache_key
        record.update(status="evaluated", evaluation=evaluation, estimated=evaluation["estimated"])
        record.update(_assessment(record["candidate"], evaluation, request))
        record["promotion"] = _promotion(registry, record, evaluation)
        _save(directory / "result.json", record)
    report = {"schema": "openram-search-report/1", "request": request, "provenance": provenance,
              "objective_scope": _scope(request["objectives"]), "signoff": False,
              "margin_scope": "user-requested research constraints, not characterized sense-amplifier specifications",
              "estimated": any(record.get("estimated") is True for record in records),
              "max_evaluations": max_evaluations, "evaluations_performed": evaluated,
              "scheduling": {"mode": "staged" if len(levels) > 1 else "direct-fidelity",
                             "requested_fidelities": levels,
                             "selection": "previous feasible frontier; F1 functional gate"},
              "candidates": records,
              "screening_frontier": _frontier(records, "screening_feasibility"),
              "fidelity_frontiers": {
                  fidelity: _frontier([record for record in records
                                       if record["candidate"]["environment"]["fidelity"] == fidelity],
                                      "stage_feasibility")
                  for fidelity in FIDELITIES},
              "physical_frontier": _frontier(records, "physical_feasibility")}
    _save(out_dir / "report.json", report)
    return report
