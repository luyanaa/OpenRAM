# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
from openram import debug
from openram.base import design
from openram.base import vector
from openram.sram_factory import factory
from openram import OPTS


class dff_array(design):
    """
    This is a simple row (or multiple rows) of flops.
    Unlike the data flops, these are never spaced out.
    """

    def __init__(self, rows, columns, name=""):
        self.rows = rows
        self.columns = columns

        if name=="":
            name = "dff_array_{0}x{1}".format(rows, columns)
        super().__init__(name)
        debug.info(1, "Creating {0} rows={1} cols={2}".format(self.name, self.rows, self.columns))
        self.add_comment("rows: {0} cols: {1}".format(rows, columns))

        self.create_netlist()
        if not OPTS.netlist_only:
            self.create_layout()

    def create_netlist(self):
        self.add_modules()
        self.add_pins()
        self.create_dff_array()

    def create_layout(self):
        self.width = self.columns * self.dff.width
        self.height = self.rows * self.dff.height

        self.place_dff_array()
        self.route_supplies()
        self.add_layout_pins()
        self.add_boundary()
        self.DRC_LVS()

    def add_modules(self):
        self.dff = factory.create(module_type="dff")

    def add_pins(self):
        for row in range(self.rows):
            for col in range(self.columns):
                self.add_pin(self.get_din_name(row, col), "INPUT")
        for row in range(self.rows):
            for col in range(self.columns):
                self.add_pin(self.get_dout_name(row, col), "OUTPUT")
        self.add_pin("clk", "INPUT")
        self.add_pin("vdd", "POWER")
        self.add_pin("gnd", "GROUND")

    def create_dff_array(self):
        self.dff_insts={}
        for row in range(self.rows):
            for col in range(self.columns):
                name = "dff_r{0}_c{1}".format(row, col)
                self.dff_insts[row, col] = self.add_inst(name=name,
                                                         mod=self.dff)
                instance_ports = [self.get_din_name(row, col),
                                  self.get_dout_name(row, col)]
                for port in self.dff.pins:
                    if port != 'D' and port != 'Q':
                        # Hard DFFs keep foundry pin names (CK/VDD/VSS)
                        # internally while the surrounding OpenRAM hierarchy
                        # uses clk/vdd/gnd.
                        instance_ports.append(
                            self.dff.get_original_pin_name(port))
                self.connect_inst(instance_ports)

    def place_dff_array(self):
        for row in range(self.rows):
            for col in range(self.columns):
                if (row % 2 == 0):
                    base = vector(col * self.dff.width, row * self.dff.height)
                    mirror = "R0"
                else:
                    base = vector(col * self.dff.width, (row + 1) * self.dff.height)
                    mirror = "MX"
                self.dff_insts[row, col].place(offset=base,
                                               mirror=mirror)

    def get_din_name(self, row, col):
        if self.columns == 1:
            din_name = "din_{0}".format(row)
        elif self.rows == 1:
            din_name = "din_{0}".format(col)
        else:
            din_name = "din_{0}_{1}".format(row, col)

        return din_name

    def get_dout_name(self, row, col):
        if self.columns == 1:
            dout_name = "dout_{0}".format(row)
        elif self.rows == 1:
            dout_name = "dout_{0}".format(col)
        else:
            dout_name = "dout_{0}_{1}".format(row, col)

        return dout_name

    def route_supplies(self):
        if self.rows > 1:
            # Vertical straps on ends if multiple rows
            left_dff_insts = [self.dff_insts[x, 0] for x in range(self.rows)]
            right_dff_insts = [self.dff_insts[x, self.columns-1] for x in range(self.rows)]
            self.route_vertical_pins("vdd", left_dff_insts, xside="lx", yside="cy")
            self.route_vertical_pins("gnd", right_dff_insts, xside="rx", yside="cy")
        else:

            # Add connections every 4 cells
            for col in range(0, self.columns, 4):
                vdd_pin=self.dff_insts[0, col].get_pin("vdd")
                self.add_power_pin("vdd", vdd_pin.lc(), start_layer=vdd_pin.layer)

            # Add connections every 4 cells
            for col in range(0, self.columns, 4):
                gnd_pin=self.dff_insts[0, col].get_pin("gnd")
                self.add_power_pin("gnd", gnd_pin.rc(), start_layer=gnd_pin.layer)

    def add_layout_pins(self):
        def add_m2_pin(text, pin):
            # Released ICsprout55 DFF macros expose M1 pins.  Promote them
            # through a local via so the array keeps OpenRAM's M2 data-pin
            # contract used by the surrounding SRAM routing.
            if pin.layer != "m2":
                self.add_via_stack_center(from_layer=pin.layer,
                                          to_layer="m2",
                                          offset=pin.center())
            self.add_layout_pin_rect_center(
                text=text,
                layer="m2",
                offset=pin.center(),
                width=max(pin.width(), self.m2_width),
                height=max(pin.height(), self.m2_width))

        for row in range(self.rows):
            for col in range(self.columns):
                din_pin = self.dff_insts[row, col].get_pin("D")
                add_m2_pin(self.get_din_name(row, col), din_pin)

                dout_pin = self.dff_insts[row, col].get_pin("Q")
                add_m2_pin(self.get_dout_name(row, col), dout_pin)

        # Keep the clock rail off the parent data-bus track.  The SRAM
        # top-level data bus is also M3; using the second M3 pitch here
        # places the rail directly on that bus after DFF placement.
        clk_ypos = 3 * self.m3_pitch + self.m3_width
        self.add_layout_pin_segment_center(text="clk",
                                           layer="m3",
                                           start=vector(0, clk_ypos),
                                           end=vector(self.width, clk_ypos))
        for col in range(self.columns):
            clk_pin = self.dff_insts[0, col].get_pin("clk")
            if clk_pin.layer != "m2":
                self.add_via_stack_center(from_layer=clk_pin.layer,
                                          to_layer="m2",
                                          offset=clk_pin.center())
            # Make a vertical strip for each column.
            self.add_rect(layer="m2",
                          offset=vector(clk_pin.cx() - 0.5 * self.m2_width, 0),
                          width=self.m2_width,
                          height=self.height)
            # Drop a via to the M3 pin.
            self.add_via_stack_center(from_layer="m2",
                                      to_layer="m3",
                                      offset=vector(clk_pin.cx(), clk_ypos))
