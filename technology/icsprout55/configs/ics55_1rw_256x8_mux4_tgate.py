tech_name = "icsprout55"
bitcell = "pbitcell"
analytical_delay = True
nominal_corner_only = True
check_lvsdrc = False
use_nix = False
word_size = 8
num_words = 256
words_per_row = 4
stdcell_library = "SC9T"
num_rw_ports = 1
num_r_ports = 0
num_w_ports = 0

# MUX-A uses style="nmos" and ics55_column_mux_nmos_size (default 8).
# MUX-B uses complementary NMOS/PMOS transmission gates.
ics55_column_mux_style = "tgate"
ics55_column_mux_nmos_size = 8
ics55_column_mux_pmos_size = 12
