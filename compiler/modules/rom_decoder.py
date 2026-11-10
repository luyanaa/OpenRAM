# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
from math import ceil, log
from openram.sram_factory import factory
from openram.base import vector, design
from openram import OPTS
from openram.tech import drc, layer
class rom_decoder(design):
    def __init__(self, num_outputs, fanout, strap_spacing, name="", route_layer="m1", output_layer="m1", invert_outputs=False):

        # word lines in the base array become the address lines/cols in the decoder
        # bit lines in the base array become the word lines/rows in the decoder
        # array gets rotated 90deg so rows/cols switch
        if "li" in layer:
            self.output_layer = "m1"
            self.inv_route_layer = "m1"
        else:
            self.output_layer = "m1"
            self.inv_route_layer = "m3"
        self.strap_spacing=strap_spacing
        self.num_outputs = num_outputs
        self.num_inputs = ceil(log(num_outputs, 2))
        self.create_decode_map()

        super().__init__(name)

        b = factory.create(module_type=OPTS.bitcell)
        self.cell_height = b.height
        self.route_layer = route_layer
        self.fanout=fanout
        self.invert_outputs=invert_outputs
        self.create_netlist()

        self.width = self.array_mod.height + self.wordline_buf.width
        # The rotated decode array is lifted to open a routing channel below
        # it (see place_array/route_channel_height); include it here so any
        # consumer of the pre-layout height is not surprised.  add_boundary()
        # recomputes both values from the actual geometry afterwards.
        self.height = (self.array_mod.width + self.control_array.height
                       + 2 * self.num_inputs * self.channel_pitch() + 1.5)
        self.create_layout()

    def create_netlist(self):
        self.add_modules()
        self.add_pins()
        self.create_instances()

    def create_layout(self):
        self.setup_layout_constants()
        self.place_array()
        self.place_input_buffer()
        self.lift_array()
        self.place_driver()
        self.route_outputs()

        self.connect_inputs()
        self.route_supplies()
        self.add_boundary()

    def add_boundary(self):
        ll = self.find_lowest_coords()
        m1_offset = self.m1_width
        self.translate_all(vector(0, ll.y))
        ur = self.find_highest_coords()

        ur = vector(ur.x, ur.y)
        super().add_boundary(ll, ur)
        self.width = ur.x
        self.height = ur.y

    def setup_layout_constants(self):
        self.inv_route_width = drc["minwidth_{}".format(self.inv_route_layer)]

    def create_decode_map(self):
        self.decode_map = []
        # create decoding map that will be the bitmap for the rom decoder
        # row/col order in the map will be switched in the placed decoder/
        for col in range(self.num_inputs):

            # odd cols are address
            # even cols are address bar
            col_array = []
            inv_col_array = []
            for row in range(self.num_outputs):

                addr_idx = -col - 1

                addr = format(row, 'b')
                if col >= len(addr) :
                    bin_digit = 0
                else:
                    bin_digit = int(addr[addr_idx])

                col_array.append(bin_digit)

                if bin_digit == 0 : inv_col_array.append(1)
                else : inv_col_array.append(0)

            self.decode_map.append(col_array)
            self.decode_map.append(inv_col_array)
        self.decode_map.reverse()

    def add_pins(self):
        for i in range(self.num_inputs):
            self.add_pin("A{0}".format(i), "INPUT")

        for j in range(self.num_outputs):
            self.add_pin("wl_{0}".format(j), "OUTPUT")
        self.add_pin("precharge", "INPUT")
        self.add_pin("clk", "INPUT")
        self.add_pin("vdd", "POWER")
        self.add_pin("gnd", "GROUND")

    def add_modules(self):

        self.control_array = factory.create(module_type="rom_address_control_array",
                                            cols=self.num_inputs)

        self.wordline_buf = factory.create(module_type="rom_wordline_driver_array", module_name="{}_wordline_buffer".format(self.name),
                                        rows=self.num_outputs,
                                        fanout=ceil(self.fanout),
                                        invert_outputs=self.invert_outputs,
                                        tap_spacing=self.strap_spacing)

        self.array_mod = factory.create(module_type="rom_base_array",
                                        module_name="{}_array".format(self.name),
                                        cols=self.num_outputs,
                                        rows=2 * self.num_inputs,
                                        bitmap=self.decode_map,
                                        strap_spacing = self.strap_spacing,
                                        bitline_layer=self.output_layer,
                                        tap_direction="col")

    def create_instances(self):

        self.create_array_inst()
        self.create_input_buffer()
        self.create_wordline_buffer()

    def create_input_buffer(self):
        name = "pre_control_array"
        self.buf_inst = self.add_inst(name=name, mod=self.control_array)

        control_pins = []

        for i in range(self.num_inputs):
            control_pins.append("A{0}".format(i))
        for i in range(self.num_inputs):
            control_pins.append("A_int_{0}".format(i))
        for i in range(self.num_inputs):
            control_pins.append("Ab_int_{0}".format(i))


        control_pins.append("clk")
        control_pins.append("vdd")
        control_pins.append("gnd")
        self.connect_inst(control_pins)

    def create_array_inst(self):
        self.array_inst = self.add_inst(name="decode_array_inst", mod=self.array_mod)

        array_pins = []

        for j in range(self.num_outputs):
            name = "wl_int{0}".format(j)
            array_pins.append(name)


        for i in reversed(range(self.num_inputs)):
            array_pins.append("Ab_int_{0}".format(i))
            array_pins.append("A_int_{0}".format(i))
        array_pins.append("precharge")
        array_pins.append("vdd")
        array_pins.append("gnd")
        self.connect_inst(array_pins)

    def create_wordline_buffer(self):
        self.wordline_buf_inst = self.add_inst("rom_wordline_driver", mod=self.wordline_buf)
        in_pins = ["wl_int{}".format(wl) for wl in range(self.num_outputs)]
        out_pins = ["wl_{}".format(wl) for wl in range(self.num_outputs)]
        pwr_pins = ["vdd", "gnd"]
        self.connect_inst(in_pins + out_pins + pwr_pins)

    def place_input_buffer(self):
        wl = self.array_mod.row_size - 1
        align = self.array_inst.get_pin(self.array_mod.wordline_names[0][wl]).cx() - self.buf_inst.get_pin("A0_out").cx()

        self.buf_inst.place(vector(align, 0))

        self.copy_layout_pin(self.buf_inst, "clk")

    def channel_pitch(self):
        """Vertical pitch between adjacent horizontal lane rows.

        Up to two nets share a lane row, one on m2 and one on m4, so
        neighbouring rows always carry different metals.  The row pitch is
        therefore set by the same-layer wire and via-pad spacing, which is
        the same value a single-layer channel used for its lane pitch.
        """
        pad = drc["minwidth_via2"] + 2 * drc["m2_extend_via2"]
        return pad + drc["m2_to_m2"]

    def _channel_assignment(self):
        """Assign every address net a lane row and a metal layer.

        The address buffers leave the control array on m3 at the same x
        columns the array wordlines use and the source/destination x ranges
        overlap while the bit ordering is anti-monotonic, so a single-layer
        L-route is topologically impossible.  Each net instead drops from its
        buffer output on m3 to a horizontal lane, runs across on m2 or m4,
        climbs back on m3 to a common via row, and then reaches its wordline
        pin with a short m2 stub (never placing a via on the pin - the array's
        gnd rail sits directly above it).

        A lane row can carry two nets when they are put on different metals,
        because then their horizontal overlaps are harmless.  Packing two per
        row roughly halves the channel height.  Returns ``(nets, placed,
        num_rows)`` where ``nets`` is the ordered list of ``(src, dst)`` pin
        pairs and ``placed[idx] = [row, colour]`` with ``colour`` 0 for m2
        and 1 for m4.
        """
        pitch = self.channel_pitch()
        half = 0.5 * pitch

        nets = []
        for i in range(self.num_inputs):
            wl = (self.num_inputs - i) * 2 - 1
            wl_bar = wl - 1
            nets.append((self.buf_inst.get_pin("A{}_out".format(i)),
                         self.array_inst.get_pin(self.array_mod.wordline_names[0][wl])))
            nets.append((self.buf_inst.get_pin("Abar{}_out".format(i)),
                         self.array_inst.get_pin(self.array_mod.wordline_names[0][wl_bar])))

        intervals = []
        for idx, (src, dst) in enumerate(nets):
            lo, hi = sorted((src.cx(), dst.cx()))
            intervals.append((lo, hi, idx))
        intervals.sort()

        # Two vertical m3 segments from different nets that are closer than
        # the spacing rule in x must not overlap in y.  The source drop of net
        # i spans y [src.cy(), lane_y(i)] and the destination rise of net j
        # spans [lane_y(j), via_y]; these overlap exactly when lane_y(j) <=
        # lane_y(i).  Requiring ``row(j) > row(i)`` therefore keeps every such
        # pair apart.  Same-net and same-kind (src/src, dst/dst) pairs are
        # already spaced by construction.
        m3_gap = drc["minwidth_m3"] + drc["m3_to_m3"] + 0.01
        conflicts = []
        for i, (src_i, _) in enumerate(nets):
            for j, (_, dst_j) in enumerate(nets):
                if i != j and abs(src_i.cx() - dst_j.cx()) < m3_gap:
                    conflicts.append((i, j))

        def row_fits(row, lo, hi):
            # A row may hold at most two nets at any x; they go on the two
            # metals.  Expanded by half the pitch, this also enforces the
            # same-layer wire/pad spacing between reused intervals.
            spans = [(a - half, b + half) for a, b, _ in row]
            spans.append((lo - half, hi + half))
            events = []
            for a, b in spans:
                events.append((a, 1))
                events.append((b, -1))
            events.sort()
            depth = 0
            for _, delta in events:
                depth += delta
                if depth > 2:
                    return False
            return True

        def ordering_ok(idx, r, row_of):
            for i, j in conflicts:
                if idx == i and j in row_of and not row_of[j] > r:
                    return False
                if idx == j and i in row_of and not r > row_of[i]:
                    return False
            return True

        rows = []
        placed = {}
        row_of = {}
        ok_all = True
        for lo, hi, idx in intervals:
            target = None
            for r, row in enumerate(rows):
                if row_fits(row, lo, hi) and ordering_ok(idx, r, row_of):
                    target = r
                    break
            if target is None:
                r = len(rows)
                if ordering_ok(idx, r, row_of):
                    rows.append([(lo, hi, idx)])
                    target = r
                else:
                    ok_all = False
                    break
            else:
                rows[target].append((lo, hi, idx))
            placed[idx] = [target, None]
            row_of[idx] = target

        if not ok_all or len(row_of) != len(nets):
            # Greedy failed (should not happen for our nets): fall back to one
            # row per net in natural index order.  Every conflict pair has
            # i < j, so row(j) = j > i = row(i) still holds.
            order = sorted(intervals, key=lambda t: t[2])
            rows = [[(lo, hi, idx)] for lo, hi, idx in order]
            placed = {idx: [r, None] for r, (lo, hi, idx) in enumerate(order)}

        # Two-colour each row.  Each row's overlap graph is an interval graph
        # of depth <= 2, so a greedy pass colours it with two metals.
        for row in rows:
            for lo, hi, idx in sorted(row):
                used = set()
                for lo2, hi2, idx2 in row:
                    colour = placed[idx2][1]
                    if idx2 == idx or colour is None:
                        continue
                    if lo2 - half < hi + half and lo - half < hi2 + half:
                        used.add(colour)
                placed[idx][1] = 0 if 0 not in used else 1

        return nets, placed, len(rows)

    def route_channel_height(self):
        """Extra y-space needed below the rotated decode array."""
        _, _, num_rows = self._channel_assignment()
        pitch = self.channel_pitch()
        lane_base = self.buf_inst.uy() + 0.42
        lane_top = lane_base + (num_rows - 1) * pitch
        via_y = lane_top + pitch
        # Keep the array's m2 wordlines clear of the via row.
        desired_bottom = via_y + 0.5
        return desired_bottom - self.array_inst.by()

    def place_array(self):
        self.array_offset = vector(self.array_mod.height,
                                   self.control_array.height + self.m1_width + self.poly_contact.width)
        self.array_inst.place(self.array_offset, rotate=90)

    def lift_array(self):
        # Called after the input buffer is placed so the channel assignment
        # can use the real source x coordinates (the buffer is placed at a
        # computed x offset).
        self.array_inst.place(self.array_offset + vector(0, self.route_channel_height()),
                              rotate=90)

    def place_driver(self):

        offset = vector(self.array_inst.height + self.m1_width, self.array_inst.by())
        self.wordline_buf_inst.place(offset)

        # calculate the offset between the decode array and the buffer inputs now that their zeros are aligned
        pin_offset = self.array_inst.get_pin("bl_0_0").cy() - self.wordline_buf_inst.get_pin("in_0").cy()
        self.wordline_buf_inst.place(offset + vector(0, pin_offset))

    def route_outputs(self):
        for j in range(self.num_outputs):

            self.copy_layout_pin(self.wordline_buf_inst, "out_{}".format(j), "wl_{}".format(j))

        array_pins = [self.array_inst.get_pin("bl_0_{}".format(bl)) for bl in range(self.num_outputs)]
        driver_pins = [self.wordline_buf_inst.get_pin("in_{}".format(bl)) for bl in range(self.num_outputs)]

        route_pins = array_pins + driver_pins
        self.connect_row_pins(self.inv_route_layer, route_pins, round=True)

    def connect_inputs(self):

        self.copy_layout_pin(self.array_inst, "precharge")
        self.copy_layout_pin(self.array_inst, "precharge_r")

        if self.inv_route_layer != "m1":
            self.connect_inputs_channel()
        else:
            self.connect_inputs_legacy()

        for i in range(self.num_inputs):
            self.copy_layout_pin(self.buf_inst, "A{}_in".format(i), "A{}".format(i))

    def connect_inputs_legacy(self):
        for i in range(self.num_inputs):
            wl = (self.num_inputs - i) * 2 - 1
            wl_bar = wl - 1
            addr_pin = self.array_inst.get_pin(self.array_mod.wordline_names[0][wl])
            addr_bar_pin = self.array_inst.get_pin(self.array_mod.wordline_names[0][wl_bar])

            addr_out_pin = self.buf_inst.get_pin("A{}_out".format(i))
            addr_bar_out_pin = self.buf_inst.get_pin("Abar{}_out".format(i))

            addr_middle = vector(addr_pin.cx(), addr_out_pin.cy())

            addr_bar_middle = vector(addr_bar_pin.cx(), addr_bar_out_pin.cy())

            self.add_path(self.inv_route_layer, [addr_out_pin.center(), addr_middle, addr_pin.center()])
            self.add_path(self.inv_route_layer, [addr_bar_out_pin.center(), addr_bar_middle, addr_bar_pin.center()])

            self.add_via_stack_center(offset=addr_pin.center(),
                                      from_layer=addr_pin.layer,
                                      to_layer=self.inv_route_layer)

            self.add_via_stack_center(offset=addr_bar_pin.center(),
                                      from_layer=addr_bar_pin.layer,
                                      to_layer=self.inv_route_layer)

            self.add_via_stack_center(offset=addr_out_pin.center(),
                                      from_layer=addr_out_pin.layer,
                                      to_layer=self.inv_route_layer)

            self.add_via_stack_center(offset=addr_bar_out_pin.center(),
                                      from_layer=addr_bar_out_pin.layer,
                                      to_layer=self.inv_route_layer)

    def connect_inputs_channel(self):
        """Route every address net from its buffer output to its wordline pin
        through the channel opened by lift_array().

        Per net: m3 down from the buffer output; via onto its lane metal
        (m2 or m4); horizontal run; via back to m3; m3 up to the common via
        row below the array; via2 back to m2; then a short m2 stub that lands
        on the wordline pin.  No via is placed on the wordline pin itself.
        """
        nets, placed, num_rows = self._channel_assignment()
        pitch = self.channel_pitch()
        lane_base = self.buf_inst.uy() + 0.42
        lane_top = lane_base + (num_rows - 1) * pitch
        via_y = lane_top + pitch

        m2_w = drc["minwidth_m2"]
        m3_w = drc["minwidth_m3"]

        for idx, (src, dst) in enumerate(nets):
            row, colour = placed[idx]
            lane_y = lane_base + row * pitch
            lane_layer = "m2" if colour == 0 else "m4"
            lane_w = drc["minwidth_{}".format(lane_layer)]

            # buffer output -> channel lane (m3 drop onto the lane metal)
            self.add_segment_center("m3", src.center(),
                                    vector(src.cx(), lane_y), width=m3_w)
            self.add_via_stack_center(offset=vector(src.cx(), lane_y),
                                      from_layer=lane_layer, to_layer="m3")

            # horizontal lane
            self.add_segment_center(lane_layer, vector(src.cx(), lane_y),
                                    vector(dst.cx(), lane_y), width=lane_w)

            # lane -> m3 vertical up to the common via row
            self.add_via_stack_center(offset=vector(dst.cx(), lane_y),
                                      from_layer=lane_layer, to_layer="m3")
            self.add_segment_center("m3", vector(dst.cx(), lane_y),
                                    vector(dst.cx(), via_y), width=m3_w)

            # via row -> back onto the wordline layer, short stub to the pin
            if dst.layer == "m2":
                self.add_via_stack_center(offset=vector(dst.cx(), via_y),
                                          from_layer="m2", to_layer="m3")
                self.add_segment_center("m2", vector(dst.cx(), via_y),
                                        vector(dst.cx(), dst.cy()), width=m2_w)
            else:
                self.add_segment_center(dst.layer, vector(dst.cx(), via_y),
                                        vector(dst.cx(), dst.cy()),
                                        width=drc["minwidth_{}".format(dst.layer)])

    def route_supplies(self):

        # The three sub-blocks are placed apart and each is already a single
        # physical supply node internally, but nothing ties those nodes
        # together.  Strap them on m4 (otherwise unused in the decoder).
        #
        # The control array has its own m4 straps near its top and bottom
        # edges and the wordline buffer has one at each end, so the decoder
        # trunks are placed clear of them: the vdd trunk runs just above the
        # bottom row of sub-block pins, and the gnd trunk runs in a lane left
        # of the control array and along the top of the wordline buffer.
        # Both trunks are also exposed as m4 pins so the bank can copy a
        # supply pin that is already on the top of the metal stack.  The
        # sub-block pins themselves are not copied: the top-level supply
        # router treats every labelled vdd/gnd shape as an endpoint, and the
        # decoder is already one node per net.
        width = drc["minwidth_m4"]

        ctrl = self.buf_inst
        arr = self.array_inst
        wlb = self.wordline_buf_inst

        def long_rail(name):
            # Each sub-block now exposes one wide pin per net on its stitching
            # layer; take the widest (full-row/full-height) one.
            cands = ctrl.get_pins(name)
            return max(cands, key=lambda p: p.width()) if cands else None

        ctrl_vdd = long_rail("vdd")
        ctrl_gnd = long_rail("gnd")
        arr_vdd = min(arr.get_pins("vdd"), key=lambda p: p.cy())
        arr_gnd = min(arr.get_pins("gnd"), key=lambda p: (p.cx(), p.cy()))
        wlb_vdd = min(wlb.get_pins("vdd"), key=lambda p: p.cy())
        wlb_gnd = max(wlb.get_pins("gnd"), key=lambda p: p.cy())

        gnd_bus_x = ctrl.lx() + 0.3
        # Left edge of the control array's supply rails: tapping here keeps the
        # decoder straps clear of the buffers' own m4 stubs further right.
        ctrl_tap_x = ctrl.lx() + 1.0

        # ----- vdd trunk (horizontal, on the control array's own m4 bus) -----
        #
        # The control array already draws a full-width m4 vdd bus along its top
        # edge (the row of buffer pins is joined there).  Route the array and
        # wordline-buffer vdd pins *down* onto that bus.  A separate bus above
        # the wordline buffer (the previous approach) crossed the rotated base
        # array's own m4 gnd bus, which appears as a vertical line through the
        # decoder, and shorted vdd to gnd.
        vdd_bus_y = ctrl_vdd.cy()
        vdd_taps = []
        # Tap in the middle of the bus: ctrl.lx() is left of where the bus
        # actually starts (the leftmost buffer's pin), so a tap at the boundary
        # would land on empty space.
        vdd_taps.append((ctrl_vdd.layer, ctrl_vdd.center()))
        vdd_taps.append((arr_vdd.layer, arr_vdd.center()))
        vdd_taps.append((wlb_vdd.layer, wlb_vdd.center()))

        # The channel opened below the rotated decode array is filled with m2
        # and m4 horizontal address lanes, so a vertical m4 supply tap that
        # crosses it would short to whichever lane it touches.  Route the
        # channel-crossing verticals on m3 instead (m3 only carries the widely
        # spaced point drop/rise verticals), and place them in a gap clear of
        # those verticals.  The bottom via lands back on the m4 vdd bus, which
        # sits below the channel.
        nets, _, _ = self._channel_assignment()
        chan_xs = []
        for src, dst in nets:
            chan_xs.append(src.cx())
            chan_xs.append(dst.cx())
        chan_xs.sort()
        m3_gap = 0.66

        tap_xs = [tap.x for _, tap in vdd_taps]
        bus_lo, bus_hi = min(tap_xs), max(tap_xs)

        def clear_intervals(lo, hi):
            ivals = []
            start = lo
            for cx in chan_xs:
                a, b = cx - m3_gap, cx + m3_gap
                if b < lo or a > hi:
                    continue
                if a > start:
                    ivals.append((start, min(a, hi)))
                start = max(start, b)
            if start < hi:
                ivals.append((start, hi))
            return [(a, b) for a, b in ivals if b - a > 0.05]

        def safe_tap_x(x):
            ivals = clear_intervals(bus_lo, bus_hi)
            for a, b in ivals:
                if a <= x <= b:
                    return x
            if not ivals:
                return x
            return min((0.5 * (a + b) for a, b in ivals),
                       key=lambda m: abs(m - x))

        relocated = []
        for layer, tap in vdd_taps:
            tx = safe_tap_x(tap.x)
            relocated.append((layer, tap, tx))
            if abs(tap.y - vdd_bus_y) > 1e-9:
                if abs(tx - tap.x) > 1e-9:
                    # Draw the horizontal jog on the pin's *own* layer and then
                    # drop the via stack to m3 at its far end.  Using m4 here
                    # regardless of the pin layer left m2 supply pins (the
                    # rotated base array's vdd) electrically isolated: the m4
                    # jog merely overprinted the m2 pin without a via.
                    self.add_segment_center(
                        layer, tap, vector(tx, tap.y),
                        width=drc["minwidth_{}".format(layer)])
                self.add_via_stack_center(from_layer=layer, to_layer="m3",
                                          offset=vector(tx, tap.y))
                self.add_segment_center("m3", vector(tx, tap.y),
                                        vector(tx, vdd_bus_y),
                                        width=drc["minwidth_m3"])
                self.add_via_stack_center(from_layer="m3", to_layer="m4",
                                          offset=vector(tx, vdd_bus_y))
            else:
                self.add_via_stack_center(from_layer=layer, to_layer="m4",
                                          offset=tap)
        xs = [tx for _, _, tx in relocated]
        self.add_segment_center("m4",
                                vector(min(xs) - 0.5 * width, vdd_bus_y),
                                vector(max(xs) + 0.5 * width, vdd_bus_y),
                                width=width)
        self.add_layout_pin_segment_center("vdd", "m4",
                                           start=vector(min(xs), vdd_bus_y),
                                           end=vector(max(xs), vdd_bus_y))

        # ----- gnd trunk (lane at the left of the control array) -----
        gnd_taps = []
        gnd_taps.append((ctrl_gnd.layer, vector(ctrl_tap_x, ctrl_gnd.cy())))
        gnd_taps.append((arr_gnd.layer, arr_gnd.center()))
        gnd_taps.append((wlb_gnd.layer, wlb_gnd.center()))
        for layer, tap in gnd_taps:
            self.add_via_stack_center(from_layer=layer, to_layer="m4",
                                      offset=tap)
            self.add_segment_center("m4", tap, vector(gnd_bus_x, tap.y),
                                    width=width)
        # The lane has to span the taps themselves.  Using the wordline
        # buffer's uy() here under-shoots because the instance boundary is
        # shorter than its drawn geometry, which left the top gnd pin isolated.
        # Extend both ends by half a wire width so the trunk squares off
        # against the horizontal tap segments instead of leaving a concave
        # notch (Magic flags the resulting M4.1 notch in the column decoder).
        ys = [tap.y for _, tap in gnd_taps]
        self.add_segment_center("m4",
                                vector(gnd_bus_x, min(ys) - 0.5 * width),
                                vector(gnd_bus_x, max(ys) + 0.5 * width),
                                width=width)
        self.add_layout_pin_segment_center("gnd", "m4",
                                           start=vector(gnd_bus_x, min(ys)),
                                           end=vector(gnd_bus_x, max(ys)))
