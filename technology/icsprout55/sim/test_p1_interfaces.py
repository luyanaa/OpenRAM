"""Contract tests for ICsprout55 package, characterization, and Liberty roles."""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from technology.icsprout55.scripts import package_macro
from technology.icsprout55.sim import charz, mklib, tbgen


LIBRARIES = {
    "SC9T": "DFFQX1_9TSVT",
    "H7CR": "DFFQX1H7R",
}
ROLE_CASES = (
    {0: "RW"},
    {0: "W", 1: "R"},
    {0: "RW", 1: "RW"},
)


def behavioral_model(name, roles):
    declarations = []
    for port, role in roles.items():
        pins = ["clk{}".format(port), "csb{}".format(port)]
        if role == "RW":
            pins.append("web{}".format(port))
        pins.append("addr{}".format(port))
        if role in ("RW", "W"):
            pins.append("din{}".format(port))
        if role in ("RW", "R"):
            pins.append("dout{}".format(port))
        declarations.append("// Port {}: {}\n    {}".format(
            port, role, ", ".join(pins)))
    return """module {name} #(parameter ADDR_WIDTH=4, DATA_WIDTH=8) (
{ports}
);
endmodule
""".format(name=name, ports=",\n".join(declarations))


def braced_group(text, declaration):
    start = text.index(declaration)
    opening = text.index("{", start)
    depth = 0
    for end in range(opening, len(text)):
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
            if depth == 0:
                return text[start:end + 1]
    raise AssertionError("unterminated Liberty group {}".format(declaration))


def measurement_metrics(role, port, load):
    values = {
        "role": role,
        "ins_rise": 0.12 + port * 0.02,
        "chain_reset": 0.38 + port * 0.03,
        "leak_mw": 0.006,
        "e_idle_pj": 0.08 + port * 0.01,
    }
    if role in ("RW", "R"):
        values.update({
            "dout_fall": 0.60 + port * 0.10 + load / 1000.0,
            "dout_tran": 0.10 + port * 0.02 + load / 1000.0,
            "dout_hold_after_rise": 0.30,
            "e_read_pj": 0.40 + port * 0.10,
        })
    if role in ("RW", "W"):
        values.update({
            "wen_end": 0.45 + port * 0.10,
            "e_write_pj": 0.50 + port * 0.10,
        })
    return values


def measurement_file(library, roles):
    def sample(load):
        return {
            "stdcell_library": library,
            "domain": {
                "load_ff": load,
                "input_slew_ns": 0.03,
                "period_ns": 8.0,
                "corner": "tt",
                "vdd": 1.20,
                "temp": 25,
            },
            "ports": {
                str(port): measurement_metrics(role, port, load)
                for port, role in roles.items()
            },
        }

    return {
        "tt": {
            "m5": sample(5.0),
            "m50": sample(50.0),
            "dff_setup": {str(port): 0.08 + port * 0.01 for port in roles},
            "dff_hold": {str(port): 0.02 + port * 0.01 for port in roles},
            "vdd": 1.20,
            "temp": 25,
        }
    }


