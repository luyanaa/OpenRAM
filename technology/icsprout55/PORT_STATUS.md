# ICsprout55 OpenRAM port — status and log

Target: OpenRAM macros (1RW single-port; 1W+1R / 2RW dual-port) for
ICsprout 55LLULP that pass **both** foundry Calibre decks:

* **tapeout deck** — the SoC sign-off runsets (`drc.runset` V1.21 /
  `lvs.runset`, as used in `Baixin_SoC_ICS55/.../full_inward_ring_staged`);
* **OS deck** — `icsprout55-pdk/pv/{DRC,LVS}/ICsprout_Cal*_55LLULP1233_REV1_0_OS.*`.

Historical Calibre runs gave matching results between both decks for the
configs listed below. Later source/layout variants require new Calibre runs
before the target sign-off claim applies.

## How checks are run

* `ics55_work/` (git-ignored, under the VMware shared folder) holds the
  harness.  `ics55_work/venv` is a local Python venv (OpenRAM requirements +
  `klayout` Python module).  Nothing relies on locally installed EDA tools.
* Calibre 2020.2 runs on the RHEL8 EDA VM (`user@172.16.142.129`,
  `MGLS_LICENSE_FILE=28000@127.0.0.1`) through `ics55_work/bin/cal.py`, which
  copies the two decks with only these edits: layout/source path and primary
  cell; `Density_rules NO`; `CHIPEDGE_CHECK NO`; tapeout LVS `lvs_box.list`
  include commented out (chip-specific IO boxes).
  *Density and chip-edge are chip-level checks* (100 µm windows, die border):
  they are evaluated after the SoC fill flow, not on a stand-alone macro.
* `ics55_work/bin/leaf.py 'module(kwargs)' ...` generates OpenRAM modules,
  finalizes them (see below), and runs DRC + LVS on both decks.
  `--margin` (default 0.4 µm) lets the derived implant extend past a
  stand-alone leaf, as its neighbours would in a real array.

## Historical Calibre results (earlier configs)

| config (`configs/`) | size (um) | DRC tapeout | DRC OS | LVS tapeout | LVS OS |
|---|---|---|---|---|---|
| ics55_1rw_16x4 (SC9T) | 72.3 x 81.6 | 0 | 0 | CORRECT | CORRECT |
| ics55_1rw_64x8 (SC9T) | | 0 | 0 | CORRECT | CORRECT |
| ics55_1rw_256x8_mux4 (SC9T, 4 words/row) | | 0 | 0 | CORRECT | CORRECT |
| ics55_1w1r_32x8 (SC9T, separate R/W clocks) | | 0 | 0 | CORRECT | CORRECT |
| ics55_2rw_32x8 (SC9T) | | 0 | 0 | CORRECT | CORRECT |
| ics55_1rw_16x4_h7cr (7T DFF) | | 138 | 138 | CORRECT | CORRECT |
| ics55_1rw_16x32 (SC9T, static precharge) | 212.95 x 83.06 | 0 | 0 | CORRECT | CORRECT |
| ics55_1rw_32x32 (SC9T, static precharge) | 213.4 x 124.6 | 0 | 0 | CORRECT | CORRECT |
| ics55_1rw_64x32 (SC9T, static precharge) | 214.3 x 208.6 | 0 | 0 | CORRECT | CORRECT |
| ics55_1w1r_32x128 (SC9T, `route_supplies="pins"`) | 868.7 x 146.3 | 0 | 0 | CORRECT | CORRECT |
| 1W+1R 64x128 (same, num_words = 64) | 867.6 x 238.3 | 0 | 0 | CORRECT | CORRECT |

All 138 results of the 7T macro are inside the 17 vendor `DFFQX1H7R`
instances (the vendor cell alone has the same categories); the generated
layout around them is clean.

## Earlier routefix5 physical candidate

