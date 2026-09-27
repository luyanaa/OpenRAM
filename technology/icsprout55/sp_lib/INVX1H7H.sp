* ICsprout55 H7C hard standard-cell wrappers for OpenRAM.

.SUBCKT INV A VDD VSS Y
*.PININFO A:I Y:O VDD:B VSS:B
MMN0 Y A VSS VSS nm1p2_hvt_lp W=nw L=nl m=1
MMP0 Y A VDD VDD pm1p2_hvt_lp W=pw L=pl m=1
.ENDS

************************************************************************

.SUBCKT INVX1H7H A Y VDD VSS
*.PININFO A:I Y:O VDD:B VSS:B
XXI0 A VDD VSS Y / INV pl=6e-08 pw=2.7e-07 nl=6e-08 nw=2.1e-07
.ENDS

************************************************************************


.SUBCKT TINVX1H7H A Y OE VDD VSS
*.PININFO A:I OE:I Y:O VDD:B VSS:B
MMNM0 net15 A VSS VSS nm1p2_hvt_lp W=210n L=60n m=1
MMN0 Y OE net15 VSS nm1p2_hvt_lp W=210n L=60n m=1
MMPM1 Y OEN net024 VDD pm1p2_hvt_lp W=270n L=60n m=1
MMPM0 net024 A VDD VDD pm1p2_hvt_lp W=270n L=60n m=1
XXI3 OE VDD VSS OEN / INV pl=6e-08 pw=1.9e-07 nl=6e-08 nw=1.5e-07
.ENDS

************************************************************************

