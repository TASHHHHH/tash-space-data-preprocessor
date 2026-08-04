from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER

class RunState(str, Enum):
    """Shared lifecycle states used by jobs and new service interfaces."""

    PENDING = "Pending"
    RUNNING = "Running"
    DONE = "Done"
    ERROR = "Error"
    STOPPED = "Stopped"
    CANCELLED = "Cancelled"

__all__ = [name for name in globals() if not name.startswith('__')]