The SC9T `pbitcell` 2RW32×8 (`words_per_row = 1`) routefix5 candidate measures
155.010 × 185.280 µm, 0.58 µm shorter and 89.9058 µm² (0.3121%) smaller than
routefix2. See `AREA_ANALYSIS.md` for the measurement basis and unchanged
replica/cap/tap boundaries.

Full-hierarchy KLayout macro-profile DRC reports 0 raw, waived, or unwaived
markers (`ics55_work/runs/recheck_routefix5_2rw/drc/`). KLayout LVS still
reports a mismatch (`ics55_work/runs/recheck_routefix5_2rw/lvs/`); the
extracted SPICE has 5,854 MOS records, unchanged from routefix2, but this is
not LVS equivalence. A separate routefix5 1RW16×4 build also has 0 KLayout
macro-profile DRC markers and an LVS mismatch. The historical Calibre results
above apply to their original variants, not these routefix5 builds. See
`verify/README.md` for the KLayout/Calibre divergence and current Calibre
runner limitation.

Icarus behavioral smoke passed for both-port writes, cross-port reads, and
concurrent writes to distinct rows. OpenRAM analytical TT timing is unchanged
from routefix2 at loads 0.225, 0.9, and 3.6: 0.242185 ns, 0.244294 ns, and
0.252732 ns, respectively. The model assigns the first read-port timing to all
ports; these are not extracted or post-route timing results.

## LVS extraction follow-up: M4 reroute

Extraction-only comparison of the earlier `routefix5_2rw.gds` against its
original `routefix5_2rw.lvs.sp` gives 5,854 MOS / 2,488 nets versus 5,884 MOS /
2,522 nets, with 50 top pins on both sides. The exact MOS-graph comparison
fails. Raw extracted MOS class/width/length histograms match the final M4
candidate, while the earlier layout has 34 fewer flattened nets. [INFERENCE]
This indicates a connectivity difference; the exact physical short location
remains unisolated.

The final M4 reroute candidate
(`ics55_work/lvs_alignment/m4_stable/runs/out_routefix5_2rw_reroute/routefix5_2rw_reroute.gds`)
passes exact MOS-graph isomorphism against the **original** routefix5 reference:
5,884 MOS, 2,522 nets, 50 pins, 23,536 terminal edges. W/L compare exactly
within 1e-12; G/B are fixed and S/D are interchangeable. The proof mapping is
in `ics55_work/lvs_alignment/m4_stable_extract/original_reference_graph.json`.

The final M4 reroute passes the production OpenRAM/KLayout macro LVS path
(`ics55_work/lvs_alignment/openram_m4_verify_pdk/lvs/lvs.log`) using physical
body taps and PORT DEPTH 0. The flattened production extraction independently
passes exact MOS-graph isomorphism against the original SPICE reference after
resolving KLayout's numeric net IDs through its emitted `* net` labels: 5,884
MOS, 2,522 nets, 50 pins, 23,536 terminal edges. W/L match within 1e-12;
G/B are fixed and S/D are interchangeable. The earlier strict-comparer
diagnostic with branch complexity 50 exhausted its search (reported at least
992 required); the production deck uses 1024. The final M4 candidate still has
no valid DRC result, and no current Calibre or Assura signoff is available.

`write_driver_array.route_supplies` now reads the supply layer through
`get_pins()` so multi-shape supply pins are supported. The standard ICsprout55
1RW array passes strict local LVS at 27 pins; spare-column and write-mask
variants still have separate mismatches and are not accepted as clean.

## Current sizing strict-LVS repair

The exact `(9,4,.75)` and `(9,3,1)` 2RW32x8 sizing variants now pass actual
KLayout0.30.7 strict physical-flat LVS against their own unchanged references.
The former invocation flattened only the reference; explicitly selecting
physical `run_mode=flat` removes that hierarchy mismatch without altering the
released/private deck or its strict rules.

