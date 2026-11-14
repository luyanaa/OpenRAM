# gf180mcu 3.3 V, 1 write port + 1 read port, 32 words x 8 bits.
#
# The write-only port drives bl0/br0 of the parameterized dual-port bitcell;
# the read-only port has its own wordline and a single-ended read device over
# bl1/br1.  Same tech-local pbitcell family as gf180_2rw_32x8.
tech_name = "gf180mcu"
bitcell = "pbitcell"
word_size = 8
num_words = 32
num_rw_ports = 0
num_w_ports = 1
num_r_ports = 1
words_per_row = 1

nominal_corner_only = True
check_lvsdrc = False
use_nix = False

output_name = "gf180_1w1r_32x8"
