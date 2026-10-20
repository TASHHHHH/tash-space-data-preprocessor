# Changelog

## 3.2.4

- Replace whole-tile median equalization with robust joint overlap normalization using spatial median cells, outlier rejection, sparse least squares, and regularized tile slopes.
- Use the same additive sky model for mean, median, feather, disk-backed mosaics, drafts, and seam repair; keep disconnected science tiles independent.
- Replace draft high-pass cleanup with optional source-masked per-detector sky fitting before blending, including a scalar fallback for sparse sky cells.
- Preserve finite-data masks, photometric gains, and the reference sky plane in overlap-only mode; record normalization in FITS headers.
- Invalidate previously combined resume caches when the normalization method changes.
- Bound sampling to 8 million float pixels across tiles and remove duplicate draft correction arrays and scratch copies.
- Exercise the new sparse solver in the packaged-runtime smoke check.

## 3.2.3

- Fetch SkyView FITS backgrounds through NASA's direct HTTPS interface, without importing astroquery's unrelated VO/SAMP dependencies; bound request size/time and cache recent cutouts.
- Keep area selection available on an offline celestial WCS grid when the sky service is unavailable.
- Add coverage-window output scale controls synchronized with the Mosaic tab, plus live per-filter width x height readouts for the full footprint or selected/rotated area.
- Compute dimensions with the same native geometry, scale overrides, and crop operations as the final mosaic writer, using headers rather than full science arrays.
- Composite transparent coverage fills once per filter so overlapping frames do not obscure the sky image.
- Deliver background worker results through a UI-thread queue and keep long status/file labels within the coverage window.

## 3.2.2

- Recognize optical ugrizY, common RGB/narrowband names, DECam descriptive FILTER headers, and compressed FITS filenames; retain custom filter header names.
- Use consistent header-first filter detection in mosaic grouping, multi-CCD inputs, coverage previews, and suggested names.
- Add Auto (safe RAM) worker selection, enabled by default, with persistent manual override.
- Bound concurrency using currently free RAM and leave two CPU threads plus at least 40% of available RAM outside the processing budget.
- Use spare CPU capacity for parallel blocks inside large tiles, within the same overall resource limits.
- Reproject in float32 blocks and blend feather mosaics with reusable workspaces instead of full tile/weight stacks.
- Avoid redundant coordinate roundtrips only for nearby, undistorted small TAN fields; retain checks for wide or distorted fields.
- Expand regression tests and validate numerical output against the previous mosaic path.

## 3.2.1

- Fix crashes when starting mosaics, cleaning backgrounds, or repairing seams after the UI refactor.
- Restore WCS coverage previews and filter navigation by retaining the window's selected filters.
- Fix filename extraction from MAST download URLs containing an encoded product URI.
- Fix mean-mosaic coverage and weight-map output by passing the coadd coverage array to the product writer.
- Validate the packaged FITS/reprojection smoke check before creating release archives.
- Add seven workflow regression tests, including Tk coverage-window construction and synthetic FITS processing.

The Windows installer retains the existing application ID and supports upgrading an existing installation.
