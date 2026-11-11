# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""gf180mcu latch-type sense amplifier built from parameterized cells.

The gf180mcu technology has no extracted sense-amp hard macro: the only
sources in the PDK are the large single-ended column blocks of the GF
018SRAM IP (``sa_3v64x8m81`` is a ~27 x 42 um mux8 column slice), which do
not match OpenRAM's pitch or its 6-pin differential interface.

Instead this module composes the amplifier from the technology's own
parameterized devices:

    inv_bl  : bl  -> br   (its gnd rail is the switched source node)
    inv_br  : br  -> bl   (its gnd rail is the switched source node)
    tail    : en NMOS between the switched source node and gnd
    inv_out : br  -> dout

With en low the source node floats, so the cross-coupled pair is inert and
only gate capacitance loads the bitlines.  With en high the pair becomes a
regenerative latch and resolves the bitline differential.

Layer assignment
~~~~~~~~~~~~~~~~
The bitcell (``cell1rw``) exposes BL/BR as m2 stripes at x 0.45-1.05 and
1.95-2.55.  The supply/source-node routing cannot live on m2 at the same
time: a m1->m3 via stack needs a 0.56 um wide m2 landing pad, and the free
m2 columns between the two bitline stripes are too narrow for one.  So the
three *signal* trunks are m3 and the supplies are m2:

  * bl / br / nsense (switched source) are **full-height m3 trunks** at
    x = 0.85 / 2.20 / 1.53;
  * the external bitline pins are **m2 pads at the top edge**, centred on
    the cell1rw bitline centres (0.75 / 2.25 um), joined to the m3 trunks
    by via2 stacks;
  * gnd is an **m2 trunk** on the left (x = 0.90) and vdd is a **stepped m2
    trunk**: straight columns are impossible because at the dout row the only
    free m2 corridor is between the dout pad and the br via pad, and the gnd
    trunk would sit in it.  vdd therefore runs down the right edge, jogs left
    above the dout row, drops through that corridor, and jogs left again
    below the nsense pad;
  * every device connection is an m1 stub and every layer change uses a via
    stack, so the cell is electrically closed inside its boundary.

