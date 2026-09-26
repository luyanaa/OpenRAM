#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# ICsprout55 technology setup for OpenRAM.

"""Configure the ICsprout55 PDK integration without loading analog models.

The OpenRAM technology module is self-contained for analytical generation.  The
external PDK path is only used by the optional KLayout DRC/LVS adapters.
"""

import os
from pathlib import Path


TECHNOLOGY = "icsprout55"


def _find_pdk_root():
    """Return the configured PDK root when it is available."""
    configured = os.environ.get("ICSPROUT55_PDK") or os.environ.get("ICS55_PDK")
    if configured:
        root = Path(configured).expanduser().resolve()
        if not root.is_dir():
            raise SystemError(
                "ICSPROUT55_PDK does not point to a directory: {}".format(root)
            )
        return root

    pdk_root = os.environ.get("PDK_ROOT")
    if pdk_root:
        for name in ("icsprout55-pdk", "icsprout55"):
            candidate = Path(pdk_root).expanduser() / name
            if candidate.is_dir():
                return candidate.resolve()

    # This is the layout used by the OpenRAM checkout in the porting guide.
    candidate = Path(__file__).resolve().parents[2].parent / "icsprout55-pdk"
    if candidate.is_dir():
        return candidate

    return None


_pdk_root = _find_pdk_root()
if _pdk_root is not None:
    # The KLayout runset adapters use this path.  Deliberately do not set an
    # analog model-directory override: the released cards are not fitted.
    os.environ["ICSPROUT55_PDK"] = str(_pdk_root)
