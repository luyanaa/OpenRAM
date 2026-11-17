# Memory-family TODO (2026-11-16)

This is the deferred/new-family list.  The four immediate repairs are tracked
separately at the bottom and are being done in the order requested:
write_mask -> native 2R1W -> same-row conflict -> icsprout55 ROM.

## 1. 8T no-read-disturb and 10T sub/near-threshold cells

Status: **TODO / new cell family** (not a parameter change to `pbitcell`).

- 8T: 6T storage + 2T decoupled single-ended read buffer.  The 1W+1R branch of
  `compiler/modules/pbitcell.py` already contains a decoupled read-port
  prototype, but it is not a standard 8T cell: define the read-device sizing,
  RBL precharge/sense, and the read-port pin/array tiling.
- 10T: fully differential low-voltage cell (6T storage + 4T read/write-assist
  or differential read).  Target Vmin below the 6T logic-rule cell and a
  measurable RSNM improvement.
- Required evidence: cell SPICE (RSNM/write margin/read current), a standalone
  DRC/LVS-clean GDS, a small array with peripheral integration, and a
  characterized Liberty/PEX path.  Do not claim a macro from a cell netlist.
- Dependencies: the P0 characterizer and PEX path must be trustworthy first;
  otherwise the cell margin numbers are not comparable to the existing 6T.

## 2. TCAM / BCAM (OpenTCAM research)

Status: **TODO / independent memory family**.

Reference: https://github.com/merledu/OpenTCAM (Apache-2.0).  OpenTCAM is a
Python framework that generates RTL and GDS-II for customizable SRAM-based
TCAM.  It currently builds TCAM blocks around OpenRAM-generated 1 kb sky130
SRAM macros and FPGA BRAM; the flow is table-map -> RTL wrapper -> memory
blocks (`make tcamtablemap`, `make tcamrtl`), with `compiler/src/
mainTableMapping.py`, `mainTcamRTLGenerator.py`, `tableMapping.py`,
`tcamRtlGenerator.py` and `lib/tcam_block_rtl/`.

Integration plan (do **not** call an SRAM wrapper a native TCAM):

1. **Emulated/block-level TCAM first**: OpenRAM 1RW/1R1W macro + per-bit mask
   storage + standard-cell compare + matchline/priority encoder.  Name it
   `emulated_cam` (or `sram_tcam`), publish the multi-cycle search latency and
   energy, and keep the TCAM table-map format separate from the SRAM config.
2. **Native TCAM later**: 10T/16T compare cell, matchline precharge/sense,
   per-bit mask storage, priority encoder, multi-match policy.  Requires a new
   bitcell and array family; 5-metal processes (gf180mcu/icsprout55/sky130) are
   the right targets.  This is not a `pbitcell` variant.
3. OpenTCAM's table mapping/RTL generator can be reused as the front end; the
   OpenRAM side needs a macro interface and a PDK-specific wrapper, not a fork
   of the SRAM bitcell.

## 3. MBIST + ECC (autoMBIST research)

Status: **TODO / DFT + reliability family**.

Reference: https://github.com/ranaumarnadeem/autoMBIST (Apache-2.0).
autoMBIST generates synthesizable MBIST controllers, a march-test FSM, and
optional 2D row/column BIRA/BISR around OpenRAM macros; it has been hardened
through LibreLane 3.0.5 on sky130, supports 1RW/1R1W/2RW, March C-/RAW/X/
MATS+/checkerboard (plus the research algorithms), JTAG/IJTAG via warptap, and
cocotb+Icarus simulation.

Integration plan:

1. Map a generated gf180mcu/icsprout55 macro (pins, clocks, power) into the
   autoMBIST config; start with 1RW 16x8/32x8, then the multiport macros.
2. Add SEC-DED Hamming columns through the existing `num_spare_cols`
   mechanism: ECC encode/decode and syndrome logic stay outside the macro; the
   spare columns are the parity/check-bit storage.  Define whether the ECC
   code covers the spare columns themselves and how a corrected/uncorrectable
   error is reported.
3. MBIST must test both the data array and the ECC logic: march tests for the
   array, a known-answer/error-injection test for the encoder/decoder, and a
   spare-column test.  autoMBIST's BIRA/BISR can remap row/column spares, but
   its repair policy must not silently discard an ECC check column.
4. Required evidence: RTL simulation (cocotb/Icarus), synthesis/LibreLane
   hardening on the target PDK, and a documented DFT interface (start/done/
   pass/fail/error syndrome).  Do not claim MBIST from an RTL-only smoke.

## 4. Assist-enabled SRAM (WL boost / NBL)

Status: **TODO / new peripheral + voltage-domain family**.

- gf180mcuD has 3.3 V and 5 V/6 V thick-oxide devices; icsprout55 is 1.2 V
  logic.  Candidate schemes: wordline overdrive (WL boost) for write margin,
  or negative-bitline (NBL) write assist.
