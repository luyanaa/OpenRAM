# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""Readers for the KLayout verification result files.

These helpers are technology independent: they parse the two report formats
KLayout writes, so the caller can decide on a verdict from the *report* rather
than from a deck's log output.

  * DRC:  ``.lyrdb``/``<cell>.drc.report``  (XML report database)
  * LVS:  ``.lvsdb``/``<cell>.lvs.report``  (KLayout netlist database)

The LVS database is not XML.  Its comparison result is the trailing ``Z(...)``
section::

    Z(
     X(<layout circuit> <reference circuit> <marker>
      Z(
       N(<layout net> <reference net> <marker>)
       P(<layout pin> <reference pin> <marker>)
       D(<layout device> <reference device> <marker>)
      )
     )
    )

Markers ``1`` and ``W`` are KLayout's Match and MatchWithWarning statuses.
The latter includes matched symmetric nets with different internal names;
it is not a topology mismatch. Other markers denote failure or a skipped
comparison. A database without ``Z(...)`` has not been compared.
"""

import re
import xml.etree.ElementTree as ET

_MATCHED_MARKERS = ("1", "W")


class ReportError(ValueError):
    """The report cannot be read reliably."""


def drc_violations(report_file):
    """Return (number_of_violations, {category: count}) of a KLayout DRC report."""

    try:
        root = ET.parse(report_file).getroot()
    except (ET.ParseError, OSError) as error:
        raise ReportError("cannot read DRC report {}: {}".format(report_file, error))

    items = root.find("items")
    if items is None:
        raise ReportError("DRC report {} has no <items> section".format(report_file))

    categories = {}
    count = 0
    for item in items.findall("item"):
        count += 1
        category = item.findtext("category") or "<unnamed>"
        categories[category] = categories.get(category, 0) + 1
    return (count, categories)


def lvs_verdict(report_file):
    """Return (compared, ok, circuits) of a KLayout LVS report.

    ``compared`` is False when the report contains no comparison at all.
    ``circuits`` is a list of (layout_circuit, reference_circuit, marker,
    mismatching_objects) tuples.
    """

    try:
        with open(report_file, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as error:
        raise ReportError("cannot read LVS report {}: {}".format(report_file, error))

    start = text.rfind("\nZ(")
    if start < 0:
        return (False, False, [])

    section = text[start:]

    # The comparison section holds one entry per circuit.  Top level entries are
    # indented by exactly one space; nested instance correspondences ("X(1 1 1)")
    # are indented deeper and must not be mistaken for circuits.
    circuits = []
    entries = list(re.finditer(r"\n X\(([^\n]*)\n", section))
    for index, entry in enumerate(entries):
        fields = entry.group(1).split()
        if len(fields) < 3:
            raise ReportError("unexpected LVS circuit entry {!r}".format(entry.group(1)))
        # A comparison that could not run carries an explanatory B('...')
        # message after the marker, which may span lines.
        (layout_circuit, reference_circuit, marker) = fields[:3]

        body_end = entries[index + 1].start() if index + 1 < len(entries) else len(section)
        body = section[entry.end():body_end]

        mismatches = []
        for obj in re.finditer(r"\n\s+([NPDE])\(([^\n]*)\)", body):
            kind = obj.group(1)
            obj_fields = obj.group(2).split()
            if len(obj_fields) != 3:
                raise ReportError("unexpected LVS {} entry {!r}".format(kind, obj.group(0)))
            (layout_obj, reference_obj, obj_marker) = obj_fields
            if obj_marker not in _MATCHED_MARKERS:
                mismatches.append((kind, layout_obj, reference_obj, obj_marker))
        circuits.append((layout_circuit, reference_circuit, marker, mismatches))

    if not circuits:
        return (False, False, [])

    ok = all(marker in _MATCHED_MARKERS and not mismatches
             for _, _, marker, mismatches in circuits)
    return (True, ok, circuits)


def lvs_summary(report_file):
    """Return a one-line human readable summary of an LVS report."""

    (compared, ok, circuits) = lvs_verdict(report_file)
    if not compared:
        return "unknown (no comparison in report)"
    if ok:
        return "match ({} circuit(s))".format(len(circuits))
    details = []
    for layout_circuit, reference_circuit, marker, mismatches in circuits:
        if marker not in _MATCHED_MARKERS:
            details.append("{} vs {}: {} mismatch".format(layout_circuit,
                                                          reference_circuit,
                                                          "circuit" if marker == "X" else marker))
        elif mismatches:
            kinds = sorted({kind for kind, _, _, _ in mismatches})
            details.append("{} vs {}: {} {}(s)".format(layout_circuit, reference_circuit,
                                                       len(mismatches), "/".join(kinds)))
    return "mismatch ({})".format("; ".join(details))
