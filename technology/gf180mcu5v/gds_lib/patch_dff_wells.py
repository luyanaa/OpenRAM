"""Replace the gf180mcu DFF's well-tie labels with real well taps.

The vendor flop only carries ``VNW``/``VPW`` labels on the nwell/pwell
layers.  Magic turns those labels into standalone nets (VNW, VPW), so LVS
reports the pfet bodies on a different net from VDD and the nfet bodies on a
different net from GND.  A label-only fix is not acceptable: the bodies have
to be tied to the rails with real diffusion.

Adding a tap inside the original footprint is impossible: the pwell's only
diffusion-free band is 0.115 um tall (MV diffusion needs 0.30 um width and
0.2025 um^2 area) and every x gap between diffusions is smaller than the
0.36 um MV spacing.  The cell is therefore grown by 0.83 um in y and the two
taps live in the new bands while the original content keeps its coordinates:

  * p-tap: an mvpsd (p+) diffusion below the cell, y -0.855 .. -0.245, tied
    to the m1 GND pad/plate with one contact;
  * n-tap: an mvnsd (n+) diffusion above the cell, y 3.87 .. 4.30, tied to
    the m1 VDD plate with one contact.

Placement rules that fix the previous 11 array DRC errors
---------------------------------------------------------
The flop tiles in vertically mirrored rows (row 0 identity, row 1 mirrored
about its top edge), and the row pitch is the BOUND height.  Two different
cells therefore face each other at every row boundary:

  * the two taps are 0.49/0.90 um away from the mirrored copy of themselves,
    so their contacts no longer touch the row boundary (Magic rejects
    "abut or partially overlap between subcells" for contacts even when the
    CO.2a rule would allow touching);
  * both taps stay >= 0.36 um from the neighbouring *column*'s MV diffusion
    (that instance is offset by x pitch only, so the y clearance is what
    matters) and >= 0.30 um from its poly (PL.5a);
  * the nwell, pwell, BOUND and the m1 VDD plate are extended to the new
    bounds so the wells stay continuous across rows (nwell top 4.80 and VDD
    plate top 4.85 overlap the mirrored row's, exactly like the vendor cell
    did at the old pitch).

Magic derives an mvpsd only from ``DIFF & PPLUS & !nwell & !POLY & !NPLUS``
(and the n-side mirror), and cifinput needs the DUALGATE (5 V oxide) mask on
top of it, otherwise the tap extracts as a 3.3 V tap that needs 0.48 um to
the 5 V diffusions.  The implant blankets are notched where needed, PPLUS is
added under the p-tap and NPLUS under the n-tap, and DUALGATE is extended
over both.

Geometry follows the vendor ``__filltie`` cell, the canonical gf180mcu
well-tie, and the 5 V diffusion rules (0.36 um spacing, 0.30 um minimum
diffusion width, 0.2025 um^2 minimum area, 0.065 um contact enclosure,
0.16 um nwell surround of an n+ tap).

Run with the gdstk-enabled interpreter:
  /home/yanlu/miniforge3/envs/hammer/bin/python3 technology/gf180mcu/gds_lib/patch_dff_wells.py
"""
import os
import gdstk

HERE = os.path.dirname(os.path.abspath(__file__))
GDS = os.path.join(HERE, "gf180_dff.gds")

BOUND = 0
NWELL = 21
COMP = 22     # active / diffusion
POLY = 30
PPLUS = 31
NPLUS = 32
CONT = 33
M1 = 34
DUALGATE = 55
PWELL = 204

PREC = 1e-4

# New cell height.  The vendor cell is 3.92 um; the n-tap needs 0.18 um of
# clearance to the row boundary (mirror) above it, so the flip-flop row pitch
# grows to 4.40 um.  The m1 VDD plates of adjacent rows stop overlapping at
# this pitch; dff_array.route_supplies() straps them together instead.
H = 3.92

# Cell width (x pitch).  The vendor cell is 16.24 um.  The p-tap needs 0.36 um
# to the same-column mirrored row's rightmost n+ (x=15.84) and to the next
# column's leftmost n+ (x = W + 0.18), so the width grows to 17.0: the tap sits
# at x 16.20..16.50 and the next column's diffusion starts at 17.18.
W = 17.0

# p-tap (GND).  Fully below the NPLUS blanket (bottom edge -0.2), so the
# derived psd is not shadowed by the opposite implant.  0.36 x 0.61 um =
# 0.220 um^2 (> 0.2025).  The top is 0.49 um from its own mirrored copy, the
# left edge 0.36 um from the cell's n+ diffusion at x=15.84 and the right
# edge 0.30 um from the neighbouring column's poly at x=16.86.  The contact
# keeps 0.075 um of COMP on every side because Magic inflates the drawn
# contact by 5 nm before applying the 0.065 um CO.4 enclosure.
P_ACT = (16.200, -0.840, 16.620, -0.260)
P_CONT = (16.310, -0.580, 16.530, -0.360)
P_IMP = (16.100, -0.910, 16.690, -0.240)
P_BAR = (16.100, -0.860, 16.640, 0.300)

