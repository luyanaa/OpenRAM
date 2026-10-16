#!/usr/bin/env python3
"""Run an actual GDS PEX transistor netlist against an existing tbgen trace.

Inputs change half a clock period before each rising edge, matching mkscs.py.
The PEX summary binds the netlist to its layout hash and extraction provenance.
A functional research pass does not establish calibrated PEX or signoff.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from check import crossings, value_at
from mkscs import read_trace, subckt_pins
from pex_debug import readwrite_target, select_probes

REPO = Path(__file__).resolve().parents[3]


def build_deck(netlist, trace, name, model, destination, period, voltage,
               temperature, corner, skew1, cload, rise, probe_profile=None):
    pins = subckt_pins(netlist, name)
    cycles, expected = read_trace(trace)
    if not cycles or sorted(cycles) != list(range(len(cycles))):
        raise ValueError("trace must contain contiguous cycles starting at zero")
    if not expected:
        raise ValueError("trace has no expected reads")
    if period <= 0 or not 0 < rise < period / 4 or voltage <= 0 or cload < 0:
        raise ValueError("period, edge time, supply or output load is invalid")
    if not 0 <= skew1 < 0.5:
        raise ValueError("secondary-port clock skew must be in [0, 0.5)")
    if len({pin.casefold() for pin in pins}) != len(pins):
        raise ValueError("top port names collide in case-insensitive ngspice")
    if not {"vdd", "gnd"}.issubset(pins):
        raise ValueError("trace adapter requires the OpenRAM vdd/gnd port contract")
    external = {pin: "e{}".format(index) for index, pin in enumerate(pins)}
    external.update(vdd="vdd", gnd="0")
    clocks = [pin for pin in pins if re.fullmatch(r"clk\d+", pin)]
    outputs = [pin for pin in pins if re.fullmatch(r"dout\d+\[\d+\]", pin)]
    if not clocks or not outputs:
        raise ValueError("netlist has no OpenRAM clock/output bus")
    stop = (len(cycles) + 1.25 + skew1) * period
    lines = ["ICsprout55 GDS PEX functional research simulation",
             '.lib "{}" {}_mos'.format(model, corner),
             '.include "{}"'.format(netlist),
             ".temp {}".format(temperature),
             ".options reltol=1e-3 abstol=1e-12 vntol=1e-6",
             "VDD vdd 0 {}".format(voltage)]
    for index, clock in enumerate(clocks):
        port = int(clock[3:])
        delay = period * (1 + (skew1 if port else 0))
        lines.append("Vclk{} {} 0 PULSE(0 {} {}n {}n {}n {}n {}n)".format(
            index, external[clock], voltage, delay, rise, rise,
            period / 2 - rise, period))
    for index, pin in enumerate(pins):
        if pin in clocks or pin in ("vdd", "gnd") or pin in outputs:
            continue
        match = re.fullmatch(r"(\w+?\d+)(?:\[(\d+)\])?", pin)
        if not match:
            raise ValueError("unsupported input pin {}".format(pin))
        field, bit = match.group(1), int(match.group(2) or 0)
        port = int(re.search(r"\d+$", field).group())
        previous = None
        points = []
        for cycle, inputs in sorted(cycles.items()):
            bits = inputs.get(field)
            if bits is None or not re.fullmatch("[01]+", bits) or len(bits) <= bit:
                raise ValueError("cycle {} has no valid {} bit {}".format(cycle, field, bit))
            value = int(bits[-1 - bit])
            edge = (cycle + 0.5 + (skew1 if port else 0)) * period
            if previous is None:
                points.append((0.0, value))
            elif previous != value:
                points.extend(((edge, previous), (edge + rise, value)))
            previous = value
        # ngspice's PWL requires two time points, including constant inputs.
        points.append((stop, previous))
        wave = " ".join("{:.9g}n {:.9g}".format(t, value * voltage) for t, value in points)
        lines.append("Vi{} {} 0 PWL({})".format(index, external[pin], wave))
    for index, pin in enumerate(outputs):
        if cload:
            lines.append("Cload{} {} 0 {}f".format(index, external[pin], cload))
    lines.append("XSRAM {} {}".format(" ".join(external[pin] for pin in pins), name))
    vectors = ["v({})".format(external[pin]) for pin in outputs]
    if probe_profile is not None:
        signals = [dict(name=pin, node=pin,
                        stage="output" if pin in outputs else
                        "supply" if pin in ("vdd", "gnd") else "input")
                   for pin in pins] + probe_profile["signals"]
        external_nodes = {pin.casefold(): node for pin, node in external.items()}
        for signal in signals:
            node = external_nodes.get(signal["node"].casefold())
            if node == "0" or signal["node"] == "0":
                signal.update(column=None, constant_v=0.0)
                continue
            vector = "v({})".format(node if node is not None else "xsram." + signal["node"])
            if vector not in vectors:
                vectors.append(vector)
            signal.update(vector=vector, column=vectors.index(vector))
        probe_profile.update(signals=signals, vectors=vectors)
        (destination / "probes.json").write_text(
            json.dumps(probe_profile, indent=2) + "\n", encoding="utf-8")
    wavefile = destination / "waves.dat"
    lines.extend((".control", "set wr_singlescale", "set wr_vecnames",
                  "set numdgt=15", "save " + " ".join(vectors),
                  "tran {:.9g}n {:.9g}n 0 {:.9g}n".format(period / 100, stop, rise / 2),
                  "wrdata waves.dat " + " ".join(vectors),
                  "quit", ".endc", ".end"))
    deck = destination / "transient.sp"
    deck.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return deck, wavefile, outputs, expected, probe_profile


def check_waveforms(wavefile, outputs, expected, period, voltage, skew1, probe_profile=None):
    times = []
    count = len(probe_profile["vectors"]) if probe_profile is not None else len(outputs)
    columns = [[] for _ in range(count)]
    waves = {pin: columns[index] for index, pin in enumerate(outputs)}
    with wavefile.open(encoding="utf-8") as stream:
        header = stream.readline().split()
        if len(header) != count + 1 or header[0].lower() != "time":
            raise ValueError("ngspice waveform has missing output/probe columns")
        for line in stream:
            row = [float(value) for value in line.split()]
            if len(row) != count + 1 or not all(math.isfinite(value) for value in row):
                raise ValueError("ngspice waveform has incomplete/non-finite samples")
            if times and row[0] < times[-1]:
                raise ValueError("ngspice waveform time is not monotonic")
            times.append(row[0])
            for index, values in enumerate(columns):
                values.append(row[index + 1])
    if len(times) < 2:
        raise ValueError("ngspice waveform has no transient interval")
    failures, measurements = [], []
    reads = bit_reads = 0
    observed_access = None
    minimum_margin = None
    for cycle, buses in sorted(expected.items()):
        for bus, bits in buses.items():
            match = re.fullmatch(r"dout(\d+)", bus)
            if not match or not re.fullmatch("[01]+", bits):
                raise ValueError("expected trace contains an unknown read value")
            port = int(match.group(1))
            edge = (cycle + 1 + (skew1 if port else 0)) * period * 1e-9
            sample = edge + (period - 0.01) * 1e-9
            if times[0] > edge or times[-1] < sample:
                raise ValueError("transient did not cover read {} {}".format(cycle, bus))
            bus_pins = {pin for pin in outputs if pin.startswith(bus + "[")}
            required = {"{}[{}]".format(bus, bit) for bit in range(len(bits))}
            if bus_pins != required:
                raise ValueError("expected output width differs from PEX ports for " + bus)
            reads += 1
            for bit, expected_bit in enumerate(reversed(bits)):
                pin = "{}[{}]".format(bus, bit)
                value = value_at(times, waves[pin], sample)
                bit_reads += 1
                margin = value / voltage if expected_bit == "1" else 1 - value / voltage
                minimum_margin = margin if minimum_margin is None else min(minimum_margin, margin)
                if margin < 0.8:
                    failures.append(dict(cycle=cycle, pin=pin, expected=int(expected_bit),
                                         voltage_v=value, sample_ns=sample * 1e9))
                transitions = [t for t in crossings(times, waves[pin], voltage / 2)
                               if edge < t < sample]
                delay = transitions[-1] - edge if transitions else None
                if delay is not None:
                    observed_access = delay if observed_access is None else max(observed_access, delay)
                initial = value_at(times, waves[pin], edge) / voltage
                initial_margin = initial if expected_bit == "1" else 1 - initial
                if margin < 0.8:
                    status = "invalid_read" if transitions else "no_output_transition"
                elif transitions:
                    status = "measured"
                elif initial_margin >= 0.8:
                    status = "already_at_expected_level"
                else:
                    status = "no_output_transition"
                measurements.append(dict(
                    cycle=cycle, port=port, pin=pin, expected=int(expected_bit),
                    passed=margin >= 0.8, status=status, voltage_v=value,
                    sample_ns=sample * 1e9,
                    tacc_ns=delay * 1e9 if status == "measured" else None))
    passed = bool(reads) and not failures
    measured = [item["tacc_ns"] for item in measurements if item["status"] == "measured"]
    if not passed:
        access_status = "functional_failure"
    elif any(item["status"] == "no_output_transition" for item in measurements):
        access_status = "no_output_transition"
    else:
        access_status = "measured" if measured else "already_at_expected_level"
    checked = dict(passed=passed, reads=reads, bit_reads=bit_reads,
                   failures=failures, read_measurements=measurements,
                   min_level_margin=minimum_margin, access_time_status=access_status,
                   tacc_ns=max(measured) if access_status == "measured" else None,
                   observed_transition_delay_ns=None if observed_access is None else observed_access * 1e9,
                   waveform_stop_ns=times[-1] * 1e9)
    if probe_profile is not None:
        samples = []
        for target in probe_profile["targets"]:
            points = (
                ("write_end", (target["write_cycle"] + 2 +
                               (skew1 if target["write_port"] else 0)) * period - 0.01),
                ("read_edge", (target["read_cycle"] + 1 +
                               (skew1 if target["read_port"] else 0)) * period),
                ("read_sample", (target["read_cycle"] + 2 +
                                 (skew1 if target["read_port"] else 0)) * period - 0.01))
            for label, time_ns in points:
                stages = {}
                for signal in probe_profile["signals"]:
                    value = signal.get("constant_v") if signal["column"] is None else value_at(
                        times, columns[signal["column"]], time_ns * 1e-9)
                    stages.setdefault(signal["stage"], {})[signal["name"]] = value
                samples.append(dict(target=target, phase=label, time_ns=time_ns, stages=stages))
        checked.update(probe_profile=probe_profile["profile"],
                       probe_targets=probe_profile["targets"], probe_samples=samples)
    return checked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("netlist", type=Path)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--pex-summary", type=Path)
    parser.add_argument("--pdk-root", type=Path)
    parser.add_argument("--period", type=float, required=True, help="clock period in ns")
    parser.add_argument("--corner", choices=("tt", "ss", "ff", "fnsp", "snfp"), default="tt")
    parser.add_argument("--vdd", type=float, default=1.2)
    parser.add_argument("--temp", type=float, default=25)
    parser.add_argument("--skew1", type=float, default=0)
    parser.add_argument("--cload", type=float, default=5, help="output load in fF")
    parser.add_argument("--tr", type=float, default=0.03, help="input edge time in ns")
    parser.add_argument("--ngspice", default="ngspice")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--probe-profile", choices=("debug-readwrite",))
    parser.add_argument("--probe-map", type=Path,
                        help="hashed semantic-to-physical correspondence from topology comparison")
    parser.add_argument("--failure-report", type=Path,
                        help="select the first failure from a same-GDS, same-trace run")
    parser.add_argument("--words-per-row", type=int, default=1,
                        help="physical column multiplexing for debug probes")
    args = parser.parse_args(argv)
    destination = args.out_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    netlist, trace = args.netlist.expanduser().resolve(), args.trace.expanduser().resolve()
    try:
        summary_path = args.pex_summary or netlist.parent / "pex_summary.json"
        extraction = json.loads(summary_path.read_text(encoding="utf-8"))
        if extraction.get("kind") in {"ics55_gds_topology_diagnostic", "ics55_gds_mos_only"}:
            selected = [dict(netlist=extraction["mos_only_netlist"],
                             netlist_sha256=extraction["mos_only_netlist_sha256"],
                             status="mos_only")]
            accepted = False
        else:
            selected = [corner for corner in extraction["corners"]
                        if Path(corner["netlist"]).resolve() == netlist]
            accepted = extraction["accepted"]
        if (len(selected) != 1 or not extraction["completed"] or
                Path(selected[0]["netlist"]).resolve() != netlist):
            raise ValueError("extraction summary does not identify the completed input netlist")
        digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
        if digest != selected[0]["netlist_sha256"]:
            raise ValueError("netlist changed after extraction; hash does not match summary")
        pdk = (args.pdk_root or Path(os.environ.get("ICS55_PDK_ROOT", REPO.parent / "icsprout55-pdk"))).expanduser().resolve()
        model = pdk / "libs.tech/spice/foundry/ngspice/ICsprout_55LLULP1225_V1p1_hsp.lib"
        if not model.is_file():
            raise ValueError("ngspice foundry model library is missing: " + str(model))
        executable = shutil.which(args.ngspice)
        if executable is None:
            raise ValueError("ngspice executable is missing: " + args.ngspice)
        probe_profile = None
        if args.probe_profile:
            if args.probe_map is None:
                raise ValueError("--probe-profile requires an established --probe-map")
            mapping = json.loads(args.probe_map.read_text(encoding="utf-8"))
            if mapping["input_gds_sha256"] != extraction["input_gds_sha256"]:
                raise ValueError("probe map belongs to a different GDS")
            if mapping["netlist_sha256"] != digest:
                raise ValueError("probe map belongs to a different emitted netlist")
            failures = None
            if args.failure_report:
                recorded = json.loads(args.failure_report.read_text(encoding="utf-8"))
                if (recorded["input_gds_sha256"] != extraction["input_gds_sha256"] or
                        recorded["trace_sha256"] != hashlib.sha256(trace.read_bytes()).hexdigest()):
                    raise ValueError("failure report belongs to a different GDS or trace")
                failures = recorded["failures"]
            _, expected_reads = read_trace(trace)
            read_ports = sorted({int(bus[4:]) for buses in expected_reads.values() for bus in buses})
            if failures is not None:
                read_ports = [port for port in read_ports if any(
                    item["pin"].startswith("dout{}[".format(port)) for item in failures)]
            if not read_ports:
                raise ValueError("debug profile has no target read ports")
            profiles = [select_probes(
                mapping, readwrite_target(trace, failures, read_port=port), args.words_per_row)
                for port in read_ports]
            signals = {}
            for profile in profiles:
                for signal in profile["signals"]:
                    previous = signals.get(signal["name"])
                    if previous is not None and previous["node"] != signal["node"]:
                        raise ValueError("debug targets disagree on a physical signal mapping")
                    signals[signal["name"]] = signal
            probe_profile = dict(profile="debug-readwrite",
                                 targets=[profile["target"] for profile in profiles],
                                 signals=list(signals.values()))
            probe_profile.update(input_gds_sha256=extraction["input_gds_sha256"],
                                 netlist_sha256=digest, probe_map=str(args.probe_map.resolve()),
                                 probe_map_sha256=hashlib.sha256(args.probe_map.read_bytes()).hexdigest(),
                                 topology_equivalent=mapping["equivalent"])
        elif args.probe_map or args.failure_report:
            raise ValueError("--probe-map/--failure-report require --probe-profile")
        deck, wavefile, outputs, expected, probe_profile = build_deck(
            netlist, trace, extraction["design"], model, destination,
            args.period, args.vdd, args.temp, args.corner, args.skew1, args.cload, args.tr,
            probe_profile)
        if wavefile.exists():
            wavefile.unlink()
        command = [executable, "-n", "-D", "ngbehavior=hsa", "-b", str(deck)]
        start = time.monotonic()
        result = subprocess.run(command, cwd=destination, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=args.timeout, check=False)
        (destination / "ngspice.log").write_text(result.stdout, encoding="utf-8", errors="replace")
        if result.returncode != 0 or not wavefile.is_file():
            raise ValueError("ngspice produced no complete waveform; see ngspice.log")
        checked = check_waveforms(wavefile, outputs, expected, args.period, args.vdd,
                                 args.skew1, probe_profile)
        checked.update(netlist=str(netlist), netlist_sha256=digest,
                       input_gds_sha256=extraction["input_gds_sha256"],
                       trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
                       pex_status=selected[0]["status"], pex_accepted=accepted,
                       extraction_summary=str(summary_path.resolve()),
                       extraction_summary_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                       signoff=False, model=str(model), corner=args.corner, voltage_v=args.vdd,
                       temperature_c=args.temp, period_ns=args.period, cload_ff=args.cload,
                       seconds=round(time.monotonic() - start, 3))
    except subprocess.TimeoutExpired as exc:
        text = exc.stdout or b""
        if isinstance(text, bytes):
            text = text.decode(errors="replace")
        (destination / "ngspice.log").write_text(text, encoding="utf-8")
        checked = dict(passed=False, error="ngspice timeout", signoff=False)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        checked = dict(passed=False, error=str(exc), signoff=False)
    (destination / "simulation.json").write_text(json.dumps(checked, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(checked, indent=2))
    return 0 if checked["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
