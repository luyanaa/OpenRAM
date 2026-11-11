#!/usr/bin/env python3
"""Derive a well-tapped variant of the gf180mcu 1RW bitcell.

``cell1rw`` has no room for a well tie inside its own 3.0 x 4.5 um pitch: the
free channels left of ``ACT`` are only 0.52 um wide and the 5 V tap needs
0.36 um to the neighbouring diffusion plus 0.30 um of active.  The array
therefore keeps the stock cell and puts the taps in the *outer dummy column*,
where the wells are still continuous in x but there is nothing to the left of
the left-most dummy column.

This script derives ``cell1rw_tap.gds`` from ``cell1rw.gds``:

  * drops the ``nwell``/``pwell`` labels (they are drawn as named nets by
    Magic; after tapping they are simply VDD/GND);
  * grows the nwell/pwell/dualgate to the left so the taps live in their own
    wells;
  * adds one n+ tap in the nwell and one p+ tap in the pwell, each tied to the
    matching m1 rail through a single contact.

The tap cell is used only in the ``row_cap_left`` dummy column (see
``gf180mcu_tap_bitcell``), so the y placement is chosen to survive the row
mirroring: the n-tap sits near the *bottom* of its nwell and the p-tap near
the *top* of its pwell.  A row pair shares an nwell band; if both cells put
the n-tap at the same relative height the two contacts would overlap exactly.

Run with the gdstk-enabled interpreter:
  /home/yanlu/miniforge3/envs/hammer/bin/python3 technology/gf180mcu/gds_lib/patch_cell1rw_tap.py
"""
import os
import gdstk

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "cell1rw.gds")
DST = os.path.join(HERE, "cell1rw_tap.gds")

BOUND = 0
NWELL = 21
COMP = 22      # active / diffusion
POLY = 30
PPLUS = 31
NPLUS = 32
CONT = 33
M1 = 34
DUALGATE = 55
PWELL = 204

PREC = 1e-4

# The cell (abutment) is x 0..3.0, y 0..4.5; the drawn well box is
# x -0.34..3.34, y -0.34..4.84.  The vertical poly (the access gates) reaches
# x -0.18, so the tap diffusion must stay at or left of x -0.50 (0.30 um
# poly-to-active spacing).  That leaves a 0.42 um wide tap.
TAP_X0, TAP_X1 = -0.92, -0.50
TAP_CONT_X0, TAP_CONT_X1 = -0.82, -0.60

# n-tap: near the bottom of the nwell (3.11) so a mirrored row lands at
# 5.20..5.70 instead of on top of this copy.
N_ACT_Y0, N_ACT_Y1 = 3.30, 3.80
N_CONT_Y0, N_CONT_Y1 = 3.44, 3.66
N_IMP = (-1.05, 3.15, -0.37, 3.95)          # NPLUS over the n-tap
N_M1_V = (-0.92, 3.30, -0.50, 4.40)         # vertical drop from the contact
N_M1_H = (-0.92, 4.10, -0.10, 4.74)         # horizontal strap into the VDD rail

# p-tap: near the top of the pwell, tied straight into the GND comb.
P_ACT_Y0, P_ACT_Y1 = 0.74, 1.24
P_CONT_Y0, P_CONT_Y1 = 0.88, 1.10
P_IMP = (-1.05, 0.59, -0.37, 1.39)          # PPLUS over the p-tap
P_M1 = (-0.92, 0.72, -0.10, 1.20)           # strap into the GND comb

# 0.16 um nwell/pwell surround of the tap active (5 V rule used by the DFF taps)
WELL_X0 = TAP_X0 - 0.16


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


def main():
    lib = gdstk.read_gds(SRC)
    top = lib.top_level()[0]

    doomed = [l for l in top.labels if l.text in ("nwell", "pwell")]
    top.remove(*doomed)
    print("removed {} well labels".format(len(doomed)))

    # Grow the wells (and the thick-oxide mask) to the left.  The cell keeps
    # its declared 3.0 x 4.5 pitch: `array_size` in the technology cell
    # properties overrides whatever the boundary layer reports.
    replace_layer(top, NWELL, [rect(WELL_X0, 3.11, 3.34, 4.84, NWELL)])
    replace_layer(top, PWELL, [rect(WELL_X0, -0.34, 3.34, 3.11, PWELL)])
    replace_layer(top, DUALGATE, [rect(WELL_X0, -0.34, 3.34, 4.84, DUALGATE)])

    # The existing PPLUS/NPLUS blankets stop at x -0.34, so the taps (x <=
    # -0.50) need their own implant rectangle and the opposite implant does
    # not shadow them.
    top.add(rect(*N_IMP, layer=NPLUS))
    top.add(rect(*P_IMP, layer=PPLUS))

    # n+ tap in the nwell (VDD)
    top.add(rect(TAP_X0, N_ACT_Y0, TAP_X1, N_ACT_Y1, COMP))
    top.add(rect(TAP_CONT_X0, N_CONT_Y0, TAP_CONT_X1, N_CONT_Y1, CONT))
    top.add(rect(*N_M1_V, layer=M1))
    top.add(rect(*N_M1_H, layer=M1))

    # p+ tap in the pwell (GND)
    top.add(rect(TAP_X0, P_ACT_Y0, TAP_X1, P_ACT_Y1, COMP))
    top.add(rect(TAP_CONT_X0, P_CONT_Y0, TAP_CONT_X1, P_CONT_Y1, CONT))
    top.add(rect(*P_M1, layer=M1))

    # Keep the two layer-0 rectangles (the drawn bbox and the 3.0 x 4.5
    # boundary) exactly as the stock cell has them, so the measured library
    # size is unchanged.

    top.name = "cell1rw_tap"
    lib.write_gds(DST)
    print("wrote", DST)
    print("labels:", [(l.text, l.layer) for l in top.labels])
    print("bbox:", tuple(round(v, 3) for v in top.bounding_box()[0]),
          tuple(round(v, 3) for v in top.bounding_box()[1]))


if __name__ == "__main__":
    main()
