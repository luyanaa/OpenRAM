# See LICENSE for licensing information.
#
# Fit calculation and macro placement for the chip assembly layer.

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .models import ConfigError, MacroSpec, SlotSpec


@dataclass
class Placement:
    index: int
    row: int
    col: int
    x: float
    y: float
    orientation: str = "N"

    def to_dict(self):
        return {"index": self.index, "row": self.row, "col": self.col,
                "x": self.x, "y": self.y, "orientation": self.orientation}


@dataclass
class FitResult:
    cols: int
    rows: int
    count: int
    total_words: int
    total_bits: int
    address_bits: int
    sram_addr_bits: int
    select_bits: int
    utilization: float
    halo_um: float
    macro_width_um: float
    macro_height_um: float
    placements: List[Placement] = field(default_factory=list)

    @property
    def total_bytes(self):
        return self.total_bits // 8

    def to_dict(self):
        return {
            "cols": self.cols,
            "rows": self.rows,
            "count": self.count,
            "total_words": self.total_words,
            "total_bits": self.total_bits,
            "total_bytes": self.total_bytes,
            "address_bits": self.address_bits,
            "sram_addr_bits": self.sram_addr_bits,
            "select_bits": self.select_bits,
            "utilization": round(self.utilization, 6),
            "halo_um": self.halo_um,
            "macro_width_um": self.macro_width_um,
            "macro_height_um": self.macro_height_um,
            "placements": [p.to_dict() for p in self.placements],
        }

    def summary(self):
        return (
            "{count} x {mname} -> {cols} cols x {rows} rows, "
            "{words} words x {width} bits ({kib:.2f} KiB), "
            "addr {addr} bits (sram {sram_addr} + select {sel}), "
            "core utilization {util:.1f}%"
        ).format(
            count=self.count, mname="", cols=self.cols, rows=self.rows,
            words=self.total_words, width=self.total_bits // max(self.total_words, 1),
            kib=self.total_bytes / 1024.0, addr=self.address_bits,
            sram_addr=self.sram_addr_bits, sel=self.select_bits,
            util=self.utilization * 100.0)


def _is_power_of_two(value):
    return value > 0 and (value & (value - 1)) == 0


def _choose_grid(count, max_cols, max_rows, core_w, core_h, cell_w, cell_h, prefer):
    """Choose a cols x rows grid of at least ``count`` cells."""
    best = None
    for cols in range(1, max_cols + 1):
        rows = int(math.ceil(count / float(cols)))
        if rows > max_rows:
            continue
        used = cols * rows
        # Wasted cells, then preferred shape.
        waste = used - count
        if prefer == "rows":
            shape_score = cols
        elif prefer == "columns":
            shape_score = rows
        else:
            target = core_w / core_h if core_h else 1.0
            shape_score = abs((cols * cell_w) / (rows * cell_h) - target)
        score = (waste, shape_score)
        if best is None or score < best[0]:
            best = (score, cols, rows)
    if best is None:
        return None, None
    return best[1], best[2]


def calculate_fit(slot: SlotSpec, macro: MacroSpec, halo_um: float = 10.0,
                  count=None, rows=None, cols=None, prefer="square",
                  reserved_um2: Optional[float] = None) -> FitResult:
    """Calculate how many macros fit in a slot and where they go."""
    if reserved_um2 is None:
        reserved_um2 = slot.reserved_area_um2

    cell_w = macro.width_um + 2 * halo_um
    cell_h = macro.height_um + 2 * halo_um
    core_w = slot.core_width
    core_h = slot.core_height

    max_cols = int(core_w // cell_w)
    max_rows = int(core_h // cell_h)
    if max_cols < 1 or max_rows < 1:
        raise ConfigError(
            "macro '{}' ({} x {} um + {} um halo) does not fit slot '{}' "
            "(core {} x {} um)".format(macro.name, macro.width_um,
                                       macro.height_um, halo_um, slot.name,
                                       core_w, core_h))

    if count == "auto" or count is None:
        best_cols, best_rows = max_cols, max_rows
        # Trim for the reserved area (logo, chip ID, seal ring keep-out).
        while (best_cols * best_rows * cell_w * cell_h >
               core_w * core_h - reserved_um2 and
               (best_cols > 1 or best_rows > 1)):
            if best_cols >= best_rows and best_cols > 1:
                best_cols -= 1
            elif best_rows > 1:
                best_rows -= 1
            else:
                break
        cols, rows = best_cols, best_rows
        count = cols * rows
    else:
        if not _is_power_of_two(count):
            raise ConfigError(
                "count must be a power of two for address decoding, got {}".format(count))
        if rows is None and cols is None:
            cols, rows = _choose_grid(count, max_cols, max_rows, core_w, core_h,
                                      cell_w, cell_h, prefer)
        elif rows is None:
            rows = int(math.ceil(count / float(cols)))
        elif cols is None:
            cols = int(math.ceil(count / float(rows)))
        if cols is None or rows is None:
            raise ConfigError("could not fit {} macros in slot '{}'".format(
                count, slot.name))
        if cols > max_cols or rows > max_rows or cols * rows < count:
            raise ConfigError(
                "{} x {} macro grid does not fit slot '{}' (max {} x {})".format(
                    cols, rows, slot.name, max_cols, max_rows))
        if cols * rows != count:
            raise ConfigError(
                "grid {} x {} does not hold exactly {} macros; provide rows/cols "
                "or use count='auto'".format(cols, rows, count))

    used_area = count * macro.width_um * macro.height_um
    utilization = used_area / slot.core_area

    total_words = count * macro.words
    total_bits = total_words * macro.data_width
    address_bits = int(math.ceil(math.log2(total_words))) if total_words > 1 else 0
    sram_addr_bits = macro.addr_width
    select_bits = int(math.ceil(math.log2(count))) if count > 1 else 0

    placed_w = cols * cell_w
    placed_h = rows * cell_h
    origin_x = slot.inset.left + (core_w - placed_w) / 2.0 + halo_um
    origin_y = slot.inset.bottom + (core_h - placed_h) / 2.0 + halo_um

    placements = []
    index = 0
    for row in range(rows):
        for col in range(cols):
            placements.append(Placement(
                index=index, row=row, col=col,
                x=round(origin_x + col * cell_w, 3),
                y=round(origin_y + row * cell_h, 3),
                orientation="N"))
            index += 1

    return FitResult(
        cols=cols, rows=rows, count=count, total_words=total_words,
        total_bits=total_bits, address_bits=address_bits,
        sram_addr_bits=sram_addr_bits, select_bits=select_bits,
        utilization=utilization, halo_um=halo_um,
        macro_width_um=macro.width_um, macro_height_um=macro.height_um,
        placements=placements)
