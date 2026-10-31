"""Engine registry: adding an engine means adding it here."""

from spleeter_gui.engines.base import Engine
from spleeter_gui.engines.demucs import DemucsEngine
from spleeter_gui.engines.spleeter import SpleeterEngine

# The first one is the default.
ENGINES: dict[str, Engine] = {engine.id: engine for engine in (SpleeterEngine(), DemucsEngine())}
