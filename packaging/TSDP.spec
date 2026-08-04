# -*- mode: python ; coding: utf-8 -*-

from importlib.metadata import PackageNotFoundError
from importlib.util import find_spec
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


spec_dir = Path(SPECPATH).resolve()
project_root = spec_dir.parent
datas = [(str(project_root / "README.md"), ".")]
hiddenimports = []
asset_dir = project_root / "tsdp2" / "assets"
if asset_dir.is_dir():
    datas += [
        (str(asset), "tsdp2/assets")
        for asset in asset_dir.iterdir()
        if asset.is_file()
    ]

for package in ("reproject", "sv_ttk", "astroquery", "keyring", "certifi"):
    if find_spec(package) is None:
        continue
    datas += collect_data_files(package)
    try:
        datas += copy_metadata(package)
    except PackageNotFoundError:
        continue

for package in (
    "astroquery.mast",
    "keyring.backends",
    "reproject.mosaicking",
):
    if find_spec(package) is not None:
        hiddenimports += collect_submodules(
            package, filter=lambda name: ".tests" not in name
        )

aria2_license = project_root / "vendor" / "aria2-COPYING.txt"
if aria2_license.is_file():
    datas.append((str(aria2_license), "licenses"))

analysis = Analysis(
    [str(project_root / "TSDP2.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(project_root / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "jupyter",
        "matplotlib",
        "pytest",
        "astropy.visualization",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TSDP",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(asset_dir / "app_icon.ico"),
    version=str(project_root / "packaging" / "version_info.txt"),
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TSDP",
)