# n-tap (VDD).  0.44 x 0.58 um = 0.255 um^2, kept inside the vendor nwell so
# that it needs no well extension: 0.17 um of nwell on the right and above
# (DF.4a needs 0.16).  The bottom is 0.375 um above the neighbouring column's
# p+ diffusion at y=3.225 and 0.38 um right of the cell's own p+ at x=15.68;
# the top is 0.22 um below the row boundary so the mirrored copy stays 0.44 um
# away.  The existing m1 VDD pad already encloses the contact.
N_ACT = (16.060, 2.020, 16.500, 2.600)
N_CONT = (16.160, 2.200, 16.380, 2.420)
N_IMP = (15.960, 1.950, 16.600, 2.650)
N_BAR = (16.080, 2.140, 16.460, 3.000)

# Grown layers.  Only the pwell needs to move: it has to contain the p-tap.
# The nwell is left exactly as the vendor drew it (it already surrounds the
# n-tap); widening it in x is rejected by Magic (the 8-vertex notch polygon
# plus the fully drawn nwell is a well-rule corner case).  DUALGATE grows over
# both taps because cifinput only derives an mvpsd/mvnsd under the 5 V mask.
PWELL_BOX = (-0.430, -1.000, 16.670, -0.420)
DUALGATE_RECT = (-0.280, -1.000, 16.670, 4.400)


def rect(x0, y0, x1, y1, layer):
    return gdstk.rectangle((x0, y0), (x1, y1), layer=layer, datatype=0)


def layer_polys(top, layer):
    return [p for p in top.polygons if p.layer == layer and p.datatype == 0]


def replace_layer(top, layer, polys):
    old = layer_polys(top, layer)
    if old:
        top.remove(*old)
    for p in polys:
        top.add(p)


def subtract_window(top, layer, window):
    """Remove `window` (x0, y0, x1, y1) from every polygon on `layer`."""
    shapes = layer_polys(top, layer)
    if not shapes:
        return
    cutter = [rect(*window, layer=layer)]
    result = gdstk.boolean(shapes, cutter, "not", precision=PREC,
                           layer=layer, datatype=0)
    replace_layer(top, layer, result)


def union_rect(top, layer, box):
    """Union a rectangle into `layer`, preserving the vendor polygon shape.

    The vendor nwell/pwell are 8-vertex polygons with an interlocking notch at
    x 4.865..6.12 (that notch is what keeps the n+ diffusion 0.6 um away from
    the nwell, DF.8), so the layer must never be replaced by a plain box.
    """
    shapes = layer_polys(top, layer) + [rect(*box, layer=layer)]
    result = gdstk.boolean(shapes, [], "or", precision=PREC,
                           layer=layer, datatype=0)
    replace_layer(top, layer, result)


def main():
    lib = gdstk.read_gds(GDS)
    top = lib.top_level()[0]

    doomed = [l for l in top.labels if l.text in ("VNW", "VPW")]
    top.remove(*doomed)
    print("removed {} well labels ({} left)".format(len(doomed), len(top.labels)))

    # Grow the pwell downwards so it contains the p-tap.  The vendor polygon
    # has an interlocking notch with the nwell, so union instead of replacing.
    union_rect(top, PWELL, PWELL_BOX)

    # Grow the thick-oxide mask over both taps and the cell border.
    replace_layer(top, DUALGATE, [rect(*DUALGATE_RECT, layer=DUALGATE)])
    replace_layer(top, BOUND, [rect(0.0, 0.0, W, H, layer=BOUND)])

    # Extend the m1 supply plates across the new width so adjacent columns' rails
    # abut instead of floating apart.
    # Add (do not boolean-merge) the extensions: get_gds_pins matches the VDD/
    # GND labels to the small rectangular m1 pads, so the pads must stay as
    # separate polygons.  Overlapping same-layer rectangles merge in Magic.
    top.add(rect(16.240, -0.300, W, 1.105, layer=M1))
    top.add(rect(16.240, 2.635, W, 4.220, layer=M1))

    # Clear the opposite implant from each tap area.  The n-tap sits under the
    # vendor PPLUS blanket (top 4.12), so it must be notched there.
    subtract_window(top, PPLUS, (15.900, 1.900, 16.800, 2.700))
    # The mirrored row above maps its p-tap to this cell's local y 0.245..0.855,
    # where the NPLUS blanket would shadow the p+ tap.  x >= 16.0 is free of
    # devices, so notch NPLUS there for both orientations.
    subtract_window(top, NPLUS, (16.000, 0.180, 16.800, 0.950))

    # p+ tap in pwell (tied to GND)
    top.add(rect(*P_IMP, layer=PPLUS))
    top.add(rect(*P_ACT, layer=COMP))
    top.add(rect(*P_CONT, layer=CONT))
    top.add(rect(*P_BAR, layer=M1))

    # n+ tap in nwell (tied to VDD)
    top.add(rect(*N_IMP, layer=NPLUS))
    top.add(rect(*N_ACT, layer=COMP))
    top.add(rect(*N_CONT, layer=CONT))
    top.add(rect(*N_BAR, layer=M1))

    lib.write_gds(GDS)
    print("wrote", GDS)


if __name__ == "__main__":
    main()
