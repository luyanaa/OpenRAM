"""Restore the connectivity labels of the gf180mcu 1RW bitcell.

The vendor ``cell1rw`` is a dense 6T cell with no spare diffusion area: every
well tap / extra contact would violate the 0.36 um MV-diffusion spacing of the
tiled array.  Its connectivity is therefore expressed with named labels, the
way the foundry ships it:

  * ``GND`` sits on the m1 VSS rail (the rail is the comb-shaped polygon at
    y 0.69..1.87 that feeds the two pull-down sources);
  * ``WL`` appears twice: once on the m3 wordline strap and once on the
    horizontal access-gate poly.  Magic merges the two by name, which is how
    the wordline reaches the gate in the standalone cell.

An earlier edit moved ``GND`` onto the BL m1 landing pad (which shorted GND to
BL) and dropped the poly ``WL`` label (which left the access gates floating).
This script restores both and is idempotent.

Run with the gdstk-enabled interpreter:
  /home/yanlu/miniforge3/envs/hammer/bin/python3 technology/gf180mcu/gds_lib/patch_cell1rw.py
"""
import os
import gdstk

HERE = os.path.dirname(os.path.abspath(__file__))
GDS = os.path.join(HERE, "cell1rw.gds")

M1 = 34
POLY = 30

# On the m1 VSS rail (rail top edge is 1.29 at this x).
GND_POS = (0.163, 1.13)
# On the horizontal access-gate poly (poly spans y 0.26..1.03).
WL_POLY_POS = (-0.005, 0.492)

# OpenRAM's GDS pin reader (`gdsMill.vlsiLayout.getPinShape`) only tests a
# label against the bounding box implied by the first two vertices of a shape.
# The m1 VSS rail is an 18-point comb (a polygon), so its first two vertices
# share the same x and the label is never "inside" it.  Rectangular pins have
# always worked (that is how the DFF's VDD/GND pads are found), so give the
# bitcell GND a small rectangular m1 pad inside the existing comb.  The pad is
# fully enclosed by the rail, so it only re-states M1 already present and adds
# no connectivity - it merely gives the reader a rectangle to anchor the pin.
GND_PAD = (0.100, 1.020, 0.240, 1.240)   # x0, y0, x1, y1 (inside the comb)


def _has_pad(top):
    for poly in top.polygons:
        if poly.layer != M1:
            continue
        (x0, y0), (x1, y1) = poly.bounding_box()
        if (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)) == \
                tuple(round(v, 3) for v in GND_PAD):
            return True
    return False


def main():
    lib = gdstk.read_gds(GDS)
    top = lib.top_level()[0]

    # Drop every GND/WL label, then re-add them at the two known-good points.
    doomed = [l for l in top.labels if l.text in ("GND",)]
    doomed += [l for l in top.labels if l.text == "WL" and l.layer == POLY]
    if doomed:
        top.remove(*doomed)

    existing = {(l.text, l.layer) for l in top.labels}
    if ("GND", M1) not in existing:
        top.add(gdstk.Label("GND", GND_POS, layer=M1, texttype=0))
    if ("WL", POLY) not in existing:
        top.add(gdstk.Label("WL", WL_POLY_POS, layer=POLY, texttype=0))

    if not _has_pad(top):
        (x0, y0, x1, y1) = GND_PAD
        top.add(gdstk.rectangle((x0, y0), (x1, y1),
                                layer=M1, datatype=0))

    lib.write_gds(GDS)
    print("labels now:")
    for l in top.labels:
        print("   %-6s L%-4d %s" % (l.text, l.layer,
                                    tuple(round(v, 3) for v in l.origin)))
    print("gnd pad present:", _has_pad(top))


if __name__ == "__main__":
    main()
