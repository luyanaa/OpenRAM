* DFFQX1_9THVT imported from ICsprout55_9THVT_basic.cdl by import_stdcells.py
* Flattened; port order D Q CK VDD VSS; well terminals tied VNW=VDD, VPW=VSS
.SUBCKT DFFQX1_9THVT D Q CK VDD VSS
MNM11 Q net32 VSS VSS nm1p2_hvt_lp W=0.47u L=0.06u M=1
MNM9 net38 net32 VSS VSS nm1p2_hvt_lp W=0.15u L=0.06u M=1
MNM10 net26 ckn net38 VSS nm1p2_hvt_lp W=0.15u L=0.06u M=1
MNM8 net32 net26 VSS VSS nm1p2_hvt_lp W=0.47u L=0.06u M=1
MNM7 net26 ckb net23 VSS nm1p2_hvt_lp W=0.23u L=0.06u M=1
MNM5 net40 net23 VSS VSS nm1p2_hvt_lp W=0.15u L=0.06u M=1
MNM6 net14 ckb net40 VSS nm1p2_hvt_lp W=0.15u L=0.06u M=1
MNM4 net23 net14 VSS VSS nm1p2_hvt_lp W=0.47u L=0.06u M=1
MNM2 net42 D VSS VSS nm1p2_hvt_lp W=0.23u L=0.06u M=1
MNM3 net14 ckn net42 VSS nm1p2_hvt_lp W=0.23u L=0.06u M=1
MNM1 ckb ckn VSS VSS nm1p2_hvt_lp W=0.23u L=0.06u M=1
MNM0 ckn CK VSS VSS nm1p2_hvt_lp W=0.23u L=0.06u M=1
MPM11 Q net32 VDD VDD pm1p2_hvt_lp W=0.59u L=0.06u M=1
MPM10 net26 ckb net39 VDD pm1p2_hvt_lp W=0.15u L=0.06u M=1
MPM9 net39 net32 VDD VDD pm1p2_hvt_lp W=0.15u L=0.06u M=1
MPM8 net32 net26 VDD VDD pm1p2_hvt_lp W=0.59u L=0.06u M=1
MPM7 net26 ckn net23 VDD pm1p2_hvt_lp W=0.3u L=0.06u M=1
MPM6 net14 ckn net41 VDD pm1p2_hvt_lp W=0.15u L=0.06u M=1
MPM5 net41 net23 VDD VDD pm1p2_hvt_lp W=0.15u L=0.06u M=1
MPM4 net23 net14 VDD VDD pm1p2_hvt_lp W=0.59u L=0.06u M=1
MPM3 net14 ckb net43 VDD pm1p2_hvt_lp W=0.3u L=0.06u M=1
MPM2 net43 D VDD VDD pm1p2_hvt_lp W=0.3u L=0.06u M=1
MPM1 ckb ckn VDD VDD pm1p2_hvt_lp W=0.3u L=0.06u M=1
MPM0 ckn CK VDD VDD pm1p2_hvt_lp W=0.3u L=0.06u M=1
.ENDS DFFQX1_9THVT
