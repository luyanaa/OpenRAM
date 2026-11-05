"""Inspect native KLayout LVS correspondence, never infer a match from a log.

The original SPICE supplies declared boundaries. The persisted native netlists
supply post-combination populations; the xref must cover those populations and
every connection bijectively. Ambiguous decoder outputs are resolved only by
an exhaustive constrained full-netlist comparison; no mismatch is waived.
Python KLayout is imported lazily for DRC-only installations.
"""
from collections import Counter
from itertools import permutations
import math
import re


class LVSReportError(ValueError):
    """The native comparison cannot be inspected reliably."""


def _net_key(net):
    return net.expanded_name() if net is not None else None


def _id(obj):
    return obj.id()


def _terminal_key(terminal):
    return terminal.device().id(), terminal.terminal_id()


def _subpin_key(pin):
    return pin.subcircuit().id(), pin.pin_id()


def _all_nets(circuit):
    """Enumerate a circuit's nets without ``Circuit#each_net()``.

    KLayout 0.30.x Python bindings corrupt the heap while iterating
    ``Circuit#each_net()``: the damage surfaces as ``malloc_consolidate():
    unaligned fastbin chunk detected`` when the ``LayoutVsSchematic`` is
    destroyed, for both real reports and the two-MOS regression fixture.
    ``nets_by_name('*')`` returns the same population through the net name
    index and is stable.
    """
    return circuit.nets_by_name("*")


def _population(circuit, iterator):
    if iterator == "each_net":
        # Native comparison does not pair zero-incidence bookkeeping nets
        # (e.g. an unused SUBSTRATE global). Disclose them, never purge them.
        return [net for net in _all_nets(circuit) if not net.is_floating()]
    return list(getattr(circuit, iterator)())


_PREDECODER_DEVICE = re.compile(
    r"(?:^|\.)ROW_DECODER\.PRE_(?P<group>\d+)\.XPRE2X4_AND_"
    r"(?P<index>\d+)\.PAND2_DEC_INV\.PINV_NMOS$", re.IGNORECASE)
_PREDECODER_GATE = re.compile(
    r"\.PRE_(?P<group>\d+)\.XPRE2X4_AND_(?P<index>\d+)\.ZB_INT$",
    re.IGNORECASE)
_ROW_OUTPUT = re.compile(r"\.ROW_DECODER\.OUT_\d+$", re.IGNORECASE)


def _decoder_output_anchors(db, xref, top):
    """Find only predecoder output nets anchored by native gate/body/source matches."""
    roots = [pair for pair in xref.each_circuit_pair()
             if pair.first() is not None and pair.second() is not None
             and pair.first().name.casefold() == top.casefold()
             and pair.second().name.casefold() == top.casefold()]
    if len(roots) != 1:
        return None
    root = roots[0]
    native_nets, native_reverse = {}, {}
    for pair in xref.each_net_pair(root):
        left, right = pair.first(), pair.second()
        if left is None or right is None or str(pair.status()) != "Match":
            continue
        left_name, right_name = _net_key(left), _net_key(right)
        if ((left_name in native_nets and native_nets[left_name] != right_name)
                or (right_name in native_reverse and native_reverse[right_name] != left_name)):
            return None
        native_nets[left_name] = right_name
        native_reverse[right_name] = left_name
    anchors = []
    for pair in xref.each_device_pair(root):
        left, right = pair.first(), pair.second()
        match = _PREDECODER_DEVICE.search(right.name) if right is not None else None
        if match is None or str(pair.status()) != "Mismatch":
            continue
        if left is None:
            return None
        left_class, right_class = left.device_class(), right.device_class()
        if (not isinstance(left_class, db.DeviceClassMOS4Transistor)
                or not isinstance(right_class, db.DeviceClassMOS4Transistor)
                or left_class.name.casefold() != right_class.name.casefold()):
            continue
        if any(abs(left.parameter(name) - right.parameter(name)) > 1e-9
               for name in ("W", "L")):
            continue

        group, index = match.group("group"), match.group("index")
        terminals = {
            name: (left.net_for_terminal(name), right.net_for_terminal(name))
            for name in ("G", "B", "S", "D")
        }
        if any(a is None or b is None for a, b in terminals.values()):
            continue
        gate_name = _net_key(terminals["G"][1])
        output_name = _net_key(terminals["D"][1])
        gate_match = _PREDECODER_GATE.search(gate_name)
        if (gate_match is None or gate_match.group("group") != group
                or gate_match.group("index") != index
                or _ROW_OUTPUT.search(output_name) is None):
            continue
        if _net_key(terminals["S"][1]).casefold() != _net_key(terminals["B"][1]).casefold():
            continue
        if any(native_nets.get(_net_key(terminals[name][0])) != _net_key(terminals[name][1])
               for name in ("G", "B", "S")):
            continue
        anchors.append({
            "device": right.name,
            "gate": gate_name,
            "layout_output": _net_key(terminals["D"][0]),
            "reference_output": output_name,
        })

    if (not anchors
            or len({anchor["device"] for anchor in anchors}) != len(anchors)
            or len({anchor["gate"] for anchor in anchors}) != len(anchors)
            or len({anchor["layout_output"] for anchor in anchors}) != len(anchors)
            or len({anchor["reference_output"] for anchor in anchors}) != len(anchors)):
        return None
    return root, anchors


