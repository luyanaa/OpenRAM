# gf180mcu 3.3 V, 1 read/write port + 1 read-only port, 32 words x 8 bits.
#
# Same parameterized multiport bitcell family as gf180_2rw_32x8.  Port 0 is a
# full RW port (write driver + sense amp); port 1 is read-only (sense amp on
# its own bitline pair).  This entry is functionally a superset of 1W+1R and
# uses the RW-port top-level routing path that is exercised by the 1RW flow.
tech_name = "gf180mcu"
bitcell = "pbitcell"
word_size = 8
num_words = 32
num_rw_ports = 1
num_w_ports = 0
num_r_ports = 1
words_per_row = 1

nominal_corner_only = True
check_lvsdrc = False
use_nix = False

output_name = "gf180_1rw1r_32x8"
