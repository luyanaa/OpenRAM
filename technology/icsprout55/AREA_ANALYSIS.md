# ICsprout55 SRAM area analysis

Measured existing GDS hierarchy, compared with retained commercial measurements and pinned public SRAM22 sources. Known vendor-cell DRC waivers do not imply SRAM-device rules or license the use of an unavailable SRAM bitcell kit.

## Findings

Small ICsprout55 macros spend most of their footprint outside the useful storage array. Large macros also pay a high incremental cost for ordinary logic-rule bitcells and row peripherals. Switching DFF libraries or copying the TG mux topology cannot eliminate either cause.

The available evidence does **not** show that ICsprout55 macros are larger than SRAM22 in absolute area: the comparable-capacity public SKY130 examples below are larger. SRAM22 does have a substantially smaller bitcell pitch despite its older process. These are different comparisons.

## Measurements and accounting

These measurements use KLayout Python 0.30.12 on the existing finalized GDS, without regenerating macros. Total area is the top-cell bounding rectangle, matching LEF dimensions. Main-array area is the bounding rectangle of useful bitcells, excluding dummy/replica cells. Mirrored cells share protruding power/well geometry; a standalone cell's raw bounding rectangle is not its tiling pitch.

| Previously measured macro | Physical rows × columns | LEF / GDS size (µm) | Total area (mm²) | µm² / useful bit | Main-array bbox (µm²) | Main-array fraction |
|---|---:|---:|---:|---:|---:|---:|
| 1RW64×8, NMOS mux2 | 32 × 16 | 115.030 × 134.605 | 0.015484 | 30.24 | 4,102.62 | 26.5% |
| 1RW256×8, TG mux4 | 64 × 32 | 168.150 × 228.015 | 0.038341 | 18.72 | 16,372.40 | 42.7% |
| 2RW32×8, mux1 baseline | 32 × 8 | 155.010 × 185.860 | 0.028810 | 112.54 | 3,666.18 | 12.7% |

The 1RW rows use the GDS/LEF pairs in `ics55_work/runs/out_ics55_1rw_64x8/` and `out_ics55_1rw_256x8_mux4_tgate/`; the 2RW baseline row uses `ics55_work/runs/out_ics55_2rw_32x8_wpr1/`. Routefix2 has the same measured outer size. Numerical hierarchy measurements are retained in `ics55_work/runs/area_analysis/hierarchy.json`.

### 2RW32×8 compaction follow-up

The routefix5 candidate was regenerated from the same SC9T, `pbitcell`,
2RW32×8, `words_per_row = 1` configuration. Its LEF size matches the KLayout
GDS bounding box.

| Candidate | LEF / GDS size (µm) | Top bbox area (µm²) | Change vs routefix2 |
|---|---:|---:|---:|
| routefix2 baseline | 155.010 × 185.860 | 28,810.1586 | — |
| routefix5 | 155.010 × 185.280 | 28,720.2528 | −89.9058 (−0.3121%) |

The measured 0.58 µm height reduction comes from retaining one M3
non-preferred track between the data-DFF channel and DFF row; long data-DFF
trunks stay on M4 and pin stubs on M3, away from the M3 supply mesh. Macro
width is unchanged; routefix5 is 112.19 µm² per useful bit. The write-driver
spacing correction does not change the top bbox. Bitcell pitch, replica/cap
structures, and supply/tap boundaries were not compressed.

The following is a footprint budget, not transistor-active or metal utilization. It uses the union of immediate bank-block bounding rectangles to avoid overlap double counting. Array/caps have precedence over data periphery at shared rail boundaries. The bank residual and outside-bank rectangle contain routing, rails, margins and vacant space; they cannot all be removed.

| Footprint component | 1RW64×8 (µm² / %) | 1RW256×8 TG (µm² / %) | 2RW32×8 (µm² / %) |
|---|---:|---:|---:|
| Useful main array | 4,102.62 / 26.5 | 16,372.40 / 42.7 | 3,666.18 / 12.7 |
| Array caps, dummy/replica and array margins | 1,544.96 / 10.0 | 2,984.60 / 7.8 | 2,917.79 / 10.1 |
| Data periphery footprint | 1,772.26 / 11.4 | 4,287.56 / 11.2 | 2,482.70 / 8.6 |
| Address decoder / wordline driver / column decoder | 1,904.51 / 12.3 | 3,825.72 / 10.0 | 4,567.41 / 15.9 |
| Bank area outside those block bboxes | 1,024.42 / 6.6 | 1,323.04 / 3.5 | 3,303.56 / 11.5 |
| Outside bank: control, input DFFs, routing, supply margins and vacant space | 5,134.85 / 33.2 | 9,547.40 / 24.9 | 11,872.52 / 41.2 |

