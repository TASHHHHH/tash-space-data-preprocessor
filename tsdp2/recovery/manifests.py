from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.fits_io import _iter_requested_image_hdus, _mosaic_input_display_name, _parse_mosaic_hdu_ref
from tsdp2.astronomy.regions import _downscale_celestial_wcs, _normalise_mosaic_downscale
from tsdp2.output_paths import atomic_json_write

_RECOVERY_MANIFEST_NAME = "recovery_manifest.json"


def _atomic_json_write(path, payload):
    """Write a small recovery manifest without leaving a half-written JSON file."""
    atomic_json_write(path, payload)


def _update_recovery_manifest(tmp_dir, **changes):
    """Best-effort manifest update. Recovery metadata must never kill a run."""
    if not tmp_dir:
        return
    path = os.path.join(tmp_dir, _RECOVERY_MANIFEST_NAME)
    try:
        payload = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        payload.update(changes)
        payload["updated_unix"] = time.time()
        _atomic_json_write(path, payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("Recovery manifest update failed for %s: %s",
                       path, exc, exc_info=True)


def _stream_float32_to_fits(raw_path, shape, header, output_path,
                            progress_cb=None, cancel_cb=None,
                            mark_recovery=True, history_text=""):
    """Wrap a native float32 raw/memmap image in an atomic FITS file.

    Rows are converted to FITS big-endian order in bounded chunks. This avoids
    loading a multi-gigabyte mosaic into RAM and leaves only a `.recovering`
    file if a destination drive disconnects.
    """
    raw_path = os.path.abspath(os.fspath(raw_path))
    output_path = os.path.abspath(os.fspath(output_path))
    h, w = int(shape[0]), int(shape[1])
    if h <= 0 or w <= 0:
        raise ValueError(f"Invalid recovery shape: {w} x {h}")
    expected = h * w * 4
    actual = os.path.getsize(raw_path)
    if actual != expected:
        raise ValueError(
            f"Raw size does not match the selected geometry. File is {actual:,} bytes; "
            f"{w} x {h} float32 requires {expected:,} bytes.")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    temp_path = output_path + ".recovering"
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        hdr = fits.PrimaryHDU(
            np.zeros((1, 1), dtype=np.float32), header=header.copy()).header.copy()
        hdr["NAXIS1"] = w
        hdr["NAXIS2"] = h
        if mark_recovery:
            hdr["RECOVER"] = (True, "Recovered from preserved mosaic float32 scratch")
            hdr["RAWSRC"] = (os.path.basename(raw_path)[:68], "Recovery source")
            hdr.add_history("TSDP Recovery streamed float32 scratch into FITS")
        elif history_text:
            hdr.add_history(str(history_text))
        header_bytes = hdr.tostring(sep="", endcard=True, padding=True).encode("ascii")

        mm = np.memmap(raw_path, dtype="float32", mode="r", shape=(h, w))
        target_chunk = 64 * 1024 * 1024
        rows_per_chunk = max(1, min(h, target_chunk // max(4, w * 4)))
        with open(temp_path, "wb", buffering=8 * 1024 * 1024) as out:
            out.write(header_bytes)
            for y0 in range(0, h, rows_per_chunk):
                if cancel_cb and cancel_cb():
                    raise RuntimeError("Recovery cancelled by user.")
                y1 = min(h, y0 + rows_per_chunk)
                block = np.asarray(mm[y0:y1], dtype=">f4")
                out.write(block.tobytes(order="C"))
                if progress_cb:
                    progress_cb(y1, h, f"Writing recovered FITS rows {y1:,}/{h:,}")
            data_bytes = h * w * 4
            padding = (-data_bytes) % 2880
            if padding:
                out.write(b"\0" * padding)
            out.flush()
            try:
                os.fsync(out.fileno())
            except OSError:
                pass
        del mm

        with fits.open(temp_path, memmap=True, lazy_load_hdus=True) as hdul:
            got_shape = tuple(int(x) for x in hdul[0].shape)
            if got_shape != (h, w):
                raise RuntimeError(f"Recovered FITS verification failed: {got_shape} != {(h, w)}")
            if not WCS(hdul[0].header, naxis=2).has_celestial:
                raise RuntimeError("Recovered FITS verification failed: celestial WCS is missing.")
        os.replace(temp_path, output_path)
        return output_path
    except EXPECTED_OPERATION_ERRORS:
        # Preserve a substantial interrupted write for diagnosis, but remove a
        # tiny header-only stub that cannot contain useful image data.
        try:
            if os.path.isfile(temp_path) and os.path.getsize(temp_path) < 1024 * 1024:
                os.remove(temp_path)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        raise


def _recovery_header_inputs(paths):
    """Build lightweight (shape-only array, header) pairs for optimal-WCS math."""
    inputs = []
    names = []
    for path in paths:
        real_path, requested_hdu, _label = _parse_mosaic_hdu_ref(path)
        with fits.open(real_path, memmap=True, lazy_load_hdus=True) as hdul:
            primary = hdul[0].header.copy() if hdul else fits.Header()
            chosen = None
            for hdu in _iter_requested_image_hdus(hdul, requested_hdu):
                hdr = hdu.header.copy()
                nx = int(hdr.get("NAXIS1", 0) or 0)
                ny = int(hdr.get("NAXIS2", 0) or 0)
                if nx <= 0 or ny <= 0:
                    continue
                try:
                    if not WCS(hdr, naxis=2).has_celestial:
                        continue
                except EXPECTED_OPERATION_ERRORS:
                    continue
                for key in ("FILTER", "FILTER1", "FILTER2", "PUPIL", "OBJECT", "EXPTIME"):
                    if key not in hdr and key in primary:
                        hdr[key] = primary[key]
                # A zero-stride view has the required logical shape while using
                # one byte of storage. find_optimal_celestial_wcs only needs its
                # shape and WCS, never its pixel values.
                seed = np.zeros(1, dtype=np.uint8)
                shape_only = np.lib.stride_tricks.as_strided(
                    seed, shape=(ny, nx), strides=(0, 0), writeable=False)
                chosen = (shape_only, hdr)
                break
            if chosen is None:
                raise ValueError(f"No celestial image HDU found in {real_path}")
            inputs.append(chosen)
            names.append(_mosaic_input_display_name(path))
    return inputs, names


def _infer_recovery_geometry(raw_path, wcs_native, shape_native, preferred_scale):
    """Match a raw float32 byte count to the scale used by the mosaic run."""
    pixels = os.path.getsize(raw_path) // 4
    if os.path.getsize(raw_path) % 4:
        raise ValueError("Raw scratch size is not divisible by four; it is truncated or not float32.")
    candidates = [preferred_scale, 1.0, 0.75, 2 / 3, 0.5, 0.4, 1 / 3,
                  0.25, 0.2, 0.125, 0.1, 0.05]
    seen = set()
    for scale in candidates:
        try:
            scale = _normalise_mosaic_downscale(scale)
        except EXPECTED_OPERATION_ERRORS:
            continue
        key = round(scale, 9)
        if key in seen:
            continue
        seen.add(key)
        wcs_scaled, shape_scaled = _downscale_celestial_wcs(
            wcs_native, shape_native, scale)
        if int(shape_scaled[0]) * int(shape_scaled[1]) == pixels:
            return wcs_scaled, tuple(int(x) for x in shape_scaled), scale
    raise ValueError(
        f"The scratch file contains {pixels:,} float32 pixels, but none of the "
        "common output scales match the supplied FITS footprints. Enter the scale "
        "used by the failed mosaic and verify that the same original tiles are loaded.")


def _merge_repair_boxes(boxes):
    """Merge overlapping (y0, y1, x0, x1) rectangles."""
    merged = []
    for box in sorted(boxes):
        y0, y1, x0, x1 = box
        changed = True
        while changed:
            changed = False
            keep = []
            for oy0, oy1, ox0, ox1 in merged:
                if not (y1 < oy0 or oy1 < y0 or x1 < ox0 or ox1 < x0):
                    y0, y1 = min(y0, oy0), max(y1, oy1)
                    x0, x1 = min(x0, ox0), max(x1, ox1)
                    changed = True
                else:
                    keep.append((oy0, oy1, ox0, ox1))
            merged = keep
        merged.append((y0, y1, x0, x1))
    return merged


def _find_internal_nan_boxes(data, sample_step=8, margin=32):
    """Locate internal NaN islands without allocating a full-resolution mask."""
    h, w = int(data.shape[-2]), int(data.shape[-1])
    step = max(2, int(sample_step))
    sample = np.asarray(data[::step, ::step])
    bad = ~np.isfinite(sample)
    labels, count = ndi_label(bad)
    if count <= 0:
        return []
    exterior = set(np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]
    ])).tolist())
    exterior.discard(0)
    boxes = []
    for label_id, slices in enumerate(find_objects(labels), start=1):
        if slices is None or label_id in exterior:
            continue
        sy, sx = slices
        local = labels[sy, sx] == label_id
        # Ignore isolated single-sample defects; the visible mosaic holes are
        # much larger and this avoids repairing random NaN cosmic-ray pixels.
        if int(local.sum()) < 4:
            continue
        y0 = max(0, sy.start * step - margin)
        y1 = min(h, sy.stop * step + margin)
        x0 = max(0, sx.start * step - margin)
        x1 = min(w, sx.stop * step + margin)
        boxes.append((y0, y1, x0, x1))
    return _merge_repair_boxes(boxes)


