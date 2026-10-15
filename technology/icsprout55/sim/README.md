# ICsprout55 macro simulation harness (Spectre + VCS + Calibre xRC)

Transistor-level functional/timing checks of OpenRAM macros, driven by a
realistic access sequence.  Runs on a remote EDA host over ssh (same
conventions as `../verify`, see `simenv.py` for the variables).  Results and
conclusions: `../SIM_RESULTS.md`.

Flow per macro:

1. `tbgen.py` writes a Verilog testbench around OpenRAM's behavioural model
   (`<macro>.v`): fills addresses with patterns, reads them back (concurrently
   with writes to other addresses on multi-port macros), read-after-write,
   overwrite with the complement.  VCS on the host (`vmrun.sh`) runs it and
   dumps `trace.txt` (`C k pin=val ...` inputs per cycle, `E k doutN=bits`
   expected read data).

The stimulus sequence includes an isolated read for every read-capable port
and an isolated write for every write-capable port before multi-port overlap
coverage; this supplies per-port energy windows to `charz.py`. Too few cycles
for that preamble and the two trailing idle cycles are rejected.

2. `mkscs.py` turns netlist + trace into a Spectre deck: corner model
   section (+ `pre_layout` for schematic netlists), supplies, a 50% duty
   clock (rising edge of cycle k at (k+1)T), inputs that change half a
   period before each edge, 5 fF on every dout.
3. `vmspectre.sh` runs classic Spectre (`errpreset=liberal`, `+mt`; within
   2 ps of the default accuracy on these circuits; APS asserts on them).
4. `check.py` samples every dout bit 10 ps before the rising edge that ends
   the read cycle and compares it with the trace.

```sh
export ICS55_WORK=$PWD/ics55_work ICS55_SSH="-i ~/.ssh/key user@vm" ...
S=technology/icsprout55/sim
# schematic netlist, all 9 corners, 3 periods, 4 parallel runs
python3 $S/sweep.py m1w1r_32x8 --periods 2,2.5,4 --jobs 4 --mt 2 --detach
sh $S/collect.sh $ICS55_WORK/sim/m1w1r_32x8 m1w1r_32x8   # check + table
# self-timing events (s_en, w_en, precharge end, dout settle) of a --diag run
python3 $S/sweep.py m1w1r_32x8 --periods 10 --diag --tag d_32x8 --detach
python3 $S/tanalyze.py $ICS55_WORK/sim/d_32x8/tt_1p2_25_T10p0.raw $ICS55_WORK/sim/d_32x8/trace.txt --period 10 --vdd 1.2
python3 $S/leak.py $ICS55_WORK/sim/d_32x8/ff_1p32_125_T10p0.raw --period 10 --vdd 1.32
# dout hold after the rising edge (any run)
python3 $S/hold.py $ICS55_WORK/sim/d_32x8/ff_1p32_m40_T10p0.raw $ICS55_WORK/sim/d_32x8/trace.txt --period 10 --vdd 1.32
```

Post-layout: build the macro (for big macros `route_supplies = "pins"`
avoids the slow supply router; extraction then needs `vc`), extract on the
Calibre host, drop the well diodes, simulate with `--postlayout`:

```sh
ssh ... sh $VM/technology/icsprout55/sim/vmpex.sh /dev/shm/pex_X X.gds X.lvs.sp X \
    $FOUNDRY/TYP $VM/ics55_work/decks c vc        # C-only: R adds ~2 %
python3 $S/clean_pex.py pex.sp pex_clean.sp
python3 $S/sweep.py X --sp pex_clean.sp --postlayout --periods 10 --diag --detach
```

Corners (`sweep.py CORNERS`): tt 1.2 V 25 C; ss 1.08 V 125/-40 C;
ff 1.32 V 125/-40 C; snfp/fnsp (slow N fast P / fast N slow P) at 1.08 V
125 C and 1.32 V -40 C.  The foundry corner sections set `scale=0.9`;
netlists are in drawn microns.

## Measured multi-port Liberty

`charz.py` records per-port timing and energy from a `--diag` run. `--diag`
also saves each `XSRAM.XcontrolN.rbl_bl_delay` replica net. Use separate sweep
tags for the two output loads; each run otherwise reuses the same trace and
corner:

```sh
S=technology/icsprout55/sim
python3 $S/sweep.py ics55_2rw_32x8 --periods 8 --corners ss_1p08_m40 \
    --cycles 24 --tag char_m5 --cload 5 --tr 0.03 --diag
python3 $S/sweep.py ics55_2rw_32x8 --periods 8 --corners ss_1p08_m40 \
    --cycles 24 --tag char_m50 --cload 50 --tr 0.03 --diag
python3 $S/charz.py $ICS55_WORK/sim/char_m5/ss_1p08_m40_T8p0.raw \
    $ICS55_WORK/sim/char_m5/trace.txt --period 8 --vdd 1.08 --temp -40 \
    --corner ss --ports 0,1 --load-ff 5 --input-slew-ns 0.03 > m5.json
python3 $S/charz.py $ICS55_WORK/sim/char_m50/ss_1p08_m40_T8p0.raw \
    $ICS55_WORK/sim/char_m50/trace.txt --period 8 --vdd 1.08 --temp -40 \
    --corner ss --ports 0,1 --load-ff 50 --input-slew-ns 0.03 > m50.json
```

Assemble those samples under `m5` and `m50` in the `mklib.py` input schema,
with `dff_setup` and `dff_hold` in ns and the corner's `vdd`/`temp`. The
samples carry their own load/slew/corner metadata; the generator rejects
mismatched axes. For example, a 2RW input sample has
`"ports": {"0": {...}, "1": {...}}`. Then generate all RW-port interfaces:

```sh
python3 $S/mklib.py measurements.json ics55_2rw_32x8 out/lib \
    --words 32 --bits 8 --area "$AREA_UM2" --ports 0,1 --power-pins VDD,VSS
```

The Liberty tables contain only the two measured loads and one measured input
slew; no interpolation or extrapolation is generated. `max_capacitance` is
the largest measured load. Use the library only inside the recorded slew/load
domain. `dff_setup`/`dff_hold` must come from the selected DFF's characterized
timing data, not the order-of-magnitude analytical estimates in `tech.py`.
The Liberty encodes measured clock high/low pulse minima, minimum period,
input setup/hold, and falling-edge read access. OpenRAM has no separate PPA
optimizer API; these standard Liberty constraints are the available handoff.

Concurrent writes to the same physical row have undefined data and no
priority, even when column-muxed logical addresses differ. The reference
`ics55_2rw_32x8` config sets `words_per_row = 1`, so distinct addresses occupy
distinct rows; only overlapping writes to the same address remain undefined.
Single-bank and multi-bank behavioral Verilog terminate with `$fatal` on
same-row write conflicts when compiled with `OPENRAM_ASSERTIONS`; packaged
black-box stubs document the same physical-row contract. The guard does not
arbitrate or repair the access.
