#!/usr/bin/env python3
"""Generate and execute sampled ICsprout55 macro timing-boundary trials.

Trial JSON selects electrical stimuli only. Limits are never read from the JSON:
all verdicts come from the generated ngspice deck and its functional waveform check.
"""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
SIM = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))
from compiler.architecture.contracts import (Registry, canonical_bytes, load_json,
                                            number, validate_request)
from technology.icsprout55.sim import architecture as native
from charz import directed_crossings
from mkscs import read_trace, subckt_pins
from ngspice_pex import build_deck, check_waveforms
from pex_debug import readwrite_target, select_probes

SCHEMA = "openram-ngspice-boundary-experiments/1"
_REQUIRED_DOMAIN = ("period_ns", "load_ff", "input_slew_ns", "corner", "vdd", "temp")


def _pin_spec(pin, roles, parameters):
    match = re.fullmatch(r"(addr|csb|web)(\d+)(?:\[(\d+)\])?", pin)
    if match is None:
        raise ValueError("boundary pin must be addrN, addrN[bit], csbN or webN")
    field, port_text, bit_text = match.groups()
    port = int(port_text)
    if port >= len(roles) or roles[port] not in ("RW", "R"):
        raise ValueError("boundary pin is not on a read-capable port")
    if field == "web" and roles[port] != "RW":
        raise ValueError("WEB timing requires a read/write port")
    if field == "addr":
        width = (parameters["num_words"] - 1).bit_length()
        bit = 0 if bit_text is None else int(bit_text)
        if width == 0 or bit >= width:
            raise ValueError("address timing bit is outside the macro address width")
        return dict(field=field, port=port, bit=bit, pin="{}{}[{}]".format(field, port, bit))
    if bit_text is not None:
        raise ValueError("CSB and WEB are scalar timing pins")
    return dict(field=field, port=port, bit=None, pin="{}{}".format(field, port))


