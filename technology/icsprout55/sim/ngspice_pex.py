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

REPO = Path(__file__).resolve().parents[3]


def build_deck(netlist, trace, name, model, destination, period, voltage,
               temperature, corner, skew1, cload, rise):
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
    wavefile = destination / "waves.dat"
    lines.extend((".control", "set wr_singlescale", "set wr_vecnames",
                  "set numdgt=15", "save " + " ".join(vectors),
                  "tran {:.9g}n {:.9g}n 0 {:.9g}n".format(period / 100, stop, rise / 2),
                  'wrdata "{}" {}'.format(wavefile, " ".join(vectors)),
                  "quit", ".endc", ".end"))
    deck = destination / "transient.sp"
    deck.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return deck, wavefile, outputs, expected


def check_waveforms(wavefile, outputs, expected, period, voltage, skew1):
    times = []
    waves = {pin: [] for pin in outputs}
    with wavefile.open(encoding="utf-8") as stream:
        header = stream.readline().split()
        if len(header) != len(outputs) + 1 or header[0].lower() != "time":
            raise ValueError("ngspice waveform has missing output columns")
        for line in stream:
            row = [float(value) for value in line.split()]
            if len(row) != len(outputs) + 1 or not all(math.isfinite(value) for value in row):
                raise ValueError("ngspice waveform has incomplete/non-finite samples")
            if times and row[0] < times[-1]:
                raise ValueError("ngspice waveform time is not monotonic")
            times.append(row[0])
            for index, pin in enumerate(outputs):
                waves[pin].append(row[index + 1])
    if len(times) < 2:
        raise ValueError("ngspice waveform has no transient interval")
    failures, reads, bit_reads, worst_access = [], 0, 0, 0.0
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
                if transitions:
                    worst_access = max(worst_access, transitions[-1] - edge)
    return dict(passed=bool(reads) and not failures, reads=reads, bit_reads=bit_reads,
                failures=failures, min_level_margin=minimum_margin,
                tacc_ns=worst_access * 1e9, waveform_stop_ns=times[-1] * 1e9)


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
    args = parser.parse_args(argv)
    destination = args.out_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    netlist, trace = args.netlist.expanduser().resolve(), args.trace.expanduser().resolve()
    try:
        summary_path = args.pex_summary or netlist.parent / "pex_summary.json"
        extraction = json.loads(summary_path.read_text(encoding="utf-8"))
        selected = [corner for corner in extraction["corners"]
                    if Path(corner["netlist"]).resolve() == netlist]
        if len(selected) != 1 or not extraction["completed"]:
            raise ValueError("PEX summary does not identify the completed input netlist")
        digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
        if digest != selected[0]["netlist_sha256"]:
            raise ValueError("PEX netlist changed after extraction; hash does not match summary")
        pdk = (args.pdk_root or Path(os.environ.get("ICS55_PDK_ROOT", REPO.parent / "icsprout55-pdk"))).expanduser().resolve()
        model = pdk / "libs.tech/spice/foundry/ngspice/ICsprout_55LLULP1225_V1p1_hsp.lib"
        if not model.is_file():
            raise ValueError("ngspice foundry model library is missing: " + str(model))
        executable = shutil.which(args.ngspice)
        if executable is None:
            raise ValueError("ngspice executable is missing: " + args.ngspice)
        deck, wavefile, outputs, expected = build_deck(
            netlist, trace, extraction["design"], model, destination,
            args.period, args.vdd, args.temp, args.corner, args.skew1, args.cload, args.tr)
        if wavefile.exists():
            wavefile.unlink()
        command = [executable, "-n", "-D", "ngbehavior=hsa", "-b", str(deck)]
        start = time.monotonic()
        result = subprocess.run(command, cwd=model.parent, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=args.timeout, check=False)
        (destination / "ngspice.log").write_text(result.stdout, encoding="utf-8", errors="replace")
        if result.returncode != 0 or not wavefile.is_file():
            raise ValueError("ngspice produced no complete waveform; see ngspice.log")
        checked = check_waveforms(wavefile, outputs, expected, args.period, args.vdd, args.skew1)
        checked.update(netlist=str(netlist), netlist_sha256=digest,
                       input_gds_sha256=extraction["input_gds_sha256"],
                       trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
                       pex_status=selected[0]["status"], pex_accepted=extraction["accepted"],
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
