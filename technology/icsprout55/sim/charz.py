#!/usr/bin/env python3
"""Extract per-port Liberty metrics from an ICsprout55 Spectre run.

All times are ns relative to the trace clock; energy is pJ and leakage is mW.
Input slew and output load are recorded as the validity domain for each run.
Usage: charz.py RAWDIR TRACE --period NS --vdd V [--ports 0,1]
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import crossings, read_psf
from tanalyze import _dir


def trace_cycles(path):
    cycles = {}
    for line in open(path):
        fields = line.split()
        if fields and fields[0] == "C":
            cycles[int(fields[1])] = dict(item.split("=", 1) for item in fields[2:])
    return cycles


def matching_signals(data, suffix):
    return sorted(name for name in data if name == suffix or name.endswith(suffix))


def port_control_signals(data, name, port, explicit=None):
    if explicit:
        return [signal for signal in data if signal == explicit or signal.endswith(explicit)]
    matches = matching_signals(data, name)
    if len(matches) <= 1:
        return matches
    port_matches = [signal for signal in matches
                    if re.search(r"control{}(?![0-9])".format(port), signal)]
    if port_matches:
        return port_matches
    raise ValueError("ambiguous {} nets for port {}; pass --replica-net".format(name, port))


def transitions(times, data, names, threshold, direction=None):
    result = []
    for name in names:
        crossing_times, signs = _dir(times, data[name], threshold)
        result.extend(t for t, sign in zip(crossing_times, signs)
                      if direction is None or sign == direction)
    return sorted(result)


def maximum(values):
    values = list(values)
    return max(values) if values else None


def measure_port(times, data, cycles, port, period, voltage, power_current,
                 replica_net=None):
    period_s = period * 1e-9
    threshold = voltage / 2
    ports = sorted({int(name[3:]) for values in cycles.values() for name in values
                    if name.startswith("csb") and name[3:].isdigit()})
    read_cycles = sorted(k for k, values in cycles.items()
                         if values.get("csb{}".format(port)) == "0" and
                         values.get("web{}".format(port)) == "1")
    write_cycles = sorted(k for k, values in cycles.items()
                          if values.get("csb{}".format(port)) == "0" and
                          values.get("web{}".format(port)) == "0")
    rise = lambda k: (k + 1) * period_s
    fall = lambda k: rise(k) + period_s / 2

    clock = matching_signals(data, "clk{}".format(port))
    if not clock:
        raise ValueError("missing clk{} waveform".format(port))
    clock_edges = transitions(times, data, clock, threshold, direction=1)
    clock_buffers = matching_signals(data, "clk_buf{}".format(port))
    buffer_edges = transitions(times, data, clock_buffers, threshold, direction=1)
    insertion = maximum(min(edge for edge in buffer_edges if edge > edge_in) - edge_in
                        for edge_in in clock_edges
                        if any(edge > edge_in for edge in buffer_edges))

    output_prefix = "dout{}".format(port)
    outputs = sorted(name for name in data
                     if re.match(r"^{}(?:_|\[|\.|$)".format(output_prefix), name))
    if not outputs and port == 0:
        outputs = sorted(name for name in data if name.startswith("dout"))
    if read_cycles and not outputs:
        raise ValueError("missing dout{} waveforms".format(port))

    access = []
    transitions_10_90 = []
    hold_after_rise = []
    for cycle in read_cycles:
        read_end = rise(cycle) + period_s
        for name in outputs:
            mids, signs = _dir(times, data[name], threshold)
            phase_crossings = [time for time in mids if fall(cycle) <= time < read_end]
            if phase_crossings:
                access.append(phase_crossings[-1] - fall(cycle))
            low = crossings(times, data[name], 0.1 * voltage)
            high = crossings(times, data[name], 0.9 * voltage)
            for crossing in phase_crossings:
                near_low = [time for time in low if abs(time - crossing) < period_s / 4]
                near_high = [time for time in high if abs(time - crossing) < period_s / 4]
                if near_low and near_high:
                    transitions_10_90.append(abs(min(near_high, key=lambda x: abs(x - crossing)) -
                                                 min(near_low, key=lambda x: abs(x - crossing))))
            after = [time for time in mids if read_end <= time < read_end + period_s / 2]
            if after:
                hold_after_rise.append(min(after) - read_end)

    enable_nets = matching_signals(data, "w_en{}".format(port))
    enable_falls = transitions(times, data, enable_nets, threshold, direction=-1)
    enable_end = maximum(min(edge for edge in enable_falls if edge > fall(cycle)) - fall(cycle)
                         for cycle in write_cycles
                         if any(edge > fall(cycle) for edge in enable_falls))

    delay_nets = port_control_signals(data, "rbl_bl_delay", port, replica_net)
    delay_falls = transitions(times, data, delay_nets, threshold, direction=-1)
    reset = []
    for cycle in read_cycles + write_cycles:
        edge = rise(cycle) + period_s
        reset_edges = [time for time in delay_falls if edge < time < edge + period_s]
        if reset_edges:
            reset.append(min(reset_edges) - edge)

    if power_current not in data:
        raise ValueError("missing supply-current waveform {}".format(power_current))
    current = data[power_current]

    def energy(start, end):
        total = 0.0
        for i in range(1, len(times)):
            left = max(start, times[i - 1])
            right = min(end, times[i])
            if left >= right:
                continue
            interval = times[i] - times[i - 1]
            current_left = current[i - 1] + (current[i] - current[i - 1]) * (
                (left - times[i - 1]) / interval)
            current_right = current[i - 1] + (current[i] - current[i - 1]) * (
                (right - times[i - 1]) / interval)
            total -= 0.5 * (current_left + current_right) * voltage * (right - left)
        return total

    def other_ports_idle(cycle):
        values = cycles[cycle]
        return all(values.get("csb{}".format(other)) == "1"
                   for other in ports if other != port)

    all_idle = [k for k, values in cycles.items()
                if k >= 3 and all(values.get("csb{}".format(p)) == "1" for p in ports)]
    idle_windows = [k for k in all_idle
                    if all(cycles.get(k - 1, {}).get("csb{}".format(p)) == "1" for p in ports)]
    read_energy = [energy(rise(k), rise(k) + period_s)
                   for k in read_cycles if other_ports_idle(k)]
    write_energy = [energy(rise(k), rise(k) + period_s)
                    for k in write_cycles if other_ports_idle(k)]
    result = {
        "ins_rise": None if insertion is None else insertion * 1e9,
        "dout_fall": None if not access else max(access) * 1e9,
        "dout_tran": None if not transitions_10_90 else max(transitions_10_90) * 1e9,
        "wen_end": None if enable_end is None else enable_end * 1e9,
        "chain_reset": None if not reset else max(reset) * 1e9,
        "dout_hold_after_rise": None if not hold_after_rise else min(hold_after_rise) * 1e9,
        "leak_mw": energy(rise(0) + 0.6 * period_s, rise(0) + 0.95 * period_s) /
                   (0.35 * period_s) * 1e3,
        "e_idle_pj": None if not idle_windows else min(
            energy(rise(k), rise(k) + period_s) for k in idle_windows) * 1e12,
        "e_read_pj": None if not read_energy else max(read_energy) * 1e12,
        "e_write_pj": None if not write_energy else max(write_energy) * 1e12,
        "read_cycles": len(read_cycles),
        "write_cycles": len(write_cycles),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw")
    parser.add_argument("trace")
    parser.add_argument("--period", type=float, required=True)
    parser.add_argument("--vdd", type=float, required=True)
    parser.add_argument("--temp", type=float, default=25)
    parser.add_argument("--corner", default="tt")
    parser.add_argument("--ports", default="0", help="comma-separated read/write port indices")
    parser.add_argument("--load-ff", type=float, default=5.0)
    parser.add_argument("--input-slew-ns", type=float, default=0.03)
    parser.add_argument("--power-current", default="VDD:p")
    parser.add_argument("--replica-net", help="explicit rbl_bl_delay waveform for a single-port run")
    args = parser.parse_args()
    ports = [int(value) for value in args.ports.split(",")]
    if not ports or len(set(ports)) != len(ports) or min(ports) < 0:
        parser.error("--ports must contain distinct non-negative indices")
    files = sorted(glob.glob(os.path.join(args.raw, "*.tran*"))) if os.path.isdir(args.raw) else [args.raw]
    if len(files) != 1:
        parser.error("expected exactly one transient waveform, found {}".format(len(files)))
    times, data = read_psf(files[0])
    cycles = trace_cycles(args.trace)
    try:
        per_port = {
            str(port): measure_port(times, data, cycles, port, args.period, args.vdd,
                                   args.power_current, args.replica_net)
            for port in ports
        }
    except ValueError as error:
        parser.error(str(error))
    domain = {
        "period_ns": args.period,
        "load_ff": args.load_ff,
        "input_slew_ns": args.input_slew_ns,
        "corner": args.corner,
        "vdd": args.vdd,
        "temp": args.temp,
    }
    if len(ports) == 1:
        result = per_port[str(ports[0])]
        result.update({"schema_version": 2, "port": ports[0], "domain": domain})
    else:
        result = {"schema_version": 2, "ports": per_port, "domain": domain}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
