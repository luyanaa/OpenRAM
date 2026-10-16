"""Column-mux array wrapper that supplies the optional CMOS TG cells."""
import math

from openram import tech
from openram.base import vector
from openram.base.utils import ceil
from openram.modules.column_mux_array import column_mux_array as column_mux_array_base
from openram.sram_factory import factory


def _rect_bounds(rect):
    (ll, ur) = rect
    return (float(ll.x), float(ll.y), float(ur.x), float(ur.y))


def _transform_rect(rect, inst):
    """Transform a local rectangle through one placed instance."""
    angle = math.radians(float(inst.rotate))
    mirror = 1
    if inst.mirror == "R90":
        angle += math.radians(90.0)
    elif inst.mirror == "R180":
        angle += math.radians(180.0)
    elif inst.mirror == "R270":
        angle += math.radians(270.0)
    elif inst.mirror == "MX":
        mirror = -1
    elif inst.mirror == "MY":
        mirror = -1
        angle += math.radians(180.0)
    elif inst.mirror == "XY":
        angle += math.radians(180.0)
    elif inst.mirror not in ("", "R0"):
        raise ValueError("Unsupported mux instance transform '{}'".format(inst.mirror))

    corners = inst.transform_coords(((rect[0], rect[1]), (rect[2], rect[3])),
                                    inst.offset, mirror, angle)
    return (min(corner[0] for corner in corners),
            min(corner[1] for corner in corners),
            max(corner[0] for corner in corners),
            max(corner[1] for corner in corners))


def _module_layer_rectangles(module, layer_name):
    """Return all drawn and pin rectangles on a layer in module coordinates."""
    lpp = tech.layer[layer_name]
    rectangles = []
    for shape in module.objs:
        if (getattr(shape, "layerNumber", None) != lpp[0] or
                getattr(shape, "layerPurpose", None) != lpp[1]):
            continue
        if shape.name == "rect":
            rectangles.append((float(shape.offset.x),
                               float(shape.offset.y),
                               float(shape.offset.x + shape.width),
                               float(shape.offset.y + shape.height)))
        elif shape.name == "path":
            half_width = 0.5 * shape.path_width
            for start, end in zip(shape.coordinates, shape.coordinates[1:]):
                rectangles.append((min(start.x, end.x) - half_width,
                                   min(start.y, end.y) - half_width,
                                   max(start.x, end.x) + half_width,
                                   max(start.y, end.y) + half_width))
        else:
            raise ValueError("Cannot search VDD landing around unsupported {} geometry on {}"
                             .format(shape.name, layer_name))

    for pins in module.pin_map.values():
        for pin in pins:
            if pin.lpp == lpp:
                rectangles.append(_rect_bounds(pin.rect))

    for inst in module.insts:
        rectangles.extend(_transform_rect(rect, inst)
                         for rect in _module_layer_rectangles(inst.mod, layer_name))
    return rectangles


def _axis_gap(a0, a1, b0, b1):
    return max(0.0, a0 - b1, b0 - a1)


def _rects_conflict(a, b, spacing):
    """Conservative rectangular same-layer spacing test."""
    return (_axis_gap(a[0], a[2], b[0], b[2]) + 1e-9 < spacing and
            _axis_gap(a[1], a[3], b[1], b[3]) + 1e-9 < spacing)


def _rects_overlap(a, b):
    return (min(a[2], b[2]) > max(a[0], b[0]) and
            min(a[3], b[3]) > max(a[1], b[1]))


def _same_rect(a, b):
    return all(abs(x - y) < 1e-9 for x, y in zip(a, b))


def _shift_rect(rect, x, y):
    return (rect[0] + x, rect[1] + y, rect[2] + x, rect[3] + y)


def _grid_floor(value, grid):
    return math.floor((value + 1e-9) / grid) * grid


def _grid_ceil(value, grid):
    return math.ceil((value - 1e-9) / grid) * grid


def _grid_snap(value, grid):
    return round(value / grid) * grid


