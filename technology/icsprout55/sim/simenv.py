"""Site settings for the Spectre/xRC harness (same variables as ../verify).

ICS55_WORK          work area; sweeps go to $ICS55_WORK/sim/<tag>, macros are
                    looked up in $ICS55_WORK/runs/out_<macro>
ICS55_LOCAL_ROOT    local prefix of a folder the simulation host also sees ...
ICS55_VM_ROOT       ... under this prefix
ICS55_SSH           ssh arguments for the simulation host
ICS55_SPECTRE_MODEL Spectre model library (icsprout_55llulp1225_v1p1_spe.lib),
                    as seen from the simulation host
"""
import os

WORK = os.environ.get("ICS55_WORK", os.path.join(os.getcwd(), "ics55_work"))
LOCAL_ROOT = os.environ.get("ICS55_LOCAL_ROOT", "/home/petergu/LoongSoC")
VM_ROOT = os.environ.get("ICS55_VM_ROOT", "/mnt/hgfs/LoongSoC")
SSH = ["ssh", "-F", "/dev/null", "-o", "LogLevel=ERROR"] + \
      os.environ.get("ICS55_SSH", "-i /home/petergu/.ssh/id_rsa_windows user@172.16.142.129").split()
MODEL = os.environ.get("ICS55_SPECTRE_MODEL",
                       VM_ROOT + "/Shuttle/official_downloaded/ICsprout_55LLULP1225_1P6M_5Ic_1T4Mc_RDL1_v1p11/"
                       "model/spectre/icsprout_55llulp1225_v1p1_spe.lib")


def VM(p):
    p = os.path.abspath(p)
    assert p.startswith(LOCAL_ROOT), p
    return VM_ROOT + p[len(LOCAL_ROOT):]
