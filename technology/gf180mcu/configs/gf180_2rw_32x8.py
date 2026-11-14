# gf180mcu 3.3 V, 2 read/write ports, 32 words x 8 bits.
#
# Parameterized multiport bitcell (pbitcell): gf180mcu has no multiport or
# second-port hard cell, so the storage array is drawn from the technology's
# parameterized devices (custom/gf180_pbitcell.py + the 6T_* sizing keys in
# tech/tech.py).  words_per_row=1 keeps the column mux out of the entry, like
# the icsprout55 2rw baseline.
tech_name = "gf180mcu"
bitcell = "pbitcell"
word_size = 8
num_words = 32
num_rw_ports = 2
num_w_ports = 0
num_r_ports = 0
words_per_row = 1

nominal_corner_only = True
# DRC/LVS are run through gf180_work/drc_top.sh + the Magic/Netgen harness so
# that the extracted top cell is checked the same way as the 1RW macros.
check_lvsdrc = False
use_nix = False

output_name = "gf180_2rw_32x8"
