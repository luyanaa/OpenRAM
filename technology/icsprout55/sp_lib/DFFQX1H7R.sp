* ICsprout55 H7C hard DFF extracted for OpenRAM.
* Port order is D Q CK VDD VSS for OpenRAM's dff module.

.SUBCKT INV A VDD VSS Y
*.PININFO A:I Y:O VDD:B VSS:B
MMN0 Y A VSS VSS nm1p2_svt_lp W=nw L=nl m=1
MMP0 Y A VDD VDD pm1p2_svt_lp W=pw L=pl m=1
.ENDS

************************************************************************

.SUBCKT DFFQX1H7R D Q CK VDD VSS
*.PININFO CK:I D:I Q:O VDD:B VSS:B
XXI14 net46 net24 net32 VDD VSS net33 / TSINV pl=6E-08 pw=1.5E-07 nl=6E-08
+ nw=1.5E-07
XXI6 D net32 net24 VDD VSS net33 / TSINV pl=6E-08 pw=2.8E-07 nl=6E-08 nw=2E-07
XXI9 net46 net24 net32 VDD VSS net25 / TSINV pl=6E-08 pw=3.2E-07 nl=6E-08
+ nw=2.3E-07
XXI15 net9 net32 net24 VDD VSS net25 / TSINV pl=6E-08 pw=1.5E-07 nl=6E-08
+ nw=1.5E-07
XXI7 net33 VDD VSS net46 / INV pl=6E-08 pw=2.8E-07 nl=6E-08 nw=2E-07
XXI13 net32 VDD VSS net24 / INV pl=6E-08 pw=2.8E-07 nl=6E-08 nw=2E-07
XXI12 net25 VDD VSS Q / INV pl=6E-08 pw=3.4E-07 nl=6E-08 nw=2.4E-07
XXI10 net25 VDD VSS net9 / INV pl=6E-08 pw=2.8E-07 nl=6E-08 nw=2E-07
XXI4 CK VDD VSS net32 / INV pl=6E-08 pw=2.8E-07 nl=6E-08 nw=2E-07
.ENDS

************************************************************************

.SUBCKT TSINV A CK CKN VDD VSS Y
*.PININFO A:I CK:I CKN:I VDD:B VSS:B Y:B
MMN0 Y CK net18 VSS nm1p2_svt_lp W=nw L=nl m=1
MMN1 net18 A VSS VSS nm1p2_svt_lp W=nw L=nl m=1
MMP1 net024 A VDD VDD pm1p2_svt_lp W=pw L=pl m=1
MMP0 Y CKN net024 VDD pm1p2_svt_lp W=pw L=pl m=1
.ENDS

************************************************************************