def _candidate_and_trial(raw, base):
    if not isinstance(raw, dict):
        raise ValueError("each boundary trial must be a JSON object")
    kind = raw.get("kind")
    env = deepcopy(base["environment"])
    for key in ("period_ns", "load_ff", "slew_ns"):
        if key in raw:
            value = number(raw[key], key, 0, strict=True)
            env["period_ns" if key == "period_ns" else
                "cload_ff" if key == "load_ff" else "slew_ns"] = value
    parameters = base["parameters"]
    roles = base["access_contract"]["ports"]
    result = {"kind": kind, "id": raw.get("id")}
    if kind in ("setup", "hold"):
        allowed = {"kind", "id", "pin", "setup_ns", "hold_ns", "period_ns", "load_ff", "slew_ns"}
        pin = raw.get("pin")
        if not isinstance(pin, str):
            raise ValueError("input boundary trial requires an explicit pin")
        pin_data = _pin_spec(pin, roles, parameters)
        if "setup_ns" not in raw or "hold_ns" not in raw:
            raise ValueError("input boundary trial requires explicit setup_ns and hold_ns")
        setup = number(raw["setup_ns"], "setup_ns", 0)
        hold = number(raw["hold_ns"], "hold_ns", 0)
        axis = "setup_ns" if kind == "setup" else "hold_ns"
        result.update(pin=pin_data["pin"], pin_field=pin_data["field"],
                      port=pin_data["port"], bit=pin_data["bit"],
                      setup_ns=setup, hold_ns=hold, axis=axis,
                      axis_value=setup if axis == "setup_ns" else hold)
        condition = dict(kind=kind, pin=pin_data["pin"],
                         setup_ns=None if axis == "setup_ns" else setup,
                         hold_ns=None if axis == "hold_ns" else hold)
    elif kind == "pulse":
        allowed = {"kind", "id", "clock_port", "clock_high_ns", "clock_low_ns", "axis",
                   "period_ns", "load_ff", "slew_ns"}
        port = raw.get("clock_port")
        if type(port) is not int or port < 0 or port >= len(roles) or roles[port] not in ("RW", "R"):
            raise ValueError("clock-pulse trial requires a read-capable integer clock_port")
        if "clock_high_ns" not in raw or "clock_low_ns" not in raw:
            raise ValueError("clock-pulse trial requires explicit high and low times")
        high = number(raw["clock_high_ns"], "clock_high_ns", 0, strict=True)
        low = number(raw["clock_low_ns"], "clock_low_ns", 0, strict=True)
        period = high + low
        if "period_ns" in raw and not math.isclose(raw["period_ns"], period, rel_tol=0, abs_tol=1e-9):
            raise ValueError("clock high and low times must sum to period_ns")
        if high <= env["slew_ns"] or low <= env["slew_ns"]:
            raise ValueError("clock high/low plateau must exceed the generated edge ramp")
        env["period_ns"] = period
        axis = raw.get("axis")
        if axis not in ("clock_high_ns", "clock_low_ns"):
            raise ValueError("pulse trial axis must be clock_high_ns or clock_low_ns")
        result.update(clock_port=port, clock_high_ns=high, clock_low_ns=low,
                      axis=axis, axis_value=high if axis == "clock_high_ns" else low)
        condition = dict(kind=kind, clock_port=port, axis=axis,
                         pulse_relation="clock_high_ns + clock_low_ns = period_ns")
    elif kind == "domain":
        allowed = {"kind", "id", "load_ff", "slew_ns"}
        changed = [key for key in ("load_ff", "slew_ns") if key in raw]
        if len(changed) != 1:
            raise ValueError("domain trial must vary exactly one of load_ff or slew_ns")
        axis = changed[0]
        value = number(raw[axis], axis, 0, strict=True)
        result.update(axis=axis, axis_value=value)
        condition = dict(kind=kind,
                         load_ff=None if axis == "load_ff" else env["cload_ff"],
                         input_slew_ns=None if axis == "slew_ns" else env["slew_ns"])
    else:
        raise ValueError("trial kind must be setup, hold, pulse or domain")
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError("unknown {} trial fields: {}".format(kind, ", ".join(sorted(unknown))))
    if result["id"] is not None and (not isinstance(result["id"], str) or not result["id"]):
        raise ValueError("trial id must be a nonempty string")
    if kind == "pulse":
        if not math.isclose(env["period_ns"], result["clock_high_ns"] + result["clock_low_ns"],
                            rel_tol=0, abs_tol=1e-9):
            raise ValueError("clock period does not match high plus low pulse")
    condition["environment"] = dict(
        period_ns=env["period_ns"],
        load_ff=None if kind == "domain" and axis == "load_ff" else env["cload_ff"],
        input_slew_ns=None if kind == "domain" and axis == "slew_ns" else env["slew_ns"],
        corner=env["corner"], vdd=env["voltage_v"], temp=env["temperature_c"])
    result["domain"] = dict(period_ns=env["period_ns"], load_ff=env["cload_ff"],
                            input_slew_ns=env["slew_ns"], corner=env["corner"],
                            vdd=env["voltage_v"], temp=env["temperature_c"])
    result["condition"] = condition
    candidate = dict(base)
    candidate["environment"] = env
    return candidate, result


def _set_pin_value(record, pin, value):
    field, bit = pin["field"], pin["bit"]
    key = "{}{}".format(field, pin["port"])
    if field != "addr":
        record[key] = str(value)
        return
    values = list(record[key])
    values[-1 - bit] = str(value)
    record[key] = "".join(values)


def _pin_value(record, pin):
    key = "{}{}".format(pin["field"], pin["port"])
    if pin["field"] != "addr":
        return int(record[key])
    return int(record[key][-1 - pin["bit"]])


def _idle_record(record):
    idle = dict(record)
    for name in tuple(idle):
        if re.fullmatch(r"csb\d+", name) or re.fullmatch(r"web\d+", name):
            idle[name] = "1"
        elif re.fullmatch(r"op\d+", name):
            idle[name] = "I"
    return idle


def _insert_input_boundary(cycles, expected, target_cycle, pin):
    if target_cycle <= min(cycles) or target_cycle not in cycles:
        raise ValueError("target read lacks a preceding input cycle")
    active = dict(cycles[target_cycle])
    pre_idle, post_idle = _idle_record(active), _idle_record(active)
    target_value = _pin_value(active, pin)
    other_value = 1 - target_value
    _set_pin_value(pre_idle, pin, other_value)
    _set_pin_value(post_idle, pin, other_value)
    new_cycles = {cycle: dict(values) for cycle, values in cycles.items() if cycle < target_cycle}
    new_cycles[target_cycle] = pre_idle
    new_cycles[target_cycle + 1] = active
    new_cycles[target_cycle + 2] = post_idle
    for cycle, values in cycles.items():
        if cycle > target_cycle:
            new_cycles[cycle + 2] = dict(values)
    new_expected = {}
    for cycle, buses in expected.items():
        shifted = cycle if cycle < target_cycle else cycle + (1 if cycle == target_cycle else 2)
        new_expected[shifted] = dict(buses)
    return new_cycles, new_expected, target_cycle + 1


