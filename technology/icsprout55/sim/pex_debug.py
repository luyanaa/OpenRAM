"""Select a concrete read and its preceding write for physical-node probes."""
import re

from mkscs import read_trace


def readwrite_target(trace, failures=None, read_port=None):
    cycles, expected = read_trace(trace)
    candidates = []
    if failures is not None:
        for failure in failures:
            match = re.fullmatch(r"dout(\d+)\[(\d+)\]", failure["pin"])
            if match is None:
                raise ValueError("failure has an unsupported output pin")
            port, bit = map(int, match.groups())
            cycle = int(failure["cycle"])
            bus = "dout{}".format(port)
            bits = expected.get(cycle, {}).get(bus, "")
            if bit >= len(bits) or int(bits[-1 - bit]) != failure["expected"]:
                raise ValueError("failure does not match the selected trace")
            candidates.append((cycle, port, bit, int(bits[-1 - bit])))
    else:
        for cycle, buses in sorted(expected.items()):
            for bus, bits in sorted(buses.items()):
                match = re.fullmatch(r"dout(\d+)", bus)
                if match is None or not re.fullmatch(r"[01]+", bits):
                    raise ValueError("debug profile requires binary expected reads")
                for bit, value in enumerate(reversed(bits)):
                    candidates.append((cycle, int(match.group(1)), bit, int(value)))
    if read_port is not None:
        candidates = [candidate for candidate in candidates if candidate[1] == read_port]
    if failures is None:
        zeroes = [candidate for candidate in candidates if candidate[3] == 0]
        candidates = zeroes or candidates
    if not candidates:
        raise ValueError("debug profile has no target read")
    cycle, port, bit, value = min(candidates)
    inputs = cycles[cycle]
    address = inputs["addr{}".format(port)]
    writes = []
    for write_cycle, values in sorted(cycles.items()):
        if write_cycle >= cycle:
            break
        for name in values:
            match = re.fullmatch(r"csb(\d+)", name)
            if match is None:
                continue
            write_port = int(match.group(1))
            operation = values.get("op{}".format(write_port))
            write_selected = values[name] == "0" and (
                operation == "W" or operation is None and
                values.get("web{}".format(write_port)) == "0")
            if write_selected and values.get("addr{}".format(write_port)) == address:
                data = values.get("din{}".format(write_port), "")
                if not re.fullmatch(r"[01]+", data) or bit >= len(data):
                    raise ValueError("preceding write has no valid target data bit")
                writes.append((write_cycle, write_port, int(data[-1 - bit])))
    if not writes:
        raise ValueError("target read has no preceding write to its address")
    latest = max(item[0] for item in writes)
    latest_writes = [item for item in writes if item[0] == latest]
    if len(latest_writes) != 1:
        raise ValueError("target read follows ambiguous simultaneous writes")
    write_cycle, write_port, written = latest_writes[0]
    if value != written:
        raise ValueError("target expected read disagrees with the preceding write")
    return dict(read_cycle=cycle, read_port=port, bit=bit, address=int(address, 2),
                address_bits=address, expected=value, write_cycle=write_cycle,
                write_port=write_port, written=written,
                selection="first_reported_failure" if failures is not None else "first_expected_zero_or_read")


