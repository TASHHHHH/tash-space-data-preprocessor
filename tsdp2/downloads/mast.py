from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER

def parse_mast_csv(path: str) -> list:
    """
    Parse a MAST search-results CSV.
    Skips all lines starting with '#' (MAST puts comment / schema info there).
    Returns a list of dicts with column names as keys.
    """
    import csv as _csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    if not lines:
        return []
    reader = _csv.DictReader(lines)
    return [dict(r) for r in reader]

__all__ = [name for name in globals() if not name.startswith('__')]
