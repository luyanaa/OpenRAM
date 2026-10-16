# ICsprout55 OpenRAM macros — Spectre timing / functional results

Transistor-level simulation of generated macros (SC9T, `pbitcell`) with the
foundry Spectre models (`icsprout_55llulp1225_v1p1_spe.lib`) on all nine
corners.  Harness: `sim/` (README there).  Each run applies a
Verilog-generated access sequence (pattern fill, read-back, read-after-write,
concurrent read + write on two-port macros, complement overwrite) with a
50 % duty clock and checks every dout bit just before the rising edge that
ends the read cycle.

Corners: TT 1.2 V 25 C; SS 1.08 V 125/-40 C; FF 1.32 V 125/-40 C;
SNFP / FNSP (slow-N fast-P / fast-N slow-P) 1.08 V 125 C and 1.32 V -40 C.

## Summary

* **1W+1R 32x128 (9T) works** on all nine corners, schematic and
  post-layout (Calibre xRC, TYP rules, C-only).  Post-layout minimum
  period: **3.0 ns at TT, 7.5 ns at the worst corner (SS 1.08 V -40 C)**;
  run it at **8 ns (125 MHz), 50 % duty** to close on every corner.  The
  read happens in the low phase, so read data is valid T/2 + 1.3 ns (TT) /
  T/2 + 3.5 ns (SS -40 C) after the rising edge that launched it, and holds
  ≥ 0.5 ns after the next rising edge (FF -40 C).
* Wiring parasitics dominate: post-layout minimum periods are 1.2-3x the
  schematic ones (worst at SS -40 C: 32x8 2.0 → 6.0 ns, 32x128 4.0 →
  7.5 ns), through the self-timed replica-bitline + delay-chain path and the
  long wordline/bitline wires.  Schematic numbers are optimistic.
* Area: 1W+1R 32x128 = 0.127 mm², 64x128 = 0.207 mm² (**1.63x for 2x
  bits**): the width stays, each extra 128-bit word adds 2.875 um of
  height (~19.4 um² per bit); the periphery is ~0.048 mm².
* Three design limits were found (details in "Problems found"):
  1. **Maximum clock-high time — fixed** (`static_precharge`, see
     "1RW 32x32" below): OpenRAM's self-terminated precharge left the
     replica bitline floating for the rest of the clock-high phase; at hot
     corners it leaked to about VDD/2 within 5-10 ns, so 1RW/2RW macros
     failed at 10 ns periods.  The results tables below this summary were
     taken before the fix (except the 32x32 section).
  2. **Historical NMOS-only column-mux failure at SS -40 C** (prior Spectre
     runs returned all ones): the mux's reduced high level did not satisfy the
     PMOS-isolated sense amp.  `words_per_row = 1` avoids that mux; a
     transmission-gate mux is now selectable and the 256x8 TG config passes a
     limited local ngspice smoke.  See the addendum; it does not supersede the
     earlier Spectre results.
  3. **Historical 2RW write failure at SNFP 1.08 V 125 C** used the former
     pull-down-only write driver.  The current driver is full-swing, but a
     cross-corner Spectre re-characterization remains open.
* **1RW 16x32 (delivered macro)**: 213 x 83 um = 0.0177 mm², DRC/LVS
  clean, post-layout pass on all corners at 8 (SS), 10, 20 and 200 ns;
  Liberty for 8 corners from post-layout Spectre measurements
  (`scripts/package_macro.py`, `sim/charz.py`, `sim/mklib.py`).  32x32 is
  0.027 mm².
* Recommended for the SoC: **1W+1R or 1RW, `words_per_row = 1`, 50 % duty
  clock, 8 ns or slower** (TT-only parts down to ~4 ns), or the foundry compiler where
  a 1RW macro suffices (it is 7-10x denser and 2-3x faster, see the last
  section).

## Timing model (what limits the period)

All operations happen in the low clock phase.  Inputs are registered at the
rising edge; `wl_en` = gated clock-bar (whole low phase); `w_en` and `s_en`
are gated by the replica bitline (`rbl_bl`) through a 9-stage FO4 delay
chain; the precharge (`p_en_bar`) is a self-terminating pulse after the
rising edge.  `dout` comes straight from the latch sense amp (transparent
before `s_en`) and is valid until the next rising edge.  So, at 50 % duty:

* low phase ≥ read: `dout` settles (`dout_after_fall`) and the write pulse
  ends (`w_en` fall) before the next rising edge;
* high phase ≥ precharge and delay-chain reset (`p_en_bar` end after the
  rising edge);
* high phase ≤ leakage limit of the floating replica bitline (problem 1).

`T_safe = 2 x max(p_en_end, w_en_fall, dout_after_fall + 0.15 ns)` lets
every self-timed event finish inside its phase; macros usually still pass a
bit below it (a truncated write/precharge can be enough), which is the
"observed minimum" below.  Hold: `dout` stays valid after the next rising
edge for ≥ 0.18 ns (schematic, FF -40 C), 0.39 ns (post-layout 32x8) and
0.55 ns (post-layout 32x128), because the next precharge has to reach the
sense amp first (`hold.py`).

## 1W+1R 32x128 (the target)

Schematic netlist (`pre_layout` device parasitics only), 20 cycles, passing
period (tested 2.5 and 4 ns):

| corner | T pass (ns) | dout after clock fall (ns) |
|---|---|---|
| tt 1.2 V 25 C | 2.5 | 0.58 |
| ss 1.08 V 125 C | 2.5 | 1.20 |
| ss 1.08 V -40 C | 4.0 (2.5 fails) | 1.28 |
| ff 1.32 V 125 / -40 C | 2.5 | 0.36 / 0.34 |
| snfp 1.08 V 125 / 1.32 V -40 C | 2.5 | 0.69 / 0.40 |
| fnsp 1.08 V 125 / 1.32 V -40 C | 2.5 | 0.94 / 0.60 |

Post-layout (xRC C-only: 57 k devices, 24 k capacitors; ~1 h per Spectre
run with 12 cycles),
self-timing at T = 10 ns (`tanalyze.py`, ns after the clock edge):

| corner | dout after fall | s_en after fall | w_en end after fall | precharge end after rise | T read-limited = 2(dout+0.15) | T_safe |
|---|---|---|---|---|---|---|
| tt 1.2 V 25 C | 1.33 | 1.91 | 1.96 | 1.91 | 3.0 | 3.9 |
| ss 1.08 V 125 C | 2.83 | 3.76 | 3.91 | 3.77 | 6.0 | 7.8 |
| ss 1.08 V -40 C | 3.54 | 3.32 | 3.45 | 3.34 | 7.4 | 7.4 |
| ff 1.32 V 125 C | 0.80 | 1.35 | 1.39 | 1.40 | 1.9 | 2.8 |
| ff 1.32 V -40 C | 0.74 | 1.20 | 1.22 | 1.20 | 1.8 | 2.4 |
| snfp 1.08 V 125 C | 1.58 | 2.73 | 2.85 | 2.62 | 3.5 | 5.7 |
| fnsp 1.08 V 125 C | 2.49 | 2.44 | 2.48 | 2.59 | 5.3 | 5.3 |
| snfp 1.32 V -40 C | 0.91 | 1.59 | 1.66 | 1.60 | 2.1 | 3.3 |
| fnsp 1.32 V -40 C | 1.55 | 1.51 | 1.53 | 1.60 | 3.4 | 3.4 |

