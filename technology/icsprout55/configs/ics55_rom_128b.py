"""icsprout55 via/contact-programmed mask ROM, 128 bytes x 8 bits."""
tech_name = "icsprout55"
stdcell_library = "SC9T"
bitcell = "pbitcell"
num_rw_ports = 1
word_size = 1          # bytes per word (8 bits)
rom_data = "/tmp/opencode/rom_data.bin"
data_type = "bin"
words_per_row = 4
strap_spacing = 8
check_lvsdrc = False
route_supplies = True
# The two supply rings must not be nested on the same layer pair: with both
# rings on m3/m4 a via at a crossing point bridged vdd to gnd (LVS reported
# one net carrying both labels).  Draw vdd's ring on m4 and gnd's on m3, with
# no cross-layer corner vias.
supply_ring_net_layers = ("m3", "m4")
use_nix = False
output_name = "ics55_rom_128b"
output_path = "/grid/OpenRAM/ics55_work/rom_128b/"