def _write_trace(path, cycles, expected):
    lines = []
    for cycle in sorted(cycles):
        values = " ".join("{}={}".format(key, value) for key, value in cycles[cycle].items())
        lines.append("C {} {}".format(cycle, values))
        for bus, bits in sorted(expected.get(cycle, {}).items()):
            lines.append("E {} {}={}".format(cycle, bus, bits))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _input_source(deck, pins, trace_cycles, pin, target_cycle, setup_ns, hold_ns,
                  period_ns, voltage_v, slew_ns, skew1):
    if pin["pin"] not in pins:
        raise ValueError("selected input pin is absent from the source netlist")
    pin_index = pins.index(pin["pin"])
    points, previous, changes = [], None, 0
    for cycle, record in sorted(trace_cycles.items()):
        value = _pin_value(record, pin)
        if previous is None:
            points.append((0.0, value))
        elif value != previous:
            if cycle == target_cycle:
                edge = (target_cycle + 1) * period_ns + slew_ns / 2
                center = edge - setup_ns
                start = center - slew_ns / 2
            elif cycle == target_cycle + 1:
                edge = (target_cycle + 1) * period_ns + slew_ns / 2
                center = edge + hold_ns
                start = center - slew_ns / 2
            else:
                start = (cycle + .5 + (skew1 if pin["port"] else 0)) * period_ns
            if points and start <= points[-1][0]:
                raise ValueError("custom input transitions overlap in the generated PWL")
            points.extend(((start, previous), (start + slew_ns, value)))
            if cycle in (target_cycle, target_cycle + 1):
                changes += 1
        previous = value
    if changes != 2:
        raise ValueError("boundary trace does not contain both intended input transitions")
    stop = (len(trace_cycles) + 1.25 + skew1) * period_ns
    points.append((stop, previous))
    wave = " ".join("{:.9g}n {:.9g}".format(time, value * voltage_v)
                    for time, value in points)
    source = "Vi{} ".format(pin_index)
    lines = deck.read_text(encoding="utf-8").splitlines()
    matches = [index for index, line in enumerate(lines) if line.startswith(source)]
    if len(matches) != 1:
        raise ValueError("native deck has no unique source for {}".format(pin["pin"]))
    lines[matches[0]] = "Vi{} e{} 0 PWL({})".format(pin_index, pin_index, wave)
    deck.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clock_pulse_source(deck, pins, port, high_ns, low_ns, period_ns, voltage_v, slew_ns, skew1):
    clock_pins = [pin for pin in pins if re.fullmatch(r"clk\d+", pin)]
    clock = "clk{}".format(port)
    if clock not in clock_pins:
        raise ValueError("selected clock is absent from the source netlist")
    clock_index = clock_pins.index(clock)
    source_index = pins.index(clock)
    width = high_ns - slew_ns
    delay = period_ns * (1 + (skew1 if port else 0))
    replacement = ("Vclk{} e{} 0 PULSE(0 {} {}n {}n {}n {}n {}n)".format(
        clock_index, source_index, voltage_v, delay, slew_ns, slew_ns,
        width, period_ns))
    lines = deck.read_text(encoding="utf-8").splitlines()
    matches = [index for index, line in enumerate(lines)
               if line.startswith("Vclk{} ".format(clock_index))]
    if len(matches) != 1:
        raise ValueError("native deck has no unique source for {}".format(clock))
    lines[matches[0]] = replacement
    deck.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _profiles(trace_path, mapping, parameters, extraction, netlist, probe_map):
    cycles, expected = read_trace(trace_path)
    read_ports = sorted({int(bus[4:]) for buses in expected.values() for bus in buses})
    if not read_ports:
        raise ValueError("boundary trace has no expected read data")
    targets = [readwrite_target(trace_path, read_port=port) for port in read_ports]
    selected = [select_probes(mapping, target, parameters["words_per_row"]) for target in targets]
    signals = {}
    for profile in selected:
        for signal in profile["signals"]:
            previous = signals.get(signal["name"])
            if previous is not None and previous["node"] != signal["node"]:
                raise ValueError("read targets disagree on exact physical probe mapping")
            signals[signal["name"]] = signal
    # The debug probe set resolves hundreds of interior nets through the exact
    # physical mapping; saving them all is what makes a 32x8 PEX transient
    # exceed the memory guard, and no boundary verdict reads them. The mapping
    # resolution above still runs, so an unusable mapping is rejected; only the
    # interior vectors are omitted, and the count is recorded for provenance.
    probe_profile = dict(profile="boundary-top-level-pins",
                         targets=[item["target"] for item in selected],
                         signals=[],
                         interior_probe_signals_omitted=len(signals),
                         interior_probe_names_omitted=sorted(signals),
                         input_gds_sha256=extraction["input_gds_sha256"],
                         netlist_sha256=native._digest(netlist),
                         probe_map=str(probe_map.resolve()),
                         probe_map_sha256=native._digest(probe_map),
                         topology_equivalent=mapping["equivalent"])
    return cycles, expected, probe_profile


