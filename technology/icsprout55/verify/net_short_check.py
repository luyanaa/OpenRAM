#!/usr/bin/env python3
"""Check that no two named top-level nets share geometry.

Usage:
  net_short_check.py LAYOUT.gds TOP --nets vdd gnd [--labels vdd,gnd]

For every supplied net name it collects the top-level label positions on the
given conductor layers, then does a breadth-first search over the real
geometry (same-layer merge + via connections only) and reports:

  * the number of shapes in each net,
  * every pair of nets that ends up electrically connected,
  * the shortest connecting path as a list of shape bounding boxes.

Exit status is 0 when the nets are all separate, 1 when they are connected
(a short), 2 on a usage or input error.
"""
import argparse
import sys
from collections import deque

import klayout.db as kdb

METALS = (81, 82, 83, 84, 85)
VIAS = {91: (81, 82), 92: (82, 83), 93: (83, 84), 94: (84, 85)}


def load(gds, top_name=None):
    layout = kdb.Layout()
    layout.read(gds)
    cell = layout.cell(top_name) if top_name else layout.top_cell()
    if cell is None:
        raise SystemExit("top cell not found: {}".format(top_name))
    return layout, cell


def regions(layout, cell):
    metals, vias = {}, {}
    for layer in METALS:
        index = layout.find_layer(layer, 1)
        if index is None:
            continue
        region = kdb.Region(cell.begin_shapes_rec(index))
        region.merge()
        metals[layer] = region
    for via in VIAS:
        index = layout.find_layer(via, 1)
        if index is None:
            continue
        region = kdb.Region(cell.begin_shapes_rec(index))
        region.merge()
        vias[via] = region
    return metals, vias


def labels(layout, cell, names):
    """Return net -> list of (layer, x, y) label positions."""
    found = {name: [] for name in names}
    for index in layout.layer_indices():
        info = layout.get_info(index)
        if info.datatype != 6:
            continue
        for shape in cell.shapes(index):
            if not shape.is_text():
                continue
            text = shape.text.string
            if text in found:
                found[text].append((info.layer, shape.text.x, shape.text.y))
    return found


def shape_key(layer, region):
    box = region.bbox()
    return (layer, box.left, box.bottom, box.right, box.top)


def polygon_at(layout, metals, layer, x, y):
    point = kdb.Point(int(x), int(y))
    for polygon in metals.get(layer, kdb.Region()).each():
        if polygon.inside(point):
            region = kdb.Region()
            region.insert(polygon)
            return region
    return None


def search(layout, metals, vias, seed_layer, seed_region, target_point,
           target_layer):
    """BFS from one seed to a point; returns the shape path or None."""
    start = (seed_layer, seed_region)
    seen = {shape_key(*start)}
    previous = {shape_key(*start): None}
    queue = deque([start])
    while queue:
        layer, region = queue.popleft()
        if layer == target_layer and any(
                polygon.inside(target_point) for polygon in region.each()):
            return rebuild(seen, previous, shape_key(layer, region))
        for via, (lower, upper) in VIAS.items():
            if via not in vias or layer not in (lower, upper):
                continue
            contacts = vias[via].interacting(region)
            if contacts.is_empty():
                continue
            next_layer = upper if layer == lower else lower
            if next_layer not in metals:
                continue
            for polygon in metals[next_layer].interacting(contacts).each():
                candidate = kdb.Region()
                candidate.insert(polygon)
                key = shape_key(next_layer, candidate)
                if key in seen:
                    continue
                seen.add(key)
                previous[key] = shape_key(layer, region)
                queue.append((next_layer, candidate))
    return None


def rebuild(seen, previous, key):
    path = []
    while key is not None:
        path.append(key)
        key = previous[key]
    path.reverse()
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gds")
    parser.add_argument("top", nargs="?")
    parser.add_argument("--nets", default="vdd,gnd")
    parser.add_argument("--max-shapes", type=int, default=0,
                        help="cap on reported shapes per net (0 = all)")
    args = parser.parse_args(argv)

    layout, cell = load(args.gds, args.top)
    dbu = layout.dbu
    names = [name for name in args.nets.split(",") if name]
    metals, vias = regions(layout, cell)
    spots = labels(layout, cell, names)

    missing = [name for name in names if not spots[name]]
    if missing:
        print("missing labels for: {}".format(", ".join(missing)))
        return 2

    seeds = {}
    for name in names:
        layer, x, y = spots[name][0]
        region = polygon_at(layout, metals, layer, x, y)
        if region is None:
            print("no conductor under the {} label".format(name))
            return 2
        seeds[name] = (layer, x, y, region)

    print("labels (um):")
    for name in names:
        for layer, x, y in spots[name]:
            print("  {:4s} layer {:3d} ({:.3f},{:.3f})".format(
                name, layer, x * dbu, y * dbu))

    # Test EVERY label of a net against EVERY label of every other net.  A
    # single-seed test reports "separate" when that one label happens to sit
    # on an isolated segment, which is a false negative.
    connected = []
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            for (flayer, fx, fy) in spots[first]:
                region = polygon_at(layout, metals, flayer, fx, fy)
                if region is None:
                    print("  no conductor under {} label at ({:.3f},{:.3f})".format(
                        first, fx * dbu, fy * dbu))
                    continue
                for (tlayer, tx, ty) in spots[second]:
                    path = search(layout, metals, vias, flayer, region,
                                  kdb.Point(int(tx), int(ty)), tlayer)
                    if path is None:
                        continue
                    connected.append((first, second))
                    print("{} label ({:.3f},{:.3f}) and {} label ({:.3f},{:.3f}): "
                          "CONNECTED via {} shape(s)".format(
                              first, fx * dbu, fy * dbu,
                              second, tx * dbu, ty * dbu, len(path)))
                    for step, (play, l, b, r, t) in enumerate(path):
                        print("  {:2d} layer {:3d} ({:.3f},{:.3f})-({:.3f},{:.3f})".format(
                            step, play, l * dbu, b * dbu, r * dbu, t * dbu))
                    break
                if connected:
                    break
            if not any(pair == (first, second) for pair in connected):
                print("{} and {}: separate (all label pairs)".format(first, second))

    if connected:
        print("SHORT: {}".format(", ".join("{}/{}".format(a, b)
                                           for a, b in connected)))
        return 1
    print("CLEAN: all nets separate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