All nine pass at 10 ns (8 reads + writes each).  The rbl droop is
negligible post-layout (≥ 1.17 V after 5 ns of float at FF 125 C).

Verification runs at shorter periods:

| corner | period (ns) | result | dout margin before the edge (ns) |
|---|---|---|---|
| tt 1.2 V 25 C | 2.5 | **fail** (6 of 8 reads) | — |
| tt 1.2 V 25 C | 3.0 | pass | 0.18 |
| tt 1.2 V 25 C | 4.0 | pass | 0.67 |
| snfp 1.08 V 125 C | 3.5 | pass (write pulse cut 1.1 ns short) | 0.17 |
| fnsp 1.08 V 125 C | 5.5 | pass | 0.24 |
| ss 1.08 V 125 C | 6.0 | pass | 0.18 |
| ss 1.08 V 125 C | 8.0 | pass | 1.17 |
| ss 1.08 V -40 C | 7.5 | pass | 0.20 |

The read-limited estimate `2 x (dout_after_fall + 0.15 ns)` predicts every
pass/fail: a write or precharge pulse truncated by the clock edge still
completes.  **Post-layout minimum period of 1W+1R 32x128: ~3 ns at TT,
7.5 ns at the worst corner (SS 1.08 V -40 C).**  Use 8 ns (125 MHz) for a
design that has to close at all corners, which is also the T_safe bound where
every self-timed event completes; at 50 % duty that is within the leakage
limit (problem 1) with ample room.

Capping the clock buffer at size 120 (`pdriver_max_size`, needed for DRC)
does not change the timing: schematic access 1.825 vs 1.834 ns at TT 2.5 ns,
3.258 vs 3.276 ns at SS -40 C 4 ns (new vs old netlist).  The extracted
netlist above is of the macro before that change.

## 1RW 32x32 with static precharge (tapeout candidate)

`configs/ics55_1rw_32x32.py`: 213.4 x 124.6 um = **0.0266 mm²**
(64x32: 214.3 x 208.6 um = 0.0447 mm²), supplies routed, DRC 0 / LVS
CORRECT on both decks.  Sizes before the fix were identical.

| check | result |
|---|---|
| schematic, all 9 corners, 20 ns | pass |
| schematic, TT and SS -40 C, 3 and 4 ns | pass (same slack as without the fix on 128x32) |
| schematic, FF 125 C / FNSP 125 C / TT 125 C, 200 ns (5 MHz) | pass |
| post-layout (xRC C), all 9 corners, 10 and 20 ns | pass |
| post-layout, FF 125 C and FNSP 125 C, 200 ns | pass |
| wordline rise after precharge release, post-layout FF/FNSP/TT | 75-133 ps (schematic 24-98 ps; 2 ps before the extra `wl_en` stages) |
| dout hold after the next rising edge, post-layout | ≥ 0.47 ns |

Post-layout self-timing at 10 ns (ns after the clock fall):

| corner | dout | s_en | w_en end | read-limited period 2(dout+0.15) |
|---|---|---|---|---|
| tt 1.2 V 25 C | 1.16 | 1.69 | 1.74 | 2.6 |
| ss 1.08 V 125 C | 2.54 | 3.33 | 3.47 | 5.4 |
| ss 1.08 V -40 C | 3.16 | 2.95 | 3.06 | 6.6 |
| fnsp 1.08 V 125 C | 2.26 | 2.20 | 2.23 | 4.8 |
| snfp 1.08 V 125 C | 1.34 | 2.39 | 2.50 | 3.0 |
| ff 1.32 V 125 / -40 C | 0.70 / 0.65 | 1.23 / 1.07 | 1.25 / 1.08 | 1.7 / 1.6 |

So 50-100 MHz has ≥ 1.8 ns of read slack at SS -40 C, and there is no
lower frequency limit; the clock may also stop in either phase.

## Other configurations (schematic, all corners)

Passing period over the tested set {1.6/1.8, 2, 2.5, 4, 10} ns;
"dout" = settle time after the clock fall at the minimum period.

| macro | worst corner | T min (ns) | TT T min | dout after fall, TT / worst | fails |
|---|---|---|---|---|---|
| 1RW 16x4 | ss | 2.0 | 2.0 | 0.42 / 0.89 | FF 125 C at 10 ns (problem 1) |
| 1W+1R 32x8 | ss | 2.0 | 1.8 | 0.44 / 0.94 | fnsp 1.08 V 125 C at 20 ns (problem 1) |
| 2RW 32x8 (mux 2) | ss 125 C | 2.5 | 2.0 | 0.50 / 1.02 | SS -40 C all T (problem 2); SNFP 1.08 V 125 C all T (problem 3); FF 125 C at 10 ns (problem 1) |
| 2RW 32x8, words_per_row=1 | ss | 2.5 | 2.5 | 0.43 / 0.93 | SNFP 1.08 V 125 C (problem 3) |
| 1RW 64x8 (mux 2) | — | 4 (TT, SNFP) | 4 | 0.55 | SS -40 C (problem 2); fnsp 125 C at 20 ns (problem 1) |

Post-layout 1W+1R 32x8 (xRC C-only):

| corner | T pass (ns) | T fail (ns) | dout after fall @10 ns | p_en end after rise | w_en fall after fall |
|---|---|---|---|---|---|
| tt 1.2 V 25 C | 2.5 | — | 1.12 | 1.70 | 1.63 |
| ss 1.08 V 125 C | 5.0 | 3.0 | 2.39 | 3.19 | 3.23 |
| ss 1.08 V -40 C | 6.0 | 3.0 | 2.88 | 2.82 | 2.87 |
| ff 1.32 V 125 / -40 C | 2.5 | — | 0.64 / 0.61 | 1.17 / 1.05 | 1.14 / 1.01 |
| snfp 1.08 V 125 C | 3.0 | 2.5 | 1.20 | 2.25 | 2.29 |
| fnsp 1.08 V 125 C | 4.5 | 3.0 | 2.05 | 2.20 | 2.09 |
| snfp / fnsp 1.32 V -40 C | 2.5 / 3.0 | — / 2.5 | 0.70 / 1.27 | 1.35 / 1.32 | 1.35 / 1.30 |

Schematic → post-layout for 32x8: TT 1.8 → 2.5 ns, SS -40 C 2.0 → 6.0 ns.
xRC with R (RCC) was within ~2 % of C-only on 32x8.

## Problems found

### 1. Replica-bitline leakage: maximum clock-high time

