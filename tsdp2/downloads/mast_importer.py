from __future__ import annotations

from tsdp2.ui._common import *
from tsdp2.downloads.mast_importer_view import MastImporterViewMixin
from tsdp2.downloads.mast_importer_rows import MastImporterRowsMixin
from tsdp2.downloads.mast_importer_products import MastImporterProductsMixin
from tsdp2.downloads.mast_importer_actions import MastImporterActionsMixin

class MASTCsvImporter(
    MastImporterActionsMixin,
    MastImporterProductsMixin,
    MastImporterRowsMixin,
    MastImporterViewMixin,
    tk.Toplevel,
):
    """MAST CSV importer with parsing, view, product, and action concerns split."""

    def __init__(self, *args, **kwargs):
        self._initialize_ui(*args, **kwargs)

__all__ = [name for name in globals() if not name.startswith('__')]
