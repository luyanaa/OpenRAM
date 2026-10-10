# ICsprout55 Calibre verification harness

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
