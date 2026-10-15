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
        for i, port0 in enumerate(write_ports):
            for port1 in write_ports[i + 1:]:
                conditions = [f"(csb{port0} === 1'b0)"]
                if port0 in rw_ports:
                    conditions.append(f"(web{port0} === 1'b0)")
                conditions.append(f"(csb{port1} === 1'b0)")
                if port1 in rw_ports:
                    conditions.append(f"(web{port1} === 1'b0)")
                conditions.append(
                    f"((addr{port0} >> {sram.col_addr_size}) "
                    f"=== (addr{port1} >> {sram.col_addr_size}))")
                conflict_condition = "\n        && ".join(conditions)
                message = (f"OpenRAM overlapping writes to the same physical row "
                           f"on ports {port0} and {port1} are undefined")
                check = f"""  always @* begin
    if ({conflict_condition}) begin
      $fatal(1, "{message}");
    end
  end
  initial begin
    #0;
    if ({conflict_condition}) begin
      $fatal(1, "{message}");
    end
  end
"""
                write_conflict_checks.append(check)
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
