#!/usr/bin/env python3
"""Drop parasitic well-diode cards (parasitic_nwd/rwd/dnwd) from a Calibre
xRC HSPICE netlist: they have no simulation model and only add reverse-biased
well-to-substrate junctions on the supplies."""
import sys
src, dst = sys.argv[1], sys.argv[2]
lines = open(src).read().split("\n")
out, card = [], []
drop = 0
def flush():
    global drop
    if card:
        text = " ".join(card)
        if any(m in text.split() for m in ("parasitic_nwd", "parasitic_rwd", "parasitic_dnwd")):
            drop += 1
        else:
            out.extend(card)
for l in lines:
    if l.startswith("+"):
        card.append(l)
    else:
        flush()
        card = [l]
flush()
open(dst, "w").write("\n".join(out))
print("dropped", drop, "diode cards")
