"""OpenRAM configuration for the ICsprout55 H7CH HVT standard-cell library."""

word_size = 1
num_words = 16
tech_name = "icsprout55"
stdcell_library = "H7CH"

# The ICsprout55 analog cards are not fitted.  Keep generation analytical.
analytical_delay = True
nominal_corner_only = True

# Run physical verification explicitly from the librelane nix-shell.
check_lvsdrc = False
use_nix = False

output_name = "ics55_h7ch_sram"
