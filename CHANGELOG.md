# Changelog

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