| Variant | Native root | Matched MOS | Active nets | Declared ports | Terminal incidences | Fresh full-hierarchy DRC |
|---|---|---:|---:|---:|---:|---|
| `ics55_2rw_32x8_d9_f4_w075` | Match | 5884 | 2522 | 50 | 23536 | 0 raw/waived/unwaived |
| `ics55_2rw_32x8_d9_f3_w100` | Match | 5848 | 2504 | 50 | 23392 | 0 raw/waived/unwaived |

Every electrically incident net, device, pin and edge has a complete native
correspondence. Zero-incidence bookkeeping nets are disclosed, not electrically
joined or purged. Native net ambiguity warnings are retained and accepted only
with the full selected mapping/incidence proof. W/L, named ports, fixed G/B
and allowed S/D symmetry are unchanged; W/body/gate/port negatives still fail.
The production physical consumer independently accepts DRC/LVS for both.

Evidence is under `ics55_work/runs/klayout_lvs_repair/`, especially
`native_macro_results.json` and `physical_validation.json`; original failed
reports are retained. This closes local strict LVS for these two exact variants,
not all TG/spare/mask configurations or foundry signoff. A separate hierarchical
smoke remains inconclusive on an unnamed `mos_leaf` boundary pin; no port was
renamed or relaxed to force a pass. No Calibre execution/license was used.
The user's best-available StarRC-corrected PEX, GDS, netlists and waves remain
unchanged; no calibration, native CAPTAB or new PEX flow was required.


## Key design decisions

1. **Bitcell**: no foundry SRAM bitcell/device kit is delivered (the LVS deck
   knows `STNPD*/DPNP*` SRAM devices, but only under the `SRAMALL` marker with
   matching IP).  The port uses OpenRAM's parameterized `pbitcell` built from
   ordinary 1.2 V logic devices (`nm1p2_*_lp`/`pm1p2_*_lp`) under normal
   logic rules — large, but legal.  No SRAM marker layers are ever drawn.
2. **Implants** (`tech/ics55_finalize.py`): ICS55 requires NP/PP to tile the
   layout (butted, never overlapping), cover all contacted poly, and keep fixed
   distances from gates.  The generators' NP/PP shapes are discarded and the
   implants are re-derived flat: active crossed by poly is a transistor typed
   by its well; active without poly is a well tap (opposite type); PP = N-well,
   NP = elsewhere, plus tap islands (tap + 0.08 µm); slivers are absorbed;
   foundry stdcells keep their own implants.  OpenRAM keeps tap actives
   `tap_to_active` (0.22 µm) from transistors so the islands are legal.
3. **Standard cells**: OpenRAM only needs a DFF from the foundry library.
   `stdcell_library` = `SC9T` (default, official 9T SVT), `SC9TL`, `SC9TH`,
   `H7CR`, `H7CL`, `H7CH`.  The DFF is wrapped (`ics55_dff`) into one fixed
   9-track-sized slot with its own well taps (both libraries are *tapless*),
   so the macro floorplan is the same for 7T and 9T.
4. **Rules** (`tech/tech.py`) come from the DRM/Calibre decks, rounded up;
   area is not optimized (e.g. contacts at 0.14 µm spacing, 0.15 µm minimum
   device width to avoid dog-bone actives).

## Findings worth knowing

* The tapeout deck maps datatype 2 of metal layers to **metal dummy**
  (`81;2` = `MET1dmi`).  The 7T vendor GDS draws its M1 pins on `81;2`, so a
  7T cell placed unchanged produces `M1DUM_*` violations.  `import_stdcells.py`
  removes that purpose (pins are also on `81;1`).
* **The vendor 7T DFF (`DFFQX1H7R`) is not clean under either Calibre deck**
  even after that: 183 results (CT_EN_4 M1-over-contact enclosure, CT_S_4,
  NP/PP_EN_3, 45° M1 MET1_S_4/W_2, ...).  The 9T `DFFQX1_9TSVT` only shows the
  expected tapless-cell latch-up/floating-well flags. These 7T internal
  findings are accepted vendor exceptions; the raw macro DRC count is not
  zero. Keep generated wrapper/connection checks separate, and see
  `verify/README.md` for the KLayout marker waiver policy.
