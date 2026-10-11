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
  2. **Column-mux macros fail at SS -40 C** (reads return all ones): the
     NMOS-only column mux passes the high bitline at VDD-Vt and the sense
     amp's PMOS isolation devices cannot pass the low one.  Use
     `words_per_row = 1` until the sense amp is reworked.
  3. **2RW writes fail at SNFP 1.08 V 125 C** (slow N, fast P): the write
     driver only pulls down; the floating "high" bitline is discharged by the
     written cell itself, and the weak access NMOS cannot overpower the
     PMOS.  1RW and 1W+1R are fine on the same corner.
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
* The periphery (control logic, decoder, DFFs, sense amps, write drivers) is
  ~0.048 mm²; each extra bit costs ~19.4 um² (the pbitcell with 1.2 V logic
  rules plus its share of the wordline driver).
* `route_supplies = "pins"` builds 32x128 in 26 min (24 of them in the GDS
  finalizer); the full OpenRAM supply router took 3.1 h for 32x128 and did
  not finish 64x128 in 4 h.

Foundry compiler (Meminder, single-port 1RW, same PDK), for reference:
ICACHE_DATA_32X128 = 567.6 x 30.5 um = 0.0173 mm², 64X128 = 0.0196 mm²
(x1.13); minimum period 1.37 ns TT, 2.8-2.9 ns SS; access 1.08 / 2.24 ns.
The OpenRAM 1W+1R macro is 7.3x (32x128) to 10.5x (64x128) larger and
2.2-2.6x slower (TT 3.0 vs 1.37 ns, worst corner 7.5 vs ~2.9 ns), but it is
a true two-port memory built only from logic devices.
