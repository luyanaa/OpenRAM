#!/usr/bin/env python3
"""Clean-room ICsprout55 GDS geometry extraction.

The extractor deliberately uses KLayout's :class:`LayoutToNetlist` for all
connectivity.  It does not read a schematic or infer a connection from a
nearest shape.  Only the core 1.2 V MOS4 devices (SVT/LVT/HVT) and the
rectilinear BEOL stack used by the SRAM macros are in scope.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import klayout.db as db
except ImportError:  # pragma: no cover - KLayout's legacy package name
    import pya as db  # type: ignore


_SCHEMA_VERSION = 1

# Stream layers used by the released technology.  The additional entries are
# masks which are recognized so that a macro can be diagnosed accurately;
# only the layers in _METALS/_VIAS are emitted as RC geometry.
_LAYERS = {
    "ACT": (2, 1),
    "NW": (9, 1),
    "LVTN": (17, 1),
    "LVTP": (18, 1),
    "HVTN": (21, 1),
    "HVTP": (22, 1),
    "TGOX": (28, 1),
    "POLY": (41, 1),
    "PPO": (41, 4),
    "DGOX": (27, 1),
    "CTopcblk": (72, 5),
    "CTbar": (72, 7),
    "ALPAD": (122, 1),
    "COVER": (124, 1),
    "NP": (52, 1),
    "PP": (53, 1),
    "ESD": (55, 1),
    "SAB": (71, 1),
    "CT": (72, 1),
    "M1": (81, 1),
    "M2": (82, 1),
    "M3": (83, 1),
    "M4": (84, 1),
    "M5": (85, 1),
    "V1": (91, 1),
    "V2": (92, 1),
    "V3": (93, 1),
    "V4": (94, 1),
    "TM2": (103, 1),
    "TV2": (113, 1),
    "RV": (120, 1),
    "CB": (121, 1),
    "RDL": (123, 1),
    "DIODE": (191, 12),
    "VPW": (261, 12),
    "L313": (313, 12),
    "CELLBOUND": (351, 12),
    "L354": (354, 12),
    "L356": (356, 12),
    "L357": (357, 12),
}
_METALS = ("M1", "M2", "M3", "M4", "M5", "TM2")
_VIAS = {
    "V1": ("M1", "M2"),
    "V2": ("M2", "M3"),
    "V3": ("M3", "M4"),
    "V4": ("M4", "M5"),
    "TV2": ("M5", "TM2"),
}
_VIA_NAMES = {"V1": "VIA1", "V2": "VIA2", "V3": "VIA3", "V4": "VIA4", "TV2": "T4V2"}
_PORT_DTS = (1, 2, 6)
_PORT_METAL_DTS = {1, 2, 6}
# These are physical mask layers which may occur in a macro but do not make a
# conductor or device.  Unknown layers are rejected below rather than silently
# disappearing from a post-layout handoff.
_IGNORED_MASKS = {"SAB", "COVER", "L313", "CELLBOUND", "L354", "L356", "L357"}
_REJECT_MASKS = {"TGOX", "DGOX", "ESD", "ALPAD", "RV", "CB", "RDL", "DIODE", "VPW"}


class _ExtractionError(ValueError):
    pass


def _fail(message: str) -> None:
    raise _ExtractionError(message)


def _layer_index(layout: db.Layout, layer: tuple[int, int]) -> int | None:
    index = layout.find_layer(*layer)
    if index is None or index < 0:
        return None
    return int(index)


def _sha256(path: os.PathLike[str] | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_layout(gds: os.PathLike[str] | str, top: str | None) -> tuple[db.Layout, db.Cell, str]:
    if not isinstance(gds, (str, bytes, os.PathLike)):
        _fail("extract_geometry requires a GDS path so input_gds_sha256 is reproducible")
    path = os.fspath(gds)
    layout = db.Layout()
    try:
        layout.read(path)
    except Exception as exc:  # KLayout exceptions are not consistently typed
        _fail(f"cannot read GDS {path!r}: {exc}")
    if top is None:
        tops = list(layout.top_cells())
        if len(tops) != 1:
            names = ", ".join(cell.name for cell in tops)
            _fail(f"ambiguous top cell; specify top explicitly (found: {names})")
        cell = tops[0]
    else:
        cell = layout.cell(top)
        if cell is None:
            _fail(f"top cell {top!r} was not found")
    if cell.bbox().empty():
        _fail(f"top cell {cell.name!r} has no geometry")
    return layout, cell, _sha256(path)


def _iter_flat_shapes(cell: db.Cell, layer: int):
    """Yield recursive iterator objects (with hierarchy transformations)."""
    return cell.begin_shapes_rec(layer).each()


def _has_geometry(cell: db.Cell, layer: int) -> bool:
    for iterator in _iter_flat_shapes(cell, layer):
        if not iterator.shape().is_text():
            return True
    return False


def _make_input(l2n: db.LayoutToNetlist, layout: db.Layout, cell: db.Cell,
                name: str, required: bool = False) -> db.Region:
    index = _layer_index(layout, _LAYERS[name])
    if index is None:
        if required:
            _fail(f"required ICsprout55 layer {name} {_LAYERS[name]} is absent")
        region = db.Region()
        l2n.register(region, name)
        return region
    # Polygon layers intentionally exclude text objects.  Direct top texts are
    # registered separately and are connected only to their owning metal.
    region = db.Region(cell.begin_shapes_rec(index))
    l2n.register(region, name)
    return region


def _rectangle_from_shape(shape: Any, dbu: float, context: str) -> dict[str, float]:
    if shape.is_box():
        box = shape.box
    elif shape.is_polygon():
        polygon = shape.polygon
        if polygon.holes() != 0:
            _fail(f"{context} contains a polygon hole; decomposition was not exact")
        box = polygon.bbox()
        points = list(polygon.each_point_hull())
        corners = {(box.left, box.bottom), (box.left, box.top),
                   (box.right, box.bottom), (box.right, box.top)}
        if polygon.area() != box.width() * box.height() or set((p.x, p.y) for p in points) != corners:
            _fail(f"{context} produced a non-rectangular trapezoid")
    else:
        _fail(f"{context} contains a non-polygon shape")
    if box.left >= box.right or box.bottom >= box.top:
        _fail(f"{context} contains a zero-area rectangle")
    return {
        "x0": float(box.left * dbu),
        "y0": float(box.bottom * dbu),
        "x1": float(box.right * dbu),
        "y1": float(box.top * dbu),
    }


def _physical_union(region):
    copy = region.dup()
    copy.remove_properties()
    return copy.merged()


def _rectangular_decomposition(region: db.Region, dbu: float, context: str) -> list[dict[str, float]]:
    """Return an exact non-overlapping rectangle decomposition.

    KLayout's trapezoid decomposition is used rather than a bounding-box
    approximation.  On a rectilinear input every emitted trapezoid must be a
    rectangle; holes and concavities therefore survive as missing rectangles.
    """
    merged = _physical_union(region)
    if merged.count() == 0:
        return []
    try:
        pieces = merged.decompose_trapezoids(db.Polygon.TD_htrapezoids)
    except Exception as exc:
        _fail(f"{context} cannot be decomposed by KLayout: {exc}")
    rectangles = [_rectangle_from_shape(shape, dbu, context) for shape in pieces.each()]
    expected = merged.area()
    actual = sum(round((r["x1"] - r["x0"]) / dbu) * round((r["y1"] - r["y0"]) / dbu)
                  for r in rectangles)
    if actual != expected:
        _fail(f"{context} rectangle decomposition changed area ({actual} != {expected})")
    rectangles.sort(key=lambda r: (r["x0"], r["y0"], r["x1"], r["y1"]))
    return rectangles


def _region_for_terminal(shapes: dict[Any, db.Region], l2n: db.LayoutToNetlist,
                         wanted: str) -> db.Region | None:
    for index, region in shapes.items():
        try:
            if l2n.layer_name(index) == wanted:
                return region
        except Exception:
            continue
    return None


def _terminal_anchor_points(region: db.Region, dbu: float, context: str) -> list[tuple[float, float]]:
    """Choose one interior point per merged M1 contact component."""
    result: list[tuple[float, float]] = []
    for polygon in region.merged().each():
        component = db.Region(polygon)
        pieces = _rectangular_decomposition(component, dbu, context)
        if not pieces:
            continue
        # The largest rectangle gives a point strictly within the physical
        # M1 component, including for concave contact unions.
        piece = max(pieces, key=lambda r: (r["x1"] - r["x0"]) * (r["y1"] - r["y0"]))
        result.append(((piece["x0"] + piece["x1"]) / 2.0,
                       (piece["y0"] + piece["y1"]) / 2.0))
    return result


def _metal_net_shapes(l2n: db.LayoutToNetlist, net: Any, layer: db.Region) -> db.Region:
    try:
        return l2n.shapes_of_net(net, layer, True)
    except Exception as exc:
        _fail(f"cannot retrieve geometry for net {net.cluster_id}: {exc}")
    raise AssertionError("unreachable")


def _special_net(name: str) -> bool:
    return bool(re.search(r"(?:^|[_/])(?:VDD|VSS|VCC|GND|VPWR|VGND|VDDA|VSSA)(?:$|[_/])",
                          name.upper()))


def _set_or_check_port_name(net: Any, label: str, by_cluster: dict[int, str]) -> None:
    cluster = int(net.cluster_id)
    previous = by_cluster.get(cluster)
    if previous is not None and previous != label:
        _fail(f"net {cluster} has conflicting direct top labels {previous!r} and {label!r}")
    if previous is None:
        by_cluster[cluster] = label
    if net.name and net.name != label:
        _fail(f"direct top label {label!r} conflicts with extracted net name {net.name!r}")
    if not net.name:
        net.name = label


def extract_geometry(gds: os.PathLike[str] | str, top: str | None = None) -> dict[str, Any]:
    """Extract core MOS and physical conductors with native directional bodies.

    The FEOL connection rules match the audited Calibre SCONNECT semantics.
    Names come only from direct top text; no label or schematic joins nets.
    """
    layout, cell, input_sha256 = _load_layout(gds, top)
    known = set(_LAYERS.values())
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        if (info.layer, info.datatype) not in known and _has_geometry(cell, index):
            _fail(f"unsupported non-text GDS layer {info.layer}/{info.datatype}")
    l2n = db.LayoutToNetlist(cell.name, layout.dbu)
    inputs = {name: _make_input(l2n, layout, cell, name) for name in _LAYERS}
    for name in _REJECT_MASKS:
        if inputs[name].count():
            _fail(f"unsupported ICsprout55 feature layer {name}")
    if (inputs["PPO"] & inputs["POLY"]).count():
        _fail("poly resistor marker PPO is outside the MOS-only scope")
    if ((inputs["CTopcblk"] | inputs["CTbar"]) & inputs["CT"]).count():
        _fail("blocked contacts are unsupported")
    if (inputs["NP"] & inputs["PP"]).count():
        _fail("overlapping NP/PP implants are ambiguous")
    poly, act, nw, ct = (inputs[name] for name in ("POLY", "ACT", "NW", "CT"))
    nact, pact = act & inputs["NP"], act & inputs["PP"]
    ngate, pgate = poly & nact, poly & pact
    nsd, psd = (nact - ngate) - nw, (pact - pgate) & nw
    n_tap = ((nact - ngate) & nw).interacting(ct)
    p_tap = ((pact - pgate) - nw).interacting(ct)
    extent = db.Region(cell.bbox()).sized(round(0.1 / layout.dbu))
    psub = extent - nw
    body = db.Region()
    feol = {"N_SD": nsd, "P_SD": psd,
            "N_TAP": n_tap, "P_TAP": p_tap, "PSUB": psub, "MOS_BULK": body}
    for name, region in feol.items():
        l2n.register(region, name)
        l2n.connect(region)
    l2n.connect(poly)
    l2n.connect(nw)
    l2n.soft_connect_global(psub, "SUBSTRATE")
    l2n.connect(body, psub)
    l2n.connect(body, nw)
    l2n.soft_connect(n_tap, nw)
    l2n.soft_connect(p_tap, psub)
    for region in (nsd, psd, poly, n_tap, p_tap):
        l2n.connect(region, ct)
    metals = {name: inputs[name] for name in _METALS}
    vias = {name: inputs[name] for name in _VIAS}
    for region in metals.values():
        l2n.connect(region)
    l2n.connect(ct, metals["M1"])
    for via, (lower, upper) in _VIAS.items():
        l2n.connect(vias[via], metals[lower])
        l2n.connect(vias[via], metals[upper])

    model_inputs = {}
    for polarity, gate, suffix in (("n", ngate, "N"), ("p", pgate, "P")):
        lvt, hvt = inputs["LVT" + suffix], inputs["HVT" + suffix]
        if (lvt & hvt & gate).count():
            _fail("overlapping LVT/HVT gates are unsupported")
        for flavor, region in (("svt", gate - lvt - hvt), ("lvt", gate & lvt), ("hvt", gate & hvt)):
            model = f"{polarity}m1p2_{flavor}_lp"
            l2n.register(region, model + "_gate")
            l2n.connect(region, poly)
            model_inputs[model] = region
            if region.count():
                l2n.extract_devices(db.DeviceExtractorMOS4Transistor(model.upper(), False),
                    {"SD": nsd if polarity == "n" else psd, "G": region,
                     "tS": nsd if polarity == "n" else psd,
                     "tD": nsd if polarity == "n" else psd,
                     "tG": poly, "W": body})
    if ((poly & act) - ngate - pgate).count():
        _fail("unsupported active/poly device")
    l2n.extract_netlist()
    errors = list(l2n.each_log_entry())
    if errors:
        _fail("native extraction reported errors: " + "; ".join(str(entry.message) for entry in errors))
    circuit = l2n.netlist().top_circuit()
    if circuit is None:
        _fail("native extraction produced no circuit")

    # Text is probed after extraction and never participates in connectivity.
    port_by_cluster, cluster_by_port, bindings = {}, {}, []
    for metal in _METALS:
        number = _LAYERS[metal][0]
        for datatype in (1, 6):
            index = _layer_index(layout, (number, datatype))
            if index is None:
                continue
            for shape in cell.shapes(index).each():
                if not shape.is_text():
                    continue
                label, point = shape.text_string, shape.text_pos
                net = l2n.probe_net(metals[metal], point)
                if net is None:
                    _fail(f"top port {label!r} is not on its own metal")
                cluster = int(net.cluster_id)
                if label in cluster_by_port and cluster_by_port[label] != cluster:
                    _fail(f"top port {label!r} labels disconnected conductors")
                if cluster in port_by_cluster and port_by_cluster[cluster] != label:
                    _fail(f"top ports {label!r} and {port_by_cluster[cluster]!r} are shorted")
                cluster_by_port[label], port_by_cluster[cluster] = cluster, label
                bindings.append((label, cluster, metal, point.x, point.y))
    if len({label.casefold() for label in cluster_by_port}) != len(cluster_by_port):
        _fail("port case collides in ngspice")
    nets = {int(net.cluster_id): net for net in circuit.each_net()}
    names = dict(port_by_cluster)
    used = {name.casefold() for name in names.values()}
    for cluster in sorted(nets):
        if cluster not in names:
            number = cluster
            while f"n{number}".casefold() in used:
                number += 1
            names[cluster] = f"n{number}"
            used.add(names[cluster].casefold())
    anchors = defaultdict(list)
    def anchor(cluster, name, metal, point):
        layer = "T4M2" if metal == "TM2" else "MET" + metal[1:]
        anchors[cluster].append({"name": name, "layer": layer,
                                 "x": point.x * layout.dbu, "y": point.y * layout.dbu})
    for label, cluster, metal, x, y in bindings:
        anchor(cluster, label, metal, db.Point(x, y))

    # Components of ideal poly/diffusion get one electrical anchor identity.
    # Each real CT cut attaches that identity to M1; same logical metal net
    # alone never shorts two separate metal locations in an RCC network.
    components, component_shapes = {}, {}
    for name, region in (("N_SD", nsd), ("P_SD", psd), ("POLY_CON", poly), ("NW", nw), ("PSUB", psub)):
        storage = db.Shapes()
        rows = []
        for polygon in _physical_union(region).each():
            index = len(rows)
            component = db.Region(polygon)
            is_body = name in ("NW", "PSUB")
            tap = n_tap if name == "NW" else p_tap
            contact_region = component & ct
            if is_body:
                contact_region &= tap
            contacts = []
            for cut in _physical_union(contact_region).each():
                boxes = _rectangular_decomposition(db.Region(cut), layout.dbu, name + " contact")
                for box in boxes:
                    point = db.Point(round((box["x0"] + box["x1"]) / (2 * layout.dbu)),
                                     round((box["y0"] + box["y1"]) / (2 * layout.dbu)))
                    net = l2n.probe_net(metals["M1"], point)
                    if net is None:
                        _fail("CT has no connected M1 enclosure")
                    contacts.append((int(net.cluster_id), point))
            component_id = f"feol:{name}:{index}"
            rows.append((component, component_id, contacts))
            shape = storage.insert(polygon)
            shape.set_property(1, index)
        components[name], component_shapes[name] = rows, storage
    def component_for(name, terminal_region):
        hits = []
        for shape in component_shapes[name].each_overlapping(terminal_region.bbox()):
            index = int(shape.property(1))
            component, identity, contacts = components[name][index]
            if (component & terminal_region).count():
                hits.append((identity, contacts))
        if len(hits) != 1:
            _fail(f"terminal does not identify one physical {name} component")
        return hits[0]

    # Keep the physical active-component identity alongside each extracted
    # device.  The LVS deck uses this grouping boundary for its documented
    # CDL-guided multi-finger normalization; it is not an electrical alias.
    active_groups = {}
    for polarity, region in (("n", nact), ("p", pact)):
        active_groups[polarity] = [
            (index, polygon.bbox())
            for index, polygon in enumerate(_physical_union(region).each())
        ]

    def active_group_for(device, model):
        point = device.trans.disp
        database_point = db.Point(round(point.x / layout.dbu), round(point.y / layout.dbu))
        hits = [index for index, bbox in active_groups[model[0]]
                if bbox.contains(database_point)]
        if len(hits) != 1:
            _fail(f"{device.device_class().name} at {point.x},{point.y} "
                  "does not identify one physical active component")
        return f"{model[0]}:{hits[0]}"

    def origin_um(device):
        return {"x": device.trans.disp.x, "y": device.trans.disp.y}


    records = []
    devices = sorted(circuit.each_device(), key=lambda d: (d.trans.disp.x, d.trans.disp.y, d.device_class().name, d.id()))
    emitted_components = set()
    for number, device in enumerate(devices, 1):
        model = device.device_class().name.lower()
        terminals, terminal_components = {}, {}
        for terminal in ("D", "G", "S", "B"):
            net = device.net_for_terminal(terminal)
            if net is None:
                _fail(f"M{number}:{terminal} has no physical net")
            cluster = int(net.cluster_id)
            terminals[terminal] = names[cluster]
            layer = "MOS_BULK" if terminal == "B" else ("POLY" if terminal == "G" else ("N_SD" if model.startswith("n") else "P_SD"))
            shape_map = l2n.shapes_of_terminal(device.terminal_ref(terminal))
            terminal_region = _region_for_terminal(shape_map, l2n, layer)
            if terminal_region is None or not terminal_region.count():
                _fail(f"M{number}:{terminal} has no terminal geometry")
            physical = (("NW" if model.startswith("p") else "PSUB") if terminal == "B"
                        else ("POLY_CON" if terminal == "G" else layer))
            identity, contacts = component_for(physical, terminal_region)
            if terminal == "B" and not contacts:
                _fail(f"M{number}:B has no physically contacted well/substrate tap")
            if terminal == "B" and model.startswith("n"):
                identity = "feol:SUBSTRATE"
            terminal_components[terminal] = identity
            if identity not in emitted_components:
                for contacted_cluster, point in contacts:
                    if contacted_cluster != cluster:
                        _fail(f"{identity} spans conflicting hard metal nets")
                    anchor(cluster, identity, "M1", point)
                emitted_components.add(identity)
        params = {key: float(device.parameter(key)) for key in ("W", "L", "AS", "AD", "PS", "PD")}
        records.append({"name": f"M{number}", "model": model,
                        "w_um": params["W"], "l_um": params["L"],
                        "as_um2": params["AS"], "ad_um2": params["AD"],
                        "ps_um": params["PS"], "pd_um": params["PD"],
                        "terminals": terminals, "terminal_components": terminal_components,
                        "active_group": active_group_for(device, model),
                        "origin_um": origin_um(device)})
    net_records = []
    rectangle_count = via_count = 0
    for cluster, net in sorted(nets.items(), key=lambda item: names[item[0]]):
        rectangles, cuts = [], []
        for metal in _METALS:
            layer = "T4M2" if metal == "TM2" else "MET" + metal[1:]
            region = _metal_net_shapes(l2n, net, metals[metal])
            rectangles.extend({"layer": layer, **box} for box in _rectangular_decomposition(region, layout.dbu, layer))
        for via, (lower, upper) in _VIAS.items():
            region = _metal_net_shapes(l2n, net, vias[via])
            for box in _rectangular_decomposition(region, layout.dbu, via):
                cuts.append({"layer": _VIA_NAMES[via],
                    "lower_layer": "T4M2" if lower == "TM2" else "MET" + lower[1:],
                    "upper_layer": "T4M2" if upper == "TM2" else "MET" + upper[1:],
                    "x": (box["x0"] + box["x1"]) / 2, "y": (box["y0"] + box["y1"]) / 2,
                    "area_um2": (box["x1"] - box["x0"]) * (box["y1"] - box["y0"])})
        rectangle_count += len(rectangles)
        via_count += len(cuts)
        net_records.append({"name": names[cluster], "special": _special_net(names[cluster]),
                            "rectangles": rectangles, "vias": cuts, "anchors": anchors[cluster]})
    limitations = ["Core MOS only; non-Manhattan BEOL and unsupported device layers hard-fail.",
                   "Diffusion/poly/contacts and body networks are ideal; FEOL resistance/capacitance is not extracted.",
                   "Bulk uses native directional tapped-body connectivity; no named rail aliases.",
                   "Metal union rectangles preserve physical holes/concavities; MOS junction AS/AD/PS/PD are physical."]
    return {"schema_version": 1, "design": cell.name, "dbu_um": layout.dbu,
            "input_gds_sha256": input_sha256, "ports": sorted(cluster_by_port),
            "devices": records, "nets": net_records,
            "summary": {"raw_mos": len(records), "nets": len(net_records), "ports": len(cluster_by_port),
                        "geometry_counts": {"rectangles": rectangle_count, "vias": via_count,
                                            "anchors": sum(map(len, anchors.values()))}, "limitations": limitations}}


__all__ = ["extract_geometry"]
