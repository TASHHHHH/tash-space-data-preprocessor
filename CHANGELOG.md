# Changelog

## 3.2.1

- Fix crashes when starting mosaics, cleaning backgrounds, or repairing seams after the UI refactor.
- Restore WCS coverage previews and filter navigation by retaining the window's selected filters.
- Fix filename extraction from MAST download URLs containing an encoded product URI.
- Fix mean-mosaic coverage and weight-map output by passing the coadd coverage array to the product writer.
- Validate the packaged FITS/reprojection smoke check before creating release archives.
- Add seven workflow regression tests, including Tk coverage-window construction and synthetic FITS processing.

The Windows installer retains the existing application ID and supports upgrading an existing installation.
