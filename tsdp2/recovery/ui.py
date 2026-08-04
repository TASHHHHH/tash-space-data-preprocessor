from __future__ import annotations

from tsdp2.ui._common import *
from tsdp2.recovery.view import RecoveryViewMixin
from tsdp2.recovery.scanning import RecoveryScanningMixin
from tsdp2.recovery.sources import RecoverySourcesMixin
from tsdp2.recovery.actions import RecoveryActionsMixin
from tsdp2.recovery.events import RecoveryEventsMixin

class RecoveryTab(
    RecoveryEventsMixin,
    RecoveryActionsMixin,
    RecoverySourcesMixin,
    RecoveryScanningMixin,
    RecoveryViewMixin,
    ttk.Frame,
):
    """Recovery controller composed from view, scan, source, and action services."""

    def __init__(self, *args, **kwargs):
        self._initialize_ui(*args, **kwargs)

__all__ = [name for name in globals() if not name.startswith('__')]