`p_en_bar = NAND(gated_clk_buf, rbl_bl_delay)` (upstream OpenRAM control
logic) precharges only until the replica bitline has been restored and the
delay chain has seen it; afterwards the replica bitline floats for the rest
of the high phase.  At the falling edge `w_en`/`s_en` are enabled by the
replica path; if leakage has already pulled `rbl_bl` below the inverter
threshold, `rbl_bl_delay` is high at the falling edge: **`w_en` never fires
(writes are lost) and `s_en` fires immediately**.

Measured (`leak.py`; schematic netlists, T = 20 ns, i.e. 10 ns of float;
lowest replica-bitline voltage at a falling edge, VDD/2 crossing time after
the rising edge where it happens; per port for two-port macros):

| macro | FF 1.32 V 125 C | FNSP 1.08 V 125 C | periods tested at the hot corners |
|---|---|---|---|
| 1RW 16x4 | 0.64 V (already 0.65 V after 5 ns) | 0.48 V, 8.1 ns | FF: 8 ns pass, 10 and 20 ns fail; FNSP: 20 ns pass |
| 1RW 64x8 | 0.96 V | 0.43 V, 7.1 ns | FF 20 ns pass; FNSP 20 ns fail |
| 1RW 256x8 mux 4 | 0.78 V | 1.05 V | 20 ns pass on both |
| 2RW 32x8 | 0.65 V, 9.9 ns | 0.29 / 0.48 V | FF: 4 ns pass, 10 and 20 ns fail; FNSP: 20 ns pass |
| 1W+1R 32x8 | 0.94 / 1.03 V | 0.43 / 0.44 V, 7.0 ns | FNSP: 12, 16 ns pass, 20 ns fail |
| 1W+1R 32x128 | 0.99 / 1.08 V | 0.44 / 0.45 V, 7.2 ns | FNSP: 14, 20 ns pass |

Whether a given droop fails depends on the delay chain behind the replica
bitline (slower at 1.08 V), so the table is read as "safe up to the longest
passing period".  At TT/SS/cold corners the droop is negligible, and the
post-layout netlists droop less (more bitline capacitance: the post-layout
1W+1R 32x8 keeps 1.16 V after 5 ns at FF 125 C).

Consequences: a minimum clock frequency at hot corners (≥ 125 MHz for
1RW/2RW at 50 % duty, ≥ 62.5 MHz for 1W+1R; or a shorter high phase with a
non-50 % duty clock), and **the clock must not be stopped high**.
Stopping it low (or deasserting chip select) is safe.  For 1RW/2RW this
leaves a narrow window once post-layout delays are added (the post-layout
1W+1R 32x8 needs 6 ns at SS -40 C); post-layout 1RW/2RW were not simulated.
**Fixed** with `parameter["static_precharge"] = True` (tech.py, now the
ICS55 default): `p_en_bar = gated_clk_bar` (NAND of it with itself plus an
inverting driver), i.e. precharge whenever no access is in its clock-low
phase, which includes the high phase and idle (`csb` high) cycles; and two
extra stages in front of the `wl_en` driver so the wordline rises after the
precharge is released (post-layout 71-247 ps on all corners).  A first
version (precharge only in the enabled high phase) left the bitlines
floating in idle cycles and at power-up; the 32x32 numbers below were
taken with it.  At the rising edge the precharge turns on 0.1-0.36 ns
before the wordline is off, i.e. the selected cell briefly sees precharged
bitlines, which is the normal read condition.  This is how OpenRAM worked before 2019 (`d36f14b4` introduced
the self-terminated pulse, which removes the precharge/wordline race at
the falling edge at the cost of floating bitlines; harmless in low-leakage
processes and fast clocks, which is what the other OpenRAM technologies
are used with).  Verification in the "1RW 32x32" section.

### 2. Column mux + sense amp at slow-N / cold

The generated latch sense amp (scn4m topology) connects `bl/br` to its latch
through PMOS pass devices while `en` is low.  Behind OpenRAM's NMOS-only
column mux the precharged bitline arrives at ~VDD - Vtn, and the PMOS cannot
pass the discharged one below |Vtp|; at SS 1.08 V -40 C the latch keeps its
previous state and all reads return ones.  Macros without a column mux
(`words_per_row = 1`) are not affected (2RW 32x8 rebuilt that way passes
SS -40 C at 2.5 ns).  Fix options: NMOS (or transmission-gate) isolation in
the sense amp, or a transmission-gate column mux.

### 3. 2RW write at slow-N / fast-P

The write driver only pulls the `0` side down.  In the 2RW cell (pull-down
0.72 um, two access pairs) the written cell discharges the floating `1`
side almost as fast as the driver pulls the other side, so both bitlines end
low and the 0.24 um access NMOS cannot flip the node held by the fast PMOS
(Q_bar stops at 0.59 V of 1.08 V).  1RW and 1W+1R pass this corner.  Fix:
drive both bitlines (tri-state inverters, as in the scn4m write driver) or a
weaker 2RW pull-down / stronger access device.

## Area

| macro (1W+1R, SC9T) | size (um) | area (mm²) | um² / bit |
|---|---|---|---|
| 32x8 | 162.4 x 146.3 | 0.0238 | 93 |
| 32x128, supply pins | 868.7 x 146.3 | 0.1271 | 31.0 |
| 64x128, supply pins | 867.6 x 238.3 | 0.2067 | 25.2 |

Both 128-bit macros are DRC 0 / LVS CORRECT on both decks with the final
code.  (Before the clock-buffer cap they were 31 um wider; with the full
OpenRAM supply router instead of `"pins"`, 32x128 was 6 um wider still.)

* 32x128 → 64x128: **x1.63** for 2x the bits.  The width is unchanged;
  the height grows 92 um = 32 rows x 2.875 um.  Linear fit for 128-bit
  words: area ≈ 0.048 mm² + 0.0025 mm² per word (128x128 would be
  ~0.37 mm²).
* The two-point fit has a ~0.0475 mm² intercept; it is an effective fixed
  overhead estimate, not a measured sum of all peripheral cells. Its slope is
  ~19.4 um² per added bit and includes decoder/wordline-driver growth and the
  macro's reserved width. It is not the bitcell area.
* `route_supplies = "pins"` builds 32x128 in 26 min (24 of them in the GDS
  finalizer); the full OpenRAM supply router took 3.1 h for 32x128 and did
  not finish 64x128 in 4 h.

Foundry compiler (Meminder, single-port 1RW, same PDK), for reference:
ICACHE_DATA_32X128 = 567.6 x 30.5 um = 0.0173 mm², 64X128 = 0.0196 mm²
(x1.13); minimum period 1.37 ns TT, 2.8-2.9 ns SS; access 1.08 / 2.24 ns.
The OpenRAM 1W+1R macro is 7.3x (32x128) to 10.5x (64x128) larger and
2.2-2.6x slower (TT 3.0 vs 1.37 ns, worst corner 7.5 vs ~2.9 ns), but it is
a true two-port memory built only from logic devices.

Current GDS hierarchy measurements and the port/process-matched interpretation
of SRAM22 and commercial comparisons are in [AREA_ANALYSIS.md](AREA_ANALYSIS.md).


## Additional local ngspice SS -40 C smoke (not signoff)

