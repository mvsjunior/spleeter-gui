"""Changes the key of audio as it plays, with Rubber Band. Loaded only along with the mixer."""

from collections.abc import Callable

import numpy as np
from pylibrb import Option, RubberBandStretcher

# The "finer" engine sounds better, but caused underruns here even with the CPU idle.
_OPTIONS = Option.PROCESS_REALTIME | Option.ENGINE_FASTER | Option.CHANNELS_TOGETHER


class PitchShifter:
    """Fills blocks of any size, asking for input one fixed block at a time.

    The output is aligned with the input: its frame N is frame N of the input, in another key.
    """

    def __init__(
        self, semitones: int, sample_rate: int, channels: int, input_frames: int = 2048
    ) -> None:
        self._semitones = semitones
        self._stretcher = RubberBandStretcher(
            sample_rate=sample_rate,
            channels=channels,
            options=_OPTIONS,
            initial_pitch_scale=_pitch_scale(semitones),
        )
        self._input_frames = input_frames
        self._stretcher.set_max_process_size(input_frames)
        self._input = np.zeros((input_frames, channels), dtype=np.float32)
        self._output = np.empty((input_frames * 4, channels), dtype=np.float32)
        self._ready = 0
        self._skip = 0
        self._fed = 0
        self._emitted = 0
        self.reset()

    @property
    def delay(self) -> int:
        """Frames already asked of the input that have not come out yet."""
        return self._fed - self._emitted

    def set_semitones(self, semitones: int) -> None:
        """Takes effect cleanly after `reset`. Must not run at the same time as `read`."""
        if semitones != self._semitones:
            self._semitones = semitones
            self._stretcher.pitch_scale = _pitch_scale(semitones)

    def reset(self) -> None:
        """Forgets the audio so far, as needed when what comes next does not follow from it."""
        self._stretcher.reset()
        self._ready = 0
        self._fed = 0
        self._emitted = 0
        pad = self._stretcher.get_preferred_start_pad()
        silence = np.zeros((self._stretcher.channels, self._input_frames), dtype=np.float32)
        for start in range(0, pad, self._input_frames):
            self._stretcher.process(silence[:, : min(self._input_frames, pad - start)])
        self._skip = self._stretcher.get_start_delay()
        self._take_output()

    def read(self, out: np.ndarray, feed: Callable[[np.ndarray], None]) -> None:
        """Fills `out`, calling `feed` to write the next frames of input into the array it gets."""
        frames = len(out)
        while self._ready < frames:
            feed(self._input)
            self._fed += self._input_frames
            self._stretcher.process(self._input.T.copy())
            self._take_output()
        out[:] = self._output[:frames]
        self._output[: self._ready - frames] = self._output[frames : self._ready]
        self._ready -= frames
        self._emitted += frames

    def _take_output(self) -> None:
        produced = self._stretcher.retrieve_available().T
        dropped = min(self._skip, len(produced))
        self._skip -= dropped
        produced = produced[dropped:]
        needed = self._ready + len(produced)
        if needed > len(self._output):
            grown = np.empty((needed * 2, self._output.shape[1]), dtype=np.float32)
            grown[: self._ready] = self._output[: self._ready]
            self._output = grown
        self._output[self._ready : needed] = produced
        self._ready = needed


def _pitch_scale(semitones: int) -> float:
    return 2.0 ** (semitones / 12)