def _signal_data(profile, columns, count):
    result = {}
    for signal in profile["signals"]:
        result[signal["name"]] = ([signal["constant_v"]] * count
                                  if signal.get("column") is None
                                  else columns[signal["column"]])
    return result




def _nearest_crossing(times_ns, values, level, direction, target_ns):
    crossing_times, signs = directed_crossings(times_ns, values, level)
    candidates = [time for time, sign in zip(crossing_times, signs) if sign == direction]
    return min(candidates, key=lambda time: abs(time - target_ns)) if candidates else None


def _transition_slews(times_ns, values, voltage_v):
    ten_percent, ten_signs = directed_crossings(times_ns, values, .1 * voltage_v)
    ninety_percent, ninety_signs = directed_crossings(times_ns, values, .9 * voltage_v)
    results = []
    for start, sign in zip(ten_percent, ten_signs):
        if sign > 0:
            end = next((time for time, direction in zip(ninety_percent, ninety_signs)
                        if direction > 0 and time > start), None)
            if end is not None:
                results.append(end - start)
    for start, sign in zip(ninety_percent, ninety_signs):
        if sign < 0:
            end = next((time for time, direction in zip(ten_percent, ten_signs)
                        if direction < 0 and time > start), None)
            if end is not None:
                results.append(end - start)
    return results


def _load_values(deck):
    values = []
    for line in deck.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"Cload\d+\s+\S+\s+0\s+([0-9.eE+-]+)f", line)
        if match:
            values.append(float(match.group(1)))
    return values


