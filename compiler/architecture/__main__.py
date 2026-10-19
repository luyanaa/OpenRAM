"""Run an explicitly admitted memory architecture experiment without global OPTS.

Source checkout:
    python -m compiler.architecture enumerate REGISTRY.json REQUEST.json
    python -m compiler.architecture search REGISTRY.json REQUEST.json --out-dir RUN

Search replays verified observations by default. --execute explicitly permits
serial local SPICE for admitted candidates lacking a matching observation.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys

from .contracts import ContractError, Registry, load_json, number, validate_request
from .search import enumerate_candidates, run_search


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("enumerate", "search"):
        command = commands.add_parser(name)
        command.add_argument("registry", type=Path)
        command.add_argument("request", type=Path)
        if name == "search":
            command.add_argument("--out-dir", type=Path, required=True)
            command.add_argument("--execute", action="store_true",
                                 help="permit actual local simulator execution for unmatched observations")
            command.add_argument("--ngspice", default="ngspice")
            command.add_argument("--iverilog", default="iverilog")
            command.add_argument("--vvp", default="vvp")
            command.add_argument("--timeout", type=float, default=3600,
                                 help="seconds per actual simulator invocation")
            command.add_argument("--max-evaluations", type=int,
                                 help="explicit callback limit; remaining uncached candidates stay unknown")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        registry = Registry(args.registry)
        request = validate_request(load_json(args.request))
        if args.command == "enumerate":
            records = list(enumerate_candidates(registry, request))
            print(json.dumps({"schema": "openram-enumeration/1", "candidates": records,
                              "registry_sha256": registry.digest, "signoff": False},
                             indent=2, default=str, allow_nan=False))
            return 0 if any(not record.get("admission_error") for record in records) else 1
        number(args.timeout, "timeout", 0, strict=True)
        if args.max_evaluations is not None and args.max_evaluations < 0:
            raise ContractError("max-evaluations must be nonnegative")
        if registry.data["technology"] != "icsprout55":
            raise ContractError("no local evaluation backend for this technology")
        prefix = "openram." if __package__.startswith("openram.") else ""
        records = list(enumerate_candidates(registry, request))
        module_names = {
            "ics55-ngspice": "technology.icsprout55.sim.architecture",
            "ics55-iverilog": "technology.icsprout55.sim.behavioral",
            "ics55-register-file-f1": "technology.icsprout55.sim.register_file",
        }
        adapter_families = {
            "ics55-ngspice": {"sram"},
            "ics55-iverilog": {"sram"},
            "ics55-register-file-f1": {"register_file"},
        }
        adapters = {}
        adapter_fingerprints = {}
        adapter_names = set()
        for record in records:
            if "admission_error" in record:
                continue
            try:
                admission = registry.admit(record["candidate"])
            except ContractError:
                # run_search owns the authoritative per-record admission result.
                continue
            adapter_names.add(admission["model"]["adapter"])
        for adapter_name in sorted(adapter_names):
            if adapter_name not in module_names:
                adapter_fingerprints[adapter_name] = {
                    "adapter": adapter_name, "status": "unsupported-local"}
                continue
            adapter = importlib.import_module(prefix + module_names[adapter_name])
            adapters[adapter_name] = adapter
            fingerprint_kwargs = {"replay_only": not args.execute, "timeout": args.timeout}
            if adapter_name == "ics55-ngspice":
                fingerprint_kwargs["ngspice"] = args.ngspice
            else:
                fingerprint_kwargs.update(iverilog=args.iverilog, vvp=args.vvp)
            adapter_fingerprints[adapter_name] = adapter.fingerprint(
                registry, **fingerprint_kwargs)
        fingerprint = {"schema": "openram-local-adapters/1",
                       "adapters": adapter_fingerprints}

        def evaluate(admission, destination):
            adapter_name = admission["model"]["adapter"]
            adapter = adapters.get(adapter_name)
            family = admission["candidate"]["family"]
            if adapter is None or family not in adapter_families.get(adapter_name, set()):
                raise ContractError("candidate has no compatible local evaluator: " + adapter_name)
            evaluate_kwargs = {"execute": args.execute, "timeout": args.timeout,
                               "required_margins": request["required_margins"]}
            if adapter_name == "ics55-ngspice":
                evaluate_kwargs["ngspice"] = args.ngspice
            else:
                evaluate_kwargs.update(iverilog=args.iverilog, vvp=args.vvp)
            return adapter.evaluate(admission, destination, **evaluate_kwargs)

        report = run_search(registry, request, args.out_dir, evaluate,
                            evaluator_fingerprint=fingerprint, max_evaluations=args.max_evaluations)
        print(json.dumps({"report": str((args.out_dir / "report.json").resolve()),
                          "objective_scope": report["objective_scope"],
                          "screening_frontier": report["screening_frontier"],
                          "physical_frontier": report["physical_frontier"],
                          "candidates": [{"id": record["id"], "status": record["status"],
                                          "period_ns": record["candidate"]["environment"]["period_ns"],
                                          "screening_feasibility": record.get("screening_feasibility"),
                                          "physical_feasibility": record.get("physical_feasibility")}
                                         for record in report["candidates"]],
                          "signoff": False}, indent=2, allow_nan=False))
        return 0 if report["screening_frontier"] or report["physical_frontier"] else 1
    except (ContractError, OSError, ValueError) as exc:
        print("architecture optimizer: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
