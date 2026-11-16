"""Measured physical-size observables for admitted ICsprout55 macros.

MOS gate width is an intermediate device-size metric, not silicon area. Layout
areas are computed only from the generated GDS; absent or incomplete evidence
remains unavailable with an explicit reason.
"""
import ast
import math
from pathlib import Path
import re


_NUMERIC = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|mil|[tgkmunpf])?$", re.I)
_SUFFIXES = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
             "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
             "mil": 25.4e-6}
_SIZED_LITERAL = re.compile(
    r"(?<![A-Za-z0-9_.])((?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|mil|[tgkmunpf])\b", re.I)
_MOS_MODEL = re.compile(r"^(?:nm|pm)\d|^(?:nmos|pmos|nfet|pfet)(?:_|$)", re.I)
_NAMED_CELL = re.compile(r"(?:^|[^a-z0-9])(?:write_driver|p?driver|buffer|buf)\d*(?:$|[^a-z0-9])", re.I)
SIZE_REASON_KEYS = {
    "total_mos_gate_width_um": "total_mos_gate_width_reason",
    "macro_bbox_area_um2": "macro_bbox_area_reason",
    "effective_bit_area_um2": "effective_bit_area_reason",
    "buffer_driver_cell_area_um2": "buffer_driver_cell_area_reason",
}


class MetricUnavailable(ValueError):
    """The requested physical observable cannot be established from its input."""


def _logical_lines(path):
    pending = ""
    with Path(path).open(encoding="utf-8", errors="strict") as stream:
        for raw in stream:
            text = raw.strip()
            if not text or text.startswith("*"):
                continue
            if text.startswith("+"):
                if not pending:
                    raise MetricUnavailable("orphan SPICE continuation line")
                pending += " " + text[1:].strip()
            else:
                if pending:
                    yield pending
                pending = text
    if pending:
        yield pending


def _expression_number(value, parameters=None):
    """Evaluate a bounded SPICE numeric/parameter expression without eval()."""
    parameters = {str(key).casefold(): val for key, val in (parameters or {}).items()}
    expression = str(value).strip().strip("{}'\"")
    if not expression:
        raise MetricUnavailable("empty SPICE device parameter")
    match = _NUMERIC.fullmatch(expression)
    if match:
        result = float(match.group(1)) * _SUFFIXES.get((match.group(2) or "").casefold(), 1.0)
        if math.isfinite(result):
            return result
        raise MetricUnavailable("non-finite SPICE device parameter")
    expression = _SIZED_LITERAL.sub(
        lambda match: repr(float(match.group(1)) * _SUFFIXES[match.group(2).casefold()]),
        expression)

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value)
        if isinstance(node, ast.Name):
            key = node.id.casefold()
            if key not in parameters:
                raise MetricUnavailable("unresolved SPICE parameter: " + node.id)
            return _expression_number(parameters[key], parameters)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise MetricUnavailable("division by zero in SPICE device parameter")
            return left / right
        raise MetricUnavailable("unsupported SPICE device-parameter expression")

    try:
        tree = ast.parse(expression, mode="eval")
        result = visit(tree)
    except (SyntaxError, ValueError, TypeError, OverflowError) as exc:
        if isinstance(exc, MetricUnavailable):
            raise
        raise MetricUnavailable("invalid SPICE device-parameter expression") from exc
    if not math.isfinite(result):
        raise MetricUnavailable("non-finite SPICE device parameter")
    return result


def _parameters(tokens, inherited):
    result = dict(inherited)
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key.casefold() == "params:":
            continue
        result[key.casefold()] = value
    return result


def _parse_subcircuits(path):
    subcircuits, current = {}, None
    for line in _logical_lines(path):
        fields = line.split()
        if not fields:
            continue
        directive = fields[0].casefold()
        if directive == ".subckt":
            if current is not None or len(fields) < 2:
                raise MetricUnavailable("malformed or nested SPICE subcircuit declaration")
            name = fields[1].casefold()
            if name in subcircuits:
                raise MetricUnavailable("duplicate SPICE subcircuit: " + fields[1])
            subcircuits[name] = {"name": fields[1], "pins": [], "defaults": {}, "lines": []}
            current = subcircuits[name]
            for token in fields[2:]:
                if "=" in token:
                    key, value = token.split("=", 1)
                    current["defaults"][key.casefold()] = value
                elif token.casefold() != "params:":
                    current["pins"].append(token)
        elif directive == ".ends":
            if current is None or (len(fields) > 1
                                   and fields[1].casefold() != current["name"].casefold()):
                raise MetricUnavailable("unmatched SPICE subcircuit terminator")
            current = None
        elif current is not None:
            current["lines"].append(line)
    if current is not None:
        raise MetricUnavailable("unterminated SPICE subcircuit: " + current["name"])
    return subcircuits


def _instance_parameters(tokens):
    return _parameters(tokens, {})