def _waveform_verdict(trial, cycles, deck, profile, times_ns, columns):
    data = _signal_data(profile, columns, len(times_ns))
    env = trial["domain"]
    tolerance = max(env["input_slew_ns"] / 2, 1e-4)
    evidence = {"passed": False, "stimulus": trial["kind"], "tolerance_ns": tolerance}
    if trial["kind"] in ("setup", "hold"):
        pin = dict(field=trial["pin_field"], port=trial["port"], bit=trial["bit"],
                   pin=trial["pin"])
        pin_name = trial["pin"]
        if pin_name not in data:
            raise ValueError("generated waveform does not capture boundary input {}".format(pin_name))
        target_cycle = trial["target_cycle"]
        clock = data["clk{}".format(trial["port"])]
        clock_edge = _nearest_crossing(times_ns, clock, env["vdd"] / 2, 1,
                                       (target_cycle + 1) * env["period_ns"] +
                                       env["input_slew_ns"] / 2)
        if clock_edge is None:
            evidence["error"] = "target rising clock crossing was not observed"
            return evidence
        previous_value = _pin_value(cycles[target_cycle - 1], pin)
        active_value = _pin_value(cycles[target_cycle], pin)
        next_value = _pin_value(cycles[target_cycle + 1], pin)
        input_edges, input_signs = directed_crossings(
            times_ns, data[pin_name], env["vdd"] / 2)
        setup_direction = 1 if active_value > previous_value else -1
        hold_direction = 1 if next_value > active_value else -1
        setup_candidates = [time for time, sign in zip(input_edges, input_signs)
                            if sign == setup_direction]
        if not setup_candidates:
            evidence["error"] = "setup input transition was not observed"
            return evidence
        setup_edge = min(setup_candidates,
                         key=lambda time: abs(time - (clock_edge - trial["setup_ns"])))
        hold_candidates = [time for time, sign in zip(input_edges, input_signs)
                           if sign == hold_direction and time > setup_edge]
        hold_edge = (min(hold_candidates,
                         key=lambda time: abs(time - (clock_edge + trial["hold_ns"])))
                     if hold_candidates else None)
        actual_setup = clock_edge - setup_edge
        actual_hold = None if hold_edge is None else hold_edge - clock_edge
        setup_ok = abs(actual_setup - trial["setup_ns"]) <= tolerance
        hold_ok = actual_hold is not None and abs(actual_hold - trial["hold_ns"]) <= tolerance
        evidence.update(pin=pin_name, target_cycle=target_cycle,
                        clock_rising_ns=clock_edge,
                        requested_setup_ns=trial["setup_ns"], observed_setup_ns=actual_setup,
                        requested_hold_ns=trial["hold_ns"], observed_hold_ns=actual_hold,
                        previous_value=previous_value, active_value=active_value,
                        next_value=next_value, setup_waveform_pass=setup_ok,
                        hold_waveform_pass=hold_ok, passed=setup_ok and hold_ok)
        return evidence
    if trial["kind"] == "pulse":
        port = trial["clock_port"]
        clock = data["clk{}".format(port)]
        crossings, signs = directed_crossings(times_ns, clock, env["vdd"] / 2)
        rises = [time for time, sign in zip(crossings, signs) if sign > 0]
        falls = [time for time, sign in zip(crossings, signs) if sign < 0]
        expected_rise = (trial["target_read_cycle"] + 1) * env["period_ns"] + env["input_slew_ns"] / 2
        rising = min(rises, key=lambda time: abs(time - expected_rise)) if rises else None
        falling = next((time for time in falls if rising is not None and time > rising), None)
        next_rising = next((time for time in rises if falling is not None and time > falling), None)
        high = None if rising is None or falling is None else falling - rising
        low = None if falling is None or next_rising is None else next_rising - falling
        pulse_ok = (high is not None and low is not None and
                    abs(high - trial["clock_high_ns"]) <= tolerance and
                    abs(low - trial["clock_low_ns"]) <= tolerance)
        evidence.update(clock_port=port, observed_high_ns=high, observed_low_ns=low,
                        requested_high_ns=trial["clock_high_ns"],
                        requested_low_ns=trial["clock_low_ns"], passed=pulse_ok)
        return evidence

    loads = _load_values(deck)
    load_ok = bool(loads) and all(math.isclose(value, env["load_ff"], rel_tol=0, abs_tol=1e-9)
                                  for value in loads)
    slew_probe = None
    measured_slews = []
    for signal in profile["signals"]:
        name = signal["name"]
        if signal.get("stage") != "input" or name.startswith(("clk", "dout")):
            continue
        values = data[name]
        measured_slews = _transition_slews(times_ns, values, env["vdd"])
        if measured_slews:
            slew_probe = name
            break
    expected_10_90 = .8 * env["input_slew_ns"]
    slew_value = measured_slews[0] if measured_slews else None
    slew_ok = (slew_value is not None and
               abs(slew_value - expected_10_90) <= tolerance)
    evidence.update(output_load_ff=loads, requested_load_ff=env["load_ff"],
                    load_waveform_pass=load_ok, slew_probe=slew_probe,
                    configured_edge_ramp_ns=env["input_slew_ns"],
                    expected_10_90_slew_ns=expected_10_90,
                    observed_10_90_slew_ns=slew_value,
                    slew_waveform_pass=slew_ok, passed=load_ok and slew_ok)
    return evidence