def _source_overlaps_repair_box(source_header, source_shape, target_wcs, box):
    try:
        ny, nx = int(source_shape[-2]), int(source_shape[-1])
        source_wcs = WCS(source_header, naxis=2)
        footprint = source_wcs.calc_footprint(axes=(nx, ny))
        if footprint is None:
            return True
        px, py = target_wcs.all_world2pix(footprint[:, 0], footprint[:, 1], 0)
        if not np.all(np.isfinite(px)) or not np.all(np.isfinite(py)):
            return True
        y0, y1, x0, x1 = box
        return not (np.nanmax(px) < x0 or np.nanmin(px) >= x1 or
                    np.nanmax(py) < y0 or np.nanmin(py) >= y1)
    except EXPECTED_OPERATION_ERRORS:
        return True


def _copy_file_chunked(source, destination, progress_cb=None, cancel_cb=None):
    total = max(1, os.path.getsize(source))
    done = 0
    with open(source, "rb") as src, open(destination, "wb") as dst:
        while True:
            if cancel_cb and cancel_cb():
                raise RuntimeError("Operation cancelled by user.")
            chunk = src.read(32 * 1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            done += len(chunk)
            if progress_cb:
                progress_cb(done, total, "Copying mosaic to a safe repair file")
        dst.flush()
        try:
            os.fsync(dst.fileno())
        except OSError:
            pass

__all__ = [name for name in globals() if not name.startswith('__')]
