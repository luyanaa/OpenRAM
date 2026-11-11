# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""gf180mcu 5 V precharge fitted to the bitcell column pitch.

The thick-oxide 5 V PMOS is much wider than the 3.3 V device: its source and
drain contacts sit 1.56 um apart (0.57 um drawn width plus the 0.30 um
thick-oxide poly-to-diffusion clearance on both sides).  Two devices in one
row therefore span 3.12 um of pins plus the contact pads, i.e. about 3.5 um,
inside a 3.0 um bitcell column.  With the generic two-row placement the
outermost m1 pads of neighbouring columns overlap by 0.26 um, which shorts
BL of column i+1 to BR of column i and chains the whole precharge array into
a single net (LVS reported one merged device and no isolated ``br`` net).

This cell stacks the three PMOS devices vertically, one per row.  Both
placements put the source/drain contacts on x = 0.69 and 2.25, which are the
bit-line contact centres of the vendor ``cell1rw`` (its BL/BR m2 pins span
0.45-1.05 and 1.95-2.55 um).  All three gate poly stripes land on the same x
range, so one poly strap ties the three gates.

Vertical pitch is set by the thick-oxide marker, which is drawn around the
whole device (active + 0.70 um of poly overhang).  Two markers closer than
that merge into one region that Magic then fails to classify as transistors
(varactor rules PL.4/DF.1c fire on the merged edges), so the pitch is chosen
just above one marker height.  The vdd rail is placed above the top device so
it cannot cross the top row's drain pad (that overlap shorted br to vdd).
"""

from openram.modules.precharge import precharge
from openram.base import vector
from openram.sram_factory import factory
from openram.tech import drc


class gf180_precharge(precharge):
    """Precharge with the 5 V devices stacked one per row."""

    # Device placements: a normal device has its D pin at +1.74 um and S at
    # +0.18 um, a mirrored (MY) one has them at -1.74 and -0.18 um.
    x_normal = 0.51
    x_mirror = 2.43

    # Source/drain contact centres (bitcell bit-line pin centres).
    bl_x = 0.69
    br_x = 2.25

    # Vertical m1 vdd trunk, in the gap between the two bit-line stripes.
    vdd_x = 1.45

    # Vertical row pitch: one thick-oxide marker is 2.57 um tall, so anything
    # below that merges the markers of adjacent rows.
    row_pitch = 1.90
    first_row_y = 1.50

    def add_ptx(self):
        """Force the 5 V pass PMOS to at least the thick-oxide minimum width.

        ``beta * min_tx_size`` is 1.7 * 0.25 = 0.425 um, which is below
        ``minwidth_tx`` = 0.57 um.  ptx then draws a ``minwidth_tx``
        dog-bone pad around every source/drain contact.  The two upper PMOS
        would share the VDD diffusion, so their pads merge into a 0.48 um
        wide blob whose outer corners sit only 0.18 um from the neighbouring
        gate; the thick-oxide poly-to-diffusion rule (PL.5a) needs 0.30 um.
        Drawing the device at ``minwidth_tx`` removes the pads (the whole
        diffusion is then at that width) and the notch disappears.
        """
        self.ptx_width = max(self.ptx_width, drc("minwidth_tx"))
        super().add_ptx()

    def place_ptx(self):
        """Stack the equaliser and the two pull-ups, one device per row.

        The netlist pin order of ``ptx`` is (D, G, S, B):

        * ``lower_pmos``   D=bl, S=br   -> mirrored, bl on 0.69, br on 2.25
        * ``upper_pmos1``  D=bl, S=vdd  -> mirrored, bl on 0.69, vdd on 2.25
        * ``upper_pmos2``  D=br, S=vdd  -> normal,   vdd on 0.69, br on 2.25
        """
        self.row_y = [self.first_row_y + i * self.row_pitch for i in range(3)]

        self.lower_pmos_position = vector(self.x_mirror, self.row_y[0])
        self.upper_pmos1_pos = vector(self.x_mirror, self.row_y[1])
        self.upper_pmos2_pos = vector(self.x_normal, self.row_y[2])

        self.lower_pmos_inst.place(offset=self.lower_pmos_position, mirror="MY")
        self.upper_pmos1_inst.place(offset=self.upper_pmos1_pos, mirror="MY")
        self.upper_pmos2_inst.place(offset=self.upper_pmos2_pos)

        # The vdd rail also carries the nwell tap, whose COMP is centred on
        # the rail.  It must clear the top device by the tap-to-diffusion and
        # tap-to-gate rules: the gate poly ends at row_y+0.87 and the n+ tap
        # needs 0.32 um to it (NP.4b), plus 0.36 um to the p+ active (DF.3a).
        # A 1.15 um offset put the tap 5 nm inside the gate poly, which Magic
        # extracted as a 5 nm-long cap_nmos_06v0_b between en_bar and vdd.
        self.vdd_rail_y = self.row_y[2] + 1.50

    def connect_poly(self):
        """Tie the three gate poly stripes with one vertical strap.

        Every placement above puts the poly gate strap on the same x range
        (1.17-1.77 um), so a single rect through the three rows ties the
        gates without crossing any foreign diffusion: the strap lies inside
        each row's gate stripe.
        """
        g0 = self.lower_pmos_inst.get_pin("G")
        g2 = self.upper_pmos2_inst.get_pin("G")
        y_bot = min(g0.by(), g2.by())
        y_top = max(g0.uy(), g2.uy())
        self.add_rect(layer="poly",
                      offset=vector(g0.lx(), y_bot),
                      width=g0.width(),
                      height=y_top - y_bot)

    def place_nwell_and_contact(self):
        """Nwell across the whole cell plus a vdd tap on the vdd rail."""
        tap = factory.create(module_type="contact",
                             layer_stack=self.active_stack,
                             implant_type="n",
                             well_type="n")
        self.well_contact_pos = vector(self.vdd_x, self.vdd_rail_y)
        self.well_contact = self.add_via_center(layers=self.active_stack,
                                                offset=self.well_contact_pos,
                                                implant_type="n",
                                                well_type="n")

        self.height = (self.well_contact_pos.y
                       + 0.5 * tap.height
                       + self.nwell_extend_active
                       + 0.1)

        # pmos-only cell: the well spans the full height and pokes out of the
        # column edges so the outermost column keeps its DF.7 enclosure.
        self.add_rect(layer="nwell",
                      offset=vector(0, 0),
                      width=self.width,
                      height=self.height)
        ext = self.well_extend_active
        self.add_rect(layer="nwell",
                      offset=vector(-ext, 0),
                      width=ext,
                      height=self.height)
        self.add_rect(layer="nwell",
                      offset=vector(self.width, 0),
                      width=ext,
                      height=self.height)

    def route_supplies(self):
        """Route vdd on m1 only: one rail above the stack plus two risers.

        The top row's source pad is on x = 0.69, so a straight riser from it
        up to the rail is clear.  The middle row's source pad is on x = 2.25,
        directly under the top row's drain pad, so it is jogged into the
        centre gap on m1 and taken up there.  The vertical trunk crosses the
        top row's gate poly and diffusion without a contact, which is legal
        (m1 over poly/active carries no connection).
        """
        trunk_w = self.m1_width
        rail_y = self.vdd_rail_y

        # vdd rail above the top device (the array stitches these across the
        # columns and it must only touch the top row's source pad).
        self.add_rect(layer="m1",
                      offset=vector(0, rail_y - 0.5 * trunk_w),
                      width=self.width,
                      height=trunk_w)

        # top row: source pad on 0.69 um, straight riser to the rail.
        self.add_rect(layer="m1",
                      offset=vector(self.bl_x - 0.5 * trunk_w,
                                    self.row_y[2] - 0.03),
                      width=trunk_w,
                      height=(rail_y + 0.5 * trunk_w)
                      - (self.row_y[2] - 0.03))

        # middle row: source pad on 2.25 um, jog into the centre gap.
        self.add_rect(layer="m1",
                      offset=vector(self.vdd_x - 0.5 * trunk_w,
                                    self.row_y[1] + 0.15),
                      width=(self.br_x + 0.5 * trunk_w) - self.vdd_x
                      + 0.5 * trunk_w,
                      height=0.30)
        self.add_rect(layer="m1",
                      offset=vector(self.vdd_x - 0.5 * trunk_w,
                                    self.row_y[1] + 0.15),
                      width=trunk_w,
                      height=(rail_y + 0.5 * trunk_w)
                      - (self.row_y[1] + 0.15))

        self.add_layout_pin_rect_center(text="vdd",
                                        layer="m1",
                                        offset=vector(0.5 * self.width, rail_y),
                                        width=trunk_w,
                                        height=trunk_w)

    def route_bitlines(self):
        """Vertical bit-line stripes on the bitcell pin centres."""
        self.bl_xoffset = self.bl_x
        self.br_xoffset = self.br_x

        top_pos = vector(self.bl_x, self.height)
        pin_pos = vector(self.bl_x, 0)
        self.add_path(self.bitline_layer, [top_pos, pin_pos])
        self.bl_pin = self.add_layout_pin_segment_center(text="bl",
                                                         layer=self.bitline_layer,
                                                         start=pin_pos,
                                                         end=top_pos)

        top_pos = vector(self.br_x, self.height)
        pin_pos = vector(self.br_x, 0)
        self.add_path(self.bitline_layer, [top_pos, pin_pos])
        self.br_pin = self.add_layout_pin_segment_center(text="br",
                                                         layer=self.bitline_layer,
                                                         start=pin_pos,
                                                         end=top_pos)

    def connect_to_bitlines(self):
        """Contact only the true bit-line pins; the vdd pads must stay clear.

        The middle row's vdd pad sits on x = 2.25 and the top row's on
        x = 0.69, i.e. directly under a bit-line stripe, so adding a via at
        those two pins would short vdd to the bit-lines.
        """
        bl_pins = [self.lower_pmos_inst.get_pin("D"),
                   self.upper_pmos1_inst.get_pin("D")]
        br_pins = [self.lower_pmos_inst.get_pin("S"),
                   self.upper_pmos2_inst.get_pin("D")]

        for pin in bl_pins + br_pins:
            self.add_via_stack_center(from_layer=pin.layer,
                                      to_layer=self.bitline_layer,
                                      offset=pin.center(),
                                      directions=("V", "V"))