For example, the 2RW main array is 35.12 × 104.39 µm, but its capped replica array is 56.06 × 117.445 µm. The bank expands to 99.93 × 169.495 µm after two address and data peripherals, and the top expands again to 155.01 × 185.86 µm. This explains why reducing only the storage cell does little for a 256-bit two-port macro.

## Why the cell pitch is large

| Bitcell implementation | Tiling pitch (µm) | Pitch area (µm² / bit) |
|---|---:|---:|
| ICsprout55 generated 1RW pbitcell | 3.05 × 2.615 | 7.97575 |
| ICsprout55 generated 2RW pbitcell | 4.39 × 3.25 | 14.26750 |
| SRAM22 bundled SKY130 single-port SRAM cell | 1.20 × 1.58 | 1.89600 |

ICsprout55 dimensions come from GDS boundary `(351,12)` and mirrored array placements. SRAM22 dimensions are explicit `BITCELL_WIDTH`/`BITCELL_HEIGHT` in its process source.[^sky130]

The ICsprout55 1RW cell already has the conventional six-transistor storage/access topology: four storage MOS plus two access MOS (`compiler/modules/pbitcell.py`, `create_storage` and `create_readwrite_ports`). It is not large because it needs more than six transistors. It is large because it implements that topology using independently contacted ordinary logic MOS, generic gate-to-contact routing, wells and logic design rules. The 2RW cell adds another access pair and larger storage pull-downs. The differential isolated-read 1W+1R topology is different again; its area cannot be inferred from the 1RW cell.

This port intentionally has no SRAM marker or delivered SRAM-device/IP kit (`PORT_STATUS.md`). Its rules include 0.15 µm minimum contacted device width, 0.09 µm contacts with 0.14 µm spacing, and 0.26/0.25 µm tap-to-active/poly clearances (`tech/tech.py`). The custom strip generator uses a 0.46 µm default gate pitch; the full-swing write driver uses 0.58 µm to retain 0.12 µm M1 clearance around supply straps, and local nets route through M3 channel tracks (`custom/ics55_gen.py`, `custom/ics55_write_driver.py`). An earlier global pitch increase failed during sense-amp layout with an M2 stub conflict, so the wider pitch is isolated to the write driver. The wider driver geometry increases drawn source/drain diffusion; extracted-parasitic impact is unmeasured, so cross-corner timing re-characterization remains open.

SRAM22 binds a hard `sky130_fd_bd_sram__sram_sp_cell_opt1` layout and its netlist uses `special_nfet_pass`, `special_nfet_latch` and `special_pfet_pass` devices.[^macros][^cell] Its tight SRAM cell rules and shared array boundaries are not obtained by changing a standard-cell row height. The ICsprout55 1RW pitch area is 4.21× that SKY130 pitch area even before node normalization. That observation demonstrates the importance of the cell kit/layout; it is not a valid prediction of how much a SKY130 cell would shrink at 55 nm. Scaling by `(55/130)²` does not preserve contact rules, devices, margins or SRAM qualification.

## SRAM22 comparison

SRAM22 is a single-port SKY130 generator. Its supported column mux ratios are 4/8.[^readme] Published LEFs give the following actual dimensions:[^lef64][^lef256][^lefwide]

| Macro | Process / ports | Area (mm²) | µm² / bit | Interpretation |
|---|---|---:|---:|---|
| ICsprout55 1RW256×8 TG mux4 | 55 nm / 1RW | 0.038341 | 18.72 | Current measured GDS |
| SRAM22 256×8 mux8, write-size1 | SKY130 / 1RW | 0.059065 | 28.84 | 261.720 × 225.680 µm; different mux and write mask |
| ICsprout55 1RW64×32 mux1 | 55 nm / 1RW | 0.044703 | 21.83 | Prior 214.3 × 208.6 µm record in `PORT_STATUS.md`; not a newly generated layout |
| SRAM22 64×32 mux4, write-size8 | SKY130 / 1RW | 0.068821 | 33.60 | 360.320 × 191.000 µm; different mux and write mask |
| SRAM22 256×128 mux4, write-size8 | SKY130 / 1RW | 0.340315 | 10.39 | 1085.880 × 313.400 µm; much larger capacity amortizes periphery |

