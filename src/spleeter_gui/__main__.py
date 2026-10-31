import signal
import socket
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QSocketNotifier
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from spleeter_gui.core.paths import DATA_DIR, HISTORY_PATH
from spleeter_gui.core.storage import History
from spleeter_gui.runner import recover_interrupted
from spleeter_gui.ui.main_window import MainWindow

# src/spleeter_gui/__main__.py -> repository root
ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "spleeter-gui.svg"


def acquire_instance_lock(lock: QLockFile) -> bool:
    """One instance at a time: two would compete for the CPU and clean up after each other."""
    lock.setStaleLockTime(0)  # the lock is only stale when its owner has died
    return lock.tryLock(0)


def exit_on_signals(app: QApplication) -> tuple[socket.socket, socket.socket, QSocketNotifier]:
    """Leaves through the normal exit on SIGINT and SIGTERM, ending the engine on the way out."""
    # Python only runs signal handlers when it has control, which Qt's loop does not give it.
    reader, writer = socket.socketpair()
    writer.setblocking(False)
    signal.set_wakeup_fd(writer.fileno())
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: None)
    notifier = QSocketNotifier(reader.fileno(), QSocketNotifier.Type.Read, app)
    notifier.activated.connect(lambda *_: app.exit(1))
    return reader, writer, notifier


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("spleeter-gui")
    app.setApplicationName("spleeter-gui")
    app.setDesktopFileName("spleeter-gui")
    app.setWindowIcon(QIcon(str(ICON_PATH)))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(DATA_DIR / "app.lock"))
    if not acquire_instance_lock(lock):
        QMessageBox.information(None, "Spleeter GUI", "O Spleeter GUI já está aberto.")
        return 0

    history = History(HISTORY_PATH)
    recover_interrupted(history)
    window = MainWindow(history=history)
    app.aboutToQuit.connect(window.shut_down)
    signal_watch = exit_on_signals(app)
    window.show()
    code = app.exec()
    del signal_watch
    return code


if __name__ == "__main__":
    sys.exit(main())