* Both 7T and 9T cells are tapless; standalone LVS of a library DFF fails only
  because its wells are untied.

## Progress log

### 2026-10-10
* Baseline (previous fork, 64x8 1RW): 7,872 non-density DRC results in 62
  rules, LVS INCORRECT, identical on both decks.
* Rewrote `tech.py` with real rule values; added `ics55_finalize.py`,
  `scripts/import_stdcells.py` (DFF for 3 VTs x 7T/9T), Calibre harness.
* OpenRAM core changes (generic, default behaviour unchanged elsewhere):
  `pgate.tap_space()` honours optional `tap_to_active`; pnand2/3/4 input
  contact clears the taller of the bottom NMOS S/D contacts.
* Leaf results (both decks, DRC 0 and LVS CORRECT): pinv (1x,4x), pnand2,
  pnand3, pnand4, pnor2, pand2, pand3, pbuf, pdriver, pinv_dec, and2_dec,
  and3_dec, wordline_driver, hierarchical_predecode2x4/3x8, precharge,
  column_mux, and pbitcell/replica_pbitcell/dummy_pbitcell for 1RW, 2RW,
  1W+1R and 1RW+1R.
* More generic core changes: precharge N-tap honours optional
  `tap_to_active`/`tap_to_poly` (and measures the real tap active).
* `ics55_finalize.py` also fixes metal minimum area (MET1_A_1/METn_A_1) by
  growing under-area landing pads where spacing allows, and grows an implant
  over poly end-caps inside it (NP_EN_3/PP_EN_3).
* Leaf LVS of blocks whose supply rails are joined by the parent power grid
  (e.g. predecoders) uses `VIRTUAL CONNECT NAME vdd gnd` — leaf checks only.

### 2026-10-10 (later)
* `custom/ics55_dff.py`: one shared 5.2 x 2.7 um slot for 7T and 9T DFFs
  (library rows are 1.4 and 1.8 um) with its own N/P taps; D/Q/clk are
  lifted to M2 by bare via1 on the foundry pins, leaving foundry M1 untouched.
  The clock uses a full-height M2 strip clear of the rail-end supply vias.
  Library DFF netlists are 7-terminal (VNW/VPW) because the cells are tapless
  and label their wells; the slot ties them to vdd/gnd.
* `import_stdcells.py` adds rectangular pin shapes *inside* the existing M1
  (OpenRAM's GDS pin finder only accepts rectangles; the rails are notched).
* `custom/ics55_gen.py` + `ics55_sense_amp.py` + `ics55_write_driver.py`:
  generated latch sense amplifier and the initial en-gated pull-down write
  driver (scn4m topologies); the driver was later replaced by the full-CMOS
  tri-state version described in `SIM_RESULTS.md`.
  The default strip pitch is 0.46 µm; the full-swing driver uses 0.58 µm
  for M1 spacing. Local nets use lower/upper M3 channel tracks joined by
  short M4 connectors, with M2 crossing lines. Layout-less `ics55_mos`
  instances provide the matching SPICE devices from those strip calls.
* dff_buf: Qb pin via moved onto inv1's output strap (it shorted to the
  second inverter's PMOS/vdd metal; only the OS deck's flat LVS flagged it —
  the tapeout deck compares the 9T DFF as an HCELL).
* Leaf results, both decks, DRC 0 + LVS CORRECT: ics55_dff, dff_array,
  dff_buf, dff_buf_array (SC9T); sense_amp, write_driver and their arrays
  (1RW and 1W+1R pitches).  7T (H7CR): LVS CORRECT; DRC only the vendor
  cell's own 104 results.