def _compare_with_decoder_anchors(db, xref, top):
    found = _decoder_output_anchors(db, xref, top)
    if found is None:
        return None
    root, anchors = found
    # A decoder-output ambiguity is a permutation cycle; two anchored outputs
    # already give two candidate mappings and therefore a unique full-netlist
    # result.  Earlier designs exposed a three-cycle, current extraction may
    # resolve one of the outputs natively.
    if len(anchors) < 2:
        return None
    anchors.sort(key=lambda anchor: anchor["device"])
    layout, reference = root.first(), root.second()
    layout_pins = {pin.name().casefold(): pin for pin in layout.each_pin()}
    reference_pins = {pin.name().casefold(): pin for pin in reference.each_pin()}
    if (len(layout_pins) != len(list(layout.each_pin()))
            or len(reference_pins) != len(list(reference.each_pin()))
            or set(layout_pins) != set(reference_pins)):
        return None

    pin_constraints = []
    for name, left_pin in layout_pins.items():
        left_net = layout.net_for_pin(left_pin.id())
        right_net = reference.net_for_pin(reference_pins[name].id())
        if left_net is None or right_net is None:
            return None
        pin_constraints.append((left_net, right_net))
    layout_outputs = [layout.net_by_name(anchor["layout_output"]) for anchor in anchors]
    reference_outputs = [anchor["reference_output"] for anchor in anchors]
    if any(net is None for net in layout_outputs):
        return None

    attempts, matches = [], []
    for candidate in permutations(reference_outputs):
        comparer = db.NetlistComparer()
        comparer.max_depth = 8
        comparer.max_branch_complexity = 1024
        comparer.max_resistance = 1e9
        comparer.min_capacitance = 1e-18
        comparer.dont_consider_net_names = True
        comparer.with_log = True
        for left_net, right_net in pin_constraints:
            comparer.same_nets(left_net, right_net, True)
        right_nets = [reference.net_by_name(name) for name in candidate]
        if any(net is None for net in right_nets):
            return None
        for left_net, right_net in zip(layout_outputs, right_nets):
            comparer.same_nets(left_net, right_net, True)
        candidate_xref = db.NetlistCrossReference()
        matched = comparer.compare(xref.netlist_a(), xref.netlist_b(), candidate_xref)
        attempt = {
            "mapping": [
                {"layout_output": left.expanded_name(),
                 "reference_output": right}
                for left, right in zip(layout_outputs, candidate)
            ],
            "matched": bool(matched),
        }
        attempts.append(attempt)
        if matched:
            # Keep the comparer alive with its cross-reference: the native
            # netlist objects referenced by candidate_xref are owned by the
            # comparer.  Dropping the comparer before _inspect() leaves
            # dangling references on some KLayout/Python builds.
            matches.append((candidate, candidate_xref, comparer))

    expected = tuple(reference_outputs)
    if len(matches) != 1 or matches[0][0] != expected:
        return None
    return matches[0][1], anchors, attempts, matches[0][2]