def select_probes(node_map, target, words_per_row=1):
    """Resolve semantic signals only through established physical mappings."""
    if words_per_row <= 0 or words_per_row & (words_per_row - 1):
        raise ValueError("words_per_row must be a positive power of two")
    if node_map.get("schema_version") != 1 or node_map.get("equivalent") is not True:
        raise ValueError("debug profile requires an equivalent schema-v1 topology map")
    nets = {name.upper().replace("/", "."): value
            for name, value in node_map["nets"].items()}
    devices = {name.upper().replace("/", "."): value
               for name, value in node_map.get("devices", {}).items()}
    read_port, write_port, bit = target["read_port"], target["write_port"], target["bit"]
    row = target["address"] // words_per_row
    column = bit * words_per_row + target["address"] % words_per_row
    signals = []

    def add_net(name, stage):
        record = nets.get(name.upper())
        if (record is None or record.get("usable_exact_identity") is not True or
                not record.get("nodes")):
            raise ValueError("debug profile has no exact usable mapping for " + name)
        nodes = sorted(set(record["nodes"]))
        # Device-terminal probes below retain local BL/WL/PG observations.
        # Limit additional long-net samples, without claiming these are endpoints.
        sampled = nodes if len(nodes) <= 8 else [nodes[0], nodes[-1]]
        for index, node in enumerate(sampled):
            signals.append(dict(name="{}:{}".format(name, index), stage=stage,
                                node=node, reference_net=name,
                                logical_net=record["logical_net"],
                                mapped_node_count=len(nodes)))
        return name

    for port in sorted({read_port, write_port}):
        for index in range(len(target["address_bits"])):
            add_net("A{}_{}".format(port, index), "registered_control")
        for suffix in ("CS", "WE", "WE_BAR"):
            add_net("CONTROL{}.{}".format(port, suffix), "registered_control")
        for name in ("CLK_BUF{}".format(port), "RBL_BL{}".format(port)):
            add_net(name, "control")
        for suffix in ("CLK_BAR", "GATED_CLK_BAR", "GATED_CLK_BUF",
                       "RBL_BL_DELAY", "RBL_BL_DELAY_BAR"):
            add_net("CONTROL{}.{}".format(port, suffix), "control")
        add_net("BANK0.WL_{}_{}".format(port, row), "decode")
    add_net("W_EN{}".format(write_port), "write")
    add_net("BANK_DIN{}_{}".format(write_port, bit), "write")
    for leg in ("BL", "BR"):
        add_net("BANK0.{}_{}_{}".format(leg, read_port, column), "read")
        if write_port != read_port:
            add_net("BANK0.{}_{}_{}".format(leg, write_port, column), "write")
    add_net("S_EN{}".format(read_port), "sense")
    add_net("P_EN_BAR{}".format(read_port), "read")
    storage_suffix = ".BITCELL_ARRAY.BIT_R{}_C{}.Q".format(row, column)
    storage = [name for name in nets if name.endswith(storage_suffix)]
    if len(storage) != 1:
        raise ValueError("debug profile cannot identify one target storage cell")
    storage_prefix = storage[0].rsplit(".", 1)[0]
    for suffix in ("Q", "Q_BAR"):
        add_net(storage_prefix + "." + suffix, "storage")
    sense_prefix = "BANK0.PORT_DATA{}.SENSE_AMP_ARRAY{}.SA_D{}".format(read_port, read_port, bit)
    for suffix in ("DINT", "DINTB", "DOUTB"):
        add_net(sense_prefix + "." + suffix, "sense")
    write_prefix = "BANK0.PORT_DATA{}.WRITE_DRIVER_ARRAY{}.WRITE_DRIVER{}".format(
        write_port, write_port, bit)
    for suffix in ("DINB", "ENB"):
        add_net(write_prefix + "." + suffix, "write")
    supply_nodes = set()
    for name in ("VDD", "GND"):
        if (name not in nets or nets[name].get("usable_exact_identity") is not True or
                not nets[name].get("nodes")):
            raise ValueError("debug profile lacks an exact physical supply mapping for " + name)
        supply_nodes.update(nets[name]["nodes"])
    for prefix, stage in ((storage_prefix, "storage"), (sense_prefix, "sense"),
                          (write_prefix, "write"),
                          ("CONTROL{}.W_EN_AND".format(write_port), "control")):
        matched = [(name, record) for name, record in devices.items()
                   if name.startswith(prefix + ".")]
        if not matched:
            raise ValueError("debug profile lacks local device mappings for " + prefix)
        for name, record in sorted(matched):
            if record.get("usable_exact_identity") is not True:
                raise ValueError("debug profile has no exact usable device mapping for " + name)
            for terminal, physical_nodes in record["terminals"].items():
                nodes = [physical_nodes] if isinstance(physical_nodes, str) else physical_nodes
                if not isinstance(nodes, list) or not nodes:
                    raise ValueError("debug profile has no physical nodes for " + name + ":" + terminal)
                for index, node in enumerate(nodes):
                    if not isinstance(node, str):
                        raise ValueError("debug profile contains an invalid physical SPICE node")
                    label = name + ":" + terminal
                    if len(nodes) > 1:
                        label += "[{}]".format(index)
                    signals.append(dict(name=label, node=node,
                                        stage="supply" if node in supply_nodes else stage,
                                        reference_device=name, terminal=terminal,
                                        mapped_node_count=len(nodes)))
    for signal in signals:
        if not isinstance(signal["node"], str) or not re.fullmatch(r"[A-Za-z0-9_.$\[\]:/+-]+", signal["node"]):
            raise ValueError("debug profile contains an invalid physical SPICE node")
    return dict(profile="debug-readwrite", target=dict(target, row=row, column=column),
                signals=signals)
