"""Focused Astropy hook for TSDP's FITS, WCS, coordinates, and units usage."""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


def _needed(module_name: str) -> bool:
    excluded = (
        "astropy.tests",
        "astropy.visualization",
        "astropy.cosmology.tests",
        "astropy.modeling.tests",
    )
    return not module_name.startswith(excluded)


datas = (
    collect_data_files("astropy")
    + collect_data_files("astropy.units.format", include_py_files=True)
    + copy_metadata("astropy")
)
hiddenimports = collect_submodules("astropy", filter=_needed)