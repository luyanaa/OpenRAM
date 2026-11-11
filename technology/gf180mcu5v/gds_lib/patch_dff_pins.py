"""Add clean metal pin pads to the gf180mcu DFF hard cell.

gdsMill's label-to-polygon matching (labelInRectangle) treats only the first
four coordinates of a polygon as its rectangle.  The vendor DFF exports most
of its m1 pins as multi-vertex (24/28/12/6 point) polygons, so Q/VDD/GND were
not extracted at all.

OpenRAM's requirements:
  * dff_array needs D/Q/clk on metal2 (they are re-pinned and routed), so those
    three get an m1 pad + via1 + m2 pad and their label moves to layer 36.
  * VDD/GND must stay on metal1: the control logic builds pinv/pand2/pbuf/
    pdriver gates whose supply rails are m1, and control_logic_base takes the
    supply pin layer from the DFF.  This mirrors sky130, where the DFF has
    VDD/GND on li1 and D/Q/clk on met2.

The added pads are centred on the vendor label origins, which are inside the
original m1 shapes, so every pad connects to the intended net.  VDD/GND are
shifted to x=1.65 so that the dff_array via stacks (dropped at the pad's lc()
/ rc()) stay clear of the vertical clock strip at x=0.56..0.84.

Run with the gdstk-enabled interpreter:
  /home/yanlu/miniforge3/envs/hammer/bin/python3 technology/gf180mcu/gds_lib/patch_dff_pins.py
"""
import os
import gdstk

HERE = os.path.dirname(os.path.abspath(__file__))
GDS = os.path.join(HERE, "gf180_dff.gds")

LAYER_M1 = 34
LAYER_VIA1 = 35
LAYER_M2 = 36

# pin -> (center x, center y, target layer)
PINS = [
    ("D",   4.76, 1.96, LAYER_M2),
    ("Q",  15.20, 2.60, LAYER_M2),
    ("CLK", 0.84, 1.96, LAYER_M2),
    ("VDD", 1.65, 3.92, LAYER_M1),
    ("GND", 1.65, 0.00, LAYER_M1),
]

# Magic DRC (gf180mcuD.tech) requires m1 overlap of via1 of 0.06 in one
# direction and 0.04 in the other (V1.3), and m2 overlap of 0.06/0.04 (V1.4i),
# not the 0.15 enclosure that OpenRAM's own DRC model assumes.  A 0.38 um m1
# pad therefore fits inside the vendor CLK pin (0.94 x 0.38 um) while leaving
# the 0.23 um m1 spacing to the neighbouring nets intact.
M1_PAD = 0.38
M2_PAD = 0.40
VIA1 = 0.26


def rect(cx, cy, size, layer):
    h = 0.5 * size
    return gdstk.rectangle((cx - h, cy - h), (cx + h, cy + h), layer=layer, datatype=0)


def main():
    lib = gdstk.read_gds(GDS)
    top = lib.top_level()[0]

    names = {p[0] for p in PINS}
    doomed = [l for l in top.labels if l.text in names and l.layer == LAYER_M1]
    top.remove(*doomed)
    print("removed {} m1 labels, kept {}".format(len(doomed), len(top.labels)))

    for name, cx, cy, target in PINS:
        top.add(rect(cx, cy, M1_PAD, LAYER_M1))
        if target == LAYER_M2:
            top.add(rect(cx, cy, VIA1, LAYER_VIA1))
            top.add(rect(cx, cy, M2_PAD, LAYER_M2))
        top.add(gdstk.Label(name, (cx, cy), layer=target, texttype=0))

    lib.write_gds(GDS)
    print("wrote", GDS)


if __name__ == "__main__":
    main()
