"""ICS55 wrapper preserving the default NMOS column mux with an explicit size knob."""
from openram import OPTS
from openram.modules.column_mux import column_mux as column_mux_base


class ics55_column_mux_nmos(column_mux_base):
    def __init__(self, name="column_mux", bitcell_bl="bl", bitcell_br="br"):
        tx_size = int(getattr(OPTS, "ics55_column_mux_nmos_size", 8))
        super().__init__(name=name, tx_size=tx_size,
                         bitcell_bl=bitcell_bl, bitcell_br=bitcell_br)
