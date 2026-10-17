* Native body connectivity probe
.SUBCKT body_probe gnd vdd out_n out_p in_n in_p
MN out_n in_n gnd gnd nm1p2_svt_lp W=20u L=10u
MP out_p in_p vdd vdd pm1p2_svt_lp W=20u L=10u
.ENDS body_probe
