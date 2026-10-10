# ICsprout55 OpenRAM port — status and log

Target: OpenRAM macros (1RW single-port; 1W+1R / 2RW dual-port) for
ICsprout 55LLULP that pass **both** foundry Calibre decks:

* **tapeout deck** — the SoC sign-off runsets (`drc.runset` V1.21 /
  `lvs.runset`, as used in `Baixin_SoC_ICS55/.../full_inward_ring_staged`);
* **OS deck** — `icsprout55-pdk/pv/{DRC,LVS}/ICsprout_Cal*_55LLULP1233_REV1_0_OS.*`.

Both decks gave identical results on every layout tried so far.

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
  expected tapless-cell latch-up/floating-well flags.  A macro using a 7T DFF
  therefore cannot be Calibre-clean; see "7T option" below.
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
* `custom/ics55_dff.py`: one 5.2 x 1.8 um slot for 7T and 9T DFFs with its
  own N/P taps; D/Q/clk lifted to M2 by bare via1 on the foundry pins (the
  foundry M1 is untouched); clock as a full-height M2 strip clear of the rail
  end supply vias.  Library DFF netlists are 7-terminal (VNW/VPW) because the
  cells are tapless and label their wells; the slot ties them to vdd/gnd.
* `import_stdcells.py` adds rectangular pin shapes *inside* the existing M1
  (OpenRAM's GDS pin finder only accepts rectangles; the rails are notched).
* `custom/ics55_gen.py` + `ics55_sense_amp.py` + `ics55_write_driver.py`:
  generated latch sense amplifier and en-gated pull-down write driver (scn4m
  topologies), pitch-matched to the bitcell.  Strips on a 0.23 um pin grid,
  channel routing with lower/upper M3 track sets joined by short M4
  connectors, M2 crossing lines.  The netlist comes from layout-less
  `ics55_mos` devices declared by the same strip calls.
* dff_buf: Qb pin via moved onto inv1's output strap (it shorted to the
  second inverter's PMOS/vdd metal; only the OS deck's flat LVS flagged it —
  the tapeout deck compares the 9T DFF as an HCELL).
* Leaf results, both decks, DRC 0 + LVS CORRECT: ics55_dff, dff_array,
  dff_buf, dff_buf_array (SC9T); sense_amp, write_driver and their arrays
  (1RW and 1W+1R pitches).  7T (H7CR): LVS CORRECT; DRC only the vendor
  cell's own 104 results.
