from __future__ import annotations

from tsdp2.ui._common import *
from tsdp2.ui.mosaic_layout import MosaicLayoutMixin
from tsdp2.ui.mosaic_sources import MosaicSourcesMixin
from tsdp2.ui.mosaic_groups import MosaicGroupsMixin
from tsdp2.ui.mosaic_recovery import MosaicRecoveryMixin
from tsdp2.ui.mosaic_processing import MosaicProcessingMixin
from tsdp2.ui.mosaic_events import MosaicEventsMixin

class MosaicTab(MosaicProcessingMixin, MosaicEventsMixin, MosaicRecoveryMixin, MosaicGroupsMixin, MosaicSourcesMixin, MosaicLayoutMixin, ttk.Frame):
    """Composed tab controller with UI, preview, and processing concerns split."""

    def __init__(self, *args, **kwargs):
        self._initialize_ui(*args, **kwargs)

__all__ = [name for name in globals() if not name.startswith('__')]
