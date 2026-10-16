#!/usr/bin/env python3
"""Strict MOS topology diagnostics for source-free ICsprout55 GDS extraction."""
from __future__ import annotations

import collections
import hashlib
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import klayout.db as db
except ImportError:  # pragma: no cover - KLayout's legacy package name
    import pya as db  # type: ignore

_TOL_UM = 1.0e-9
_SPICE_NUMBER = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|mil|[tgkmunpf])?$",
    re.IGNORECASE,
)
_SUFFIX = {
    "meg": 1.0e6, "mil": 25.4e-6, "t": 1.0e12, "g": 1.0e9,
    "k": 1.0e3, "m": 1.0e-3, "u": 1.0e-6, "n": 1.0e-9,
    "p": 1.0e-12, "f": 1.0e-15,
}


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _name(value: Any, label: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError("%s must be a non-empty name" % label)
    return result


def _port_names(geometry: Mapping[str, Any]) -> list[str]:
    ports = geometry.get("ports", [])
    if not isinstance(ports, Sequence) or isinstance(ports, (str, bytes)):
        raise ValueError("geometry.ports must be a list")
    result = []
    seen = set()
    for item in ports:
        port = _name(item.get("name") if isinstance(item, Mapping) else item, "port")
        folded = port.casefold()
        if folded in seen:
            raise ValueError("port names collide after case-fold: %s" % port)
        seen.add(folded)
        result.append(port)
    return result


def _net_records(geometry: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    records = geometry.get("nets", [])
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("geometry.nets must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    folded = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError("geometry.nets[%d] must be an object" % index)
        name = _name(record.get("name"), "geometry.nets[%d].name" % index)
        key = name.casefold()
        if key in folded:
            raise ValueError("logical net names collide after case-fold: %s/%s" % (folded[key], name))
        folded[key] = name
        result[name] = record
    return result


def _port_logical_nets(geometry: Mapping[str, Any]) -> dict[str, str]:
    ports = _port_names(geometry)
    nets = _net_records(geometry)
    result = {}
    for port in ports:
        matches = []
        for logical, record in nets.items():
            anchors = record.get("anchors", [])
            if not isinstance(anchors, Sequence) or isinstance(anchors, (str, bytes)):
                raise ValueError("geometry net %s anchors must be a list" % logical)
            if any(isinstance(anchor, Mapping) and
                   str(anchor.get("name", "")).casefold() == port.casefold()
                   for anchor in anchors):
                matches.append(logical)
        if len(matches) != 1:
            raise ValueError("port %s must anchor exactly one GDS logical net; found %d" %
                             (port, len(matches)))
        result[port] = matches[0]
    return result


def _geometry_devices(geometry: Mapping[str, Any]) -> list[dict[str, Any]]:
    devices = geometry.get("devices", [])
    if not isinstance(devices, Sequence) or isinstance(devices, (str, bytes)):
        raise ValueError("geometry.devices must be a list")
    nets = _net_records(geometry)
    names = set()
    result = []
    for index, raw in enumerate(devices):
        if not isinstance(raw, Mapping):
            raise ValueError("geometry.devices[%d] must be an object" % index)
        device = dict(raw)
        device_name = _name(device.get("name"), "geometry.devices[%d].name" % index)
        folded = device_name.casefold()
        if folded in names:
            raise ValueError("GDS MOS device names collide after case-fold: %s" % device_name)
        names.add(folded)
        model = _name(device.get("model"), device_name + ".model").lower()
        terminals = device.get("terminals")
        if not isinstance(terminals, Mapping):
            raise ValueError("device %s has no terminal mapping" % device_name)
        normalized_terminals = {}
        for terminal in ("D", "G", "S", "B"):
            logical = _name(terminals.get(terminal), "%s.%s" % (device_name, terminal))
            if logical not in nets:
                raise ValueError("device %s terminal %s references unknown GDS net %s" %
                                 (device_name, terminal, logical))
            normalized_terminals[terminal] = logical
        device.update(name=device_name, model=model, terminals=normalized_terminals)
        for field in ("w_um", "l_um"):
            value = float(device.get(field, 0.0))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError("device %s.%s must be finite and positive" % (device_name, field))
            device[field] = value
        result.append(device)
    return result


def _source_circuit(path: str | Path, design: str):
    source = db.Netlist()
    try:
        source.read(str(Path(path).expanduser().resolve()), db.NetlistSpiceReader())
    except Exception as exc:
        raise ValueError("cannot parse reference SPICE netlist %s: %s" % (path, exc)) from exc
    source.flatten()
    circuit = source.circuit_by_name(design)
    if circuit is None:
        candidates = list(source.top_circuits())
        if len(candidates) != 1:
            raise ValueError("reference netlist has no unique flattened top circuit named %s" % design)
        circuit = candidates[0]
    return source, circuit


def _set_compare_semantics(netlist) -> None:
    parameters = (
        db.EqualDeviceParameters(db.DeviceClassMOS4Transistor.PARAM_W, _TOL_UM, 0.0) +
        db.EqualDeviceParameters(db.DeviceClassMOS4Transistor.PARAM_L, _TOL_UM, 0.0)
    )
    for device_class in netlist.each_device_class():
        if not isinstance(device_class, db.DeviceClassMOS4Transistor):
            continue
        device_class.strict = False
        device_class.equal_parameters = parameters


def _build_geometry_netlist(geometry: Mapping[str, Any]):
    design = _name(geometry.get("design"), "geometry.design")
    nets = _net_records(geometry)
    port_nets = _port_logical_nets(geometry)
    devices = _geometry_devices(geometry)
    netlist = db.Netlist()
    circuit = db.Circuit()
    circuit.name = design.upper()
    netlist.add(circuit)
    logical_nets = {name: circuit.create_net(name.upper()) for name in nets}
    classes = {}
    for device in devices:
        model = device["model"].upper()
        if model not in classes:
            device_class = db.DeviceClassMOS4Transistor()
            device_class.name = model
            netlist.add(device_class)
            classes[model] = device_class
        instance = circuit.create_device(classes[model], device["name"])
        for terminal in ("S", "G", "D", "B"):
            instance.connect_terminal(terminal, logical_nets[device["terminals"][terminal]])
        for parameter, field in (("W", "w_um"), ("L", "l_um")):
            instance.set_parameter(parameter, device[field])
        for parameter, field in (("AS", "as_um2"), ("AD", "ad_um2"),
                                 ("PS", "ps_um"), ("PD", "pd_um")):
            if field in device and device[field] is not None:
                instance.set_parameter(parameter, float(device[field]))
    for port, logical in port_nets.items():
        pin = circuit.create_pin(port.upper())
        circuit.connect_pin(pin, logical_nets[logical])
    return netlist, circuit, devices, port_nets


def _near(left: float, right: float) -> bool:
    return abs(left - right) <= _TOL_UM


def _normalize_physical_parallel(geometry: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Combine only MOS devices already parallel on identical logical nets."""
    normalized = dict(geometry)
    devices = [dict(device) for device in _geometry_devices(geometry)]
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = collections.defaultdict(list)
    for device in devices:
        drain_source = tuple(sorted((
            device["terminals"]["D"].casefold(),
            device["terminals"]["S"].casefold(),
        )))
        grouped[(device["model"], device["terminals"]["G"].casefold(),
                 device["terminals"]["B"].casefold(), round(device["l_um"], 9),
                 drain_source)].append(device)

    remaining = {device["name"]: device for device in devices}
    parallel_groups = []
    for _key, members in sorted(grouped.items(), key=lambda row: tuple(map(str, row[0]))):
        if len(members) < 2:
            continue
        individual_widths = [float(member["w_um"]) for member in members]
        total_width = sum(individual_widths)
        keeper = members[0]
        keeper["w_um"] = total_width
        parallel_groups.append({
            "model": keeper["model"],
            "gate_logical_net": keeper["terminals"]["G"],
            "bulk_logical_net": keeper["terminals"]["B"],
            "source_drain_logical_nets": sorted(
                {keeper["terminals"]["D"], keeper["terminals"]["S"]},
                key=str.casefold),
            "members": [member["name"] for member in members],
            "widths_um": individual_widths,
            "normalized_width_um": total_width,
        })
        for member in members[1:]:
            remaining.pop(member["name"], None)
    normalized["devices"] = [remaining[device["name"]] for device in devices
                              if device["name"] in remaining]
    return normalized, parallel_groups


def compare_source_netlist(
    geometry: Mapping[str, Any],
    source_netlist: str | Path,
    rcc_nodes_by_logical: Mapping[str, Sequence[str]] | None = None,
    rcc_mos_nodes_by_device: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Compare the direct GDS MOS graph to flattened original SPICE with strict pins."""
    design = _name(geometry.get("design"), "geometry.design")
    reference_path = Path(source_netlist).expanduser().resolve()
    source, source_top = _source_circuit(reference_path, design)
    raw_reference_devices = list(source_top.each_device())
    raw_reference_model_counts = collections.Counter(
        device.device_class().name.lower() for device in raw_reference_devices)
    raw_reference_mos = len(raw_reference_devices)
    normalized_geometry, parallel_groups = _normalize_physical_parallel(geometry)
    layout, layout_top, normalized_devices, port_nets = _build_geometry_netlist(normalized_geometry)
    _set_compare_semantics(source)
    _set_compare_semantics(layout)
    source.combine_devices()
    layout.combine_devices()
    # Match the bounded comparison policy used by the ICsprout55 LVS deck.
    # A false result under these limits is not independently sufficient to
    # classify a graph as physically mismatched; inspect its cross-reference.
    compare_policy = {
        "max_depth": 8,
        "max_branch_complexity": 1024,
        "max_resistance_ohms": 1.0e9,
        "min_capacitance_farads": 1.0e-18,
        "dont_consider_net_names": True,
        "with_log": True,
    }
    comparer = db.NetlistComparer()
    comparer.max_depth = compare_policy["max_depth"]
    comparer.max_branch_complexity = compare_policy["max_branch_complexity"]
    comparer.max_resistance = compare_policy["max_resistance_ohms"]
    comparer.min_capacitance = compare_policy["min_capacitance_farads"]
    comparer.dont_consider_net_names = compare_policy["dont_consider_net_names"]
    comparer.with_log = compare_policy["with_log"]
    reference_devices = list(source_top.each_device())
    source_pin_map = {pin.name().casefold(): pin for pin in source_top.each_pin()}
    layout_pin_map = {pin.name().casefold(): pin for pin in layout_top.each_pin()}
    port_mismatches = []
    if set(source_pin_map) != set(layout_pin_map):
        port_mismatches.append({
            "reference_only": sorted(set(source_pin_map) - set(layout_pin_map)),
            "gds_only": sorted(set(layout_pin_map) - set(source_pin_map)),
        })
    source_pin_net_users = collections.defaultdict(list)
    layout_pin_net_users = collections.defaultdict(list)
    for folded, pin in source_pin_map.items():
        net = source_top.net_for_pin(pin.id())
        if net is not None:
            source_pin_net_users[str(net.name).casefold()].append(folded)
    for folded, pin in layout_pin_map.items():
        net = layout_top.net_for_pin(pin.id())
        if net is not None:
            layout_pin_net_users[str(net.name).casefold()].append(folded)
    source_aliases = sorted(tuple(sorted(names)) for names in source_pin_net_users.values() if len(names) > 1)
    layout_aliases = sorted(tuple(sorted(names)) for names in layout_pin_net_users.values() if len(names) > 1)
    if source_aliases != layout_aliases:
        port_mismatches.append({"reference_port_aliases": source_aliases,
                                "gds_port_aliases": layout_aliases})
    for folded in sorted(set(source_pin_map) & set(layout_pin_map)):
        source_net = source_top.net_for_pin(source_pin_map[folded].id())
        layout_net = layout_top.net_for_pin(layout_pin_map[folded].id())
        if source_net is not None and layout_net is not None:
            comparer.same_nets(source_net, layout_net, True)
        elif source_net is not layout_net:
            port_mismatches.append({"port": source_pin_map[folded].name(),
                                    "reference_connected": source_net is not None,
                                    "gds_connected": layout_net is not None})
    xref = db.NetlistCrossReference()
    try:
        equivalent = bool(comparer.compare(source, layout, xref))
    except Exception as exc:
        raise ValueError("KLayout netlist comparison failed: %s" % exc) from exc

    reference_devices = list(source_top.each_device())
    raw_devices = _geometry_devices(geometry)
    device_pairs = list(xref.each_device_pair(source_top))
    net_pairs = list(xref.each_net_pair(source_top))
    device_status_counts = collections.Counter(pair.status().to_s() for pair in device_pairs)
    net_status_counts = collections.Counter(pair.status().to_s() for pair in net_pairs)
    matched_device_pairs = [pair for pair in device_pairs
                            if pair.status().to_s() == "Match" and pair.first() and pair.second()]
    matched_net_pairs = [pair for pair in net_pairs
                         if pair.status().to_s() == "Match" and pair.first() and pair.second()]
    raw_by_name = {device["name"]: device for device in raw_devices}
    raw_logical_names = {name.casefold(): name for name in _net_records(geometry)}
    reference_net_users = collections.defaultdict(list)
    for device in reference_devices:
        device_class = device.device_class()
        for terminal in ("D", "G", "S", "B"):
            net = device.net_for_terminal(device_class.terminal_id(terminal))
            if net is not None:
                reference_net_users[str(net.name).casefold()].append(
                    {"device": device.name, "terminal": terminal})
    reference_pins_by_net = collections.defaultdict(list)
    for folded, pin in source_pin_map.items():
        net = source_top.net_for_pin(pin.id())
        if net is not None:
            reference_pins_by_net[str(net.name).casefold()].append(pin.name())
    layout_net_users = collections.defaultdict(list)
    for device in raw_devices:
        for terminal in ("D", "G", "S", "B"):
            layout_net_users[device["terminals"][terminal].casefold()].append(
                {"device": device["name"], "terminal": terminal,
                 "origin_um": device.get("origin_um")})
    layout_ports_by_logical = collections.defaultdict(list)
    for port, logical in port_nets.items():
        layout_ports_by_logical[logical.casefold()].append(port)
    unmatched_reference_nets = []
    unmatched_gds_nets = []
    for pair in net_pairs:
        status = pair.status().to_s()
        if status == "Match" and pair.first() and pair.second():
            continue
        reference_net, layout_net = pair.first(), pair.second()
        if reference_net is not None:
            cluster = str(reference_net.name).casefold()
            unmatched_reference_nets.append({
                "name": reference_net.name,
                "status": status,
                "ambiguity_warning": status == "MatchWithWarning",
                "ports": sorted(reference_pins_by_net.get(cluster, [])),
                "mos_terminals": reference_net_users.get(cluster, []),
            })
        if layout_net is not None:
            logical = raw_logical_names.get(str(layout_net.name).casefold(), str(layout_net.name))
            unmatched_gds_nets.append({
                "name": logical,
                "status": status,
                "ambiguity_warning": status == "MatchWithWarning",
                "ports": sorted(layout_ports_by_logical.get(logical.casefold(), [])),
                "mos_terminals": layout_net_users.get(logical.casefold(), []),
            })
    grouped_members = {name: group for group in parallel_groups for name in group["members"]}
    normalized_to_raw = {}
    for device in normalized_devices:
        group = grouped_members.get(device["name"])
        normalized_to_raw[device["name"]] = group["members"] if group else [device["name"]]

    def source_device_record(device) -> dict[str, Any]:
        device_class = device.device_class()
        terminals = {}
        for terminal in ("D", "G", "S", "B"):
            net = device.net_for_terminal(device_class.terminal_id(terminal))
            terminals[terminal] = net.name if net is not None else None
        return {
            "name": device.name,
            "model": device_class.name.lower(),
            "w_um": float(device.parameter("W")),
            "l_um": float(device.parameter("L")),
            "terminals": terminals,
        }
    reference_unmatched = []
    layout_unmatched = []


    for pair in device_pairs:
        first, second = pair.first(), pair.second()
        status = pair.status().to_s()
        if status == "Match" and first and second:
            continue
        if first is not None:
            record = source_device_record(first)
            record["status"] = status
            record["ambiguity_warning"] = status == "MatchWithWarning"
            reference_unmatched.append(record)
        if second is not None:
            record = raw_by_name.get(second.name)
            record = dict(record) if record else {"name": second.name}
            record["status"] = status
            record["ambiguity_warning"] = status == "MatchWithWarning"
            layout_unmatched.append(record)

    if rcc_nodes_by_logical is not None:
        nodes_by_logical = {
            str(net).casefold(): sorted(set(map(str, nodes)))
            for net, nodes in rcc_nodes_by_logical.items()
        }
    else:
        nodes_by_logical = {
            record["name"].casefold(): [record["name"]]
            for record in normalized_geometry.get("nets", [])
        }
    emitted_nodes_by_device = {
        str(name).casefold(): terminals
        for name, terminals in (rcc_mos_nodes_by_device or {}).items()
    }
    reference_net_map = {}
    ambiguous_net_map = {}
    net_pair_by_reference = {
        str(pair.first().name).casefold(): pair for pair in net_pairs if pair.first() is not None
    }
    for pair in matched_net_pairs:
        reference_net, layout_net = pair.first(), pair.second()
        semantic_name = str(reference_net.name).strip()
        logical_folded = str(layout_net.name).strip().casefold()
        logical_name = raw_logical_names.get(logical_folded, str(layout_net.name).strip())
        if not semantic_name or not logical_name:
            continue
        reference_net_map[semantic_name] = {
            "logical_net": logical_name,
            "nodes": nodes_by_logical.get(logical_folded, [logical_name]),
            "status": "Match",
            "usable_exact_identity": True,
        }
    for pair in net_pairs:
        if pair.status().to_s() != "MatchWithWarning" or not pair.first() or not pair.second():
            continue
        semantic_name = str(pair.first().name).strip()
        logical_folded = str(pair.second().name).strip().casefold()
        logical_name = raw_logical_names.get(logical_folded, str(pair.second().name).strip())
        ambiguous_net_map[semantic_name] = {
            "selected_gds_logical_net": logical_name,
            "status": "MatchWithWarning",
            "usable_exact_identity": False,
            "ambiguity": "KLayout selected an unspecific candidate; no node map is asserted",
        }

    reference_device_map = {}
    for pair in matched_device_pairs:
        reference_device, layout_device = pair.first(), pair.second()
        raw_members = normalized_to_raw.get(layout_device.name, [])
        if not raw_members:
            continue
        terminal_nodes = {}
        member_terminal_nodes = {}
        terminal_statuses = {}
        complete = True
        for terminal in ("D", "G", "S", "B"):
            ref_net = reference_device.net_for_terminal(
                reference_device.device_class().terminal_id(terminal))
            gds_net = xref.other_net_for(ref_net) if ref_net is not None else None
            if gds_net is None:
                complete = False
                break
            logical_folded = str(gds_net.name).casefold()
            logical_name = raw_logical_names.get(logical_folded, str(gds_net.name))
            net_pair = net_pair_by_reference.get(str(ref_net.name).casefold())
            terminal_statuses[terminal] = (
                net_pair.status().to_s() if net_pair is not None else "NoMatch")
            allowed_terminals = ("D", "S") if terminal in ("D", "S") else (terminal,)
            all_nodes = set()
            for raw_name in raw_members:
                raw_record = raw_by_name.get(raw_name)
                if raw_record is None:
                    complete = False
                    break
                candidates = [
                    layout_terminal for layout_terminal in allowed_terminals
                    if raw_record["terminals"][layout_terminal].casefold() == logical_folded
                ]
                if not candidates:
                    complete = False
                    break
                actual_terminals = emitted_nodes_by_device.get(raw_name.casefold(), {})
                member_terminal_nodes.setdefault(terminal, {})[raw_name] = []
                for candidate in candidates:
                    node = actual_terminals.get(candidate, raw_record["terminals"][candidate])
                    member_terminal_nodes[terminal][raw_name].append(node)
                    all_nodes.add(node)
            if not complete:
                break
            ordered_nodes = sorted(all_nodes, key=str.casefold)
            terminal_nodes[terminal] = ordered_nodes[0] if len(ordered_nodes) == 1 else ordered_nodes
        if complete:
            reference_device_map[reference_device.name] = {
                "gds_device": raw_members[0] if len(raw_members) == 1 else None,
                "gds_members": list(raw_members),
                "normalized_gds_device": layout_device.name,
                "match_status": "Match",
                "terminal_match_statuses": terminal_statuses,
                "usable_exact_identity": all(status == "Match"
                                             for status in terminal_statuses.values()),
                "terminals": terminal_nodes,
                "member_terminal_nodes": member_terminal_nodes,
            }

    model_counts_reference = collections.Counter(d.device_class().name.lower() for d in reference_devices)
    model_counts_gds = collections.Counter(d["model"] for d in raw_devices)
    unmatched_reference_counts = collections.Counter(d["model"] for d in reference_unmatched)
    unmatched_gds_counts = collections.Counter(d.get("model", "unknown") for d in layout_unmatched)

    def wl_histogram(records, is_reference):
        if is_reference:
            counts = collections.Counter(
                (device.device_class().name.lower(),
                 round(float(device.parameter("W")), 9),
                 round(float(device.parameter("L")), 9))
                for device in records)
        else:
            counts = collections.Counter(
                (device["model"], round(float(device["w_um"]), 9),
                 round(float(device["l_um"]), 9))
                for device in records)
        return counts

    reference_wl_counts = wl_histogram(reference_devices, True)
    gds_wl_counts = wl_histogram(normalized_devices, False)
    wl_count_differences = [
        {"model": model, "w_um": width, "l_um": length,
         "gds_count": gds_wl_counts[(model, width, length)],
         "reference_count": reference_wl_counts[(model, width, length)],
         "delta": gds_wl_counts[(model, width, length)] -
                  reference_wl_counts[(model, width, length)]}
        for model, width, length in sorted(set(gds_wl_counts) | set(reference_wl_counts))
        if gds_wl_counts[(model, width, length)] != reference_wl_counts[(model, width, length)]
    ]
    result = {
        "equivalent": equivalent and not port_mismatches,
        "reference_equivalent": equivalent and not port_mismatches,
        "reference_netlist_sha256": _sha256(reference_path),
        "input_gds_sha256": str(geometry.get("input_gds_sha256", "")),
        "top": design,
        "comparison": {
            "engine": "KLayout NetlistComparer",
            "flattened": True,
            "parameter_policy": "model, W/L (1e-9 um tolerance), D/G/S/B topology with native MOS4 source/drain interchange (no terminal remapping), strict declared top-level pin identity; combine only physically parallel MOS4 devices with identical G/B/L and unordered D/S nets (no net joins or unused-pin pruning)",
            "comparer_policy": compare_policy,
            "device_cross_reference_status_counts": dict(sorted(device_status_counts.items())),
            "net_cross_reference_status_counts": dict(sorted(net_status_counts.items())),
            "raw_gds_mos": len(raw_devices),
            "normalized_gds_mos": len(normalized_devices),
            "raw_reference_mos": raw_reference_mos,
            "normalized_reference_mos": len(reference_devices),
            "reference_parallel_device_reduction": raw_reference_mos - len(reference_devices),
            "raw_reference_model_counts": dict(sorted(raw_reference_model_counts.items())),
            "reference_wl_counts": [
                {"model": model, "w_um": width, "l_um": length, "count": count}
                for (model, width, length), count in sorted(reference_wl_counts.items())
            ],
            "gds_wl_counts": [
                {"model": model, "w_um": width, "l_um": length, "count": count}
                for (model, width, length), count in sorted(gds_wl_counts.items())
            ],
            "wl_count_differences": wl_count_differences,
            "reference_mos": len(reference_devices),
            "gds_logical_nets": len(_net_records(geometry)),
            "reference_nets": sum(1 for _ in source_top.each_net()),
            "matched_devices": len(matched_device_pairs),
            "mapped_reference_devices": len(reference_device_map),
            "matched_nets": len(matched_net_pairs),
            "mapped_exact_reference_nets": len(reference_net_map),
            "mapped_ambiguous_reference_nets": len(ambiguous_net_map),
            "matched_ports": len(set(source_pin_map) & set(layout_pin_map)),
            "gds_model_counts": dict(sorted(model_counts_gds.items())),
            "reference_model_counts": dict(sorted(model_counts_reference.items())),
            "physical_parallel_normalizations": parallel_groups,
            "unmatched_reference_model_counts": dict(sorted(unmatched_reference_counts.items())),
            "unmatched_gds_model_counts": dict(sorted(unmatched_gds_counts.items())),
            "unmatched_reference_devices_sample": reference_unmatched[:100],
            "unmatched_gds_devices_sample": layout_unmatched[:100],
            "unmatched_reference_nets": len(unmatched_reference_nets),
            "unmatched_gds_nets": len(unmatched_gds_nets),
            "unmatched_reference_nets_sample": unmatched_reference_nets[:100],
            "unmatched_gds_nets_sample": unmatched_gds_nets[:100],
            "port_mismatches": port_mismatches,
        },
        "nets": reference_net_map,
        "ambiguous_nets": ambiguous_net_map,
        "devices": reference_device_map,
    }
    return result


class _DisjointSet:
    def __init__(self):
        self.parent: dict[str, str] = {}
        self.display: dict[str, str] = {}

    def add(self, value: str) -> str:
        key = str(value).casefold()
        self.parent.setdefault(key, key)
        self.display.setdefault(key, str(value))
        return key

    def find(self, value: str) -> str:
        key = self.add(value)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != key:
            parent = self.parent[key]
            self.parent[key] = root
            key = parent
        return root

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)

    def members(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = collections.defaultdict(list)
        for key in self.parent:
            result[self.find(key)].append(self.display[key])
        return {root: sorted(values, key=str.casefold) for root, values in result.items()}


def _parse_spice_number(value: str, label: str) -> float:
    match = _SPICE_NUMBER.fullmatch(value.strip())
    if not match:
        raise ValueError("invalid SPICE number %s=%r" % (label, value))
    number = float(match.group(1))
    suffix = match.group(2)
    result = number * _SUFFIX.get(suffix.casefold(), 1.0) if suffix else number
    if not math.isfinite(result):
        raise ValueError("non-finite SPICE number %s=%r" % (label, value))
    return result


def _read_emitted_spice(path: str | Path) -> dict[str, Any]:
    statements = []
    current = ""
    try:
        lines = Path(path).expanduser().resolve().read_text().splitlines()
    except OSError as exc:
        raise ValueError("cannot read emitted PEX netlist %s: %s" % (path, exc)) from exc
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("*"):
            continue
        if stripped.startswith("+"):
            if not current:
                raise ValueError("SPICE continuation has no preceding statement")
            current += " " + stripped[1:].strip()
            continue
        if current:
            statements.append(current)
        current = stripped
    if current:
        statements.append(current)
    subckt = None
    ports = []
    mos = {}
    resistors = []
    capacitors = []
    for statement in statements:
        tokens = statement.split()
        if not tokens:
            continue
        directive = tokens[0].casefold()
        if directive == ".subckt":
            if len(tokens) < 2 or subckt is not None:
                raise ValueError("expected exactly one valid .subckt in emitted PEX netlist")
            subckt = tokens[1]
            ports.extend(tokens[2:])
        elif directive == ".ends":
            continue
        elif directive.startswith("."):
            continue
        elif tokens[0][0].casefold() == "x":
            if len(tokens) < 7:
                raise ValueError("invalid MOS wrapper element: %s" % statement)
            name = tokens[0][1:]
            folded = name.casefold()
            if not name or folded in mos:
                raise ValueError("duplicate or empty emitted MOS name %s" % name)
            assignments = {}
            for token in tokens[6:]:
                if "=" in token:
                    key, value = token.split("=", 1)
                    assignments[key.casefold()] = value
            if "w" not in assignments or "l" not in assignments:
                raise ValueError("emitted MOS %s lacks W/L parameters" % name)
            mos[folded] = {
                "name": name,
                "model": tokens[5].casefold(),
                "terminals": dict(zip(("D", "G", "S", "B"), tokens[1:5])),
                "w_um": _parse_spice_number(assignments["w"], name + ".W") * 1.0e6,
                "l_um": _parse_spice_number(assignments["l"], name + ".L") * 1.0e6,
            }
        elif tokens[0][0].casefold() == "r":
            if len(tokens) < 4:
                raise ValueError("invalid emitted resistor element: %s" % statement)
            resistors.append({"name": tokens[0], "node1": tokens[1], "node2": tokens[2],
                              "value_ohm": _parse_spice_number(tokens[3], tokens[0])})
        elif tokens[0][0].casefold() == "c":
            if len(tokens) < 4:
                raise ValueError("invalid emitted capacitor element: %s" % statement)
            capacitors.append({"name": tokens[0], "node1": tokens[1], "node2": tokens[2]})
        else:
            raise ValueError("unsupported element in emitted source-free PEX: %s" % statement)
    if subckt is None:
        raise ValueError("emitted PEX netlist has no .subckt")
    if len({port.casefold() for port in ports}) != len(ports):
        raise ValueError("emitted PEX ports collide after case-fold")
    return {"subckt": subckt, "ports": ports, "mos": mos,
            "resistors": resistors, "capacitors": capacitors}


def check_rc_ideal_wire_invariant(geometry: Mapping[str, Any], emitted_netlist: str | Path) -> dict[str, Any]:
    """Check invariant A by unioning every emitted R edge and ignoring every C."""
    direct_devices = _geometry_devices(geometry)
    direct_nets = _net_records(geometry)
    port_nets = _port_logical_nets(geometry)
    emitted = _read_emitted_spice(emitted_netlist)
    ds = _DisjointSet()
    for logical in direct_nets:
        ds.add(logical)
    for entry in emitted["mos"].values():
        for node in entry["terminals"].values():
            ds.add(node)
    for port in emitted["ports"]:
        ds.add(port)
    for resistor in emitted["resistors"]:
        ds.union(resistor["node1"], resistor["node2"])

    direct_by_name = {device["name"].casefold(): device for device in direct_devices}
    emitted_by_name = emitted["mos"]
    device_errors = []
    for name in sorted(set(direct_by_name) - set(emitted_by_name)):
        device_errors.append({"device": direct_by_name[name]["name"], "error": "missing emitted MOS"})
    for name in sorted(set(emitted_by_name) - set(direct_by_name)):
        device_errors.append({"device": emitted_by_name[name]["name"], "error": "unexpected emitted MOS"})
    net_owners: dict[str, set[str]] = collections.defaultdict(set)
    roots_by_net: dict[str, set[str]] = collections.defaultdict(set)
    for logical in direct_nets:
        ds.add(logical)
    for name in sorted(set(direct_by_name) & set(emitted_by_name)):
        direct = direct_by_name[name]
        actual = emitted_by_name[name]
        if direct["model"].casefold() != actual["model"].casefold():
            device_errors.append({"device": direct["name"], "error": "model mismatch",
                                  "gds_model": direct["model"], "emitted_model": actual["model"]})
        for field in ("w_um", "l_um"):
            if not _near(float(direct[field]), float(actual[field])):
                device_errors.append({"device": direct["name"], "error": field + " mismatch",
                                      "gds": direct[field], "emitted": actual[field]})
        for terminal in ("D", "G", "S", "B"):
            logical = direct["terminals"][terminal]
            node = actual["terminals"][terminal]
            root = ds.find(node)
            net_owners[root].add(logical)
            roots_by_net[logical].add(root)
    port_errors = []
    emitted_ports = {port.casefold(): port for port in emitted["ports"]}
    expected_ports = {port.casefold(): port for port in _port_names(geometry)}
    if set(emitted_ports) != set(expected_ports):
        port_errors.append({"missing": sorted(set(expected_ports) - set(emitted_ports)),
                            "unexpected": sorted(set(emitted_ports) - set(expected_ports))})
    for folded in sorted(set(emitted_ports) & set(expected_ports)):
        port = expected_ports[folded]
        logical = port_nets[port]
        root = ds.find(emitted_ports[folded])
        roots_by_net[logical].add(root)
        net_owners[root].add(logical)
    member_names = ds.members()
    shorted = []
    for root, owners in sorted(net_owners.items()):
        if len(owners) > 1:
            shorted.append({"logical_nets": sorted(owners), "emitted_nodes": member_names.get(root, [])})
    disconnected = [
        {"logical_net": logical, "emitted_components": [member_names.get(root, []) for root in sorted(roots)]}
        for logical, roots in sorted(roots_by_net.items()) if len(roots) > 1
    ]
    node_names_by_logical = {}
    for logical, roots in roots_by_net.items():
        node_names_by_logical[logical] = sorted({node for root in roots
                                                 for node in member_names.get(root, [])},
                                                key=str.casefold)
    equivalent = not device_errors and not port_errors and not shorted and not disconnected
    return {
        "equivalent": equivalent,
        "method": "emitted SPICE graph; all R are ideal-wire unions; all C are ignored",
        "emitted_netlist_sha256": _sha256(emitted_netlist),
        "mos_count": len(emitted["mos"]),
        "resistor_count": len(emitted["resistors"]),
        "capacitor_count_ignored": len(emitted["capacitors"]),
        "gds_logical_net_count": len(direct_nets),
        "shorted_logical_nets": shorted[:100],
        "disconnected_logical_nets": disconnected[:100],
        "device_mismatches": device_errors[:100],
        "port_mismatches": port_errors,
        "nodes_by_logical_net": node_names_by_logical,
        "emitted_mos_nodes": {entry["name"]: dict(entry["terminals"])
                              for entry in emitted["mos"].values()},
    }


__all__ = ["check_rc_ideal_wire_invariant", "compare_source_netlist"]
