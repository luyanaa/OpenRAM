# See LICENSE for licensing information.
#
"""gf180mcu parameterized multiport bitcell (pbitcell) with tech-local fixes.

The generic ``pbitcell`` cross-couples the storage inverters with two poly
wires, each tied to an inverter output through a poly->m1 contact.  The two
contacts are placed ``poly_to_contact + poly_contact.width`` apart in y.  On
gf180mcu ``poly_to_contact`` is 0 and the contact's poly pad is 0.36 um
square, so the two pads are exactly 0.36 um apart and abut: the Q and Q_bar
poly wires merge into a single polygon.  DRC does not flag this (same-layer
abutment is one shape), but Magic extraction makes Q and Q_bar one net, so
LVS reports the cell shorted ("Top level cell failed pin matching").

Raising the upper cross-couple wire and contact by 0.26 um leaves 0.26 um of
poly between the pads (rule: poly_to_poly = 0.24) and 0.34 um between the two
wire edges.  Nothing else in the cell depends on this y coordinate.
"""

from openram.modules.pbitcell import pbitcell


class gf180_pbitcell(pbitcell):

    def calculate_spacing(self):
        super().calculate_spacing()
        # gf180mcu 5 V shares the 3.3 V cross-couple abutment bug: the two
        # poly->m1 contact pads merge when the upper wire sits at the generic
        # y.  Keep the 3.3 V +0.26 fix here; the remaining 5 V DRC errors
        # (PL.5a / CO.8 / PL.3a / M1.2a) are a denser-layout issue, see
        # gf180_work/multiport_5v/README.md.
        self.cross_couple_upper_ypos += 0.26
