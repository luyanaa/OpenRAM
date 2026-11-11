# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""gf180mcu write driver built from parameterized cells.

gf180mcu has no extracted write-driver macro: the PDK only contains the
018SRAM IP column slices (``din_3v64x8m81`` is a ~14 x 45 um block), and the
12T 2R2W macro's column leaf is a 26 x 70 um block, both far too coarse to
drop into an OpenRAM bitline pitch.

Topology
~~~~~~~~
Rather than the four-stage tri-state inverter stack of the generic OpenRAM
leaf (which needs four long, mutually crossing m2/m3 nets inside a 3.34 um
column), this cell uses the standard SRAM **pass-gate write driver**::

    inv_din : din -> din_bar
    pg_bl   : NMOS(bl,  en, din)
    pg_br   : NMOS(br,  en, din_bar)

With ``en`` high the two NMOS pass devices are on and the bitlines follow the
data: ``bl = din`` and ``br = din_bar``.  A zero is driven hard (the NMOS is
in its linear region) while a one only has to be held, which the precharge
already does.  With ``en`` low both devices are off and the bitlines are
released, so the cell is tri-stated exactly like the generic driver.

The polarity matches the sense amplifier (``dout = ~br``), so writing
``din`` and reading back returns ``din``.

Layer assignment
~~~~~~~~~~~~~~~~
The bitcell (``cell1rw``) exposes BL/BR as m2 stripes at x 0.45-1.05 and
1.95-2.55.  The sense amplifier cell above this one carries full-height m3
trunks at x = 0.85 / 2.20 that reach its bottom edge, so this cell also
raises BL/BR on **m3 trunks at the same x**, ending exactly on the shared
boundary.  That makes the sense-amp/write-driver bitline connection a plain
abutment with no bank-level bridge routing.

