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
from openram import OPTS




class rom_address_control_buf(design):
    """
    Takes the input address lines and creates the address and address bar lines for the decoder.
    Adds control logic for the precharge cycle so that all address lines are high before the read cycle
    """
    def __init__(self, size, name="", route_layer="m1", add_wells=False):

        self.route_layer = route_layer
        self.add_wells = add_wells
        self.size = size
        if "li" in layer:
            self.inv_layer = "li"
            self.non_inverting_layer = "m2"
        else:
            self.inv_layer = "m1"
            self.route_layer = "m2"
            self.non_inverting_layer = "m3"
        super().__init__(name)
        self.create_netlist()
        self.create_layout()

    def create_netlist(self):
        self.create_modules()
        self.add_pins()
        self.create_instances()

    def create_layout(self):
        self.setup_layout_constants()
        self.place_instances()
        self.route_gates()
        self.route_sources()
        self.add_boundary()

    def add_boundary(self):
        """
        Size the cell from its actual contents.

        The rotated inverter and NAND gates are laid out across the width of
        the cell.  That width is set by the tallest logic cell, not by the ROM
        bitcell size: on gf180mcu the bitcell is much smaller than the decoder
        gates, so `rom_base_cell.height * 2` under-sized the cell.  Address
        control buffers are then placed at that (too small) pitch next to each
        other, which makes neighbouring wells, diffusion and metal overlap and
        triggers DRC errors.  Deriving the boundary from the real geometry
        keeps the tiling pitch correct on every technology.

        We use get_true_bbox() rather than find_lowest_coords()/find_highest_coords()
        because the well taps and the sub-cell well overhangs stick out past the
        declared boundaries of the logic cells.  Those overhangs are part of the
        drawn cell and must be contained inside the buffer boundary, otherwise
        the next tiled buffer overlaps them.
        """
        (minx, miny, maxx, maxy) = self.get_true_bbox()
        self.translate_all(vector(minx, miny))
        self.width = maxx - minx
        self.height = maxy - miny
        super().add_boundary()

    def create_modules(self):

        # For layout constants
        self.cell = factory.create(module_type="rom_base_cell")
        self.nand = factory.create(module_type="nand2_dec")

        self.inv = factory.create(module_type="pinv_dec", module_name="inv_array_mod", add_wells=False, size=self.size, height=self.nand.height)
    def add_pins(self):

        self.add_pin("A_in", "INPUT")
        self.add_pin("A_out", "INOUT")
        self.add_pin("Abar_out", "OUTPUT")
        self.add_pin("clk", "INPUT")
        self.add_pin("vdd", "POWER")
        self.add_pin("gnd", "GROUND")

    def create_instances(self):

        name = "XinvAbar"

        self.inv_inst = self.add_inst(name=name, mod=self.inv)
        inst_A = "A_in"
        inst_Z = "Abar_internal"
        self.connect_inst([inst_A, inst_Z, "vdd", "gnd"])

        name = "Xnand_addr"

        self.addr_nand = self.add_inst(name=name, mod=self.nand)
        inst_A = "clk"
        inst_B = "Abar_internal"
        inst_Z = "A_out"
        self.connect_inst([inst_A, inst_B, inst_Z, "vdd", "gnd"])

        name = "Xnand_addr_bar"

        self.addr_bar_nand = self.add_inst(name=name, mod=self.nand)
        inst_A = "clk"
        inst_B = "A_out"
        inst_Z = "Abar_out"
        self.connect_inst([inst_A, inst_B, inst_Z, "vdd", "gnd"])

    def setup_layout_constants(self):
        self.route_width = drc["minwidth_{}".format(self.route_layer)]
        self.interconnect_width = drc["minwidth_{}".format(self.inv_layer)]

    def place_instances(self):
        self.inv_inst.place(offset=vector(self.inv_inst.height, 0), rotate=90)

        # The gf180mcu pgate/well of the rotated gates is drawn past the
        # declared cell boundary (the same overhang that
        # layer_properties.port_address.decoder_driver_spacing compensates for
        # in the SRAM row decoder).  Stacking the gates at their declared pitch
        # leaves the inverter's n-well overlapping the first NAND's p-well in a
        # *different* sub-cell, which Magic reports as "Can't overlap those
        # layers".  Align the NAND's drawn bottom edge with the inverter's drawn
        # n-well top so the wells abut (n-well to n-well under the left half,
        # p-well to n-well under the right half, which pwell_to_nwell allows).
        inv_nwell_top = self.inv_inst.offset.y + self.inv.find_highest_layer_coords("nwell").x
        nand_bottom = self.nand.get_true_bbox()[0]
        nand_y = inv_nwell_top - nand_bottom

        # Fall back to the original declared pitch on technologies where the
        # well does not overhang (or if it would move the gates down).
        nand_y = max(nand_y, self.inv_inst.width + self.route_width)

        self.addr_nand.place(offset=vector(self.addr_nand.height, nand_y), rotate=90)
        self.addr_bar_nand.place(offset=vector(self.addr_bar_nand.height,
                                                nand_y + self.addr_nand.width),
                                 rotate=90)

    def route_gates(self):
        clk1_pin = self.addr_nand.get_pin("A")
        clk2_pin = self.addr_bar_nand.get_pin("A")

        Abar_out = self.addr_bar_nand.get_pin("Z")
        A_out = self.addr_nand.get_pin("Z")

        Abar_in = self.addr_nand.get_pin("B")
        Abar_int_out = self.inv_inst.get_pin("Z")

        Aint_in = self.addr_bar_nand.get_pin("B")
        A_in = self.inv_inst.get_pin("A")

        vdd_rail = self.addr_nand.get_pin("vdd")
        # Find the center of the pmos poly/gate
        poly_right = clk1_pin.cx() + self.poly_enclose_contact + 0.5 * self.contact_width

        ppoly_center = poly_right - 0.7 * self.poly_width
        poly_y = A_out.cy()

        # Placement of gate contacts for NAND cell are different in gf180 which requires tech-specific placement.
        if OPTS.tech_name == "gf180mcu":
            poly_y = vdd_rail.cy() + 0.5 * drc("minwidth_tx") * 3 + self.poly_extend_active
            ppoly_center = A_out.cx() + 0.5 * self.interconnect_width + 0.5 * self.poly_width


        contact_offset = vector(ppoly_center, clk2_pin.cy())

        # Route the two shared clk inputs together by connecting poly
        self.add_segment_center("poly", contact_offset, vector(ppoly_center, poly_y))


        clk_offset = vector(clk2_pin.cx(), self.addr_nand.uy())
        self.add_layout_pin_rect_center("clk", offset=clk_offset, layer=self.route_layer)

        self.add_via_stack_center(from_layer=self.inv_layer, to_layer=self.route_layer, offset=self.addr_bar_nand.get_pin("A").center())
        # The two NAND clk inputs are the A gates of the two stacked gates.
        # Their far ends are joined by a poly strap, but the lower gate's own
        # B-gate poly lies between the two clk gates, so that strap cannot
        # reach the lower A gate (it stops just above the B poly).  Carry the
        # clk net down to the first NAND's A pin on the route layer instead;
        # the two A pins share the same x, so this is one straight vertical.
        self.add_via_stack_center(from_layer=self.inv_layer, to_layer=self.route_layer,
                                  offset=clk1_pin.center())
        self.add_segment_center(self.route_layer,
                                vector(clk_offset.x, clk1_pin.cy()),
                                vector(clk_offset.x, clk2_pin.cy()))

        # Route first NAND output to second NAND input
        start = A_out.center()
        end = Aint_in.center()
        self.add_path(self.non_inverting_layer, [start, end])
        self.add_via_stack_center(Aint_in.center(), self.inv_layer, self.non_inverting_layer)
        self.add_via_stack_center(A_out.center(), self.inv_layer, self.non_inverting_layer)

        # Route first NAND to output pin
        self.add_segment_center(self.non_inverting_layer, end, vector(end.x, self.addr_bar_nand.uy()))
        self.add_layout_pin_rect_center("A_out", offset=vector(end.x, self.addr_bar_nand.uy() - 0.5 * self.m2_width), layer=self.non_inverting_layer)

        # Route second NAND to output pin
        self.add_via_stack_center(Abar_out.center(), self.inv_layer, self.non_inverting_layer)
        self.add_segment_center(self.non_inverting_layer, Abar_out.center(), vector(Abar_out.cx(), self.addr_bar_nand.uy()))
        self.add_layout_pin_rect_center("Abar_out", offset=vector(Abar_out.cx(), self.addr_bar_nand.uy() - 0.5 * self.m2_width), layer=self.non_inverting_layer)

        # Route inverter output to NAND
        end = vector(Abar_int_out.cx(), Abar_in.cy() + 0.5 * self.interconnect_width)
        self.add_segment_center(self.inv_layer, Abar_int_out.center(), end)
        self.copy_layout_pin(self.inv_inst, "A", "A_in")

    def route_sources(self):

        self.copy_layout_pin(self.addr_nand, "vdd")
        self.copy_layout_pin(self.addr_bar_nand, "vdd")
        self.copy_layout_pin(self.inv_inst, "vdd")

        self.copy_layout_pin(self.addr_bar_nand, "gnd")
        self.copy_layout_pin(self.addr_nand, "gnd")
        self.copy_layout_pin(self.inv_inst, "gnd")

        # The NAND gates expose their supplies on m1 columns near the cell
        # edges while the inverter's source rails are m2 bars at a different
        # height; without a strap vdd (and gnd) is two separate physical
        # nodes.  Tie each net together on m4, which is unused inside the
        # decoder cells: the vdd strap runs above the cell and the gnd strap
        # below it so the two never meet.
        vdd_pins = list(self.get_pins("vdd"))
        gnd_pins = list(self.get_pins("gnd"))
        # Replace the per-instance pins with a single pin per net on the
        # stitching layer.  The top-level supply router finds every labelled
        # supply shape, so leaving one pin per gate rail makes it route
        # through the decoder interior instead of around the block.
        self.remove_layout_pin("vdd")
        self.remove_layout_pin("gnd")
        if vdd_pins:
            vdd_bus_y = max(p.uy() for p in vdd_pins) + 0.3
            self.stitch_supply_pins("vdd", "m4", pins=vdd_pins, bus_y=vdd_bus_y,
                                    pin=True, wide_tap_edge="left")
        if gnd_pins:
            gnd_bus_y = min(p.by() for p in gnd_pins) - 1.0
            self.stitch_supply_pins("gnd", "m4", pins=gnd_pins, bus_y=gnd_bus_y,
                                    pin=True, wide_tap_edge="left")


        """ Add n/p well taps to the layout and connect to supplies """

        source_pin = self.inv_inst.get_pin("vdd")
        gnd_pin = self.inv_inst.get_pin("gnd")

        left_edge = self.inv_inst.get_pin("Z").cx() - 2 * self.contact_width - 2 * self.active_contact_to_gate - 4 * self.active_enclose_contact - self.poly_width - self.active_space

        contact_pos = vector(left_edge, source_pin.cy())
        self.add_via_center(layers=self.active_stack,
                            offset=contact_pos,
                            implant_type="n",
                            well_type="n")
        self.add_via_stack_center(offset=contact_pos,
                                  from_layer=self.active_stack[2],
                                  to_layer=self.route_layer)

        contact_pos = vector(left_edge, gnd_pin.cy())
        self.add_via_center(layers=self.active_stack,
                            offset=contact_pos,
                            implant_type="p",
                            well_type="p")
        self.add_via_stack_center(offset=contact_pos,
                                  from_layer=self.active_stack[2],
                                  to_layer=self.route_layer)
