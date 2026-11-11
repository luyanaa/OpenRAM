* gf180mcu 1RW SRAM DFF hard cell.
* Extracted from gf180mcu_fd_sc_mcu7t5v0__dffq_1 (GF018hv5v_mcu_sc7, 5 V devices,
* valid at 3.3 V).  Wells are tied to the rails: VNW -> VDD, VPW -> GND.
.subckt gf180_dff D Q CLK VDD GND
X_tn3 GND CLK ncki GND nfet_05v0 W=4.05e-07 L=6e-07
X_tn4 cki ncki GND GND nfet_05v0 W=4.05e-07 L=6e-07
X_tn5 net5 ncki GND GND nfet_05v0 W=3.6e-07 L=6e-07
X_tn8 net4 D net5 GND nfet_05v0 W=3.6e-07 L=6e-07
X_tn6 net6 cki net4 GND nfet_05v0 W=3.6e-07 L=6e-07
X_tn7 GND net0 net6 GND nfet_05v0 W=3.6e-07 L=6e-07
X_tn0 net0 net4 GND GND nfet_05v0 W=3.6e-07 L=6e-07
X_tn9 net2 cki net0 GND nfet_05v0 W=3.6e-07 L=6e-07
X_tn11 net1 ncki net2 GND nfet_05v0 W=3.6e-07 L=6e-07
X_tn12 GND net3 net1 GND nfet_05v0 W=3.6e-07 L=6e-07
X_tn10 net3 net2 GND GND nfet_05v0 W=4e-07 L=6e-07
X_tn1 Q net3 GND GND nfet_05v0 W=8.2e-07 L=6e-07
X_tp3 VDD CLK ncki VDD pfet_05v0 W=8.65e-07 L=5e-07
X_tp4 cki ncki VDD VDD pfet_05v0 W=8.65e-07 L=5e-07
X_tp12 net7 cki VDD VDD pfet_05v0 W=3.6e-07 L=5e-07
X_tp11 net4 D net7 VDD pfet_05v0 W=3.6e-07 L=5e-07
X_tp6 net8 ncki net4 VDD pfet_05v0 W=3.6e-07 L=5e-07
X_tp5 VDD net0 net8 VDD pfet_05v0 W=3.6e-07 L=5e-07
X_tp0 net0 net4 VDD VDD pfet_05v0 W=3.6e-07 L=5e-07
X_tp7 net2 ncki net0 VDD pfet_05v0 W=3.6e-07 L=5e-07
X_tp9 net1x cki net2 VDD pfet_05v0 W=3.6e-07 L=5e-07
X_tp10 VDD net3 net1x VDD pfet_05v0 W=3.6e-07 L=5e-07
X_tp8 net3 net2 VDD VDD pfet_05v0 W=8e-07 L=5e-07
X_tp1 Q net3 VDD VDD pfet_05v0 W=1.22e-06 L=5e-07
.ends
