#!/usr/bin/env python3
"""Extract per-port Liberty metrics from an ICsprout55 Spectre run.

All times are ns relative to the trace clock; energy is pJ and leakage is mW.
Input slew and output load are recorded as the validity domain for each run.
Usage: charz.py RAWDIR TRACE --period NS --vdd V --stdcell-library LIB
       [--ports 0,1]
"""
import argparse
import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import read_psf
from mklib import normalize_stdcell_library, ports_argument


def trace_cycles(path):
    cycles = {}
    for line in open(path):
        fields = line.split()
        if fields and fields[0] == "C":
            cycles[int(fields[1])] = dict(item.split("=", 1) for item in fields[2:])
    return cycles


def port_access_cycles(cycles, port):
    csb_pin = "csb{}".format(port)
    web_pin = "web{}".format(port)
    role_pin = "role{}".format(port)
    operation_pin = "op{}".format(port)
    role_values = {values[role_pin].upper() for values in cycles.values()
                   if role_pin in values}
    if len(role_values) > 1:
        raise ValueError("trace changes role for port {}".format(port))
    role = next(iter(role_values), None)
    if role not in (None, "RW", "R", "W"):
        raise ValueError("trace has invalid role for port {}".format(port))
    if role is None and any(web_pin in values for values in cycles.values()):
        role = "RW"
    if role is None:
        raise ValueError(
            "trace lacks role{} metadata; regenerate it with tbgen.py".format(port))

    reads, writes = [], []
    for cycle, values in cycles.items():
        if csb_pin not in values:
            raise ValueError("trace lacks {} on cycle {}".format(csb_pin, cycle))
        selected = values[csb_pin] == "0"
        operation = values.get(operation_pin)
        if operation is None:
            if not selected:
                operation = "I"
            elif role == "RW" and web_pin in values:
                operation = "W" if values[web_pin] == "0" else "R"
            else:
                operation = role if role in ("R", "W") else None
        if operation not in ("I", "R", "W"):
            raise ValueError("trace lacks an unambiguous op{} on cycle {}".format(
                port, cycle))
        if (operation == "I") == selected:
            raise ValueError("trace op{} and {} disagree on cycle {}".format(
                port, csb_pin, cycle))
        if role == "RW" and selected:
            if values.get(web_pin) not in ("0", "1"):
                raise ValueError("selected RW operation requires an electrical " + web_pin)
            if operation != ("W" if values[web_pin] == "0" else "R"):
                raise ValueError("trace operation disagrees with electrical " + web_pin)
        if operation == "R":
            if role not in ("RW", "R"):
                raise ValueError("trace reads from write-only port {}".format(port))
            reads.append(cycle)
        elif operation == "W":
            if role not in ("RW", "W"):
                raise ValueError("trace writes through read-only port {}".format(port))
            writes.append(cycle)
    return role, sorted(reads), sorted(writes)


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


def directed_crossings(times, values, threshold):
    """PWL crossings, including exact-threshold samples but excluding touches."""
    crossing_times, directions = [], []
    previous = None
    plateau = None
    for index, value in enumerate(values):
        sign = 1 if value > threshold else -1 if value < threshold else 0
        if sign == 0:
            if previous is not None and plateau is None:
                plateau = times[index]
            continue
        if previous is not None and sign != previous[1]:
            prior_index = previous[0]
            time = (plateau if plateau is not None else times[prior_index] +
                    (threshold - values[prior_index]) / (value - values[prior_index]) *
                    (times[index] - times[prior_index]))
            crossing_times.append(time)
            directions.append(sign)
        previous = (index, sign)
        plateau = None
    return crossing_times, directions


def transitions(times, data, names, threshold, direction=None):
    result = []
    for name in names:
        crossing_times, signs = directed_crossings(times, data[name], threshold)
        result.extend(t for t, sign in zip(crossing_times, signs)
                      if direction is None or sign == direction)
    return sorted(result)


def maximum(values):
    values = list(values)
    return max(values) if values else None