def _grid_candidates(low, high, events, grid):
    """Sample every geometry-defined window boundary and each open window."""
    low = _grid_ceil(low, grid)
    high = _grid_floor(high, grid)
    if low > high + 1e-9:
        return []

    points = {low, high}
    for event in events:
        for value in (event - grid, event, event + grid):
            snapped = _grid_snap(value, grid)
            if low - 1e-9 <= snapped <= high + 1e-9:
                points.add(snapped)
    ordered = sorted(points)
    for left, right in zip(ordered, ordered[1:]):
        if right - left > grid + 1e-9:
            middle = _grid_snap(0.5 * (left + right), grid)
            if low - 1e-9 <= middle <= high + 1e-9:
                points.add(middle)
    return sorted(points)


class ics55_column_mux_array_tgate(column_mux_array_base):
    """Generic select/bitline routing plus an explicit VDD rail for TG muxes."""

    uses_vdd = True

    def add_pins(self):
        super().add_pins()
        self.add_pin("vdd", "POWER")

    def create_array(self):
        self.mux_inst = []
        for col_num in range(self.columns):
            name = "XMUX{}".format(col_num)
            self.mux_inst.append(self.add_inst(name=name, mod=self.mux))
            self.connect_inst(["bl_{}".format(col_num),
                               "br_{}".format(col_num),
                               "bl_out_{}".format(int(col_num / self.words_per_row)),
                               "br_out_{}".format(int(col_num / self.words_per_row)),
                               "sel_{}".format(col_num % self.words_per_row),
                               "gnd", "vdd"])

    def _vdd_via_footprints(self):
        """Build the exact M1/V1/M2/V2/M3 footprints used by add_via_stack_center."""
        via1 = factory.create(module_type="contact", layer_stack=self.m1_stack)
        via2 = factory.create(module_type="contact", layer_stack=self.m2_stack)
        footprints = {}
        for via in (via1, via2):
            for layer_name in via.layer_stack:
                relative = [(rect[0] - 0.5 * via.width,
                             rect[1] - 0.5 * via.height,
                             rect[2] - 0.5 * via.width,
                             rect[3] - 0.5 * via.height)
                            for rect in _module_layer_rectangles(via, layer_name)]
                footprints.setdefault(layer_name, []).extend(relative)

        # add_via_stack_center adds a minimum-area enclosure on the intermediate
        # M2 layer even when min_area=False.
        width = via2.first_layer_width
        height = via2.first_layer_height
        minimum_area = tech.drc("minarea_m2")
        if minimum_area:
            minimum_width = tech.drc("minwidth_m2")
            if tech.preferred_directions["m2"] == "V":
                height = ceil(max(minimum_area / width, minimum_width))
            else:
                width = ceil(max(minimum_area / height, minimum_width))
            footprints.setdefault("m2", []).append(
                (-0.5 * width, -0.5 * height, 0.5 * width, 0.5 * height))

        return via1, via2, footprints

    def _find_vdd_landing_x(self, inst, pin, pin_rect, y, footprints,
                            obstacles, spacings, strap_footprint, top_rail):
        """Find the leftmost grid point in any legal same-layer window."""
        all_footprints = [rect for rects in footprints.values() for rect in rects]
        low_x = max(inst.lx() - min(rect[0] for rect in all_footprints),
                    inst.lx() - strap_footprint[0])
        high_x = min(inst.rx() - max(rect[2] for rect in all_footprints),
                     inst.rx() - strap_footprint[2])
        grid = tech.drc("grid")
        low_x = _grid_ceil(low_x, grid)
        high_x = _grid_floor(high_x, grid)
        if low_x > high_x:
            return None

        blocked = []
        for layer_name, layer_footprints in footprints.items():
            spacing = spacings[layer_name]
            for obstacle in obstacles[layer_name]:
                if layer_name == pin.layer and _same_rect(obstacle, pin_rect):
                    continue
                for footprint in layer_footprints:
                    vertical_gap = _axis_gap(y + footprint[1], y + footprint[3],
                                             obstacle[1], obstacle[3])
                    if vertical_gap < spacing:
                        blocked.append((obstacle[0] - footprint[2] - spacing,
                                        obstacle[2] - footprint[0] + spacing))

        strap_y0 = pin.cy()
        strap_y1 = y + strap_footprint[3]
        if strap_y1 <= strap_y0:
            return None
        for obstacle in obstacles["m1"]:
            if _same_rect(obstacle, pin_rect):
                continue
            vertical_gap = _axis_gap(strap_y0, strap_y1,
                                     obstacle[1], obstacle[3])
            if vertical_gap < spacings["m1"]:
                blocked.append((obstacle[0] - strap_footprint[2] - spacings["m1"],
                                obstacle[2] - strap_footprint[0] + spacings["m1"]))

        merged = []
        for lo, hi in sorted(blocked):
            if hi < low_x or lo > high_x:
                continue
            lo = max(low_x, lo)
            hi = min(high_x, hi)
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))

        candidates = {low_x, high_x}
        cursor = low_x
        for lo, hi in merged:
            if lo >= cursor:
                candidates.add(_grid_snap(cursor, grid))
                candidates.add(_grid_snap(lo, grid))
                if lo - cursor > grid:
                    candidates.add(_grid_snap(0.5 * (cursor + lo), grid))
            candidates.add(_grid_snap(lo, grid))
            candidates.add(_grid_snap(hi, grid))
            cursor = max(cursor, hi)
        if cursor <= high_x:
            candidates.add(_grid_snap(cursor, grid))
            candidates.add(_grid_snap(high_x, grid))
            if high_x - cursor > grid:
                candidates.add(_grid_snap(0.5 * (cursor + high_x), grid))

        for x in sorted(candidates):
            if x < low_x - 1e-9 or x > high_x + 1e-9:
                continue
            landing_rects = {
                layer_name: [_shift_rect(rect, x, y) for rect in layer_footprints]
                for layer_name, layer_footprints in footprints.items()
            }
            if not any(_rects_overlap(rect, top_rail)
                       for rect in landing_rects.get("m3", [])):
                continue

            legal = True
            for layer_name, candidate_rects in landing_rects.items():
                for candidate_rect in candidate_rects:
                    for obstacle in obstacles[layer_name]:
                        if (layer_name == pin.layer and
                                _same_rect(obstacle, pin_rect)):
                            continue
                        if _rects_conflict(candidate_rect, obstacle,
                                           spacings[layer_name]):
                            legal = False
                            break
                    if not legal:
                        break
                if not legal:
                    break
            if not legal:
                continue

            strap = (x + strap_footprint[0], strap_y0,
                     x + strap_footprint[2], strap_y1)
            if any(not _same_rect(obstacle, pin_rect) and
                   _rects_conflict(strap, obstacle, spacings["m1"])
                   for obstacle in obstacles["m1"]):
                continue
            return x
        return None

    def _find_vdd_landings(self, via2, footprints, vdd_pins, strap_footprint):
        layer_names = tuple(footprints)
        spacings = {}
        for layer_name in layer_names:
            rule = "{}_to_{}".format(layer_name, layer_name)
            if rule not in tech.drc:
                raise ValueError("Missing {} spacing rule for the TG VDD via stack"
                                 .format(rule))
            spacings[layer_name] = tech.drc(rule)

        obstacles = {layer_name: _module_layer_rectangles(self, layer_name)
                     for layer_name in layer_names}
        grid = tech.drc("grid")
        min_foot_y = min(rect[1] for rects in footprints.values() for rect in rects)
        max_foot_y = max(rect[3] for rects in footprints.values() for rect in rects)
        low_y = max(max(pin.cy() + grid, inst.by() - min_foot_y)
                    for inst, pin, _ in vdd_pins)
        high_y = min(inst.uy() - max_foot_y for inst, _, _ in vdd_pins)

        top_width = via2.second_layer_width
        top_height = via2.second_layer_height
        top_rail = (0.5 * top_width,
                    0.0,
                    self.width - 0.5 * top_width,
                    top_height)
        if top_rail[2] <= top_rail[0]:
            raise ValueError("TG mux array is too narrow for its VDD M3 landing rail")

        events = []
        for layer_name, layer_footprints in footprints.items():
            spacing = spacings[layer_name]
            for obstacle in obstacles[layer_name]:
                for footprint in layer_footprints:
                    events.extend((obstacle[1] - footprint[3] - spacing,
                                   obstacle[3] - footprint[1] + spacing))
        for obstacle in obstacles["m1"]:
            events.extend((obstacle[1] - strap_footprint[3] - spacings["m1"],
                           obstacle[3] - strap_footprint[1] + spacings["m1"]))
        rail_half_height = 0.5 * top_height
        for obstacle in obstacles["m3"]:
            events.extend((obstacle[1] - rail_half_height - spacings["m3"],
                           obstacle[3] + rail_half_height + spacings["m3"]))

        for y in _grid_candidates(low_y, high_y, events, grid):
            rail = (top_rail[0], y - rail_half_height,
                    top_rail[2], y + rail_half_height)
            if any(_rects_conflict(rail, obstacle, spacings["m3"])
                   for obstacle in obstacles["m3"]):
                continue

            working = {layer_name: list(rectangles)
                       for layer_name, rectangles in obstacles.items()}
            landings = []
            for inst, pin, pin_rect in vdd_pins:
                x = self._find_vdd_landing_x(inst, pin, pin_rect, y, footprints,
                                             working, spacings, strap_footprint, rail)
                if x is None:
                    break
                landings.append(vector(x, y))
                for layer_name, layer_footprints in footprints.items():
                    working[layer_name].extend(_shift_rect(rect, x, y)
                                               for rect in layer_footprints)
                strap_y1 = y + strap_footprint[3]
                working["m1"].append((x + strap_footprint[0], min(pin.cy(), strap_y1),
                                      x + strap_footprint[2], max(pin.cy(), strap_y1)))
            if len(landings) == len(vdd_pins):
                return landings

        raise ValueError(
            "ICS55 TG column mux array has no legal common VDD M1-to-M3 landing "
            "window across {} leaves".format(len(vdd_pins)))

    def route_supplies(self):
        self.route_horizontal_pins("gnd", self.insts)
        if not self.mux_inst:
            raise ValueError("ICS55 TG column mux array has no leaves for VDD routing")

        vdd_pins = []
        for inst in self.mux_inst:
            pin = inst.get_pin("vdd")
            if pin.layer != "m1":
                raise ValueError("ICS55 TG mux VDD must connect from its M1 rail, got {}"
                                 .format(pin.layer))
            vdd_pins.append((inst, pin, _rect_bounds(pin.rect)))

        via1, via2, footprints = self._vdd_via_footprints()
        strap_footprint = (min(rect[0] for rect in footprints["m1"]),
                           min(rect[1] for rect in footprints["m1"]),
                           max(rect[2] for rect in footprints["m1"]),
                           max(rect[3] for rect in footprints["m1"]))
        landings = self._find_vdd_landings(via2, footprints,
                                           vdd_pins, strap_footprint)

        last_via = None
        via_y = landings[0].y
        for (inst, pin, _), landing in zip(vdd_pins, landings):
            self.add_rect(layer=pin.layer,
                          offset=vector(landing.x + strap_footprint[0], pin.cy()),
                          width=strap_footprint[2] - strap_footprint[0],
                          height=landing.y + strap_footprint[3] - pin.cy())
            last_via = self.add_via_stack_center(from_layer=pin.layer,
                                                 to_layer="m3",
                                                 offset=landing)

        via_width = last_via.mod.second_layer_width
        via_height = last_via.mod.second_layer_height
        self.add_layout_pin_segment_center(text="vdd",
                                           layer="m3",
                                           start=vector(0.5 * via_width, via_y),
                                           end=vector(self.width - 0.5 * via_width, via_y),
                                           width=via_height)
