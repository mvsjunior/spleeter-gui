import subprocess
import sys


def test_core_and_engines_stay_free_of_qt_and_engine_libraries():
    code = """
import sys
import spleeter_gui.core.models, spleeter_gui.core.probe, spleeter_gui.core.storage
import spleeter_gui.core.estimate, spleeter_gui.core.preflight, spleeter_gui.core.timecode
import spleeter_gui.engines
forbidden = ("PySide6", "tensorflow", "torch", "spleeter", "demucs")
print([m for m in sys.modules if m.split(".")[0] in forbidden])
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_opening_the_interface_never_loads_engine_libraries():
    code = """
import sys
import spleeter_gui.ui.main_window
forbidden = ("tensorflow", "torch", "spleeter", "demucs", "numpy", "sounddevice", "pylibrb")
print([m for m in sys.modules if m.split(".")[0] in forbidden])
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
