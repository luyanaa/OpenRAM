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

## Local GDS-derived ngspice diagnostics

`ngspice_pex.py` runs locally; it does not invoke the SSH/Spectre wrappers
above. Keep its input netlist with the matching `pex_summary.json`, or
`mos_summary.json` for genuine MOS-only. For an established topology map,
enable targeted probes:

```sh
python3 technology/icsprout55/sim/ngspice_pex.py macro_typ.pex.sp trace.txt \
    --pex-summary pex_summary.json --out-dir debug_ss \
    --period 2.5 --corner ss --vdd 1.08 --temp -40 \
    --probe-profile debug-readwrite --probe-map probe_map.json \
    --failure-report previous_run/simulation.json --words-per-row 1
```

The map must use `schema_version=1`, `equivalent=true`, and match the exact GDS
and emitted netlist hashes. For RCC this overall verdict requires A and B;
MOS-only requires B. Regenerate legacy maps without the overall contract.
Its `nets` associate canonical flattened reference names with `logical_net`
and actual emitted `nodes`; its `devices` associate reference device names
with physical `terminals`. Every selected record must have
`usable_exact_identity=true`. Missing or ambiguous correspondences fail closed.
The failure report must match the GDS and trace; without one, the profile
selects the first expected-zero read (or first read) per read port. With one
it selects a reported failing bit per affected port. Each target is linked to
its most recent preceding write, including cross-port writes.

`probes.json` records per-port targets, node-map hash, physical nodes and waveform
columns. It includes actual external inputs, registered controls/address,
buffered/gated clocks, replica-bitline delay and complement, write-enable
generator MOS terminals, selected WL, write-driver nodes, target Q/QB,
BL/BR, sense-latch nodes and local device supply terminals. Global ground is
the ideal zero reference,
not a measured local-ground waveform. `simulation.json` adds samples at
write completion, the read edge and the read sampling point; full saved
waveforms remain in `waves.dat`.
Merged MOS terminal points are saved separately as `device:terminal[index]`;
no arbitrary first member replaces the physical group. Representative long-net
samples are not claimed to be physical endpoints; local device points are kept.

Functional failures always have `tacc_ns=null` and
`access_time_status="functional_failure"`. A correct output already at its
expected level is not a measured zero-delay transition. Raw crossings are
reported separately as `observed_transition_delay_ns`; they are not valid
performance measurements for a failing run. Neither a functional pass nor
normal solver termination makes estimated PEX calibrated or signoff-ready.

Capacitive-only floating metal has no unique DC potential. A failed initial
operating point is not a functional bit-check result. The frozen-input
C-only/RCC experiments in `../SIM_RESULTS.md` preserve the default failure
and separately record an explicit initial-voltage assumption for eight
parasitic-only components. Those ordinary `.ic` values are released for the
transient; no shunts, UIC, or MOS/storage/port forcing were used. Do not merge
such an initialized diagnostic into the default result or interpret initial
voltage as zero capacitor charge.

## Measured multi-port Liberty

`tbgen.py` reads the `// Port N: RW|R|W` comments on the generated behavioral
model. Its `C` trace records include both `roleN=RW|R|W` and `opN=I|R|W`.
`charz.py` uses those fields to classify operations; pure read and write ports
need no `webN` pin. Re-generate traces with `tbgen.py` before characterizing a
pure R or W port.

`charz.py` records per-port timing and energy from a `--diag` run. `--diag`
also saves each `XSRAM.XcontrolN.rbl_bl_delay` replica net. Use separate sweep
tags for the two output loads; each run otherwise reuses the same trace and
corner. Supply the selected standard-cell library so the measurement carries
the DFF-library provenance used for setup/hold:

```sh
S=technology/icsprout55/sim
STDCELL_LIBRARY=SC9T     # use the same value selected by the macro config
python3 $S/sweep.py ics55_2rw_32x8 --periods 8 --corners ss_1p08_m40 \
    --cycles 24 --tag char_m5 --cload 5 --tr 0.03 --diag
python3 $S/sweep.py ics55_2rw_32x8 --periods 8 --corners ss_1p08_m40 \
    --cycles 24 --tag char_m50 --cload 50 --tr 0.03 --diag
python3 $S/charz.py $ICS55_WORK/sim/char_m5/ss_1p08_m40_T8p0.raw \
    $ICS55_WORK/sim/char_m5/trace.txt --period 8 --vdd 1.08 --temp -40 \
    --corner ss --stdcell-library "$STDCELL_LIBRARY" --ports 0,1 \
    --load-ff 5 --input-slew-ns 0.03 > m5.json
python3 $S/charz.py $ICS55_WORK/sim/char_m50/ss_1p08_m40_T8p0.raw \
    $ICS55_WORK/sim/char_m50/trace.txt --period 8 --vdd 1.08 --temp -40 \
    --corner ss --stdcell-library "$STDCELL_LIBRARY" --ports 0,1 \
    --load-ff 50 --input-slew-ns 0.03 > m50.json
```

Assemble those samples under `m5` and `m50` in the `mklib.py` input schema,
with per-port `dff_setup` and `dff_hold` in ns and the corner's `vdd`/`temp`.
Keep each `charz.py` sample's root `stdcell_library` field and each port's
`role`; `mklib.py` rejects missing or mismatched library/role metadata. For a
2RW macro the port roles are `0:RW,1:RW`; a 1W+1R macro uses `0:W,1:R` when
those are the behavioral-model port indices.

```sh
python3 $S/mklib.py measurements.json ics55_2rw_32x8 out/lib \
    --words 32 --bits 8 --area "$AREA_UM2" --stdcell-library "$STDCELL_LIBRARY" \
    --port-roles 0:RW,1:RW --ports 0,1 --power-pins VDD,VSS
```

The Liberty pins and memory descriptions follow those port roles: RW exposes
read and write pins, R exposes only read pins/arcs, and W exposes only write
pins/arcs. Per-port setup/hold, read delay, write-enable, and energy metrics
remain separate; unavailable operations do not receive fallback RW arcs.
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
The behavioral guard samples each port's raw CSB/WEB and full address at its
positive clock edge; that write window remains pending through the port's
following negative edge. It compares physical rows after removing column-mux
select bits while retaining bank bits, and catches same-time start/completion
boundaries. Compile with `OPENRAM_ASSERTIONS` to make single-bank and multibank
models terminate with `$fatal` on a conflict. The regression in
`compiler/tests/25_verilog_write_window_test.py` covers distinct logical words
in one physical row, read-only overlap, different rows/banks, post-sample input
changes, different clock periods/phases, stopped-high/stopped-low clocks, and
both boundary scheduler orders. The guard does not arbitrate or repair writes.