The m3 trunks reach the bottom edge of the cell so that the write-driver
array, which abuts this cell from below, can join them with its own m3
trunks.  Because SA and WD arrays use the same 3.34 um pitch and mirroring
rule as the bitcell array, all columns line up without jogs.
"""

from openram import debug
from openram import OPTS
from openram.base import design
from openram.base import vector
from openram.sram_factory import factory
from openram.tech import drc
from openram.modules.sense_amp import sense_amp as sense_amp_base


class gf180_sense_amp(sense_amp_base):
    """Latch sense amplifier made of parameterized inverters."""

    # Vertical distance between the stacked inverters.  A single pinv at the
    # minimum height that still fits a single finger is 4.84 um; 0.56 um of
    # routing channel keeps the m1 supply rails apart.
    STAGE_PITCH = 5.40
    # The array pitch must stay at the bitcell width so that the bank's
    # bitline paths are vertical.
    WIDTH = 3.34
    # Content height: three stacked inverters plus the enable NMOS and the
    # enable contact above it.
    HEIGHT = 18.0

    # Three full-height m3 trunks: bl, br and the switched source node
    # (nsense).  NS_TX is confined by the 0.28 um m3 spacing rule on both
    # sides: bl spans 0.71-0.99 and br 2.06-2.34.
    BL_TX = 0.85
    BR_TX = 2.20
    NS_TX = 1.53
    # m2 supply trunks.  GND_TX is far enough left that the dout landing pad
    # of the neighbouring cell (its m2 pad reaches 0.04 in this cell's frame)
    # clears it, while still on the output inverter's full-width gnd rail.
    GND_TX = 0.90
    # The gnd trunk runs up beside the output stage and then steps left over
    # the tail device to reach its source.
    GND_JOG_Y = 16.60
    TAIL_S_TX = 0.24

    # The vdd trunk cannot be a single column: the m2 landing pads of the bl,
    # br and nsense via stacks together with the dout pad of the *next* cell
    # leave only one 0.28 um corridor free at the dout row (between this
    # cell's dout pad at 0.42 and the br pad at 2.01).  So it is stepped:
    #   - VDD_TX down the right edge from the output rail to VDD_TOP_Y,
    #   - left along VDD_TOP_Y to VDD_MID_TX (the corridor),
    #   - down the corridor to VDD_BOT_Y,
    #   - left along VDD_BOT_Y to VDD_LOW_TX, then onto the bl inverter rail.
    VDD_TX = 2.90
    VDD_MID_TX = 1.53
    VDD_LOW_TX = 0.60
    VDD_TOP_Y = 13.90
    VDD_BOT_Y = 6.20

    # Top-edge m2 bitline pads.  bl 0.45-1.05 and br 1.95-2.55 are the
    # cell1rw bitline extents.  PIN_Y0 is dictated by the m2 landing pad of
    # the tail drain via stack, which reaches y = 17.56.
    PIN_Y0 = 17.90
    PIN_Y1 = 18.18
    TOP_VIA_Y = 18.04

    # Cell bottom: the m3 trunks are the lowest shapes in the cell, so the
    # standard offset_all_coordinates() shift puts their bottom end exactly
    # on the cell boundary and the abutting write driver can reach them.
    BOT_Y = -0.20

    # The enable bus of the array runs on m1 straight through every cell at
    # the y of the amp's en pin.  The tail device occupies m1 up to y=17.685,
    # so the en pin sits above it and the gate poly is extended upward.
    GATE_W = 0.28
    EN_Y = 18.45

    def __init__(self, name="sense_amp"):
        # Deliberately bypass sense_amp.__init__: it loads a hard macro from
        # the technology library, which gf180mcu does not have.
        design.__init__(self, name)
        self.add_comment("gf180mcu parameterized latch sense amplifier")

        self.add_pin_list(["bl", "br", "dout", "en", "vdd", "gnd"],
                          ["INPUT", "INPUT", "OUTPUT", "INPUT", "POWER", "GROUND"])
        # The hard-macro base class fills this from cell_properties; since this
        # cell is composed from parameterized devices there is no property
        # entry, but the characterizer still queries it (get_enable_name).
        self.add_pin_names({x: x for x in ("bl", "br", "dout", "en", "vdd", "gnd")})
        self._route_rects = []

        self.create_netlist()
        if not OPTS.netlist_only:
            self.create_layout()

    ############################################################
    # Netlist
    ############################################################
    def create_netlist(self):
        self.add_modules()
        self.create_insts()

    def add_modules(self):
        self.inv = factory.create(module_type="pinv", size=1, height=4.84)
        # The cross-coupled pair uses its gnd rail as the switched source
        # node (nsense).  A stock pinv taps that rail to the p-substrate,
        # which Magic extracts as the global gnd net, shorting nsense (and
        # the whole amplifier) to gnd.  Use a variant with an nwell tap only.
        self.inv_ns = factory.create(module_type="gf180_pinv_nwell",
                                     size=1, height=4.84)
        debug.check(abs(self.inv.width - self.inv_ns.width) < 1e-6,
                    "gf180_pinv_nwell width {0} differs from pinv width {1}".format(
                        self.inv_ns.width, self.inv.width))
        self.tail = factory.create(module_type="ptx",
                                   tx_type="nmos",
                                   width=2 * drc("minwidth_tx"))

    def create_insts(self):
        self.inv_bl_inst = self.add_inst(name="sa_inv_bl", mod=self.inv_ns)
        self.connect_inst(["bl", "br", "vdd", "nsense", "gnd"])

        self.inv_br_inst = self.add_inst(name="sa_inv_br", mod=self.inv_ns)
        self.connect_inst(["br", "bl", "vdd", "nsense", "gnd"])

        self.inv_out_inst = self.add_inst(name="sa_inv_out", mod=self.inv)
        self.connect_inst(["br", "dout", "vdd", "gnd"])

        self.tail_inst = self.add_inst(name="sa_tail", mod=self.tail)
        self.connect_inst(["nsense", "en", "gnd", "gnd"])

    ############################################################
    # Layout
    ############################################################
    def create_layout(self):
        self.place_insts()
        self.route_nets()
        self.check_routes()
        ll = self.find_lowest_coords()
        ur = self.find_highest_coords()
        self.width = max(self.WIDTH, ur.x - ll.x)
        self.height = max(self.HEIGHT, ur.y - ll.y)
        self.add_layout_pins(ll)
        self.add_boundary(ll)
        # Anchor the cell's x origin at 0 rather than at the absolute lowest
        # object.  The dout via stack reaches x < 0.1 (its instance origin
        # lands at -0.05), and letting that define the origin would shift the
        # whole cell 0.05 um to the right: the array places these cells on the
        # bitcell pitch, so the m3 trunks would no longer line up with the
        # write-driver bridge or the bitcell bitlines, and the union of the two
        # 0.28 um m3 straps would neck down to 0.23 um at their seam (M3.1).
        self.translate_all(vector(0, self.find_lowest_coords().y))

    def place_insts(self):
        # Stage 0: the bl->br inverter, plain orientation.
        self.inv_bl_inst.place(vector(0, 0))
        # Stage 1: br->bl inverter mirrored so that its bl output sits on the
        # left and its br input on the right.
        self.inv_br_inst.place(vector(self.inv.width, self.STAGE_PITCH), mirror="MY")
        # Stage 2: output buffer, same mirrored orientation.
        self.inv_out_inst.place(vector(self.inv.width, 2 * self.STAGE_PITCH), mirror="MY")
        # Enable device on top of the stack.  The extra 0.6 um clears the
        # output inverter's nwell, which ends at y=16.305.
        self.tail_inst.place(vector(0, 3 * self.STAGE_PITCH + 0.6))

    ############################################################
    # Internal routing
    ############################################################
    def route_nets(self):
        p = self.pin_centers()

        # --- bl: m3 trunk, connected to the top pad and both inverters ----
        # The trunk overlaps both via stacks so the m3 stubs are short.
        self.via("bl", (self.BL_TX, p["inv_bl_A"][1]), "m1", "m3")
        self.via("bl", (self.BL_TX, p["inv_br_Z"][1]), "m1", "m3")
        self.via("bl", (self.BL_TX, self.TOP_VIA_Y), "m2", "m3")
        # inv_bl.A is left of the trunk; inv_br.Z is right of it.
        self.route("bl", "m1", p["inv_bl_A"], (self.BL_TX, p["inv_bl_A"][1]))
        self.route("bl", "m1", p["inv_br_Z"], (self.BL_TX, p["inv_br_Z"][1]))
        self.route("bl", "m3", (self.BL_TX, self.BOT_Y),
                   (self.BL_TX, self.TOP_VIA_Y))

        # --- br: m3 trunk, connected to the top pad and three pins -------
        self.via("br", (self.BR_TX, p["inv_bl_Z"][1]), "m1", "m3")
        self.via("br", (self.BR_TX, p["inv_br_A"][1]), "m1", "m3")
        self.via("br", (self.BR_TX, p["inv_out_A"][1]), "m1", "m3")
        self.via("br", (self.BR_TX, self.TOP_VIA_Y), "m2", "m3")
        # All three br pins lie left of the trunk.
        for name in ("inv_bl_Z", "inv_br_A", "inv_out_A"):
            self.route("br", "m1", p[name], (self.BR_TX, p[name][1]))
        self.route("br", "m3", (self.BR_TX, self.BOT_Y),
                   (self.BR_TX, self.TOP_VIA_Y))

        # --- nsense: m3 trunk between the two bitline trunks ---------------
        for y in (p["inv_bl_nsense"][1], p["inv_br_nsense"][1]):
            self.via("nsense", (self.NS_TX, y), "m1", "m3")
        # The tail drain is left of the trunk.  Its via pad must not touch the
        # bl trunk either, so land the via on the trunk itself and reach it
        # with an m1 stub.
        self.via("nsense", (self.NS_TX, p["tail_D"][1]), "m1", "m3")
        self.route("nsense", "m1", p["tail_D"],
                   (self.NS_TX, p["tail_D"][1]))
        self.route("nsense", "m3", (self.NS_TX, p["inv_bl_nsense"][1]),
                   (self.NS_TX, p["tail_D"][1]))

        # --- gnd: m2 trunk on the left, from the output rail to tail.S ---
        self.via("gnd", (self.GND_TX, p["inv_out_gnd"][1]), "m1", "m2")
        self.via("gnd", (self.TAIL_S_TX, p["tail_S"][1]), "m1", "m2",
                 dirs=("V", "H"))
        self.route("gnd", "m2", (self.GND_TX, p["inv_out_gnd"][1]),
                   (self.GND_TX, self.GND_JOG_Y))
        self.route("gnd", "m2", (self.GND_TX, self.GND_JOG_Y),
                   (self.TAIL_S_TX, self.GND_JOG_Y))
        self.route("gnd", "m2", (self.TAIL_S_TX, self.GND_JOG_Y),
                   (self.TAIL_S_TX, p["tail_S"][1]))

        # --- vdd: stepped m2 trunk, all three rails -----------------------
        # A straight column would either cross the bl/br/nsense landing pads
        # or, at x=2.90, touch the next cell's dout pad (this shorted every
        # dout to vdd in the array).  Route through the one free corridor.
        self.via("vdd", (self.VDD_TX, p["inv_out_vdd"][1]), "m1", "m2")
        self.via("vdd", (self.VDD_MID_TX, p["inv_br_vdd"][1]), "m1", "m2")
        self.via("vdd", (self.VDD_LOW_TX, p["inv_bl_vdd"][1]), "m1", "m2")

        self.route("vdd", "m2", (self.VDD_TX, p["inv_out_vdd"][1]),
                   (self.VDD_TX, self.VDD_TOP_Y))
        self.route("vdd", "m2", (self.VDD_TX, self.VDD_TOP_Y),
                   (self.VDD_MID_TX, self.VDD_TOP_Y))
        self.route("vdd", "m2", (self.VDD_MID_TX, self.VDD_TOP_Y),
                   (self.VDD_MID_TX, self.VDD_BOT_Y))
        self.route("vdd", "m2", (self.VDD_MID_TX, self.VDD_BOT_Y),
                   (self.VDD_LOW_TX, self.VDD_BOT_Y))
        self.route("vdd", "m2", (self.VDD_LOW_TX, self.VDD_BOT_Y),
                   (self.VDD_LOW_TX, p["inv_bl_vdd"][1]))

        # --- en: poly riser above the tail into the array's en bus --------
        gate_x = self.tail_inst.offset.x + 0.5 * self.tail.width - 0.5 * self.GATE_W
        en_via = self.via("en", (gate_x + 0.5 * self.GATE_W, self.EN_Y), "poly", "m1")
        # Match the riser width to the via's poly landing pad.  Drawing it at
        # the interconnect minimum leaves 0.04 um slivers of the pad hanging
        # over the riser just above the tail gate stripe; under thick-oxide
        # rules that 0.03 um gap is a poly notch (PL.3a).
        riser_w = max(self.GATE_W, en_via.mod.first_layer_width)
        self.add_rect("poly",
                      offset=(gate_x + 0.5 * self.GATE_W - 0.5 * riser_w, 18.10),
                      width=riser_w, height=0.50)

        # --- supply / source rail abutment --------------------------------
        # The parameterized inverter is 2.90 um wide inside a 3.00 um column
        # pitch, so its m1 vdd/gnd rails stop 0.10 um short of the column
        # edge and leave an M1.2a gap to the identical rail of the next
        # column.  All columns are placed in the same orientation, so the
        # neighbouring rail is the same net: extend each rail to the edge.
        # The switched-source rails (nsense) are deliberately NOT extended:
        # they are distinct nets between neighbouring amplifier columns, so
        # abutting them would short every column's source node together.
        for name in ("inv_out_gnd",
                     "inv_bl_vdd", "inv_br_vdd", "inv_out_vdd"):
            yc = p[name][1]
            self.add_rect("m1", offset=(2.78, yc - 0.13),
                          width=0.22, height=0.26)

    def add_layout_pins(self, ll):
        p = self.pin_centers()

        # Top-edge m2 bitline pads on the cell1rw bitline centres.
        for name, cx in (("bl", 0.75), ("br", 2.25)):
            self.add_layout_pin_rect_center(
                text=name, layer="m2",
                offset=vector(cx, 0.5 * (self.PIN_Y0 + self.PIN_Y1)),
                width=0.60, height=self.PIN_Y1 - self.PIN_Y0)

        # dout is exposed on m3 rather than on the output inverter's m1 pin.
        # The bank promotes every data output up to the m3 power grid, and a
        # via stack punched at the m1 output pin (y = 13.45) would land in the
        # middle of the amp's own trunks: the m3 pad would be 0.09 um from the
        # nsense trunk (overlap) and the m2 pad would sit on the m2 gnd trunk,
        # i.e. two shorts.  There is no free 0.38 um column anywhere inside the
        # cell between the three m3 trunks, but there is one to the left of bl
        # (0.04-0.42, 0.29 um clear of bl at 0.71).  Reach it with an m1 stub
        # from the inverter output and a via stack there.
        dout_x = self.BL_TX - 0.62
        dout_y = p["inv_out_Z"][1]
        self.add_rect("m1",
                      offset=(dout_x - 0.13, dout_y - 0.13),
                      width=(p["inv_out_Z"][0] + 0.13) - (dout_x - 0.13),
                      height=0.26)
        self.via("dout", (dout_x, dout_y), "m1", "m3")
        self.add_layout_pin_rect_center(text="dout", layer="m3",
                                        offset=vector(dout_x, dout_y),
                                        width=0.38, height=0.38)

        # en is the tail gate.  The pin is the m1 landing of the poly riser
        # above the device; see route_nets.
        en_x = self.tail_inst.offset.x + 0.5 * self.tail.width
        self.add_layout_pin_rect_center(text="en", layer="m1",
                                        offset=vector(en_x, self.EN_Y))

        # Supply pins: use the actual rail extents (not WIDTH) so that the
        # cell does not grow past the array pitch.
        vdd_pin = self.inv_out_inst.get_pin("vdd")
        gnd_pin = self.inv_out_inst.get_pin("gnd")
        self.add_layout_pin(text="vdd",
                            layer=vdd_pin.layer,
                            offset=vdd_pin.ll(),
                            width=vdd_pin.width(),
                            height=vdd_pin.height())
        self.add_layout_pin(text="gnd",
                            layer=gnd_pin.layer,
                            offset=gnd_pin.ll(),
                            width=gnd_pin.width(),
                            height=gnd_pin.height())

    ############################################################
    # Helpers
    ############################################################
    def pin_centers(self):
        """Return the center of every pin used for internal routing."""
        centers = {}
        for tag, inst in (("inv_bl", self.inv_bl_inst),
                          ("inv_br", self.inv_br_inst),
                          ("inv_out", self.inv_out_inst)):
            # The cross-coupled pair has a signal source rail named nsense;
            # the output buffer is a stock pinv with a gnd rail.
            for name in ("A", "Z", "vdd", "gnd", "nsense"):
                pins = inst.get_pins(name)
                if not pins:
                    continue
                pin = pins[0]
                centers["{}_{}".format(tag, name)] = (pin.center().x,
                                                      pin.center().y)
        for name in ("D", "S", "G"):
            pin = self.tail_inst.get_pin(name)
            centers["tail_{}".format(name)] = (pin.center().x, pin.center().y)
        return centers

    def route(self, net, layer, p1, p2):
        """Draw an axis-aligned wire and record it for the overlap check."""
        width = getattr(self, "{}_width".format(layer))
        x0, x1 = sorted((p1[0], p2[0]))
        y0, y1 = sorted((p1[1], p2[1]))
        x0 -= 0.5 * width
        x1 += 0.5 * width
        y0 -= 0.5 * width
        y1 += 0.5 * width
        self.add_rect(layer, offset=(x0, y0), width=x1 - x0, height=y1 - y0)
        self._route_rects.append((net, layer, (x0, y0, x1, y1)))

    def via(self, net, offset, from_layer, to_layer, dirs=("H", "V")):
        # The m1 enclosure is asymmetric in gf180mcu (0 / 0.15), so the pad
        # orientation matters: H keeps the landing only 0.26 um tall, which
        # is what fits between the pinv rails and its well taps.  The one
        # exception is the tail source, which is only 0.26 um from the cell
        # edge and must use a vertical pad.
        return self.add_via_stack_center(from_layer=from_layer,
                                         to_layer=to_layer,
                                         offset=vector(*offset),
                                         directions=dirs)

    def check_routes(self):
        """Fail loudly if two different nets overlap on the same layer."""
        rects = self._route_rects
        for i in range(len(rects)):
            net1, layer1, r1 = rects[i]
            for j in range(i + 1, len(rects)):
                net2, layer2, r2 = rects[j]
                if layer1 != layer2 or net1 == net2:
                    continue
                if (r1[0] < r2[2] - 1e-6 and r2[0] < r1[2] - 1e-6 and
                        r1[1] < r2[3] - 1e-6 and r2[1] < r1[3] - 1e-6):
                    debug.error("gf180_sense_amp: {0} and {1} overlap on {2}: {3} {4}".format(
                        net1, net2, layer1, r1, r2), -1)
                    raise RuntimeError("gf180_sense_amp: {0} and {1} overlap on {2}: {3} {4}".format(
                        net1, net2, layer1, r1, r2))
