import signal
import subprocess
import sys
import time

from PySide6.QtCore import QLockFile

from spleeter_gui.__main__ import acquire_instance_lock


def test_second_instance_is_refused(qtbot, tmp_path):
    path = str(tmp_path / "app.lock")
    first = QLockFile(path)
    assert acquire_instance_lock(first)
    code = f"""
import sys
from PySide6.QtCore import QLockFile
from spleeter_gui.__main__ import acquire_instance_lock
sys.exit(0 if acquire_instance_lock(QLockFile({path!r})) else 3)
"""
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 3
    first.unlock()
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


def test_lock_of_a_killed_instance_does_not_block_the_next(qtbot, tmp_path):
    path = str(tmp_path / "app.lock")
    code = f"""
import time
from PySide6.QtCore import QLockFile
from spleeter_gui.__main__ import acquire_instance_lock
lock = QLockFile({path!r})
assert acquire_instance_lock(lock)
print("ok", flush=True)
time.sleep(60)
"""
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "ok"
    holder.send_signal(signal.SIGKILL)
    holder.wait()
    holder.stdout.close()
    time.sleep(0.05)
    assert acquire_instance_lock(QLockFile(path))
