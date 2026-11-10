# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#

import datetime
from math import ceil, log
from openram.base import vector
from openram.base import design
from openram.base import rom_verilog
from openram.base import lef
from openram import OPTS, print_time
from openram.sram_factory import factory
from openram.tech import spice
from openram.tech import drc, layer, parameter

try:
    from openram.tech import lef_rom_interconnect
except ImportError:
    # Some technologies do not define a ROM LEF interconnect list; fall back
    # to the generic metal range instead of failing the tech import.
    from openram.tech import layer_indices
    lef_rom_interconnect = ["m1", "m2", "m3", "m4", "m5"]
    lef_rom_interconnect = [name for name in lef_rom_interconnect
                            if name in layer_indices]


class rom_bank(design, rom_verilog, lef):

    """
    Rom data bank with row and column decoder + control logic

    word size is in bytes
    """

    def __init__(self, name, rom_config):
        super().__init__(name=name)
        lef.__init__(self, lef_rom_interconnect)
        self.rom_config = rom_config
        rom_config.set_local_config(self)

        self.word_size = self.word_bits
        self.num_outputs = self.rows
        self.num_inputs = ceil(log(self.rows, 2))
        self.col_bits = ceil(log(self.words_per_row, 2))
        self.row_bits = self.num_inputs

        self.tap_spacing = self.strap_spacing

        try:
            from openram.tech import power_grid
            self.supply_stack = power_grid
        except ImportError:
            # if no power_grid is specified by tech we use sensible defaults
            # Route a M3/M4 grid
            self.supply_stack = self.top_stack

        self.interconnect_layer = "m1"
        self.bitline_layer = "m1"
        self.wordline_layer = "m2"

        if "li" in layer:
            self.route_stack = self.m1_stack
        else:
            self.route_stack = getattr(self, "m2_stack", self.m1_stack[::-1])
        self.route_layer = self.route_stack[0]

        if OPTS.is_unit_test:
            self.create_netlist()
            self.create_layout()


    def create_netlist(self):
        start_time = datetime.datetime.now()
        self.add_modules()
        self.add_pins()
        self.create_instances()
        if not OPTS.is_unit_test:
            print_time("Submodules", datetime.datetime.now(), start_time)

    def create_layout(self):

        start_time = datetime.datetime.now()

        self.setup_layout_constants()
        self.place_instances()
        if not OPTS.is_unit_test:
            print_time("Placement", datetime.datetime.now(), start_time)

        self.add_boundary()

        start_time = datetime.datetime.now()
        self.route_layout()
        if not OPTS.is_unit_test:
            print_time("Routing", datetime.datetime.now(), start_time)

        start_time = datetime.datetime.now()
        if not OPTS.is_unit_test and OPTS.check_lvsdrc:
            # Match the SRAM top-level verification policy.
            self.DRC_LVS(final_verification=bool(OPTS.route_supplies), force_check=True)
            print_time("Verification", datetime.datetime.now(), start_time)

    def add_boundary(self):

        ll = self.find_lowest_coords()
        m1_offset = self.m1_width
        self.translate_all(vector(0, ll.y))
        ur = self.find_highest_coords()
        ur = vector(ur.x, ur.y)
        super().add_boundary(vector(0, 0), ur)
        self.width = ur.x
        self.height = ur.y

    def route_layout(self):
        self.route_decode_outputs()
        self.route_precharge()
        self.route_clock()
        self.route_array_outputs()
        self.place_top_level_pins()
        self.route_output_buffers()

        # FIXME: Somehow ROM layout behaves weird and doesn't add all the pin
        # shapes before routing supplies
        init_bbox = self.get_bbox()
        self.route_supplies(init_bbox)
        # Route the pins to the perimeter
        if OPTS.perimeter_pins:
            # We now route the escape routes far enough out so that they will
            # reach past the power ring or stripes on the sides
            self.route_escape_pins(init_bbox)


    def setup_layout_constants(self):
        self.route_layer_width = drc["minwidth_{}".format(self.route_stack[0])]
        self.route_layer_pitch = drc["{0}_to_{0}".format(self.route_stack[0])]

        self.interconnect_layer_width = drc["minwidth_{}".format(self.interconnect_layer)]
        self.interconnect_layer_pitch = drc["{0}_to_{0}".format(self.interconnect_layer)]

    def add_pins(self):

        for port in self.all_ports:
            self.add_pin("clk{}".format(port), "INPUT")
        for port in self.all_ports:
            self.add_pin("cs{}".format(port), "INPUT")

        for port in self.all_ports:
            for i in range(self.row_bits + self.col_bits):
                self.add_pin("addr{0}[{1}]".format(port, i), "INPUT")

        # out_pins = []
        for port in self.read_ports:
            for bit in range(self.word_size):
                self.add_pin("dout{0}[{1}]".format(port, bit), "OUTPUT")

        # Standard supply and ground names
        try:
            self.vdd_name = spice["power"]
        except KeyError:
            self.vdd_name = "vdd"
        try:
            self.gnd_name = spice["ground"]
        except KeyError:
            self.gnd_name = "gnd"

        self.add_pin(self.vdd_name, "POWER")
        self.add_pin(self.gnd_name, "GROUND")

        self.ext_supplies = [self.vdd_name, self.gnd_name]
        self.ext_supply = {"vdd" : self.vdd_name, "gnd" : self.gnd_name}

    def add_modules(self):

        # TODO: provide technology-specific calculation of these parameters
        # in sky130 the address control buffer is composed of 2 size 2 NAND gates,
        # with a beta of 3, each of these gates has gate capacitance of 2 min sized inverters, therefor a load of 4


        addr_control_buffer_effort = parameter['beta'] + 1
        # a single min sized nmos makes up 1/4 of the input capacitance of a min sized inverter
        bitcell_effort = 0.25

        # Takes into account inverter sizing
        wordline_effort = bitcell_effort * 0.5

        # a single min sized pmos plus a single min sized nmos have approximately half the gate capacitance of a min inverter
        # an additional 0.2 accounts for the long wire capacitance and add delay to gaurentee the read timing
        precharge_cell_effort = 0.5 + 0.2

        self.array = factory.create(module_type="rom_base_array",
                                    cols=self.cols,
                                    rows=self.rows,
                                    strap_spacing=self.strap_spacing,
                                    bitmap=self.data,
                                    bitline_layer=self.bitline_layer,
                                    wordline_layer=self.wordline_layer,
                                    pitch_match=True,
                                    tap_spacing=self.tap_spacing)


        self.decode_array = factory.create(module_name="rom_row_decode",
                                           module_type="rom_decoder",
                                           num_outputs=self.rows,
                                           strap_spacing=self.strap_spacing,
                                           route_layer=self.route_layer,
                                           fanout=(self.cols)*wordline_effort )


        self.column_mux = factory.create(module_type="rom_column_mux_array",
                                         columns=self.cols,
                                         word_size=self.word_size,
                                         tap_spacing=self.strap_spacing,
                                         bitline_layer=self.interconnect_layer,
                                         input_layer=self.bitline_layer)

        self.column_decode = factory.create(module_name="rom_column_decode",
                                            module_type="rom_decoder",
                                            num_outputs=self.words_per_row,
                                            strap_spacing=self.strap_spacing,
                                            route_layer=self.route_layer,
                                            fanout=2,
                                            invert_outputs=True )

        self.control_logic = factory.create(module_type="rom_control_logic",
                                            num_outputs=(self.cols + self.words_per_row * precharge_cell_effort) \
                                                         + (addr_control_buffer_effort * self.col_bits),
                                            clk_fanout=(self.row_bits * addr_control_buffer_effort) + (precharge_cell_effort * self.rows),
                                            height=self.column_decode.height )

        self.bitline_inv = factory.create(module_type="rom_wordline_driver_array",
                                            module_name="rom_bitline_inverter",
                                            rows=self.cols,
                                            fanout=4,
                                            invert_outputs=True,
                                            tap_spacing=0,
                                            flip_io=True)
        self.output_inv = factory.create(module_type="rom_wordline_driver_array",
                                            module_name="rom_output_buffer",
                                            rows=self.word_size,
                                            fanout=4,
                                            tap_spacing=1,
                                            invert_outputs=True)


    def create_instances(self):
        gnd = ["vssd1"]
        vdd = ["vccd1"]
        prechrg = ["precharge"]
        clk = ["clk_int"]

        bitlines = ["bl_{}".format(bl) for bl in range(self.cols)]
        wordlines = ["wl_{}".format(wl) for wl in range(self.rows)]

        addr_msb = ["addr0[{}]".format(addr + self.col_bits) for addr in range(self.row_bits)]
        addr_lsb = ["addr0[{}]".format(addr) for addr in range(self.col_bits)]

        select_lines = ["word_sel_{}".format(word) for word in range(self.words_per_row)]

        bitline_bar = ["bl_b_{}".format(bl) for bl in range(self.cols)]
        pre_buf_outputs = ["rom_out_prebuf_{}".format(bit) for bit in range(self.word_size)]
        outputs = ["dout0[{}]".format(bl) for bl in range(self.word_size)]


        array_pins = bitlines + wordlines + prechrg + vdd + gnd

        row_decode_pins = addr_msb + wordlines + clk + clk + vdd + gnd
        col_decode_pins = addr_lsb + select_lines + prechrg + prechrg + vdd + gnd

        col_mux_pins = bitline_bar + select_lines + pre_buf_outputs + gnd

        bitline_inv_pins = bitlines + bitline_bar + vdd + gnd

        output_buf_pins = pre_buf_outputs + outputs + vdd + gnd

        self.array_inst = self.add_inst(name="rom_bit_array", mod=self.array)
        self.connect_inst(array_pins)

        self.decode_inst = self.add_inst(name="rom_row_decoder", mod=self.decode_array)
        self.connect_inst(row_decode_pins)

        self.control_inst = self.add_inst(name="rom_control", mod=self.control_logic)
        self.connect_inst(["clk0", "cs0", "precharge", "clk_int"] + vdd + gnd)

        self.mux_inst = self.add_inst(name="rom_column_mux", mod=self.column_mux)
        self.connect_inst(col_mux_pins)

        self.col_decode_inst = self.add_inst(name="rom_column_decoder", mod=self.column_decode)
        self.connect_inst(col_decode_pins)

        self.bitline_inv_inst = self.add_inst(name="rom_bitline_inverter", mod=self.bitline_inv)
        self.connect_inst(bitline_inv_pins)

        self.output_inv_inst = self.add_inst(name="rom_output_inverter", mod=self.output_inv)
        self.connect_inst(output_buf_pins)

    def place_instances(self):
        self.place_row_decoder()
        self.place_data_array()
        self.place_bitline_inverter()
        self.place_col_mux()
        self.place_col_decoder()
        self.place_control_logic()
        self.place_output_buffer()


    def place_row_decoder(self):
        # Both decoders opened an internal routing channel to fix the address
        # pin shorts, which raised the top of the column decoder.  Keep the
        # historical escape corridor between the column decoder and the row
        # decoder bottom; without it the perimeter escape router can no
        # longer reach the row decoder's address pins.
        escape_gap = self.column_decode.route_channel_height()
        self.decode_offset = vector(0, self.control_inst.height + escape_gap)
        self.decode_inst.place(offset=self.decode_offset)

    def place_data_array(self):
        # We approximate the correct position for the array
        array_x = self.decode_inst.width + (2) * ( self.route_layer_width + self.route_layer_pitch )
        array_y = self.decode_array.buf_inst.height - self.array.precharge_inst.cy() - self.array.zero_cell.height * 0.5
        self.array_offset = vector(array_x ,array_y)
        self.array_inst.place(offset=self.array_offset)

        # now move array to correct alignment with decoder
        array_align = self.decode_inst.get_pin("wl_0").cy() - self.array_inst.get_pin("wl_0_0").cy()
        self.array_inst.place(offset=(self.array_offset + vector(0, array_align)))

    def place_bitline_inverter(self):
        self.bitline_inv_inst.place(offset=[0,0], rotate=90)
        inv_y_offset = self.array_inst.by() - self.bitline_inv_inst.width - 1.5 * self.m1_pitch

        inv_x_offset = self.array_inst.get_pin("bl_0_0").cx() - self.bitline_inv_inst.get_pin("out_0").cx()
        self.inv_offset = vector(inv_x_offset, inv_y_offset)
        self.bitline_inv_inst.place(offset=self.inv_offset, rotate=90)

    def place_control_logic(self):

        self.control_offset = vector(self.col_decode_inst.lx() - self.control_inst.width - 3 * self.m1_pitch, self.decode_inst.by() - self.control_logic.height - self.m1_pitch)
        self.control_inst.place(offset=self.control_offset)

    def place_col_decoder(self):
        col_decode_y = self.mux_inst.get_pin("sel_0").cy() - self.col_decode_inst.get_pin("wl_0").cy()
        self.col_decode_offset = vector(self.decode_inst.width - self.col_decode_inst.width, col_decode_y)
        self.col_decode_inst.place(offset=self.col_decode_offset)

    def place_col_mux(self):
        mux_y_offset = self.bitline_inv_inst.by() - self.mux_inst.height - 5 * self.route_layer_pitch

        mux_x_offset = self.bitline_inv_inst.get_pin("out_0").cx() - self.mux_inst.get_pin("bl_0").cx()
        self.mux_offset = vector(mux_x_offset, mux_y_offset)
        self.mux_inst.place(offset=self.mux_offset)

    def place_output_buffer(self):
        output_x = self.col_decode_inst.rx() + self.output_inv_inst.height
        output_y = self.mux_inst.by() - self.word_size * self.m1_pitch
        self.output_inv_offset = vector(output_x, output_y)
        self.output_inv_inst.place(offset=self.output_inv_offset, rotate=270)

    def route_decode_outputs(self):
        # for the row decoder
        route_pins = [self.array_inst.get_pin("wl_0_{}".format(wl)) for wl in range(self.rows)]
        decode_pins = [self.decode_inst.get_pin("wl_{}".format(wl)) for wl in range(self.rows)]
        route_pins.extend(decode_pins)
        self.connect_row_pins(self.interconnect_layer, route_pins, round=True)


        # then for the column decoder
        col_decode_pins = [self.col_decode_inst.get_pin("wl_{}".format(wl)) for wl in range(self.words_per_row)]
        sel_pins = [self.mux_inst.get_pin("sel_{}".format(wl)) for wl in range(self.words_per_row)]
        sel_pins.extend(col_decode_pins)
        self.connect_row_pins(self.wordline_layer, sel_pins, round=True)

    def route_array_inputs(self):

        for wl in range(self.rows):
            array_wl = self.array.wordline_names[0][wl]
            array_wl_pin = self.array_inst.get_pin(array_wl)

            wl_bus_wire = self.wl_bus[self.wl_interconnects[wl]]

            end = array_wl_pin.center()
            start = vector(wl_bus_wire.cx(), end.y)

            self.add_segment_center(self.interconnect_layer, start, end)


    def route_precharge(self):

        prechrg_control = self.control_inst.get_pin("prechrg")

        col_decode_prechrg = self.col_decode_inst.get_pin("precharge_r")
        col_decode_clk = self.col_decode_inst.get_pin("clk")
        array_prechrg = self.array_inst.get_pin("precharge")

        self.add_via_stack_center(from_layer=self.route_stack[0],
                                  to_layer=prechrg_control.layer,
                                  offset=prechrg_control.center())

        # Route precharge to col decoder
        start = prechrg_control.center()

        # The vertical precharge trunk runs in the channel between the
        # control logic and the column decoder.  The column decoder's instance
        # boundary (`lx`) sits 0.8um to the right of its drawn geometry, and
        # its left-edge supply landing pads (m2 + via2/via3 stacks) are drawn
        # right on that left edge.  Anchoring the trunk to `lx` therefore drops
        # it straight through those pads and shorts precharge to gnd.  Anchor
        # it just outside the control logic instead: the control logic draws no
        # m2 near its right boundary, so this leaves a clean channel of roughly
        # 0.4um to the decoder's landing pads.
        path_x = self.control_inst.rx() + 0.5 * self.route_layer_width

        mid1 = vector(path_x, prechrg_control.cy())
        mid2 = vector(path_x, col_decode_prechrg.cy())
        end = col_decode_prechrg.center()
        self.add_path(self.route_stack[0], [start, mid1, mid2, end])

        self.add_via_stack_center(from_layer=self.route_stack[0],
                                  to_layer=col_decode_prechrg.layer,
                                  offset=end)

        start = mid1
        mid1 = vector(path_x, start.y)
        mid2 = vector(mid1.x, col_decode_clk.cy())
        end = col_decode_clk.center()
        self.add_path(self.route_stack[0], [start, mid1, mid2, end])


        # Route precharge to main array
        mid = vector(col_decode_prechrg.cx(), array_prechrg.cy() )
        self.add_path(self.route_stack[0], [array_prechrg.center(), mid, col_decode_prechrg.center()])

        self.add_via_stack_center(from_layer=self.route_stack[0],
                                  to_layer=array_prechrg.layer,
                                  offset=array_prechrg.center())


    def route_clock(self):
        clk_out = self.control_inst.get_pin("clk_out")
        row_decode_clk = self.decode_inst.get_pin("clk")

        self.add_via_stack_center(from_layer=self.route_stack[2],
                                  to_layer=clk_out.layer,
                                  offset=clk_out.center())

        # Route clock to row decoder.  The column decoder draws a gnd supply tap
        # (m2/m3 landing pad plus via stack) on its left edge, so the clock must
        # stay at least one spacing rule to the left of that while it crosses
        # the column-decoder band.  Above the column decoder the row decoder has
        # its own m3 landing pads a little further right, so use two vertical
        # lanes and step between them in the gap between the two decoder blocks.
        col_lane_x = self.control_inst.rx() + 0.5 * self.route_layer_width - self.route_layer_pitch
        row_lane_x = self.control_inst.rx() + self.m1_pitch
        gap_y = 0.5 * (self.col_decode_inst.uy() + self.decode_inst.by())

        # Land on the row decoder's m2 clock rail vertically with a via.  A
        # horizontal m3 run at the rail's y would instead cross the decoder's
        # internal m3 buffer-output drops (A5/A4) and short the clock to those
        # address nets.
        addr_control_clk = vector(row_lane_x, row_decode_clk.cy())
        row_decode_prechrg = self.decode_inst.get_pin("precharge")

        # The row decoder's decode array taps its vdd rail up to m4 right next
        # to the precharge pin.  Approaching that pin along its own y would run
        # the m3 clock/precharge lane across the array's m2->m4 landing pad and
        # short clock to vdd.  Jog the last horizontal run above those pads and
        # drop onto the pin at its own x.
        jog_y = max(addr_control_clk.y, row_decode_prechrg.cy() + 0.75)
        clock_path = [clk_out.center(),
                      vector(col_lane_x, clk_out.cy()),
                      vector(col_lane_x, gap_y),
                      vector(row_lane_x, gap_y),
                      addr_control_clk,
                      vector(addr_control_clk.x, jog_y),
                      vector(row_decode_prechrg.cx(), jog_y),
                      row_decode_prechrg.center()]
        self.add_path(self.route_stack[2], clock_path)

        self.add_via_stack_center(from_layer=self.route_stack[2],
                                  to_layer=row_decode_clk.layer,
                                  offset=addr_control_clk)

        self.add_via_stack_center(from_layer=self.route_stack[2],
                                  to_layer=row_decode_prechrg.layer,
                                  offset=row_decode_prechrg.center())

        self.add_segment_center(row_decode_clk.layer, addr_control_clk, row_decode_clk.rc())

    def route_array_outputs(self):
        array_out_pins = [self.array_inst.get_pin("bl_0_{}".format(bl)) for bl in range(self.cols)]
        inv_in_pins = [self.bitline_inv_inst.get_pin("in_{}".format(bl)) for bl in range(self.cols)]
        inv_out_pins = [self.bitline_inv_inst.get_pin("out_{}".format(bl)) for bl in range(self.cols)]
        mux_pins = [self.mux_inst.get_pin("bl_{}".format(bl)) for bl in range(self.cols)]

        if "li" in layer:
            output_layer = "m1"
        else:
            output_layer = "m3"
        self.connect_col_pins(output_layer, array_out_pins + inv_in_pins, round=True, directions="nonpref")
        self.connect_col_pins(self.interconnect_layer, inv_out_pins + mux_pins, round=True, directions="nonpref")

    def route_output_buffers(self):
        mux = self.mux_inst
        buf = self.output_inv_inst
        route_nets = [ [mux.get_pin("bl_out_{}".format(bit)), buf.get_pin("in_{}".format(bit))] for bit in range(self.word_size)]

        channel_ll = vector( route_nets[0][0].cx(), route_nets[0][1].cy() + self.m1_pitch * 3)
        self.create_horizontal_channel_route(netlist=route_nets, offset=channel_ll, layer_stack=self.m1_stack)

    def place_top_level_pins(self):
        self.add_io_pin(self.control_inst, "CS", "cs0")
        self.add_io_pin(self.control_inst, "clk_in", "clk0")

        for i in range(self.word_size):
            self.add_io_pin(self.output_inv_inst, "out_{}".format(i), "dout0[{}]".format(i), directions="nonpref")

        for lsb in range(self.col_bits):
            name = "addr0[{}]".format(lsb)
            self.add_io_pin(self.col_decode_inst, "A{}".format(lsb), name)

        for msb in range(self.col_bits, self.row_bits + self.col_bits):
            name = "addr0[{}]".format(msb)
            pin_num = msb - self.col_bits
            self.add_io_pin(self.decode_inst, "A{}".format(pin_num), name)

    def get_supply_representative(self, inst, pin_name, center):
        """Return the supply pin that best faces the outside of the macro.

        ROM sub-blocks (in particular the decoder arrays) expose many layout
        pins for a supply net that their own route_supplies() already ties
        together on the lower layers.  Copying all of them to the top level
        makes the supply router thread new wires between redundant pins
        through the wordline/bitline metal; with a multi-layer power grid
        such as gf180mcu's m3/m4 stack it eventually finds no path.  A single
        pin per instance and net is enough to connect the block's internal
        supply network to the ring, so pick the one furthest from the macro
        centre to leave the block on its outer edge.
        """
        pins = inst.get_pins(pin_name)
        if not pins:
            return None

        # Prefer a pin already on the top of the supply stack (m4 on
        # gf180mcu).  Copying a lower-layer pin makes copy_power_pin punch a
        # blind via stack at that point, which can land on unrelated routing
        # in the middle layers; the blocks now expose their whole supply node
        # on the stitching layer, so take that pin when there is one.
        def rank(p):
            try:
                depth = self.supply_stack.index(p.layer)
            except ValueError:
                depth = -1
            return (depth,
                    (p.cx() - center.x) ** 2 + (p.cy() - center.y) ** 2)

        return max(pins, key=rank)

    def copy_supply_representative(self, inst, pin_name, center):
        """Copy one supply pin of an instance up to the power grid layer."""
        pin = self.get_supply_representative(inst, pin_name, center)
        if pin is None:
            return
        # Keep the internal names ("vdd"/"gnd") so that the supply router's
        # find_pins() call matches; the final perimeter pins are renamed to
        # the technology's external supply names afterwards.
        if pin.layer in self.pwr_grid_layers:
            self.add_layout_pin(pin.name,
                                pin.layer,
                                pin.ll(),
                                pin.width(),
                                pin.height())
        else:
            self.copy_power_pin(pin, new_name=pin.name)

    def route_supplies(self, bbox):

        center = vector((bbox[0].x + bbox[1].x) * 0.5,
                        (bbox[0].y + bbox[1].y) * 0.5)
        for pin_name in ["vdd", "gnd"]:
            for inst in self.insts:
                self.copy_supply_representative(inst, pin_name, center)

        if OPTS.route_supplies:

            from openram.router import supply_router as router
            rtr = router(layers=self.supply_stack,
                        design=self,
                        bbox=bbox,
                        pin_type=OPTS.supply_pin_type)
            rtr.route()

            if OPTS.supply_pin_type in ["left", "right", "top", "bottom", "ring"]:
                # Find the lowest leftest pin for vdd and gnd
                for pin_name in ["vdd", "gnd"]:
                    # Copy the pin shape(s) to rectangles
                    for pin in self.get_pins(pin_name):
                        self.add_rect(layer=pin.layer,
                                    offset=pin.ll(),
                                    width=pin.width(),
                                    height=pin.height())

                    # Remove the pin shape(s)
                    self.remove_layout_pin(pin_name)

                    # Get new pins
                    pins = rtr.get_new_pins(pin_name)
                    for pin in pins:
                        self.add_layout_pin(self.ext_supply[pin_name],
                                            pin.layer,
                                            pin.ll(),
                                            pin.width(),
                                            pin.height())

    def route_escape_pins(self, bbox):
        pins_to_route = []

        for bit in range(self.col_bits):
            pins_to_route.append("addr0[{0}]".format(bit))

        for bit in range(self.row_bits):
            pins_to_route.append("addr0[{0}]".format(bit + self.col_bits))

        for bit in range(self.word_size):
            pins_to_route.append("dout0[{0}]".format(bit))

        pins_to_route.append("clk0")
        pins_to_route.append("cs0")
        from openram.router import signal_escape_router as router
        rtr = router(layers=self.top_stack,
                     bbox=bbox,
                     design=self)
        rtr.route(pins_to_route)