Generated schematic netlists were exercised with the PDK `ss_mos` ngspice
models at 1.08 V, -40 C, 5 fF output loads, and 30 ps input/clock edges.
The temporary smoke adapter maps the 24 flattened DFF MOS cards to the
equivalent PDK transistor-wrapper subcircuits. No interconnect parasitics,
PEX, mismatch, or foundry Spectre models are included.

| macro / configuration | period | initialized accesses | readback |
|---|---:|---|---|
| 1RW 64x8, default NMOS mux 2:1 | 20 ns | addresses 0–2 prewritten to `FF`, then `A5`, `3C`, `5A` | 3 reads, all bits match |
| 1RW 256x8, TG mux 4:1 | 5 ns | addresses 0–3 prewritten to `FF`, then `A5`, `3C`, `5A`, `C3` | 4 reads, all bits match |
| 2RW 32x8, `words_per_row = 1` | 20 ns | addresses 0–3 prewritten to `FF`; simultaneous writes to distinct addresses, followed by cross-port reads | 4 reads, all bits match |

The first 2RW mux-2 experiment exposed a physical-row collision: concurrent
writes of `A5` and `3C` to logical addresses 0 and 1 (same physical row)
returned `BD` from both words. The reference 2RW config now uses one word per
row, and the behavioral assertion flags simultaneous writes to any shared
physical row. This increases LEF area from 0.02690 mm² (185.97 x 144.67 um)
to 0.02881 mm² (155.01 x 185.86 um), about 7.1%.

The `OPENRAM_ASSERTIONS` guard was exercised with Icarus Verilog 13.0 on the
generated 2RW bank model and multi-bank top. The regression covers sampled
per-port enables and addresses, overlapping same-physical-row writes (including
distinct logical column addresses), coincident start/completion boundaries in
both scheduler orders, asynchronous clocks with different periods/phases,
stopped-high and stopped-low clocks, read/write overlap, distinct rows, and
cross-bank writes. Same-row write cases terminate with `$fatal`; legal cases
continue. Both generated models also compile and run without the guard enabled.

These local ngspice results do not supersede the older Spectre measurements in
this file: they use different model decks and omit extracted parasitics. In
particular, the 64x8 NMOS-mux pass is a schematic-model smoke, not proof that
the prior cold-corner failure is fixed in the foundry flow.

## Fresh current 2RW full-swing schematic and GDS-derived PEX

The current 2RW trace is `ics55_work/runs/full_swing_2rw_sscold_snfp_write.trace`
(SHA-256 `76d76708f450a1321b9c3eafdbcc5759aefd3c7cecc580165460feb6b9c3f26d`);
the fresh schematic netlist SHA-256 is
`9d8b2abf0c1e4580f119e2ccd99fba56493a73d7159d02d3e2ca6548a4fa2c9c`. Both
corners used ngspice-47, 1.08 V, 2.5 ns, and 5 fF.

| Model/corner | Temperature | Result | `tacc` |
|---|---:|---|---:|
| Schematic SS | −40 °C | 6 reads / 48 bits, no failures | 2.221572209 ns |
| Schematic SNFP | +125 °C | 6 reads / 48 bits, no failures | 1.728684519 ns |

These schematic-model runs are not post-layout signoff. Their records are under
`ics55_work/runs/ngspice_schematic_fresh_2rw_verified/{ss,snfp}/`.

The extracted RCC netlist is derived from the exact fresh 2RW GDS
`ics55_work/runs/out_fresh_final_2rw_baseline/fresh_final_2rw_baseline.gds`
(SHA-256 `f3b92dbb37273630e61fbfbc2670dbfc9e5e7d7fe326f6958170fd836484622e`).
The netlist is at
`ics55_work/runs/out_fresh_final_2rw_baseline/pex_pdk_estimate_rcc_feol_only_fix/`
with SHA-256 `99c2442eccd36dd97ec00e99b81d8c5662a4b44db425b99640ca2b73454f04d0`.
This `pdk-estimate` extraction completed but has `accepted=false` and
`signoff=false`; its parasitics are estimated, not calibrated.
The ngspice-47 SS/−40 °C PEX transient (817.041 s) completed on this exact
netlist and failed 24/48 bit reads: all expected-zero bits at 7.49, 12.49, and
14.99 ns remained at 1.0775–1.0784 V; no output crossing followed the read
edge (the original checker reported `tacc=0`, an unavailable-measurement
sentinel, not zero access delay). Expected-one bits passed; this estimated
PEX result is not accepted or signoff evidence. SS run record:
`ics55_work/runs/ngspice_pex_2rw_sscold/`.

The ngspice-47 SNFP/+125 °C PEX transient (798.443 s) also completed and
failed 24/48 bit reads. All expected-zero samples at 7.49, 12.49, and 14.99 ns
were 0.9712–1.0817 V; expected-one bits passed. The original checker's
`0.9646 ns` is an observed output crossing, not a valid access time for this
functionally failing run. Run record:
`ics55_work/runs/ngspice_pex_2rw_snfp/`. Both PEX runs reached
`ngspice-47 done` with 1112 data rows; the searched diagnostics contained no
“timestep too small,” singular-matrix, or convergence-failure message. The SS
log includes model warnings for negative `Dvt0` and temperature-clamped
`Cjsws/Cjswd`.

