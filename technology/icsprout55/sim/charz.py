#!/usr/bin/env python3
"""Extract per-port Liberty metrics from an ICsprout55 Spectre run.

All times are ns relative to the trace clock; energy is pJ and leakage is mW.
Input slew and output load are recorded as the validity domain for each run.
Usage: charz.py RAWDIR TRACE --period NS --vdd V --stdcell-library LIB
       [--ports 0,1]
"""
import argparse
import bisect
import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import read_psf
from mklib import normalize_stdcell_library, ports_argument


def trace_records(path):
    """Read the electrical input trace and the expected read-data identities."""
    cycles, expected = {}, {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "C":
                cycles[int(fields[1])] = dict(item.split("=", 1) for item in fields[2:])
            elif fields[0] == "E":
                bus, bits = fields[2].split("=", 1)
                record = expected.setdefault(int(fields[1]), {})
                if bus in record:
                    raise ValueError("trace repeats expected output {} on cycle {}".format(
                        bus, fields[1]))
                record[bus] = bits
    return cycles, expected


def trace_cycles(path):
    return trace_records(path)[0]


def _sample_value(times, values, point):
    if point < times[0] or point > times[-1]:
        return None
    right = bisect.bisect_right(times, point)
    left = right - 1
    if times[left] == point or right == len(times):
        return values[left]
    fraction = (point - times[left]) / (times[right] - times[left])
    return values[left] + fraction * (values[right] - values[left])


def _output_bit(name, port, width):
    match = re.fullmatch(r"dout{}(?:\[(\d+)\]|_(\d+)|\.(\d+))?".format(port), name)
    if match is None:
        return None
    indexes = [value for value in match.groups() if value is not None]
    if not indexes:
        return 0 if width == 1 else None
    return int(indexes[0])


def discover_read_arcs(times, data, expected, read_cycles, outputs, port,
                       period, voltage, clock_signal, domain=None, functional=None):
    """Associate output transitions with expected read bits and actual clock edges."""
    threshold = voltage / 2
    clock_values, clock_signs = directed_crossings(times, data[clock_signal], threshold)
    clock_edges = []
    for edge, sign in zip(clock_values, clock_signs):
        clock_edges.append((edge, "rising" if sign > 0 else "falling"))
    clock_edges.sort()
    edge_times = [edge for edge, _ in clock_edges]
    edge_half_window = period / 2 + max(period * .02, 2e-12)
    crossings = {name: directed_crossings(times, data[name], threshold)
                 for name in outputs}
    events, samples, holds = [], [], []
    phase_events = {"rising": set(), "falling": set()}
    phase_polarity_events = {
        phase: {"rise": set(), "fall": set()} for phase in phase_events}
    for cycle in read_cycles:
        bits = expected.get(cycle, {}).get("dout{}".format(port))
        sample_time = (cycle + 2) * period - 10e-12
        if not isinstance(bits, str) or not bits or re.fullmatch("[01]+", bits) is None:
            samples.append(dict(cycle=cycle, status="missing_or_unknown_expected_read"))
            continue
        for name in outputs:
            bit = _output_bit(name, port, len(bits))
            if bit is None or bit >= len(bits):
                samples.append(dict(cycle=cycle, signal=name, status="unmapped_expected_output_bit"))
                continue
            expected_bit = int(bits[-1 - bit])
            expected_direction = 1 if expected_bit else -1
            observed = _sample_value(times, data[name], sample_time)
            margin = None if observed is None else (
                observed / voltage if expected_bit else 1 - observed / voltage)
            sample_pass = margin is not None and margin >= .8
            mids, signs = crossings[name]
            in_window = [(index, crossing, sign) for index, (crossing, sign)
                         in enumerate(zip(mids, signs))
                         if (cycle + 1) * period <= crossing < sample_time]
            last_crossing = in_window[-1][1] if in_window else None
            signal_events = []
            for _, crossing, sign in in_window:
                edge_index = bisect.bisect_right(edge_times, crossing) - 1
                associated = clock_edges[edge_index] if edge_index >= 0 else None
                if associated is not None and crossing - associated[0] > edge_half_window:
                    associated = None
                phase = None if associated is None else associated[1]
                direction = "rise" if sign > 0 else "fall"
                matches = sign == expected_direction
                stable = crossing == last_crossing and matches
                event = dict(cycle=cycle, signal=name, bit=bit, expected=expected_bit,
                             direction=direction, crossing_ns=crossing * 1e9,
                             associated_edge=None if associated is None else phase,
                             edge_ns=None if associated is None else associated[0] * 1e9,
                             delay_ns=None if associated is None else
                             (crossing - associated[0]) * 1e9,
                             expected_direction=("rise" if expected_bit else "fall"),
                             sample_ns=sample_time * 1e9,
                             sample_voltage_v=observed, sample_pass=sample_pass,
                             matches_expected_direction=matches,
                             last_crossing_before_sample=stable,
                             valid_data_transition=False)
                if not matches:
                    event["classification"] = "opposes_expected_read_data"
                elif not sample_pass:
                    event["classification"] = "read_sample_does_not_match_expected_data"
                elif not stable:
                    event["classification"] = "not_final_transition_to_expected_data"
                elif phase is None:
                    event["classification"] = "no_associated_clock_edge"
                else:
                    event["classification"] = "phase_candidate"
                    phase_events[phase].add((cycle, name))
                    phase_polarity_events[phase][direction].add((cycle, name))
                events.append(event)
                signal_events.append(event)
            samples.append(dict(cycle=cycle, signal=name, bit=bit, expected=expected_bit,
                                sample_ns=sample_time * 1e9, sample_voltage_v=observed,
                                sample_pass=sample_pass,
                                transition_count=len(signal_events),
                                target_transition_count=sum(event["matches_expected_direction"]
                                                            for event in signal_events),
                                status=("sampled_expected_data" if sample_pass else
                                        "read_sample_mismatch")))

        edge_target = (cycle + 2) * period
        rising_targets = [edge for edge, phase in clock_edges
                          if phase == "rising" and
                          abs(edge - edge_target) <= period / 4]
        next_rise = min(rising_targets, key=lambda edge: abs(edge - edge_target)) \
            if rising_targets else None
        for name in outputs:
            bit = _output_bit(name, port, len(bits))
            if bit is None or bit >= len(bits):
                continue
            expected_bit = int(bits[-1 - bit])
            if next_rise is None:
                holds.append(dict(cycle=cycle, signal=name, expected=expected_bit,
                                  status="missing_next_rising_edge"))
                continue
            edge_value = _sample_value(times, data[name], next_rise)
            edge_margin = None if edge_value is None else (
                edge_value / voltage if expected_bit else 1 - edge_value / voltage)
            passed = edge_margin is not None and edge_margin >= .8
            mids, signs = crossings[name]
            departure = next((crossing for crossing, sign in zip(mids, signs)
                              if crossing >= next_rise and
                              crossing < next_rise + period / 2 and
                              sign != (1 if expected_bit else -1)), None)
            holds.append(dict(cycle=cycle, signal=name, expected=expected_bit,
                              next_rising_edge_ns=next_rise * 1e9,
                              edge_voltage_v=edge_value, passed=passed,
                              hold_ns=None if departure is None else
                              (departure - next_rise) * 1e9,
                              hold_censored=departure is None,
                              status="held_at_next_rising_edge" if passed else
                              "read_data_not_valid_at_next_rising_edge"))

    observed_directions = {direction for phase in phase_polarity_events.values()
                           for direction, items in phase.items() if items}
    zero_data_phases = [phase for phase, polarities in phase_polarity_events.items()
                        if polarities["fall"]]
    selected_phase = zero_data_phases[0] if len(zero_data_phases) == 1 else None
    if selected_phase is not None:
        for event in events:
            if event["classification"] == "phase_candidate":
                if event["associated_edge"] != selected_phase:
                    event["classification"] = "phase_mismatch_not_credited_as_data"
                elif functional is False:
                    event["classification"] = "functional_failure_not_credited_as_data"
                else:
                    event["classification"] = "valid_expected_read_transition"
                    event["valid_data_transition"] = True
    else:
        for event in events:
            if event["classification"] == "phase_candidate":
                event["classification"] = (
                    "clock_phase_no_expected_zero_anchor_not_credited"
                    if not zero_data_phases else
                    "clock_phase_ambiguous_not_credited_as_data")

    valid_arcs, precharge_candidates = [], []
    for event in events:
        if event["valid_data_transition"]:
            valid_arcs.append(event)
        elif (event["direction"] == "rise" and
              (not event["matches_expected_direction"] or
               event["associated_edge"] != selected_phase)):
            event["pre_access_or_phase_mismatch_candidate"] = True
            precharge_candidates.append(event)
    threshold_cache = {}

    def threshold_crossings(name, level, direction):
        key = (name, level, direction)
        if key not in threshold_cache:
            crossing_times, signs = directed_crossings(times, data[name], level)
            threshold_cache[key] = [time for time, sign in zip(crossing_times, signs)
                                    if sign == direction]
        return threshold_cache[key]

    for event in valid_arcs:
        sign = 1 if event["direction"] == "rise" else -1
        first_level, last_level = ((.1 * voltage, .9 * voltage) if sign > 0
                                   else (.9 * voltage, .1 * voltage))
        edge = event["edge_ns"] * 1e-9
        crossing = event["crossing_ns"] * 1e-9
        sample = event["sample_ns"] * 1e-9
        first = [time for time in threshold_crossings(event["signal"], first_level, sign)
                 if edge <= time <= crossing]
        last = [time for time in threshold_crossings(event["signal"], last_level, sign)
                if crossing <= time < sample]
        event["transition_ns"] = (min(last) - max(first)) * 1e9 if first and last else None
        event["delay_ns"] = (crossing - edge) * 1e9
    domain = {} if domain is None else dict(domain)
    required_domain = ("period_ns", "load_ff", "input_slew_ns", "corner", "vdd", "temp")
    read_hold_failures = [item for item in holds if item["status"] != "held_at_next_rising_edge"]
    coverage = {
        name: {direction: sum(item["signal"] == name and item["direction"] == direction
                              and item["transition_ns"] is not None for item in valid_arcs)
               for direction in ("rise", "fall")}
        for name in outputs}
    return dict(
        phase_status=("unique_phase_from_expected_zero_data" if selected_phase is not None else
                      "ambiguous_expected_zero_phase" if zero_data_phases else
                      "missing_expected_zero_phase_anchor"),
        selected_clock_phase=selected_phase,
        phase_anchor_candidates=zero_data_phases,
        phase_evidence={
            phase: dict(read_signal_transitions=len(items),
                        rise_transitions=len(phase_polarity_events[phase]["rise"]),
                        fall_transitions=len(phase_polarity_events[phase]["fall"]),
                        expected_zero_fall_anchor=bool(phase_polarity_events[phase]["fall"]))
            for phase, items in phase_events.items()},
        observed_data_directions=sorted(observed_directions),
        phase_inference="unique observed clock phase of expected-zero output falls anchors the read phase; other output polarities are credited only at that phase, and missing polarity is never inferred",
        output_coverage=coverage,
        valid_domain=dict(domain=domain,
                          missing_fields=[key for key in required_domain if key not in domain],
                          scope="exact waveform domain and E-trace data identities only; no continuous-domain or STA claim"),
        functional_pass=functional,
        expected_read_samples=samples,
        transition_events=events,
        valid_data_arcs=valid_arcs,
        precharge_or_phase_mismatch_candidates=precharge_candidates,
        read_hold=dict(scope="expected data sampled at the next observed rising clock edge; later departures are censored waveform observations",
                       samples=holds, failures=read_hold_failures,
                       passed=bool(holds) and not read_hold_failures),
        counts=dict(expected_samples=len(samples),
                    output_events=len(events), valid_data_arcs=len(valid_arcs),
                    rejected_precharge_or_phase_mismatch=len(precharge_candidates),
                    held_edge_samples=sum(item.get("passed") is True for item in holds),
                    failed_hold_samples=len(read_hold_failures)))


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
                 replica_net=None, *, skew1=0, expected=None, domain=None,
                 functional=None):
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
    arc_discovery = None
    if expected is not None:
        clock_signal = "clk{}".format(port) if "clk{}".format(port) in data else (
            clock[0] if len(clock) == 1 else None)
        if clock_signal is not None:
            arc_discovery = discover_read_arcs(
                times, data, expected, read_cycles, outputs, port, period_s,
                voltage, clock_signal, domain=domain, functional=functional)
        else:
            arc_discovery = dict(
                phase_status="missing_or_ambiguous_clock_signal",
                selected_clock_phase=None, phase_evidence={},
                output_coverage={name: {"rise": 0, "fall": 0} for name in outputs},
                valid_domain=dict(domain={} if domain is None else dict(domain),
                                  missing_fields=[key for key in
                                      ("period_ns", "load_ff", "input_slew_ns",
                                       "corner", "vdd", "temp")
                                      if domain is None or key not in domain],
                                  scope="unresolved clock identity; no data arcs credited"),
                functional_pass=functional, expected_read_samples=[],
                transition_events=[], valid_data_arcs=[],
                precharge_or_phase_mismatch_candidates=[],
                read_hold=dict(samples=[], failures=[], passed=False,
                               status="unresolved_clock_identity"))

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
    if arc_discovery is not None:
        directional = {name: [] for name in
                       ("cell_rise", "cell_fall", "rise_transition", "fall_transition")}
        for event in arc_discovery["valid_data_arcs"]:
            if event["transition_ns"] is None:
                continue
            direction = "rise" if event["direction"] == "rise" else "fall"
            directional["cell_" + direction].append(event["delay_ns"])
            directional[direction + "_transition"].append(event["transition_ns"])
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
    if arc_discovery is not None:
        result["arc_discovery"] = arc_discovery
        result["output_coverage"] = arc_discovery["output_coverage"]
    if arc_discovery is None:
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
    cycles, expected = trace_records(args.trace)
    ports = args.ports
    if ports is None:
        ports = sorted({int(name[3:]) for values in cycles.values() for name in values
                        if name.startswith("csb") and name[3:].isdigit()})
    if not ports:
        parser.error("trace contains no port control cycles")
    domain = {
        "period_ns": args.period,
        "load_ff": args.load_ff,
        "input_slew_ns": args.input_slew_ns,
        "corner": args.corner,
        "vdd": args.vdd,
        "temp": args.temp,
    }
    try:
        per_port = {
            str(port): measure_port(
                times, data, cycles, port, args.period, args.vdd,
                args.power_current, args.replica_net, expected=expected,
                domain=domain)
            for port in ports
        }
    except ValueError as error:
        parser.error(str(error))
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
