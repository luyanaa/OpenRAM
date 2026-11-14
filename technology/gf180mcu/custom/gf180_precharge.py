# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""gf180mcu precharge with the vdd tap nudged off the bitline stripe.

The generic precharge puts the nwell tap at the centre of the upper pmos
source pin, which in gf180mcu is x = 1.74 inside a 3.0 um column.  The vdd
pin and the array-level m1->m3 via stack are both centred on that tap, so
their 0.38 um m2 pad spans 1.55-1.93 and the br stripe starts at 2.20: a
0.27 um gap where the technology needs 0.28 (M2.2a).  The pad cannot be
narrowed because 0.26 um via1 plus 0.06 um m2 enclosure on each side is
already exactly 0.38 um, so the tap itself moves instead.  0.03 um of shift
leaves 0.30 um to the stripe and still keeps the tap well inside the nwell.
"""

from openram.modules.precharge import precharge


class gf180_precharge(precharge):
    """Precharge whose vdd tap clears the bitline stripe."""

    well_contact_x_offset = -0.03
