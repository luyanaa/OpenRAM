"""Native 1W+2R 32x8 prototype: three physical ports on icsprout55.

The third port is placed on the bottom/left below port 0; the prototype uses
words_per_row=1 (no column mux) and the delay control logic (no replica
bitline) to isolate the 3-port bank placement from the RBL/periphery work.
"""
tech_name = "icsprout55"
bitcell = "pbitcell"
stdcell_library = "SC9T"
word_size = 8
num_words = 32
num_rw_ports = 0
num_w_ports = 1
num_r_ports = 2
words_per_row = 1
control_logic = "control_logic_delay"
# ics55_* keys are copied even when a default option exists.
ics55_control_logic = "control_logic_delay"
netlist_only = False
check_lvsdrc = False
analytical_delay = True
nominal_corner_only = True
use_nix = False
output_name = "ics55_1w2r_32x8"
output_path = "/grid/OpenRAM/ics55_work/1w2r_32x8/"
