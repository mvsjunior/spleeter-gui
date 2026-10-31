"""Checks made before starting a job."""

import shutil
from pathlib import Path

# 48 kHz, stereo, 16 bits: the largest uncompressed output that is common.
_PCM_BYTES_PER_SECOND = 192_000
_LOSSLESS_CODECS = ("wav", "flac")
_MARGIN_BYTES = 100_000_000


def required_bytes(duration: float, stems: int, codec: str, bitrate: str) -> int:
    """Upper estimate of the disk space a job's output takes."""
    if codec in _LOSSLESS_CODECS:
        rate = _PCM_BYTES_PER_SECOND
    else:
        rate = int(bitrate.removesuffix("k")) * 1000 // 8
    return int(duration * rate * stems) + _MARGIN_BYTES


def free_bytes(path: Path) -> int:
    """Free space on the disk that holds `path`, which may not exist yet."""
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    return shutil.disk_usage(existing).free


def disk_shortage(path: Path, needed: int) -> str | None:
    """Explains the problem when the disk that holds `path` lacks `needed` bytes."""
    free = free_bytes(path)
    if free >= needed:
        return None
    return (
        f"Espaço insuficiente no destino: são necessários cerca de "
        f"{needed / 1e9:.1f} GB, e há {free / 1e9:.1f} GB livres."
    )


def available_memory_mb(meminfo: Path = Path("/proc/meminfo")) -> float | None:
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None