def _trial_support(observations):
    groups = {}
    for record in observations:
        if record.get("status") != "measured" or type(record.get("passed")) is not bool:
            continue
        trial = record["trial"]
        key = canonical_bytes(trial["condition"])
        group = groups.setdefault(key, dict(condition=trial["condition"], axis=trial["axis"],
                                             samples=[]))
        group["samples"].append(dict(value=trial["axis_value"], passed=record["passed"],
                                     id=trial.get("id")))
    result = []
    for group in groups.values():
        samples = sorted(group["samples"], key=lambda item: (item["value"], item["passed"]))
        pairs = []
        same_value_conflicts = []
        for left, right in zip(samples, samples[1:]):
            if left["value"] != right["value"] and left["passed"] != right["passed"]:
                pairs.append(dict(left=left, right=right,
                                  description="adjacent sampled pass/fail points only"))
            elif left["value"] == right["value"] and left["passed"] != right["passed"]:
                same_value_conflicts.append(dict(left=left, right=right,
                                                 description="same stimulus produced conflicting outcomes"))
        outcomes = {item["passed"] for item in samples}
        support_status = ("inconsistent_repeated_sample" if same_value_conflicts else
                          "sampled_pass_and_fail" if len(outcomes) == 2 else
                          "sampled_pass_only" if outcomes == {True} else
                          "sampled_fail_only")
        result.append(dict(condition=group["condition"], axis=group["axis"], samples=samples,
                           support_status=support_status,
                           sampled_pass_fail_pairs=pairs,
                           same_value_conflicts=same_value_conflicts, limit=None,
                           continuous_domain_proven=False))
    return result


def _unresolved_constraints(base):
    unresolved = []
    for port, role in enumerate(base["access_contract"]["ports"]):
        if role not in ("RW", "R"):
            continue
        for pin in ("addr{}".format(port), "csb{}".format(port)):
            unresolved.extend((pin + "_setup", pin + "_hold"))
        if role == "RW":
            unresolved.extend(("web{}_setup".format(port), "web{}_hold".format(port)))
        unresolved.extend(("clk{}_minimum_high_pulse".format(port),
                           "clk{}_minimum_low_pulse".format(port)))
    unresolved.extend(("minimum_period", "output_load_continuity", "input_slew_continuity",
                       "input_clock_capacitance", "complete_macro_setup_hold_and_pulse_limits",
                       "STA/Liberty signoff"))
    return unresolved


def _process_rss_mb(pid):
    """Observed resident set of one process, or None when it cannot be read."""
    try:
        result = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return int(text.splitlines()[0].strip()) / 1024.0
    except ValueError:
        return None


def _supervised_run(command, cwd, timeout, memory_limit_mb, poll_s=1.0):
    """Run ngspice under a hard resident-memory guard and an explicit timeout.

    The simulator keeps every saved vector in memory for the whole transient, so
    an unbounded run can starve the host. A guard breach kills the process and
    fails closed as unavailable; it is never reported as a device verdict.
    """
    log = Path(cwd) / "ngspice.log"
    started = time.monotonic()
    peak = 0.0
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(command, cwd=cwd, stdout=stream,
                                   stderr=subprocess.STDOUT, text=True)
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout:
                    process.kill()
                    process.wait()
                    raise ValueError("ngspice boundary trial timed out after {:.0f}s".format(timeout))
                observed = _process_rss_mb(process.pid)
                if observed is not None:
                    peak = max(peak, observed)
                    if memory_limit_mb is not None and observed > memory_limit_mb:
                        process.kill()
                        process.wait()
                        raise ValueError(
                            "ngspice boundary trial exceeded the {} MB memory guard at "
                            "{:.0f} MB".format(memory_limit_mb, observed))
                time.sleep(poll_s)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    return {"returncode": process.returncode, "peak_rss_mb": peak,
            "output": log.read_text(encoding="utf-8", errors="replace")}


