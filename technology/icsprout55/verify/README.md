# ICsprout55 verification

Runs the foundry Calibre decks on a remote host (here the RHEL8 EDA VM) over
ssh, with the work area on a folder both machines see.

```sh
# once: copy and edit the decks (see mkdecks.sh for the exact edits)
technology/icsprout55/verify/mkdecks.sh TAPE.drc TAPE.lvs OS.drc OS.lvs OS_subckt.sp ics55_work

export ICS55_WORK=$PWD/ics55_work            # shared folder
export ICS55_LOCAL_ROOT=/home/me/share ICS55_VM_ROOT=/mnt/hgfs/share
export ICS55_SSH="-i ~/.ssh/key user@vm" ICS55_CALIBRE=/path/to/calibre
export ICS55_MGLS_LICENSE_FILE=28000@127.0.0.1

# a whole macro: compile, then DRC + LVS on both decks
venv/bin/python technology/icsprout55/verify/macro.py my_config.py
# OpenRAM leaf modules (generated, finalized, DRC + LVS)
venv/bin/python technology/icsprout55/verify/leaf.py 'pinv(size=2)' 'sense_amp()'
# a GDS you already have
venv/bin/python technology/icsprout55/verify/cal.py my.gds TOP --cdl my.sp
# where are the DRC results (rule, deepest instance)?
venv/bin/python technology/icsprout55/verify/where.py $ICS55_WORK/runs/macro_X my.gds
```

