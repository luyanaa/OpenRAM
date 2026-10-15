#!/usr/bin/env python3
"""Open-source ICsprout55 GDS parasitic extraction.

The module deliberately keeps the clean-room process data in the PDK.  It does
not copy CAPTAB/ITF data into the OpenRAM repository and never interprets the
absolute CAPTAB columns.  ``c`` is the default, screening-oriented output;
``rcc`` adds a finite-volume metal/via network.
"""
from __future__ import annotations

import bisect
import importlib.util
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

_EPS = 1.0e-9
_METAL_ALIASES = {
    "M1": "MET1", "M2": "MET2", "M3": "MET3", "M4": "MET4",
    "M5": "MET5", "TM2": "T4M2", "RDL": "RDL",
}
_VIA_ALIASES = {"V1": "VIA1", "V2": "VIA2", "V3": "VIA3", "V4": "VIA4", "TV2": "T4V2"}


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ValueError("cannot load PEX helper %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _number(value: Any, label: str, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be numeric" % label) from exc
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise ValueError("%s must be finite%s" % (label, " and positive" if positive else ""))
    return result


def _json_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("%s must be an object" % label)
    return {str(k): v for k, v in value.items()}


def _load_process_json(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, Mapping):
        return _json_mapping(value, "process_json"), "<mapping>"
    path = Path(value)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read process_json %s: %s" % (path, exc)) from exc
    return _json_mapping(data, "process_json"), str(path)


def _select_corner(data: dict[str, Any] | None, corner: str) -> dict[str, Any]:
    if not data:
        return {}
    corners = data.get("corners")
    if isinstance(corners, Mapping):
        wanted = str(corner).casefold()
        for name, value in corners.items():
            if str(name).casefold() == wanted:
                return _json_mapping(value, "process_json.corners.%s" % name)
        raise ValueError("process_json has no corner %s" % corner)
    return data


def _override_for(mapping: Any, *names: str) -> Mapping[str, Any] | None:
    if not isinstance(mapping, Mapping):
        return None
    folded = {str(k).casefold(): v for k, v in mapping.items()}
    for name in names:
        value = folded.get(name.casefold())
        if isinstance(value, Mapping):
            return value
    return None


def _has_coupling(layer: Mapping[str, Any]) -> bool:
    coup = layer.get("coupling")
    if isinstance(coup, Mapping):
        if any(k in coup for k in ("same_layer_pf_per_um", "same_layer_by_spacing", "cross_layer_pf_per_um2")):
            return True
    return any(k in layer for k in (
        "coupling_same_layer_pf_per_um", "same_layer_pf_per_um",
        "coupling_same_layer_by_spacing", "same_layer_by_spacing",
        "coupling_cross_layer_pf_per_um2", "cross_layer_pf_per_um2",
    ))