def inspect_report(report, top, mode, reference):
    """Inspect native coverage, then resolve only exact anchored decoder matches."""
    if mode not in ("flat", "hierarchical"):
        raise LVSReportError("unsupported native LVS comparison mode")
    try:
        import klayout.db as db
        database = db.LayoutVsSchematic()
        database.read(str(report))
        xref = database.xref()
        if xref is None or xref.netlist_a() is None or xref.netlist_b() is None:
            raise LVSReportError("native LVS report has no comparison cross-reference")
        original = db.Netlist()
        original.read(str(reference), db.NetlistSpiceReader())

        if mode == "flat":
            roots = [pair for pair in xref.each_circuit_pair()
                     if pair.first() is not None and pair.second() is not None
                     and pair.first().name.casefold() == top.casefold()
                     and pair.second().name.casefold() == top.casefold()]
            if len(roots) == 1:
                root_status = str(roots[0].status())
                device_statuses = Counter(
                    str(pair.status()) for pair in xref.each_device_pair(roots[0]))
                constrained = _compare_with_decoder_anchors(db, xref, top)
                if constrained is not None:
                    constrained_xref, anchors, attempts, comparer = constrained
                    result = _inspect(db, constrained_xref, original, top, mode)
                    # comparer owns the constrained cross-reference netlists;
                    # keep it alive until _inspect() returns.
                    _ = comparer
                    if result["complete"]:
                        result["resolution"] = {
                            "method": "unique-full-netlist-match-across-predecoder-output-bijections",
                            "native_root_status": root_status,
                            "native_device_status_counts": dict(sorted(device_statuses.items())),
                            "anchors": anchors,
                            "candidate_mappings": attempts,
                            "passing_mappings": sum(item["matched"] for item in attempts),
                        }
                        return result

        # A compare attempt can update native cross-reference state. Reload
        # the persisted result before reporting its original correspondence.
        database = db.LayoutVsSchematic()
        database.read(str(report))
        native_xref = database.xref()
        if native_xref is None:
            raise LVSReportError("native LVS report has no comparison cross-reference")
        return _inspect(db, native_xref, original, top, mode)
    except (ImportError, OSError, RuntimeError, ValueError, AttributeError,
            IndexError, KeyError, TypeError) as exc:
        raise LVSReportError("cannot inspect native LVS report: " + str(exc)) from exc




