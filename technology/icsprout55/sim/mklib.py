#!/usr/bin/env python3
"""Export explicitly incomplete, polarity-specific SRAM timing tables.

Only --diagnostic-table-only is supported. Full STA export requires independently
verified macro boundary sweeps and measured input/clock loading; JSON assertions
and DFF constants are not substitutes. Diagnostic tables are always dont_use.
"""
import argparse
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from compiler.architecture.contracts import load_json

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
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("missing finite measurement {}".format(key))
    return float(value)


def measurement_pair(corner_data, port, input_slew_ns=None):
    samples = []
    required_domain = {"period_ns", "load_ff", "input_slew_ns", "corner", "vdd", "temp"}
    for label in ("m5", "m50"):
        sample = corner_data[label]
        if sample.get("schema_version") != 3:
            raise ValueError("numeric export requires schema_version 3")
        domain = sample.get("domain", {})
        if set(domain) != required_domain:
            raise ValueError("sample requires complete measured domain")
        for key in required_domain - {"corner"}:
            required_number(domain, key)
        load, slew = domain["load_ff"], domain["input_slew_ns"]
        if input_slew_ns is not None and (
                type(input_slew_ns) not in (int, float) or not math.isfinite(input_slew_ns)
                or input_slew_ns != slew):
            raise ValueError("requested slew differs from recorded input slew")
        if load <= 0 or slew <= 0 or domain["period_ns"] <= 0 or domain["vdd"] <= 0:
            raise ValueError("load, slew, period and voltage must be positive")
        if not isinstance(domain["corner"], str) or not domain["corner"]:
            raise ValueError("missing measured corner")
        if sample.get("functional") is not True:
            raise ValueError("timing export requires functional success")
        if domain["vdd"] != corner_data.get("vdd") or domain["temp"] != corner_data.get("temp"):
            raise ValueError("sample domain differs from root PVT")
        samples.append((load, slew, domain, port_sample(sample, port)))
    samples.sort(key=lambda item: item[0])
    if samples[0][0] >= samples[1][0]:
        raise ValueError("m5 and m50 must describe distinct output loads")
    for key in required_domain - {"load_ff"}:
        if samples[0][2][key] != samples[1][2][key]:
            raise ValueError("m5 and m50 domains differ for " + key)
    return samples[0], samples[1]


