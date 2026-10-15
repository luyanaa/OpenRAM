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

## Open-source KLayout DRC/LVS and OpenRCX

The optional runner uses `../icsprout55-pdk` and KLayout from the
`../librelane` Nix shell. Its legacy `shell.nix` reads `<nixpkgs>`, so provide
the nixpkgs revision pinned by `../librelane/flake.lock`:

```sh
NIXPKGS_URL=https://github.com/NixOS/nixpkgs/archive/b3aad468604d3e488d627c0b43984eb60e75e782.tar.gz
# Compile a macro and run full-hierarchy KLayout DRC + LVS
nix-shell -I "nixpkgs=$NIXPKGS_URL" ../librelane --run 'venv/bin/python technology/icsprout55/verify/macro.py technology/icsprout55/configs/ics55_1rw_64x8.py --decks klayout --klayout-mode full'

# Or check an existing GDS/CDL pair
nix-shell -I "nixpkgs=$NIXPKGS_URL" ../librelane --run 'venv/bin/python technology/icsprout55/verify/opensource.py verify macro.gds TOP --netlist macro.lvs.sp --out-dir runs/opensource --pdk-root ../icsprout55-pdk --librelane-root ../librelane --mode full'

# Explicitly limited top-cell interconnect screen; this does not inspect child geometry
nix-shell -I "nixpkgs=$NIXPKGS_URL" ../librelane --run 'venv/bin/python technology/icsprout55/verify/opensource.py verify macro.gds TOP --no-lvs --mode top-interconnect'
```

Full mode checks the complete hierarchy. `top-interconnect` creates a GDS
containing only direct top-cell shapes and disables FEOL, density, antenna,
and MOM checks; it is not a full-macro DRC result. Both open decks are
provisional screening decks derived from released GDS/LEF, not foundry
signoff decks. Keep the Calibre flow for signoff.

OpenRAM macro LVS invokes the PDK deck with `no_well_ties=true`: MOS bulk
terminals follow CT-qualified physical taps instead of global VSS/VDD aliases.
Generic standard-cell LVS keeps the deck's default global-tie mode. The PDK
deck uses PORT DEPTH 0 for direct top-cell conductor labels, so internal
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
