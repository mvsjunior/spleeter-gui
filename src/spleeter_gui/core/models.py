"""Plain data shared by the engines, the runner and the interface."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

OptionValue = str | int | float | bool


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    stems: tuple[str, ...]
    relative_cost: float  # measured time relative to the engine's cheapest model
    download_mb: int = 0


@dataclass(frozen=True)
class OptionSpec:
    """An engine-specific option. The interface builds its form from these."""

    key: str
    label: str
    kind: Literal["bool", "text", "int", "float", "choice"]
    default: OptionValue
    tooltip: str = ""
    advanced: bool = False
    affects_speed: bool = False  # runs with another value do not count for the time estimate
    minimum: float = 0
    maximum: float = 0
    choices: tuple[tuple[str, str], ...] = ()  # value and label


@dataclass(frozen=True)
class CommandSpec:
    argv: list[str]
    env: dict[str, str]  # added to the inherited environment


@dataclass
class Job:
    input_path: Path
    output_dir: Path
    engine_id: str
    model: str
    source_duration: float  # from probe, in seconds
    codec: str = "wav"
    bitrate: str = "128k"
    offset: float = 0.0
    duration: float | None = None  # None means until the end of the file
    options: dict[str, OptionValue] = field(default_factory=dict)  # engine-specific
    id: int | None = None
    status: JobStatus = JobStatus.QUEUED
    result_dir: Path | None = None
    error: str | None = None
    elapsed_s: float | None = None
    created_at: str | None = None
    downloaded_model: bool = False  # its time includes the download, so it says nothing of speed

    @property
    def effective_duration(self) -> float:
        """Seconds to process from `offset`, capped at the end of the file."""
        if self.source_duration <= 0:
            raise ValueError(f"Duração inválida: {self.source_duration}")
        if not 0 <= self.offset < self.source_duration:
            raise ValueError(f"Início fora da música: {self.offset} s")
        remaining = self.source_duration - self.offset
        if self.duration is None:
            return remaining
        if self.duration <= 0:
            raise ValueError(f"Duração do trecho inválida: {self.duration} s")
        return min(self.duration, remaining)