def _inspect(db, xref, original, top, mode):
    netlists = (xref.netlist_a(), xref.netlist_b())
    circuits = [list(netlist.each_circuit()) for netlist in netlists]
    errors = set()
    counts, statuses = {}, {}

    def require(condition, message):
        if not condition:
            errors.add(message)

    categories = {
        "devices": ("each_device", "each_device_pair", _id),
        "nets": ("each_net", "each_net_pair", _net_key),
        "pins": ("each_pin", "each_pin_pair", _id),
        "subcircuits": ("each_subcircuit", "each_subcircuit_pair", _id),
    }
    counts["circuits"] = dict(layout=len(circuits[0]), reference=len(circuits[1]), paired=0)
    for category, (iterator, _, _) in categories.items():
        totals = [sum(len(_population(circuit, iterator)) for circuit in side)
                  for side in circuits]
        counts[category] = dict(layout=totals[0], reference=totals[1], paired=0)
    for category, iterator in (("device_terminals", "each_terminal"),
                               ("pin_connections", "each_pin"),
                               ("subcircuit_pin_connections", "each_subcircuit_pin")):
        totals = [sum(len(list(getattr(net, iterator)()))
                      for circuit in side for net in _all_nets(circuit)) for side in circuits]
        counts[category] = dict(layout=totals[0], reference=totals[1], paired=0)

    def bijection(category, left, right, pairs, key, native_status=True, lookups=None):
        # KLayout0.30.7 each_device/each_subcircuit yields value copies with
        # cleared IDs. Pair objects carry live IDs: validate those IDs by the
        # owning circuit lookup, uniqueness and exhaustive population size.
        left_keys, right_keys = {key(obj) for obj in left}, {key(obj) for obj in right}
        if lookups is None:
            require(len(left_keys) == len(left) and len(right_keys) == len(right),
                    category + ": duplicate object identities")
        seen_left, seen_right, mapping = set(), set(), {}
        good = True
        for pair in pairs:
            a, b = pair.first(), pair.second()
            if native_status:
                status = str(pair.status())
                statuses.setdefault(category, Counter())[status] += 1
                allowed = ("Match", "MatchWithWarning") if category == "nets" else ("Match",)
                if status not in allowed:
                    require(False, category + ": non-matching native status " + status)
                    good = False
            if a is None or b is None:
                require(False, category + ": one-sided correspondence")
                good = False
                continue
            counts[category]["paired"] += 1
            ka, kb = key(a), key(b)
            if lookups is None:
                member = ka in left_keys and kb in right_keys
            else:
                member = (ka > 0 and kb > 0 and lookups[0](ka) is not None
                          and lookups[1](kb) is not None)
            valid = member and ka not in seen_left and kb not in seen_right
            require(valid, category + ": foreign or repeated correspondence")
            good = good and valid
            seen_left.add(ka)
            seen_right.add(kb)
            mapping[ka] = b
        covered = (seen_left == left_keys and seen_right == right_keys) if lookups is None else (
            len(seen_left) == len(left) and len(seen_right) == len(right))
        require(covered, category + ": incomplete two-sided coverage")
        return mapping, good and covered

    circuit_pairs = list(xref.each_circuit_pair())
    circuit_map, _ = bijection("circuits", *circuits, circuit_pairs, lambda c: c.name)
    original_circuits = {}
    for circuit in original.each_circuit():
        name = circuit.name.casefold()
        require(name not in original_circuits, "source: duplicate case-equivalent circuit names")
        original_circuits[name] = circuit
    roots = [[circuit for circuit in side if circuit.name.casefold() == top.casefold()]
             for side in circuits]
    require(all(len(side) == 1 for side in roots), "root: missing or ambiguous top circuit")
    source_root = original_circuits.get(top.casefold())
    require(source_root is not None, "source: declared top circuit missing")
    if mode == "flat":
        require(all(len(side) == 1 for side in circuits), "flat: residual native circuits")
        require(counts["subcircuits"]["layout"] == counts["subcircuits"]["reference"] == 0,
                "flat: residual subcircuits")

    def ports(circuit):
        names = [pin.name().casefold() for pin in circuit.each_pin()]
        require(all(names) and len(set(names)) == len(names),
                "boundary: unnamed or duplicate case-equivalent pins")
        return set(names)

    source_ports = ports(source_root) if source_root is not None else set()
    root_status, maps = None, {}
    for pair in circuit_pairs:
        a, b = pair.first(), pair.second()
        if a is None or b is None:
            continue
        is_root = a.name.casefold() == b.name.casefold() == top.casefold()
        if is_root:
            require(root_status is None, "root: repeated correspondence")
            root_status = str(pair.status())
        require(a.name.casefold() == b.name.casefold(), "circuits: boundary names differ")
        require(ports(a) == ports(b), "boundary: layout/reference pins differ")
        source_circuit = source_root if is_root else original_circuits.get(a.name.casefold())
        if is_root or mode == "hierarchical":
            require(source_circuit is not None, "boundary: circuit absent from original source")
            if source_circuit is not None:
                require(ports(a) == ports(source_circuit), "boundary: original declared pins differ")
        record = {"a": a, "b": b, "pairs": {}, "good": True}
        for category, (iterator, pair_iterator, key) in categories.items():
            pairs = list(getattr(xref, pair_iterator)(pair))
            lookups = None
            if category in ("devices", "subcircuits"):
                lookup = "device_by_id" if category == "devices" else "subcircuit_by_id"
                lookups = (getattr(a, lookup), getattr(b, lookup))
            mapping, good = bijection(category, _population(a, iterator),
                                      _population(b, iterator), pairs, key, lookups=lookups)
            record[category] = mapping
            record["pairs"][category] = pairs
            record["good"] = record["good"] and good
        maps[a.name] = record
    require(root_status == "Match", "root: native comparison was not Match")

    def terminal_name(terminal):
        return terminal.device_class().terminal_definition(terminal.terminal_id()).name.upper()

    for record in maps.values():
        if not record["good"]:
            continue
        a, b = record["a"], record["b"]
        net_map = record["nets"]

        def connected(left, right):
            if left is None or right is None:
                return left is None and right is None
            selected = net_map.get(_net_key(left))
            return selected is not None and _net_key(selected) == _net_key(right)

        for pair in record["pairs"]["pins"]:
            left, right = pair.first(), pair.second()
            require(left.name().casefold() == right.name().casefold(),
                    "pins: named boundary correspondence differs")
            require(connected(a.net_for_pin(left.id()), b.net_for_pin(right.id())),
                    "pins: selected net correspondence differs")
        for pair in record["pairs"]["devices"]:
            left, right = pair.first(), pair.second()
            lc, rc = left.device_class(), right.device_class()
            require(lc.name.casefold() == rc.name.casefold(), "devices: models differ")
            mos4 = isinstance(lc, db.DeviceClassMOS4Transistor) and isinstance(rc, db.DeviceClassMOS4Transistor)
            require(mos4, "devices: strict incidence inspection requires MOS4 models")
            if not mos4:
                continue
            for parameter in ("W", "L"):
                lv, rv = left.parameter(parameter), right.parameter(parameter)
                require(math.isfinite(lv) and math.isfinite(rv) and abs(lv - rv) <= 1e-9,
                        "devices: exact MOS " + parameter + " differs")
            require(all(connected(left.net_for_terminal(t), right.net_for_terminal(t)) for t in ("G", "B")),
                    "devices: gate/body incidence differs")
            require(any(all(connected(left.net_for_terminal(lt), right.net_for_terminal(rt))
                            for lt, rt in zip(("S", "D"), order))
                        for order in (("S", "D"), ("D", "S"))),
                    "devices: source/drain incidence differs")
        for pair in record["pairs"]["subcircuits"]:
            left, right = pair.first(), pair.second()
            child_a, child_b = left.circuit_ref(), right.circuit_ref()
            selected_child = circuit_map.get(child_a.name)
            require(selected_child is not None and selected_child.name == child_b.name,
                    "subcircuits: child circuit correspondence differs")
            child = maps.get(child_a.name)
            require(child is not None and child["good"], "subcircuits: child boundary was not compared")
            if child is not None:
                for pin in child_a.each_pin():
                    selected_pin = child["pins"].get(pin.id())
                    require(selected_pin is not None and connected(left.net_for_pin(pin.id()),
                            right.net_for_pin(selected_pin.id())), "subcircuits: boundary incidence differs")
        for pair in record["pairs"]["nets"]:
            left, right = pair.first(), pair.second()
            terminal_pairs = list(xref.each_net_terminal_pair(pair))
            _, good = bijection("device_terminals", list(left.each_terminal()), list(right.each_terminal()),
                                terminal_pairs, _terminal_key, native_status=False)
            if good:
                for terminal_pair in terminal_pairs:
                    lt, rt = terminal_pair.first(), terminal_pair.second()
                    selected_device = record["devices"].get(lt.device().id())
                    require(selected_device is not None and selected_device.id() == rt.device().id(),
                            "device_terminals: device correspondence differs")
                    ln, rn = terminal_name(lt), terminal_name(rt)
                    require(ln == rn or {ln, rn} == {"S", "D"},
                            "device_terminals: fixed terminal correspondence differs")
            pin_pairs = list(xref.each_net_pin_pair(pair))
            _, good = bijection("pin_connections", list(left.each_pin()), list(right.each_pin()),
                                pin_pairs, lambda p: p.pin_id(), native_status=False)
            if good:
                for pin_pair in pin_pairs:
                    selected_pin = record["pins"].get(pin_pair.first().pin_id())
                    require(selected_pin is not None and selected_pin.id() == pin_pair.second().pin_id(),
                            "pin_connections: pin correspondence differs")
            subpin_pairs = list(xref.each_net_subcircuit_pin_pair(pair))
            _, good = bijection("subcircuit_pin_connections", list(left.each_subcircuit_pin()),
                                list(right.each_subcircuit_pin()), subpin_pairs, _subpin_key, native_status=False)
            if good:
                for pin_pair in subpin_pairs:
                    lp, rp = pin_pair.first(), pin_pair.second()
                    selected_sub = record["subcircuits"].get(lp.subcircuit().id())
                    child = maps.get(lp.subcircuit().circuit_ref().name)
                    selected_pin = child["pins"].get(lp.pin_id()) if child is not None else None
                    require(selected_sub is not None and selected_sub.id() == rp.subcircuit().id()
                            and selected_pin is not None and selected_pin.id() == rp.pin_id(),
                            "subcircuit_pin_connections: correspondence differs")
    for category, sides in counts.items():
        require(sides["layout"] == sides["reference"] == sides["paired"],
                category + ": incomplete aggregate coverage")
    unconnected_nets = {
        side: sorted(circuit.name + ":" + net.expanded_name()
                     for circuit in population for net in _all_nets(circuit) if net.is_floating())
        for side, population in zip(("layout", "reference"), circuits)}
    return {"schema": "ics55-native-lvs-comparison/1", "top": top, "mode": mode,
            "complete": not errors, "root_status": root_status, "counts": counts,
            "status_counts": {key: dict(sorted(value.items())) for key, value in sorted(statuses.items())},
            "unconnected_nets": unconnected_nets,
            "reference_ports": sorted(source_ports), "errors": sorted(errors)}