def _run_trial(registry, base, trial, destination, *, execute, ngspice, timeout,
               memory_limit_mb):
    candidate, normalized = _candidate_and_trial(trial, base)
    record = dict(trial=normalized, candidate=candidate, status="unavailable",
                  functional=None, waveform_verdict=None, passed=None, signoff=False)
    try:
        admission = registry.admit(candidate)
        paths, hashes, extraction, mapping = native._inputs(admission)
        attempt = Path(tempfile.mkdtemp(prefix="boundary-", dir=destination))
        source_trace = paths["trace"]
        cycles, expected = read_trace(source_trace)
        target_cycle = None
        target = None
        pin = None
        if normalized["kind"] in ("setup", "hold"):
            pin = _pin_spec(normalized["pin"], candidate["access_contract"]["ports"],
                            candidate["parameters"])
            target = readwrite_target(source_trace, read_port=pin["port"])
            cycles, expected, target_cycle = _insert_input_boundary(
                cycles, expected, target["read_cycle"], pin)
            normalized["target_read_cycle"] = target["read_cycle"]
            normalized["target_cycle"] = target_cycle
            trace_path = attempt / "boundary.trace"
            _write_trace(trace_path, cycles, expected)
        else:
            trace_path = source_trace
        cycles, expected, profile = _profiles(trace_path, mapping,
                                              candidate["parameters"], extraction,
                                              paths["netlist"], paths["probe_map"])
        if normalized["kind"] == "pulse":
            pulse_target = next((item for item in profile["targets"]
                                 if item["read_port"] == normalized["clock_port"]), None)
            if pulse_target is None:
                raise ValueError("pulse trial has no expected read on the selected clock port")
            normalized["target_read_cycle"] = pulse_target["read_cycle"]
        corner_matches = [item for item in extraction.get("corners", [])
                          if item.get("netlist_sha256") == hashes["netlist"] and
                          Path(item.get("netlist", "")).resolve() == paths["netlist"]]
        if len(corner_matches) != 1:
            raise ValueError("extraction does not select one exact source netlist")
        env = candidate["environment"]
        deck, wavefile, outputs, expected, profile = build_deck(
            paths["netlist"], trace_path, extraction["design"], paths["model"], attempt,
            env["period_ns"], env["voltage_v"], env["temperature_c"], env["corner"],
            env["skew1"], env["cload_ff"], env["slew_ns"], profile,
            env["initialization"], corner_matches[0])
        if normalized["kind"] in ("setup", "hold"):
            _input_source(deck, subckt_pins(paths["netlist"], extraction["design"]), cycles,
                          pin, target_cycle, normalized["setup_ns"], normalized["hold_ns"],
                          env["period_ns"], env["voltage_v"], env["slew_ns"], env["skew1"])
        elif normalized["kind"] == "pulse":
            _clock_pulse_source(deck, subckt_pins(paths["netlist"], extraction["design"]),
                                normalized["clock_port"], normalized["clock_high_ns"],
                                normalized["clock_low_ns"], env["period_ns"], env["voltage_v"],
                                env["slew_ns"], env["skew1"])
        record["domain"] = normalized["domain"]
        record["source_artifacts"] = {name: native._ref(path) for name, path in paths.items()}
        record["artifacts"] = {"trace": native._ref(trace_path), "deck": native._ref(deck),
                               "probes": native._ref(attempt / "probes.json"),
                               "initialization": native._ref(attempt / "initialization_plan.json")}
        record["stimulus"] = dict(target=target, target_cycle=target_cycle, pin=pin,
                                  generated_trace_sha256=native._digest(trace_path),
                                  deck_sha256=native._digest(deck))
        if not execute:
            record["status"] = "generated-not-measured"
            return record
        dependencies_before = native._dependencies(paths["model"])
        tool = native._tool_identity(ngspice)
        command = [tool["path"], "-n", "-D", "ngbehavior=hsa", "-b", str(deck)]
        run = _supervised_run(command, attempt, timeout, memory_limit_mb)
        if native._dependencies(paths["model"]) != dependencies_before or \
                native._digest(tool["path"]) != tool["sha256"]:
            raise ValueError("model dependencies or ngspice binary changed during boundary trial")
        if run["returncode"] != 0 or not wavefile.is_file():
            raise ValueError("ngspice boundary trial produced no complete waveform")
        checked = check_waveforms(wavefile, outputs, expected, env["period_ns"],
                                  env["voltage_v"], env["skew1"], profile)
        times_ns, columns = native._waveforms(wavefile, profile)
        waveform = _waveform_verdict(normalized, cycles, deck, profile,
                                     times_ns, columns)
        stimulus_valid = waveform["passed"] is True
        passed = checked["passed"] is True if stimulus_valid else None
        result = dict(schema=SCHEMA, trial=normalized, domain=normalized["domain"],
                      functional=checked["passed"] is True, stimulus_valid=stimulus_valid,
                      waveform_verdict=waveform, passed=passed,
                      signoff=False, complete_sta=False,
                      memory_guard_mb=memory_limit_mb, peak_rss_mb=run["peak_rss_mb"],
                      probe_scope=profile["profile"],
                      netlist_sha256=hashes["netlist"], trace_sha256=native._digest(trace_path),
                      deck_sha256=native._digest(deck), model_dependencies=dependencies_before,
                      ngspice=tool, command=command)
        simulation = attempt / "simulation.json"
        simulation.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                              encoding="utf-8")
        record.update(status="measured" if stimulus_valid else "stimulus-invalid",
                      functional=result["functional"], stimulus_valid=stimulus_valid,
                      waveform_verdict=waveform, passed=passed,
                      execution=dict(tool=tool, command=command,
                                     model_dependencies=dependencies_before,
                                     memory_guard_mb=memory_limit_mb,
                                     peak_rss_mb=run["peak_rss_mb"],
                                     probe_scope=profile["profile"]),
                      artifacts={**record["artifacts"],
                                 "waves": native._ref(wavefile),
                                 "log": native._ref(attempt / "ngspice.log"),
                                 "simulation": native._ref(simulation)})
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        record["error"] = str(exc)
        if "attempt" in locals():
            record["attempt_artifacts"] = {
                path.name: native._ref(path) for path in sorted(attempt.iterdir()) if path.is_file()}
    return record


