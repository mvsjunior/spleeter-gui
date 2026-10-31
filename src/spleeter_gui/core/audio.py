"""Decoding and mixing stems. Imports NumPy, so the app only loads it when the mixer opens."""

import math
import os
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_FRAME = CHANNELS * 4
MAX_STEMS = 8
EXPORT_BITRATE = "320k"
EXPORT_ENCODERS = {
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac"],
    ".mp3": ["-c:a", "libmp3lame", "-b:a", EXPORT_BITRATE],
    ".ogg": ["-c:a", "libvorbis", "-b:a", EXPORT_BITRATE],
    ".m4a": ["-c:a", "aac", "-b:a", EXPORT_BITRATE],
}
AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".wma", ".aiff", ".aif", ".opus"}
STEM_ORDER = ("vocals", "drums", "bass", "guitar", "piano", "other", "accompaniment")

_READ_BYTES = 1 << 20
_EXPORT_BLOCK_FRAMES = SAMPLE_RATE
_PEAK_PASS_SHARE = 0.3
# Just under full scale: exactly 1.0 does not survive the conversion to 16 bits unchanged.
_NORMALIZED_PEAK = 0.999


class AudioError(Exception):
    """A problem with the audio, with a message meant for the user."""


class ExportCancelled(Exception):
    pass


@dataclass(frozen=True)
class ExportResult:
    path: Path
    peak: float  # of the mix before normalizing, where 1.0 is 0 dBFS
    scale: float


def find_stems(folder: Path) -> dict[str, Path]:
    """Maps each stem's name to its file, in a stable and familiar order."""
    try:
        paths = [p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES and p.is_file()]
    except OSError as error:
        raise AudioError(f"Não foi possível ler a pasta: {error}") from error
    if not paths:
        raise AudioError(f"Nenhum arquivo de áudio em: {folder}")
    if len(paths) > MAX_STEMS:
        raise AudioError(
            f"{folder.name} tem {len(paths)} arquivos de áudio e não parece uma pasta de stems "
            f"(o limite é {MAX_STEMS})."
        )

    def rank(path: Path) -> tuple[int, str]:
        known = path.stem in STEM_ORDER
        return (STEM_ORDER.index(path.stem) if known else len(STEM_ORDER), path.stem)

    named: dict[str, Path] = {}
    for path in sorted(paths, key=rank):
        name = path.stem
        if name in named:  # the same name in another format or subfolder
            name = f"{path.stem} ({path.suffix.lstrip('.')}, {path.parent.name})"
        named[name] = path
    return named


def memory_needed_bytes(durations: Sequence[float]) -> int:
    return int(sum(durations) * SAMPLE_RATE * BYTES_PER_FRAME)