### 2026-10-10 (evening) — first clean macros
* **16x4 1RW (SC9T): DRC 0 and LVS CORRECT on both decks.**
  **64x8 1RW (SC9T): DRC 0 and LVS CORRECT on both decks.**
* Harness fix: an LVS run is CORRECT only if the *overall* comparison banner
  is; the tapeout deck prints per-HCELL banners (it has HCELL entries for the
  9T library), which the first parser mistook for an overall result.  All
  earlier leaf verdicts were re-checked with the fixed parser.
* Issues found and fixed on the way (all would have been silicon defects):
  - din bits shorted through dff_array's M3 clock rail, which crossed the D
    pins where the top level lands its via stacks -> optional `clk_rail_y`
    on the DFF module, used by dff_array/dff_buf_array/dff_inv_array;
  - Q pads of mirrored DFF rows overlapping across the shared rail;
  - dff_buf Q and Qb at the same height, joined by the parent's M3 route;
  - SA dout landing pad next to the array M3 supply rail (dout now on M3).
* Control logic builds its gates at the DFF height; pnand2/pnand3 need
  >= 2.4 um under these rules, so the DFF slot is 2.7 um (1.5 x 9T row).
* Generic OpenRAM fixes: factory.get_mods with technology-overridden types,
  pbitcell analytical model import, sram.save tech `finalize_gds` hook, .lib
  and datasheet kept when there are no device models.
* LEF layer names follow the ICsprout55 tech LEF (MET1..MET5, VIA1..VIA4).

### 2026-10-10 (night)
* 64x8, 256x8 (column mux 4), 1W+1R 32x8 and 2RW 32x8 macros: all clean on
  both decks.  7T variant: LVS CORRECT, DRC only inside the vendor DFF.
* Finalizer metal clean-up rewritten with spatially indexed queries (the
  256x8 macro took >9 min before, ~1 min now).

### 2026-10-11 — Spectre simulation (see SIM_RESULTS.md)
* `sim/`: Verilog-driven Spectre harness (VCS trace of OpenRAM's model,
  9 corners, pass/fail per read, self-timing and leakage analysis), Calibre
  xRC extraction (`vmpex.sh`; `verify/mkdecks.sh` now also derives
  `tape_pex.svrf`).
* **1W+1R 32x128 (SC9T) works on all corners**, schematic and post-layout;
  3.0 ns at TT, 7.5 ns at SS -40 C post-layout (8 ns recommended).
  64x128 is 1.63x the area of 32x128.
* Fixed on the way:
  - w_en of a 128-bit word was a runt pulse (single-stage pand3 output
    inverter of size >100 behind a minimum NAND): pand2/3/4 use a
    multi-stage `pdriver` when `multistage_and_drivers` is set and size > 16;
  - PO_L_1 (narrow poly > 25 um from a contact) in the 82-finger clock
    buffer of a 128-bit word: `pdriver_max_size = 120` caps pdriver stages;
  - `route_supplies = "pins"`: export the supply pins without running the
    supply router (3.1 h for 32x128 with the full router, 72 s without);
  - router: spatially indexed blockage/via lists; finalizer: faster PO
    implant test, GDS polygons split at 4000 vertices (8191 is the GDSII
    limit; the flat implants of a 128-bit macro exceeded it).
* Found, not fixed (SIM_RESULTS.md "Problems found"): column-mux macros fail
  reads at SS -40 C (sense amp PMOS isolation behind the NMOS mux); 2RW
  writes fail at SNFP 1.08 V 125 C (pull-down-only write driver).

### 2026-10-11 (later) — static precharge, 1RW 32x32
* `static_precharge` (tech parameter, on for ICS55): precharge for the whole
  clock-high phase plus two extra `wl_en` stages; removes the replica-bitline
  leakage limit (5 MHz passes at FF/FNSP 125 C post-layout).
