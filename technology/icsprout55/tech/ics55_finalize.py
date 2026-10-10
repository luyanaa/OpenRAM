# See LICENSE for licensing information.
#
# ICsprout55 GDS finalization: implant (NP/PP) and N-well legalization.
"""Derive mask-legal NP/PP implants and N-well for an OpenRAM ICsprout55 GDS.

ICsprout55 requires NP and PP to tile the layout (butted, never overlapping),
to cover all contacted poly, and to keep fixed distances from gates.  OpenRAM
draws one implant rectangle per transistor/contact, which cannot satisfy these
rules hierarchically.  This pass therefore uses OpenRAM's NP/PP shapes only as
*type hints* and regenerates the implants flat at the top cell:

* PP covers the N-well, NP covers everything else inside the macro boundary;
* well taps (N+ in N-well, P+ outside N-well) get opposite-type islands;
* implant slivers narrower than the minimum width are absorbed;
* foundry standard cells (``lib_cells``) keep their own implants;
* N-well notches narrower than NW1_S_1 are filled when no N+ active is near.

The function needs the ``klayout`` Python package (pip install klayout).
"""

import klayout.db as kdb

# Layer numbers (layer, datatype)
AA = (2, 1)
NW = (9, 1)
PO = (41, 1)
NP = (52, 1)
PP = (53, 1)
BOUNDARY = (351, 12)

# Rules in micron (ICsprout55 DRM / Calibre deck)
IMP_MIN_W = 0.18        # NP_W_1/PP_W_1, NP_S_1/PP_S_1
TAP_ENC = 0.08          # island enclosure of the tap active (NP_A_1 0.12um2)
NW_MIN_S = 0.47         # NW1_S_1
NW_NACT_S = 0.15        # NW to N+ active spacing (ACT_EN_1 equivalent)
POLY_ENC = 0.155        # NP_EN_3/PP_EN_3 implant enclosure of field poly
IMP_ENC_ACT = 0.125     # NP_EN_2/PP_EN_2 implant enclosure of active
GRID = 5                # dbu (5 nm)
HOLE_MAX = 0.20         # fill metal holes smaller than this (MET*_A_2 0.18)
# (layer, min area um2 with margin, spacing um)
# metal -> (via below, metal below), for same-net detection
LOWER = {(82, 1): ((91, 1), (81, 1)), (83, 1): ((92, 1), (82, 1)),
         (84, 1): ((93, 1), (83, 1)), (85, 1): ((94, 1), (84, 1))}
METAL_AREA = [((81, 1), 0.046, 0.12), ((82, 1), 0.056, 0.12),
              ((83, 1), 0.056, 0.12), ((84, 1), 0.056, 0.12),
              ((85, 1), 0.056, 0.12)]


def _region(layout, cell, lp, exclude=None):
    li = layout.find_layer(lp[0], lp[1])
    if li is None:
        return kdb.Region()
    it = cell.begin_shapes_rec(li)
    if exclude:
        it.unselect_cells([c for c in exclude])
    r = kdb.Region(it)
    r.merge()
    return r