The rest of the cell is short (about 7.5 um) so the internal nets only need
m1/m2 jogs: ``en`` is a full-width m1 bus drawn by the array at the gate
riser height, and din/din_bar run on m2.
"""

from openram import debug
from openram import OPTS
from openram.base import design
from openram.base import vector
from openram.sram_factory import factory
from openram.tech import drc
from openram.modules.write_driver import write_driver as write_driver_base


class gf180_write_driver(write_driver_base):
    """Pass-gate write driver made of one inverter and two NMOS devices."""

    # Array pitch: must equal the bitcell width so that the m3 bitline
    # trunks line up with the bitcell columns and with the sense amp.
    WIDTH = 3.34
    # Height: inverter (4.84) + two stacked pass rows + the en contact and
    # bitline stubs.
    HEIGHT = 9.73

    # Bitline m3 trunks; the same x as gf180_sense_amp.
    BL_TX = 0.85
    BR_TX = 2.20
    # Temporary m2 landing column for the br m1->m3 via.  It has to clear the
    # din_bar m2 run that ends at x=0.30 on this row (padded right edge 1.87)
    # and the next column's din m2 trunk at 3.00 (pad edge <= 2.72).
    BR_VIA_TX = 2.45

    # The two pass devices are *stacked* rather than placed side by side.
    # Each ptx is 1.36 um wide in the active/contact direction (one 0.36 um
    # contact plus 0.18 um enclosure on each side, 2 x 0.18 contact-to-gate
    # and 0.28 poly), so two of them need 1.36 + 0.28 + 1.36 + 0.28 = 3.28 um
    # of pitch.  The bitcell pitch is 3.00 um, which is why the earlier
    # side-by-side placement at x=1.70 left only 0.04 um at the column seam
    # (M1.2a) and actually overlapped the neighbouring column's active by
    # 0.06 um.  Stacking one device above the other keeps every diffusion and
    # m1 pin inside x = 0 .. 1.36, well clear of the seam.
    #
    # 5.70 clears the inverter's nwell (top 5.355) by 0.045 um.  The second
    # row is one active height plus the 0.28 diffusion space above that.
    PASS_Y = 5.70
    # Pitch chosen so the two gate polys overlap (row 1's poly reaches
    # PASS_Y+1.49, row 2's starts at PASS_Y2-0.09): at 1.52 the columns
    # share 0.06 um of gate poly and are automatically the same en net,
    # while the two diffusions stay 0.38 um apart (> the 0.28 active space).
    PASS_Y2 = 7.22
    # The single en contact sits above row 2's gate, where there is no
    # diffusion to respect: a contact in the inter-row gap cannot satisfy
    # both CO.8 (0.165 from each diffusion) and PL.5a (0.10).  Row 2's gate
    # poly ends at PASS_Y2+1.49 = 8.71, so 8.70 puts the via's poly
    # enclosure (8.52..8.88) over it while keeping the contact 0.21 clear
    # of row 2's diffusion (top 8.36).
    EN_Y = 8.70
    # m1 height of the inverter's gnd rail (and therefore the low bound of
    # the cell before the standard offset).
    BOT_Y = -0.13

    def __init__(self, name="write_driver"):
        # Bypass write_driver.__init__: it loads a hard macro that does not
        # exist for this technology.
        design.__init__(self, name)
        self.add_comment("gf180mcu pass-gate write driver")

        self.add_pin_list(["din", "bl", "br", "en", "vdd", "gnd"],
                          ["INPUT", "OUTPUT", "OUTPUT", "INPUT", "POWER", "GROUND"])
        self.add_pin_names({x: x for x in ("din", "bl", "br", "en", "vdd", "gnd")})
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
        self.pg = factory.create(module_type="ptx",
                                 tx_type="nmos",
                                 width=2 * drc("minwidth_tx"))

    def create_insts(self):
        self.din_inv_inst = self.add_inst(name="wd_din_inv", mod=self.inv)
        self.connect_inst(["din", "din_bar", "vdd", "gnd"])

        self.bl_pg_inst = self.add_inst(name="wd_bl_pg", mod=self.pg)
        self.connect_inst(["bl", "en", "din", "gnd"])

        self.br_pg_inst = self.add_inst(name="wd_br_pg", mod=self.pg)
        self.connect_inst(["br", "en", "din_bar", "gnd"])

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
        # The vdd/gnd pins copied from the inverter sit at y=-0.13, so after
        # the pins exist the true low bound of the cell is below 0.  Drawing
        # the boundary from that bound keeps it aligned after the automatic
        # offset shifts the whole cell up by 0.13.
        self.add_boundary(self.find_lowest_coords())
        # Shift only in y so the vdd/gnd pins (which sit at y=-0.13) land on
        # the cell boundary, but keep the drawn column origin in x.  The ptx
        # well/implant overhang reaches x=-0.12; a plain offset_all_coordinates
        # would use that as the low bound and move the whole cell 0.12 um to
        # the right, leaving its m3 bitline trunks at 0.97 instead of the
        # sense amp's 0.85.  Well/implant overhang across the column seam is
        # harmless (they are not nets), so pin x to the layout coordinates.
        ll = self.find_lowest_coords()
        self.offset_all_coordinates(vector(-ll.x, 0))

    def place_insts(self):
        self.din_inv_inst.place(vector(0, 0))
        self.bl_pg_inst.place(vector(0, self.PASS_Y))
        # Stack the second pass device above the first instead of placing it
        # at x=1.70; see the PASS_Y comment for the pitch arithmetic.
        self.br_pg_inst.place(vector(0, self.PASS_Y2))

    def route_nets(self):
        p = self.pin_centers()

        # --- din: inv A -> bl pass source ---------------------------------
        # The m2 trunk sits at x=0.14 so that its right edge (0.28) is a full
        # 0.29 um from the A-pin via pad (left edge 0.57), which is the closest
        # same-net spacing in the cell.  A trunk at 0.30 left only a 0.13 um
        # notch and Magic flagged it as an M2.2a spacing error.
        a = p["din_inv_A"]
        b = p["bl_pg_S"]
        self.via("din", a, "m1", "m2")
        # The via's m2 landing pad (0.38) is wider than the 0.28 trunk.  If
        # the pad (in the child contact cell) only partially overlaps this
        # cell's m2 trunk, Magic reports M2.2a when the trunk reaches out to a
        # parent routing cell (the data-DFF channel route).  Cover the pad
        # with an exact-size square in this cell so the overlap is total.
        self.add_rect("m2", offset=(a[0] - 0.19, a[1] - 0.19),
                      width=0.38, height=0.38)
        self.route("din", "m2", a, (0.14, a[1]))
        self.route("din", "m2", (0.14, a[1]), (0.14, b[1]))
        self.route("din", "m2", (0.14, b[1]), (0.04, b[1]))
        self.via("din", (0.04, b[1]), "m2", "m1")
        self.route("din", "m1", (0.04, b[1]), b)

        # --- din_bar: inv Z -> br pass source -----------------------------
        # Same m2 trunk at x=1.73, but now the second pass device is on the
        # upper row, so the trunk runs up to PASS_Y2 and then jogs left to
        # the source contact at x=0.30 on that row.  The jog only exists at
        # y=PASS_Y2+0.57, above the din trunk (which stops at row 1), so the
        # two data nets never share an m2 track.
        a = p["din_inv_Z"]
        b = p["br_pg_S"]
        self.via("din_bar", a, "m1", "m2")
        self.route("din_bar", "m2", a, (1.73, a[1]))
        self.route("din_bar", "m2", (1.73, a[1]), (1.73, b[1]))
        self.route("din_bar", "m2", (1.73, b[1]), (0.30, b[1]))
        self.via("din_bar", (0.30, b[1]), "m2", "m1")
        self.route("din_bar", "m1", (0.30, b[1]), b)

        # --- en: one via tying both gate rows -----------------------------
        # Row 2's gate poly already overlaps row 1's (the L-shaped stack is
        # one en net).  The via's own poly enclosure reaches down over row
        # 2's gate, so a single contact above row 2's diffusion serves both
        # gates; no separate poly riser is needed.
        xc = self.bl_pg_inst.get_pin("G").center().x
        self.via("en", (xc, self.EN_Y), "poly", "m1")

        # --- bl: pass drain -> m3 trunk to the top edge -------------------
        # Route m1 to the trunk column first and place the via there: a via
        # stack at the drain (x~1.18) leaves an m2 landing pad only 0.22 um
        # from the din_bar m2 trunk at 1.73 (M2.2a needs 0.28).  At x=0.85
        # the landing pad clears both the din m2 trunk (0.28) and din_bar.
        d = p["bl_pg_D"]
        self.route("bl", "m1", d, (self.BL_TX, d[1]))
        self.via("bl", (self.BL_TX, d[1]), "m1", "m3")
        self.route("bl", "m3", (self.BL_TX, d[1]),
                   (self.BL_TX, self.BOT_Y + self.HEIGHT - 0.14))

        # --- br: pass drain -> m3 trunk to the top edge -------------------
        # The br trunk (2.20) is too close to the din_bar m2 jog for a via
        # landing pad, so land the via in the free window at 2.45 (pad
        # 2.26-2.64) and jog on m3 back to the trunk.
        d = p["br_pg_D"]
        self.route("br", "m1", d, (self.BR_VIA_TX, d[1]))
        self.via("br", (self.BR_VIA_TX, d[1]), "m1", "m3")
        self.route("br", "m3", (self.BR_VIA_TX, d[1]),
                   (self.BR_TX, d[1]))
        self.route("br", "m3", (self.BR_TX, d[1]),
                   (self.BR_TX, self.BOT_Y + self.HEIGHT - 0.14))

        # --- supply rail abutment -----------------------------------------
        # The inverter is 2.90 um wide inside a 3.00 um column pitch; all
        # columns share the same orientation, so extend the m1 vdd/gnd rails
        # to the column edge and let the neighbours merge.
        for yc in (p["din_inv_gnd"][1], p["din_inv_vdd"][1]):
            self.add_rect("m1", offset=(2.78, yc - 0.13),
                          width=0.22, height=0.26)

    def add_layout_pins(self, ll):
        p = self.pin_centers()

        # din is exposed on the m2 trunk, not on the inverter's m1 A pin.
        # The bank's data channel route lands its pin-side via at pin.bc()
        # (half a pin height, 0.13 um, below the pin centre).  If the pin is
        # m1, that via overlaps the din m1->m2 via1 that this cell already
        # places at the pin centre, and Magic reports "can't abut or
        # partially overlap between subcells" once per bit column.  On m2 the
        # channel route's first via is a via2, safely clear of the m1 via1.
        self.add_layout_pin_rect_center(text="din", layer="m2",
                                        offset=vector(0.12, p["din_inv_A"][1]))

        # The array replaces this pin with a full-width m1 rail at the same y,
        # which is what ties the two stacked gate rows together.
        self.add_layout_pin_rect_center(text="en", layer="m1",
                                        offset=vector(p["bl_pg_G"][0], self.EN_Y))

        # Bitline pins: m2 pads on the top boundary.  They are 0.60 wide and
        # centred at the bitcell/bitline-stripe x (0.75 / 2.25) so that
        # port_data.connect_bitlines can z-jog them straight up on m2 to the
        # sense amp's m2 bitline pins.  A via drops each m3 trunk onto its pad.
        top = self.BOT_Y + self.HEIGHT
        for name, xc, xpin in (("bl", self.BL_TX, 0.75), ("br", self.BR_TX, 2.25)):
            self.add_layout_pin(text=name, layer="m2",
                                offset=vector(xpin - 0.30, top - 0.28),
                                width=0.60, height=0.28)
            self.via(name, (xc, top - 0.14), "m3", "m2")

        vdd_pin = self.din_inv_inst.get_pin("vdd")
        gnd_pin = self.din_inv_inst.get_pin("gnd")
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
        centers = {}
        for name in ("A", "Z", "vdd", "gnd"):
            pin = self.din_inv_inst.get_pin(name)
            centers["din_inv_{}".format(name)] = (pin.center().x, pin.center().y)
        for tag, inst in (("bl_pg", self.bl_pg_inst), ("br_pg", self.br_pg_inst)):
            for name in ("D", "G", "S"):
                pin = inst.get_pin(name)
                centers["{}_{}".format(tag, name)] = (pin.center().x, pin.center().y)
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

    def via(self, net, offset, from_layer, to_layer):
        return self.add_via_stack_center(from_layer=from_layer,
                                         to_layer=to_layer,
                                         offset=vector(*offset),
                                         directions=("H", "V"))

    def check_routes(self):
        rects = self._route_rects
        for i in range(len(rects)):
            net1, layer1, r1 = rects[i]
            for j in range(i + 1, len(rects)):
                net2, layer2, r2 = rects[j]
                if layer1 != layer2 or net1 == net2:
                    continue
                if (r1[0] < r2[2] - 1e-6 and r2[0] < r1[2] - 1e-6 and
                        r1[1] < r2[3] - 1e-6 and r2[1] < r1[3] - 1e-6):
                    debug.error("gf180_write_driver: {0} and {1} overlap on {2}: {3} {4}".format(
                        net1, net2, layer1, r1, r2), -1)
                    raise RuntimeError("gf180_write_driver: {0} and {1} overlap on {2}: {3} {4}".format(
                        net1, net2, layer1, r1, r2))