class PackageAndLibertyContractTests(unittest.TestCase):
    def test_lef_uses_clock_antenna_area_for_every_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, "input.lef")
            output = os.path.join(temp, "output.lef")
            with open(source, "w") as lef:
                lef.write("""VERSION 5.4 ;
DATABASE MICRONS 1 ;
MACRO mem
  SIZE 10 BY 20 ;
   PIN clk0
      DIRECTION INPUT ;
      PORT
      END
   END clk0
   PIN clk1
      DIRECTION INPUT ;
      PORT
      END
   END clk1
   PIN addr0
      DIRECTION INPUT ;
      PORT
      END
   END addr0
END mem
END LIBRARY
""")
            package_macro.lef(source, output, "mem", 0.0318, 0.027, 0.2)
            with open(output) as lef:
                text = lef.read()
            self.assertEqual(text.count("ANTENNAGATEAREA 0.0270 LAYER MET4 ;"), 2)
            self.assertEqual(text.count("ANTENNAGATEAREA 0.0318 LAYER MET4 ;"), 1)

    def test_six_library_and_port_role_combinations(self):
        cases = [(library, roles)
                 for library in LIBRARIES
                 for roles in ROLE_CASES]
        for library, roles in cases:
            with self.subTest(library=library, roles=roles), tempfile.TemporaryDirectory() as temp:
                macro = "mem"
                source = os.path.join(temp, macro + ".v")
                with open(source, "w") as output:
                    output.write(behavioral_model(macro, roles))
                trace_dir = os.path.join(temp, "tb")
                tbgen.gen(source, trace_dir, 24, 1)
                with open(os.path.join(trace_dir, "tb.v")) as input_file:
                    generated_tb = input_file.read()
                for port, role in roles.items():
                    self.assertIn("role{}={} op{}=".format(port, role, port), generated_tb)
                    self.assertRegex(generated_tb, r"\bcsb{}=0;".format(port))
                    self.assertRegex(generated_tb, r"\baddr{}=4'd\d+;".format(port))
                _, _, _, parsed_roles = package_macro.behavioral_ports(source, macro)
                packaged_model = os.path.join(temp, "packaged.v")
                stub = os.path.join(temp, "stub.v")
                package_macro.verilog(source, packaged_model, stub, macro, 16, 8, parsed_roles)

                with open(stub) as input_file:
                    stub_text = input_file.read()
                declarations = {
                    name: direction
                    for direction, name in re.findall(
                        r"^\s*(input|output)\s+(?:\[\d+:\d+\]\s*)?(\w+)\s*[,;]?$",
                        stub_text, re.M)
                }
                expected = {}
                for port, role in roles.items():
                    expected.update({
                        "clk{}".format(port): "input",
                        "csb{}".format(port): "input",
                        "addr{}".format(port): "input",
                    })
                    if role == "RW":
                        expected["web{}".format(port)] = "input"
                    if role in ("RW", "W"):
                        expected["din{}".format(port)] = "input"
                    if role in ("RW", "R"):
                        expected["dout{}".format(port)] = "output"
                self.assertEqual(declarations, expected)

                dff = LIBRARIES[library]
                foundry_cdl = os.path.join(temp, "stdcell.cdl")
                with open(foundry_cdl, "w") as output:
                    output.write(".SUBCKT {} CK D Q VDD VSS\n.ENDS {}\n".format(dff, dff))
                self.assertEqual(package_macro.foundry_ports(foundry_cdl, dff),
                                 ("CK", "D", "Q", "VDD", "VSS"))
                source_netlist = os.path.join(temp, "source.lvs.sp")
                packaged_cdl = os.path.join(temp, "packaged.cdl")
                with open(source_netlist, "w") as output:
                    output.write(
                        ".SUBCKT {} D Q clk vdd gnd vnw vpw\n.ENDS {}\n"
                        ".SUBCKT {} vdd gnd\n"
                        "XU0 din0 dout0 clk0 vdd gnd vnw vpw {}\n"
                        ".ENDS {}\n".format(dff, dff, macro, dff, macro))
                package_macro.netlist(
                    source_netlist, packaged_cdl, macro, dff=dff,
                    dff_ports=package_macro.foundry_ports(foundry_cdl, dff),
                    stdcell_library=library)
                with open(packaged_cdl) as input_file:
                    cdl_text = input_file.read()
                self.assertIn("XU0 clk0 din0 dout0 VDD VSS {}".format(dff), cdl_text)
                self.assertNotRegex(cdl_text, r"(?im)^\.SUBCKT\s+{}\b".format(dff))

                measurement = os.path.join(temp, "measurements.json")
                with open(measurement, "w") as output:
                    json.dump(measurement_file(library, roles), output)
                with mock.patch.object(sys, "argv", [
                    "mklib.py", measurement, macro, temp,
                    "--words", "16", "--bits", "8", "--area", "42",
                    "--stdcell-library", library,
                    "--port-roles", ",".join(
                        "{}:{}".format(port, role) for port, role in roles.items()),
                ]):
                    mklib.main()
                with open(os.path.join(temp, "{}_tt.lib".format(macro))) as input_file:
                    liberty = input_file.read()
                self.assertIn("stdcell {} ({})".format(library, dff), liberty)
                self.assertEqual(liberty.count("memory_read ()"),
                                 sum(role in ("RW", "R") for role in roles.values()))
                self.assertEqual(liberty.count("memory_write ()"),
                                 sum(role in ("RW", "W") for role in roles.values()))
                for port, role in roles.items():
                    clock = braced_group(liberty, "pin (clk{})".format(port))
                    self.assertIn("direction : input", clock)
                    for pin in ("csb{}".format(port), "addr{}".format(port)):
                        self.assertIn("direction : input", braced_group(
                            liberty, "pin ({})".format(pin) if pin.startswith("csb")
                            else "bus ({})".format(pin)))
                    if role == "RW":
                        self.assertIn("direction : input", braced_group(
                            liberty, "pin (web{})".format(port)))
                        self.assertIn('when : "!csb{} & web{}"'.format(port, port), clock)
                        self.assertIn('when : "!csb{} & !web{}"'.format(port, port), clock)
                    else:
                        self.assertNotIn("web{}".format(port), liberty)
                        self.assertIn('when : "!csb{}"'.format(port), clock)
                    if role in ("RW", "W"):
                        data_in = braced_group(liberty, "bus (din{})".format(port))
                        self.assertIn("direction : input", data_in)
                        self.assertIn("memory_write ()", data_in)
                    else:
                        self.assertNotIn("bus (din{})".format(port), liberty)
                    if role in ("RW", "R"):
                        data_out = braced_group(liberty, "bus (dout{})".format(port))
                        self.assertIn("direction : output", data_out)
                        self.assertIn("memory_read ()", data_out)
                        self.assertIn("timing_type : falling_edge", data_out)
                        self.assertIn("cell_rise (delay_tbl{})".format(port), data_out)
                        self.assertIn("cell_fall (delay_tbl{})".format(port), data_out)
                    else:
                        self.assertNotIn("bus (dout{})".format(port), liberty)
                        self.assertNotIn("lu_table_template (delay_tbl{})".format(port), liberty)
                read_ports = [port for port, role in roles.items()
                              if role in ("RW", "R")]
                if len(read_ports) > 1:
                    values = []
                    for port in read_ports:
                        group = braced_group(
                            braced_group(liberty, "bus (dout{})".format(port)),
                            "cell_rise (delay_tbl{})".format(port))
                        values.append(re.search(r'values\s*\("([^"]+)"\)', group).group(1))
                    self.assertEqual(len(set(values)), len(read_ports))

    def test_mklib_rejects_measurement_from_another_library(self):
        roles = {0: "RW"}
        with tempfile.TemporaryDirectory() as temp:
            measurement = os.path.join(temp, "measurements.json")
            with open(measurement, "w") as output:
                json.dump(measurement_file("SC9T", roles), output)
            with mock.patch.object(sys, "argv", [
                "mklib.py", measurement, "mem", temp,
                "--words", "16", "--bits", "8", "--area", "42",
                "--stdcell-library", "H7CR", "--port-roles", "0:RW",
            ]):
                with self.assertRaisesRegex(ValueError, "sample uses SC9T, requested H7CR"):
                    mklib.main()


    def test_supported_library_variants_and_fail_closed_cdl(self):
        expected = {
            "SC9T": "DFFQX1_9TSVT", "SC9TL": "DFFQX1_9TLVT",
            "SC9TH": "DFFQX1_9THVT", "H7CR": "DFFQX1H7R",
            "H7CL": "DFFQX1H7L", "H7CH": "DFFQX1H7H",
        }
        for library, dff in expected.items():
            with self.subTest(library=library):
                self.assertEqual(package_macro.STDCELL_DFF[library], dff)
                self.assertEqual(mklib.STDCELL_DFF[library], dff)
                self.assertEqual(package_macro.normalize_stdcell_library(library), library)
                self.assertEqual(mklib.normalize_stdcell_library(library), library)
        aliases = {"9T": "SC9T", "SC9TSVT": "SC9T", "7T": "H7CR"}
        for normalizer in (package_macro.normalize_stdcell_library,
                           mklib.normalize_stdcell_library):
            for alias, library in aliases.items():
                self.assertEqual(normalizer(alias), library)
        with tempfile.TemporaryDirectory() as temp:
            cdl = os.path.join(temp, "wrong.cdl")
            with open(cdl, "w") as output:
                output.write(".SUBCKT DFFQX1H7R D Q CK VDD VSS\n.ENDS\n")
            with self.assertRaisesRegex(ValueError, "terminal order.*CK D Q VDD VSS"):
                package_macro.foundry_ports(cdl, "DFFQX1H7R")
            with self.assertRaisesRegex(ValueError, "lacks expected subcircuit"):
                package_macro.foundry_ports(cdl, "DFFQX1H7L")

    def test_characterization_classifies_pure_read_and_write_without_web(self):
        times = [index * 0.1e-9 for index in range(111)]
        for role, active_operation in (("R", "R"), ("W", "W")):
            cycles = {
                cycle: {
                    "role0": role,
                    "op0": active_operation if cycle == 1 else "I",
                    "csb0": "0" if cycle == 1 else "1",
                }
                for cycle in range(6)
            }
            self.assertNotIn("web0", cycles[1])
            data = {
                "clk0": [0.0] * len(times),
                "clk_buf0": [0.0] * len(times),
                "XSRAM.Xcontrol0.rbl_bl_delay": [0.0] * len(times),
                "VDD:p": [-1e-3] * len(times),
            }
            if role == "W":
                data["w_en0"] = [0.0] * len(times)
            else:
                data["dout0[0]"] = [0.0] * len(times)

            def measured_transitions(_times, _data, names, _threshold, direction=None):
                if names == ["clk0"]:
                    return [2.0e-9]
                if names == ["clk_buf0"]:
                    return [2.1e-9]
                if any("rbl_bl_delay" in name for name in names):
                    return [3.2e-9]
                if any("w_en0" in name for name in names):
                    return [2.7e-9]
                return []

            with mock.patch.object(charz, "transitions", side_effect=measured_transitions):
                metrics = charz.measure_port(
                    times, data, cycles, 0, 1.0, 1.2, "VDD:p")
            self.assertEqual(metrics["role"], role)
            self.assertEqual(metrics["read_cycles"], 1 if role == "R" else 0)
            self.assertEqual(metrics["write_cycles"], 1 if role == "W" else 0)
            self.assertIsNone(metrics["e_write_pj"] if role == "R" else metrics["e_read_pj"])


if __name__ == "__main__":
    unittest.main()
