"""Analyze production ICsprout55 search reports without rerunning EDA.

Run after ``python -m compiler.architecture search`` with an explicit sampled
period grid. The analyzer hash-checks the report's generated netlist, GDS, and
extraction artifacts before recomputing physical-size observables.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compiler.architecture.contracts import (ContractError, load_json, resolve_artifact,
                                             validate_measurement)
from compiler.architecture.search import sampled_period_feasibility
from technology.icsprout55.sim.efficiency import SIZE_REASON_KEYS, actual_size_metrics


_SIZE_METRICS = ("total_mos_gate_width_um", "macro_bbox_area_um2",
                 "effective_bit_area_um2", "buffer_driver_cell_area_um2")
_WAVEFORM_METRICS = {
    "read_energy_pj": ("pJ", "read"),
    "write_energy_pj": ("pJ", "write"),
    "leakage_power_uw": ("uW", "macro"),
}


def _artifact(evaluation, name, base):
    reference = evaluation.get("artifacts", {}).get(name)
    if reference is None:
        raise ContractError("evaluation lacks generated " + name + " artifact")
    return resolve_artifact(reference, base), reference



def _waveform_metrics(evaluation, base):
    measurements = evaluation.get("measurements", [])
    if not isinstance(measurements, list):
        raise ContractError("production evaluation measurements must be an array")
    records = {item["name"]: item for item in measurements
               if isinstance(item, dict) and item.get("port") is None}
    details = evaluation.get("details")
    availability = details.get("metric_availability", {}) if isinstance(details, dict) else {}
    result = {}
    for name, (unit, operation) in _WAVEFORM_METRICS.items():
        record = records.get(name)
        if record is None:
            available = availability.get(name, {})
            result[name] = {
                "value": None, "unit": unit,
                "reason": available.get("reason") or "aggregate waveform measurement is absent",
                "evidence": [],
            }
            continue
        validated = validate_measurement(dict(record), base=base)
        if (validated["unit"] != unit or validated["operation"] != operation
                or validated["port"] is not None):
            raise ContractError("invalid unit/operation/domain for " + name)
        result[name] = {
            "value": validated["value"], "unit": unit,
            "reason": validated.get("reason"),
            "evidence": validated["evidence"],
        }
    return result


def _recompute_candidate(record, base):
    evaluation = record.get("evaluation")
    if record.get("status") != "evaluated" or not isinstance(evaluation, dict):
        return None
    waveform_metrics = _waveform_metrics(evaluation, base)
    units = {"total_mos_gate_width_um": "um", "macro_bbox_area_um2": "um^2",
             "effective_bit_area_um2": "um^2", "buffer_driver_cell_area_um2": "um^2"}

    def unavailable(reason):
        metrics = {name: {"value": None, "unit": units[name], "reason": reason}
                   for name in _SIZE_METRICS}
        metrics.update(waveform_metrics)
        return {
            "candidate_id": record.get("id"),
            "status": "unavailable",
            "input_artifacts": {},
            "metrics": metrics,
        }

    try:
        netlist, netlist_ref = _artifact(evaluation, "netlist", base)
        gds, gds_ref = _artifact(evaluation, "gds", base)
        extraction, extraction_ref = _artifact(evaluation, "extraction", base)
        extraction_data = load_json(extraction)
        top_name = extraction_data.get("design") if isinstance(extraction_data, dict) else None
        if not isinstance(top_name, str) or not top_name:
            raise ContractError("hash-bound extraction lacks a generated top-cell name")
    except (ContractError, OSError, ValueError, TypeError) as exc:
        return unavailable(str(exc))
    candidate = record.get("candidate")
    if not isinstance(candidate, dict) or not isinstance(candidate.get("parameters"), dict):
        return unavailable("production candidate lacks generated parameter tuple")
    measured = actual_size_metrics(netlist, gds, top_name, candidate["parameters"])
    metrics = {
        name: {"value": measured[name], "unit": units[name],
               "reason": measured.get(SIZE_REASON_KEYS[name])}
        for name in _SIZE_METRICS
    }
    metrics.update(waveform_metrics)
    return {
        "candidate_id": record["id"],
        "status": "measured" if any(measured[name] is not None for name in _SIZE_METRICS)
                 else "unavailable",
        "input_artifacts": {"netlist": netlist_ref, "gds": gds_ref,
                            "extraction": extraction_ref},
        "metrics": metrics,
        "details": measured["details"],
    }


def analyze_report(report_path):
    report_path = Path(report_path).expanduser().resolve()
    report = load_json(report_path)
    if not isinstance(report, dict) or report.get("schema") != "openram-search-report/1":
        raise ContractError("input must be an openram-search-report/1 production report")
    period_analysis = sampled_period_feasibility(report)
    candidates = [_recompute_candidate(record, report_path.parent)
                  for record in report.get("candidates", [])]
    candidates = [record for record in candidates if record is not None]
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    return {
        "schema": "openram-efficiency-analysis/1",
        "source_report": {"path": str(report_path), "sha256": digest},
        "signoff": False,
        "interpretation": (
            "Physical-size metrics are recomputed from hash-checked generated netlists/GDS. "
            "MOS gate width is device-size observability, not silicon area. Macro bbox, "
            "effective-bit area, and named buffer/driver cell area remain distinct. "
            "Operation energy/leakage values are validated aggregate measurements only; "
            "missing waveform evidence stays null with a reason, and idle energy is not leakage."),
        "sampled_period_feasibility": period_analysis,
        "candidates": candidates,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="production openram-search-report/1 JSON")
    parser.add_argument("--out", required=True, type=Path, help="analysis JSON destination")
    args = parser.parse_args(argv)
    try:
        result = analyze_report(args.report)
        output = args.out.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                          encoding="utf-8")
    except (ContractError, OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