def finalize_gds(in_gds, out_gds, top=None, lib_cells=(), report=print, margin=0.0):
    """margin grows the implant region beyond the cell bbox (leaf checks only)."""
    layout = kdb.Layout()
    layout.read(in_gds)
    dbu = layout.dbu
    um = lambda v: int(round(v / dbu))
    um2 = lambda v: int(round(v / dbu / dbu))
    top_cell = layout.cell(top) if top else layout.top_cell()

    lib_idx = [c.cell_index() for c in layout.each_cell() if c.name in lib_cells]
    # Boxes of library-cell instances (in top coordinates)
    lib_box = kdb.Region()
    bnd_li = layout.find_layer(BOUNDARY[0], BOUNDARY[1])
    for ci in lib_idx:
        cell_box = layout.cell(ci).bbox()
        if bnd_li is not None:
            bshapes = layout.cell(ci).shapes(bnd_li)
            if not bshapes.is_empty():
                cell_box = kdb.Region(bshapes).bbox()
        # enumerate placements of this cell
        it = kdb.RecursiveInstanceIterator(layout, top_cell)
        it.targets = [ci]
        while not it.at_end():
            trans = it.trans() * it.inst_trans()
            lib_box.insert(cell_box.transformed(trans))
            it.next()
    lib_box.merge()

    aa = _region(layout, top_cell, AA, lib_idx)
    po = _region(layout, top_cell, PO, lib_idx)
    nw = _region(layout, top_cell, NW)
    np_lib = _region(layout, top_cell, NP) & lib_box
    pp_lib = _region(layout, top_cell, PP) & lib_box

    # Transistor active is crossed by poly and typed by its well; active
    # without poly is a well tap of the opposite type.
    mos = aa.interacting(po)
    tap = aa.not_interacting(po)
    straddle = (mos.overlapping(nw) - mos.inside(nw)) + (tap.overlapping(nw) - tap.inside(nw))
    if not straddle.is_empty():
        report("ics55_finalize: {} active regions straddle the N-well edge".format(straddle.count()))
    nact = (mos - nw) + (tap & nw)
    pact = (mos & nw) + (tap - nw)

    # --- N-well: fill narrow notches/gaps where no N+ active is close.
    half = um(NW_MIN_S / 2) + 1
    nw_closed = nw.sized(half).sized(-half)
    added = nw_closed - nw
    n_mos = nact - nw
    keep_out = n_mos.sized(um(NW_NACT_S))
    added = added - keep_out
    # only add pieces that actually bridge (avoid creating new slivers)
    nw_new = (nw + added)
    nw_new.merge()

    bbox = top_cell.bbox().enlarged(um(margin), um(margin))
    boundary = kdb.Region(bbox)

    pp = (nw_new & boundary)
    np_ = boundary - nw_new
    ntap = tap & nw_new
    ptap = tap - nw_new
    np_isl = ntap.sized(um(TAP_ENC))
    pp_isl = ptap.sized(um(TAP_ENC))
    # make islands at least minimum width
    np_isl = np_isl.sized(um(IMP_MIN_W / 2)).sized(-um(IMP_MIN_W / 2))
    pp_isl = pp_isl.sized(um(IMP_MIN_W / 2)).sized(-um(IMP_MIN_W / 2))
    pp = (pp - np_isl) + pp_isl
    np_ = (np_ - pp_isl) + np_isl
    pp.merge()
    np_.merge()

    # Field poly that ends inside one implant must be enclosed by it
    # (NP_EN_3/PP_EN_3), unless it crosses into the other implant.  Grow the
    # implant over such poly ends where that does not encroach on
    # opposite-type active or poly.
    field_po = po - aa
    for _ in range(2):
        po_in_pp = field_po.inside(pp)
        po_in_np = field_po.inside(np_)
        grow_pp = po_in_pp.sized(um(POLY_ENC)) - pp
        grow_pp -= nact.sized(um(IMP_ENC_ACT)) + po_in_np.sized(um(POLY_ENC))
        grow_np = po_in_np.sized(um(POLY_ENC)) - np_
        grow_np -= pact.sized(um(IMP_ENC_ACT)) + po_in_pp.sized(um(POLY_ENC))
        if grow_pp.is_empty() and grow_np.is_empty():
            break
        pp = (pp + grow_pp) - grow_np
        np_ = (np_ + grow_np) - grow_pp
        pp.merge()
        np_.merge()

    # Absorb slivers: parts of one implant narrower than the minimum width
    # that contain no active of that type are given to the other implant.
    for _ in range(3):
        h = um(IMP_MIN_W / 2) - 1
        pp_sliver = pp - pp.sized(-h).sized(h)
        pp_sliver = pp_sliver.not_interacting(pact)
        np_sliver = np_ - np_.sized(-h).sized(h)
        np_sliver = np_sliver.not_interacting(nact)
        if pp_sliver.is_empty() and np_sliver.is_empty():
            break
        pp = (pp - pp_sliver) + np_sliver
        np_ = (np_ - np_sliver) + pp_sliver
        pp.merge()
        np_.merge()

    # Library cells keep their own implants.
    pp = (pp - lib_box) + pp_lib
    np_ = (np_ - lib_box) + np_lib
    pp.merge()
    np_.merge()

    # Remove all implants from generated cells, then write the flat ones.
    for lp in (NP, PP):
        li = layout.find_layer(lp[0], lp[1])
        if li is None:
            continue
        for c in layout.each_cell():
            if c.cell_index() in lib_idx:
                continue
            c.shapes(li).clear()
    nwl = layout.layer(NW[0], NW[1])
    top_cell.shapes(nwl).insert(added)

    # ---- metal clean-up (spatially indexed: each check only looks at the
    # shapes in a small window around the polygon being fixed)
    def local(li, box, extra=None):
        r = kdb.Region(top_cell.begin_shapes_rec_overlapping(li, box))
        if extra is not None:
            r += extra.interacting(kdb.Region(box))
        r.merge()
        return r

    n_holes = n_notch = n_area_fixed = n_area_left = 0
    for (lp, min_area, space) in METAL_AREA:
        li = layout.find_layer(lp[0], lp[1])
        if li is None:
            continue
        sp = um(space)
        metal = kdb.Region(top_cell.begin_shapes_rec(li))
        metal.merge()

        # Enclosed area (MET*_A_2 >= 0.18 um2) and slits: fill small holes
        # that contain no other metal (the fill is then on the same net).
        holes = metal.holes().with_area(None, um2(HOLE_MAX), False)
        fill = holes.not_overlapping(metal)
        n_holes += fill.count()
        top_cell.shapes(li).insert(fill)

        # Same-net notches (MET*_S_1 inside one polygon): close by half the
        # spacing; keep a piece only if it stays one spacing away from every
        # polygon but its own.
        h = sp // 2 + 1
        add = (metal + fill).sized(h).sized(-h) - (metal + fill)
        notch = kdb.Region()
        for piece in add.each():
            pr = kdb.Region(piece)
            loc = local(li, piece.bbox().enlarged(2 * sp, 2 * sp), notch)
            if loc.interacting(pr.sized(sp)).count() == 1:
                notch.insert(piece)
        n_notch += notch.count()
        top_cell.shapes(li).insert(notch)

        # Minimum area (MET1_A_1/METn_A_1): grow under-area polygons where
        # there is room, else bridge to a nearby polygon of the same net.
        metal = kdb.Region(top_cell.begin_shapes_rec(li))
        metal.merge()
        small = metal.with_area(None, um2(min_area), False)
        added_m = kdb.Region()
        vli = lli = None
        if lp in LOWER:
            vli = layout.find_layer(*LOWER[lp][0])
            lli = layout.find_layer(*LOWER[lp][1])
        for poly in small.each():
            box = poly.bbox()
            need = um2(min_area) - poly.area()
            win = box.enlarged(int(4 * um(0.56)), int(4 * um(0.56)))
            loc = local(li, win, added_m)
            ok = False
            dirs = ["x", "y"] if box.width() >= box.height() else ["y", "x"]
            for d in dirs:
                side = box.height() if d == "x" else box.width()
                ext = int(-(-need // side)) + 1
                ext = ((ext + GRID - 1) // GRID) * GRID
                half = ((ext + 1) // 2 + GRID - 1) // GRID * GRID
                cands = ([kdb.Box(box.left - half, box.bottom, box.right + half, box.top),
                          kdb.Box(box.left, box.bottom, box.right + ext, box.top),
                          kdb.Box(box.left - ext, box.bottom, box.right, box.top)] if d == "x" else
                         [kdb.Box(box.left, box.bottom - half, box.right, box.top + half),
                          kdb.Box(box.left, box.bottom, box.right, box.top + ext),
                          kdb.Box(box.left, box.bottom - ext, box.right, box.top)])
                for r in cands:
                    # only the pad itself may be within one spacing
                    if loc.interacting(kdb.Region(r).sized(sp)).count() == 1:
                        added_m.insert(r)
                        ok = True
                        break
                if ok:
                    break
            if not ok and vli is not None and lli is not None:
                vias = kdb.Region(top_cell.begin_shapes_rec_overlapping(vli, win))
                low = local(lli, win)
                me = kdb.Region(poly)
                mine = low.interacting(vias & me)
                for q in (loc - me).interacting(me.sized(sp)).each():
                    theirs = low.interacting(vias & kdb.Region(q))
                    if (mine & theirs).is_empty():
                        continue
                    qb = q.bbox()
                    if box.width() > box.height():
                        bridge = kdb.Box(min(box.left, qb.left), max(box.bottom, qb.bottom),
                                         max(box.right, qb.right), min(box.top, qb.top))
                    else:
                        bridge = kdb.Box(max(box.left, qb.left), min(box.bottom, qb.bottom),
                                         min(box.right, qb.right), max(box.top, qb.top))
                    if bridge.width() <= 0 or bridge.height() <= 0:
                        continue
                    if loc.interacting(kdb.Region(bridge).sized(sp)).count() <= 2:
                        added_m.insert(bridge)
                        ok = True
                        break
            if ok:
                n_area_fixed += 1
            else:
                n_area_left += 1
                report("ics55_finalize: cannot fix min area of {} polygon at {}".format(lp, box))
        top_cell.shapes(li).insert(added_m)

    top_cell.shapes(layout.layer(NP[0], NP[1])).insert(np_)
    top_cell.shapes(layout.layer(PP[0], PP[1])).insert(pp)
    layout.write(out_gds)
    return dict(nact=nact.count(), pact=pact.count(), ntap=ntap.count(),
                ptap=ptap.count(), nw_added=added.count(),
                area_fixed=n_area_fixed, area_left=n_area_left,
                holes_filled=n_holes, notches_filled=n_notch)


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    libs = []
    if "--lib" in args:
        i = args.index("--lib")
        libs = args[i + 1].split(",")
        del args[i:i + 2]
    print(finalize_gds(args[0], args[1], args[2] if len(args) > 2 else None, libs))
