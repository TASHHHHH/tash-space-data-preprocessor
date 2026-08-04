"""Shared dependency facade for the tab mixins."""

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.models import *
from tsdp2.config import *
from tsdp2.output_paths import *
from tsdp2.utilities.threading import *
from tsdp2.utilities.memory import *
from tsdp2.astronomy.fits_io import *
from tsdp2.astronomy.reference import *
from tsdp2.astronomy.normalization import *
from tsdp2.astronomy.alignment import *
from tsdp2.astronomy.previews import *
from tsdp2.astronomy.coverage import *
from tsdp2.astronomy.regions import *
from tsdp2.astronomy.mosaic_inputs import *
from tsdp2.astronomy.mosaic_geometry import *
from tsdp2.astronomy.mosaicking import *
from tsdp2.astronomy.seam_repair import *
from tsdp2.recovery.manifests import *
from tsdp2.downloads.aria2 import *
from tsdp2.downloads.queue import *
from tsdp2.downloads.pipeline import *
from tsdp2.downloads.mast import *
from tsdp2.telegram.client import *
from tsdp2.ui.theme import *
from tsdp2.ui.components import *
from tsdp2.ui.dialogs import *

__all__ = [name for name in globals() if not name.startswith("__")]
