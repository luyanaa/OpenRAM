"""Native 1W+2R (2R1W) register-file macro on icsprout55.

The three physical ports are built natively (one write-only port followed by
two read-only ports); this is not a replicated two-copy SRAM.  The macro also
exposes a physical ``write_conflict`` output, tied to gnd because a single
write port cannot have a same-row write conflict.
"""

tech_name = "icsprout55"
bitcell = "pbitcell"
stdcell_library = "SC9T"

word_size = 8
num_words = 32

# CPU-facing contract: two independent reads and one independent writer.
register_file_backend = "native-pbitcell"
register_file_port_roles = ("R", "R", "W")

# OpenRAM's canonical physical ordering is RW, W, R.  This backend uses one
# write-only port followed by two read-only ports; the role map is explicit so
# callers do not infer it from signal names.
register_file_compiler_port_roles = ("W", "R", "R")
register_file_compiler_port_map = {
    "write": 0,
    "read0": 1,
    "read1": 2,
}

num_rw_ports = 0
num_w_ports = 1
num_r_ports = 2

# The native 1W+2R bank is implemented; build the full macro, not just the
# pbitcell netlist.
netlist_only = False
control_logic = "control_logic_delay"
ics55_control_logic = "control_logic_delay"
words_per_row = 1
analytical_delay = True
nominal_corner_only = True
check_lvsdrc = False
use_nix = False
output_path = "/grid/OpenRAM/ics55_work/2r1w_native/"
output_name = "ics55_2r1w_32x8"
functional = False
# The behavioral model now exposes the extra write_conflict port; the
# analytical-delay path does not need a SPICE-characterized .lib/datasheet.
liberty = False
datasheet = False