The first two capacity-matched comparisons make ICsprout55 about 35% smaller in absolute area. They do not establish superior layout efficiency or equivalent speed/margins: process, mux, mask and characterization differ. Conversely, comparing ICsprout55 2RW32×8 at 112.54 µm²/bit to SRAM22's large 1RW macro at 10.39 µm²/bit mixes capacity, process and ports and cannot isolate a compiler problem.

Useful SRAM22 references are its dedicated hard SRAM array/cap/strap layouts, jointly placed column group, complementary `sel`/`sel_b`, and two full-swing tri-state write inverters.[^columns][^write] The current ICsprout55 port already has an optional TG mux and full-swing driver. Copying those schematics alone does not copy the array cell pitch, tap sharing or physical packing. No evidence here attributes SRAM22 density to extra banking; bank partitioning duplicates peripheral circuitry and must be evaluated against timing and total area.

## Commercial comparison

The retained same-PDK Meminder figures in `SIM_RESULTS.md` are 0.0173 mm² for 1RW32×128 and 0.0196 mm² for 1RW64×128. OpenRAM's corresponding **1W+1R** figures are 0.1271 and 0.2067 mm²: 7.35× and 10.55×. The capacity/process match, but the port match does not. Separate concurrent read/write addressing requires more cell devices, bitlines, wordlines, decoding and clock/control circuits. There is no matched two-port commercial layout here, so no honest fraction of the ratio can be assigned to port overhead alone.

The two-point depth fits are nevertheless informative:

| Effective area fit, µm² | OpenRAM 1W+1R | Commercial 1RW |
|---|---:|---:|
| Intercept at zero useful bits | ~47,500 | ~15,000 |
| Incremental footprint per added bit | ~19.43 | ~0.562 |

These are fits to two rounded macro measurements, not isolated device/periphery measurements or guaranteed extrapolations. The ~34.6× slope difference shows that array pitch, row peripherals and reserved macro width are a major large-capacity issue; fixed DFF/control overhead alone cannot explain it. Commercial SRAM-specific cells/rules and highly shared custom layout are the likely source of much of that density advantage [INFERENCE]; the proprietary bitcell geometry and circuit split are not available here to quantify them.

The 55 nm logic-rule 1RW pitch area of 7.976 µm² already exceeds the **entire** commercial 64×128 macro's 2.39 µm²/bit. Even a zero-periphery implementation of that ordinary logic cell could not reach the recorded commercial density. A delivered and qualified SRAM cell/device kit is needed for that class of density; a DFF internal waiver is not such a kit.

## Optimization order

1. **For small macros, repack the floorplan and reduce routing amplification.** Bank gaps plus the region outside the bank account for 39.8% / 28.4% / 52.7% of the three footprints. These regions include necessary wiring/control and are opportunity bounds, not promised savings. Place/control/address circuitry in presently unused vertical spans, shorten buses and share appropriate supply/tap margins before adding banks. Preserve pin access, independent-port timing and electrical isolation.
2. **Reduce the capped dummy/replica footprint while preserving boundary rules and tracking.** Caps/margins add ~38%, ~18% and ~80% over the useful-array bbox, respectively. The narrow 2RW macro is especially sensitive to fixed replica/tap columns. Removing replica tracking without replacing its timing function would invalidate the access contract.
3. **Optimize the actual row/data physical cells.** Address peripherals consume 10–16%; data-periphery bboxes consume 9–11%. The generated sense amplifier is ~8.455 µm high and full-swing driver ~12.435 µm high. Their strips/channels and distributed placement are worth jointly compacting. These are already custom cells, not standard-cell synthesis; arrays have one sense/driver per output word bit after mux, not one active device for every physical column (`compiler/modules/port_data.py`).
4. **For larger SRAM, prioritize a pitch-optimized bitcell/array.** A custom abutting six-transistor layout under validated logic rules is a reachable experiment; foundry SRAM rules/models require the actual matching kit. Reducing the main-array area by 50% alone would save only about 13.2% / 21.4% / 6.4% of these small-macro footprints before any dependent repacking. No 50% cell reduction has been demonstrated.
5. **Treat mux ratio and banks as timing/area choices, not automatic area wins.** More mux can amortize output peripherals and shorten bitlines but widens arrays and adds selection routing; more banks add independent periphery. For 2RW, reverting to mux2 reintroduces the measured same-row write conflict. Its mux1 choice costs ~7.1% versus the prior mux2 layout but is a functional-contract decision.
6. **Do not prioritize the library switch alone.** `custom/ics55_dff.py` fixes both 7T and 9T wrappers to 5.2 × 2.7 µm. The measured foundry-cell GDS bboxes are 4.70 × 1.63 µm (7T) and 4.68 × 2.15 µm (9T); their row heights are 1.4 and 1.8 µm. The common slot leaves a tap column and at least 2.4 µm for control gates, so the raw DFF bbox does not justify a smaller wrapper. The measured macros contain 16 / 18 / 30 DFFs; their nominal wrapper tile sums are only ~1.45% / 0.66% / 1.46% of total area. Even replacing them with zero-area DFFs would have little direct benefit unless the floorplan/channel dimensions are also repacked.

