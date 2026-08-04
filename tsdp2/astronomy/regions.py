from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER

def _normalise_mosaic_downscale(value, default=1.0):
    """Parse a mosaic output scale factor.

    1.0 = full native output scale.
    0.5 = half width and half height, about 1/4 pixels.
    Values above 1.0 are rejected because this option is for downscaling,
    not artificial upscaling.
    """
    if value is None:
        return float(default)
    raw = str(value).strip().replace(",", ".")
    if raw == "":
        return float(default)
    try:
        scale = float(raw)
    except EXPECTED_OPERATION_ERRORS as exc:
        raise ValueError(f"Invalid mosaic scale '{value}'. Use a number like 1, 0.75, 0.5, or 0.25.") from exc
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Mosaic scale must be greater than 0.")
    if scale > 1.0:
        raise ValueError("Mosaic scale must be <= 1.0. Use 1.0 for full resolution, 0.5 for half resolution.")
    if scale < 0.02:
        raise ValueError("Mosaic scale is too tiny. Use 0.02 or higher.")
    return float(scale)


def _mosaic_filter_scale(filter_code, global_scale=1.0, filter_scales=None):
    """Return the active downscale for one filter code."""
    filter_scales = filter_scales or {}
    raw = filter_scales.get(filter_code, "")
    if str(raw).strip():
        return _normalise_mosaic_downscale(raw, default=global_scale)
    return _normalise_mosaic_downscale(global_scale, default=1.0)


def _downscale_celestial_wcs(wcs_out, shape_out, scale):
    """Return (wcs_scaled, shape_scaled) for an output mosaic scale.

    This does not modify the input FITS files. It keeps the same sky coverage
    but makes the output pixel grid smaller. A scale of 0.5 halves NAXIS1 and
    NAXIS2 and doubles the angular pixel size.
    """
    scale = _normalise_mosaic_downscale(scale)
    if abs(scale - 1.0) < 1e-9:
        return wcs_out, tuple(int(x) for x in shape_out)

    h, w = int(shape_out[0]), int(shape_out[1])
    new_h = max(1, int(round(h * scale)))
    new_w = max(1, int(round(w * scale)))

    try:
        w2 = wcs_out.deepcopy()
    except EXPECTED_OPERATION_ERRORS:
        import copy as _copy
        w2 = _copy.deepcopy(wcs_out)

    # FITS WCS CRPIX is 1-based. This maps new pixel centers onto the
    # corresponding old pixel centers while preserving the same sky footprint.
    try:
        w2.wcs.crpix = (np.asarray(w2.wcs.crpix, dtype=float) - 0.5) * scale + 0.5
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)

    # Enlarged sky pixel size. With CD matrix use CD/scale. Otherwise CDELT/scale.
    try:
        if hasattr(w2.wcs, "has_cd") and w2.wcs.has_cd():
            w2.wcs.cd = np.asarray(w2.wcs.cd, dtype=float) / scale
        else:
            w2.wcs.cdelt = np.asarray(w2.wcs.cdelt, dtype=float) / scale
    except EXPECTED_OPERATION_ERRORS:
        try:
            w2.wcs.cdelt = np.asarray(w2.wcs.cdelt, dtype=float) / scale
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

    try:
        w2.array_shape = (new_h, new_w)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    try:
        w2.pixel_shape = (new_w, new_h)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    return w2, (new_h, new_w)


def _rotated_rectangle_corners(cx, cy, width, height, angle_deg=0.0):
    """Return four clockwise screen/pixel corners for a rotated rectangle."""
    half_w = max(0.0, float(width)) / 2.0
    half_h = max(0.0, float(height)) / 2.0
    local = np.asarray([
        [-half_w, -half_h], [half_w, -half_h],
        [half_w, half_h], [-half_w, half_h],
    ], dtype=float)
    angle = np.deg2rad(float(angle_deg))
    rotation = np.asarray([
        [np.cos(angle), -np.sin(angle)],
        [np.sin(angle), np.cos(angle)],
    ], dtype=float)
    return local @ rotation.T + np.asarray([float(cx), float(cy)])


_RESIZE_HANDLE_SIGNS = {
    "nw": (-1, -1), "n": (0, -1), "ne": (1, -1),
    "e": (1, 0), "se": (1, 1), "s": (0, 1),
    "sw": (-1, 1), "w": (-1, 0),
}


def _rotated_rectangle_handles(selection):
    """Return Photoshop-style corner and side handle positions."""
    corners = _rotated_rectangle_corners(
        selection["cx"], selection["cy"], selection["width"],
        selection["height"], selection.get("angle_deg", 0.0))
    return {
        "nw": corners[0],
        "n": (corners[0] + corners[1]) / 2.0,
        "ne": corners[1],
        "e": (corners[1] + corners[2]) / 2.0,
        "se": corners[2],
        "s": (corners[2] + corners[3]) / 2.0,
        "sw": corners[3],
        "w": (corners[3] + corners[0]) / 2.0,
    }


