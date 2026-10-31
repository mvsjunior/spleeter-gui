"""What the runner and the interface expect from an engine."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from spleeter_gui.core.models import CommandSpec, Job, ModelInfo, OptionSpec

# src/spleeter_gui/engines/base.py -> repository root
ENGINES_DIR = Path(__file__).resolve().parents[3] / "engines"


class Engine(Protocol):
    id: str
    label: str
    codecs: tuple[str, ...]
    lossless_codecs: tuple[str, ...]

    def is_installed(self) -> bool: ...

    def models(self) -> list[ModelInfo]: ...

    def options(self) -> list[OptionSpec]: ...

    def is_model_cached(self, model: str) -> bool: ...

    def estimate_peak_mb(self, job: Job) -> float: ...

    def estimate_seconds(self, job: Job) -> float | None: ...  # when the history has no record

    def required_disk_bytes(self, job: Job) -> int: ...

    def prepare_commands(self, job: Job, workdir: Path) -> list[CommandSpec]: ...

    def build_command(self, job: Job, workdir: Path) -> CommandSpec: ...

    def finish_commands(self, job: Job, workdir: Path) -> list[CommandSpec]: ...

    def progress_parser(self, job: Job) -> Callable[[str], float | None]: ...

    def collect_outputs(self, job: Job, workdir: Path) -> dict[str, Path]: ...
