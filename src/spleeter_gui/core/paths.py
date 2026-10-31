"""Where the app keeps its local data."""

from pathlib import Path

DATA_DIR = Path.home() / ".local/share/spleeter-gui"
MODELS_DIR = DATA_DIR / "models"
HISTORY_PATH = DATA_DIR / "history.db"
