"""Explicit native 2R1W register-file cell configuration.

This configuration describes the cell/netlist boundary only. OpenRAM's native
bank and port-data layout currently supports at most two physical ports, so a
three-port full macro must be rejected by the register-file adapter rather
than silently treated as an SRAM or replicated implementation.
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

# The standalone smoke emits the pbitcell netlist and never enters bank layout.
netlist_only = True
analytical_delay = True
nominal_corner_only = True
check_lvsdrc = False
use_nix = False
output_name = "native_2r1w_pbitcell"