def run_boundary_experiments(registry, request, trials, out_dir, *, execute=False,
                             ngspice="ngspice", timeout=3600, memory_limit_mb=1200.0):
    if type(execute) is not bool:
        raise ValueError("execute must be an explicit boolean")
    number(timeout, "timeout", 0, strict=True)
    number(memory_limit_mb, "memory_limit_mb", 0, strict=True)
    if not isinstance(trials, list) or not trials:
        raise ValueError("at least one explicit boundary trial is required")
    request = validate_request(deepcopy(request))
    if request["search"]:
        raise ValueError("boundary request must select one implementation/environment")
    base = registry.candidate(request["capability_id"], request["parameters"],
                              request["environment"])
    if base["environment"]["skew1"] != 0:
        raise ValueError("boundary trials require explicitly zero skew1")
    destination = Path(out_dir).expanduser().resolve()
    runs = (ROOT / "ics55_work" / "runs").resolve()
    if not destination.is_relative_to(runs):
        raise ValueError("boundary experiment artifacts must be under ics55_work/runs")
    destination.mkdir(parents=True, exist_ok=True)
    observations = [_run_trial(registry, base, trial, destination, execute=execute,
                               ngspice=ngspice, timeout=timeout,
                               memory_limit_mb=memory_limit_mb) for trial in trials]
    measured_domains = [item["domain"] for item in observations if item.get("status") == "measured"]
    manifest = dict(schema=SCHEMA, base_candidate=base, execute=execute,
                    observations=observations, sampled_support=_trial_support(observations),
                    memory_guard_mb=memory_limit_mb,
                    probe_scope="top-level pins only; interior BL/BR/RBL probe vectors are "
                                "omitted and their count is recorded in each attempt probes.json",
                    execution_rule="boundary experiment invocations must run one at a time; "
                                   "concurrent 32x8 PEX transients exhaust host memory",
                    domain_coverage={key: sorted({item.get(key) for item in measured_domains
                                                  if item.get(key) is not None})
                                    for key in _REQUIRED_DOMAIN},
                    unresolved_constraints=_unresolved_constraints(base),
                    minimum_limits=dict.fromkeys(("setup_ns", "hold_ns", "clock_high_ns",
                                                  "clock_low_ns", "minimum_period_ns",
                                                  "maximum_load_ff", "input_slew_domain")),
                    continuous_domain_proven=False, signoff=False, complete_sta=False,
                    verdict_rule="pass/fail support requires both the ngspice functional output check and a waveform-verified requested stimulus; incorrect stimulus is invalid/unavailable, not a DUT failure, and simulator errors are unavailable")
    path = destination / "boundary-experiments.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry")
    parser.add_argument("request")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--trial", action="append", required=True,
                        help="explicit JSON stimulus object; repeat for sampled trials")
    parser.add_argument("--execute", action="store_true", help="authorize fresh serial ngspice trials")
    parser.add_argument("--ngspice", default="ngspice")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--memory-limit-mb", type=float, default=1200.0,
                        help="resident-memory guard for one ngspice trial; a breach fails closed")
    args = parser.parse_args(argv)
    try:
        trials = [json.loads(value) for value in args.trial]
        result = run_boundary_experiments(Registry(args.registry), load_json(args.request),
                                          trials, args.out_dir, execute=args.execute,
                                          ngspice=args.ngspice, timeout=args.timeout,
                                          memory_limit_mb=args.memory_limit_mb)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print(str(Path(args.out_dir).resolve() / "boundary-experiments.json"))
    return 0 if args.execute and all(
        item.get("status") == "measured" for item in result["observations"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