## Verification scope

Known original-cell DRC markers are retained as exact leaf waivers in `verify/drc_waivers.json`. Generated wrapper vias, interfaces and array/core findings remain unwaived; LVS has an independent verdict. See [verification notes](verify/README.md) for retained full-hierarchy reports and the final M4-reroute LVS result; its DRC remains unvalidated. The compaction keeps bitcell pitch and replica rows but changes the data-DFF routing gap and write-driver source/drain spacing; extracted timing impact and current foundry signoff remain unverified.

The earlier routefix5 2RW candidate's full-hierarchy KLayout macro-profile DRC
has 0 raw, waived, and unwaived markers; its KLayout LVS reports a mismatch.
These results predate the final M4 reroute and do not validate its DRC. The M4
reroute's production OpenRAM/KLayout LVS passes; see [`verify/README.md`](verify/README.md).
No current Calibre sign-off is available.

The 2RW bank retains `rbl = [1, 1]`, the left/right replica columns, and
array cap structures (`compiler/modules/bank.py`,
`compiler/modules/capped_replica_bitcell_array.py`). Its decoder gap is
rule-derived from well spacing and two M2 pitches; `place_control()` reserves
two M2 pitches and `route_clk()` uses the adjacent M2 clock track
(`compiler/modules/sram_1bank.py`). No replica, cap, tap, or decoder boundary
was reduced without evidence that replica tracking, well separation, and clock
and data access remain valid.

## Public references

SRAM22 source revision: `6ec35d5acc777eeaa199730d7976554e3b3fc50c`; catalog revision: `de0a9a7cdabf941c807a5ab35864f80d97fccfea`. The earlier local checkout is unavailable; public source and catalog are explicit evidence, not assumed to be identical to that earlier fork.

[^sky130]: [SRAM22 SKY130 constants](https://github.com/ucb-substrate/sram22/blob/6ec35d5acc777eeaa199730d7976554e3b3fc50c/src/tech/sky130.rs).
[^macros]: [SRAM22 hard-cell bindings](https://github.com/ucb-substrate/sram22/blob/6ec35d5acc777eeaa199730d7976554e3b3fc50c/src/blocks/macros/mod.rs).
[^cell]: [Bundled SRAM cell SPICE](https://github.com/ucb-substrate/sram22/blob/6ec35d5acc777eeaa199730d7976554e3b3fc50c/tech/sky130/spice/sram_sp_cell.spice).
[^readme]: [SRAM22 process, ports and configuration](https://github.com/ucb-substrate/sram22/blob/6ec35d5acc777eeaa199730d7976554e3b3fc50c/README.md).
[^lef64]: [SRAM22 64×32 LEF](https://github.com/ucb-substrate/sram22_sky130_macros/blob/de0a9a7cdabf941c807a5ab35864f80d97fccfea/sram22_64x32m4w8/sram22_64x32m4w8.lef).
[^lef256]: [SRAM22 256×8 LEF](https://github.com/ucb-substrate/sram22_sky130_macros/blob/de0a9a7cdabf941c807a5ab35864f80d97fccfea/sram22_256x8m8w1/sram22_256x8m8w1.lef).
[^lefwide]: [SRAM22 256×128 LEF](https://github.com/ucb-substrate/sram22_sky130_macros/blob/de0a9a7cdabf941c807a5ab35864f80d97fccfea/sram22_256x128m4w8/sram22_256x128m4w8.lef).
[^columns]: [SRAM22 column-group layout](https://github.com/ucb-substrate/sram22/blob/6ec35d5acc777eeaa199730d7976554e3b3fc50c/src/blocks/columns/layout.rs).
[^write]: [SRAM22 tri-state differential write driver](https://github.com/ucb-substrate/sram22/blob/6ec35d5acc777eeaa199730d7976554e3b3fc50c/src/blocks/wrdriver/schematic.rs).