def _resize_rotated_rectangle(selection, handle_name, pointer_x, pointer_y,
                              keep_aspect=False, min_size=8.0):
    """Resize a rotated rectangle while keeping the opposite handle fixed."""
    if handle_name not in _RESIZE_HANDLE_SIGNS:
        return dict(selection)
    result = dict(selection)
    sx, sy = _RESIZE_HANDLE_SIGNS[handle_name]
    width = max(float(selection["width"]), float(min_size))
    height = max(float(selection["height"]), float(min_size))
    center = np.asarray([selection["cx"], selection["cy"]], dtype=float)
    angle = np.deg2rad(float(selection.get("angle_deg", 0.0)))
    rotation = np.asarray([
        [np.cos(angle), -np.sin(angle)],
        [np.sin(angle), np.cos(angle)],
    ], dtype=float)
    pointer_local = (
        np.asarray([float(pointer_x), float(pointer_y)]) - center) @ rotation

    fixed_x = -sx * width / 2.0 if sx else 0.0
    fixed_y = -sy * height / 2.0 if sy else 0.0
    new_width = (max(float(min_size), sx * (pointer_local[0] - fixed_x))
                 if sx else width)
    new_height = (max(float(min_size), sy * (pointer_local[1] - fixed_y))
                  if sy else height)

    if keep_aspect and sx and sy:
        scale_x = new_width / max(width, 1e-9)
        scale_y = new_height / max(height, 1e-9)
        scale = scale_x if abs(scale_x - 1.0) >= abs(scale_y - 1.0) else scale_y
        scale = max(scale, float(min_size) / width, float(min_size) / height)
        new_width = width * scale
        new_height = height * scale
        dragged_x = fixed_x + sx * new_width
        dragged_y = fixed_y + sy * new_height
    else:
        dragged_x = fixed_x + sx * new_width if sx else 0.0
        dragged_y = fixed_y + sy * new_height if sy else 0.0

    center_local = np.asarray([
        (fixed_x + dragged_x) / 2.0 if sx else 0.0,
        (fixed_y + dragged_y) / 2.0 if sy else 0.0,
    ])
    new_center = center + center_local @ rotation.T
    result.update({
        "cx": float(new_center[0]), "cy": float(new_center[1]),
        "width": float(new_width), "height": float(new_height),
    })
    return result


def _crop_wcs_to_sky_region(wcs_out, shape_out, sky_region):
    """Crop, and optionally rotate, an output WCS to a selected sky rectangle."""
    if not sky_region:
        return wcs_out, tuple(int(x) for x in shape_out), None
    corners = np.asarray(sky_region.get("world_corners") or [], dtype=float)
    if corners.shape != (4, 2) or not np.all(np.isfinite(corners)):
        raise ValueError("Interactive mosaic area has invalid sky coordinates.")
    h, w = int(shape_out[0]), int(shape_out[1])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        px, py = wcs_out.all_world2pix(corners[:, 0], corners[:, 1], 0)
    finite = np.isfinite(px) & np.isfinite(py)
    if int(np.count_nonzero(finite)) < 2:
        raise ValueError("The selected sky area cannot be projected onto this filter.")
    requested_rotation = float(sky_region.get("rotation_deg", 0.0) or 0.0)
    if abs(requested_rotation) > 1e-7 and int(np.count_nonzero(finite)) == 4:
        points = np.column_stack((px, py)).astype(float)
        old_bbox_overlaps = not (
            np.nanmax(points[:, 0]) < 0 or np.nanmin(points[:, 0]) >= w or
            np.nanmax(points[:, 1]) < 0 or np.nanmin(points[:, 1]) >= h)
        if not old_bbox_overlaps:
            raise ValueError(
                "The rotated sky area does not overlap this filter's mosaic footprint.")

        # Align the new output x axis to the first selected rectangle edge.
        edge = points[1] - points[0]
        edge_length = float(np.hypot(edge[0], edge[1]))
        if not np.isfinite(edge_length) or edge_length < 1e-6:
            raise ValueError("The rotated mosaic selection is too small.")
        angle = float(np.arctan2(edge[1], edge[0]))
        rotation = np.asarray([
            [np.cos(angle), -np.sin(angle)],
            [np.sin(angle), np.cos(angle)],
        ], dtype=float)
        center = np.mean(points, axis=0)
        aligned = (points - center) @ rotation
        q_min = np.floor(np.nanmin(aligned, axis=0))
        q_max = np.ceil(np.nanmax(aligned, axis=0))
        new_w = int(q_max[0] - q_min[0]) + 1
        new_h = int(q_max[1] - q_min[1]) + 1
        if new_w < 2 or new_h < 2:
            raise ValueError("The rotated mosaic selection is too small.")

        try:
            w2 = wcs_out.deepcopy()
        except EXPECTED_OPERATION_ERRORS:
            import copy as _copy
            w2 = _copy.deepcopy(wcs_out)
        old_reference = np.asarray(wcs_out.wcs.crpix, dtype=float) - 1.0
        new_reference = (old_reference - center) @ rotation - q_min
        old_matrix = np.asarray(wcs_out.pixel_scale_matrix, dtype=float)
        new_matrix = old_matrix @ rotation
        w2.wcs.crpix = new_reference + 1.0
        w2.wcs.cd = new_matrix
        try:
            w2.wcs.set()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            w2.array_shape = (new_h, new_w)
            w2.pixel_shape = (new_w, new_h)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        return w2, (new_h, new_w), (
            int(q_min[1]), int(q_max[1]) + 1,
            int(q_min[0]), int(q_max[0]) + 1)

    x0 = max(0, int(np.floor(np.nanmin(px[finite]))))
    x1 = min(w, int(np.ceil(np.nanmax(px[finite]))) + 1)
    y0 = max(0, int(np.floor(np.nanmin(py[finite]))))
    y1 = min(h, int(np.ceil(np.nanmax(py[finite]))) + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError("The selected sky area does not overlap this filter's mosaic footprint.")
    try:
        w2 = wcs_out.deepcopy()
    except EXPECTED_OPERATION_ERRORS:
        import copy as _copy
        w2 = _copy.deepcopy(wcs_out)
    w2.wcs.crpix = np.asarray(w2.wcs.crpix, dtype=float) - [x0, y0]
    try:
        w2.array_shape = (y1 - y0, x1 - x0)
        w2.pixel_shape = (x1 - x0, y1 - y0)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    return w2, (y1 - y0, x1 - x0), (y0, y1, x0, x1)

__all__ = [name for name in globals() if not name.startswith('__')]