def validate_waveforms(times, data):
    """Reject partial/ambiguous PWL data before measuring any quantity."""
    if (len(times) < 2 or any(type(t) not in (int, float) or not math.isfinite(t) for t in times)
            or any(right <= left for left, right in zip(times, times[1:]))):
        raise ValueError("waveform times must be finite and strictly increasing")
    for name, values in data.items():
        if len(values) != len(times) or any(
                type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise ValueError("incomplete/nonfinite waveform: " + name)


def stable_idle(cycles, cycle, ports):
    """Full-cycle support includes the NEXT half-period input update."""
    records = [cycles.get(k) for k in (cycle - 1, cycle, cycle + 1)]
    if any(record is None for record in records):
        return False
    for port in ports:
        if any(record.get("csb{}".format(port)) != "1" for record in records):
            return False
        pins = {name for record in records for name in record
                if re.fullmatch(r"(?:csb|web|addr|din|wmask|spare_wen){}".format(port), name)}
        if any(len({record.get(pin) for record in records}) != 1 for pin in pins):
            return False
    return True


def next_inputs_held(cycles, cycle, port):
    """Exclude preparation of the next operation inside this full-cycle integral."""
    records = [cycles.get(cycle), cycles.get(cycle + 1)]
    if any(record is None for record in records):
        return False
    pins = {name for record in records for name in record
            if re.fullmatch(r"(?:csb|web|addr|din|wmask|spare_wen){}".format(port), name)}
    return all(records[0].get(pin) == records[1].get(pin) for pin in pins)


def measure_port(times, data, cycles, port, period, voltage, power_current,
                 replica_net=None, *, skew1=0):
    validate_waveforms(times, data)
    if (type(period) not in (int, float) or not math.isfinite(period) or period <= 0
            or type(voltage) not in (int, float) or not math.isfinite(voltage) or voltage <= 0):
        raise ValueError("period and supply voltage must be finite and positive")
    if type(skew1) not in (int, float) or skew1 != 0:
        raise ValueError("measurement windows require explicitly zero port skew")
    period_s = period * 1e-9
    threshold = voltage / 2
    ports = sorted({int(name[3:]) for values in cycles.values() for name in values
                    if name.startswith("csb") and name[3:].isdigit()})
    role, read_cycles, write_cycles = port_access_cycles(cycles, port)
    rise = lambda k: (k + 1) * period_s
    fall = lambda k: rise(k) + period_s / 2

    clock = matching_signals(data, "clk{}".format(port))
    if not clock:
        raise ValueError("missing clk{} waveform".format(port))
    clock_edges = transitions(times, data, clock, threshold, direction=1)
    clock_falls = transitions(times, data, clock, threshold, direction=-1)
    clock_buffers = matching_signals(data, "clk_buf{}".format(port))
    buffer_edges = transitions(times, data, clock_buffers, threshold, direction=1)
    insertion = maximum(min(edge for edge in buffer_edges if edge > edge_in) - edge_in
                        for edge_in in clock_edges
                        if any(edge > edge_in for edge in buffer_edges))

    output_prefix = "dout{}".format(port)
    outputs = sorted(name for name in data
                     if re.match(r"^{}(?:_|\[|\.|$)".format(output_prefix), name))
    if not outputs and port == 0 and role in ("RW", "R"):
        outputs = sorted(name for name in data if name.startswith("dout"))
    if read_cycles and not outputs:
        raise ValueError("missing dout{} waveforms".format(port))

    access = []
    transitions_10_90 = []
    directional = {name: [] for name in ("cell_rise", "cell_fall", "rise_transition", "fall_transition")}
    raw_timing = []
    hold_after_rise = []
    for cycle in read_cycles:
        read_end = rise(cycle) + period_s
        if fall(cycle) < times[0] or read_end > times[-1]:
            raise ValueError("read timing window outside waveform support")
        references = [edge for edge in clock_falls if fall(cycle) - period_s / 4 <= edge < fall(cycle) + period_s / 4]
        if len(references) != 1:
            continue
        reference = references[0]
        for name in outputs:
            mids, signs = directed_crossings(times, data[name], threshold)
            for crossing, sign in zip(mids, signs):
                if not reference <= crossing < read_end:
                    continue
                delay = crossing - reference
                access.append(delay)
                delay_key = "cell_rise" if sign == 1 else "cell_fall"
                transition_key = "rise_transition" if sign == 1 else "fall_transition"
                directional[delay_key].append(delay * 1e9)
                first_threshold, last_threshold = ((0.1, 0.9) if sign == 1 else (0.9, 0.1))
                first = transitions(times, data, [name], first_threshold * voltage, sign)
                last = transitions(times, data, [name], last_threshold * voltage, sign)
                before = [t for t in first if reference <= t <= crossing]
                after = [t for t in last if crossing <= t < read_end]
                transition = None
                if before and after:
                    transition = (min(after) - max(before)) * 1e9
                    transitions_10_90.append(transition * 1e-9)
                    directional[transition_key].append(transition)
                raw_timing.append({"cycle": cycle, "signal": name,
                                   "direction": "rise" if sign == 1 else "fall",
                                   "clock_fall_s": reference,
                                   "delay_ns": delay * 1e9, "transition_ns": transition})
            after = [time for time in mids if read_end <= time < read_end + period_s / 2]
            if after:
                hold_after_rise.append(min(after) - read_end)

    enable_end = None
    if role in ("RW", "W"):
        enable_nets = matching_signals(data, "w_en{}".format(port))
        enable_falls = transitions(times, data, enable_nets, threshold, direction=-1)
        enable_end = maximum(
            min(edge for edge in enable_falls if edge > fall(cycle)) - fall(cycle)
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
        if start < times[0] or end > times[-1] or end <= start:
            raise ValueError("energy window outside complete waveform support")
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

    other_ports = [other for other in ports if other != port]
    idle_windows = [k for k in sorted(cycles) if k >= 3 and stable_idle(cycles, k, ports)]
    read_windows = [k for k in read_cycles if stable_idle(cycles, k, other_ports)
                    and next_inputs_held(cycles, k, port)]
    write_windows = [k for k in write_cycles if stable_idle(cycles, k, other_ports)
                     and next_inputs_held(cycles, k, port)]
    raw_energy = {operation: [{"cycle": k, "start_s": rise(k), "end_s": rise(k) + period_s,
                              "energy_pj": energy(rise(k), rise(k) + period_s) * 1e12}
                             for k in windows]
                  for operation, windows in (("read", read_windows), ("write", write_windows),
                                             ("idle", idle_windows))}
    read_energy = [item["energy_pj"] for item in raw_energy["read"]]
    write_energy = [item["energy_pj"] for item in raw_energy["write"]]
    result = {
        "role": role,
        "ins_rise": None if insertion is None else insertion * 1e9,
        "dout_fall": None if not access else max(access) * 1e9,
        "dout_tran": None if not transitions_10_90 else max(transitions_10_90) * 1e9,
        "wen_end": None if enable_end is None else enable_end * 1e9,
        "chain_reset": None if not reset else max(reset) * 1e9,
        "dout_hold_after_rise": None if not hold_after_rise else min(hold_after_rise) * 1e9,
        "leak_mw": None,
        "e_idle_pj": minimum_energy(raw_energy["idle"]),
        "e_read_pj": None if not read_energy else max(read_energy),
        "e_write_pj": None if not write_energy else max(write_energy),
        "raw_timing": raw_timing,
        "raw_energy": raw_energy,
        "energy_scope": "whole-macro signed supply energy; includes background clocks and output loads",
        "idle_scope": "settled clock-active idle, not static leakage",
        "polarity_coverage": {name: len(values) for name, values in directional.items()},
        "read_cycles": len(read_cycles),
        "write_cycles": len(write_cycles),
    }
    result.update({name: maximum(values) for name, values in directional.items()})
    result["output_coverage"] = {
        name: {direction: sum(item["signal"] == name and item["direction"] == direction
                              and item["transition_ns"] is not None for item in raw_timing)
               for direction in ("rise", "fall")}
        for name in outputs}
    for direction, keys in (("rise", ("cell_rise", "rise_transition")),
                            ("fall", ("cell_fall", "fall_transition"))):
        if not outputs or any(result["output_coverage"][name][direction] == 0 for name in outputs):
            result.update(dict.fromkeys(keys))
    return result


def minimum_energy(samples):
    return min(item["energy_pj"] for item in samples) if samples else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw")
    parser.add_argument("trace")
    parser.add_argument("--period", type=float, required=True)
    parser.add_argument("--vdd", type=float, required=True)
    parser.add_argument("--temp", type=float, default=25)
    parser.add_argument("--corner", default="tt")
    parser.add_argument("--stdcell-library", required=True)
    parser.add_argument("--ports", type=ports_argument,
                        help="comma-separated port indices; defaults to ports in the trace")
    parser.add_argument("--load-ff", type=float, default=5.0)
    parser.add_argument("--input-slew-ns", type=float, default=0.03)
    parser.add_argument("--power-current", default="VDD:p")
    parser.add_argument("--replica-net", help="explicit rbl_bl_delay waveform for a single-port run")
    args = parser.parse_args()
    try:
        stdcell_library = normalize_stdcell_library(args.stdcell_library)
    except ValueError as error:
        parser.error(str(error))
    files = sorted(glob.glob(os.path.join(args.raw, "*.tran*"))) if os.path.isdir(args.raw) else [args.raw]
    if len(files) != 1:
        parser.error("expected exactly one transient waveform, found {}".format(len(files)))
    times, data = read_psf(files[0])
    cycles = trace_cycles(args.trace)
    ports = args.ports
    if ports is None:
        ports = sorted({int(name[3:]) for values in cycles.values() for name in values
                        if name.startswith("csb") and name[3:].isdigit()})
    if not ports:
        parser.error("trace contains no port control cycles")
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
        result.update({"schema_version": 3, "port": ports[0], "domain": domain})
    else:
        result = {"schema_version": 3, "ports": per_port, "domain": domain}
    result["stdcell_library"] = stdcell_library
    result.update(functional=None, complete_sta=False, signoff=False)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
