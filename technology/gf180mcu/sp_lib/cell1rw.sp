* NGSPICE file created from cell1rw.ext - technology: gf180mcuC
*
* OpenRAM 5-pin view.  The extracted cell exposed separate nwell/pwell bias
* ports; the physical cell ties them to the rails through its taps, so this
* view connects the device bodies to VDD/GND and presents only the five pins
* that OpenRAM's bitcell interface expects.
.subckt cell1rw BL BR WL VDD GND
X0 GND a_63_149# a_18_103# GND nfet_05v0 ad=0.627p pd=3.74u as=0.7275p ps=4.76u w=0.95u l=0.6u
X1 a_18_103# WL BL GND nfet_05v0 ad=0p pd=0u as=0.282p ps=2.14u w=0.6u l=0.77u
X2 a_63_149# a_18_103# VDD VDD pfet_05v0 ad=0.27p pd=2.1u as=0.489p ps=3.38u w=0.6u l=0.6u
X3 a_63_149# WL BR GND nfet_05v0 ad=0.7275p pd=4.76u as=0.282p ps=2.14u w=0.6u l=0.77u
X4 VDD a_63_149# a_18_103# VDD pfet_05v0 ad=0p pd=0u as=0.27p ps=2.1u w=0.6u l=0.6u
X5 a_63_149# a_18_103# GND GND nfet_05v0 ad=0p pd=0u as=0p ps=0u w=0.95u l=0.6u
.ends