- Required: charge pump / level shifter / timing controller, separate voltage
  domain and decoupling, write-margin characterization at SS/-40 C and SNFP,
  and a read-path/area/power comparison against the unassisted macro.
- This is not a `supply_voltages` list change: the cell/periphery must tolerate
  the boosted/negative node and the assist must be disabled safely at nominal.

## Immediate repair queue (in order)

1. **write_mask**: fix `compiler/modules/port_data.py`
   `route_write_mask_and_array_to_write_driver`; target 16x8 `write_size=4`
   DRC 0 / LVS unique.  Evidence so far: 8 M2.2a + LVS open on
   `bank_wmask0_1`/`wdriver_sel_*`.
2. **native 2R1W**: add the third physical port side/channel in
   `bank.py`/`port_data.py`; start with icsprout55 1W+2R 32x8, then
   `register_file.py` 3-port acceptance.
3. **same-row conflict**: define `write_conflict`/stall contract or per-port
   column mux; verify the A5/3C same-physical-row cases from `SIM_RESULTS.md`.
4. **icsprout55 ROM**: add a ROM config and run `rom_compiler.py` to
   DRC 0 / LVS unique; gf180mcu 1 kb ROM already has that evidence.

## Progress 2026-11-17

- **write_mask**: parked; route-level fixes exhausted (8 -> 14 -> 4 -> 20 DRC
  variants, all reverted).  Needs leaf/placement change.  Evidence in
  `gf180_work/STATUS.md`.
- **native 2R1W**: full 1W+2R 32x8 macro generated (GDS/LEF/SP/Verilog) at
  `ics55_work/2r1w_native/`; `NATIVE_LAYOUT_MAX_PORTS=3`; third port placed on
  the bottom data row / right address column; `addr2[0]` escape fixed.
  Top-interconnect DRC reduced 63 -> 29 (ics55 min-area top pins/via pads;
  m3.area 37 -> 3); remaining 22 are port-2 m2 bitline/via slivers.  Full DRC
  launched in background; generic LVS mismatch (ERC pWell 1267), strict deck
  hash drift.  A flat LVS with `no_well_ties=true/no_erc=true` still fails:
  the extracted net `dout2[5]|dout2[6]|dout2[7]|gnd|vdd` proves a real
  port-2 read-data/power-ring short; fix that before pWell taps.  See
  `ics55_work/1w2r_32x8/README.md`.
- **same-row**: hardware priority gate in `compiler/base/verilog.py`
  (lowest-index write wins; `write_conflict` asserted; loser dropped).
  Icarus test passes (`ics55_work/1w2r_32x8/same_row/run.sh`).  Physical
  `write_conflict` pin/gates still to add.
- **icsprout55 ROM**: 128 B x 8 GDS generated; `rom.py` now calls
  `tech.finalize_gds` (DRC 6939 -> 3893); remaining findings are the generic
  ROM cell/array pitch vs icsprout55 rules.  See `ics55_work/rom_128b/README.md`.
- **klayout**: Python module installed in `compiler/.venv` (0.30.13); ics55
  finalize works, so the 1W+2R and ROM GDS writes complete.

## Progress 2026-11-17 (second pass)

- **2R1W metal markers**: root-caused.  The `top-interconnect` screening view
  keeps only direct top-cell shapes, which tears via pads away from the
  child-cell rails they merge with; the real GDS has zero merged M2/M3
  polygons below the area rule.  `ics55_finalize.py` now re-owns the exact
  physical polygon of each isolated pad at the top cell (`direct_pads_fixed`
  = 44, identity geometry, no net change).  Top-interconnect DRC 29 -> 6
  (4 chip-edge + 2 residual reduced-view M2 width markers).  Evidence in
  `ics55_work/1w2r_32x8/README.md`.
- **same-row physical**: 2R1W macro now has a physical `write_conflict`
  output (LEF MET3 pin at 1.5/99.875, GDS label on the bank gnd pin, Verilog
  `assign write_conflict = 1'b0`).  RTL priority gate test still passes.
  Remaining: a 2RW `words_per_row>1` row-address comparator + `w_en` priority
  gates in the layout.
- **ROM metal**: the finalize direct-pad pass also removed all top-level
  M2/M3/M4 area/width markers (top-interconnect 280 -> 129; remaining are the
  ROM bitcell's 0.128 um M1 strap and one chip-edge marker).  An ics55-specific
  `rom_base_cell`/array pitch is still needed for the cell-level FEOL/poly
  findings.
- **LVS**: `write_conflict` is physically gnd, so the reference netlist must
  model that tie for any LVS run; the open KLayout deck still cannot serve as
  a verdict (1W+1R control: 24 layout nets vs 2953 reference nets).  Calibre on
  the RHEL EDA VM or a strict-capability/deck-hash match remains required.