The four-rank XyceNF 7.10 attempt timed out at 1800 s without a waveform. Its
log says `.TEMP -40.0` (line 4) is an unrecognized dot line and will be ignored,
so it did not exercise the cold corner and is not counted. [Xyce 7.10 Reference
Guide §2.1.25.1](https://xyce.sandia.gov/download/2068/) specifies
`.OPTIONS DEVICE TEMP=...` (default 27 °C); a standalone `-syntax` check
accepted that directive. No full Xyce corner result is claimed.

### Access-time measurement correction

`sim/ngspice_pex.py` now reports `tacc_ns=null` with
`access_time_status="functional_failure"` whenever any checked read fails.
Missing transitions are never encoded as zero delay. A correct output that
was already stable at the read edge is identified separately as
`already_at_expected_level`; it is not a measured zero-delay transition.
Raw crossing delay remains available as `observed_transition_delay_ns`,
explicitly separate from valid access time.

The corrected checker was exercised on both archived PEX waveforms without
rerunning either simulation. Both have null valid access time. SS has no
observed output crossing; SNFP retains a 0.964592235 ns observed crossing.
Per-port/per-cycle inspection shows four passing one-bits and four failing
zero-bits in each of the six reads. The frozen trace uses mixed patterns
(`A5/3C`, then `5A/C3`), not three all-zero words. Five deterministic
measurement regressions passed, covering stuck outputs, invalid reads with
crossings, already-correct stable outputs, valid transitions and settling
without a midpoint crossing. Original simulation records remain unchanged.

### Frozen-GDS MOS-only baseline and ideal-wire invariant

A genuine MOS-only netlist was emitted from the frozen source-free GDS
geometry: every logical net is ideal/equipotential, with no parasitic R or C.
It was not created by deleting resistors from the distributed RCC network.
The netlist is
`ics55_work/runs/topology_repair_mos_only_manifest/fresh_final_2rw_baseline.mos.sp`,
SHA-256 `f6a4c1c75f074e6b338a398aa1aabb3f853adf78c2c4ed124c2515cdd217b859`.
Its manifest records `completed=true`, `accepted=false`, `signoff=false`;
completion refers to netlist emission, not LVS equivalence.

Both ngspice-47 runs used the unchanged GDS/trace hashes above, 1.08 V,
2.5 ns period and 5 fF output load:

| Source-free GDS baseline | Result | Measured access time | Runtime |
|---|---|---:|---:|
| MOS-only SS / −40 °C | 48/48 bits pass | 2.240811562 ns | 243.836 s |
| MOS-only SNFP / +125 °C | 48/48 bits pass | 1.756330880 ns | 459.234 s |

Run records: `ics55_work/runs/ngspice_mos_only_2rw_sscold/` and
`ics55_work/runs/ngspice_mos_only_2rw_snfp/`.

Invariant A was also exercised against the actual frozen RCC SPICE: all
111,925 resistor edges were treated as ideal wires and all 21,088 capacitors
ignored. Its 6,042 MOS and 50 ports preserve the direct GDS extraction's
2,531 logical nets: no device/port mismatch, logical-net short or disconnected
logical net was reported. Record:
`ics55_work/runs/topology_repair_mos_only_manifest/invariant_a.json`.

These results establish the tested zero-parasitic functionality and the
adapter's ideal-wire connectivity invariant. They do not establish invariant
B (equivalence to the original design netlist), validate spatial RC placement
or numerical values, or turn estimated PEX into signoff evidence. C-only/RCC
timing isolation and independent strict reference-graph equivalence remain
separate gates.

### Frozen 2RW LVS repair: topology-preserving extraction

The same frozen GDS (`f3b92dbb…484622e`) and original `.lvs.sp`
(`b4197191…5d0a6`) now pass the provisional KLayout 0.30.7 LVS run in
449.07 seconds, using physical well taps (`no_well_ties=true`):
`ics55_work/runs/out_fresh_final_2rw_baseline/signoff/parallel_only_klayout_0.30.7/`.
`lvs/lvs.log` contains `ICS55_LVS_COMPARE=PASS`; the extracted top header
contains separate `gnd` and `vdd` pins. `repair_manifest.json` records the
input and repaired-deck hashes.

The legacy adjacent-PDK `rule_decks/combine_fingers.lvs` joined distinct
source/drain nets based on a matching reference width. Its ACT query read only
direct top-cell shapes, so hierarchical macro instances also fell into a
missing-component group. This manufactured the earlier `gnd|vdd` alias; it was
not a localized physical supply short with a GDS coordinate. The pass now uses
KLayout's built-in electrical combination: MOS4 widths are combined only for
already parallel devices with the same G/B/L and unordered D/S net pair.
No supply alias, source-guided diffusion join, sizing change or GDS edit was
used to obtain the pass.

NMOS/PMOS bulk annotations were also separated in `general_derivations.lvs`,
`devices_connections.lvs` and `mos_extraction.lvs`. That change alone still
failed in `signoff/bulk_annotation_fix_klayout_0.30.7/`; removing the unsafe
finger-net joining was necessary for the observed pass.

This deck uses a 10% MOS width tolerance and is not foundry signoff. A separate
audit of its persisted match pairs found all 5884 MOS models/W/L exactly equal
(`max |delta W| = max |delta L| = 0`); see `paired_dimensions.json`.

The independent direct-GDS topology CLI now also passes invariant B against the
original `.lvs.sp`, together with A against the original RCC, in 730.25 seconds:
`ics55_work/runs/topology_repair_strict_b/{topology.json,reference_map.json}`.
Both sides reduce only already-physical parallel MOS to 5884 devices; 50 top
ports are fixed, W/L tolerance is 1e-9 um, and 2357 net pairs have exact mapped
identities. Internal symmetry does not imply a unique semantic identity for
every remaining net, so ambiguous pairs are not used as guessed probes.

An earlier false native-graph mismatch was a checker bug: manually resetting
MOS4 S/D equivalence broke KLayout's built-in source/drain exchange symmetry.
A real two-MOS inverter graph with only D/S swapped compares equal by default
and unequal with that reset. The reset is removed; no graph connection was
changed to obtain the strict pass. Additional complete physical-witness
comparisons are retained under `ics55_work/runs/topology_repair_pass_xref/`.

### Both-port internal RCC probes: write enable is missing before sensing

A new SS/-40 C, 1.08 V, 2.5 ns RCC run saved both ports' internal paths on the
unchanged frozen inputs. It completed in 935.656 s and still failed 24/48 bits;
valid `tacc_ns` is null. This run adds internal evidence, not a replacement for
the archived failing baseline:
`ics55_work/runs/ngspice_rcc_debug_both_ports_2rw_sscold/`.

The selected failing reads are port 0 / address 0 / bit 1 and port 1 / address 1
/ bit 0, each following its cycle-0 zero-write. During the entire first write
window (2.5–5.0 ns), registered CS/WE remain valid-high. Both saved W_EN
points per port and the selected write-driver M2/M3 EN gate points stay below
5 mV. Table peaks cover both saved W_EN points, not every node on the RC net:

| Target | Minimum registered WE | Peak saved W_EN | Time W_EN > 0.864 V | Minimum storage Q |
|---|---:|---:|---:|---:|
| Port 0, addr 0, bit 1 | 1.04394 V | 3.911 mV | 0 ns | 1.07295 V |
| Port 1, addr 1, bit 0 | 1.04382 V | 4.888 mV | 0 ns | 1.07798 V |

The cycle-2 write window (7.5–10.0 ns) likewise has no valid-high interval at
`W_EN0:0` / `W_EN1:0` (peaks 4.057 / 5.107 mV). The frozen write-driver is
active-high on `en` (NMOS M2/M3 and complementary PMOS M6/M7 gating).
During the first write, selected NMOS EN gate peaks are below 4.889 mV while
selected PMOS ENB gates remain at 1.07856–1.08051 V. The observed failure is
already in write-control/enable, before sensing could recover the requested
zero. Do not infer an SA-only failure or retune the latch from the plateau.

At the sampled write-end/read-sample instants, representative saved VDD-grid
points are 1.07979–1.07995 V and ground-grid points are 0.092–0.118 mV.
These archived points are not literal MOS-body measurements: the reviewed
mapper replaces 36 old B-terminal labels with the actual emitted MOS B nodes.
Registered addresses match the two selected rows. At the first read sample,
Q remains about 1.0795/1.0796 V,
BL is high, and the outputs are 1.07829/1.07835 V instead of zero. S_EN is only
0.401/0.622 V at that instant; read-path margin is not independently cleared.

`probe_stage_summary.json`, `write_window_metrics.json`,
`expanded_write_enable_metrics.json` and `additional_stage_metrics.json`
retain the measurements; `probes.json` binds every saved vector to actual
nodes and per-port operations. All 24 points in the expanded enable report
retain exact semantic identity in the final invariant-B map. All 12 diagnostic
regressions pass, including nullable timing, cross-port write provenance,
per-port failing-bit selection and skewed per-port probe sampling. C-only,
RCC and longer-period controls remain necessary to isolate the parasitic
mechanism; this estimate is still uncalibrated and non-signoff.

### Reviewed topology-map and physical-probe acceptance

The post-review full `gds-topology` CLI completed in 1028.54 s with A and B
MATCH on the same frozen GDS/source/RCC:
`ics55_work/runs/topology_repair_reviewed/`. The emitted schema-v1 map has
`equivalent=true`, meaning A+B for an RCC target, not merely a completed
comparison or B alone. It retains 5884 matched reduced MOS, 2357 exact net
pairs and 165 ambiguous net pairs; ambiguous identities are not usable probes.

Actual negative-path checks distinguish completion from acceptance:

- An intentionally invalid reference against genuine frozen geometry now
  makes `gds-reference-topology` exit 1 instead of 0.
- A deliberately shorted emitted graph, with every original MOS retained,
  gives A false/B true and now exits 1 after 1026.74 s. Its schema-v1
  `equivalent=false` map and the archived B-only legacy map are both rejected
  by the current probe consumer. This is an invalid graph fixture, not
  physical PEX and not an analog result.
- Explicitly marking a selected device or net identity unusable makes the
  consumer reject it. These are fault injections, not a claim that the
  selected frozen SRAM identities are ambiguous.
- The optional separate full-CLI invalid-reference run timed out at 120 s;
  it is inconclusive, not a mismatch/pass proof. The completed cached-geometry
  invalid-reference check above is the exercised B-negative proof.

The reviewed real RCC map now resolves both archived targets, preserving
every distinct physical point of merged terminals rather than selecting one
member. Actual deck generation produced 394 named signals and 290 unique
saved vectors. A real ngspice-47 full-RCC operating point completed in
236.98 s: every requested vector is present and finite. This verifies node
and waveform transport only, not SRAM read/write behavior or access time.
The log retains negative-Dvt0 and cold junction-capacitance-clamp model
warnings; dynamic gmin stepping completed.

Evidence is under `ics55_work/runs/reviewed_probe_contract_smoke/`.
All 60 archived gate bindings are unchanged; the 36 changed bindings are
exclusively B terminals. Thus the bounded saved-gate write-enable diagnosis
above is retained, but old B labels must not be used as MOS-body voltages.
All 13 graph/RC and 12 simulator regressions pass with ResourceWarning
treated as an error. No calibrated-PEX or signoff claim follows.

### Hierarchical TG mux DRC repair

The original frozen `fresh_latest_1rw_tgate_baseline` GDS and its 133-marker
report remain unchanged. The repaired macro uses the same configuration
bytes (`1cf47811c4f17ef1512ae635fba667bd190b2db4d1bda7ecde0fd8164860772d`),
including N-size 8/P-size 12; no mux sizing or sense timing was retuned.

| Actual generated surface | Raw DRC | Waived | Unwaived |
|---|---:|---:|---:|
| TG leaf, 3.05 x 11.8 um | 0 | 0 | 0 |
| Two bare adjacent leaves, actual 3.05 um pitch | 0 | 0 | 0 |
| Routed eight-column mux4 array, first attempt | 9 | 0 | 9 |
| Same array after landing repair | 0 | 0 | 0 |
| Fresh full 1RW 256x8 mux4 macro | 0 | 0 | 0 |

All checks use pinned KLayout 0.30.7, full hierarchy and the macro DRC profile.
The leaf PMOS supply drop was moved away from its select pad; M3 output
tracks now cover both via2 endpoints. The array lifts its VDD via stack away
from the original M2 signal-pad and M3-track conflicts.

The first routed-array check exposed a further real M1 violation: its
forced horizontal minimum-area landing reaches x=1.90 um while a leaf M1
riser reaches x=1.84 um, leaving only 0.06 um against the 0.09 um rule.
Moving the via right would crowd the neighboring M2 track. Removing only
the redundant M1 landing leaves the connected 0.17 um vertical strap to
supply M1 area; automatic intermediate-M2 area remains. Actual finalized
GDS gives 0.105 um M1 clearance and unchanged 0.14/0.11 um M2 clearances.
The nine-marker failing array and the before/after crops are retained under
`ics55_work/tg_hierarchy_repair/`.

Fresh full-macro generation completed in 315.07 s; its separate DRC check
completed in 11.265 s:

- GDS: `ics55_work/runs/out_fresh_repaired_1rw_tgate/fresh_repaired_1rw_tgate.gds`
- GDS SHA-256: `4bea3dd536f80b1d5885bc256816d727d9d215fa8c290441bdbe50333887ff65`
- Original generated reference SHA-256:
  `bfc064988335ff018d594eb250bc97acb4bbae29e51438d20c8a78fb630fbb0a`
- Report: `signoff/pinned_klayout_0.30.7/drc/drc.lyrdb`
- Manifest: `signoff/pinned_klayout_0.30.7/drc_repair_manifest.json`

These are DRC results only; LVS is an independent gate. The macro profile
does not establish chip-fill/provisional-antenna closure, Calibre or foundry
signoff. Finalizer area warnings are retained; none were converted into DRC
waivers.

## 冻结 2RW 的 C-only 对照与浮空金属初始条件

`frozen_2rw_c_only_controls/` 直接复用原 GDS geometry 和原 estimated process
JSON；没有把估算耦合系数改成“已接受”输入。C-only 网表 SHA256 为
`93eea732056dbe851cf07314c958d3ad46e2fa134b79a988d9d5e4c6f1626dd8`：
0 R、21088 C，ground 6.986037868384938 pF、coupling 0.6710490159323117 pF。
逐电容名称、数值、端点 lineage 和完整 R 连通分区审计证明它是原 RCC 的
理想电阻极限，而非另一套寄生参数；重新生成的完整 RCC parasitics 与冻结文件相同。
`all_capacitor_partition_audit.json` 保留审计，原生 topology CLI 的 A/B 均 MATCH
（684.98 s）。

默认 SS/-40°C C-only 运行保存在
`ngspice_c_only_2rw_sscold_controls/`：623.28 s 后初始 DC 失败，报
`xsram.n358` singular matrix，数据行数 0。这不是 24/48 功能结果，也没有可用延迟。
几何和网表诊断识别出 n357–n360、n383–n386 共 8 条纯电容浮空金属网：
无 MOS terminal、无端口；不指定其初始状态时，DC 无唯一解。

另存的显式初始电压诊断只对这 8 个纯寄生 R 连通分量施加普通 `.ic ...=0`，
不用 UIC、不加泄放电阻、不删除 C、不强制 MOS/storage/port 节点。
每个目录的 `initialization_plan.json` 记录所选实际节点及假设；C/RCC 网表 SHA
不变。这是额外场景，不替代默认失败，也不是“零电荷”假设。
小电容控制电路实测浮空节点随 1.08 V 激励升至 0.54 V 后回落到 0；
SRAM 中这些节点也产生非零响应，确认 `.ic` 没有在 transient 中钳位。

| 显式浮空金属 0 V 初始电压场景，2.5 ns、1.08 V、5 fF | 位检查 | 有效 tacc | 原生耗时 |
|---|---:|---|---:|
| C-only SS/-40°C | 24/48 失败 | `null` | 154.95 s |
| C-only SNFP/+125°C | 24/48 失败 | `null`；0.959070050 ns 仅为无效功能结果中的输出翻转诊断 | 263.26 s |
| RCC SS/-40°C，同样的浮空金属初始电压 | 24/48 失败 | `null` | 781.45 s |

这些场景均有 1112 行有效 transient 数据。结果在
`ngspice_c_only_2rw_{sscold,snfp}_float_ic/`；`simulation.json` 使用生产
`check_waveforms`，`control_window_metrics.json` 保留逐点线性插值的控制窗口分析。
因此在该明确初始电压场景，单独加入 C 已足以重现 2.5 ns 功能失败，
不能将失败全部归给串联 R，也不能用“未校准”将其略去。

首个写窗口 2.5–5 ns，SS 两端口的 WE/CS 均有效，但
`RBL_BL_DELAY_BAR` 峰值分别只有 0.773522351/0.328716232 V，
没有超过 0.8 VDD 的三输入写使能重叠窗口；`W_EN0/1` 峰值仅
3.264815/3.547455 mV。SNFP 的 `W_EN0/1` 虽能升高，但超过 0.8 VDD
已在 4.729373/4.753983 ns。该结果证明存在 replica/control/write 时序失效，
但不等于所有读错都由写失败解释；较长周期 RCC 对照仍需独立完成。

相同初始电压约定下的 RCC SS/2.5 ns 也复现 24/48 失败，保存于
`ngspice_rcc_2rw_sscold_control_float_ic/`。C-only SS 和该 RCC 的两个实际
write-enable NAND 各三个输入 gate 均没有共同的 >0.8 VDD 窗口；
不仅是某个长网代表点的推测。原无初始条件 RCC 失败和本次结果均保留。

初始 storage 未被强制，其稳定状态随场景变化：显式初始电压的 SS C-only/RCC
中，port0 目标 Q 从约 0 V 开始且首读时仍为低；SNFP 的 port1 目标 Q 也从低
开始。这些目标仍读出错误高电平，不能套用原无初始条件 RCC 中“两目标均未写低”
的解释。`read_path_metrics.json` 和 `sense_pulse_metrics.json` 另存实际读路径。
SS C-only port0 的实际 SA tail gate 在 6.842079–7.391805 ns 高于 0.8 VDD；
上升阈值时 BL/BR 仍为 1.076868/1.075332 V，至下降阈值才变为
0.439307/1.073171 V，DINT 却保持 0.911950–1.079288 V。
所以读末低 S_EN 不能证明整个读窗口没有脉冲；保存波形显示 sense 启动时
位线尚未建立正确差分。这个观测要求继续检查 read/control 时序，不支持直接
把全部失败归于串联 R、写驱动或 SA 尺寸。

### 同一冻结 RCC 的较长周期控制

保留相同网表、1.08 V、5 fF、原 trace、15 ps 最大步长和上述浮空金属初始
电压约定，只把周期改为 10 ns。SS/-40°C 原生仿真 2177.58 s，4235 行数据，
48/48 位检查通过，有效 `tacc=7.728257761 ns`；目录为
`ngspice_rcc_2rw_sscold_10ns_float_ic/`。这里 tacc 从读周期上升沿计时，
包含等到下降沿的 5 ns；不是 7.728 ns 的下降沿到输出延迟，也不是最小周期。

首写窗口中，两个实际三输入 NAND 的共同高窗口分别为
15.330079–17.258642 ns 和 15.336130–17.383970 ns；W_EN0/1 都能达到全摆幅。
首读中 port0/1 的正确 50 mV 位线差分分别在 25.741100/25.822447 ns 建立，
实际 SA tail gate 到 27.475664/27.608760 ns 才超过 0.8 VDD，此时差分约
1.082341/0.967596 V。50 mV 只是明确的观测阈值，不是已表征的 SA 最小差分。
这与 2.5 ns 场景中写使能缺失及 sense 早于有效差分的观测相互约束：
SS 失败确实依赖控制/时序窗口，而不是 MOS 图错误或不可恢复的固定输出。
SNFP/+125°C 同条件 10 ns 控制也已独立完成：1445.96 s、4235 行，
48/48 位通过，有效 `tacc=6.443746684 ns`（同样从上升沿计时并包含 5 ns 等待）。
目录为 `ngspice_rcc_2rw_snfp_10ns_float_ic/`。首写实际 NAND 共同高窗口为
15.285662–16.825444 / 15.290564–16.921183 ns；W_EN0/1 的 0.8 VDD 窗口为
15.566874–17.066008 / 15.579055–17.164007 ns。
首读正确 50 mV 差分在 25.611453 / 25.671569 ns 建立；
tail gate 的 0.8 VDD 观测时刻为 26.997005 / 27.093864 ns，此时正确差分为
1.058321 / 1.011434 V。0.8 VDD 不是 MOS 开通阈值，不能据此代替完整 sense 波形。
因此完整 failure 原件、未收敛默认场景和各受控 deck 都保留；不能把
“10 ns 可运行”外推到最小周期、原始 initialization 或模型校准。

### SNFP 同初始条件短周期复测

为排除 2.5/10 ns 两次控制间的浮空节点初值混杂，短周期对照复用 SNFP/125°C
10 ns 原件的全部八个 `.ic` 电压、RC 网表、输入 trace、负载和向量，只把周期
设为 2.5 ns。原生 RCC 566.04 s，15.625 ns 仿真结束；读取检查失败，48 位中
24 位失败，`tacc=null`，实际观察到的输出转换为 0.967366 ns。

失败输出对应的两个目标 bit 在 4.99 ns 时，cell Q 仍为高电压，虽 trace 期望写 0；
同一窗口物理 NAND gate 的 0.8 VDD 高区间仅为 port0 4.462745–5.0 ns、
port1 4.484458–5.0 ns，W_EN 到 0.8 VDD 更晚（4.744202 / 4.773552 ns），
写终点采样仅有 10 ps 余量。10 ns 控制在对应 19.99 ns 结束时 storage Q 已为低。
实际 0.8 VDD NAND/W_EN 区间仅是波形观察阈值，不等于 MOS/bitcell 写窗口的
电气定义；信号分叉与 cycle/trace 写入语义须继续核查，不能据此宣称路径根因。
逐周期结果、哈希、控制窗口和浮空节点波形保存在
`ngspice_rcc_2rw_snfp_control_float_ic/`。这是新的确定性短周期失效复测；
`24/48` 的具体写语义及方向尚未完整定位，也不能把失败归咎于 PEX 拓扑。
严格 native LVS 回归也已在冻结原始 2RW 宏上完成：411.99 s，50 个直接顶层
端口的 layout/reference 集合相同，`ICS55_LVS_COMPARE=PASS`；报告和抽取网表在
`out_fresh_final_2rw_baseline/signoff/strict_topology_klayout_0.30.7/lvs/`。
这不是 TG 完整宏的 verdict；TG 最终宏严格搜索在 3600.26 s 超时，仅留下
48487 行预比较抽取网表，没有 report/log/verdict，因此仍 inconclusive。实际短周期
结果使用同一受控初始状态，记录在 `ngspice_rcc_2rw_snfp_matched_period_comparison.json`。

## TG LVS 独立根因：write-driver 电源提升点触碰边缘 bitline

两个完整 TG GDS 的原生 LVS 在修复 finger reduction 后仍失败：
冻结原件 13.55 s，新 DRC0 宏 13.52 s。两次实际命令均已使用
`no_well_ties=true`；不能把差异归于默认 `WELL_TIES`。独立 CT/M1–M5/V1–V4
抽取不连接任何 label、bulk 或 MOS annotation，结果是 leaf/pair/mux-array
电源分离，而两个完整宏的电源真实短接，见
`tg_hierarchy_repair/metal_rail_connectivity.json`。

精确 merged-metal/via 路径与原始 shape owner 查询定位到 write-driver array：
GND 电源提升点 `(150.8, 11.63)` 和 VDD 提升点 `(150.8, 16.73)` 的 M2
landing 左边界均为 x=150.715 µm，恰好触碰边缘 bitline 的右边界
x=150.715 µm；并非 label 误并网。路径和所属 cell 保存在
`metal_rail_short_path.json`、`metal_short_shape_owners.json`、
`metal_short_m2_carrier.json`。纯同层导体接触不一定产生 spacing DRC，
所以完整宏 DRC0 没有排除这个错误。

`compiler/modules/write_driver_array.py` 保留原 leaf-layer M1 电源轨，
只把供父层提升的端点放到阵列左右各一个 M2 routing pitch 之外；
当前工艺为 0.29 µm。不移动 bitline，不改变器件 W/L。
真实两 driver、mux4 小层级包含父层实际 `copy_power_pins`：
修复前两电源同一物理分量，修复后分离，且原生 DRC0、LVS matched。
证据在 `write_driver_power_promotion_smoke.json` 和
`write_pg_after/opensource/`。完整宏必须重新生成并分别验证 DRC/LVS；
这个小层级通过不替代完整宏结论。

上述 generic 修复后的完整宏另存为 `out_fresh_power_repaired_1rw_tgate/`，
GDS SHA256 `424b5bf73353f0f65505908283ca192489a2596713f2d296e76535c4edd45159`。
独立金属图确认 gnd/vdd 已分离，完整原生 DRC 为 0/0/0，但 LVS 仍失败；
`power_fixed_remaining_lvs_mismatch.json` 和 model population 审计保留后续问题，
没有把电源分离当成完整 LVS 通过。

### TG leaf 与阵列中的另外两类短路

原 bare pair 的独立 LVS 抽取显示 `bl_0|bl_1|gnd`，且每个 BR pass MOS 的
D/S 同网。实际几何原因分别是 inverter NMOS 的 M1 GND 下引线穿过 BL 输入
M1 jog，以及 BR 输入从左侧 diffusion 拉到右侧 bitline 时穿过自己的输出
diffusion pad。修复把 inverter NMOS 放在 GND 邻侧，并让 BR 输入使用右侧
diffusion；W/L、PMOS 位置、leaf 宽高和逻辑功能不变。

修复 leaf 后，其原生 DRC/LVS 和 bare pair 的原生 DRC/LVS 均通过。
阵列独立 LVS 又揭示 `br_out_0|br_out_1|vdd`：原 VDD 提升点 x=2.03 µm
覆盖了调整后的 BR-output M2 riser（x=2.045 µm）。把接点移至该高度的空闲
M2 窗口 x=2.58 µm 后，八列 mux4 阵列也通过原生 DRC/LVS。

| 当前修复层级 | raw/waived/unwaived DRC | 原始生成 source 的 LVS | 精确 model/W/L 配对 |
|---|---|---|---:|
| leaf | 0/0/0 | matched | 6/6 |
| 裸相邻 pair，3.05 µm pitch、不镜像 | 0/0/0 | matched | 12/12 |
| 八列 mux4 阵列 | 0/0/0 | matched | 48/48 |

证据目录分别为 `tg_leaf_electrical_fix/`、`tg_abutted_pair_electrical_fix/`、
`tg_array_8col_vdd_access_fix/`（均在 `tg_hierarchy_repair/`）。
`electrical_hierarchy_exact_dimensions.json` 确认全部器件 model 相同且 W/L 差值为
0；没有靠电源 alias、short 或放宽尺寸比较修补。当前阵列 GDS SHA256 为
`2fcca5eb0277e1f983843430985d2cc94031f1c579d94dc2d0e33ed452395815`。
最终完整宏已重生为 `out_fresh_electrical_repaired_1rw_tgate/`（261.10 s），
GDS SHA256 `3a9fd7c6a26a24116f85b5243c96dcd00184f14e016acac22e5bcbdc2895d174`，
source SHA256 `4409b43a5861ff549d508e246a8d27fdde00409899eb4e1550dd971b988e717a`。
与冻结原始 source 的严格 native 图比较匹配全部 16150 MOS、6133 nets、
29 个声明端口；独立 BEOL 图也确认电源分离。完整原生 DRC 为 0/0/0。
原默认 LVS 搜索运行至 3600.25 s 超时，没有最终 verdict；这是 inconclusive，
既不是 LVS pass，也不是新的物理 mismatch。

### 原生 LVS 的严格端口与尺寸比较

额外的小电路反例发现：给真实六 MOS leaf 的 reference 增加一个未连接
`ghost` 端口，旧默认 native LVS 仍 matched，因为 simplify 删除了该声明边界。
OpenRAM runner 现请求 PDK `strict_topology=true`：禁用 simplify/purge，
仅合并物理并联 MOS，要求 model/W/L 的 1e-9 µm 绝对、零相对公差，并把
同名顶层端口对应的 nets 作为强制比较约束；不连接或 alias 任何物理 net。
抽取 SPICE 在比较前保存，超时也不会丢掉待诊断的图。

当前真实 leaf/pair/array 在新 native 模式仍 matched（6/12/48 MOS）；
上述 `ghost` 反例现在 mismatch，真实 CLI 返回 1。另一个单 MOS W 增加
8% 的反例在默认 foundry-library 10% 模式可接受，在新严格模式正确失败。
证据在 `tg_hierarchy_repair/native_strict_compare/`；一般标准单元直接运行
PDK deck 的默认宽度公差未改。完整 TG 宏的严格 native verdict 仍需独立完成；
其严格重跑同样在 3600.26 s 超时，仅留下 48487 行预比较抽取网表，
没有 report/log/verdict。冻结原始 2RW 宏则已单独严格通过，见上方短周期复测段。