`leaf.py` runs LVS with `VIRTUAL CONNECT NAME vdd gnd` (block supply rails
are joined by the parent's power grid); `macro.py` never does.

`leaf.py` uses the generic PDK LVS profile for standalone generated cells.
`macro.py` uses `openram-macro`, which passes `no_well_ties=true` and
`strict_topology=true`: retain declared unused ports, combine only physically
parallel devices, compare MOS model/W/L at 1e-9 um absolute and zero relative
tolerance, and bind every same-named top pin's nets as mandatory comparison
correspondences. This never joins nets. The extracted netlist is saved before
comparison so a search timeout preserves the graph for diagnosis.
Both `opensource.py verify` and `macro.py` default to `--lvs-mode flat`; this is
the existing strict comparison with a flattened schematic. The generic profile
remains the default for direct `opensource.py verify` and passes no profile
flags. Hierarchical comparison is opt-in only for `openram-macro`.
`--lvs-mode hierarchical` preserves the schematic hierarchy and requires an
exact one-to-one case-insensitive set of circuit names and named boundary pins
for every extracted/reference circuit. All corresponding pin nets are mandatory
native comparison constraints; duplicate or mismatched module/boundary
correspondence fails closed as `INCONCLUSIVE` (or a deck error), never as a
pass. Deep extraction can materialize empty helper circuits absent from the CDL;
those are intentionally not discarded or aliased, so the hierarchy smoke can be
inconclusive until the source CDL exposes the same boundaries. There is no
automatic fallback to flat: use `--lvs-mode flat` explicitly to select the strict
flat path. No full-macro runtime improvement is claimed until a full hierarchical
run completes.
Generic standard-cell LVS keeps the deck's default global-tie and 10%-W modes.

## Open-source KLayout DRC/LVS and OpenRCX

The optional runner uses `../icsprout55-pdk` and KLayout from the
`../librelane` Nix shell. Its legacy `shell.nix` reads `<nixpkgs>`, so provide
the nixpkgs revision pinned by `../librelane/flake.lock`:

```sh
NIXPKGS_URL=https://github.com/NixOS/nixpkgs/archive/b3aad468604d3e488d627c0b43984eb60e75e782.tar.gz
# Compile a macro and run full-hierarchy KLayout DRC + LVS
nix-shell -I "nixpkgs=$NIXPKGS_URL" ../librelane --run 'venv/bin/python technology/icsprout55/verify/macro.py technology/icsprout55/configs/ics55_1rw_64x8.py --decks klayout --klayout-mode full'

# OpenRAM macro strict boundary mode; generic standalone-cell checks retain the
# PDK's global-well-tie and 10%-W comparison defaults.
nix-shell -I "nixpkgs=$NIXPKGS_URL" ../librelane --run 'venv/bin/python technology/icsprout55/verify/opensource.py verify macro.gds TOP --netlist macro.lvs.sp --out-dir runs/opensource --pdk-root ../icsprout55-pdk --librelane-root ../librelane --mode full --lvs-profile openram-macro'

# Optional strict hierarchy experiment; defaults to strict flat comparison.
nix-shell -I "nixpkgs=$NIXPKGS_URL" ../librelane --run 'venv/bin/python technology/icsprout55/verify/macro.py technology/icsprout55/configs/ics55_1rw_64x8.py --decks klayout --klayout-mode full --lvs-mode hierarchical'

# Explicitly limited top-cell interconnect screen; this does not inspect child geometry
nix-shell -I "nixpkgs=$NIXPKGS_URL" ../librelane --run 'venv/bin/python technology/icsprout55/verify/opensource.py verify macro.gds TOP --no-lvs --mode top-interconnect'
```

Full mode checks the complete hierarchy. `top-interconnect` creates a GDS
containing only direct top-cell shapes and disables FEOL, density, antenna,
and MOM checks; it is not a full-macro DRC result. Both open decks are
provisional screening decks derived from released GDS/LEF, not foundry
signoff decks. Keep the Calibre flow for signoff.

The PDK deck uses PORT DEPTH 0 for direct top-cell conductor labels, so internal
power labels do not become external ports; only a case-equivalent parsed root
circuit name is normalized.

This checkout supplies Calibre tapeout/OS decks only; no Assura runset is
present in the supplied PDK `pv/` tree or this verification directory.

Known vendor DFF internals are handled as DRC waivers, not by changing library
geometry or excluding every child cell. The default `drc_waivers.json` matches
an exact owning cell, category and marker coordinates in the cell's local
frame. It currently records one ACT-width marker reproduced in the original
`DFFQX1_9TSVT` and two M1 minimum-area markers from the imported `DFFQX1H7R`.
The standalone vendor cell remains unwaived when it is the report's top cell. New coordinates,
OpenRAM wrappers, generated circuitry and cross-cell findings stay unwaived.
Other 7T cells need their own reviewed marker entries; a 7T library name alone
is not a waiver. Existing Calibre 7T internal findings in `PORT_STATUS.md`
remain known vendor exceptions, not KLayout category names to copy blindly.

The runner preserves `drc.lyrdb` and writes `drc_summary.json` with raw,
waived and unwaived counts by rule and owning cell. `--drc-waivers FILE`
selects an explicit manifest for both `opensource.py verify` and `macro.py`.
An empty `{"version": 1, "waivers": []}` manifest disables waivers.
Counts are report-marker multiplicities, not the number of placed instances;
a deep report can store one reusable leaf marker for many DFF placements.
To classify an existing report without rerunning the deck:

```sh
.venv/bin/python technology/icsprout55/verify/opensource.py drc-report \
  ics55_work/runs/opensource_2rw_wpr1/drc/drc.lyrdb \
  --summary ics55_work/runs/opensource_2rw_wpr1/drc/drc_summary.json
```

### KLayout/Calibre rule cross-check

The routefix2 2RW macro-profile report
(`ics55_work/runs/recheck_routefix2_2rw/drc/drc_summary.json`) had 42
raw/unwaived markers: 8 `metal.m1.space`, 18 `metal.m2.space`, and 16
`via.v1.enc.lower`. The report owners are limited to the top macro, its
write-driver array, and the generated write-driver cell.

The released OS Calibre deck's mandatory bodies set M1 spacing to 0.09 µm,
M2 spacing to 0.10 µm, and V1 lower enclosure to either 0.04/0.04 µm on
opposite sides or 0.03 µm on all sides. The corresponding routefix2 markers
measure 0.06 µm, 0.095 µm, and missing lower-metal enclosure. `SRAMALL` is
not drawn in the macro, so its foundry exceptions do not apply; optional
recommended spacing checks are disabled by default. The 16 V1 markers came
from the redundant via on the M2-only `din` input; the current source removes
that via and retains the `din` pin.

These map to mandatory OS-deck geometry rules, not harmless KLayout-only
checks. An older Calibre table cannot certify a later layout variant. KLayout
is provisional screening, not foundry sign-off.

No current Calibre run was performed for this candidate. `cal.py` defaults
to mapping `/home/petergu/LoongSoC` to `/mnt/hgfs/LoongSoC`, while this checkout
is `/Users/yanlu/Documents/OpenRAM`; no working shared-root/SSH mapping is
configured here.

The retained full-hierarchy **diagnostic-profile** report for
`ics55_2rw_32x8` (`words_per_row = 1`) contains **4,940 raw / 1 waived /
4,939 unwaived** markers. The category-level comparison with the released OS
Calibre deck and the KLayout rule sources is:

| Diagnostic group | Raw markers | Rule-deck comparison |
|---|---:|---|
| Density + antenna | 471 + 4 | Disabled in the KLayout macro profile; density is chip-fill (`Density_rules NO` in the macro Calibre flow), and the KLayout antenna limit is provisional. |
| M1/M2 spacing | 8 + 18 | Mandatory OS Calibre thresholds are 0.09/0.10 µm; routefix2 measures 0.06/0.095 µm. |
| Active area/width | 5 + 1 | `ACT_A_1` uses the same 0.0384 µm² threshold; the one `ACT_W` marker is the exact vendor-DFF marker covered by the existing waiver. |
| NP/PP width/spacing | 3,162 | The 0.18 µm thresholds match the OS deck; the KLayout implementation explicitly marks `ABUT`/`OPPOSITE` qualifiers provisional. |
| V1/V2/V3 enclosure | 1,271 | Core lower (0/0.04 µm opposite pairs or 0.03 µm all sides) and upper (0.005/0.02 µm opposite pairs) patterns match; complex-cut and exclusion semantics remain provisional in KLayout. |

Sources: `../icsprout55-pdk/pv/DRC/ICsprout_CalDRC_55LLULP1233_REV1_0_OS.drc`,
`../icsprout55-pdk/libs.tech/drc/rule_decks/ics55_rules.json`,
`../icsprout55-pdk/libs.tech/drc/rule_decks/feol/02_active.drc`,
`../icsprout55-pdk/libs.tech/drc/rule_decks/feol/04_implant.drc`, and
`../icsprout55-pdk/libs.tech/drc/rule_decks/beol/07_via.drc`. The finalizer
places regenerated implants at top level, so report ownership alone does not
prove a macro-interconnect error. Do not convert diagnostic counts into
Calibre failures or broad waivers without matching geometry and rule
qualifiers.

Seven markers are reported under `DFFQX1_9TSVT`, but only one is reproduced
by the original cell. Its imported GDS has no VIA1 shapes: the other six
`via.v1.enc.lower` markers involve wrapper-created vias and remain unwaived.
Report ownership by itself is insufficient evidence of a vendor-internal
exception.

DRC waivers do not change LVS. The retained `opensource_2rw_wpr1` report is
from an earlier 2RW candidate: it found 5,854 extracted versus 5,884
reference MOS devices, with unmatched devices in generated write
drivers/decoders and data DFFs. Its flattened top-circuit comparison cannot
localize all mismatches to waived vendor leaves. This historical mismatch is
specific to that earlier candidate; the final M4 reroute is reported below.
The report is under `ics55_work/runs/opensource_2rw_wpr1/`.

The earlier `routefix5_2rw` candidate had 0 raw, waived or unwaived markers in
its full-hierarchy macro-profile DRC report
(`ics55_work/runs/recheck_routefix5_2rw/drc/drc_summary.json`), but KLayout LVS
reported a mismatch (`ics55_work/runs/recheck_routefix5_2rw/lvs/lvs.log`).
Its extraction had 5,854 MOS records, the same count as routefix2; this was
not an LVS-equivalence result, and its DRC does not apply to the M4 reroute.

The pinned `../librelane` Nix shell recheck of
`ics55_work/runs/out_routefix5_2rw/routefix5_2rw.gds` with KLayout 0.30.7
reported 0 raw macro-profile DRC markers and an LVS mismatch (wrapper exit 1).
The extracted top-level `gnd` and `vdd` ports both resolve to `gnd|vdd`.
Reports are retained under
`ics55_work/runs/opensource_nix_recheck_routefix5_2rw/{drc,lvs}/`:
`drc_summary.json` records zero markers and `lvs.log` records the compare
failure. This is the observed result for that artifact only; it does not
explain the mismatch or change the separate final M4 reroute report below.
No Calibre comparison was available.

A separate `ics55_work/runs/opensource_audit_current` directory retains a
0-marker DRC summary and an LVS-match log, but these files do not name the
source GDS or KLayout executable. They do not establish that the retained
pass used the same input or pinned tool as the rerun above.

The final M4 reroute passes the production OpenRAM/KLayout LVS flow with
physical body taps and PORT DEPTH 0. Its retained report is
`ics55_work/lvs_alignment/openram_m4_verify_pdk/lvs/lvs.log`. The extracted
netlist also passes exact MOS-graph comparison against the original SPICE
reference: 5,884 MOS, 2,522 nets, 50 pins and 23,536 terminal edges; W/L match
within 1e-12. The final reroute has no valid DRC result, and there is no current
Calibre or Assura signoff.

The separate routefix5 1RW16×4 regression has 0 KLayout macro-profile DRC
markers but KLayout LVS reports a mismatch. The earlier KLayout 16×4 report
also mismatched, while the historical Calibre table in `PORT_STATUS.md`
records `CORRECT` on both decks. This is an observed KLayout/Calibre
divergence, not proof of a false positive; no current Calibre run was
available. Reports are under
`ics55_work/runs/out_routefix5_1rw/signoff/macro_routefix5_1rw/opensource/`.

Verification of waiver handling: four regression tests cover marker identity,
generated/top-cell boundaries, malformed counts and wildcard rejection. An
actual full-hierarchy KLayout run with both vendor DFFs and deliberately bad
generated M1 geometry produced 6 raw / 3 waived / 3 unwaived markers and failed
as expected. The three unwaived markers are generated M1 width/spacing errors;
reports are under `ics55_work/runs/waiver_scope_smoke/`.

Optional interconnect RCX consumes the PDK's normalized routed-geometry JSON
(`design`, `nets`, `ports`, `segments`, `vias`; coordinates in micrometres):

```sh
venv/bin/python technology/icsprout55/verify/opensource.py pex routed_geometry.json --out-dir runs/pex
# Or include it in a compile/check run:
venv/bin/python technology/icsprout55/verify/macro.py my_config.py --decks klayout --pex-json routed_geometry.json
```

The OpenRAM GDS is not converted to this JSON: a route database/geometry
adapter must provide it. LEF-seeded RCX omits coupling and reports
`seed_only`; this SPEF is an estimate, not signoff parasitics or a transistor
extraction netlist. `--pex-require-coupling` fails unless explicit coupling
models are supplied via the PDK RCX process JSON.

### GDS-derived transistor-level PEX

`gds-pex` extracts core MOS devices, BEOL rectangles/vias, named ports, and an RCC parasitic network directly from the selected GDS top cell:

```sh
python technology/icsprout55/verify/opensource.py gds-pex macro.gds \
  --top macro_top --pdk-root /path/to/icsprout55-pdk \
  --out-dir runs/gds_pex --source pdk-estimate --corner typ --mode rcc
python technology/icsprout55/sim/ngspice_pex.py \
  runs/gds_pex/macro_top_typ.pex.sp trace.txt \
  --pex-summary runs/gds_pex/pex_summary.json \
  --pdk-root /path/to/icsprout55-pdk --out-dir runs/gds_pex/ngspice \
  --period 2.5 --corner ss --vdd 1.08 --temp -40
```

Select the PEX netlist whose corner matches the summary and keep the summary
with it; the simulator verifies both the netlist hash and input GDS hash.
Repeat the ngspice command with the desired device-model corner and temperature
(for example, `--corner snfp --vdd 1.08 --temp 125`).

This source-free extraction is distinct from routed-geometry JSON RCX. It is
limited to supported core MOS and BEOL geometry. FEOL diffusion/poly/contact
resistance and capacitance are idealized; uncontacted FEOL-only terminals are
represented as ideal nodes, and repeated shapes of one top-level port connect
to its named SPICE node. The `pdk-estimate` source uses generator coefficients,
not calibrated coupling data: output is `status=estimated`,
`accepted=false`, `signoff=false`. Artifact production can therefore complete
while the command returns nonzero; this is not foundry signoff or a Calibre
substitute.

### MOS topology diagnostics before RC interpretation

Check the same frozen GDS against its original LVS netlist and the emitted RCC:

```sh
python technology/icsprout55/verify/opensource.py gds-topology macro.gds \
  macro.lvs.sp --top macro_top --rcc-netlist macro_top_typ.pex.sp \
  --out-dir runs/gds_topology
python technology/icsprout55/verify/opensource.py gds-rc-topology \
  runs/gds_topology/geometry.json macro_top_typ.pex.sp \
  --out-dir runs/ideal_wire_check
```

Invariant A collapses every emitted resistor edge and omits capacitors, then
checks MOS model/W/L, D/G/S/B connections and top ports against direct GDS
extraction. Invariant B compares that GDS MOS graph to the original flattened
reference with strict W/L (1e-9 um absolute tolerance), fixed top-pin identity
and the built-in MOS4 D/S symmetry. Only already-physical parallel devices
may be reduced; reference widths never justify joining diffusion or supply nets.

`topology.json` records the individual verdicts and overall `equivalent`.
Topology commands return nonzero on a required mismatch; `completed` means
artifact emission, not equivalence. A schema-v1 `reference_map.json` binds
semantic nets/devices to actual nodes of the exact emitted netlist hash.
Its `equivalent=true` requires A and B for an RCC target, or B for MOS-only.
Selected records must also have `usable_exact_identity=true`; ambiguous
candidates are diagnostic only, never usable probes. Merged MOS terminal
lists retain every distinct physical point rather than an arbitrary member.
Legacy maps without this overall contract must be regenerated.

The independently emitted MOS-only netlist has no R/C cards and uses
`mos_summary.json` with the local ngspice adapter. Emission is not proof of
equivalence; all these artifacts remain non-signoff and unaccepted for PEX
calibration purposes.

### Fresh final-configuration evidence

These reports are from two isolated builds of the current source configurations.
The copied config inputs preserve SRAM geometry and functional parameters and set
`check_lvsdrc=False`, `use_nix=False`; the build invocations supply unique
output names/paths so verification can be run separately on each resulting GDS.

| Candidate | Source config SHA-256 | GDS SHA-256 | Pinned KLayout 0.30.7 full-hierarchy result |
|---|---|---|---|
| 2RW `fresh_final_2rw_baseline` | `208e7b9d21846c998044e6b64b1b7e6bbb53242092676e4cc8c8f7628bbf0875` | `f3b92dbb37273630e61fbfbc2670dbfc9e5e7d7fe326f6958170fd836484622e` | DRC 0 raw / 0 waived / 0 unwaived; initial LVS mismatch, repaired-deck LVS match on the same GDS |
| 1RW/TG `fresh_latest_1rw_tgate_baseline` | `1cf47811c4f17ef1512ae635fba667bd190b2db4d1bda7ecde0fd8164860772d` | `3288e7d2bd0aa6ce8fc5060b539101a2bb828a8f0d567d1ca937159465887829` | DRC 133 raw / 0 waived / 133 unwaived; LVS mismatch |

Source configurations: `technology/icsprout55/configs/ics55_2rw_32x8.py`
and `technology/icsprout55/configs/ics55_1rw_256x8_mux4_tgate.py`.
Input wrapper configs and SHA-256 values:
`ics55_work/runs/fresh_final_2rw_baseline.py`
(`7e7508af4989b9e831fedad5647631208671add7b86c88a178970748f543cf6d`, 2RW) and
`ics55_work/runs/fresh_latest_1rw_tgate_baseline.py`
(`5150504aac217929ea74b8c9c01c151751bc1f7f4f222ef23887fb6bd94831ed`, 1RW/TG).

OpenRAM's emitted config snapshots are
`ics55_work/runs/out_fresh_final_2rw_baseline/fresh_final_2rw_baseline_cfg.py`
(SHA-256 `c9b1e69f05c17eb1d832d811b5839a63c98e6fa99fa30a860c00afe39350a469`) and
`ics55_work/runs/out_fresh_latest_1rw_tgate_baseline/fresh_latest_1rw_tgate_baseline_cfg.py`
(SHA-256 `4b98133947f734e76bef15934f4eccdb0d33b74930e04491e541cf7730aa66b8`).

The exact GDS files and reports are under
`ics55_work/runs/out_fresh_final_2rw_baseline/` and
`ics55_work/runs/out_fresh_latest_1rw_tgate_baseline/`; initial DRC/LVS reports
are under `signoff/pinned_klayout_0.30.7/`. The 1RW/TG markers are M1 spacing 33,
M2 spacing 66, M3 spacing 33, and V2 upper cover 1. Both initial LVS runs failed;
the frozen reports remain intact.
The repaired 2RW LVS report is under `signoff/parallel_only_klayout_0.30.7/`.
Removing CDL-guided S/D net joining eliminated the synthetic `gnd|vdd` alias;
all 5884 matched reduced MOS have exactly equal W/L. The independent direct
GDS topology CLI also passes A and B at `ics55_work/runs/topology_repair_strict_b/`
(6042 raw MOS; 5884 physically reduced matched devices; 50 top ports).

Calibre was skipped as optional for this pass; this local environment has no
Calibre executable, local decks, or configured license. No Calibre DRC/LVS
result is claimed for either candidate.