def _merge_layer(base: dict[str, Any], override: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(base)
    if not override:
        return result
    for key, value in override.items():
        result[str(key)] = value
    coup = override.get("coupling")
    if isinstance(coup, Mapping):
        result["coupling"] = dict(coup)
    return result


def build_process(
    pdk_root: str | Path,
    source: str = "lef",
    corner: str = "typ",
    process_json: str | Path | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the PDK stack and normalize it for GDS geometry extraction.

    ``source=lef`` uses only released LEF RC seeds.  ``pdk-estimate`` uses the
    documented OpenRCX generator coefficients, and is explicitly estimated.
    ``starrc`` is accepted for local research runs, but its absolute CAPTAB
    values are never read here; without explicit coupling JSON it remains
    ``seed_only``.
    """
    root = Path(pdk_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("pdk_root is not a directory: %s" % root)
    source = str(source).strip().lower()
    if source not in {"lef", "pdk-estimate", "starrc"}:
        raise ValueError("unsupported PEX source %r" % source)
    corner = str(corner).strip()
    if not corner:
        raise ValueError("corner must not be empty")
    cfg_path = root / "libs.tech" / "pex" / "config_gen.py"
    if not cfg_path.is_file():
        raise ValueError("missing PDK process generator: %s" % cfg_path)
    cfg = _module(cfg_path, "ics55_config_gen_%x" % id(cfg_path))
    stack_source = "lef" if source == "pdk-estimate" else source
    try:
        stack = cfg.build_stack(corner=corner, source=stack_source)
    except (OSError, ValueError, KeyError, RuntimeError, SystemExit) as exc:
        raise ValueError("cannot build PDK stack (%s/%s): %s" % (stack_source, corner, exc)) from exc

    raw_process, process_path = _load_process_json(process_json)
    selected = _select_corner(raw_process, corner)
    raw_layers = selected.get("layers")
    if isinstance(raw_layers, Sequence) and not isinstance(raw_layers, (str, bytes)):
        raw_layers = {str(item.get("name")): item for item in raw_layers if isinstance(item, Mapping) and item.get("name")}
    if not isinstance(raw_layers, Mapping):
        raw_layers = {}
    raw_vias = selected.get("vias")
    if isinstance(raw_vias, Sequence) and not isinstance(raw_vias, (str, bytes)):
        raw_vias = {str(item.get("name")): item for item in raw_vias if isinstance(item, Mapping) and item.get("name")}
    if not isinstance(raw_vias, Mapping):
        raw_vias = {}

    layers: dict[str, dict[str, Any]] = {}
    explicit_layers = set()
    for item in list(stack.get("metals", [])) + [stack.get("rdl", {})]:
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        pdk_name = str(item["name"])
        name = _METAL_ALIASES.get(pdk_name, pdk_name)
        base = {
            "name": name, "pdk_name": pdk_name,
            "width_um": _number(item.get("width"), "%s.width" % pdk_name, True),
            "resistance_ohm_per_square": _number(item.get("rpsq"), "%s.rpsq" % pdk_name, True),
            "ground_capacitance_pf_per_um2": _number(item.get("cap"), "%s.cap" % pdk_name, True),
            "edge_capacitance_pf_per_um": _number(item.get("edge"), "%s.edge" % pdk_name, True),
            "thickness_um": _number(item.get("thickness", 0.001), "%s.thickness" % pdk_name, True),
            "order": len(layers),
            "coupling": {},
        }
        override = _override_for(raw_layers, name, pdk_name)
        if override:
            if _has_coupling(override):
                explicit_layers.add(name)
            base = _merge_layer(base, override)
        if source == "pdk-estimate" and not _has_coupling(base):
            # These are precisely the coefficients emitted by config_gen's
            # documented OpenRCX rule generator, not CAPTAB absolute values.
            width = base["width_um"]
            edge = base["edge_capacitance_pf_per_um"]
            area = base["ground_capacitance_pf_per_um2"]
            lateral_ff_per_um = edge * 1.0e3
            base["coupling"] = {
                # config_gen emits edge*1e3 in fF/um; the RC API stores
                # capacitance in pF, so convert that documented coefficient
                # back to pF/um at the process boundary.
                "same_layer_fF_per_um": lateral_ff_per_um,
                "same_layer_pf_per_um": lateral_ff_per_um * 1.0e-3,
                "same_layer_by_spacing": [
                    [width, lateral_ff_per_um * 1.0e-3], [1.5 * width, lateral_ff_per_um * 1.0e-3],
                    [2.0 * width, lateral_ff_per_um * 1.0e-3], [3.0 * width, lateral_ff_per_um * 1.0e-3],
                    [5.0 * width, lateral_ff_per_um * 1.0e-3],
                ],
                "cross_layer_pf_per_um2": area * 0.15,
            }
        layers[name] = base
    if not layers:
        raise ValueError("PDK stack has no routing layers")

    vias: dict[str, dict[str, Any]] = {}
    for item in stack.get("vias", []):
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        pdk_name = str(item["name"])
        name = _VIA_ALIASES.get(pdk_name, pdk_name)
        base = {
            "name": name, "pdk_name": pdk_name,
            "resistance_ohm": _number(item.get("r"), "%s.r" % pdk_name, True),
            "lower_layer": _METAL_ALIASES.get(str(item.get("lower")), str(item.get("lower"))),
            "upper_layer": _METAL_ALIASES.get(str(item.get("upper")), str(item.get("upper"))),
            "cut_um": _number(item.get("cut", 0.001), "%s.cut" % pdk_name, True),
        }
        override = _override_for(raw_vias, name, pdk_name)
        if override:
            base.update(dict(override))
        vias[name] = base

    # A process JSON may use rcx.py's top-level ``coupling`` object.  It is
    # intentionally accepted only as an explicit model, never inferred from
    # unknown CAPTAB columns.
    top_coupling = selected.get("coupling")
    if isinstance(top_coupling, Mapping):
        for lname, value in top_coupling.items():
            name = _METAL_ALIASES.get(str(lname), str(lname))
            if isinstance(value, Mapping) and name in layers:
                layers[name]["coupling"] = dict(value)
                explicit_layers.add(name)
    max_cross = int(_number(selected.get("max_cross_layer", 3), "max_cross_layer", True))
    max_spacing = selected.get("max_spacing")
    max_spacing = _number(max_spacing, "max_spacing", True) if max_spacing is not None else None
    status = "estimated" if source == "pdk-estimate" else "seed_only"
    if explicit_layers:
        status = "explicit_coupling"
    limitations = [
        "LEF CPERSQDIST and EDGECAPACITANCE are used for substrate/self capacitance.",
        "Absolute CAPTAB columns are undocumented and are not interpreted or used as calibration.",
        "Ground edge capacitance is applied to the perimeter of the non-overlapping extracted metal union.",
    ]
    if status != "explicit_coupling":
        limitations.append("No calibrated lateral/vertical coupling model was supplied; coupling is omitted." if status == "seed_only" else "Coupling uses pdk-estimate generator coefficients and is not signoff calibration.")
    if source == "starrc":
        limitations.append("StarRC local files are used only through config_gen stack values; CAPTAB absolute values remain excluded.")
    return {
        "schema_version": 1, "kind": "ics55_gds_pex_process",
        "source": source, "corner": corner, "status": status,
        "signoff": False, "layers": layers, "vias": vias,
        "explicit_layers": sorted(explicit_layers),
        "max_cross_layer": max_cross, "max_spacing": max_spacing,
        "provenance": {
            "pdk_root": str(root), "generator": str(cfg_path),
            "process_json": process_path, "stack_source": stack_source,
            "assumptions": [
                "pdk-estimate same-layer coefficient is edge*1e3 fF/um expressed as pF/um and held piecewise over spacing widths 1..5",
                "pdk-estimate cross-layer coefficient is cap*0.15 pF/um2",
            ] if source == "pdk-estimate" else [],
        },
        "limitations": limitations,
    }


def _rect(record: Mapping[str, Any], label: str) -> tuple[float, float, float, float]:
    x0 = _number(record.get("x0"), label + ".x0")
    y0 = _number(record.get("y0"), label + ".y0")
    x1 = _number(record.get("x1"), label + ".x1")
    y1 = _number(record.get("y1"), label + ".y1")
    if x1 <= x0 or y1 <= y0:
        raise ValueError("%s must have positive dimensions" % label)
    return (x0, y0, x1, y1)


def _contains(rect: tuple[float, float, float, float], x: float, y: float) -> bool:
    return rect[0] - _EPS <= x <= rect[2] + _EPS and rect[1] - _EPS <= y <= rect[3] + _EPS


def _perimeter(rectangles: Sequence[tuple[float, float, float, float]]) -> float:
    """Perimeter of the rectangle union using signed boundary intervals.

    Geometry extraction guarantees non-overlapping rectangle decomposition.  A
    duplicate rectangle is removed before this function; opposing signed edges
    cancel at shared boundaries, avoiding the classic double-counted metal
    union perimeter.
    """
    horizontal: dict[float, list[tuple[float, float, int]]] = defaultdict(list)
    vertical: dict[float, list[tuple[float, float, int]]] = defaultdict(list)
    for x0, y0, x1, y1 in rectangles:
        horizontal[y0].append((x0, x1, -1))
        horizontal[y1].append((x0, x1, 1))
        vertical[x0].append((y0, y1, -1))
        vertical[x1].append((y0, y1, 1))
    total = 0.0
    for groups in (horizontal, vertical):
        for intervals in groups.values():
            events: list[tuple[float, int]] = []
            for start, end, sign in intervals:
                events.append((start, sign)); events.append((end, -sign))
            events.sort()
            active = 0
            previous = None
            for coordinate, delta in events:
                if previous is not None and coordinate > previous and active:
                    total += coordinate - previous
                active += delta
                previous = coordinate
    return total


def _dedupe_rectangles(records: Sequence[Mapping[str, Any]], label: str) -> list[tuple[float, float, float, float]]:
    found: set[tuple[float, float, float, float]] = set()
    result = []
    for index, record in enumerate(records):
        value = _rect(record, "%s[%d]" % (label, index))
        key = tuple(round(v, 9) for v in value)
        if key in found:
            continue
        found.add(key)
        result.append(value)
    return result


def _layer_info(process: Mapping[str, Any], layer: str) -> Mapping[str, Any]:
    layers = process.get("layers")
    if not isinstance(layers, Mapping):
        raise ValueError("process has no layers")
    wanted = str(layer).casefold()
    for name, value in layers.items():
        if str(name).casefold() == wanted:
            return _json_mapping(value, "process.layers.%s" % name)
        if isinstance(value, Mapping) and str(value.get("pdk_name", "")).casefold() == wanted:
            return _json_mapping(value, "process.layers.%s" % name)
    raise ValueError("geometry references unknown routing layer %s" % layer)


def _via_info(process: Mapping[str, Any], layer: str) -> Mapping[str, Any]:
    vias = process.get("vias")
    if not isinstance(vias, Mapping):
        raise ValueError("process has no vias")
    wanted = str(layer).casefold()
    for name, value in vias.items():
        if str(name).casefold() == wanted or (isinstance(value, Mapping) and str(value.get("pdk_name", "")).casefold() == wanted):
            return _json_mapping(value, "process.vias.%s" % name)
    raise ValueError("geometry references unknown via layer %s" % layer)


def _coupling_mapping(layer: Mapping[str, Any]) -> Mapping[str, Any]:
    coupling = layer.get("coupling")
    if not isinstance(coupling, Mapping) or not coupling:
        return layer
    merged = dict(layer)
    merged.update(coupling)
    return merged


def _coupling_value(layer: Mapping[str, Any], spacing: float) -> float | None:
    coupling = _coupling_mapping(layer)
    table = coupling.get("same_layer_by_spacing", coupling.get("coupling_same_layer_by_spacing"))
    points = []
    if isinstance(table, Mapping):
        axis = table.get("x", table.get("spacing_um"))
        values = table.get("values", table.get("values_pf_per_um"))
        if axis is not None or values is not None:
            if not isinstance(axis, Sequence) or not isinstance(values, Sequence) or len(axis) != len(values):
                raise ValueError("coupling spacing table has inconsistent axes")
            points = list(zip(axis, values))
        else:
            points = list(table.items())
    elif isinstance(table, Sequence) and not isinstance(table, (str, bytes)):
        for item in table:
            if isinstance(item, Mapping):
                points.append((item.get("spacing_um", item.get("x")), item.get("value", item.get("values_pf_per_um"))))
            elif isinstance(item, Sequence) and len(item) == 2:
                points.append(tuple(item))
            else:
                raise ValueError("invalid coupling spacing table row")
    if table is not None and not points:
        raise ValueError("coupling spacing table is empty")
    if points:
        points = sorted((_number(x, "coupling spacing"), _number(y, "coupling value")) for x, y in points)
        if any(x < 0 or y < 0 for x, y in points) or len({x for x, _ in points}) != len(points):
            raise ValueError("coupling spacing/value must be nonnegative with unique axis points")
        if spacing > points[-1][0] + _EPS:
            return None
        if spacing <= points[0][0]:
            return points[0][1]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if spacing <= x1:
                return y0 + (spacing - x0) * (y1 - y0) / (x1 - x0)
    value = coupling.get("same_layer_pf_per_um", coupling.get("coupling_same_layer_pf_per_um"))
    if value is None:
        return None
    value = _number(value, "same-layer coupling")
    if value < 0:
        raise ValueError("same-layer coupling must be nonnegative")
    return value


def _cross_value(layer: Mapping[str, Any]) -> float | None:
    coupling = _coupling_mapping(layer)
    value = coupling.get("cross_layer_pf_per_um2", coupling.get("coupling_cross_layer_pf_per_um2"))
    return _number(value, "cross-layer coupling", True) if value is not None else None


def _edge_records(rectangles, net, layer):
    """Emit exterior union edges, cancelling shared decomposition boundaries."""
    lines = defaultdict(list)
    for x0, y0, x1, y1 in rectangles:
        for orientation, fixed, start, end, side in (
            ("h", y0, x0, x1, -1), ("h", y1, x0, x1, 1),
            ("v", x0, y0, y1, -1), ("v", x1, y0, y1, 1)):
            lines[(orientation, round(fixed, 9))].extend(((start, side), (end, -side)))
    edges = []
    for (orientation, fixed), events in lines.items():
        changes = defaultdict(int)
        for position, delta in events:
            changes[position] += delta
        active, previous = 0, None
        for position, delta in sorted(changes.items()):
            if previous is not None and position > previous and active:
                edges.append(dict(net=net, layer=layer, orientation=orientation,
                                  fixed=fixed, start=previous, end=position,
                                  side=1 if active > 0 else -1))
            active += delta
            previous = position
    return edges


def _bucket_pairs(items: Sequence[Mapping[str, Any]], radius: float, bbox):
    if radius <= 0.0 or not items:
        return []
    cell = max(radius, 1.0e-6)
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    overflow: list[int] = []
    for index, item in enumerate(items):
        x0, y0, x1, y1 = bbox(item)
        ix0, ix1 = math.floor(x0 / cell), math.floor(x1 / cell)
        iy0, iy1 = math.floor(y0 / cell), math.floor(y1 / cell)
        count = (ix1 - ix0 + 1) * (iy1 - iy0 + 1)
        if count > 4096:
            overflow.append(index)
            continue
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                buckets[(ix, iy)].append(index)
    pairs: set[tuple[int, int]] = set()
    for values in buckets.values():
        for position, left in enumerate(values):
            for right in values[position + 1:]:
                if left != right:
                    pairs.add(tuple(sorted((left, right))))
    for left in overflow:
        for right in range(len(items)):
            if left != right:
                pairs.add(tuple(sorted((left, right))))
    return sorted(pairs)


def _validate_geometry(geometry: Mapping[str, Any]):
    if not isinstance(geometry, Mapping):
        raise ValueError("geometry must be an object")
    design = str(geometry.get("design", "")).strip()
    if not design or any(ch.isspace() for ch in design):
        raise ValueError("geometry design must be a non-empty ngspice-compatible name")
    raw_nets = geometry.get("nets")
    if not isinstance(raw_nets, Sequence) or isinstance(raw_nets, (str, bytes)) or not raw_nets:
        raise ValueError("geometry must contain a non-empty nets list")
    names: dict[str, str] = {}
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_nets):
        net = _json_mapping(raw, "geometry.nets[%d]" % index)
        name = str(net.get("name", "")).strip()
        if not name:
            raise ValueError("geometry.nets[%d] has no name" % index)
        folded = name.casefold()
        if folded in names and names[folded] != name:
            raise ValueError("net names collide after case-fold: %s/%s" % (names[folded], name))
        if folded in result:
            raise ValueError("duplicate net %s" % name)
        names[folded] = name
        rects = net.get("rectangles", [])
        if not isinstance(rects, Sequence) or isinstance(rects, (str, bytes)):
            raise ValueError("rectangles for net %s must be a list" % name)
        vias = net.get("vias", [])
        if not isinstance(vias, Sequence) or isinstance(vias, (str, bytes)):
            raise ValueError("vias for net %s must be a list" % name)
        anchors = net.get("anchors", [])
        if not isinstance(anchors, Sequence) or isinstance(anchors, (str, bytes)):
            raise ValueError("anchors for net %s must be a list" % name)
        normalized = dict(net)
        normalized["rectangles"] = rects
        normalized["vias"] = vias
        normalized["anchors"] = anchors
        result[name] = normalized
    ports = geometry.get("ports", [])
    if not isinstance(ports, Sequence) or isinstance(ports, (str, bytes)):
        raise ValueError("geometry.ports must be a list")
    port_names = []
    seen_ports: set[str] = set()
    for port in ports:
        name = str(port.get("name") if isinstance(port, Mapping) else port).strip()
        if not name:
            raise ValueError("empty port name")
        if name.casefold() in seen_ports:
            raise ValueError("port names collide after case-fold: %s" % name)
        seen_ports.add(name.casefold()); port_names.append(name)
    return design, result, port_names


def _anchor_name(anchor: Mapping[str, Any], label: str) -> str:
    name = str(anchor.get("name", "")).strip()
    if not name:
        raise ValueError("%s has no name" % label)
    _number(anchor.get("x"), label + ".x")
    _number(anchor.get("y"), label + ".y")
    if not str(anchor.get("layer", "")).strip():
        raise ValueError("%s has no layer" % label)
    return name


def _same_layer_caps(net_records, process, max_spacing):
    """Integrate nearest facing exterior edges using a dynamic neighbor sweep."""
    groups = defaultdict(list)
    for net, record in net_records.items():
        by_layer = defaultdict(list)
        for raw in record["rectangles"]:
            by_layer[raw["layer"]].append(raw)
        for layer, raw in by_layer.items():
            rectangles = _dedupe_rectangles(raw, net + "." + layer)
            for edge in _edge_records(rectangles, net, layer):
                groups[(layer, edge["orientation"])].append(edge)
    caps = defaultdict(float)
    for (layer, orientation), edges in groups.items():
        info = _layer_info(process, layer)
        if _coupling_value(info, 0) is None:
            continue
        events = defaultdict(list)
        for index, edge in enumerate(edges):
            events[edge["start"]].append((1, index))
            events[edge["end"]].append((-1, index))
        active, opened = [], {}
        def close(left, right, position):
            if left is None or right is None:
                return
            row = opened.pop((left[2], right[2]), None)
            if row:
                start, coefficient, pair = row
                caps[pair] += (position - start) * coefficient
        def begin(left, right, position):
            if left is None or right is None:
                return
            a, b = edges[left[2]], edges[right[2]]
            spacing = b["fixed"] - a["fixed"]
            if a["side"] != 1 or b["side"] != -1 or a["net"] == b["net"] or not _EPS < spacing <= max_spacing:
                return
            coefficient = _coupling_value(info, spacing)
            if coefficient:
                opened[(left[2], right[2])] = (position, coefficient, tuple(sorted((a["net"], b["net"]))))
        for position, changes in sorted(events.items()):
            for action, index in sorted(changes):
                edge = edges[index]
                key = (edge["fixed"], edge["side"], index)
                offset = bisect.bisect_left(active, key)
                before = active[offset - 1] if offset else None
                if action == -1:
                    after = active[offset + 1] if offset + 1 < len(active) else None
                    close(before, key, position)
                    close(key, after, position)
                    active.pop(offset)
                    begin(before, after, position)
                else:
                    after = active[offset] if offset < len(active) else None
                    close(before, after, position)
                    active.insert(offset, key)
                    begin(before, key, position)
                    begin(key, after, position)
    return [(left, right, value) for (left, right), value in sorted(caps.items()) if value > 0]


def _cross_layer_caps(net_records: Mapping[str, dict[str, Any]], process: Mapping[str, Any], max_order: int):
    items = []
    for net, record in net_records.items():
        by_layer: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for index, raw in enumerate(record["rectangles"]):
            if not isinstance(raw, Mapping):
                raise ValueError("%s.rectangles[%d] is not an object" % (net, index))
            by_layer[str(raw.get("layer", "")).strip()].append(raw)
        for layer, raw_rectangles in by_layer.items():
            info = _layer_info(process, layer)
            order = int(info.get("order", 0))
            for index, rect in enumerate(_dedupe_rectangles(raw_rectangles, "%s.%s.rectangles" % (net, layer))):
                items.append({"net": net, "layer": layer, "order": order, "rect": rect, "index": index})
    if not items:
        return []
    max_distance = max((_number(_layer_info(process, item["layer"]).get("width_um"), "layer width", True) for item in items), default=1.0) * 5.0
    pairs = _bucket_pairs(items, max_distance, lambda item: item["rect"])
    merged: dict[tuple[str, str], float] = defaultdict(float)
    for left_i, right_i in pairs:
        left, right = items[left_i], items[right_i]
        if left["net"] == right["net"] or left["layer"] == right["layer"]:
            continue
        if abs(left["order"] - right["order"]) > max_order:
            continue
        x0 = max(left["rect"][0], right["rect"][0]); x1 = min(left["rect"][2], right["rect"][2])
        y0 = max(left["rect"][1], right["rect"][1]); y1 = min(left["rect"][3], right["rect"][3])
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if area <= _EPS:
            continue
        lower = left if left["order"] < right["order"] else right
        value = _cross_value(_layer_info(process, lower["layer"]))
        if value is None:
            value = _cross_value(_layer_info(process, left["layer"])) or _cross_value(_layer_info(process, right["layer"]))
        if value is None:
            continue
        merged[tuple(sorted((left["net"], right["net"]))) ] += value * area
    return [(left, right, value) for (left, right), value in sorted(merged.items()) if value > 0.0]


def _mesh(net_records: Mapping[str, dict[str, Any]], process: Mapping[str, Any]):
    cells = []
    by_net: dict[str, list[int]] = defaultdict(list)
    # Split a rectangle at all anchors that it owns.  This is the minimum
    # finite-volume partition needed to preserve multiple terminal anchors.
    for net, record in net_records.items():
        grouped_raw: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for index, raw in enumerate(record["rectangles"]):
            if not isinstance(raw, Mapping):
                raise ValueError("%s.rectangles[%d] is not an object" % (net, index))
            grouped_raw[str(raw.get("layer", "")).strip()].append(raw)
        grouped: dict[str, list[tuple[float, float, float, float]]] = {
            layer: _dedupe_rectangles(raw_rectangles, "%s.%s.rectangles" % (net, layer))
            for layer, raw_rectangles in grouped_raw.items()
        }
        for layer, rectangles in grouped.items():
            anchors = []
            for index, raw in enumerate(record["anchors"]):
                if str(raw.get("layer", "")).strip().casefold() != layer.casefold():
                    continue
                anchors.append((_number(raw.get("x"), "%s.anchor[%d].x" % (net, index)), _number(raw.get("y"), "%s.anchor[%d].y" % (net, index))))
            for rect in rectangles:
                xs = {rect[0], rect[2]}; ys = {rect[1], rect[3]}
                for x, y in anchors:
                    if _contains(rect, x, y): xs.add(min(rect[2], max(rect[0], x))); ys.add(min(rect[3], max(rect[1], y)))
                xs = sorted(xs); ys = sorted(ys)
                for x0, x1 in zip(xs, xs[1:]):
                    for y0, y1 in zip(ys, ys[1:]):
                        if x1 - x0 > _EPS and y1 - y0 > _EPS:
                            cell = {"id": len(cells), "net": net, "layer": layer, "rect": (x0, y0, x1, y1)}
                            cells.append(cell); by_net[net].append(cell["id"])
    if not cells:
        return cells, by_net, []
    # Match opposite finite-volume faces by exact coordinate and overlapping
    # interval.  Sorting makes this linear in the number of candidate faces.
    faces: dict[tuple[str, str, float, str], list[tuple[float, float, int]]] = defaultdict(list)
    for cell in cells:
        x0, y0, x1, y1 = cell["rect"]
        faces[(cell["net"], cell["layer"], round(x0, 9), "v-left")].append((y0, y1, cell["id"]))
        faces[(cell["net"], cell["layer"], round(x1, 9), "v-right")].append((y0, y1, cell["id"]))
        faces[(cell["net"], cell["layer"], round(y0, 9), "h-bottom")].append((x0, x1, cell["id"]))
        faces[(cell["net"], cell["layer"], round(y1, 9), "h-top")].append((x0, x1, cell["id"]))
    resistors = []
    seen = set()
    for key, first in list(faces.items()):
        net, layer, coordinate, side = key
        opposite = {"v-left": "v-right", "v-right": "v-left", "h-bottom": "h-top", "h-top": "h-bottom"}[side]
        other = faces.get((net, layer, coordinate, opposite), [])
        first = sorted(first); other = sorted(other)
        i = j = 0
        while i < len(first) and j < len(other):
            a0, a1, aid = first[i]; b0, b1, bid = other[j]
            overlap = min(a1, b1) - max(a0, b0)
            if overlap > _EPS and aid != bid and tuple(sorted((aid, bid))) not in seen:
                seen.add(tuple(sorted((aid, bid))))
                ca, cb = cells[aid], cells[bid]
                if side.startswith("v"):
                    distance = abs((ca["rect"][0] + ca["rect"][2]) / 2.0 - (cb["rect"][0] + cb["rect"][2]) / 2.0)
                else:
                    distance = abs((ca["rect"][1] + ca["rect"][3]) / 2.0 - (cb["rect"][1] + cb["rect"][3]) / 2.0)
                info = _layer_info(process, layer)
                rpsq = _number(info.get("resistance_ohm_per_square"), "%s resistance" % layer, True)
                if distance > _EPS:
                    resistors.append((aid, bid, rpsq * distance / overlap))
            if a1 < b1 - _EPS: i += 1
            else: j += 1
    return cells, by_net, resistors


def extract_parasitics(geometry: Mapping[str, Any], process: Mapping[str, Any], mode: str = "c") -> dict[str, Any]:
    """Extract positive ground/mutual C, optionally with distributed RCC."""
    mode = str(mode).strip().lower()
    if mode not in {"c", "rcc"}:
        raise ValueError("mode must be 'c' or 'rcc'")
    design, net_records, ports = _validate_geometry(geometry)
    if not isinstance(process, Mapping):
        raise ValueError("process must be an object returned by build_process")
    ground_name = next((name for name in net_records if name.casefold() == "gnd"), "0")
    net_rects: dict[str, dict[str, list[tuple[float, float, float, float]]]] = defaultdict(lambda: defaultdict(list))
    net_area: dict[str, float] = defaultdict(float); net_perim: dict[str, float] = defaultdict(float)
    for net, record in net_records.items():
        by_layer: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
        for index, raw in enumerate(record["rectangles"]):
            layer = str(raw.get("layer", "")).strip()
            _layer_info(process, layer)
            by_layer[layer].append(_rect(raw, "%s.rectangles[%d]" % (net, index)))
        for layer, raw_rects in by_layer.items():
            rects = _dedupe_rectangles(
                [{"x0": r[0], "y0": r[1], "x1": r[2], "y1": r[3]} for r in raw_rects],
                "%s.%s.rectangles" % (net, layer),
            )
            net_rects[net][layer] = rects
            net_area[net] += sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
            net_perim[net] += _perimeter(rects)
    capacitors = []
    ground_total = 0.0
    cap_index = 0
    for net in sorted(net_records):
        if net.casefold() in {"gnd", "0"}:
            continue
        for layer in sorted(net_rects[net]):
            info = _layer_info(process, layer)
            area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in net_rects[net][layer])
            perimeter = _perimeter(net_rects[net][layer])
            value = area * _number(info.get("ground_capacitance_pf_per_um2"), "%s area cap" % layer, True)
            value += perimeter * _number(info.get("edge_capacitance_pf_per_um"), "%s edge cap" % layer, True)
            if value > 0.0:
                capacitors.append({"name": "CPEX%05d" % cap_index, "node1": net, "node2": ground_name, "value_pf": value})
                cap_index += 1; ground_total += value
    same_limit = process.get("max_spacing")
    if same_limit is None:
        same_limit = max((_number(_layer_info(process, l).get("width_um"), "layer width", True) for n in net_records for l in net_rects[n]), default=1.0) * 5.0
    same = _same_layer_caps(net_records, process, _number(same_limit, "max_spacing", True))
    cross = _cross_layer_caps(net_records, process, int(process.get("max_cross_layer", 3)))
    coupling_total = 0.0
    for left, right, value in same + cross:
        if left == right or value <= 0.0:
            continue
        capacitors.append({"name": "CPEX%05d" % cap_index, "node1": left, "node2": right, "value_pf": value})
        cap_index += 1; coupling_total += value

    anchor_net: dict[str, str] = {}
    for net, record in net_records.items():
        for index, raw in enumerate(record["anchors"]):
            anchor = _json_mapping(raw, "%s.anchors[%d]" % (net, index))
            name = _anchor_name(anchor, "%s.anchors[%d]" % (net, index))
            if name in anchor_net and anchor_net[name] != net:
                raise ValueError("anchor %s belongs to different nets" % name)
            layer = str(anchor["layer"]).strip()
            x = _number(anchor["x"], name + ".x"); y = _number(anchor["y"], name + ".y")
            _layer_info(process, layer)
            if not any(_contains(rect, x, y) for rect in net_rects[net].get(layer, [])):
                raise ValueError("anchor %s is not on a physical metal rectangle" % name)
            anchor_net[name] = net
    terminal_nodes: dict[str, str] = {}
    port_nodes: dict[str, str] = {}
    resistors = []
    limitations = list(process.get("limitations", [])) if isinstance(process.get("limitations"), Sequence) else []
    if mode == "rcc":
        cells, by_net, mesh_resistors = _mesh(net_records, process)
        # Resolve anchors to mesh cells. Multiple contacts on one physical
        # FEOL component are an ideal short; distinct FEOL components remain
        # separate nodes connected only by their extracted BEOL paths.
        anchor_cells: dict[str, list[int]] = defaultdict(list)
        anchor_layers: dict[str, str] = {}
        for net, record in net_records.items():
            for index, raw in enumerate(record["anchors"]):
                anchor = _json_mapping(raw, "%s.anchors[%d]" % (net, index))
                name = _anchor_name(anchor, "%s.anchors[%d]" % (net, index))
                layer = str(anchor["layer"]).strip()
                x = _number(anchor["x"], name + ".x")
                y = _number(anchor["y"], name + ".y")
                hits = [cid for cid in by_net.get(net, [])
                        if cells[cid]["layer"].casefold() == layer.casefold()
                        and _contains(cells[cid]["rect"], x, y)]
                if not hits:
                    raise ValueError("anchor %s is not on a physical metal rectangle" % name)
                is_feol = name.casefold().startswith("feol:")
                is_port = name in ports
                if name in anchor_cells and not (is_feol or is_port):
                    raise ValueError("duplicate anchor name %s" % name)
                previous_layer = anchor_layers.get(name)
                if previous_layer and previous_layer.casefold() != layer.casefold() and not is_port:
                    raise ValueError("anchor %s spans multiple routing layers" % name)
                anchor_layers.setdefault(name, layer)
                anchor_cells[name].append(min(hits))

        # Connect contacts belonging to the same ideal FEOL component by
        # assigning their M1 mesh cells the same electrical node.
        ideal_parent = {cell["id"]: cell["id"] for cell in cells}

        def ideal_find(cell_id: int) -> int:
            while ideal_parent[cell_id] != cell_id:
                ideal_parent[cell_id] = ideal_parent[ideal_parent[cell_id]]
                cell_id = ideal_parent[cell_id]
            return cell_id

        def ideal_union(left: int, right: int) -> None:
            left, right = ideal_find(left), ideal_find(right)
            if left != right:
                ideal_parent[max(left, right)] = min(left, right)

        for name, ids in anchor_cells.items():
            if name.casefold().startswith("feol:"):
                unique_ids = sorted(set(ids))
                for cell_id in unique_ids[1:]:
                    ideal_union(unique_ids[0], cell_id)
        port_roots: dict[int, str] = {}
        for port in ports:
            ids = sorted(set(anchor_cells.get(port, [])))
            if not ids:
                raise ValueError("port %s has no physical anchor" % port)
            for cell_id in ids:
                root = ideal_find(cell_id)
                previous = port_roots.get(root)
                if previous and previous != port:
                    raise ValueError("ports %s and %s share an electrical node" % (previous, port))
                port_roots[root] = port


        node_names = {}
        for cell in cells:
            root = ideal_find(cell["id"])
            node_names[cell["id"]] = port_roots.get(root, "__PEX_N%06d" % root)
        anchor_nodes = {
            name: node_names[ideal_find(min(ids))]
            for name, ids in anchor_cells.items() if ids
        }
        representatives = {
            net: (node_names[ids[0]] if ids else net)
            for net, ids in by_net.items()
        }
        for net in net_records:
            representatives.setdefault(net, net)
        unanchored_feol_nodes: dict[str, str] = {}
        component_nets: dict[str, str] = {}
        for device in geometry.get("devices", []):
            if not isinstance(device, Mapping):
                raise ValueError("device record is not an object")
            dname = str(device.get("name", "")).strip()
            terms = device.get("terminals")
            components = device.get("terminal_components")
            if not isinstance(terms, Mapping):
                raise ValueError("device %s has no terminals" % dname)
            if not isinstance(components, Mapping):
                raise ValueError("device %s has no terminal_components" % dname)
            for terminal in ("D", "G", "S", "B"):
                if terminal not in terms:
                    raise ValueError("device %s missing terminal %s" % (dname, terminal))
                key = "%s:%s" % (dname, terminal)
                net = str(terms[terminal])
                if net not in net_records:
                    raise ValueError("device %s terminal %s references unknown net %s" % (dname, terminal, net))
                component = str(components.get(terminal, "")).strip()
                if not component:
                    raise ValueError("device %s terminal %s has no physical component" % (dname, terminal))
                previous_net = component_nets.get(component)
                if previous_net is not None and previous_net != net:
                    raise ValueError("device terminal component %s belongs to different nets" % component)
                component_nets[component] = net
                if component in anchor_nodes:
                    if anchor_net.get(component) != net:
                        raise ValueError("device %s terminal %s anchor %s does not belong to net %s" % (
                            dname, terminal, component, net))
                    terminal_nodes[key] = anchor_nodes[component]
                elif component.casefold().startswith("feol:") and not anchor_cells.get(component):
                    if component not in unanchored_feol_nodes:
                        unanchored_feol_nodes[component] = "__PEX_FEOL_%06d" % len(unanchored_feol_nodes)
                    terminal_nodes[key] = unanchored_feol_nodes[component]
                else:
                    raise ValueError("device %s terminal %s has no physical anchor %s" % (dname, terminal, component))
        if unanchored_feol_nodes:
            limitations.append("Uncontacted FEOL-only components use ideal terminal nodes without BEOL parasitics.")
        for port in ports:
            if port not in anchor_nodes:
                raise ValueError("port %s has no physical anchor" % port)
            port_nodes[port] = anchor_nodes[port]
        for aid, bid, value in mesh_resistors:
            if value <= 0.0:
                continue
            node1, node2 = node_names[aid], node_names[bid]
            if node1 != node2:
                resistors.append({"name": "RPEX%05d" % len(resistors), "node1": node1, "node2": node2, "value_ohm": value})
        via_pairs: list[tuple[int, int]] = []
        for net, record in net_records.items():
            for index, raw in enumerate(record["vias"]):
                via = _json_mapping(raw, "%s.vias[%d]" % (net, index))
                layer = str(via.get("layer", "")).strip()
                info = _via_info(process, layer)
                x = _number(via.get("x"), "%s.vias[%d].x" % (net, index))
                y = _number(via.get("y"), "%s.vias[%d].y" % (net, index))
                low = str(via.get("lower_layer", info.get("lower_layer", "")))
                high = str(via.get("upper_layer", info.get("upper_layer", "")))
                low_hits = [cid for cid in by_net.get(net, [])
                            if cells[cid]["layer"].casefold() == low.casefold()
                            and _contains(cells[cid]["rect"], x, y)]
                high_hits = [cid for cid in by_net.get(net, [])
                             if cells[cid]["layer"].casefold() == high.casefold()
                             and _contains(cells[cid]["rect"], x, y)]
                if not low_hits or not high_hits:
                    raise ValueError("via %s at %.9g,%.9g is not connected to both metal layers" % (layer, x, y))
                low_cell, high_cell = min(low_hits), min(high_hits)
                via_pairs.append((low_cell, high_cell))
                node1, node2 = node_names[low_cell], node_names[high_cell]
                if node1 != node2:
                    value = _number(via.get("resistance_ohm", info.get("resistance_ohm")), "%s resistance" % layer, True)
                    resistors.append({"name": "RPEX%05d" % len(resistors), "node1": node1, "node2": node2, "value_ohm": value})
        parent = {cell["id"]: cell["id"] for cell in cells}

        def find(cell_id: int) -> int:
            while parent[cell_id] != cell_id:
                parent[cell_id] = parent[parent[cell_id]]
                cell_id = parent[cell_id]
            return cell_id

        def union(left: int, right: int) -> None:
            left, right = find(left), find(right)
            if left != right:
                parent[right] = left

        for left, right, _value in mesh_resistors:
            union(left, right)
        for left, right in via_pairs:
            union(left, right)
        for name, ids in anchor_cells.items():
            if name.casefold().startswith("feol:"):
                unique_ids = sorted(set(ids))
                for cell_id in unique_ids[1:]:
                    union(unique_ids[0], cell_id)
        components: dict[str, set[int]] = defaultdict(set)
        for name, net in anchor_net.items():
            ids = anchor_cells.get(name, [])
            if not ids:
                raise ValueError("anchor %s has no physical mesh cell" % name)
            components[net].update(find(cell_id) for cell_id in ids)
        for net, roots in components.items():
            if len(roots) > 1:
                raise ValueError("net %s has disconnected physical anchor components" % net)
        # Ground/self C is attached to a representative finite-volume node;
        # the metal resistors, rather than a lumped top-pin resistor, retain
        # the distributed path between all terminal anchors.
        for capacitor in capacitors:
            if capacitor["node1"] in representatives:
                capacitor["node1"] = representatives[capacitor["node1"]]
            if capacitor["node2"] in representatives:
                capacitor["node2"] = representatives[capacitor["node2"]]
        limitations.append("RCC uses rectangle finite-volume cells split at anchors; mesh/reduction error is bounded by input rectangle and anchor spacing, not calibrated.")
    else:
        for device in geometry.get("devices", []):
            if not isinstance(device, Mapping):
                raise ValueError("device record is not an object")
            dname = str(device.get("name", "")).strip(); terms = device.get("terminals")
            if not dname or not isinstance(terms, Mapping):
                raise ValueError("device record lacks name or terminals")
            for terminal in ("D", "G", "S", "B"):
                net = str(terms.get(terminal, ""))
                if net not in net_records:
                    raise ValueError("device %s terminal %s references unknown net" % (dname, terminal))
                terminal_nodes["%s:%s" % (dname, terminal)] = net
        for port in ports:
            anchors = [
                net for net, rec in net_records.items()
                if any(str(a.get("name", "")) == port for a in rec["anchors"])
            ]
            if len(anchors) != 1:
                raise ValueError("port %s must have exactly one physical net anchor" % port)
            port_nodes[port] = anchors[0]
    status = str(process.get("status", "seed_only"))
    if status not in {"seed_only", "estimated", "explicit_coupling"}:
        status = "seed_only"
    used_layers = {layer for layers in net_rects.values() for layer in layers}
    if status == "explicit_coupling":
        explicit = set(process.get("explicit_layers", []))
        if not used_layers.issubset(explicit):
            status = "estimated" if process.get("source") == "pdk-estimate" else "seed_only"
            limitations.append("Explicit coupling overrides do not cover all used metal layers.")
    if status == "explicit_coupling" and not same and not cross:
        limitations.append("Explicit coupling process supplied no geometrically overlapping/facing candidate pairs.")
    return {
        "schema_version": 1, "design": design, "status": status, "mode": mode,
        "signoff": False, "ground_capacitance_pf": ground_total,
        "coupling_capacitance_pf": coupling_total,
        "resistance_ohm": sum(float(item["value_ohm"]) for item in resistors),
        "capacitors": capacitors, "resistors": resistors,
        "terminal_nodes": terminal_nodes, "port_nodes": port_nodes,
        "summary": {
            "nets": len(net_records), "ports": len(ports),
            "devices": len(geometry.get("devices", [])) if isinstance(geometry.get("devices", []), Sequence) else 0,
            "rectangles": sum(len(r.get("rectangles", [])) for r in net_records.values()),
            "limitations": sorted(set(limitations)),
        },
        "provenance": dict(process.get("provenance", {})) if isinstance(process.get("provenance"), Mapping) else {},
    }


def _check_case_unique(values: Sequence[str], label: str) -> None:
    found: dict[str, str] = {}
    for value in values:
        folded = value.casefold()
        if folded in found and found[folded] != value:
            raise ValueError("%s collide after case-fold: %s/%s" % (label, found[folded], value))
        found[folded] = value


def write_spice(geometry: Mapping[str, Any], parasitics: Mapping[str, Any], path: str | Path) -> None:
    """Write an ngspice-compatible top subcircuit with X transistor wrappers."""
    design, net_records, ports = _validate_geometry(geometry)
    if not isinstance(parasitics, Mapping):
        raise ValueError("parasitics must be an object returned by extract_parasitics")
    if str(parasitics.get("design", design)) != design:
        raise ValueError("parasitics design does not match geometry")
    port_nodes = parasitics.get("port_nodes", {})
    if not isinstance(port_nodes, Mapping) or any(port not in port_nodes for port in ports):
        raise ValueError("parasitics is missing a port node")
    node_values = [str(value) for value in port_nodes.values()]
    node_values += [str(item.get("node1")) for item in parasitics.get("capacitors", []) if isinstance(item, Mapping)]
    node_values += [str(item.get("node2")) for item in parasitics.get("capacitors", []) if isinstance(item, Mapping)]
    node_values += [str(item.get("node1")) for item in parasitics.get("resistors", []) if isinstance(item, Mapping)]
    node_values += [str(item.get("node2")) for item in parasitics.get("resistors", []) if isinstance(item, Mapping)]
    _check_case_unique(node_values, "node names")
    _check_case_unique(ports, "port names")
    devices = geometry.get("devices", [])
    if not isinstance(devices, Sequence) or isinstance(devices, (str, bytes)):
        raise ValueError("geometry.devices must be a list")
    device_names = [str(d.get("name", "")) for d in devices if isinstance(d, Mapping)]
    _check_case_unique(device_names, "device names")
    names = []
    lines = ["* ICsprout55 open-source GDS PEX; signoff=false; status=%s" % parasitics.get("status", "seed_only")]
    lines.append(".subckt %s %s" % (design, " ".join(ports)))
    terminal_nodes = parasitics.get("terminal_nodes", {})
    if not isinstance(terminal_nodes, Mapping):
        raise ValueError("parasitics.terminal_nodes must be an object")
    for device in devices:
        if not isinstance(device, Mapping):
            raise ValueError("device is not an object")
        name = str(device.get("name", "")); model = str(device.get("model", ""))
        terms = device.get("terminals")
        if not model or not isinstance(terms, Mapping):
            raise ValueError("device %s lacks model or terminals" % name)
        nodes = []
        for terminal in ("D", "G", "S", "B"):
            key = "%s:%s" % (name, terminal)
            if key not in terminal_nodes:
                raise ValueError("parasitics missing terminal node %s" % key)
            nodes.append(str(terminal_nodes[key]))
        params = ["w=%su" % _number(device.get("w_um"), name + ".w_um", True),
                  "l=%su" % _number(device.get("l_um"), name + ".l_um", True),
                  "nf=1", "mismod=0"]
        # model_wrapper1_hsp.lib consumes AS/AD in um^2 with the ngspice
        # pico suffix and PS/PD in um with the micro suffix.  They are passed
        # only when the physical extractor supplied the corresponding value.
        for field, parameter, suffix in (("as_um2", "as", "p"), ("ad_um2", "ad", "p"), ("ps_um", "ps", "u"), ("pd_um", "pd", "u")):
            if field in device and device[field] is not None:
                value = _number(device[field], name + "." + field)
                if value < 0.0:
                    raise ValueError("%s must not be negative" % (name + "." + field))
                params.append("%s=%s%s" % (parameter, value, suffix))
        line = "X%s %s %s %s" % (name, " ".join(nodes), model, " ".join(params))
        lines.append(line); names.append("X" + name)
    for item in parasitics.get("resistors", []):
        record = _json_mapping(item, "parasitics.resistors")
        name = str(record.get("name", "")); n1 = str(record.get("node1", "")); n2 = str(record.get("node2", "")); value = _number(record.get("value_ohm"), name + ".value_ohm", True)
        if not name or not n1 or not n2 or n1.casefold() == n2.casefold():
            raise ValueError("invalid resistor record %s" % name)
        lines.append("R%s %s %s %.12g" % (name, n1, n2, value)); names.append("R" + name)
    for item in parasitics.get("capacitors", []):
        record = _json_mapping(item, "parasitics.capacitors")
        name = str(record.get("name", "")); n1 = str(record.get("node1", "")); n2 = str(record.get("node2", "")); value = _number(record.get("value_pf"), name + ".value_pf", True)
        if not name or not n1 or not n2 or n1.casefold() == n2.casefold():
            raise ValueError("invalid capacitor record %s" % name)
        lines.append("C%s %s %s %.12g" % (name, n1, n2, value * 1.0e-12)); names.append("C" + name)
    _check_case_unique(names, "SPICE element names")
    lines.extend([".ends %s" % design, ""])
    output = Path(path)
    try:
        output.write_text("\n".join(lines))
    except OSError as exc:
        raise ValueError("cannot write SPICE output %s: %s" % (output, exc)) from exc


__all__ = ["build_process", "extract_parasitics", "write_spice"]
