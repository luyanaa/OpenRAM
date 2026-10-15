#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import os
import shutil
import subprocess
import tempfile
import unittest

import openram
from openram import OPTS


class write_window_assertion_test(unittest.TestCase):

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"),
                         "Icarus Verilog is required for behavioral assertion regressions")
    def runTest(self):
        OPTS.tech_name = "freepdk45"
        OPTS.route_supplies = False
        OPTS.check_lvsdrc = False
        OPTS.netlist_only = True
        OPTS.num_rw_ports = 2
        OPTS.num_r_ports = 0
        OPTS.num_w_ports = 0
        config_file = "{}/tests/configs/config.py".format(os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)
        try:

            from openram import sram
            from openram import sram_config

            config = sram_config(word_size=8, num_words=16, num_banks=2, words_per_row=2)
            design = sram(config, "sram_write_window_2rw")
            with tempfile.TemporaryDirectory(prefix="openram_write_windows_") as temp_dir:
                model_file = os.path.join(temp_dir, design.name + ".v")
                design.verilog_write(model_file)
                top_file = os.path.join(temp_dir, design.name + "_top.v")

                model_width = design.bank_addr_size
                top_width = design.bank_addr_size + 1
                for generated_files, module_name, address_width, multibank in [
                        ([model_file], design.name, model_width, False),
                        ([model_file, top_file], design.name + "_top", top_width, True)]:
                    self.run_model_regressions(generated_files, module_name, address_width,
                                               design.bank_addr_size, multibank, temp_dir)
        finally:
            openram.end_openram()

    def run_model_regressions(self, generated_files, module_name, address_width,
                              local_address_width, multibank, temp_dir):
        testbench_file = os.path.join(temp_dir, "write_window_tb.v")
        executable = os.path.join(temp_dir, "write_window_sim")
        with open(testbench_file, "w") as testbench:
            testbench.write(self.generate_testbench_source(module_name, address_width,
                                                  local_address_width, multibank))

        compile_result = subprocess.run(
            [shutil.which("iverilog"), "-g2012", "-DOPENRAM_ASSERTIONS", "-s", "tb",
             "-o", executable] + generated_files + [testbench_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(compile_result.returncode, 0, compile_result.stdout)

        positive_cases = [1, 4, 5, 6, 9, 10]
        if multibank:
            positive_cases.append(7)
        negative_cases = [0, 2, 3, 8, 11, 12, 13, 14, 15]
        for case_id in positive_cases:
            result = self.run_simulation(executable, case_id)
            self.assertEqual(result.returncode, 0,
                             "positive case {} failed:\n{}".format(case_id, result.stdout))
            self.assertIn("PASS_CASE_{}".format(case_id), result.stdout)
        for case_id in negative_cases:
            result = self.run_simulation(executable, case_id)
            self.assertNotEqual(result.returncode, 0,
                                "negative case {} unexpectedly passed:\n{}".format(case_id, result.stdout))
            self.assertIn("OpenRAM overlapping writes to the same physical row", result.stdout)
        unguarded_executable = os.path.join(temp_dir, "write_window_sim_unguarded")
        compile_result = subprocess.run(
            [shutil.which("iverilog"), "-g2012", "-s", "tb", "-o", unguarded_executable] +
            generated_files + [testbench_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(compile_result.returncode, 0, compile_result.stdout)
        result = self.run_simulation(unguarded_executable, 0)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PASS_CASE_0", result.stdout)

    @staticmethod
    def run_simulation(executable, case_id):
        return subprocess.run([shutil.which("vvp"), executable, "+case={}".format(case_id)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)

    @staticmethod
    def generate_testbench_source(module_name, address_width, local_address_width, multibank):
        if multibank:
            memory_check = """
      if ((address >> {local_width}) == 0) begin
        if (dut.bank0.mem[address & {local_mask}] !== expected)
          $fatal(1, \"bank0 write completion mismatch at %0d\", address);
      end else begin
        if (dut.bank1.mem[address & {local_mask}] !== expected)
          $fatal(1, \"bank1 write completion mismatch at %0d\", address);
      end
""".format(local_width=local_address_width,
           local_mask=(1 << local_address_width) - 1)
        else:
            memory_check = """
      if (dut.mem[address] !== expected)
        $fatal(1, \"write completion mismatch at %0d\", address);
"""

        return """`timescale 1ns/1ps
module tb;
  reg clk0, clk1;
  reg csb0, csb1, web0, web1;
  reg [__ADDR_MSB__:0] addr0, addr1;
  reg [7:0] din0, din1;
  wire [7:0] dout0, dout1;
  integer case_id;

  __MODULE_NAME__ dut (
    .clk0(clk0), .csb0(csb0), .web0(web0), .addr0(addr0), .din0(din0), .dout0(dout0),
    .clk1(clk1), .csb1(csb1), .web1(web1), .addr1(addr1), .din1(din1), .dout1(dout1)
  );

  task check_word;
    input integer address;
    input [7:0] expected;
    begin
__MEMORY_CHECK__    end
  endtask

  initial begin
    clk0 = 0; clk1 = 0;
    csb0 = 1; csb1 = 1; web0 = 1; web1 = 1;
    addr0 = 0; addr1 = 0;
    din0 = 8'hA5; din1 = 8'h5A;
    if (!$value$plusargs(\"case=%d\", case_id)) $fatal(1, \"missing case id\");
    case (case_id)
      0: begin // Same-row asynchronous overlap.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #5; addr1 = 1; csb1 = 0; web1 = 0; clk1 = 1;
        #5; clk0 = 0;
        #5; clk1 = 0;
      end
      1: begin // Different rows complete during overlapping windows.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #5; addr1 = 2; csb1 = 0; web1 = 0; clk1 = 1;
        #5; clk0 = 0;
        #5; clk1 = 0;
        #1; check_word(1, 8'hA5); check_word(2, 8'h5A);
      end
      2: begin // A falling-edge completion coincides with a new write sample.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #9; addr1 = 1; csb1 = 0; web1 = 0;
        #1; clk0 = 0; clk1 = 1;
        #5; clk1 = 0;
      end
      3: begin // Port 0 stops high, leaving its write pending.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #5; addr1 = 1; csb1 = 0; web1 = 0; clk1 = 1;
      end
      4: begin // An external write request while stopped low is not sampled.
        #4; clk0 = 1;
        #3; clk0 = 0;
        #1; addr0 = 1; csb0 = 0; web0 = 0;
        addr1 = 1; csb1 = 0; web1 = 0;
        #3; clk1 = 1;
        #5; clk1 = 0;
        #1; check_word(1, 8'h5A);
      end
      5: begin // Raw address changes do not change the sampled write row.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #1; addr0 = 2;
        #1; addr1 = 2; csb1 = 0; web1 = 0;
        #2; clk1 = 1;
        #7; clk0 = 0;
        #5; clk1 = 0;
        #1; check_word(1, 8'hA5); check_word(2, 8'h5A);
      end
      6: begin // Different clock periods and phases; distinct rows overlap.
        addr0 = 1; addr1 = 2; csb0 = 0; csb1 = 0; web0 = 0; web1 = 0;
        fork
          begin
            #3; clk0 = 1;
            #1; csb0 = 1; web0 = 1;
            #9; clk0 = 0;
            #10; clk0 = 1;
            #5; clk0 = 0;
          end
          begin
            #8; clk1 = 1;
            #1; csb1 = 1; web1 = 1;
            #5; clk1 = 0;
            #8; clk1 = 1;
            #1; clk1 = 0;
          end
        join
        #1; check_word(1, 8'hA5); check_word(2, 8'h5A);
      end
      7: begin // Equal local rows in distinct banks are independent.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #5; addr1 = 9; csb1 = 0; web1 = 0; clk1 = 1;
        #5; clk0 = 0;
        #5; clk1 = 0;
        #1; check_word(1, 8'hA5); check_word(9, 8'h5A);
      end
      8: begin // A raw deassert/address change cannot hide a sampled conflict.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #1; addr0 = 2; csb0 = 1; web0 = 1;
        #3; addr1 = 1; csb1 = 0; web1 = 0; clk1 = 1;
        #5; clk0 = 0;
        #5; clk1 = 0;
      end
      9: begin // A read sample on the same row is not a competing write.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #3; addr1 = 1; csb1 = 0; web1 = 1; clk1 = 1;
        #5; clk0 = 0;
        #5; clk1 = 0;
        #1; check_word(1, 8'hA5);
      end
      10: begin // Reverse phase ordering with different clock periods.
        addr0 = 2; addr1 = 1; csb0 = 0; csb1 = 0; web0 = 0; web1 = 0;
        fork
          begin
            #10; clk0 = 1;
            #1; csb0 = 1; web0 = 1;
            #3; clk0 = 0;
            #9; clk0 = 1;
            #3; clk0 = 0;
          end
          begin
            #3; clk1 = 1;
            #1; csb1 = 1; web1 = 1;
            #7; clk1 = 0;
            #11; clk1 = 1;
            #3; clk1 = 0;
          end
        join
        #1; check_word(1, 8'h5A); check_word(2, 8'hA5);
      end
      11: begin // Both same-row write windows start on the same simulation edge.
        #4; addr0 = 1; addr1 = 1; csb0 = 0; csb1 = 0; web0 = 0; web1 = 0;
        clk0 = 1; clk1 = 1;
        #5; clk0 = 0; clk1 = 0;
      end
      12: begin // Completion is processed before a same-time write start.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #5; clk0 = 0;
        #0;
        #0; addr1 = 1; csb1 = 0; web1 = 0; clk1 = 1;
        #5; clk1 = 0;
      end
      13: begin // The same-time write start is processed before completion.
        #4; addr0 = 1; csb0 = 0; web0 = 0; clk0 = 1;
        #5; addr1 = 1; csb1 = 0; web1 = 0; clk1 = 1;
        #0;
        #0; clk0 = 0;
        #5; clk1 = 0;
      end
      14: begin // Different word addresses in the same physical row conflict.
        #4; addr0 = 0; csb0 = 0; web0 = 0; clk0 = 1;
        #5; addr1 = 1; csb1 = 0; web1 = 0; clk1 = 1;
        #5; clk0 = 0;
        #5; clk1 = 0;
      end
      15: begin // Different-frequency clocks overlap on one physical row.
        addr0 = 0; addr1 = 1;
        csb0 = 1; csb1 = 1; web0 = 1; web1 = 1;
        fork
          begin
            #3; clk0 = 1;
            #10; clk0 = 0;
            #10; csb0 = 0; web0 = 0; clk0 = 1;
            #1; csb0 = 1; web0 = 1;
            #9; clk0 = 0;
          end
          begin
            #8; clk1 = 1;
            #1; csb1 = 0; web1 = 0;
            #6; clk1 = 0;
            #7; clk1 = 1;
            #1; csb1 = 1; web1 = 1;
            #6; clk1 = 0;
          end
        join
      end
      default: $fatal(1, \"unknown case id %0d\", case_id);
    endcase
    $display(\"PASS_CASE_%0d\", case_id);
    $finish;
  end
endmodule
""".replace("__MODULE_NAME__", module_name).replace(
            "__ADDR_MSB__", str(address_width - 1)).replace("__MEMORY_CHECK__", memory_check)


if __name__ == "__main__":
    unittest.main()