def _mos_multiplicity(params, inherited, instance):
    """Resolve parallel multiplicity from SPICE ``m`` or the foundry ``mr`` form.

    This technology's wrapper models (``compiler/modules/ptx.py`` when
    ``model_is_subckt``) emit ``mr=<mults>`` rather than ``m``, so each spelling
    contributes one set of parallel devices. A local declaration overrides an
    inherited one; conflicting values within one scope fail closed instead of
    picking one silently.
    """
    for scope in (params, inherited):
        declared = [(name, scope[name]) for name in ("m", "mr") if name in scope]
        if not declared:
            continue
        values = set()
        for _, raw in declared:
            number = _expression_number(raw, inherited)
            if number <= 0:
                raise MetricUnavailable("MOS multiplicity must be positive: " + instance)
            values.add(number)
        if len(values) > 1:
            raise MetricUnavailable(
                "MOS instance declares conflicting m and mr multiplicity: " + instance)
        return values.pop()
    return 1.0


def _mos_width(parameters, inherited, instance):
    params = _instance_parameters(parameters)
    def value(name, default=None):
        raw = params.get(name, inherited.get(name, default))
        if raw is None:
            raise MetricUnavailable("MOS instance lacks an explicit " + name + ": " + instance)
        return _expression_number(raw, inherited)
    width_m = value("w")
    fingers = value("nf", 1)
    multiplier = _mos_multiplicity(params, inherited, instance)
    if width_m <= 0 or fingers <= 0:
        raise MetricUnavailable("MOS gate width and fingers must be positive")
    return width_m * fingers * multiplier


def total_hierarchical_mos_gate_width_um(netlist, top_name):
    """Sum physical gate widths across instantiated hierarchy, in micrometres.

    Each transistor contributes ``w * nf * m``, where the foundry ``mr`` spelling
    of parallel multiplicity counts the same as ``m``. Defined subcircuits are
    expanded from the exact named top cell; unexpanded X instances fail closed
    rather than silently disappearing from the total.
    """
    subcircuits = _parse_subcircuits(netlist)
    top = subcircuits.get(str(top_name).casefold())
    if top is None:
        raise MetricUnavailable("generated netlist lacks top subcircuit " + str(top_name))
    def expand(subckt, inherited, stack):
        key = subckt["name"].casefold()
        if key in stack:
            raise MetricUnavailable("recursive SPICE hierarchy at " + subckt["name"])
        local = dict(subckt["defaults"])
        local.update(inherited)
        local = {str(name).casefold(): value for name, value in local.items()}
        subtotal = 0.0
        for line in subckt["lines"]:
            tokens = line.split()
            if not tokens:
                continue
            kind = tokens[0][0].casefold()
            if kind == "m":
                if len(tokens) < 6:
                    raise MetricUnavailable("malformed MOS statement: " + tokens[0])
                subtotal += _mos_width(tokens[6:], local, tokens[0])
            elif kind == "x":
                positional = []
                parameter_start = len(tokens)
                for index, token in enumerate(tokens[1:], 1):
                    if "=" in token:
                        parameter_start = index
                        break
                    positional.append(token)
                if not positional:
                    raise MetricUnavailable("malformed SPICE subcircuit instance: " + tokens[0])
                model_name = positional[-1]
                model_key = model_name.casefold()
                actual_parameters = _instance_parameters(tokens[parameter_start:])
                if _MOS_MODEL.match(model_name):
                    subtotal += _mos_width(tokens[parameter_start:], local, tokens[0])
                elif model_key in subcircuits:
                    child = subcircuits[model_key]
                    child_parameters = dict(local)
                    child_parameters.update(child["defaults"])
                    child_parameters.update(actual_parameters)
                    subtotal += expand(child, child_parameters, stack + (key,))
                else:
                    raise MetricUnavailable("unresolved hierarchical instance model: " + model_name)
        return subtotal

    width_m = expand(top, {}, ())
    if width_m <= 0:
        raise MetricUnavailable("generated netlist contains no measurable MOS gate width")
    return width_m * 1e6


def _member(value, name):
    try:
        result = getattr(value, name)
    except AttributeError as exc:
        raise MetricUnavailable("KLayout hierarchy object lacks " + name) from exc
    return result() if callable(result) else result


def _instance_count(instance):
    array = _member(instance, "is_regular_array")
    if not array:
        return 1
    na, nb = int(_member(instance, "na")), int(_member(instance, "nb"))
    if na < 1 or nb < 1:
        raise MetricUnavailable("GDS contains an invalid cell array")
    return na * nb


