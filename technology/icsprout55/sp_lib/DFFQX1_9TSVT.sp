* DFFQX1_9TSVT imported from ICsprout55_9TSVT_basic.cdl by import_stdcells.py
* Flattened; ports D Q CK VDD VSS VNW VPW (wells are separate terminals)
.SUBCKT DFFQX1_9TSVT D Q CK VDD VSS VNW VPW
MNM11 Q net32 VSS VPW nm1p2_svt_lp W=0.47u L=0.06u M=1
MNM9 net38 net32 VSS VPW nm1p2_svt_lp W=0.15u L=0.06u M=1
MNM10 net26 ckn net38 VPW nm1p2_svt_lp W=0.15u L=0.06u M=1
MNM8 net32 net26 VSS VPW nm1p2_svt_lp W=0.47u L=0.06u M=1
MNM7 net26 ckb net23 VPW nm1p2_svt_lp W=0.23u L=0.06u M=1
MNM5 net40 net23 VSS VPW nm1p2_svt_lp W=0.15u L=0.06u M=1
MNM6 net14 ckb net40 VPW nm1p2_svt_lp W=0.15u L=0.06u M=1
MNM4 net23 net14 VSS VPW nm1p2_svt_lp W=0.47u L=0.06u M=1
MNM2 net42 D VSS VPW nm1p2_svt_lp W=0.23u L=0.06u M=1
MNM3 net14 ckn net42 VPW nm1p2_svt_lp W=0.23u L=0.06u M=1
MNM1 ckb ckn VSS VPW nm1p2_svt_lp W=0.23u L=0.06u M=1
MNM0 ckn CK VSS VPW nm1p2_svt_lp W=0.23u L=0.06u M=1
MPM11 Q net32 VDD VNW pm1p2_svt_lp W=0.59u L=0.06u M=1
MPM10 net26 ckb net39 VNW pm1p2_svt_lp W=0.15u L=0.06u M=1
MPM9 net39 net32 VDD VNW pm1p2_svt_lp W=0.15u L=0.06u M=1
MPM8 net32 net26 VDD VNW pm1p2_svt_lp W=0.59u L=0.06u M=1
MPM7 net26 ckn net23 VNW pm1p2_svt_lp W=0.3u L=0.06u M=1
MPM6 net14 ckn net41 VNW pm1p2_svt_lp W=0.15u L=0.06u M=1
MPM5 net41 net23 VDD VNW pm1p2_svt_lp W=0.15u L=0.06u M=1
MPM4 net23 net14 VDD VNW pm1p2_svt_lp W=0.59u L=0.06u M=1
MPM3 net14 ckb net43 VNW pm1p2_svt_lp W=0.3u L=0.06u M=1
MPM2 net43 D VDD VNW pm1p2_svt_lp W=0.3u L=0.06u M=1
MPM1 ckb ckn VDD VNW pm1p2_svt_lp W=0.3u L=0.06u M=1
MPM0 ckn CK VDD VNW pm1p2_svt_lp W=0.3u L=0.06u M=1
.ENDS DFFQX1_9TSVT
