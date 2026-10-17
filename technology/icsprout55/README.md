# OpenRAM for ICsprout 55LLULP (`icsprout55`)

Generates SRAM macros (1RW, 1W+1R, 2RW, 1RW+1R) with optional foundry
Calibre sign-off checks and provisional open-source KLayout DRC/LVS screening.
The open-source decks are not sign-off decks and report violations on a
sample macro; see [verify/README.md](verify/README.md) for scope and caveats.

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

Flow packaging keeps that selection explicit: `scripts/package_macro.py`
requires `--stdcell-library LIB --stdcell-cdl CDL`, selects the mapped DFF
from the table above (aliases `9T`, `SC9TSVT`, and `7T` are accepted), and
rejects a missing subcircuit or a CDL terminal order other than
`CK D Q VDD VSS`. The behavioral model's `// Port N: RW|R|W` comments define
the package stub and Liberty roles; `--ports`, when supplied, only asserts the
port indices found there. See [sim/README.md](sim/README.md#measured-multi-port-liberty)
for native `characterize.py` and `mklib.py` measurement/replay contracts.
Packaging requires `--diagnostic-table-only`; its `dont_use` tables are not
complete STA Liberty or a Calibre, Genus, or Innovus result.

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

Measured array/periphery footprint budgets and comparable SRAM22/commercial
area evidence are in [AREA_ANALYSIS.md](AREA_ANALYSIS.md).

## Electrical experiments and port contract

The default column mux remains the NMOS pass-gate implementation, with its
size selectable through `ics55_column_mux_nmos_size`. The checked-in
`ics55_1rw_256x8_mux4.py` explicitly uses size 8. The optional full-CMOS
transmission-gate implementation is selected with
`ics55_column_mux_style = "tgate"`; the
`ics55_1rw_256x8_mux4_tgate.py` experiment uses N-size 8 and P-size 12.
It adds a local select inverter and a VDD rail; it does not change the default.
The TG leaf keeps its inverter-NMOS supply drop below the bitline jogs and
orients the BR input diffusion toward its bitline rail. Its array promotes VDD
through an unused M2 window to a raised M3 rail, without an extra M1 landing.
Pinned KLayout 0.30.7 full-hierarchy macro-profile DRC has zero
raw/waived/unwaived markers for the repaired leaf, bare pair and eight-column
array; each also independently matches its generated source in LVS with exact
model/W/L pairs. The final repaired 256x8 TG macro has full-hierarchy DRC
0/0/0, but its strict native LVS search timed out after 3600.26 s while writing
the pre-compare extracted netlist; no verdict was produced. This is
inconclusive, not an LVS pass or mismatch. These are not foundry-signoff
results; see [the repair evidence](SIM_RESULTS.md#tg-lvs-独立根因write-driver-电源提升点触碰边缘-bitline).

`ics55_write_driver.py` is the selected write-driver implementation. It drives
`bl = din` and `br = !din` with complementary NMOS/PMOS paths while enabled,
and isolates both bitlines while disabled. The current cell uses fixed 0.60 um
output devices and has no strength-sweep API. These are circuit/layout
intentions, not an SS -40 C timing claim; verify the generated macro with the
vendor models and the intended load.
Break-before-make is provided by the macro timing contract, not an independent
dead-time circuit in the driver: the top-level data DFF captures on the rising
edge, and `control_logic` gates `w_en` with `gated_clk_bar` and replica delay so
the driver is disabled while data changes and active only in the low-phase
write window. External use of the leaf driver must hold `din` stable for the
entire `en` interval.

Decoder and replica timing reuse OpenRAM's `control_logic`, word-size-based
`pand3` drive, the `pdriver_max_size` ceiling and `static_precharge` option.
The bounded sizing path uses existing `delay_chain_stages`/`delay_chain_fanout`
and explicit `write_driver_strength`:1 keeps the original geometry; .75
narrows only eight output MOS per ICsprout55 driver from.60 to.45um, preserving
four local inversion MOS, pins, footprint and full-rail topology. Unsupported
strengths/technologies reject; factory caching and write-mask measurement
geometry distinguish the strength. Sizing admission still requires each exact
tuple's own GDS, RCC netlist and equivalent topology/probe map.

When two write ports have overlapping write-enable windows to the same
physical row (which can include distinct logical addresses in a column-muxed
SRAM), stored data is undefined and neither port has priority. Behavioral
Verilog reports this case under `OPENRAM_ASSERTIONS`; packaged black-box
stubs document it. The guard does not arbitrate writes.

Transistor simulation uses model files from the separately supplied
`icsprout55-pdk` (override with `ICSPROUT55_PDK_ROOT` or `SPICE_MODEL_DIR`).
The foundry model files are vendor-licensed and are not redistributed by
OpenRAM. Default compiled Liberty remains analytical. Native two-load
characterization can produce measured diagnostic tables at the exact recorded
PVT/load/slew/period, but `mklib.py` requires `--diagnostic-table-only` and marks
them `dont_use`. Macro-boundary setup/hold/pulse/Tmin and measured input/clock
loading are missing; no complete-STA export is available.

Strict macro LVS records the validated PDK capability and frozen LVS-deck hash
manifest before KLayout starts. Nix discovery resolves the installed KLayout
executable and runs it directly, so runtime evidence binds the actual tool,
not the Nix launcher. Strict flat mode selects physical-flat extraction as well
as a flat reference. The native report must contain a genuinely matched root,
complete object/incidence correspondence and the exact original source ports;
log text or JSON assertions alone cannot pass. Missing/drifted capability,
skipped root or incomplete proof stays unknown. Generic cell LVS remains
separate. Local LVS repair uses KLayout and the supplied rule sources, without
Calibre or a new PEX/CAPTAB flow; the existing StarRC-corrected PEX is preserved.
This is not Calibre equivalence or foundry signoff.

## Capability-gated architecture timing search

The registry admits exact 32x8, one-bank, mux-1 SC9T 2RW tuples
`(delay_chain_stages, delay_chain_fanout, write_driver_strength)`:
`(9,4,1)`, `(9,4,.75)` and `(9,3,1)`. The original tuple retains its frozen
GDS and observation bindings; both new variants have their own generated
GDS/config/reference, RCC netlist and equivalent topology/probe map.
Individually legal values do not imply a supported combination: `(9,3,.75)`
is rejected. TG, other cell/port families and unvalidated variants never fall
back to the baseline. Only `(9,4,.75)` has the explicit F4 diagnostic model;
registration authorizes measurement attempts, not physical/STA acceptance.

Run from the repository root with the local Python environment:

```sh
python -m compiler.architecture enumerate \
    technology/icsprout55/architecture_capabilities.json \
    technology/icsprout55/configs/ics55_2rw_control_timing_search.json
python -m compiler.architecture search \
    technology/icsprout55/architecture_capabilities.json \
    technology/icsprout55/configs/ics55_2rw_control_timing_search.json \
    --out-dir ics55_work/runs/architecture_timing_search
```

The sizing request screens the three registered tuples at one operating point:

```sh
python -m compiler.architecture search \
    technology/icsprout55/architecture_capabilities.json \
    technology/icsprout55/configs/ics55_2rw_sizing_search.json \
    --out-dir ics55_work/runs/architecture_sizing_replay
python technology/icsprout55/scripts/sizing_variants.py \
    --out-dir ics55_work/runs/new_sizing --generate
```

Generation is serial and refuses conflicting output directories; it does not
auto-admit new outputs or claim DRC/LVS/extraction success. See
[verify/README.md](verify/README.md) for the per-variant evidence contract.

Search defaults to replaying hash-bound local observations. Missing licensed
PDK files or work-directory evidence fail admission; these inputs are not
downloaded. Add `--execute` to allow serial local ngspice execution for an
admitted operating point without a matching observation. The path invokes
neither SSH nor Calibre. It preserves the extracted circuit and records the
actual deck, initialization plan, tool/model identities and output hashes.
Replay additionally requires a manifest of the complete recursive model include
closure captured before the original simulator launch and bound to its exact
inputs/outputs. Old observations containing only a top-library hash remain
historical diagnostics; computing current dependency hashes cannot upgrade them.


The example varies period at SNFP/125 C, 1.08 V, 5 fF and 30 ps slew.
SS/-40 C is a separate comparison domain, as are different load, slew, skew,
initialization policy, access contract and fidelity. Explicit margin thresholds
in the request are research screening criteria, not characterized limits.
Functional failure, absent measurements and inadequate control margins cannot
enter a feasible frontier. The report separates timing screening from physical
feasibility; estimated PEX is never accepted physical/signoff evidence.
The generic engine can stage a request across multiple admitted fidelities,
filtering failures and dominated points before higher-cost evaluation; missing
intermediate evidence prevents promotion, including cached higher-stage reuse.
An explicit single-F3 study does not assert prior F0/F1/F2 passes. Native F4
diagnostics additionally validate exact-load characterization and physical
evidence, but always leave macro characterization closure unknown. The current
new width075/fanout3 layouts have verified full-hierarchy macro DRC and complete
strict physical-flat native LVS, including their actual root comparison and
all50 declared ports. Existing extraction acceptance metadata and incomplete
macro characterization still prevent physical/full-STA closure. The existing
StarRC-corrected PEX is preserved, not recalibrated as an LVS prerequisite.


Actual width075 runs at5/50fF each pass96/96 bit checks and supply isolated
read/write energies; the falling-clock read tables cover only output falls.
Missing rise tables stay unknown and are omitted from the diagnostic Liberty,
not inferred from correct logical0/1 reads. Exact values and evidence are in
[SIM_RESULTS.md](SIM_RESULTS.md#width75-双负载原生-f4-诊断表征).

The original concurrent-port trace has no isolated supply-current windows and
remains the comparable F3 timing workload. F4 uses a separate26-cycle held-input
trace with isolated read/write and clock-active-idle windows. Signed energy is
whole-macro energy including clocks/load charging, not intrinsic pin power or
static leakage. Missing macro setup/hold/pulse/Tmin, input/clock loading and
accepted physical evidence prevent complete STA/full-PPA closure; later memory
families remain unsupported.
See [the execution plan](../../MEMORY_ARCHITECTURE_OPTIMIZER_PLAN.md) for the
remaining capability and characterization gates.

The first model-closure-complete local example is SNFP/125 C at7.5ns:
48/48 bit checks and the four research control-window constraints pass.
It is a measured screening point, not a minimum-period or sizing result.
The separate5ns pass lacks launch-time model closure and remains diagnostic.
Metrics, final report paths and physical limitations are in [SIM_RESULTS.md](SIM_RESULTS.md).



## Physical verification

The decks used for sign-off are the foundry Calibre runsets with
`Density_rules NO` and `CHIPEDGE_CHECK NO`: density (100 um windows) and die
edge are chip-level checks done after the SoC fill.  The tapeout LVS
runset's chip-specific `lvs_box.list` include is disabled.  Everything else
is unmodified.

Known vendor-cell internal DRC findings are tracked as scoped waivers, not
repaired by altering library geometry. The optional KLayout adapter preserves
raw reports and counts waived and unwaived markers separately; LVS remains an
independent verdict. See [verify/README.md](verify/README.md).

## Simulation

`sim/` runs the macros in Spectre with the foundry models on all corners
(Verilog-generated access sequences, post-layout via Calibre xRC).  Results,
the recommended clocking and the known problems are in
[SIM_RESULTS.md](SIM_RESULTS.md).  In short: use 1W+1R (or 1RW) with
`words_per_row = 1` and a 50 % duty clock of 8 ns or slower (no lower
limit: precharge is static, `static_precharge` in tech.py).  A small 1RW
32x32 is `configs/ics55_1rw_32x32.py` (0.027 mm²).

Large macros: `route_supplies = "pins"` skips OpenRAM's supply router (hours
for 128-bit words) and exports every supply pin; the SoC power grid joins
them (an extraction of the stand-alone macro then needs
`VIRTUAL CONNECT NAME vdd gnd`, see `sim/vmpex.sh`).

## Limitations

* Timing in the `.lib` is analytical; the Spectre results in
  SIM_RESULTS.md are the reference until a characterized `.lib` exists.
* Internal routing spans M1–M5. The supply grid uses M3/M4; data-DFF channels
  use M4/V4/M5 with horizontal trunks on M5. The macro LEF reserves M5 with an
  obstruction, so the SoC must not treat M5 as available across the macro;
  route above it only if the SoC stack permits.
* Read/write margins of the logic-rule bitcell (SNM, Monte Carlo) are not
  simulated, only process corners.