def _named_buffer_driver_area(layout, top):
    counts = {}
    top_index = _member(top, "cell_index")

    def descendants_have_named_cell(cell, seen):
        index = _member(cell, "cell_index")
        if index in seen:
            return False
        seen = seen | {index}
        for instance in cell.each_inst():
            child = instance.cell
            if _NAMED_CELL.search(child.name) and _member(child, "cell_index") != top_index:
                return True
            if descendants_have_named_cell(child, seen):
                return True
        return False

    def visit(cell, multiplicity, seen):
        index = _member(cell, "cell_index")
        if index in seen:
            raise MetricUnavailable("recursive GDS hierarchy")
        next_seen = seen | {index}
        if index != top_index and _NAMED_CELL.search(cell.name) and not descendants_have_named_cell(cell, set()):
            bbox = cell.bbox()
            area = bbox.area() * layout.dbu * layout.dbu
            if area > 0:
                record = counts.setdefault(cell.name, {"instances": 0, "cell_bbox_area_um2": area})
                record["instances"] += multiplicity
                return
        for instance in cell.each_inst():
            visit(instance.cell, multiplicity * _instance_count(instance), next_seen)

    visit(top, 1, set())
    if not counts:
        raise MetricUnavailable("GDS hierarchy contains no identifiable named buffer/driver cells")
    return sum(record["instances"] * record["cell_bbox_area_um2"] for record in counts.values()), counts


def generated_layout_metrics(gds, top_name, parameters):
    """Return GDS bbox, effective-bit, and explicitly named buffer/driver areas."""
    try:
        import klayout.db as kdb
    except ImportError as exc:
        raise MetricUnavailable("klayout.db is unavailable for generated-layout measurement") from exc
    layout = kdb.Layout()
    layout.read(str(Path(gds).resolve()))
    top = layout.cell(str(top_name))
    if top is None:
        raise MetricUnavailable("generated GDS lacks top cell " + str(top_name))
    bbox = top.bbox()
    bbox_area = bbox.area() * layout.dbu * layout.dbu
    if bbox_area <= 0:
        raise MetricUnavailable("generated GDS top-cell bounding box is empty")
    words, bits = parameters.get("num_words"), parameters.get("word_size")
    effective_bit_area = None
    effective_reason = None
    if type(words) is not int or words <= 0 or type(bits) is not int or bits <= 0:
        effective_reason = "candidate lacks positive integer num_words and word_size"
    else:
        effective_bit_area = bbox_area / (words * bits)
    try:
        driver_area, named_cells = _named_buffer_driver_area(layout, top)
        driver_reason = None
    except MetricUnavailable as exc:
        driver_area, named_cells, driver_reason = None, {}, str(exc)
    return {
        "macro_bbox_area_um2": bbox_area,
        "effective_bit_area_um2": effective_bit_area,
        "effective_bit_area_reason": effective_reason,
        "buffer_driver_cell_area_um2": driver_area,
        "buffer_driver_cell_area_reason": driver_reason,
        "named_buffer_driver_cells": named_cells,
        "area_scope": "generated GDS top-cell bounding rectangle, including empty space",
        "effective_bit_area_definition": "macro bounding-box area / (num_words * word_size)",
        "buffer_driver_area_scope": (
            "sum of leaf named buffer/driver cell-instance bounding boxes; no strength/fanout estimate"),
    }


def actual_size_metrics(netlist, gds, top_name, parameters):
    """Measure device width and layout area independently, preserving failures."""
    result = {
        "total_mos_gate_width_um": None,
        "total_mos_gate_width_reason": None,
        "macro_bbox_area_um2": None,
        "macro_bbox_area_reason": None,
        "effective_bit_area_um2": None,
        "effective_bit_area_reason": None,
        "buffer_driver_cell_area_um2": None,
        "buffer_driver_cell_area_reason": None,
        "details": {"gate_width_interpretation": (
            "sum of instantiated MOS w * nf * m from the generated hierarchical netlist; "
            "intermediate device-size metric, not silicon area")},
    }
    try:
        result["total_mos_gate_width_um"] = total_hierarchical_mos_gate_width_um(netlist, top_name)
    except (OSError, UnicodeError, MetricUnavailable, ValueError, TypeError,
            OverflowError, RuntimeError) as exc:
        result["total_mos_gate_width_reason"] = str(exc)
    try:
        result_layout = generated_layout_metrics(gds, top_name, parameters)
        result["macro_bbox_area_um2"] = result_layout["macro_bbox_area_um2"]
        result["macro_bbox_area_reason"] = None
        result["effective_bit_area_um2"] = result_layout["effective_bit_area_um2"]
        result["effective_bit_area_reason"] = result_layout["effective_bit_area_reason"]
        result["buffer_driver_cell_area_um2"] = result_layout["buffer_driver_cell_area_um2"]
        result["buffer_driver_cell_area_reason"] = result_layout["buffer_driver_cell_area_reason"]
        result["details"].update(result_layout)
    except (OSError, UnicodeError, MetricUnavailable, ValueError, TypeError,
            OverflowError, RuntimeError, AttributeError) as exc:
        result["macro_bbox_area_reason"] = str(exc)
        result["effective_bit_area_reason"] = str(exc)
        result["buffer_driver_cell_area_reason"] = str(exc)
    return result
