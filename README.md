# Tash Space Data Preprocessor (TSDP)

TSDP 3.2.1 is a Windows desktop application for downloading and processing astronomical FITS data. It supports multi-HDU inputs, WCS-aware mosaics, normalization, alignment, previews, recovery, and optional Telegram control.

## Recommended installation

Run `TSDP-Setup-3.2.1.exe`. The installer uses the current Windows account, supports installing over a previous version, creates Start menu shortcuts, and can optionally create a desktop shortcut. Python is not required for the installed application.

A portable ZIP is also provided. Extract the entire folder before running `TSDP.exe`; do not run the executable from inside the ZIP.

On the first launch, the setup assistant checks folders, dependencies, available memory and disk space, aria2, and optional integrations. The same checks remain available from **Check Setup** and from Settings. The bundled `aria2c.exe` enables managed downloads out of the box.

## Running from source

Use Python 3.10 or newer:

```powershell
cd C:\Users\egeme\Downloads\tsdp
python -m pip install -e ".[all,dev]"
python TSDP2.py
```

The normal workflow is Download & Process -> Mosaic -> Align & Composite. Recovery can resume or repair interrupted disk-backed mosaic work. Large reprojections use float32 memmaps, resource-adaptive workers, disk preflight checks, chunked calculations, and cancellation checks rather than forcing complete detector frames into RAM.

## Resource planning

Before a mosaic starts, TSDP shows estimated peak working memory, final-product size, scratch-disk use, and free space. Disk-backed Turbo worker counts are not limited to three: they adapt to the requested value, tile count, CPU threads, geometry, and currently available RAM.

## Output folders

Every organized processing run receives an isolated, collision-safe job folder. Directories are created only when needed:

```text
Job_Name/
|-- final/{aligned,mosaics,normalized}/<filter>/
|-- previews/{png,jpeg}/
|-- diagnostics/{footprints,coverage_maps,masks,seam_maps,weight_maps,rejected_frames}/
|-- intermediate/{reprojected,cropped,normalized,temporary_fits,memmaps}/
|-- recovery/{manifests,checkpoints}/
|-- metadata/{logs,reports,file_lists}/
`-- job_manifest.json
```

Final FITS products are never removed with intermediates. Existing flat folders are not moved, renamed, or deleted; a newly numbered job folder is created by default. The manifest uses relative product paths so jobs can be moved safely.

## Telegram and security

Configure the chat ID and notification choices in Settings. Tokens are read from Windows Credential Manager through `keyring`, or from `JWST_SUITE_TELEGRAM_TOKEN`. Legacy plaintext tokens are migrated out of the JSON settings file atomically. Token values and token-bearing Telegram URLs are redacted from logs and excluded from job manifests.

The existing command set is preserved, including guided `/newjob`, `/status`, `/rerun`, `/stop`, reference selection, output/previews/FITS/log delivery, ZIP delivery, CSV upload, blink GIFs, cleanup, and `/flush`.

## Architecture

- `tsdp2/astronomy`: FITS, WCS, normalization, coverage, mosaic, and seam algorithms
- `tsdp2/downloads`: aria2, queue, MAST import, and post-download services
- `tsdp2/telegram`: client, parser, listener, orchestration, and output delivery
- `tsdp2/recovery`: manifests, cleanup, repair, and recovery UI
- `tsdp2/ui`: focused tabs, dialogs, onboarding, installation check, and About
- `tsdp2/utilities`: resource planning, release checks, and Tk dispatch
- `tsdp2/product.py`, `config.py`, `output_paths.py`, `logging_config.py`, and `models.py`: shared infrastructure

`TSDP2.py` is a small launcher. Runtime monkey-patching is not used.

## Tests and release build

Tests use synthetic offline data:

```powershell
python -m pytest -q
```

Build the portable app and installer with:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1
```

Release artifacts and SHA-256 hashes are written to `release/`. The release build also supports a non-GUI packaged FITS/WCS/reprojection smoke check.

## Logo and artwork

The custom pixel-art Earth logo was supplied by the TSDP project owner. Its original 2500 x 2500 PNG and SHA-256 provenance are retained under `tsdp2/assets`. Windows icon sizes use nearest-neighbor conversion so the intended pixel blocks stay sharp in the title bar, taskbar, executable, shortcuts, and installer.
## Bundled software

The Windows distribution bundles aria2 1.37.0 and its COPYING file. aria2 remains covered by its own license. TSDP's Python astronomy and UI dependencies are included in the packaged application.