* `configs/ics55_1rw_32x32.py` / `ics55_1rw_64x32.py`: 0.027 / 0.045 mm²,
  DRC 0 / LVS CORRECT on both decks; 32x32 post-layout pass on all corners
  at 10-20 ns.  16x4, 1W+1R 32x8 and 2RW 32x8 rebuilt clean with the change.

### 2026-10-11 (night) — 1RW 16x32 delivery
* Static precharge now also covers idle cycles (`p_en_bar = gated_clk_bar`).
* `configs/ics55_1rw_16x32.py`: 212.95 x 83.06 um (33 % smaller than 32x32).
  Post-layout pass on all corners at 8-200 ns.
* `scripts/package_macro.py` provides flow-package generation: VDD/VSS pin
  names, LEF 5.8 / DATABASE 1000 / full MET1-4 OBS / antenna data, CDL with
  the selected `stdcell_library` DFF (validated against its explicit CDL and
  `CK D Q VDD VSS` terminal order), role-correct Liberty from
  `sim/charz.py`/`sim/mklib.py`, Verilog model + role-correct stub. The
  interface contract is covered by deterministic fixture tests; no current
  packaged GDS/CDL has been run through Calibre, Genus, or Innovus in this
  validation pass.

### Native sizing/characterization continuation

* The registry admits exact 32x8 SC9T 2RW tuples `(9,4,1)`, `(9,4,.75)` and
  `(9,3,1)` for delay stages/fanout/write strength; ungenerated combinations
  reject. Both new layouts complete their own GDS/RCC/topology and actual
  SNFP125,7.5ns native screening with48/48 bit checks.
* Real width075 characterization at5/50fF passes96/96 bit checks per load
  and measures isolated whole-macro signed read/write energy. Both ports have
  measured falling-clock output-fall tables; output-rise tables are unknown.
  The actual diagnostic Liberty omits missing polarity pairs and remains
  `dont_use`; no complete F4/STA interface is inferred from passing reads.
* Packaging/export now requires explicit `--diagnostic-table-only` and emits
  incomplete `dont_use` tables. Historical role-correct table generation and
  fixture coverage do not prove macro setup/hold/pulse/Tmin or input/clock
  loading. Complete STA Liberty remains unavailable.
* Native F4 diagnostics and strict physical evidence ingestion are implemented;
  DRC evidence passes, strict native LVS fails and RC is estimated/unaccepted.
  The independent characterization closure gate remains unknown. See
  [SIM_RESULTS.md](SIM_RESULTS.md) for exact measured domains and raw evidence.


## Open items
* Column-mux transistor-level cross-corner validation and cross-corner
  re-characterization of the full-swing 2RW write driver remain open; see
  `SIM_RESULTS.md`.  The current `ics55_2rw_32x8` configuration uses
  `words_per_row = 1`, so distinct addresses occupy distinct physical rows.
* The `.lib` is still OpenRAM's analytical model; use SIM_RESULTS.md for
  timing until a characterized .lib exists.
* Read/write margins (SNM, Monte Carlo) were not simulated, only corners.
* Antenna rules are part of the decks run and pass; density is chip-level.

## Current port-aware generator validation

The package and characterization flow now requires an explicit standard-cell
library and CDL, validates the selected DFF subcircuit, and derives Verilog
stubs and Liberty pins/arcs from per-port RW/R/W roles. Supported DFF mappings
cover the SC9T/SC9TL/SC9TH and H7CR/H7CL/H7CH families. The deterministic
contract test `technology/icsprout55/sim/test_p1_interfaces.py` passed 5 tests
for SC9T and H7CR across 1RW, 1W1R, and 2RW interfaces, mapping and
fail-closed cases, and multi-clock LEF antenna sizing. Those tests use fixture
measurement data, not a Spectre-characterized package. The subsequent native
ngspice continuation above produces measured, incomplete diagnostic fall tables
only; neither result is a complete measured STA Liberty artifact.
