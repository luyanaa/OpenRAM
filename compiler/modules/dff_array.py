# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
from openram import debug
from openram.base import design
from openram.tech import layer, layer_indices
from openram.base import vector
from openram.sram_factory import factory
from openram import OPTS


class dff_array(design):
    """
    This is a simple row (or multiple rows) of flops.
    Unlike the data flops, these are never spaced out.
    """

    def __init__(self, rows, columns, name="", dout_pin_extend=0.0):
        self.rows = rows
        self.columns = columns
        # A technology whose hard flop has other metal (e.g. a neighbour's
        # via stack) too close above the Q pin can ask for the exported dout
        # pin to grow upward so a channel route can place its via stack clear
        # of that metal.  The pin's lower-left corner is unchanged, so the
        # route's pin.uc() moves up by exactly this amount.
        self.dout_pin_extend = dout_pin_extend

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
                self.dff_insts[row, col]=self.add_inst(name=name,
                                                       mod=self.dff)
                instance_ports = [self.get_din_name(row, col),
                                  self.get_dout_name(row, col)]
                for port in self.dff.pins:
                    if port == 'D' or port == 'Q':
                        continue
                    # A technology may expose the flop reset as a port (the
                    # TR-1um hard cell does); arrays tie it to ground.
                    instance_ports.append("gnd" if port == "RST" else port)
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
            # Separate the two straps when the technology's supply pins are
            # too close to put one at each edge of the pin.
            self.route_vertical_pins("gnd", right_dff_insts, xside="rx", yside="cy",
                                     xshift=getattr(self.dff, "gnd_tap_xshift", 0.0))
        else:

            # Add connections every 4 cells
            for col in range(0, self.columns, 4):
                vdd_pin=self.dff_insts[0, col].get_pin("vdd")
                # col-0 via centred on the array edge puts m2 0.055 um from an abutted array's channel trunk (met2.2 0.14 um)
                # deliberately unconditional: harmless where the rule is looser, and keeps one geometry across technologies
                inset = vector(0.5 * self.m2_pitch, 0) if col == 0 else vector(0, 0)
                self.add_power_pin("vdd", vdd_pin.lc() + inset, start_layer=vdd_pin.layer)

            # Add connections every 4 cells
            for col in range(0, self.columns, 4):
                gnd_pin=self.dff_insts[0, col].get_pin("gnd")
                self.add_power_pin("gnd", gnd_pin.rc(), start_layer=gnd_pin.layer)

    def add_layout_pins(self):
        for row in range(self.rows):
            for col in range(self.columns):
                din_pin = self.dff_insts[row, col].get_pin("D")
                debug.check(din_pin.layer == "m2", "DFF D pin not on metal2")
                self.add_layout_pin(text=self.get_din_name(row, col),
                                    layer=din_pin.layer,
                                    offset=din_pin.ll(),
                                    width=din_pin.width(),
                                    height=din_pin.height())

                dout_pin = self.dff_insts[row, col].get_pin("Q")
                debug.check(dout_pin.layer == "m2", "DFF Q pin not on metal2")
                dout_height = dout_pin.height() + self.dout_pin_extend
                self.add_layout_pin(text=self.get_dout_name(row, col),
                                    layer=dout_pin.layer,
                                    offset=dout_pin.ll(),
                                    width=dout_pin.width(),
                                    height=dout_height)

        # Create vertical spines to a single horizontal rail
        clk_pin = self.dff_insts[0, 0].get_pin("clk")
        debug.check(clk_pin.layer == "m2", "DFF clk pin not on metal2")
        # A DFF module may ask for the clock rail at a specific height.
        clk_ypos = getattr(self.dff, "clk_rail_y", None)
        # A 2-metal technology has no M3: the rail shares M2 with the pins
        # and the per-column strips merge into it directly.
        has_m3 = "m3" in layer and bool(layer["m3"])
        if clk_ypos is None:
            clk_ypos = 2 * self.m3_pitch + self.m3_width
        rail_layer = "m3" if has_m3 else "m2"
        self.add_layout_pin_segment_center(text="clk",
                                           layer=rail_layer,
                                           start=vector(0, clk_ypos),
                                           end=vector(self.width, clk_ypos))
        for col in range(self.columns):
            clk_pin = self.dff_insts[0, col].get_pin("clk")
            # Make a vertical strip for each column
            self.add_rect(layer="m2",
                          offset=clk_pin.ll().scale(1, 0),
                          width=self.m2_width,
                          height=self.height)
            if has_m3:
                # Drop a via to the M3 pin
                self.add_via_stack_center(from_layer=clk_pin.layer,
                                          to_layer=self.top_layer,
                                          offset=vector(clk_pin.cx(), clk_ypos))
