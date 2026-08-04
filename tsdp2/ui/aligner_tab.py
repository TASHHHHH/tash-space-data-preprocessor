from __future__ import annotations

from tsdp2.ui._common import *
from tsdp2.ui.aligner_layout import AlignerLayoutMixin
from tsdp2.ui.aligner_inputs import AlignerInputMixin
from tsdp2.ui.aligner_previews import AlignerPreviewMixin
from tsdp2.ui.aligner_processing import AlignerProcessingMixin

class AlignerTab(AlignerProcessingMixin, AlignerPreviewMixin, AlignerInputMixin, AlignerLayoutMixin, ttk.Frame):
    """Composed tab controller with UI, preview, and processing concerns split."""

    def __init__(self, *args, **kwargs):
        self._initialize_ui(*args, **kwargs)

__all__ = [name for name in globals() if not name.startswith('__')]
