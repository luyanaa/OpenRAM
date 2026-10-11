# OpenRAM for ICsprout 55LLULP (`icsprout55`)

Generates SRAM macros (1RW, 1W+1R, 2RW, 1RW+1R) whose GDS passes the foundry
Calibre DRC and LVS decks — both the SoC tapeout runset and the open
`55LLULP1233_REV1_0_OS` deck.  See [PORT_STATUS.md](PORT_STATUS.md) for the
verification record and the design decisions.

## Quick start

```sh
python3 -m venv venv && venv/bin/pip install -r requirements.txt klayout
export OPENRAM_HOME=$PWD/compiler OPENRAM_TECH=$PWD/technology
venv/bin/python sram_compiler.py technology/icsprout55/configs/ics55_1rw_16x4.py
```

The `klayout` Python package is required: the final GDS is post-processed by
`tech/ics55_finalize.py` (called from `sram.save()` through
`tech.finalize_gds`).

Example configuration:

```python
tech_name = "icsprout55"
bitcell = "pbitcell"          # logic-rule bitcell (no foundry SRAM IP needed)
stdcell_library = "SC9T"      # DFF source, see below
word_size = 8
num_words = 64
num_rw_ports = 1              # or num_w_ports = 1 / num_r_ports = 1, or 2 RW
num_r_ports = 0
num_w_ports = 0
analytical_delay = True       # .lib from OpenRAM's analytical models
check_lvsdrc = False          # sign-off is run with Calibre, see below
```

Outputs: `.gds` (finalized), `.lef`, `.lvs.sp` (LVS netlist), `.sp`, `.v`,
`_TT_1p2V_25C.lib` (analytical, not characterized), `.html` datasheet.

## Standard-cell library option

OpenRAM only takes one cell from a foundry library: the D flip-flop.  It is
placed in `custom/ics55_dff.py`, a 5.2 x 2.7 um slot (1.5 x the 9-track row)
with its own well taps, so the macro floorplan is identical for every option.

| `stdcell_library` | DFF | notes |
|---|---|---|
| `SC9T` (default) | `DFFQX1_9TSVT` | official 9T SVT — Calibre clean |
| `SC9TL`, `SC9TH` | `DFFQX1_9T{LVT,HVT}` | official 9T LVT/HVT |
| `H7CR`, `H7CL`, `H7CH` | `DFFQX1H7{R,L,H}` | open 7T (icsprout55-pdk); the vendor cell itself has ~104 Calibre DRC results (CT_EN_4, CT_S_4, 45-degree M1, ...) |

A macro is self-contained, so a 7T-based SoC can use an `SC9T` macro; the
7T option exists for flows that cannot use the 9T library views.

`scripts/import_stdcells.py` regenerates `gds_lib/` and `sp_lib/` from the
vendor libraries (see its docstring for the source paths).

## What is generated, and how

* Bitcell: OpenRAM's parameterized `pbitcell` with ordinary 1.2 V devices
  (`nm1p2_*_lp`/`pm1p2_*_lp`) under logic rules.  No SRAM marker layers are
  used, so no foundry SRAM device models are involved.  It is large
  (1RW cell 3.05 x 2.6 um).
* Sense amplifier and write driver: `custom/ics55_gen.py` (strip + channel
  generator) with `ics55_sense_amp.py` / `ics55_write_driver.py`.
* Implants: drawn by `tech/ics55_finalize.py` from the final layout
  (NP/PP tiling, tap islands, poly end-cap enclosure), which also fills
  N-well notches, metal holes and same-net notches and fixes metal minimum
  area.

## Physical verification

The decks used for sign-off are the foundry Calibre runsets with
`Density_rules NO` and `CHIPEDGE_CHECK NO`: density (100 um windows) and die
edge are chip-level checks done after the SoC fill.  The tapeout LVS
runset's chip-specific `lvs_box.list` include is disabled.  Everything else
is unmodified.

## Simulation

`sim/` runs the macros in Spectre with the foundry models on all corners
(Verilog-generated access sequences, post-layout via Calibre xRC).  Results,
the recommended clocking and the known problems are in
[SIM_RESULTS.md](SIM_RESULTS.md).  In short: use 1W+1R (or 1RW) with
`words_per_row = 1`, a 50 % duty clock of 4-8 ns, and do not stop the clock
high.

Large macros: `route_supplies = "pins"` skips OpenRAM's supply router (hours
for 128-bit words) and exports every supply pin; the SoC power grid joins
them (an extraction of the stand-alone macro then needs
`VIRTUAL CONNECT NAME vdd gnd`, see `sim/vmpex.sh`).

## Limitations

* Timing in the `.lib` is analytical; the Spectre results in
  SIM_RESULTS.md are the reference until a characterized `.lib` exists.
* The macro uses M1–M4 internally (M4 only for short connectors and the
  supply grid); M5 and above are free for the SoC.
* Read/write margins of the logic-rule bitcell (SNM, Monte Carlo) are not
  simulated, only process corners.
