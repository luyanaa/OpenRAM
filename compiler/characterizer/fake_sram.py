# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#
from openram import sram_config
from math import ceil
from openram import OPTS


class fake_sram(sram_config):
    """
    This is an SRAM class that doesn't actually create an instance.
    It will read neccessary members from HTML file from a previous run.
    """
    def __init__(self, name, word_size, num_words, write_size=None, num_banks=1,
                 words_per_row=None, num_spare_rows=0, num_spare_cols=0):
        sram_config.__init__(self, word_size, num_words, write_size,
                             num_banks, words_per_row, num_spare_rows,
                             num_spare_cols)
        self.name = name
        if write_size and self.write_size != self.word_size:
            self.num_wmasks = int(ceil(self.word_size / self.write_size))
        else:
            self.num_wmasks = 0

    def setup_multiport_constants(self):
        """
        Taken from ../base/design.py
        These are contants and lists that aid multiport design.
        Ports are always in the order RW, W, R.
        Port indices start from 0 and increment.
        A first RW port will have clk0, csb0, web0, addr0, data0
        A first W port (with no RW ports) will be: clk0, csb0, addr0, data0

        """
        total_ports = OPTS.num_rw_ports + OPTS.num_w_ports + OPTS.num_r_ports

        # These are the read/write port indices.
        self.readwrite_ports = []
        # These are the read/write and write-only port indices
        self.write_ports = []
        # These are the write-only port indices.
        self.writeonly_ports = []
        # These are the read/write and read-only port indices
        self.read_ports = []
        # These are the read-only port indices.
        self.readonly_ports = []
        # These are all the ports
        self.all_ports = list(range(total_ports))

        # The order is always fixed as RW, W, R
        port_number = 0
        for port in range(OPTS.num_rw_ports):
            self.readwrite_ports.append(port_number)
            self.write_ports.append(port_number)
            self.read_ports.append(port_number)
            port_number += 1
        for port in range(OPTS.num_w_ports):
            self.write_ports.append(port_number)
            self.writeonly_ports.append(port_number)
            port_number += 1
        for port in range(OPTS.num_r_ports):
            self.read_ports.append(port_number)
            self.readonly_ports.append(port_number)
            port_number += 1

    def get_cell_name(self, inst_name, row, col):
        """
        Name of the bitcell instance at (row, col).  Only used by the
        non-PEX path; sram_char.py always runs with top_process='memchar'
        and reads the storage nodes by their extracted label names.
        """
        if inst_name.find("x") != 0:
            inst_name = "x" + inst_name
        return (inst_name + OPTS.hier_seperator + "bitcell_array"
                + OPTS.hier_seperator + "bitcell_{0}_{1}".format(row, col))

    def get_bank_num(self, inst_name, row, col):
        """
        Bank index for the bitcell at (row, col).  fake_sram only describes a
        single flat bank (sram_char.py characterizes one macro), so this
        matches sram_1bank.get_bank_num and always returns 0.  The PEX storage
        net labels use this value: bitcell_Q_b{bank}_r{row}_c{col}.
        """
        return 0

    def generate_pins(self):
        self.pins = ['vdd', 'gnd']
        self.pins.extend(['clk{}'.format(port) for port in range(
            OPTS.num_rw_ports + OPTS.num_r_ports + OPTS.num_w_ports)])
        for port in range(OPTS.num_rw_ports):
            self.pins.extend(['din{0}[{1}]'.format(port, bit)
                              for bit in range(self.word_size + self.num_spare_cols)])
            self.pins.extend(['dout{0}[{1}]'.format(port, bit)
                              for bit in range(self.word_size + self.num_spare_cols)])
            self.pins.extend(['addr{0}[{1}]'.format(port, bit)
                              for bit in range(self.addr_size)])
            self.pins.extend(['spare_wen{0}[{1}]'.format(port, bit)
                              for bit in range(self.num_spare_cols)])
            if self.num_wmasks != 0:
                self.pins.extend(['wmask{0}[{1}]'.format(port, bit)
                                  for bit in range(self.num_wmasks)])

            self.pins.extend(['csb{}'.format(port), 'web{}'.format(port)])

        start_port = OPTS.num_rw_ports
        for port in range(start_port, start_port + OPTS.num_r_ports):
            self.pins.extend(['dout{0}[{1}]'.format(port, bit)
                              for bit in range(self.word_size + self.num_spare_cols)])
            self.pins.extend(['addr{0}[{1}]'.format(port, bit)
                              for bit in range(self.addr_size)])

            self.pins.extend(['csb{}'.format(port)])

        start_port += OPTS.num_r_ports
        for port in range(start_port, start_port + OPTS.num_w_ports):
            self.pins.extend(['din{0}[{1}]'.format(port, bit)
                              for bit in range(self.word_size + self.num_spare_cols)])
            self.pins.extend(['spare_wen{0}[{1}]'.format(port, bit)
                              for bit in range(self.num_spare_cols)])
            self.pins.extend(['addr{0}[{1}]'.format(port, bit)
                              for bit in range(self.addr_size)])
            if self.num_wmasks != 0:
                self.pins.extend(['wmask{0}[{1}]'.format(port, bit)
                                  for bit in range(self.num_wmasks)])

            self.pins.extend(['csb{}'.format(port), 'web{}'.format(port)])