def decode(path: Path, duration: float) -> np.ndarray:
    """Reads any format as float32 stereo at SAMPLE_RATE, with one array of shape (frames, 2)."""
    # Allocated once and filled in place: joining chunks would need twice the memory at the end.
    capacity = math.ceil(duration * SAMPLE_RATE) + SAMPLE_RATE
    samples = np.empty((capacity, CHANNELS), dtype=np.float32)
    view = memoryview(samples).cast("B")
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path.absolute()), "-vn",
        "-f", "f32le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "pipe:1",
    ]  # fmt: skip
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors
            )
        except OSError as error:
            raise AudioError(f"Não foi possível iniciar o ffmpeg: {error}") from error
        with process:
            received = 0
            while received < len(view):
                count = process.stdout.readinto(view[received : received + _READ_BYTES])
                if not count:
                    break
                received += count
            truncated = received == len(view)
            if truncated:
                process.kill()
            code = process.wait()
        view.release()
        if not truncated and (code != 0 or received < BYTES_PER_FRAME):
            errors.seek(0)
            detail = errors.read().decode(errors="replace").strip().splitlines()
            reason = detail[-1] if detail else f"código {code}"
            raise AudioError(f"Não foi possível decodificar {path.name}: {reason}")
    return samples[: received // BYTES_PER_FRAME]


def effective_gains(
    volumes: Sequence[float], muted: Sequence[bool], soloed: Sequence[bool]
) -> list[float]:
    """What each stem is heard at. Any solo silences the rest; mute wins over solo."""
    any_solo = any(soloed)
    return [
        0.0 if mute or (any_solo and not solo) else volume
        for volume, mute, solo in zip(volumes, muted, soloed, strict=True)
    ]


def render(
    out: np.ndarray,
    stems: Sequence[np.ndarray],
    position: int,
    start_gains: Sequence[float],
    end_gains: Sequence[float],
) -> None:
    """Writes the sum of the stems at `position`, each gain moving linearly along the block."""
    frames = len(out)
    out.fill(0.0)
    for samples, start, end in zip(stems, start_gains, end_gains, strict=True):
        if start == 0.0 and end == 0.0:
            continue
        chunk = samples[position : position + frames]
        count = len(chunk)
        if count == 0:
            continue
        if start == end:
            out[:count] += chunk * end
        else:
            ramp = np.linspace(start, end, frames, dtype=np.float32)
            out[:count] += chunk * ramp[:count, np.newaxis]


def waveform_peaks(samples: np.ndarray, buckets: int) -> np.ndarray:
    """The loudest sample of each of `buckets` equal parts of the audio."""
    peaks = np.zeros(buckets, dtype=np.float32)
    edges = np.linspace(0, len(samples), buckets + 1, dtype=np.int64)
    for index in range(buckets):
        part = samples[edges[index] : edges[index + 1]]
        if len(part):
            peaks[index] = np.abs(part).max()
    return peaks


def total_frames(stems: Sequence[np.ndarray]) -> int:
    return max((len(samples) for samples in stems), default=0)


def export_mix(
    stems: Sequence[np.ndarray],
    gains: Sequence[float],
    path: Path,
    on_progress: Callable[[float], None] = lambda value: None,
    should_stop: Callable[[], bool] = lambda: False,
) -> ExportResult:
    """Encodes the mix to `path`, scaled down if it would clip. Nothing partial stays behind."""
    encoder = EXPORT_ENCODERS.get(path.suffix.lower())
    if encoder is None:
        raise AudioError(f"Formato de exportação não suportado: {path.suffix or path.name}")
    total = total_frames(stems)
    block = np.empty((_EXPORT_BLOCK_FRAMES, CHANNELS), dtype=np.float32)

    peak = 0.0
    for position in range(0, total, _EXPORT_BLOCK_FRAMES):
        if should_stop():
            raise ExportCancelled
        render(block, stems, position, gains, gains)
        peak = max(peak, float(np.abs(block).max()))
        on_progress(_PEAK_PASS_SHARE * position / total)
    scale = _NORMALIZED_PEAK / peak if peak > 1.0 else 1.0

    temporary = path.with_name(f".{uuid.uuid4().hex}{path.suffix}")
    command = [
        "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SAMPLE_RATE),
        "-ac", str(CHANNELS), "-i", "pipe:0", *encoder, str(temporary),
    ]  # fmt: skip
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
            stopped = False
            try:
                for position in range(0, total, _EXPORT_BLOCK_FRAMES):
                    if should_stop():
                        stopped = True
                        break
                    render(block, stems, position, gains, gains)
                    count = min(_EXPORT_BLOCK_FRAMES, total - position)
                    process.stdin.write((block[:count] * scale).tobytes())
                    share = _PEAK_PASS_SHARE + (1 - _PEAK_PASS_SHARE) * position / total
                    on_progress(share)
                process.stdin.close()
            except BrokenPipeError:
                pass  # ffmpeg ended first; its exit code and message say why
            if stopped:
                process.kill()
            code = process.wait()
            if stopped:
                raise ExportCancelled
            if code != 0:
                errors.seek(0)
                detail = errors.read().decode(errors="replace").strip().splitlines()
                raise AudioError(f"A exportação falhou: {detail[-1] if detail else code}")
        os.replace(temporary, path)
    except OSError as error:
        raise AudioError(f"A exportação falhou: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return ExportResult(path, peak, scale)
