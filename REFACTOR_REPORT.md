# TSDP 3.2 release and refactor report

## Architecture

`TSDP2.py` is a small launcher. The application is split into focused packages:

- `tsdp2/astronomy`: FITS, WCS, alignment, normalization, mosaics, coverage, and seams
- `tsdp2/downloads`: aria2, queueing, MAST import, and post-download processing
- `tsdp2/telegram`: client, parsing, commands, listener, and output delivery
- `tsdp2/recovery`: manifests, cleanup, repair, and recovery UI
- `tsdp2/ui`: main window, tabs, onboarding, diagnostics, About, and settings
- `tsdp2/utilities`: resource planning, installation checks, and Tk dispatch

The former monolithic executable and runtime monkey-patch chains are no longer used.

## Completed refactor

- Consolidated effective downloader and Telegram behavior into direct classes and services.
- Removed runtime method replacement and captured `_ORIGINAL`, `_PREV`, and `_BASE` chains.
- Split Downloader, Aligner, Mosaic, Recovery, MAST, and WCS coverage responsibilities.
- Replaced broad `except Exception` handlers with explicit expected-operation boundaries and contextual logging.
- Added structured rotating logs and Telegram-token/URL redaction.
- Moved Telegram-token persistence to Windows Credential Manager with environment fallback and atomic legacy migration.
- Centralized generated files under public `JobPaths` routes.
- Preserved float32 memmaps, blockwise calculations, cancellation, recovery data, and atomic final replacement.
- Added a proper offline pytest suite using synthetic FITS data.

## TSDP 3.2 release additions

- Renamed the product to **Tash Space Data Preprocessor** throughout the UI, metadata, package, and installer.
- Removed the old descriptive subtitle from the main window.
- Added a first-run setup assistant with folder, downloader, Telegram, and system-check steps.
- Added a reusable installation checker for required libraries, optional features, aria2, folders, permissions, RAM, and free disk space.
- Added a proper About window with version, build, Python, OS, and update information.
- Added clear peak-memory, final-output, scratch-space, and free-space estimates before mosaic work begins.
- Removed the old three-worker ceiling; disk-backed Turbo workers now adapt to CPU, memory, geometry, requested workers, and tile count.
- Added a self-contained Windows portable build and a per-user installer that supports installing over previous versions.
- Bundled aria2 1.37.0 and its license for managed downloads.
- Added the owner's custom pixel-art Earth logo to the title bar, taskbar, executable, portable build, and installer.

## Validation

Current source result: **62 passed**.

Coverage includes:

- FITS HDU parsing and 2D FITS loading
- filter parsing, grouping, wavelength ordering, and mosaic cache keys
- safe Windows/Unicode paths and traversal protection
- final, preview, diagnostic, intermediate, recovery, and metadata routes
- job collision handling, manifests, atomic settings, and secret exclusion
- WCS comparison, overlap masks, normalization, alignment, mosaics, and cancellation
- recovery manifests and temporary memmap cleanup
- Telegram parsing, batch barriers, cancellation, and redaction
- installation checking, resource-adaptive workers, and mosaic estimate reporting
- main-window construction and state handling
- packaged FITS read/write and WCS reprojection

Release validation also passed:

- clean Python compilation
- PyInstaller build
- portable executable FITS/WCS/reprojection smoke test
- bundled aria2 1.37.0 execution
- Inno Setup compilation
- silent temporary installation
- installed executable FITS/WCS/reprojection smoke test
- clean temporary uninstall
- final UI screenshot capture

## Compatibility and limits

No astronomy pixel algorithm was intentionally changed. Organized jobs change directories, not FITS base filenames or processing math. Existing flat output folders are not moved or deleted. Low-level string-path calls retain their compatibility layout, while GUI runners use organized `JobPaths` jobs.

The automated suite is offline. It does not contact live MAST, Telegram, or a real remote aria2 server. Multi-gigabyte stress behavior still depends on the user's data, selected scratch drive, free space, and available RAM; the same resource, memmap, recovery, and cancellation paths are covered with synthetic data.


