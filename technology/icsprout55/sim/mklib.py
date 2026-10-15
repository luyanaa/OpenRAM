#!/usr/bin/env python3
"""Generate a role- and standard-cell-aware SRAM Liberty from ICsprout55 data.

Each m5/m50 sample is charz.py output with ``stdcell_library`` metadata; its
port metrics carry the RW/R/W role read from tbgen's behavioral-model comments.
Only supported reads and writes are emitted, and delay tables cover exactly
the two recorded output loads and one recorded input slew.

Usage: mklib.py MEAS.json MACRO OUTDIR --words N --bits B --area UM2
       --stdcell-library LIB --port-roles 0:RW,1:R [--ports 0,1]
       [--power-pins VDD,VSS]
"""
import argparse
import json
import math
import os

MARGIN = 1.10
SETUP_PAD = 0.10
HOLD_PAD = 0.10
IN_CAP = 0.0015
# DFF mappings mirror technology/icsprout55/tech/tech.py.
STDCELL_DFF = {
    "SC9T": "DFFQX1_9TSVT",
    "SC9TL": "DFFQX1_9TLVT",
    "SC9TH": "DFFQX1_9THVT",
    "H7CR": "DFFQX1H7R",
    "H7CL": "DFFQX1H7L",
    "H7CH": "DFFQX1H7H",
}
STDCELL_ALIASES = {"9T": "SC9T", "SC9TSVT": "SC9T", "7T": "H7CR"}


def table(values, indent="            "):
    rows = ['"' + ", ".join("%.4f" % value for value in row) + '"' for row in values]
    return indent + "values(" + (", \\\n" + indent + "       ").join(rows) + ");\n"


def normalize_stdcell_library(value):
    library = value.upper()
    library = STDCELL_ALIASES.get(library, library)
    if library not in STDCELL_DFF:
        raise ValueError("unsupported ICsprout55 stdcell library '{}'; choose one of {}".format(
            value, ", ".join(STDCELL_DFF)))
    return library


def ports_argument(value):
    try:
        ports = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("ports must be comma-separated indices") from error
    if not ports or min(ports) < 0 or len(set(ports)) != len(ports):
        raise argparse.ArgumentTypeError("ports must be distinct non-negative indices")
    return ports


def port_roles_argument(value):
    roles = {}
    try:
        for item in value.split(","):
            index_text, role = item.split(":", 1)
            index = int(index_text)
            role = role.upper()
            if index < 0 or role not in ("RW", "R", "W") or index in roles:
                raise ValueError
            roles[index] = role
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "port roles must be distinct entries in INDEX:RW|R|W form") from error
    if not roles:
        raise argparse.ArgumentTypeError("at least one port role is required")
    return roles


def port_sample(sample, port):
    if "ports" in sample:
        try:
            return sample["ports"][str(port)]
        except KeyError as error:
            raise ValueError("measurement lacks port {}".format(port)) from error
    if port == 0:
        return sample
    raise ValueError("multi-port measurements must contain a ports object")


def required_number(values, key):
    value = values.get(key)
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("missing finite measurement {}".format(key))
    return float(value)


def measurement_pair(corner_data, port, input_slew_ns):
    samples = []
    for label, default_load in (("m5", 5.0), ("m50", 50.0)):
        sample = corner_data[label]
        domain = sample.get("domain", {})
        metrics = port_sample(sample, port)
        load = float(domain.get("load_ff", default_load))
        slew = float(domain.get("input_slew_ns", input_slew_ns))
        if load <= 0 or slew <= 0:
            raise ValueError("load and input slew must be positive")
        samples.append((load, slew, domain, metrics))
    samples.sort(key=lambda item: item[0])
    if samples[0][0] >= samples[1][0]:
        raise ValueError("m5 and m50 must describe distinct output loads")
    if samples[0][1] != samples[1][1]:
        raise ValueError("m5 and m50 input slews differ; characterize a separate slew axis")
    for key in ("period_ns", "corner", "vdd", "temp"):
        left, right = samples[0][2].get(key), samples[1][2].get(key)
        if left is not None and right is not None and left != right:
            raise ValueError("m5 and m50 domains differ for {}".format(key))
    return samples[0], samples[1]


