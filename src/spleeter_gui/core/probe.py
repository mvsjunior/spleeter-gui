"""Audio duration and metadata through ffprobe."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ProbeError(Exception):
    """The file cannot be used as audio. The message is shown to the user."""


@dataclass(frozen=True)
class AudioInfo:
    duration: float
    codec: str
    sample_rate: int
    channels: int
    tags: dict[str, str]


def probe(path: Path) -> AudioInfo:
    # -i keeps a file name starting with "-" from being read as an option.
    argv = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-select_streams",
        "a:0",
        "-i",
        str(path),
    ]
    if not path.is_file():
        raise ProbeError(f"Arquivo não encontrado: {path}")
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise ProbeError("O ffprobe não está instalado.") from None
    except subprocess.TimeoutExpired:
        raise ProbeError(f"O ffprobe demorou demais para ler: {path.name}") from None
    if result.returncode != 0:
        detail = result.stderr.strip() or "formato desconhecido"
        raise ProbeError(f"Não foi possível ler {path.name}: {detail}")

    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ProbeError(f"{path.name} não tem faixa de áudio.")
    stream = streams[0]
    fmt = data.get("format", {})
    duration = _duration(fmt, stream)
    if duration is None:
        raise ProbeError(f"Não foi possível obter a duração de {path.name}.")
    tags = {**stream.get("tags", {}), **fmt.get("tags", {})}
    return AudioInfo(
        duration=duration,
        codec=stream.get("codec_name", ""),
        sample_rate=int(stream.get("sample_rate", 0)),
        channels=int(stream.get("channels", 0)),
        tags=tags,
    )


def _duration(fmt: dict, stream: dict) -> float | None:
    for source in (fmt, stream):
        try:
            value = float(source["duration"])
        except (KeyError, ValueError):
            continue
        if value > 0:
            return value
    return None
