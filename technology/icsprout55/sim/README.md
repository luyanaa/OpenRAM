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

`ngspice_pex.py` defaults to `--initialization uninitialized`: no `.ic`, UIC,
shunt, or ground alias is added for floating conductors. Use
`--initialization zero-voltage` only with a matching RCC parasitics metadata
record; it writes `initialization_plan.json` and applies `.ic V(xsram.node)=0`
to the inventoried floating metal nodes inside the `XSRAM` PEX instance. This
specifies voltage only: metadata with a specified or non-unknown initial charge
is rejected, because extraction never determines either state.
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
parasitic-only components. The generated `.ic` targets are hierarchical
(`xsram.<net>`), because the extracted macro is instantiated as `XSRAM` in the
transient deck. The initialized path remains an ordinary released `.ic`
condition: it does not add UIC, shunts, or MOS/storage/port forcing. Metadata
with a specified or non-unknown initial charge is rejected; do not interpret
initial voltage as zero capacitor charge.

## Capability-gated control-timing search

`python -m compiler.architecture` enumerates exact admitted implementation
tuples, replays matching local observations, and optionally executes new
operating points with `--execute`. It does not invoke the remote wrappers.
Commands and the supplied registry/request are documented in
[the technology README](../README.md#capability-gated-architecture-timing-search).

The adapter uses the selected physical write-driver EN and sense-amplifier
tail-gate terminals, not only a representative logical control waveform:

- `wen_pulse_ns`: minimum complete single EN pulse above 0.8 VDD. No pulse
  yields zero; clipped or chopped pulses remain unknown.
- `bl_diff_v`: signed BL-minus-BR differential at each observed SA activation,
  with polarity chosen for the expected bit. Wrong-polarity differential is
  negative, not an absolute-value success.
- `sa_setup_ns`: SA 0.5 VDD activation minus readiness at the requested signed
  BL differential. An observed early SA is negative; never-observed readiness
  remains unknown.
- `write_recovery_ns`: the next actual access edge on either port minus the
  last local EN release below 0.2 VDD. A late release is negative.
- Selected WL delay requires an observed rising crossing. A gate already high
  at the access-window start is `already_high` with unknown delay, not 0 ns.
  Aggregate write margins require sampled writes on every declared write port,
  independently of read-port coverage.

Read/preceding-write targets, physical probes and sampled cycles remain in
the result. WL/replica delays, Q completion and output settling are diagnostic
details, not full-array stability or Monte Carlo characterization. Research
thresholds must be explicit in the request. Missing energy or other requested
objectives prevents a complete Pareto point; estimated extraction never
establishes physical feasibility or foundry signoff.

New native adapter runs save `model-dependencies-before.json` before launching
ngspice and an `observation-manifest.json` after verifying unchanged model/tool
bytes. The latter binds the model closure to exact inputs, executed deck,
initialization, probe columns, run result and waveforms. Registered bundles use
`models__<observation>` and, when metadata differs, `extraction__<observation>`.
Missing observation-time model provenance is unknown; do not manufacture a
posthoc manifest for an archived passing run.

Cache fingerprints hash current transitive dependency bytes, including the native
LVS comparison reader. Historical captured producer hashes are not assertions
that those paths still contain old code: admission and evidence consumers,
not dependency discovery, validate declared hashes and eligibility. Changed
reports invalidate cache keys and remain unknown when consumer bindings fail.
After analysis-code changes, read-only characterization replay can publish a
new analysis manifest from the exact registered raw bundles with
`--ngspice /must-not-execute` and without `--execute`. It preserves original
waveforms and observation-time model manifests; it does not invent historical
execution provenance or authorize a different period/load.



## Measured multi-port Liberty

`tbgen.py` reads the `// Port N: RW|R|W` comments on the generated behavioral
model. Its `C` trace records include both `roleN=RW|R|W` and `opN=I|R|W`; the
generated Verilog testbench drives the corresponding `csbN` and `addrN` values
on every cycle before the clock edge; `charz.py` uses those fields to classify
operations; pure read and write ports need no `webN` pin. The generated
`tbgen.py` directory also contains `trace.meta.json`, a deterministic sidecar
recording model dimensions, port-role metadata, seed, cycle count, clock period,
and the rule that each `C` record is applied before that cycle's rising edge.
Existing `trace.txt` readers remain the source of simulation inputs and continue
to accept traces without the sidecar. Re-generate traces with `tbgen.py` before
characterizing a pure R or W port.

`charz.py` computes polarity-specific read delay/10–90% transition, signed
whole-macro supply energy and clock-active idle energy. Stable outputs do not
become zero-delay observations. Every output bit must exhibit both directions
before a complete rise/fall table is available. Standalone Spectre extraction
reports `functional: null`; it does not manufacture a passing waveform verdict.

The local, provenance-bound path uses the native ngspice checker and admits
both loads independently:

```sh
python3 technology/icsprout55/sim/characterize.py \
    technology/icsprout55/architecture_capabilities.json \
    technology/icsprout55/configs/ics55_2rw_characterization.json \
    --out-dir ics55_work/runs/new_characterization --execute --timeout 18000
```

The request selects one exact sizing tuple, PVT, period, slew, initialization
and fidelity. `--loads-ff 5 50` is the default pair; neither load substitutes for
the other. Native jobs run serially. Without `--execute`, only exact registered
current-capture bundles can be replayed. A nonzero launcher exit or incomplete
bundle is never accepted, even if `simulation.json` looks successful; failed
attempts and `adapter-launch.log` remain in the output directory.

`characterization.json` binds each candidate/load to the raw run, waves, typed
probes, deck, initialization, extraction, execution-time model closure and
measurement source hashes. `measurements.json` contains schema-3 `m5`/`m50`
samples for the exporter. Replay recomputes the selected-load numeric sample
and validates the other load's references; changing tuple, load, PVT, period,
slew, initialization or fidelity cannot relabel an observation.

An operation-energy window runs from its rising clock edge to the next rising
edge. Other ports must stay deselected with stable electrical inputs across
the previous/current/next cycles; the measured port's next-cycle electrical
inputs must equal its current inputs. That next half-period update lies inside
the integral. Use real held-input operation cycles, including masks/spare
enables, rather than attributing preparation of the next operation to this one.
Read/write aggregates require every eligible port; unsupported operations are
not published on pure R/W ports. Energy includes background clocks and output
load charging; it is not intrinsic pin power. Idle is whole-macro clock-active
idle, not static leakage. Functional failure clears objective timing/energy.

Only an explicit **incomplete diagnostic** Liberty table export is supported:

```sh
python3 technology/icsprout55/sim/mklib.py measurements.json \
    ics55_2rw_32x8_d9_f4_w075 out/diagnostic_lib \
    --words 32 --bits 8 --area "$AREA_UM2" --stdcell-library SC9T \
    --port-roles 0:RW,1:RW --ports 0,1 --power-pins VDD,VSS \
    --diagnostic-table-only
```

The tables retain the two measured loads and one measured slew/PVT. A polarity
is emitted only when its delay and transition are measured on every output bit
at both loads; otherwise both tables for that direction are explicitly omitted,
never filled with zero or copied from the other polarity. A read port with no
complete measured polarity rejects export. The current native run observes
falling-clock `cell_fall`/`fall_transition`, but no post-fall output rise; its
`cell_rise`/`rise_transition` remain unknown. See `../SIM_RESULTS.md`.
All diagnostic tables set `dont_use: true`; they are not usable STA/F4 closure.
`package_macro.py` likewise requires explicit `--diagnostic-table-only`.
No guessed DFF setup/hold, clock pulse minima, minimum period, input/clock
capacitance, static leakage or intrinsic pin power is emitted. A passing tested
period is not Tmin. Complete export remains unavailable without waveform-
verified macro boundary measurements, measured input/clock loading and accepted
nonestimated physical evidence. Self-declared JSON brackets are not proof.
The optimizer has an independent `characterization` gate; diagnostic samples
leave it unknown even if DRC/LVS/PEX later pass.

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
