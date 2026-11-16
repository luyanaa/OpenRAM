#!/usr/bin/env python3
"""Source-bound ICsprout55 native pbitcell electrical/layout research study.

This study instantiates the generated 0RW/1W/2R pbitcell only. It does not
construct a bank/router or claim DRC, LVS, PEX, timing, or signoff closure.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SIM = Path(__file__).resolve().parent
TECH = SIM.parent
ROOT = TECH.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))

from technology.icsprout55.sim import native_register_file as native
from architecture import _dependencies as model_dependencies

MODEL_RELATIVE = Path("libs.tech/spice/foundry/ngspice/ICsprout_55LLULP1225_V1p1_hsp.lib")
PVT_POINTS = {
    "tt_1p2_25": {"section": "tt_mos", "vdd_v": 1.20, "temperature_c": 25},
    "ss_1p08_125": {"section": "ss_mos", "vdd_v": 1.08, "temperature_c": 125},
    "ff_1p32_m40": {"section": "ff_mos", "vdd_v": 1.32, "temperature_c": -40},
}
CELL_NAME = "native_2r1w_pbitcell"
SNM_CELL_NAME = CELL_NAME + "_snm_study"
DEFAULT_CORNERS = tuple(PVT_POINTS)
WRITE_PULSE_WIDTHS_NS = (0.05, 0.15, 0.45)
BITLINE_LOAD_F = 5e-15
PRECHARGE_R_OHM = 10.0
PRECHARGE_EDGE_NS = 0.005
INITIAL_WRITE_START_NS = 0.10
INITIAL_WRITE_WIDTH_NS = 0.30
INITIAL_WRITE_SAMPLE_NS = 0.44
PRECHARGE_START_NS = 0.45
PRECHARGE_WIDTH_NS = 0.20
OPERATION_START_NS = 0.70
TRANSIENT_STEP_NS = 0.001
RECOVERY_NS = 0.40
VTC_STEP_V = 0.001
LOGIC_THRESHOLD_FRACTION = 0.5
EXPECTED_BL_PINS = ("bl0", "br0", "bl1", "br1", "bl2", "br2")
EXPECTED_WL_PINS = ("wl0", "wl1", "wl2")
MC_MARKERS = ("agauss", "monte=", "mc_mos", "mismatch", "sigma_mis")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": digest(path)}


def _spice_quote(path):
    return '"' + str(Path(path).resolve()).replace('"', '\\"') + '"'


def _pdk_root(value=None):
    if value is not None:
        return Path(value).expanduser().resolve()
    configured = os.environ.get("ICS55_PDK_ROOT") or os.environ.get("ICSPROUT55_PDK_ROOT")
    return Path(configured).expanduser().resolve() if configured else ROOT.parent / "icsprout55-pdk"


def _model_path(pdk_root):
    path = Path(pdk_root) / MODEL_RELATIVE
    if not path.is_file():
        raise ValueError("ICsprout55 ngspice model library is missing: " + str(path))
    return path.resolve()


def _tool_identity(name):
    executable = shutil.which(name)
    if executable is None:
        raise ValueError("ngspice executable is missing: " + name)
    executable = str(Path(executable).resolve())
    result = subprocess.run([executable, "--version"], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=15, check=True)
    return {"requested": name, "path": executable, "sha256": digest(executable),
            "version": result.stdout.strip(), "mode": "execution-enabled"}


def audit_monte_carlo(dependencies):
    """Describe statistical model markers without treating them as validation."""
    found = []
    for path in sorted(dependencies):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        matches = [marker for marker in MC_MARKERS if marker in text]
        if matches:
            found.append({"path": str(path), "markers": matches})
    if found:
        return {
            "status": "statistical_models_present_but_unvalidated_for_ngspice",
            "statistical_model_evidence": found,
            "execution": "not_run",
            "reason": ("the vendor closure contains statistical/mismatch constructs, but this "
                       "study has no validated ngspice statistical-model execution evidence; "
                       "no Monte Carlo deck or arbitrary random mismatch is used"),
        }
    return {
        "status": "statistical_model_support_not_found",
        "statistical_model_evidence": [],
        "execution": "not_run",
        "reason": "no validated statistical model path was found; no random mismatch is injected",
    }


def select_pvt_points(names):
    names = tuple(names)
    if not names or len(set(names)) != len(names):
        raise ValueError("PVT selection must contain unique named points")
    unknown = sorted(set(names) - set(PVT_POINTS))
    if unknown:
        raise ValueError("unsupported PVT point(s): " + ", ".join(unknown))
    return [(name, dict(PVT_POINTS[name])) for name in names]


def logical_spice_lines(lines):
    """Return (start, end, joined-text) records for SPICE continuation lines."""
    logical = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("*") or stripped.startswith("$"):
            index += 1
            continue
        start = index
        parts = [stripped]
        index += 1
        while index < len(lines) and lines[index].lstrip().startswith("+"):
            parts.append(lines[index].lstrip()[1:].strip())
            index += 1
        logical.append((start, index - 1, " ".join(parts)))
    return logical


def expose_storage_feedback(netlist_text, source_name=None):
    """Make a study-only cell copy with the two inverter gates externally swept.

    Only the four cross-coupled storage-inverter gate nets are changed. Access
    devices, foundry models, dimensions, ports, and all other connectivity stay
    byte-for-byte as generated, apart from the renamed/additional subckt pins.
    """
    lines = netlist_text.splitlines()
    subckts = []
    for start, end, text in logical_spice_lines(lines):
        fields = text.split()
        if fields and fields[0].lower() == ".subckt" and len(fields) >= 2:
            subckts.append((fields[1], start, end, fields))
    target = CELL_NAME
    matches = [item for item in subckts if item[0].casefold() == target.casefold()]
    if len(matches) != 1:
        raise ValueError("generated pbitcell netlist does not contain exactly one expected subckt")
    original_name, header_start, header_end, header = matches[0]
    if tuple(header[2:]) != native.EXPECTED_NATIVE_PINS:
        raise ValueError("generated pbitcell subckt pin order differs from the electrical testbench")
    end_records = [(start, end, text.split()) for start, end, text in logical_spice_lines(lines)
                   if text.split() and text.split()[0].lower() == ".ends"
                   and (len(text.split()) == 1 or text.split()[1].casefold() == original_name.casefold())]
    end_records = [item for item in end_records if item[0] > header_start]
    if len(end_records) != 1:
        raise ValueError("generated pbitcell subckt has no unique matching .ends")
    end_start, end_end, end_fields = end_records[0]
    if end_start != end_end:
        raise ValueError("generated pbitcell .ends uses unsupported continuation syntax")

    feedback_q, feedback_qb = [], []
    for start, end, text in logical_spice_lines(lines):
        if not (header_end < start < end_start):
            continue
        fields = text.split()
        if not fields[0].lower().startswith(("m", "x")) or len(fields) < 6 or start != end:
            continue
        nodes = [item.casefold() for item in fields[1:5]]
        if nodes[0] == "q" and nodes[1] == "q_bar" and nodes[2] in ("gnd", "vdd") and nodes[3] == nodes[2]:
            feedback_q.append(start)
        elif nodes[1] == "q" and nodes[2] == "q_bar" and nodes[0] in ("gnd", "vdd") and nodes[3] == nodes[0]:
            feedback_qb.append(start)
    if len(feedback_q) != 2 or len(feedback_qb) != 2:
        label = " in " + str(source_name) if source_name else ""
        raise ValueError("cannot unambiguously identify both cross-coupled inverter pairs" + label)

    for start in feedback_q:
        fields = lines[start].split()
        fields[2] = "qb_drive"
        lines[start] = " ".join(fields)
    for start in feedback_qb:
        fields = lines[start].split()
        fields[2] = "q_drive"
        lines[start] = " ".join(fields)
    new_name = SNM_CELL_NAME
    header_fields = list(header)
    header_fields[1] = new_name
    header_fields.extend(("q_drive", "qb_drive"))
    lines[header_start] = " ".join(header_fields)
    for index in range(header_start + 1, header_end + 1):
        lines[index] = ""
    end_fields = lines[end_start].split()
    if len(end_fields) > 1:
        end_fields[1] = new_name
        lines[end_start] = " ".join(end_fields)
    return "\n".join(lines) + "\n", {
        "source_subckt": original_name,
        "study_subckt": new_name,
        "storage_q_inverter_instances": [lines[index].split()[0] for index in feedback_q],
        "storage_qb_inverter_instances": [lines[index].split()[0] for index in feedback_qb],
        "gate_inputs": {"q_output_inverter": "qb_drive", "qb_output_inverter": "q_drive"},
    }


def parse_wrdata(path, minimum_columns=2):
    """Parse finite ngspice wrdata output, with optional vector-name header."""
    rows, header = [], None
    with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            fields = line.split()
            if not fields:
                continue
            try:
                row = [float(value) for value in fields]
            except ValueError:
                if header is not None or rows:
                    raise ValueError("unexpected nonnumeric wrdata line {} in {}".format(line_number, path))
                header = [field.strip().strip('"').casefold() for field in fields]
                continue
            if len(row) < minimum_columns or not all(math.isfinite(value) for value in row):
                raise ValueError("incomplete or non-finite wrdata row {} in {}".format(line_number, path))
            if rows and len(row) != len(rows[0]):
                raise ValueError("inconsistent wrdata column count in " + str(path))
            rows.append(row)
    if len(rows) < 2:
        raise ValueError("wrdata file has fewer than two numeric samples: " + str(path))
    if header is not None and len(header) != len(rows[0]):
        raise ValueError("wrdata header/data column mismatch in " + str(path))
    if header is None:
        header = ["column_{}".format(index) for index in range(len(rows[0]))]
    columns = {name: [row[index] for row in rows] for index, name in enumerate(header)}
    return {"header": header, "rows": rows, "columns": columns}


def _clean_curve(points):
    points = sorted((float(x), float(y)) for x, y in points)
    cleaned = []
    for x, y in points:
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("VTC curve contains non-finite data")
        if cleaned and abs(x - cleaned[-1][0]) <= 1e-15:
            cleaned[-1] = (x, 0.5 * (cleaned[-1][1] + y))
        else:
            cleaned.append((x, y))
    if len(cleaned) < 3:
        raise ValueError("VTC curve has too few distinct points")
    return cleaned


def _interp_curve(points, x):
    if x < points[0][0] - 1e-12 or x > points[-1][0] + 1e-12:
        raise ValueError("VTC interpolation lies outside measured domain")
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    low, high = 0, len(points) - 1
    while high - low > 1:
        middle = (low + high) // 2
        if points[middle][0] < x:
            low = middle
        else:
            high = middle
    x0, y0 = points[low]
    x1, y1 = points[high]
    ratio = (x - x0) / (x1 - x0)
    return y0 + ratio * (y1 - y0)


def _crossings(curve_a, curve_b, x_values, tolerance_v):
    differences = [_interp_curve(curve_a, x) - _interp_curve(curve_b, x) for x in x_values]
    roots = []
    for index, delta in enumerate(differences):
        if abs(delta) <= tolerance_v:
            roots.append((x_values[index], 0.5 * (_interp_curve(curve_a, x_values[index])
                                                   + _interp_curve(curve_b, x_values[index]))))
        if index + 1 == len(differences):
            continue
        next_delta = differences[index + 1]
        if delta * next_delta < 0:
            ratio = delta / (delta - next_delta)
            x = x_values[index] + ratio * (x_values[index + 1] - x_values[index])
            ya = _interp_curve(curve_a, x)
            yb = _interp_curve(curve_b, x)
            roots.append((x, 0.5 * (ya + yb)))
    deduplicated = []
    for root in sorted(roots):
        if deduplicated and math.hypot(root[0] - deduplicated[-1][0], root[1] - deduplicated[-1][1]) <= 4 * tolerance_v:
            previous = deduplicated[-1]
            deduplicated[-1] = (0.5 * (previous[0] + root[0]), 0.5 * (previous[1] + root[1]))
        else:
            deduplicated.append(root)
    return deduplicated


def point_in_polygon(point, polygon):
    x, y = point
    inside = False
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if (abs(cross) <= 1e-12 and min(x1, x2) - 1e-12 <= x <= max(x1, x2) + 1e-12
                and min(y1, y2) - 1e-12 <= y <= max(y1, y2) + 1e-12):
            return True
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def _linf_segment_distance(point, start, end):
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    candidates = [0.0, 1.0]
    for denominator, numerator in ((dx, px - ax), (dy, py - ay),
                                   (dx - dy, (px - ax) - (py - ay)),
                                   (dx + dy, (px - ax) + (py - ay))):
        if abs(denominator) > 1e-18:
            value = numerator / denominator
            if 0.0 <= value <= 1.0:
                candidates.append(value)
    return min(max(abs(ax + t * dx - px), abs(ay + t * dy - py)) for t in candidates)


def max_inscribed_square_linf(polygon, *, grid_points=96, refinements=4):
    """Estimate the largest axis-aligned square in a sampled simple polygon.

    Butterfly SNM uses squares aligned with the original Q/QB voltage axes.
    Rotating the polygon before this search would measure a different margin.
    Clearance uses the exact L-infinity distance to each piecewise-linear edge;
    only the center search is discretized and locally refined.
    """
    if len(polygon) < 4 or grid_points < 8 or refinements < 0:
        raise ValueError("polygon/search grid is too small")
    x_min = min(point[0] for point in polygon)
    x_max = max(point[0] for point in polygon)
    y_min = min(point[1] for point in polygon)
    y_max = max(point[1] for point in polygon)
    span = min(x_max - x_min, y_max - y_min)
    if span <= 0:
        raise ValueError("polygon has no positive two-dimensional extent")

    edges = list(zip(polygon, polygon[1:] + polygon[:1]))

    def clearance(point):
        if not point_in_polygon(point, polygon):
            return -1.0
        return min(_linf_segment_distance(point, first, second) for first, second in edges)

    best_point, best_radius = None, -1.0
    initial_step = span / grid_points
    nx = max(1, int(math.ceil((x_max - x_min) / initial_step)))
    ny = max(1, int(math.ceil((y_max - y_min) / initial_step)))
    for ix in range(nx + 1):
        x = min(x_max, x_min + ix * initial_step)
        for iy in range(ny + 1):
            y = min(y_max, y_min + iy * initial_step)
            value = clearance((x, y))
            if value > best_radius:
                best_point, best_radius = (x, y), value
    if best_point is None or best_radius <= 0:
        raise ValueError("no positive interior square was found in butterfly lobe")

    step = initial_step
    for _ in range(refinements):
        step /= 10.0
        center = best_point
        for ix in range(-10, 11):
            x = center[0] + ix * step
            if not x_min <= x <= x_max:
                continue
            for iy in range(-10, 11):
                y = center[1] + iy * step
                if not y_min <= y <= y_max:
                    continue
                value = clearance((x, y))
                if value > best_radius:
                    best_point, best_radius = (x, y), value
    return {
        "side_v": 2.0 * best_radius,
        "center_uv_v": [best_point[0], best_point[1]],
        "center_search_initial_pitch_v": initial_step,
        "center_search_final_pitch_v": step,
        "orientation_in_transformed_plane_deg": 0.0,
    }


def _segment_curve(curve, start_x, end_x, sample_step_v):
    if end_x < start_x:
        raise ValueError("invalid VTC segment range")
    count = max(1, int(math.ceil((end_x - start_x) / sample_step_v)))
    xs = [start_x + (end_x - start_x) * index / count for index in range(count + 1)]
    return [(x, _interp_curve(curve, x)) for x in xs]


def extract_butterfly_snm(q_curve, qb_curve, vdd_v):
    """Extract both hold-state butterfly margins from two measured inverter VTCs."""
    q_curve, qb_curve = _clean_curve(q_curve), _clean_curve(qb_curve)
    x_low = max(q_curve[0][0], qb_curve[0][0])
    x_high = min(q_curve[-1][0], qb_curve[-1][0])
    if x_high <= x_low:
        raise ValueError("storage-inverter VTC domains do not overlap")
    sample_step = min(VTC_STEP_V, vdd_v / 1000.0)
    count = max(2, int(math.ceil((x_high - x_low) / sample_step)))
    xs = [x_low + (x_high - x_low) * index / count for index in range(count + 1)]
    endpoint_tolerance = max(2.0 * sample_step, 0.002 * vdd_v)
    roots = _crossings(q_curve, qb_curve, xs, endpoint_tolerance)
    if len(roots) < 3:
        raise ValueError("butterfly VTCs have fewer than three stable/metastable intersections")
    middle = min(roots, key=lambda point: abs(point[0] - 0.5 * vdd_v))
    left_candidates = [point for point in roots if point[0] < middle[0] - endpoint_tolerance]
    right_candidates = [point for point in roots if point[0] > middle[0] + endpoint_tolerance]
    if not left_candidates or not right_candidates:
        raise ValueError("butterfly VTCs do not bound both stable-state lobes")
    low = min(left_candidates, key=lambda point: point[0])
    high = max(right_candidates, key=lambda point: point[0])

    def lobe_margin(start_x, stop_x):
        first = _segment_curve(q_curve, start_x, stop_x, sample_step)
        second = _segment_curve(qb_curve, start_x, stop_x, sample_step)
        polygon = first + list(reversed(second))[1:-1]
        result = max_inscribed_square_linf(polygon)
        result["square_center_q_qb_v"] = list(result["center_uv_v"])
        result["orientation_q_qb_deg"] = 0.0
        return result

    return {
        "status": "measured_from_dc_vtcs",
        "vdd_v": vdd_v,
        "stable_state_q0_qb1": lobe_margin(low[0], middle[0]),
        "stable_state_q1_qb0": lobe_margin(middle[0], high[0]),
        "intersections_q_qb_v": {"low_state": list(low), "metastable": list(middle),
                                  "high_state": list(high)},
        "vtc_sample_step_v": sample_step,
        "endpoint_match_tolerance_v": endpoint_tolerance,
        "method": {
            "name": "hold butterfly maximum inscribed square",
            "square_orientation_q_qb_axes_deg": 0.0,
            "computation": "maximize piecewise-linear-boundary L-infinity clearance in the original Q/QB voltage axes; no rotation",
            "read_wordlines": "held low",
            "write_wordline": "held low",
            "storage_gate_handling": "study-only copy exposes the actual four storage-inverter gates; no MOS model or dimension is replaced",
            "interpretation": "research DC hold margin; not a foundry characterization or signoff limit",
        },
    }


def _is_supply_rail(name):
    """Supply rails are abutted between mirrored rows, not router access pins."""
    return str(name).casefold() in ("vdd", "gnd")


def _supply_rail_straddle(shape, width_um, height_um, grid_um):
    """Classify a full-width supply rail that leaves the cell bounding box.

    Array rows in this technology are placed with alternating ``MX`` mirrors
    (``compiler/modules/bitcell_base_array.py``), so ``route_rails`` centers the
    ``vdd``/``gnd`` rails on the top/bottom cell edges and each rail coincides
    with the neighbour's same-net rail instead of being a routed access pin.
    Such a rail may straddle an edge by at most half of its own thickness while
    spanning the full cell width on ``m1``; anything else still fails closed.
    """
    x0, y0, x1, y1 = shape["rect_um"]
    thickness = y1 - y0
    center_y = 0.5 * (y0 + y1)
    straddle = max(-y0, y1 - height_um, 0.0)
    record = {"layer_is_m1": shape["layer"] == "m1",
              "spans_cell_width": x0 <= grid_um and x1 >= width_um - grid_um,
              "centered_on_bbox_edge": (abs(center_y) <= 0.5 * thickness + grid_um or
                                        abs(center_y - height_um) <= 0.5 * thickness + grid_um),
              "straddle_um": straddle,
              "allowed_straddle_um": 0.5 * thickness + grid_um}
    record["accepted"] = (record["layer_is_m1"] and record["spans_cell_width"]
                          and record["centered_on_bbox_edge"]
                          and straddle <= record["allowed_straddle_um"])
    return record


def _polygon_data(width_um, height_um, pins, expected_pins, grid_um=0.005):
    if not math.isfinite(width_um) or not math.isfinite(height_um) or width_um <= 0 or height_um <= 0:
        raise ValueError("layout dimensions must be finite and positive")
    missing = sorted(set(expected_pins) - set(pins))
    if missing:
        raise ValueError("layout pin inventory missing: " + ", ".join(missing))
    shapes, out_of_bbox, rail_straddles = {}, [], {}
    for name, records in pins.items():
        normalized = []
        for record in records:
            layer = str(record["layer"]).lower()
            (x0, y0), (x1, y1) = record["rect"]
            x0, y0, x1, y1 = map(float, (x0, y0, x1, y1))
            if not all(math.isfinite(value) for value in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
                raise ValueError("invalid pin rectangle for " + name)
            inside = (x0 >= -grid_um and y0 >= -grid_um
                      and x1 <= width_um + grid_um and y1 <= height_um + grid_um)
            entry = {"layer": layer, "rect_um": [x0, y0, x1, y1], "inside_bbox": inside,
                     "center_um": [0.5 * (x0 + x1), 0.5 * (y0 + y1)],
                     "width_um": x1 - x0, "height_um": y1 - y0}
            normalized.append(entry)
            if inside:
                continue
            if _is_supply_rail(name):
                rail = _supply_rail_straddle(entry, width_um, height_um, grid_um)
                rail_straddles.setdefault(name, []).append(rail)
                if rail["accepted"]:
                    continue
            out_of_bbox.append(name)
        if not normalized:
            raise ValueError("layout pin has no access shapes: " + name)
        shapes[name] = normalized

    bitline_checks = {}
    for name in EXPECTED_BL_PINS:
        shape = shapes[name][0]
        x0, y0, x1, y1 = shape["rect_um"]
        bitline_checks[name] = {
            "layer_is_m2": shape["layer"] == "m2",
            "vertical": shape["height_um"] >= shape["width_um"],
            "spans_cell_height": y0 <= grid_um and y1 >= height_um - grid_um,
        }
    wordline_checks = {}
    for name in EXPECTED_WL_PINS:
        shape = shapes[name][0]
        x0, y0, x1, y1 = shape["rect_um"]
        wordline_checks[name] = {
            "layer_is_m1": shape["layer"] == "m1",
            "horizontal": shape["width_um"] >= shape["height_um"],
            "spans_cell_width": x0 <= grid_um and x1 >= width_um - grid_um,
        }

    bitline_shapes = [(name, shapes[name][0]) for name in EXPECTED_BL_PINS]
    bitline_shapes.sort(key=lambda item: item[1]["center_um"][0])
    bitline_gaps = []
    for (left_name, left), (right_name, right) in zip(bitline_shapes, bitline_shapes[1:]):
        left_x1 = left["rect_um"][2]
        right_x0 = right["rect_um"][0]
        bitline_gaps.append({"left": left_name, "right": right_name,
                             "centerline_pitch_um": right["center_um"][0] - left["center_um"][0],
                             "edge_gap_um": right_x0 - left_x1})
    wordline_shapes = [(name, shapes[name][0]) for name in EXPECTED_WL_PINS]
    wordline_shapes.sort(key=lambda item: item[1]["center_um"][1])
    wordline_pitches = []
    for (lower_name, lower), (upper_name, upper) in zip(wordline_shapes, wordline_shapes[1:]):
        wordline_pitches.append({
            "lower": lower_name, "upper": upper_name,
            "centerline_pitch_um": upper["center_um"][1] - lower["center_um"][1],
            "edge_gap_um": upper["rect_um"][1] - lower["rect_um"][3],
        })
    write_read_pairs = {"write_port0": ["bl0", "br0"], "read_port0": ["bl1", "br1"],
                        "read_port1": ["bl2", "br2"]}
    pair_pitch = {name: abs(shapes[pair[0]][0]["center_um"][0] - shapes[pair[1]][0]["center_um"][0])
                  for name, pair in write_read_pairs.items()}
    all_inside = not out_of_bbox
    rails_ok = all(rail["accepted"] for rails in rail_straddles.values() for rail in rails)
    bitline_ok = all(all(values.values()) for values in bitline_checks.values())
    wordline_ok = all(all(values.values()) for values in wordline_checks.values())
    bitline_no_overlap = all(item["edge_gap_um"] > 0 for item in bitline_gaps)
    wordline_no_overlap = all(item["edge_gap_um"] > 0 for item in wordline_pitches)
    return {
        "schema": "ics55-native-pbitcell-layout-geometry/1",
        "scope": "one generated pbitcell only; no bank, decoder, mux, router, or array expansion",
        "cell_bbox_um": {"width": width_um, "height": height_um, "area_um2": width_um * height_um},
        "pins": shapes,
        "supply_rail_straddles": rail_straddles,
        "checks": {"expected_pin_inventory": True, "pin_shapes_inside_bbox": all_inside,
                   "supply_rails_centered_on_bbox_edges": rails_ok,
                   "bitlines_are_vertical_m2_full_height": bitline_ok,
                   "wordlines_are_horizontal_m1_full_width": wordline_ok,
                   "adjacent_bitlines_do_not_overlap_geometrically": bitline_no_overlap,
                   "adjacent_wordlines_do_not_overlap_geometrically": wordline_no_overlap},
        "bitline_pair_centerline_pitch_um": pair_pitch,
        "adjacent_bitline_centerline_pitches_and_edge_gaps": bitline_gaps,
        "wordline_centerline_pitches_and_edge_gaps": wordline_pitches,
        "status": ("pin_access_geometry_observed"
                   if all_inside and bitline_ok and wordline_ok
                   and bitline_no_overlap and wordline_no_overlap
                   else "pin_access_geometry_issue"),
        "limitations": ["geometry inventory is not a DRC result", "no LVS was run",
                        "accepted supply-rail straddles are the abutted-row convention of "
                        "MX-mirrored rows sharing vdd/gnd rails, not array-level closure",
                        "no router/bank expansion or array-level pitch closure is represented"],
    }


def audit_layout_geometry(width_um, height_um, pins, *, grid_um=0.005):
    """Public deterministic geometry boundary used by tests and layout export."""
    return _polygon_data(width_um, height_um, pins, native.EXPECTED_NATIVE_PINS, grid_um)


def _extract_layout_pins(cell):
    pins = {}
    for name in native.EXPECTED_NATIVE_PINS:
        pins[name] = []
        for pin in sorted(cell.get_pins(name), key=lambda item: (item.ll().x, item.ll().y, item.layer)):
            pins[name].append({"layer": pin.layer,
                               "rect": [[pin.rect[0].x, pin.rect[0].y],
                                        [pin.rect[1].x, pin.rect[1].y]]})
    return pins


def _generate_layout(out_dir, config_path):
    import common
    common.make_openram_package()
    import openram
    from openram import OPTS
    from openram.sram_factory import factory

    out_dir.mkdir(parents=True, exist_ok=True)
    initialized = False
    try:
        openram.init_openram(str(config_path), is_unit_test=True)
        initialized = True
        OPTS.output_path = str(out_dir) + os.sep
        OPTS.output_name = "native_2r1w_pbitcell_layout"
        OPTS.netlist_only = False
        OPTS.check_lvsdrc = False
        OPTS.inline_lvsdrc = False
        cell = factory.create(module_type="pbitcell", module_name=OPTS.output_name)
        if tuple(cell.pins) != native.EXPECTED_NATIVE_PINS:
            raise ValueError("layout pbitcell pin order differs from the native contract")
        gds_path = out_dir / (OPTS.output_name + ".gds")
        cell.gds_write(str(gds_path))
        geometry = audit_layout_geometry(cell.width, cell.height, _extract_layout_pins(cell))
        geometry.update({"gds": ref(gds_path),
                         "source": {"pbitcell": ref(ROOT / "compiler/modules/pbitcell.py"),
                                    "technology": ref(TECH / "tech/tech.py"),
                                    "config": ref(config_path)}})
        return geometry
    finally:
        if initialized:
            openram.end_openram()


def _source_refs(config_path):
    paths = (Path(__file__).resolve(), Path(native.__file__).resolve(),
             ROOT / "compiler/modules/pbitcell.py", ROOT / "compiler/modules/ptx.py",
             ROOT / "compiler/base/hierarchy_spice.py",
             ROOT / "compiler/base/hierarchy_layout.py",
             ROOT / "compiler/base/pin_layout.py",
             TECH / "tech/tech.py", Path(config_path).resolve())
    return [ref(path) for path in paths]


def _make_pwl(initial, changes, vdd, edge_ns=PRECHARGE_EDGE_NS):
    """Build a finite-slew PWL rail from (time_ns, normalized_value) events."""
    points = [(0.0, initial)]
    value = initial
    previous_end = 0.0
    for time_ns, next_value in sorted(changes):
        time_ns = float(time_ns)
        next_value = float(next_value)
        if time_ns < previous_end + edge_ns:
            raise ValueError("PWL transitions overlap or are not strictly ordered")
        points.extend(((time_ns, value), (time_ns + edge_ns, next_value)))
        value = next_value
        previous_end = time_ns + edge_ns
    if len(points) == 1:
        points.append((1.0, value))
    return "PWL(" + " ".join("{:.9g}n {:.9g}".format(t, level * vdd) for t, level in points) + ")"


def _pulse_changes(start_ns, width_ns, edge_ns=PRECHARGE_EDGE_NS, active=1.0):
    if width_ns <= 2.0 * edge_ns:
        raise ValueError("pulse width must exceed two finite-slew edges")
    return [(start_ns, active), (start_ns + width_ns - edge_ns, 0.0)]


def _write_bitlines(initial_q, target_q=None):
    initial = (1.0, 0.0) if initial_q else (0.0, 1.0)
    if target_q is None:
        return initial, initial
    target = (1.0, 0.0) if target_q else (0.0, 1.0)
    return initial, target


def _spice_source(name, node, initial, events, vdd):
    return "V{} {} 0 {}".format(name, node, _make_pwl(initial, events, vdd))


def _write_transient_deck(deck_path, cell_spice, model_path, pvt_name, pvt,
                          *, initial_q, target_q=None, write_width_ns=None,
                          read_enabled=False, read_ports=None,
                          read_disturb=False, concurrent=False):
    vdd = pvt["vdd_v"]
    if read_disturb:
        operation_width = 0.40
        target_q = None
        read_ports = (1, 2)
    elif concurrent:
        operation_width = 0.40
        if target_q is None:
            raise ValueError("concurrent 2R1W case requires an explicit write datum")
        write_width_ns = operation_width
        read_ports = (1, 2)
    else:
        if target_q is None or write_width_ns is None:
            raise ValueError("write case requires a target datum and sampled pulse width")
        operation_width = float(write_width_ns)
        if read_ports is None:
            read_ports = (1, 2) if read_enabled else ()
    read_ports = tuple(read_ports)
    if len(set(read_ports)) != len(read_ports) or not set(read_ports).issubset({1, 2}):
        raise ValueError("read port enables must be a unique subset of ports 1 and 2")
    read_enabled = bool(read_ports)

    q_inputs, target_inputs = _write_bitlines(initial_q, target_q)
    data_change_ns = INITIAL_WRITE_START_NS + INITIAL_WRITE_WIDTH_NS + 0.08
    bl0_events = [(data_change_ns, target_inputs[0])] if target_q is not None else []
    br0_events = [(data_change_ns, target_inputs[1])] if target_q is not None else []
    wl0_events = _pulse_changes(INITIAL_WRITE_START_NS, INITIAL_WRITE_WIDTH_NS)
    if target_q is not None:
        wl0_events += _pulse_changes(OPERATION_START_NS, operation_width)
    wl1_events = _pulse_changes(OPERATION_START_NS, operation_width) if 1 in read_ports else []
    wl2_events = _pulse_changes(OPERATION_START_NS, operation_width) if 2 in read_ports else []
    pre_events = _pulse_changes(PRECHARGE_START_NS, PRECHARGE_WIDTH_NS)
    stop_ns = max(OPERATION_START_NS + operation_width, PRECHARGE_START_NS + PRECHARGE_WIDTH_NS) + RECOVERY_NS
    vectors = ["v(xcell.q)", "v(xcell.q_bar)", "v(bl1)", "v(br1)", "v(bl2)", "v(br2)",
               "v(wl0)", "v(wl1)", "v(wl2)", "v(pre)"]
    lines = ["Native pbitcell {} {}".format(pvt_name, deck_path.parent.name),
             ".lib {} {}".format(_spice_quote(model_path), pvt["section"]),
             ".lib {} pre_layout".format(_spice_quote(model_path)),
             ".include {}".format(_spice_quote(cell_spice)),
             ".temp {}".format(pvt["temperature_c"]),
             ".options numdgt=15", "VDD vdd 0 {:.9g}".format(vdd),
             _spice_source("bl0", "bl0", q_inputs[0], bl0_events, vdd),
             _spice_source("br0", "br0", q_inputs[1], br0_events, vdd),
             _spice_source("wl0", "wl0", 0.0, wl0_events, vdd),
             _spice_source("wl1", "wl1", 0.0, wl1_events, vdd),
             _spice_source("wl2", "wl2", 0.0, wl2_events, vdd),
             _spice_source("pre", "pre", 0.0, pre_events, vdd),
             "Cbl1 bl1 0 {:.9g}".format(BITLINE_LOAD_F),
             "Cbr1 br1 0 {:.9g}".format(BITLINE_LOAD_F),
             "Cbl2 bl2 0 {:.9g}".format(BITLINE_LOAD_F),
             "Cbr2 br2 0 {:.9g}".format(BITLINE_LOAD_F),
             ".model precharge_switch SW(Ron={:.9g} Roff=1e12 Vt=0.5 Vh=0.05)".format(PRECHARGE_R_OHM),
             "Spre_bl1 bl1 vdd pre 0 precharge_switch",
             "Spre_br1 br1 vdd pre 0 precharge_switch",
             "Spre_bl2 bl2 vdd pre 0 precharge_switch",
             "Spre_br2 br2 vdd pre 0 precharge_switch",
             "Xcell bl0 br0 bl1 br1 bl2 br2 wl0 wl1 wl2 vdd 0 {}".format(CELL_NAME),
             ".control", "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
             "save " + " ".join(vectors),
             "tran {:.9g}n {:.9g}n 0 {:.9g}n".format(TRANSIENT_STEP_NS, stop_ns, TRANSIENT_STEP_NS),
             "wrdata waves.dat " + " ".join(vectors), "quit", ".endc", ".end"]
    deck_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "study": "both_read_disturb" if read_disturb else "concurrent_2r1w" if concurrent else "write_margin",
        "initial_q": initial_q, "target_q": target_q, "read_enabled": read_enabled,
        "read_ports_enabled": list(read_ports),
        "operation_start_ns": OPERATION_START_NS, "operation_width_ns": operation_width,
        "precharge_start_ns": PRECHARGE_START_NS, "precharge_width_ns": PRECHARGE_WIDTH_NS,
        "initial_physical_write": {"wl0_start_ns": INITIAL_WRITE_START_NS,
                                    "wl0_width_ns": INITIAL_WRITE_WIDTH_NS,
                                    "sample_ns": INITIAL_WRITE_SAMPLE_NS,
                                    "bitline_data": {"bl0": "VDD" if initial_q else "0",
                                                     "br0": "0" if initial_q else "VDD"}},
        "write_driver_assumption": "ideal PWL bitline voltage sources; no peripheral-driver resistance or extracted wiring",
        "read_bitline_assumption": {"precharge_switch": "ideal voltage-controlled switch",
                                     "precharge_ron_ohm": PRECHARGE_R_OHM,
                                     "capacitance_each_ff": BITLINE_LOAD_F * 1e15},
        "logic_threshold_fraction": LOGIC_THRESHOLD_FRACTION,
        "stop_ns": stop_ns,
        "initialization_policy": "storage initialized only by physical WL0/BL0/BR0 write; no .ic, UIC, or storage-node forcing",
    }


def _write_snm_deck(deck_path, cell_spice, model_path, pvt_name, pvt, sweep="q"):
    vdd = pvt["vdd_v"]
    if sweep not in ("q", "qb"):
        raise ValueError("SNM sweep must be q or qb")
    source = "VQB qb_drive 0 0" if sweep == "q" else "VQ q_drive 0 0"
    sweep_source = "VQB" if sweep == "q" else "VQ"
    output_node = "xcell.q" if sweep == "q" else "xcell.q_bar"
    deck = ["Native pbitcell hold SNM VTC {} {}".format(pvt_name, sweep),
            ".lib {} {}".format(_spice_quote(model_path), pvt["section"]),
            ".lib {} pre_layout".format(_spice_quote(model_path)),
            ".include {}".format(_spice_quote(cell_spice)),
            ".temp {}".format(pvt["temperature_c"]),
            ".options numdgt=15", "VDD vdd 0 {:.9g}".format(vdd),
            "VBL0 bl0 0 0", "VBR0 br0 0 0", "VBL1 bl1 0 0", "VBR1 br1 0 0",
            "VBL2 bl2 0 0", "VBR2 br2 0 0", "VWL0 wl0 0 0", "VWL1 wl1 0 0",
            "VWL2 wl2 0 0", "VQ q_drive 0 0" if sweep == "q" else "VQB qb_drive 0 0",
            source,
            "Xcell bl0 br0 bl1 br1 bl2 br2 wl0 wl1 wl2 vdd 0 q_drive qb_drive {}".format(SNM_CELL_NAME),
            ".control", "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
            "save v({})".format(output_node),
            "dc {} 0 {:.9g} {:.9g}".format(sweep_source, vdd, VTC_STEP_V),
            "wrdata vtc.dat v({})".format(output_node), "quit", ".endc", ".end"]
    deck_path.write_text("\n".join(deck) + "\n", encoding="utf-8")
    return {"study": "hold_snm_vtc", "curve": sweep, "pvt": pvt_name,
            "sweep_source": sweep_source, "sweep_range_v": [0.0, vdd],
            "sweep_step_v": VTC_STEP_V, "all_wordlines": 0,
            "initialization_policy": "DC inverter-transfer sweep; no transient initialization or forced storage state"}


def _case_record(case_id, directory, pvt_name, spec, deck_meta, deck_path, data_path):
    directory.mkdir(parents=True, exist_ok=True)
    return {"case_id": case_id, "directory": str(directory.resolve()), "pvt": pvt_name,
            "study": spec, "deck_metadata": deck_meta, "deck": str(deck_path.resolve()),
            "data": str(data_path.resolve()), "status": "prepared"}


def _build_case_specs(out_dir, cell_spice, model_path, pvt_points):
    cases = []
    snm_netlist = out_dir / "inputs" / "native_2r1w_pbitcell_snm_study.sp"
    snm_text, snm_edit = expose_storage_feedback(cell_spice.read_text(encoding="utf-8"), cell_spice)
    snm_netlist.write_text(snm_text, encoding="utf-8")
    (out_dir / "inputs" / "snm_gate_exposure.json").write_text(
        json.dumps(snm_edit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for pvt_name, pvt in pvt_points:
        for curve in ("q", "qb"):
            case_id = "{}_hold_snm_{}".format(pvt_name, curve)
            directory = out_dir / "cases" / case_id
            directory.mkdir(parents=True, exist_ok=True)
            deck = directory / "study.sp"
            meta = _write_snm_deck(deck, snm_netlist, model_path, pvt_name, pvt, curve)
            cases.append(_case_record(case_id, directory, pvt_name, "hold_snm_vtc", meta, deck,
                                      directory / "vtc.dat"))

        for initial_q in (0, 1):
            case_id = "{}_both_read_disturb_q{}".format(pvt_name, initial_q)
            directory = out_dir / "cases" / case_id
            directory.mkdir(parents=True, exist_ok=True)
            deck = directory / "study.sp"
            meta = _write_transient_deck(deck, cell_spice, model_path, pvt_name, pvt,
                                         initial_q=initial_q, read_disturb=True)
            cases.append(_case_record(case_id, directory, pvt_name, "both_read_disturb", meta, deck,
                                      directory / "waves.dat"))

        for initial_q in (0, 1):
            target_q = 1 - initial_q
            for read_ports in ((), (1,), (2,), (1, 2)):
                read_mask = sum(1 << (port - 1) for port in read_ports)
                for width_ns in WRITE_PULSE_WIDTHS_NS:
                    case_id = "{}_write_q{}_to_q{}_rm{:02b}_{}ps".format(
                        pvt_name, initial_q, target_q, read_mask, int(round(width_ns * 1000)))
                    directory = out_dir / "cases" / case_id
                    directory.mkdir(parents=True, exist_ok=True)
                    deck = directory / "study.sp"
                    meta = _write_transient_deck(deck, cell_spice, model_path, pvt_name, pvt,
                                                 initial_q=initial_q, target_q=target_q,
                                                 write_width_ns=width_ns, read_ports=read_ports)
                    cases.append(_case_record(case_id, directory, pvt_name, "write_margin", meta, deck,
                                              directory / "waves.dat"))

            case_id = "{}_concurrent_2r1w_q{}_to_q{}".format(pvt_name, initial_q, target_q)
            directory = out_dir / "cases" / case_id
            directory.mkdir(parents=True, exist_ok=True)
            deck = directory / "study.sp"
            meta = _write_transient_deck(deck, cell_spice, model_path, pvt_name, pvt,
                                         initial_q=initial_q, target_q=target_q,
                                         concurrent=True)
            cases.append(_case_record(case_id, directory, pvt_name, "concurrent_2r1w", meta, deck,
                                      directory / "waves.dat"))
    return cases


def _execute_case(case, tool, timeout_s):
    deck = Path(case["deck"])
    directory = Path(case["directory"])
    command = [tool["path"], "-n", "-D", "ngbehavior=hsa", "-b", str(deck)]
    started = time.monotonic()
    try:
        process = subprocess.run(command, cwd=str(directory), text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 timeout=timeout_s, check=False)
        text, returncode = process.stdout, process.returncode
    except subprocess.TimeoutExpired as exc:
        text = exc.stdout or ""
        if isinstance(text, bytes):
            text = text.decode(errors="replace")
        returncode = None
        case["execution_error"] = "ngspice timeout after {} seconds".format(timeout_s)
    except OSError as exc:
        text, returncode = str(exc), None
        case["execution_error"] = "ngspice launch failed: " + str(exc)
    log = directory / "ngspice.log"
    log.write_text(text, encoding="utf-8", errors="replace")
    case["execution"] = {"command": command, "returncode": returncode,
                         "elapsed_s": round(time.monotonic() - started, 6),
                         "log": ref(log)}
    data = Path(case["data"])
    if returncode != 0 or not data.is_file() or data.stat().st_size == 0:
        case["status"] = "execution_failed"
        if "execution_error" not in case:
            case["execution_error"] = "ngspice exited nonzero or produced no complete raw data"
        return case
    case["status"] = "executed"
    case["raw_data"] = ref(data)
    case["deck_ref"] = ref(deck)
    return case


def _column(table, name, fallback_index=None):
    target = name.casefold()
    for index, header in enumerate(table["header"]):
        if header.casefold() == target:
            return [row[index] for row in table["rows"]]
    if fallback_index is not None and fallback_index < len(table["header"]):
        return [row[fallback_index] for row in table["rows"]]
    raise ValueError("wrdata is missing required column " + name)


def _interpolate(xs, ys, target):
    if target < xs[0] or target > xs[-1]:
        raise ValueError("requested sample lies outside waveform time range")
    low, high = 0, len(xs) - 1
    while high - low > 1:
        middle = (low + high) // 2
        if xs[middle] < target:
            low = middle
        else:
            high = middle
    if xs[low] == target:
        return ys[low]
    if xs[high] == target:
        return ys[high]
    ratio = (target - xs[low]) / (xs[high] - xs[low])
    return ys[low] + ratio * (ys[high] - ys[low])


def _window_values(xs_ns, ys, start_ns, stop_ns):
    values = [value for time_ns, value in zip(xs_ns, ys) if start_ns <= time_ns <= stop_ns]
    values.extend((_interpolate(xs_ns, ys, start_ns), _interpolate(xs_ns, ys, stop_ns)))
    return values


def _crossing_time(xs_ns, ys, threshold, start_ns, stop_ns, rising):
    for index in range(1, len(xs_ns)):
        t0, t1 = xs_ns[index - 1], xs_ns[index]
        if t1 < start_ns or t0 > stop_ns:
            continue
        v0, v1 = ys[index - 1], ys[index]
        crossed = (v0 < threshold <= v1) if rising else (v0 > threshold >= v1)
        if crossed:
            if v1 == v0:
                return t1
            return t0 + (threshold - v0) * (t1 - t0) / (v1 - v0)
    return None


def summarize_write_sweep(widths_ns, succeeded):
    """Report sampled write outcomes without assuming a monotone margin curve."""
    if len(widths_ns) != len(succeeded) or not widths_ns:
        raise ValueError("write sweep needs one verdict per sampled pulse width")
    ordered = sorted(zip(map(float, widths_ns), map(bool, succeeded)))
    if len({width for width, _ in ordered}) != len(ordered):
        raise ValueError("write sweep pulse widths must be unique")
    passes = [width for width, success in ordered if success]
    failures = [width for width, success in ordered if not success]
    verdicts = [success for _, success in ordered]
    nonmonotonic = any(verdicts[index] and not verdicts[index + 1]
                       for index in range(len(verdicts) - 1))
    boundary = None
    if passes and failures and not nonmonotonic:
        candidates = [(fail, success) for fail in failures for success in passes if fail < success]
        if candidates:
            boundary = {"largest_sampled_failure_ns": max(item[0] for item in candidates),
                        "smallest_sampled_success_ns": min(item[1] for item in candidates)}
    return {"sampled_success_widths_ns": passes, "sampled_failure_widths_ns": failures,
            "observed_nonmonotonicity": nonmonotonic,
            "sampled_transition_bracket_ns": boundary,
            "interpolation_or_extrapolation": "none"}


def _analyze_transient(case, table):
    headers = case["deck_metadata"]
    xs = [value * 1e9 for value in _column(table, "time", 0)]
    signals = {
        name: _column(table, "v(" + name + ")")
        for name in ("xcell.q", "xcell.q_bar", "bl1", "br1", "bl2", "br2", "wl0", "wl1", "wl2", "pre")
    }
    start = headers["operation_start_ns"]
    stop = start + headers["operation_width_ns"]
    sample_time = headers["stop_ns"] - 0.02
    initial_sample_time = headers["initial_physical_write"]["sample_ns"]
    initial_q_v = _interpolate(xs, signals["xcell.q"], initial_sample_time)
    initial_qb_v = _interpolate(xs, signals["xcell.q_bar"], initial_sample_time)
    final_q = _interpolate(xs, signals["xcell.q"], sample_time)
    final_qb = _interpolate(xs, signals["xcell.q_bar"], sample_time)
    vdd = case["pvt_vdd_v"]
    threshold = LOGIC_THRESHOLD_FRACTION * vdd

    def classify_state(q_value, qb_value):
        return (1 if q_value >= threshold and qb_value < threshold else
                0 if q_value < threshold and qb_value >= threshold else None)

    initial_state = classify_state(initial_q_v, initial_qb_v)
    initial_write_succeeded = initial_state == headers["initial_q"]
    final_state = classify_state(final_q, final_qb)
    read_bitlines = {}
    for port in (1, 2):
        bl_name, br_name = "bl{}".format(port), "br{}".format(port)
        bl_start = _interpolate(xs, signals[bl_name], start)
        br_start = _interpolate(xs, signals[br_name], start)
        bl_end = _interpolate(xs, signals[bl_name], stop)
        br_end = _interpolate(xs, signals[br_name], stop)
        bl_cross = _crossing_time(xs, signals[bl_name], threshold, start, stop, False)
        br_cross = _crossing_time(xs, signals[br_name], threshold, start, stop, False)
        read_bitlines["port{}".format(port)] = {
            "bl_start_v": bl_start, "br_start_v": br_start,
            "bl_end_v": bl_end, "br_end_v": br_end,
            "signed_bl_minus_br_end_v": bl_end - br_end,
            "bl_crossing_below_half_vdd_ns": None if bl_cross is None else bl_cross - start,
            "br_crossing_below_half_vdd_ns": None if br_cross is None else br_cross - start,
        }
    result = {"case_id": case["case_id"], "study": case["study"], "pvt": case["pvt"],
              "initial_q": headers["initial_q"], "target_q": headers["target_q"],
              "read_enabled": headers["read_enabled"],
              "read_ports_enabled": headers["read_ports_enabled"],
              "initial_write_sample_ns": initial_sample_time,
              "initial_write_q_v": initial_q_v, "initial_write_qb_v": initial_qb_v,
              "initial_logic_state_q": initial_state,
              "initial_write_succeeded": initial_write_succeeded,
              "read_window_ns": [start, stop], "read_bitlines": read_bitlines,
              "final_q_v": final_q, "final_qb_v": final_qb,
              "final_logic_state_q": final_state, "final_sample_ns": sample_time,
              "logic_threshold_v": threshold,
              "logic_threshold_convention": "0.5 * VDD reporting classifier; not a characterized device threshold"}
    if case["study"] == "write_margin":
        q_rising = bool(headers["target_q"])
        q_cross = _crossing_time(xs, signals["xcell.q"], threshold, start, stop, q_rising)
        qb_cross = _crossing_time(xs, signals["xcell.q_bar"], threshold, start, stop, not q_rising)
        result.update({"pulse_width_ns": headers["operation_width_ns"],
                       "write_succeeded": initial_write_succeeded and final_state == headers["target_q"],
                       "q_crossing_ns": q_cross, "qb_crossing_ns": qb_cross,
                       "write_completion_delay_ns": max(value for value in (q_cross, qb_cross) if value is not None)
                       - start if q_cross is not None and qb_cross is not None else None})
    elif case["study"] == "concurrent_2r1w":
        q_rising = bool(headers["target_q"])
        q_cross = _crossing_time(xs, signals["xcell.q"], threshold, start, stop, q_rising)
        qb_cross = _crossing_time(xs, signals["xcell.q_bar"], threshold, start, stop, not q_rising)
        result.update({"write_succeeded": initial_write_succeeded and final_state == headers["target_q"],
                       "q_crossing_ns": q_cross, "qb_crossing_ns": qb_cross,
                       "write_completion_delay_ns": max(value for value in (q_cross, qb_cross) if value is not None)
                       - start if q_cross is not None and qb_cross is not None else None})
    elif case["study"] == "both_read_disturb":
        q_before, qb_before = initial_q_v, initial_qb_v
        result.update({"q_read_min_v": min(_window_values(xs, signals["xcell.q"], start, stop)),
                       "q_read_max_v": max(_window_values(xs, signals["xcell.q"], start, stop)),
                       "qb_read_min_v": min(_window_values(xs, signals["xcell.q_bar"], start, stop)),
                       "qb_read_max_v": max(_window_values(xs, signals["xcell.q_bar"], start, stop)),
                       "q_deviation_from_pre_read_max_v": max(abs(value - q_before)
                                                              for value in _window_values(xs, signals["xcell.q"], start, stop)),
                       "qb_deviation_from_pre_read_max_v": max(abs(value - qb_before)
                                                               for value in _window_values(xs, signals["xcell.q_bar"], start, stop)),
                       "bitline_end_v": {name: _interpolate(xs, signals[name], stop)
                                         for name in ("bl1", "br1", "bl2", "br2")},
                       "both_read_ports_enabled": True,
                       "state_retained": initial_write_succeeded and final_state == headers["initial_q"]})
    return result


def _analyze_snm_curve(case):
    table = parse_wrdata(case["data"], minimum_columns=2)
    sweep = _column(table, table["header"][0], 0)
    output = _column(table, table["header"][1], 1)
    if case["deck_metadata"]["curve"] == "q":
        points = list(zip(output, sweep))
    else:
        points = list(zip(sweep, output))
    case["curve_points"] = points
    case["curve_ref"] = ref(case["data"])
    return case


def _build_summary(cases, pvt_points):
    by_pvt = {name: {"pvt": dict(PVT_POINTS[name]), "hold_snm": None,
                     "both_read_disturb": [], "write_margin": [], "concurrent_2r1w": []}
              for name, _ in pvt_points}
    curves = {}
    failures = []
    for case in cases:
        if case.get("status") != "executed":
            failures.append({"case_id": case["case_id"], "status": case.get("status"),
                             "error": case.get("execution_error") or case.get("measurement_error")})
            continue
        try:
            if case["study"] == "hold_snm_vtc":
                _analyze_snm_curve(case)
                curves.setdefault(case["pvt"], {})[case["deck_metadata"]["curve"]] = case["curve_points"]
            else:
                case["pvt_vdd_v"] = PVT_POINTS[case["pvt"]]["vdd_v"]
                measurement = _analyze_transient(case, parse_wrdata(case["data"], minimum_columns=2))
                by_pvt[case["pvt"]][case["study"]].append(measurement)
                case["measurements"] = measurement
                if not measurement["initial_write_succeeded"]:
                    failures.append({"case_id": case["case_id"], "status": "initialization_failed",
                                     "initial_logic_state_q": measurement["initial_logic_state_q"],
                                     "expected_initial_q": measurement["initial_q"]})
                elif case["study"] == "both_read_disturb" and not measurement["state_retained"]:
                    failures.append({"case_id": case["case_id"], "status": "state_not_retained_under_both_reads"})
                elif case["study"] == "concurrent_2r1w" and not measurement["write_succeeded"]:
                    failures.append({"case_id": case["case_id"], "status": "concurrent_write_failed"})
        except (OSError, ValueError, KeyError, IndexError) as exc:
            case["status"] = "measurement_failed"
            case["measurement_error"] = str(exc)
            failures.append({"case_id": case["case_id"], "status": case["status"], "error": str(exc)})

    for pvt_name, curve_set in curves.items():
        try:
            if set(curve_set) != {"q", "qb"}:
                raise ValueError("both storage-inverter VTC curves are required")
            by_pvt[pvt_name]["hold_snm"] = extract_butterfly_snm(
                curve_set["q"], curve_set["qb"], PVT_POINTS[pvt_name]["vdd_v"])
        except ValueError as exc:
            by_pvt[pvt_name]["hold_snm"] = {"status": "extraction_failed", "error": str(exc)}
            failures.append({"case_id": pvt_name + "_hold_snm", "status": "measurement_failed", "error": str(exc)})

    for pvt_name, item in by_pvt.items():
        grouped = {}
        for measurement in item["write_margin"]:
            key = (measurement["initial_q"], measurement["target_q"],
                   tuple(measurement["read_ports_enabled"]))
            grouped.setdefault(key, []).append(measurement)
        summaries = []
        for key, samples in sorted(grouped.items()):
            samples.sort(key=lambda measurement: measurement["pulse_width_ns"])
            sweep = summarize_write_sweep([sample["pulse_width_ns"] for sample in samples],
                                          [sample["write_succeeded"] for sample in samples])
            summaries.append({"initial_q": key[0], "target_q": key[1],
                              "read_enabled": bool(key[2]), "read_ports_enabled": list(key[2]), **sweep,
                              "sampled_cases": samples})
        item["write_margin_sweeps"] = summaries
    return {"schema": "ics55-native-pbitcell-study-results/1",
            "scope": "standalone generated 0RW/1W/2R pbitcell; not macro/array/signoff",
            "pvt_results": by_pvt, "failures": failures,
            "measurement_limitations": [
                "ideal write voltage sources and ideal wordline sources do not represent peripheral drivers",
                "read bitlines use explicit 5 fF capacitors and a simple precharge switch, not extracted array wiring",
                "sampled write pulse widths are observations only; no interpolation, continuous margin, or binary-search assumption",
                "no DRC, LVS, PEX, STA, or signoff claim is made",
            ]}


def run_study(out_dir, *, pdk_root=None, corners=DEFAULT_CORNERS, ngspice="ngspice", timeout_s=600):
    out_dir = Path(out_dir).expanduser().resolve()
    expected_runs = Path(os.environ.get("ICS55_WORK", str(Path.cwd() / "ics55_work"))) / "runs"
    expected_runs = expected_runs.resolve()
    if expected_runs not in out_dir.parents:
        raise ValueError("study output must be a new child of " + str(expected_runs))
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("study output directory must be empty to preserve prior evidence: " + str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    pvt_points = select_pvt_points(corners)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be a finite positive number")

    pdk = _pdk_root(pdk_root)
    model = _model_path(pdk)
    dependencies_before = model_dependencies(model)
    mc_audit = audit_monte_carlo(dependencies_before)
    config_path = native.CONFIG_PATH
    input_dir = out_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    environment_keys = ("ICS55_PDK_ROOT", "ICSPROUT55_PDK_ROOT", "SPICE_MODEL_DIR")
    previous_environment = {key: os.environ.get(key) for key in environment_keys}
    os.environ["ICS55_PDK_ROOT"] = str(pdk)
    os.environ["ICSPROUT55_PDK_ROOT"] = str(pdk)
    os.environ["SPICE_MODEL_DIR"] = str(pdk / "libs.tech/spice/foundry/ngspice")
    try:
        cell_manifest = native.generate_native_pbitcell(input_dir / "generated_cell", config_path)
        cell_spice = Path(cell_manifest["simulation_netlist"]["path"])
        geometry = _generate_layout(input_dir / "standalone_layout", config_path)
    finally:
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    cell_ref = ref(cell_spice)
    geometry_path = input_dir / "standalone_layout" / "geometry.json"
    geometry_path.write_text(json.dumps(geometry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    cases = _build_case_specs(out_dir, cell_spice, model, pvt_points)
    tool = _tool_identity(ngspice)
    sources_before = _source_refs(config_path)
    before = {"schema": "ics55-native-cell-study-before/1",
              "model_dependencies": dependencies_before, "tool": tool,
              "sources": sources_before, "generated_cell": cell_ref,
              "pvt_points": [{"name": name, **config} for name, config in pvt_points],
              "monte_carlo_audit": mc_audit}
    (out_dir / "model-dependencies-before.json").write_text(
        json.dumps(before, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for case in cases:
        case["pvt_vdd_v"] = PVT_POINTS[case["pvt"]]["vdd_v"]
        _execute_case(case, tool, timeout_s)

    dependencies_after = model_dependencies(model)
    tool_unchanged = digest(tool["path"]) == tool["sha256"]
    sources_after = _source_refs(config_path)
    models_unchanged = dependencies_before == dependencies_after
    sources_unchanged = sources_before == sources_after
    summary = _build_summary(cases, pvt_points)
    if geometry["status"] != "pin_access_geometry_observed":
        summary["failures"].append({"case_id": "standalone_layout_geometry",
                                    "status": "geometry_issue",
                                    "detail": "required pin access, orientation, span, or bbox checks failed"})
    summary["monte_carlo"] = mc_audit
    summary["geometry"] = geometry
    summary["source_binding"] = {"generated_cell": cell_ref,
                                 "generated_cell_manifest": ref(input_dir / "generated_cell" / "native_2r1w_pbitcell.json"),
                                 "layout_geometry": ref(geometry_path),
                                 "layout_gds": geometry["gds"],
                                 "model_dependencies_before": dependencies_before,
                                 "model_dependencies_after": dependencies_after,
                                 "sources_before": sources_before, "sources_after": sources_after,
                                 "tool_before": tool,
                                 "post_execution_unchanged": {"models": models_unchanged,
                                                               "sources": sources_unchanged,
                                                               "tool": tool_unchanged}}
    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {"schema": "ics55-native-pbitcell-study-manifest/1",
                "status": "complete" if not summary["failures"] and models_unchanged
                and sources_unchanged and tool_unchanged else "incomplete_or_failed",
                "out_dir": str(out_dir), "pdk_root": str(pdk), "model_library": ref(model),
                "model_dependencies_before": dependencies_before,
                "model_dependencies_after": dependencies_after,
                "monte_carlo_audit": mc_audit,
                "generated_cell": cell_ref,
                "generated_cell_manifest": ref(input_dir / "generated_cell" / "native_2r1w_pbitcell.json"),
                "layout_geometry": ref(geometry_path), "layout_gds": geometry["gds"],
                "source_refs_before": sources_before, "source_refs_after": sources_after,
                "tool": tool, "tool_hash_unchanged_after_run": tool_unchanged,
                "model_hashes_unchanged_after_run": models_unchanged,
                "source_hashes_unchanged_after_run": sources_unchanged,
                "results": ref(results_path),
                "cases": [{"case_id": case["case_id"], "study": case["study"], "pvt": case["pvt"],
                           "status": case["status"], "deck": ref(case["deck"]),
                           "log": case.get("execution", {}).get("log"),
                           "raw_data": case.get("raw_data"),
                           "error": case.get("execution_error") or case.get("measurement_error")}
                          for case in cases],
                "signoff": False}
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    all_executed = all(case["status"] == "executed" or case["status"] == "measurement_failed" for case in cases)
    print(str(results_path))
    print(str(manifest_path))
    return 0 if all_executed and not summary["failures"] and models_unchanged and sources_unchanged and tool_unchanged else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="new or empty directory beneath ics55_work/runs/")
    parser.add_argument("--pdk-root", type=Path, default=None)
    parser.add_argument("--corners", default=",".join(DEFAULT_CORNERS),
                        help="comma-separated named PVT points: " + ",".join(PVT_POINTS))
    parser.add_argument("--ngspice", default="ngspice")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    try:
        corners = tuple(part.strip() for part in args.corners.split(",") if part.strip())
        return run_study(args.out_dir, pdk_root=args.pdk_root, corners=corners,
                         ngspice=args.ngspice, timeout_s=args.timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("native pbitcell study: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
