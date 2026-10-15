# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#
import os
import re
from math import ceil, log
from openram import OPTS
from .template import template


class sram_multibank:

    def __init__(self, sram):
        rw_ports = [i for i in sram.all_ports if i in sram.read_ports and i in sram.write_ports]
        r_ports = [i for i in sram.all_ports if i in sram.read_ports and i not in sram.write_ports]
        w_ports = [i for i in sram.all_ports if i not in sram.read_ports and i in sram.write_ports]
        write_ports = rw_ports + w_ports
        write_conflict_checks = []
        if len(write_ports) > 1:
            for port in write_ports:
                write_conflict_checks.extend([
                    f"  reg port{port}_write_pending;",
                    f"  reg [ADDR_WIDTH-1:0] port{port}_write_pending_addr;",
                    f"  reg port{port}_write_completed_valid;",
                    f"  reg [ADDR_WIDTH-1:0] port{port}_write_completed_addr;",
                    f"  realtime port{port}_write_completed_time;",
                    "  initial begin",
                    f"    port{port}_write_pending = 1'b0;",
                    f"    port{port}_write_pending_addr = 0;",
                    f"    port{port}_write_completed_valid = 1'b0;",
                    f"    port{port}_write_completed_addr = 0;",
                    f"    port{port}_write_completed_time = 0.0;",
                    "  end",
                ])

            for port in write_ports:
                if port in rw_ports:
                    write_enabled = f"(csb{port} === 1'b0) && (web{port} === 1'b0)"
                else:
                    write_enabled = f"(csb{port} === 1'b0)"
                write_conflict_checks.extend([
                    f"  always @(posedge clk{port}) begin",
                    f"    if ({write_enabled}) begin",
                    f"      port{port}_write_pending_addr = addr{port};",
                    f"      port{port}_write_pending = 1'b1;",
                    "      #0;",
                ])
                for other in write_ports:
                    if other == port:
                        continue
                    row_match = (
                        f"((port{other}_write_pending_addr >> {sram.col_addr_size}) "
                        f"=== (port{port}_write_pending_addr >> {sram.col_addr_size}))")
                    pending_conflict = (
                        f"((port{other}_write_pending === 1'b1) && {row_match})")
                    completed_row_match = (
                        f"((port{other}_write_completed_addr >> {sram.col_addr_size}) "
                        f"=== (port{port}_write_pending_addr >> {sram.col_addr_size}))")
                    boundary_conflict = (
                        f"((port{other}_write_completed_valid === 1'b1) && "
                        f"(port{other}_write_completed_time == $realtime) && {completed_row_match})")
                    message = (f"OpenRAM overlapping writes to the same physical row "
                               f"on ports {port} and {other} are undefined")
                    write_conflict_checks.extend([
                        f"      if ({pending_conflict} || {boundary_conflict})",
                        f"        $fatal(1, \"{message}\");",
                    ])
                write_conflict_checks.extend([
                    "    end",
                    "  end",
                    f"  always @(negedge clk{port}) begin",
                    f"    if (port{port}_write_pending === 1'b1) begin",
                    f"      port{port}_write_completed_addr = port{port}_write_pending_addr;",
                    f"      port{port}_write_completed_time = $realtime;",
                    f"      port{port}_write_completed_valid = 1'b1;",
                    f"      #0 port{port}_write_pending = 1'b0;",
                    "    end",
                    "  end",
                ])
        write_conflict_checks = "\n".join(write_conflict_checks)
        self.dict = {
            'module_name': sram.name + '_top',
            'bank_module_name': sram.name,
            'vdd': 'vdd',
            'gnd': 'gnd',
            'ports': sram.all_ports,
            'rw_ports': rw_ports,
            'r_ports': r_ports,
            'w_ports': w_ports,
            'write_conflict_checks': write_conflict_checks,
            'banks': list(range(sram.num_banks)),
            'data_width': sram.word_size,
            'addr_width': sram.bank_addr_size + ceil(log(sram.num_banks, 2)),
            'bank_sel': ceil(log(sram.num_banks, 2)),
            'col_addr_width': sram.col_addr_size,
            'num_wmask': sram.num_wmasks,
            'write_size': sram.write_size
        }

    def verilog_write(self, name):
        template_filename = os.path.join(os.path.abspath(os.environ["OPENRAM_HOME"]), "modules/sram_multibank_template.v")
        t = template(template_filename, self.dict)
        t.write(name)
        with open(name, 'r') as f:
            text = f.read()
            badComma = re.compile(r',(\s*\n\s*\);)')
            text = badComma.sub(r'\1', text)
        with open(name, 'w') as f:
            f.write(text)
