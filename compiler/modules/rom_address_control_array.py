# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
from openram.base import design
from openram.sram_factory import factory
from openram.base import vector
from openram.tech import layer, drc



class rom_address_control_array(design):
    """
    Takes the input address lines and creates the address and address bar lines for the decoder.
    Adds control logic for the precharge cycle so that all address lines are high before the read cycle
    """
    def __init__(self, cols, inv_height=None, inv_size=1, name="", route_layer="m1"):
        self.size=inv_size
        self.cols = cols
        self.route_layer = route_layer
        if name=="":
            name = "rom_inv_array_{0}".format(cols)
        if inv_height == None:

            self.inv_height = drc("minwidth_{}".format(route_layer)) * 14
        else:
            self.inv_height = inv_height


        if "li" in layer:
            self.inv_layer = "li"
        else:
            self.inv_layer = "m1"
            self.route_layer = "m2"
        super().__init__(name)
        self.create_netlist()
        self.create_layout()

    def create_netlist(self):
        self.create_modules()
        self.add_pins()
        self.create_instances()


    def create_layout(self):
        # Every buffer is an independent standard-cell-like block whose drawn
        # wells are inset from its right edge.  Tiling the buffers at their
        # declared width alone leaves the n-well/p-well strips of neighbouring
        # buffers closer than the well-to-well spacing rule (NW.2a on gf180mcu).
        # Add the largest same-well spacing rule as an explicit gap so the
        # tiled row is DRC clean on every technology.
        well_gap = 0.0
        for rule in ("nwell_to_nwell", "pwell_to_pwell"):
            if rule in drc:
                well_gap = max(well_gap, drc[rule])
        self.pitch = self.addr_control.width + well_gap

        # Provisional size for the horizontal supply/clk routing; the final
        # boundary is recomputed from the placed geometry in add_boundary().
        self.width = (self.cols - 1) * self.pitch + self.addr_control.width
        self.height = self.addr_control.height
        self.setup_layout_constants()
        self.place_instances()
        self.route_clk()
        self.route_sources()
        self.copy_pins()
        self.add_boundary()

    def add_boundary(self):
        """Size the row of address buffers from its real contents.

        The buffers are tiled with the pitch of their own boundary, so the
        array boundary must contain all of them (including the well taps and
        sub-cell well overhangs that can stick out past a buffer origin).  As
        in rom_address_control_buf, use the true drawn geometry rather than the
        declared boundaries of the sub-cells.
        """
        (minx, miny, maxx, maxy) = self.get_true_bbox()
        self.translate_all(vector(minx, miny))
        self.width = maxx - minx
        self.height = maxy - miny
        super().add_boundary()


    def create_modules(self):
        self.addr_control = factory.create(module_type="rom_address_control_buf", size=self.inv_height)


    def add_pins(self):
        for col in range(self.cols):
            self.add_pin("A{0}_in".format(col), "INPUT")
        for col in range(self.cols):
            self.add_pin("A{0}_out".format(col), "OUTPUT")
        for col in range(self.cols):
            self.add_pin("Abar{0}_out".format(col), "OUTPUT")
        self.add_pin("clk", "INPUT")
        self.add_pin("vdd", "POWER")
        self.add_pin("gnd", "GROUND")

    def create_instances(self):

        self.buf_insts = []

        for col in range(self.cols):

            name = "Xaddr_buf_{0}".format(col)
            addr_buf = self.add_inst(name=name, mod=self.addr_control)

            A_in = "A{0}_in".format(col)
            Aout = "A{0}_out".format(col)
            Abar_out = "Abar{0}_out".format(col)
            self.connect_inst([A_in, Aout, Abar_out, "clk", "vdd", "gnd"])

            self.buf_insts.append(addr_buf)

    def setup_layout_constants(self):
        self.route_width = drc["minwidth_{}".format(self.route_layer)]

    def place_instances(self):
        for col in range(self.cols):
            # Mirror "MY" maps a buffer of width W placed at offset o to
            # [o - W, o].  Put buffer col at [col*pitch, col*pitch + W] so a
            # well-spacing gap is left between neighbours.
            base = vector(self.addr_control.width + col * self.pitch, 0)

            self.buf_insts[col].place(offset=base, mirror="MY")

    def copy_pins(self):
        for i in range(self.cols):
            self.copy_layout_pin(self.buf_insts[i], "A_out", "A{0}_out".format(i))
            self.copy_layout_pin(self.buf_insts[i], "Abar_out", "Abar{0}_out".format(i))
            self.copy_layout_pin(self.buf_insts[i], "A_in", "A{0}_in".format(i))

    def route_clk(self):
        self.route_horizontal_pins("clk", insts=self.buf_insts, layer=self.route_layer)

    def route_sources(self):

        self.route_horizontal_pins("clk", insts=self.buf_insts, layer=self.route_layer)

        # Each buffer already ties its own supply rails together and exposes
        # one pin per net on m4.  Join the buffers on that same layer; using
        # the m2 rails here put a full-width m2 bar at the same y for both
        # nets (the old route_horizontal_pins("vdd"/"gnd") calls) and shorted
        # vdd to gnd.
        for pin_name in ("vdd", "gnd"):
            pins = [p for inst in self.buf_insts
                    for p in inst.get_pins(pin_name)]
            if not pins:
                continue
            layer = pins[0].layer
            self.connect_row_pins(layer, pins, name=pin_name)
            # connect_row_pins skips lone pins; still expose the net.
            if not self.get_pins(pin_name):
                p = pins[0]
                self.add_layout_pin(pin_name, p.layer, p.ll(),
                                    p.width(), p.height())
