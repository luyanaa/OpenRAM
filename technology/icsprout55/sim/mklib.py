#!/usr/bin/env python3
"""Generate a multi-port SRAM Liberty from measured ICsprout55 data.

Input JSON is {corner: {"m5": sample, "m50": sample, "dff_setup": ns,
"dff_hold": ns, "vdd": V, "temp": C}}, where each sample is charz.py output
at an explicitly recorded output load. Tables contain only the two measured
loads and one measured input slew; no delay or slew extrapolation is generated.
Reads are timed from the falling clock edge; input constraints reference the
rising edge.

Usage: mklib.py MEAS.json MACRO OUTDIR --words N --bits B --area UM2
       [--ports 0,1] [--power-pins VDD,VSS]
"""
import argparse
import json
import math
import os

MARGIN = 1.10
SETUP_PAD = 0.10
HOLD_PAD = 0.10
IN_CAP = 0.0015


def table(values, indent="            "):
    rows = ['"' + ", ".join("%.4f" % value for value in row) + '"' for row in values]
    return indent + "values(" + (", \\\n" + indent + "       ").join(rows) + ");\n"


def ports_argument(value):
    ports = [int(item) for item in value.split(",")]
    if not ports or min(ports) < 0 or len(set(ports)) != len(ports):
        raise argparse.ArgumentTypeError("ports must be distinct non-negative indices")
    return ports


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
    parser.add_argument("--ports", type=ports_argument, default=[0])
    parser.add_argument("--input-slew-ns", type=float, default=0.03,
                        help="slew for legacy measurement files without domain metadata")
    parser.add_argument("--power-pins", default="vdd,gnd",
                        help="names of the power and ground pins")
    args = parser.parse_args()
    power_pin, ground_pin = args.power_pins.split(",")
    measurements = json.load(open(args.meas))
    address_width = (args.words - 1).bit_length()
    os.makedirs(args.outdir, exist_ok=True)

    for corner, corner_data in measurements.items():
        pairs = {port: measurement_pair(corner_data, port, args.input_slew_ns)
                 for port in args.ports}
        first_sample = pairs[args.ports[0]][0][2]
        voltage = float(corner_data.get("vdd", first_sample.get("vdd", 0)))
        temperature = float(corner_data.get("temp", first_sample.get("temp", 25)))
        if voltage <= 0:
            raise ValueError("missing corner supply voltage for {}".format(corner))
        name = "{}_{}".format(args.macro, corner)
        library = []
        emit = library.append
        emit("/* {}: ICsprout55 {}x{}, {} RW port(s), corner {}.\n".format(
            args.macro, args.words, args.bits, len(args.ports), corner))
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
        maximum_output_transition = max(
            MARGIN * required_number(metrics, "dout_tran")
            for port in args.ports
            for _, _, _, metrics in pairs[port])
        emit("    default_max_transition : {:.4f};\n    default_fanout_load : 1;\n".format(
            maximum_output_transition))
        emit("    default_input_pin_cap : {:.4f};\n    default_output_pin_cap : 0;\n    default_inout_pin_cap : {:.4f};\n".format(
            IN_CAP, IN_CAP))
        emit("    default_leakage_power_density : 0;\n    default_cell_leakage_power : 0;\n")
        emit("    voltage_map ({}, {:.2f});\n    voltage_map ({}, 0);\n".format(
            power_pin, voltage, ground_pin))
        for port in args.ports:
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
            required_number(pairs[args.ports[0]][0][3], "leak_mw")))

        def constraints(port, setup, hold):
            output = []
            for timing_type, value in (("setup_rising", setup), ("hold_rising", hold)):
                output.append('            timing () {{\n                related_pin : "clk{}";\n                timing_type : {};\n'.format(
                    port, timing_type))
                output.append('                rise_constraint (scalar) {{ values ("{:.4f}"); }}\n'.format(value))
                output.append('                fall_constraint (scalar) {{ values ("{:.4f}"); }}\n            }}\n'.format(value))
            return "".join(output)

        for port in args.ports:
            low_load, high_load = pairs[port]
            low_metrics, high_metrics = low_load[3], high_load[3]
            loads_pf = [low_load[0] / 1000.0, high_load[0] / 1000.0]
            input_slew = low_load[1]
            delay = [[MARGIN * required_number(low_metrics, "dout_fall"),
                      MARGIN * required_number(high_metrics, "dout_fall")]]
            transition = [[MARGIN * required_number(low_metrics, "dout_tran"),
                           MARGIN * required_number(high_metrics, "dout_tran")]]
            setup = corner_value(corner_data, "dff_setup", port) + SETUP_PAD
            insertion = max(required_number(low_metrics, "ins_rise"),
                            required_number(high_metrics, "ins_rise"))
            hold = corner_value(corner_data, "dff_hold", port) + insertion + HOLD_PAD
            chain_reset = max(required_number(low_metrics, "chain_reset"),
                              required_number(high_metrics, "chain_reset"))
            write_enable_end = max(required_number(low_metrics, "wen_end"),
                                   required_number(high_metrics, "wen_end"))
            pulse_high = MARGIN * chain_reset + 0.1
            pulse_low = MARGIN * max(required_number(high_metrics, "dout_fall") + 0.15,
                                     write_enable_end) + 0.1
            minimum_period = pulse_high + pulse_low
            output_cap = max(loads_pf)

            emit("        pin (clk{}) {{\n            direction : input;\n            clock : true;\n            capacitance : {:.4f};\n".format(
                port, IN_CAP))
            emit("            max_transition : {:.4f};\n".format(input_slew))
            emit("            min_pulse_width_high : {:.4f};\n            min_pulse_width_low : {:.4f};\n".format(
                pulse_high, pulse_low))
            emit('            timing () {{\n                related_pin : "clk{}";\n                timing_type : minimum_period;\n'.format(port))
            emit('                rise_constraint (scalar) {{ values ("{:.4f}"); }}\n'.format(minimum_period))
            emit('                fall_constraint (scalar) {{ values ("{:.4f}"); }}\n            }}\n'.format(minimum_period))
            for condition, key in (("!csb{} & web{}".format(port, port), "e_read_pj"),
                                   ("!csb{} & !web{}".format(port, port), "e_write_pj"),
                                   ("csb{}".format(port), "e_idle_pj")):
                energy = required_number(low_metrics, key)
                emit('            internal_power () {{\n                when : "{}";\n'.format(condition))
                emit('                rise_power (scalar) {{ values ("{:.4f}"); }}\n'.format(energy))
                emit('                fall_power (scalar) { values ("0"); }\n            }\n')
            emit("        }\n")

            for pin in ("csb{}".format(port), "web{}".format(port)):
                emit("        pin ({}) {{\n            direction : input;\n            capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                    pin, IN_CAP, input_slew))
                emit(constraints(port, setup, hold) + "        }\n")
            emit("        bus (addr{}) {{\n            bus_type : ADDR;\n            direction : input;\n            capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                port, IN_CAP, input_slew))
            emit(constraints(port, setup, hold) + "        }\n")
            emit("        bus (din{}) {{\n            bus_type : DATA;\n            direction : input;\n            capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                port, IN_CAP, input_slew))
            emit('            memory_write () {{\n                address : addr{};\n                clocked_on : "clk{}";\n            }}\n'.format(
                port, port))
            emit(constraints(port, setup, hold) + "        }\n")
            emit("        bus (dout{}) {{\n            bus_type : DATA;\n            direction : output;\n            max_capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                port, output_cap, max(transition[0])))
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
            # The Liberty tables are measured at this single input slew.
            emit("        /* port {}: slew {:.4f} ns; loads {:.4f}..{:.4f} pF */\n".format(
                port, input_slew, loads_pf[0], loads_pf[1]))
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
        emit("    }\n}\n")
        output_path = os.path.join(args.outdir, name + ".lib")
        with open(output_path, "w") as output_file:
            output_file.write("".join(library))
        print("{}: ports={} loads={:.1f}/{:.1f} fF slew={:.3f} ns".format(
            corner, ",".join(map(str, args.ports)),
            pairs[args.ports[0]][0][0], pairs[args.ports[0]][1][0],
            pairs[args.ports[0]][0][1]))


if __name__ == "__main__":
    main()
