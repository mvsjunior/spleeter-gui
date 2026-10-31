import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spleeter_gui.core.models import CommandSpec, Job, ModelInfo, OptionSpec  # noqa: E402


class FakeEngine:
    """Runs a short Python script instead of a model."""

    id = "fake"
    label = "Falso"
    codecs = ("wav", "mp3")
    lossless_codecs = ("wav",)

    def __init__(self, script: str = "", installed: bool = True, cached: bool = True) -> None:
        self.script = script
        self.installed = installed
        self.cached = cached
        self.peak_mb = 100.0
        self.seconds: float | None = None

    def is_installed(self) -> bool:
        return self.installed

    def models(self) -> list[ModelInfo]:
        return [
            ModelInfo("one", "Um stem", ("a",), 1.0, 10),
            ModelInfo("two", "Dois stems", ("a", "b"), 2.0, 20),
        ]

    def options(self) -> list[OptionSpec]:
        return [
            OptionSpec("flag", "Opção", "bool", False),
            OptionSpec("name", "Nome", "text", "padrão", advanced=True),
        ]

    def is_model_cached(self, model: str) -> bool:
        return self.cached

    def estimate_peak_mb(self, job: Job) -> float:
        return self.peak_mb

    def estimate_seconds(self, job: Job) -> float | None:
        return self.seconds

    def required_disk_bytes(self, job: Job) -> int:
        return 1000

    def prepare_commands(self, job: Job, workdir: Path) -> list[CommandSpec]:
        return []

    def finish_commands(self, job: Job, workdir: Path) -> list[CommandSpec]:
        return []

    def build_command(self, job: Job, workdir: Path) -> CommandSpec:
        return CommandSpec(
            argv=[sys.executable, "-c", self.script, str(workdir)],
            env={"FAKE_ENGINE_VAR": "ok"},
        )

    def progress_parser(self, job: Job):
        return lambda line: (
            float(line.removeprefix("PROGRESS ")) if line.startswith("PROGRESS ") else None
        )

    def collect_outputs(self, job: Job, workdir: Path) -> dict[str, Path]:
        path = workdir / "a.wav"
        if not path.is_file():
            raise FileNotFoundError("Stem não gerado: a")
        return {"a": path}


WRITES_OUTPUT = "import pathlib, sys; pathlib.Path(sys.argv[1], 'a.wav').write_text('x')"


@pytest.fixture
def fake_engine() -> FakeEngine:
    return FakeEngine(WRITES_OUTPUT)


@pytest.fixture
def make_tone(tmp_path):
    """Creates a real, short audio file."""

    def make(name: str = "tone.wav", seconds: float = 1.0) -> Path:
        path = tmp_path / name
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:sample_rate=44100",
                "-t", str(seconds), "-ac", "2", str(path),
            ],
            check=True,
        )  # fmt: skip
        return path

    return make


@pytest.fixture
def make_executable():
    def make(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    return make
