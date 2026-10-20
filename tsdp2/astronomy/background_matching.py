"""Robust relative sky planes from co-spatial, same-filter overlap pixels.

No tile histogram equalization or multiplicative photometric scaling is used.
An anchored component retains its reference image's absolute sky/gradient.
"""
from __future__ import annotations

import os
import numpy as np
from scipy.sparse import coo_matrix, vstack, diags
from scipy.sparse.linalg import lsqr

BACKGROUND_METHOD = "robust-overlap-v1"


def _sample_tiles(tiles, shape):
    # At most 32 MiB of float samples across all tiles (plus working masks).
    limit = max(1, min(200_000, 8_000_000 // max(1, len(tiles))))
    step = max(1, int(np.ceil(np.sqrt(np.prod(shape) / limit))))
    samples, boxes, coverage = [], [], []
    for tile in tiles:
        mm = None
        try:
            if isinstance(tile, (str, os.PathLike)):
                mm = np.memmap(tile, mode="r", dtype="float32", shape=shape)
                tile = mm
            sample = np.array(tile[::step, ::step], dtype=np.float32, copy=True)
            y, x = np.nonzero(np.isfinite(sample))
            coverage.append(len(x))
            if len(x):
                box = (int(y.min()), int(y.max()) + 1, int(x.min()), int(x.max()) + 1)
                y0, y1, x0, x1 = box
                samples.append(sample[y0:y1, x0:x1].copy())
                boxes.append(box)
            else:
                samples.append(None)
                boxes.append((0, 0, 0, 0))
        finally:
            if mm is not None:
                mm._mmap.close()
    return samples, boxes, coverage, step


def _overlap_cells(a, b, origin, step, shape):
    """Spatial medians suppress cosmic rays, star residuals and bad pixels."""
    good = np.isfinite(a) & np.isfinite(b)
    y, x = np.nonzero(good)
    if len(x) < 24:
        return None
    diff = a[good].astype(np.float64) - b[good].astype(np.float64)
    # Equal spatial cells prevent a crowded patch dominating the entire fit.
    cell_ids = (np.minimum(11, (y - y.min()) * 12 // max(1, np.ptp(y) + 1)) * 12
                + np.minimum(11, (x - x.min()) * 12 // max(1, np.ptp(x) + 1)))
    cells = []
    for cell in np.unique(cell_ids):
        keep = cell_ids == cell
        if keep.sum() < 3:
            continue
        cx = 2 * ((origin[1] + float(np.mean(x[keep]))) * step) / max(1, shape[1] - 1) - 1
        cy = 2 * ((origin[0] + float(np.mean(y[keep]))) * step) / max(1, shape[0] - 1) - 1
        cells.append((cx, cy, float(np.median(diff[keep]))))
    if len(cells) < 6:
        return None
    cells = np.array(cells)
    # Robustly reject whole contaminated cells, after removing the sky trend.
    design = np.column_stack((np.ones(len(cells)), cells[:, :2]))
    keep = np.ones(len(cells), dtype=bool)
    for _ in range(4):
        fit = np.linalg.lstsq(design[keep], cells[keep, 2], rcond=None)[0]
        residual = cells[:, 2] - design @ fit
        centre = np.median(residual[keep])
        sigma = max(1e-5, 1.4826 * np.median(np.abs(residual[keep] - centre)))
        proposed = np.abs(residual - centre) <= 4.5 * sigma
        if proposed.sum() < 6 or np.array_equal(proposed, keep):
            break
        keep = proposed
    return cells[keep]


def estimate_background_planes(tiles, shape=None, filter_code="", diagnostics=None):
    """Return additive (offset, x, y) planes in normalized output coordinates.

    Solve all overlap cells jointly with robust sparse least squares. Local
    tile coordinates and weak slope regularization keep narrow overlaps from
    producing extreme extrapolated planes. Disconnected sky is never forced
    to the median of a different pointing.
    """
    n = len(tiles)
    if shape is None and n:
        shape = tiles[0].shape
    shape = tuple(shape or (0, 0))
    if n < 2:
        return [(0.0, 0.0, 0.0)] * n
    samples, boxes, coverage, step = _sample_tiles(tiles, shape)
    centres, spans = [], []
    for y0, y1, x0, x1 in boxes:
        centres.append(((x0 + x1 - 1) * step / max(shape[1] - 1, 1) - 1,
                        (y0 + y1 - 1) * step / max(shape[0] - 1, 1) - 1))
        spans.append((max(step, (x1 - x0 - 1) * step) / max(shape[1] - 1, 1),
                      max(step, (y1 - y0 - 1) * step) / max(shape[0] - 1, 1)))
    centres, spans = np.array(centres), np.array(spans)
    rows, columns, values, targets = [], [], [], []
    adjacency = [set() for _ in tiles]
    edge_count = 0
    for i in range(n):
        if samples[i] is None:
            continue
        for j in range(i + 1, n):
            if samples[j] is None:
                continue
            bi, bj = boxes[i], boxes[j]
            y0, y1, x0, x1 = max(bi[0], bj[0]), min(bi[1], bj[1]), max(bi[2], bj[2]), min(bi[3], bj[3])
            if y1 <= y0 or x1 <= x0:
                continue
            a = samples[i][y0-bi[0]:y1-bi[0], x0-bi[2]:x1-bi[2]]
            b = samples[j][y0-bj[0]:y1-bj[0], x0-bj[2]:x1-bj[2]]
            cells = _overlap_cells(a, b, (y0, x0), step, shape)
            if cells is None:
                continue
            edge_count += 1
            adjacency[i].add(j)
            adjacency[j].add(i)
            for x, y, difference in cells:
                r = len(targets)
                for idx, sign in ((i, 1), (j, -1)):
                    basis = (1.0, (x - centres[idx, 0]) / spans[idx, 0],
                             (y - centres[idx, 1]) / spans[idx, 1])
                    rows.extend((r, r, r))
                    columns.extend((3*idx, 3*idx+1, 3*idx+2))
                    values.extend(sign * v for v in basis)
                targets.append(-difference)
    seen, anchors, components = set(), [], []
    for i in range(n):
        if i in seen:
            continue
        pending, component = [i], []
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            component.append(node)
            pending.extend(adjacency[node] - seen)
        components.append(component)
        anchors.append(max(component, key=lambda k: (len(adjacency[k]), coverage[k], -k)))
    solved = np.zeros(3*n)
    residual_before = residual_after = 0.0
    if targets:
        matrix = coo_matrix((values, (rows, columns)), shape=(len(targets), 3*n)).tocsr()
        # Exact reference anchors; no bias from a soft global zero constraint.
        active = np.ones(3*n, dtype=bool)
        for anchor in anchors:
            active[3*anchor:3*anchor+3] = False
        matrix = matrix[:, active]
        target = np.array(targets)
        slopes = np.tile((0.0, 0.025, 0.025), n)[active]
        regularizer = diags(slopes, format="csr")
        weights = np.ones(len(target))
        for _ in range(4):
            weighted = matrix.multiply(np.sqrt(weights)[:, None])
            system = vstack((weighted, regularizer), format="csr")
            rhs = np.concatenate((target * np.sqrt(weights), np.zeros(active.sum())))
            solution = lsqr(system, rhs, atol=1e-9, btol=1e-9, iter_lim=max(200, n*12))[0]
            residual = matrix @ solution - target
            median = np.median(residual)
            sigma = max(1e-5, 1.4826 * np.median(np.abs(residual - median)))
            weights = np.minimum(1.0, 2.5 * sigma / np.maximum(np.abs(residual - median), 1e-10))
        solved[active] = solution
        residual_before = float(np.median(np.abs(target)))
        residual_after = float(np.median(np.abs(residual)))
    corrections = []
    for i, (a, bx, by) in enumerate(solved.reshape(n, 3)):
        bx, by = bx / spans[i, 0], by / spans[i, 1]
        corrections.append((float(a - bx*centres[i, 0] - by*centres[i, 1]), float(bx), float(by)))
    report = dict(method=BACKGROUND_METHOD, tiles=n, overlap_pairs=edge_count,
                  components=len(components), isolated_tiles=sum(not a for a in adjacency),
                  sample_step=step, median_overlap_before=residual_before,
                  median_overlap_after=residual_after)
    if diagnostics is not None:
        diagnostics.update(report)
    print(f"[Mosaic] {filter_code}: robust overlap normalization: {edge_count} pairs, "
          f"{len(components)} component(s); median cell difference "
          f"{residual_before:.4g} -> {residual_after:.4g}; "
          f"{report['isolated_tiles']} tile(s) without measured overlap", flush=True)
    return corrections


def apply_background_plane(array, correction, chunk_rows=256):
    """In-place additive correction, retaining NaNs and bounding work arrays."""
    a, bx, by = correction
    if not any(correction):
        return
    h, w = array.shape
    x = (2 * np.arange(w, dtype=np.float32) / max(1, w-1) - 1)[None, :]
    for y0 in range(0, h, chunk_rows):
        y1 = min(h, y0+chunk_rows)
        y = (2 * np.arange(y0, y1, dtype=np.float32) / max(1, h-1) - 1)[:, None]
        array[y0:y1] += np.float32(a) + np.float32(bx)*x + np.float32(by)*y


def estimate_source_masked_sky_planes(tiles, shape=None):
    """Independent sky estimates for the optional cosmetic normalization.

    Fit a low-order surface to spatial sky medians, iteratively excluding bright
    source cells. This can bridge detector gaps without pretending those are
    overlaps. It is a display/background-subtraction model, not photometric
    calibration; extended emission filling a detector cannot be distinguished
    from illumination without an external sky reference.
    """
    if not tiles:
        return []
    shape = tuple(shape if shape is not None else tiles[0].shape)
    samples, boxes, _coverage, step = _sample_tiles(tiles, shape)
    fitted = []
    for sample, box in zip(samples, boxes):
        if sample is None:
            fitted.append(None)
            continue
        sh, sw = sample.shape
        cells = []
        for ys in np.array_split(np.arange(sh), min(10, max(1, sh//3))):
            for xs in np.array_split(np.arange(sw), min(10, max(1, sw//3))):
                patch = sample[np.ix_(ys, xs)]
                good = np.isfinite(patch)
                if good.sum() < 6:
                    continue
                py, px = np.nonzero(good)
                values = patch[good].astype(np.float64)
                # Median cell sky remains stable with sparse stellar PSFs.
                centre = float(np.median(values))
                noise = max(1e-5, 1.4826*float(np.median(np.abs(values-centre))))
                for _ in range(3):
                    keep = np.abs(values-centre) < 2.5*noise
                    if keep.sum() < 6:
                        break
                    centre = float(np.median(values[keep]))
                    noise = max(1e-5, 1.4826*float(np.median(np.abs(values[keep]-centre))))
                x = 2*(box[2]+xs[0]+float(np.mean(px)))*step/max(shape[1]-1,1)-1
                y = 2*(box[0]+ys[0]+float(np.mean(py)))*step/max(shape[0]-1,1)-1
                cells.append((x,y,centre,noise))
        if len(cells) < 12:
            fitted.append((float(np.median(np.array(cells)[:,2])),0.0,0.0) if len(cells)>=3 else None)
            continue
        cells = np.array(cells)
        centre = np.mean(cells[:, :2], axis=0)
        spread = np.maximum(np.ptp(cells[:, :2], axis=0)/2, 1e-4)
        design = np.column_stack((np.ones(len(cells)), (cells[:, :2]-centre)/spread))
        keep = np.ones(len(cells), dtype=bool)
        coeff = np.zeros(3)
        for _ in range(8):
            coeff = np.linalg.lstsq(design[keep],cells[keep,2],rcond=None)[0]
            residual = cells[:,2] - design@coeff
            median = np.median(residual[keep])
            sigma = max(1e-5,1.4826*np.median(np.abs(residual[keep]-median)))
            # Bright galaxy/halo cells are excluded rather than subtracted as sky.
            proposed = (residual-median < 2*sigma) & (residual-median > -3.5*sigma)
            if proposed.sum() < max(10, len(cells)//3) or np.array_equal(proposed,keep):
                break
            keep = proposed
        if np.linalg.cond(design[keep]) > 30:
            fitted.append((float(np.median(cells[keep,2])),0.0,0.0))
            continue
        bx,by=coeff[1:]/spread
        fitted.append((float(coeff[0]-bx*centre[0]-by*centre[1]),float(bx),float(by)))
    baseline = float(np.median([v[0] for v in fitted if v is not None])) if any(v is not None for v in fitted) else 0.0
    return [(baseline-v[0],-v[1],-v[2]) if v is not None else (0.0,0.0,0.0) for v in fitted]