def _observed_read_tables(pair):
    """Keep only polarities with delay and transition measured at both loads."""
    tables = {}
    for keys in (("cell_rise", "rise_transition"), ("cell_fall", "fall_transition")):
        values = {key: [] for key in keys}
        for _, _, _, metrics in pair:
            for key in keys:
                if key not in metrics:
                    raise ValueError("missing measurement field " + key)
                values[key].append(None if metrics[key] is None else required_number(metrics, key))
        if all(value is not None for row in values.values() for value in row):
            tables.update({key: [row] for key, row in values.items()})
    if not tables:
        raise ValueError("read port has no complete measured polarity at both loads")
    return tables


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
    parser.add_argument("--input-slew-ns", type=float,
                        help="optional assertion of the exact recorded input slew")
    parser.add_argument("--power-pins", default="vdd,gnd",
                        help="names of the power and ground pins")
    parser.add_argument("--diagnostic-table-only", action="store_true",
                        help="explicitly unusable/incomplete timing tables; never complete STA/F4")
    args = parser.parse_args()
    if not args.diagnostic_table_only:
        parser.error("complete STA export is unavailable without waveform-verified macro boundaries "
                     "and measured input/clock loading; use --diagnostic-table-only for dont_use tables")
    try:
        stdcell_library = normalize_stdcell_library(args.stdcell_library)
    except ValueError as error:
        parser.error(str(error))
    ports = sorted(args.port_roles) if args.ports is None else args.ports
    if not ports or set(ports) != set(args.port_roles):
        parser.error("--ports must select exactly the indices declared by --port-roles")
    if args.words <= 0 or args.bits <= 0 or not math.isfinite(args.area) or args.area <= 0:
        parser.error("--words, --bits, and --area must be positive")
    power_pin, ground_pin = args.power_pins.split(",")
    measurements = load_json(args.meas)
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
        if pairs[ports[0]][0][2]["corner"] != corner:
            raise ValueError("sample corner differs from root corner key")
        for port in ports:
            role = args.port_roles[port]
            for sample in pairs[port]:
                metrics = sample[3]
                if metrics.get("role") != role:
                    raise ValueError(
                        "{} port {} measurement role {} does not match requested {}".format(
                            corner, port, metrics.get("role", "missing"), role))
        read_ports = [port for port in ports if args.port_roles[port] in ("RW", "R")]
        read_tables = {port: _observed_read_tables(pairs[port]) for port in read_ports}
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
        emit("   INCOMPLETE DIAGNOSTIC ONLY: raw polarity-specific observed read tables.\n")
        emit("   Not STA/F4: no capture constraints, minima, capacitance or intrinsic power.\n")
        emit("   Only two measured loads and one slew/PVT; tested period is not Tmin. */\n")
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
        if read_ports:
            maximum_output_transition = max(
                value for port in read_ports for key, rows in read_tables[port].items()
                if key.endswith("_transition") for row in rows for value in row)
            emit("    default_max_transition : {:.4f};\n".format(
                maximum_output_transition))
        emit("    default_fanout_load : 1;\n")
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
        emit("        area : {:.3f};\n        dont_touch : true;\n        dont_use : true;\n        interface_timing : true;\n".format(
            args.area))
        emit("        memory () {{\n            type : ram;\n            address_width : {};\n            word_width : {};\n        }}\n".format(address_width, args.bits))
        emit("        pg_pin ({}) {{\n            voltage_name : {};\n            pg_type : primary_power;\n        }}\n".format(power_pin, power_pin))
        emit("        pg_pin ({}) {{\n            voltage_name : {};\n            pg_type : primary_ground;\n        }}\n".format(ground_pin, ground_pin))

        for port in ports:
            role = args.port_roles[port]
            can_read = role in ("RW", "R")
            can_write = role in ("RW", "W")
            low_load, high_load = pairs[port]
            low_metrics, high_metrics = low_load[3], high_load[3]
            loads_pf = [low_load[0] / 1000.0, high_load[0] / 1000.0]
            input_slew = low_load[1]

            emit("        pin (clk{}) {{\n            direction : input;\n            clock : true;\n".format(port))
            emit("            max_transition : {:.4f};\n".format(input_slew))
            emit("        }\n")

            input_pins = ["csb{}".format(port)]
            if role == "RW":
                input_pins.append("web{}".format(port))
            for pin in input_pins:
                emit("        pin ({}) {{\n            direction : input;\n            max_transition : {:.4f};\n".format(
                    pin, input_slew))
                emit("        }\n")
            emit("        bus (addr{}) {{\n            bus_type : ADDR;\n            direction : input;\n            max_transition : {:.4f};\n".format(
                port, input_slew))
            emit("        }\n")

            if can_write:
                emit("        bus (din{}) {{\n            bus_type : DATA;\n            direction : input;\n            max_transition : {:.4f};\n".format(
                    port, input_slew))
                emit('            memory_write () {{\n                address : addr{};\n                clocked_on : "clk{}";\n            }}\n'.format(
                    port, port))
                emit("        }\n")

            if can_read:
                tables = read_tables[port]
                transition_values = [value for key, rows in tables.items()
                                     if key.endswith("_transition") for row in rows for value in row]
                emit("        bus (dout{}) {{\n            bus_type : DATA;\n            direction : output;\n            max_capacitance : {:.4f};\n            max_transition : {:.4f};\n".format(
                    port, max(loads_pf), max(transition_values)))
                emit("            memory_read () {{\n                address : addr{};\n            }}\n".format(port))
                for direction in ("rise", "fall"):
                    if "cell_" + direction not in tables:
                        emit("            /* {} tables omitted: not observed on every bit at both loads. */\n".format(
                            direction))
                emit('            timing () {{\n                related_pin : "clk{}";\n                timing_type : falling_edge;\n                timing_sense : non_unate;\n'.format(port))
                for key in ("cell_rise", "cell_fall", "rise_transition", "fall_transition"):
                    if key not in tables:
                        continue
                    emit("                {} (delay_tbl{}) {{\n".format(key, port))
                    emit(table(tables[key], "                    "))
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
