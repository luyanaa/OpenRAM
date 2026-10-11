tech_name = "icsprout55"
bitcell = "pbitcell"
analytical_delay = True
nominal_corner_only = True
check_lvsdrc = False
use_nix = False
word_size = 128
num_words = 32
stdcell_library = "SC9T"
num_rw_ports = 0
num_r_ports = 1
num_w_ports = 1
# Skip the OpenRAM supply router (hours for 128-bit words); the SoC power
# grid joins the exported vdd/gnd pins.
route_supplies = "pins"