def corner_value(values, key, port):
    value = values[key]
    if isinstance(value, dict):
        value = value.get(str(port))
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("missing finite {} for port {}".format(key, port))
    return float(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("meas")
    parser.add_argument("macro")
    parser.add_argument("outdir")
    parser.add_argument("--words", type=int, required=True)
    parser.add_argument("--bits", type=int, required=True)
    parser.add_argument("--area", type=float, required=True)
    parser.add_argument("--stdcell-library", required=True)
    parser.add_argument("--port-roles", type=port_roles_argument, required=True,
                        help="behavioral-model roles as INDEX:RW|R|W entries")
    parser.add_argument("--ports", type=ports_argument,
                        help="optional comma-separated port indices; defaults to all roles")
    parser.add_argument("--input-slew-ns", type=float, default=0.03,
                        help="slew for measurement files without domain metadata")
    parser.add_argument("--power-pins", default="vdd,gnd",
                        help="names of the power and ground pins")
    args = parser.parse_args()
    try:
        stdcell_library = normalize_stdcell_library(args.stdcell_library)
    except ValueError as error:
        parser.error(str(error))
    ports = sorted(args.port_roles) if args.ports is None else args.ports
    if not ports or set(ports) != set(args.port_roles):
        parser.error("--ports must select exactly the indices declared by --port-roles")
    if args.words <= 0 or args.bits <= 0 or args.area <= 0:
        parser.error("--words, --bits, and --area must be positive")
    power_pin, ground_pin = args.power_pins.split(",")
    measurements = json.load(open(args.meas))
    address_width = (args.words - 1).bit_length()
    os.makedirs(args.outdir, exist_ok=True)

    for corner, corner_data in measurements.items():
        for label in ("m5", "m50"):
            sample = corner_data[label]
            reported_library = sample.get("stdcell_library")
            if reported_library is None:
                raise ValueError(
                    "{} {} sample lacks stdcell_library; rerun charz.py with "
                    "--stdcell-library".format(corner, label))
            if normalize_stdcell_library(reported_library) != stdcell_library:
                raise ValueError(
                    "{} {} sample uses {}, requested {}".format(
                        corner, label, reported_library, stdcell_library))
        pairs = {port: measurement_pair(corner_data, port, args.input_slew_ns)
                 for port in ports}
        for port in ports:
            role = args.port_roles[port]
            for sample in pairs[port]:
                metrics = sample[3]
                if metrics.get("role") != role:
                    raise ValueError(
                        "{} port {} measurement role {} does not match requested {}".format(
                            corner, port, metrics.get("role", "missing"), role))
        first_sample = pairs[ports[0]][0][2]
        voltage = float(corner_data.get("vdd", first_sample.get("vdd", 0)))
        temperature = float(corner_data.get("temp", first_sample.get("temp", 25)))
        if voltage <= 0:
            raise ValueError("missing corner supply voltage for {}".format(corner))
        name = "{}_{}".format(args.macro, corner)
        library = []
        emit = library.append
        emit("/* {}: ICsprout55 {}x{}, roles {}, stdcell {} ({}), corner {}.\n".format(
            args.macro, args.words, args.bits,
            ",".join("{}:{}".format(port, args.port_roles[port]) for port in ports),
            stdcell_library, STDCELL_DFF[stdcell_library], corner))
        emit("   Worst measured post-layout delay with {:.2f} margin. The delay table\n".format(MARGIN))
        emit("   covers only the two recorded output loads and one recorded input slew;\n")
        emit("   do not use outside that characterized domain. */\n")
        emit("library ({}) {{\n".format(name))
        emit("    delay_model : table_lookup;\n    time_unit : \"1ns\";\n    voltage_unit : \"1V\";\n")
        emit("    current_unit : \"1mA\";\n    resistance_unit : \"1kohm\";\n    capacitive_load_unit (1, pf);\n")
        emit("    leakage_power_unit : \"1mW\";\n    pulling_resistance_unit : \"1kohm\";\n")
        emit("    nom_process : 1;\n    nom_voltage : {:.2f};\n    nom_temperature : {:g};\n".format(
            voltage, temperature))
        emit("    operating_conditions ({}) {{\n        process : 1;\n        voltage : {:.2f};\n        temperature : {:g};\n    }}\n".format(
            corner, voltage, temperature))
        emit("    default_operating_conditions : {};\n".format(corner))
        for key in ("input_threshold_pct_rise", "input_threshold_pct_fall",
                    "output_threshold_pct_rise", "output_threshold_pct_fall"):
            emit("    {} : 50;\n".format(key))
        emit("    slew_lower_threshold_pct_rise : 10;\n    slew_upper_threshold_pct_rise : 90;\n")
        emit("    slew_lower_threshold_pct_fall : 10;\n    slew_upper_threshold_pct_fall : 90;\n")
        emit("    slew_derate_from_library : 1;\n")
        read_ports = [port for port in ports if args.port_roles[port] in ("RW", "R")]
        if read_ports:
            maximum_output_transition = max(
                MARGIN * required_number(metrics, "dout_tran")
                for port in read_ports
                for _, _, _, metrics in pairs[port])
            emit("    default_max_transition : {:.4f};\n".format(
                maximum_output_transition))
        emit("    default_fanout_load : 1;\n")
        emit("    default_input_pin_cap : {:.4f};\n    default_output_pin_cap : 0;\n    default_inout_pin_cap : {:.4f};\n".format(
            IN_CAP, IN_CAP))
        emit("    default_leakage_power_density : 0;\n    default_cell_leakage_power : 0;\n")
        emit("    voltage_map ({}, {:.2f});\n    voltage_map ({}, 0);\n".format(
            power_pin, voltage, ground_pin))
        for port in read_ports:
            low_load, high_load = pairs[port]
            emit("    lu_table_template (delay_tbl{}) {{\n".format(port))
            emit("        variable_1 : input_net_transition;\n")
            emit("        variable_2 : total_output_net_capacitance;\n")
            emit('        index_1 ("{:.4f}");\n'.format(low_load[1]))
            emit('        index_2 ("{:.4f}, {:.4f}");\n'.format(
                low_load[0] / 1000.0, high_load[0] / 1000.0))
            emit("    }\n")
        emit("    type (DATA) {{\n        base_type : array;\n        data_type : bit;\n        bit_width : {};\n        bit_from : {};\n        bit_to : 0;\n        downto : true;\n    }}\n".format(args.bits, args.bits - 1))
        emit("    type (ADDR) {{\n        base_type : array;\n        data_type : bit;\n        bit_width : {};\n        bit_from : {};\n        bit_to : 0;\n        downto : true;\n    }}\n".format(address_width, address_width - 1))
        emit("    cell ({}) {{\n".format(args.macro))
        emit("        area : {:.3f};\n        dont_touch : true;\n        dont_use : true;\n        interface_timing : true;\n".format(args.area))
        emit("        memory () {{\n            type : ram;\n            address_width : {};\n            word_width : {};\n        }}\n".format(address_width, args.bits))
        emit("        pg_pin ({}) {{\n            voltage_name : {};\n            pg_type : primary_power;\n        }}\n".format(power_pin, power_pin))
        emit("        pg_pin ({}) {{\n            voltage_name : {};\n            pg_type : primary_ground;\n        }}\n".format(ground_pin, ground_pin))
        emit("        cell_leakage_power : {:.6f};\n".format(
            required_number(pairs[ports[0]][0][3], "leak_mw")))

        def constraints(port, setup, hold):
            output = []
            for timing_type, value in (("setup_rising", setup), ("hold_rising", hold)):
                output.append('            timing () {{\n                related_pin : "clk{}";\n                timing_type : {};\n'.format(
                    port, timing_type))
                output.append('                rise_constraint (scalar) {{ values ("{:.4f}"); }}\n'.format(value))
                output.append('                fall_constraint (scalar) {{ values ("{:.4f}"); }}\n            }}\n'.format(value))
            return "".join(output)

        for port in ports:
            role = args.port_roles[port]
            can_read = role in ("RW", "R")
            can_write = role in ("RW", "W")
            low_load, high_load = pairs[port]
            low_metrics, high_metrics = low_load[3], high_load[3]
            loads_pf = [low_load[0] / 1000.0, high_load[0] / 1000.0]
            input_slew = low_load[1]
            setup = corner_value(corner_data, "dff_setup", port) + SETUP_PAD
            insertion = max(required_number(low_metrics, "ins_rise"),
                            required_number(high_metrics, "ins_rise"))
            hold = corner_value(corner_data, "dff_hold", port) + insertion + HOLD_PAD
            chain_reset = max(required_number(low_metrics, "chain_reset"),
                              required_number(high_metrics, "chain_reset"))
            low_pulse_limits = []
            if can_read:
                read_fall = max(required_number(low_metrics, "dout_fall"),
                                required_number(high_metrics, "dout_fall"))
                low_pulse_limits.append(read_fall + 0.15)
            if can_write:
                write_enable_end = max(required_number(low_metrics, "wen_end"),
                                       required_number(high_metrics, "wen_end"))
                low_pulse_limits.append(write_enable_end)
            pulse_high = MARGIN * chain_reset + 0.1
            pulse_low = MARGIN * max(low_pulse_limits) + 0.1
            minimum_period = pulse_high + pulse_low

            emit("        pin (clk{}) {{\n            direction : input;\n            clock : true;\n            capacitance : {:.4f};\n".format(
                port, IN_CAP))
            emit("            max_transition : {:.4f};\n".format(input_slew))
            emit("            min_pulse_width_high : {:.4f};\n            min_pulse_width_low : {:.4f};\n".format(
                pulse_high, pulse_low))
            emit('            timing () {{\n                related_pin : "clk{}";\n                timing_type : minimum_period;\n'.format(port))
            emit('                rise_constraint (scalar) {{ values ("{:.4f}"); }}\n'.format(minimum_period))
            emit('                fall_constraint (scalar) {{ values ("{:.4f}"); }}\n            }}\n'.format(minimum_period))
            power_conditions = []
            if can_read:
                condition = "!csb{} & web{}".format(port, port) if role == "RW" else "!csb{}".format(port)
                power_conditions.append((condition, "e_read_pj"))
            if can_write:
                condition = "!csb{} & !web{}".format(port, port) if role == "RW" else "!csb{}".format(port)
                power_conditions.append((condition, "e_write_pj"))
            power_conditions.append(("csb{}".format(port), "e_idle_pj"))
            for condition, key in power_conditions:
                energy = required_number(low_metrics, key)
                emit('            internal_power () {{\n                when : "{}";\n'.format(condition))
                emit('                rise_power (scalar) {{ values ("{:.4f}"); }}\n'.format(energy))
                emit('                fall_power (scalar) { values ("0"); }\n            }\n')
            emit("        }\n")

            input_pins = ["csb{}".format(port)]
            if role == "RW":
                input_pins.append("web{}".format(port))
            for pin in input_pins:
                emit("        pin ({}) {{\n            direction : input;\n            capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                    pin, IN_CAP, input_slew))
                emit(constraints(port, setup, hold) + "        }\n")
            emit("        bus (addr{}) {{\n            bus_type : ADDR;\n            direction : input;\n            capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                port, IN_CAP, input_slew))
            emit(constraints(port, setup, hold) + "        }\n")

            if can_write:
                emit("        bus (din{}) {{\n            bus_type : DATA;\n            direction : input;\n            capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                    port, IN_CAP, input_slew))
                emit('            memory_write () {{\n                address : addr{};\n                clocked_on : "clk{}";\n            }}\n'.format(
                    port, port))
                emit(constraints(port, setup, hold) + "        }\n")

            if can_read:
                delay = [[MARGIN * required_number(low_metrics, "dout_fall"),
                          MARGIN * required_number(high_metrics, "dout_fall")]]
                transition = [[MARGIN * required_number(low_metrics, "dout_tran"),
                               MARGIN * required_number(high_metrics, "dout_tran")]]
                emit("        bus (dout{}) {{\n            bus_type : DATA;\n            direction : output;\n            max_capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                    port, max(loads_pf), max(transition[0])))
                emit("            memory_read () {{\n                address : addr{};\n            }}\n".format(port))
                emit('            timing () {{\n                related_pin : "clk{}";\n                timing_type : falling_edge;\n                timing_sense : non_unate;\n'.format(port))
                for key in ("cell_rise", "cell_fall"):
                    emit("                {} (delay_tbl{}) {{\n".format(key, port))
                    emit(table(delay, "                    "))
                    emit("                }\n")
                for key in ("rise_transition", "fall_transition"):
                    emit("                {} (delay_tbl{}) {{\n".format(key, port))
                    emit(table(transition, "                    "))
                    emit("                }\n")
                emit("            }\n        }\n")
                observed_holds = [
                    value for value in (low_metrics.get("dout_hold_after_rise"),
                                        high_metrics.get("dout_hold_after_rise"))
                    if isinstance(value, (int, float)) and math.isfinite(value)
                ]
                if observed_holds:
                    emit("        /* port {}: minimum observed dout hold after rising edge {:.4f} ns */\n".format(
                        port, min(observed_holds)))
                else:
                    emit("        /* port {}: dout hold after rising edge not measured */\n".format(port))

            emit("        /* port {}: role {}; slew {:.4f} ns; loads {:.4f}..{:.4f} pF */\n".format(
                port, role, input_slew, loads_pf[0], loads_pf[1]))
        emit("    }\n}\n")
        output_path = os.path.join(args.outdir, name + ".lib")
        with open(output_path, "w") as output_file:
            output_file.write("".join(library))
        print("{}: ports={} loads={:.1f}/{:.1f} fF slew={:.3f} ns".format(
            corner, ",".join(map(str, ports)),
            pairs[ports[0]][0][0], pairs[ports[0]][1][0],
            pairs[ports[0]][0][1]))


if __name__ == "__main__":
    main()
