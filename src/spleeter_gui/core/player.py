"""Plays the sum of the stems in one audio callback, which is what keeps them in sync."""

from collections.abc import Callable, Sequence

import numpy as np
import sounddevice as sd

from spleeter_gui.core.audio import CHANNELS, SAMPLE_RATE, render, total_frames

# Large for a player that needs no quick response: the callback competes for Python's lock with
# the interface, and this CPU can be throttled to 1.2 GHz.
BLOCK_FRAMES = 2048


class Player:
    def __init__(
        self,
        stems: Sequence[np.ndarray],
        gains: Sequence[float],
        on_stream_finished: Callable[[], None],
        stream_factory: Callable[..., object] = sd.OutputStream,
    ) -> None:
        """`on_stream_finished` is called from the audio thread; it must call `stream_ended`."""
        self.frames = total_frames(stems)
        self.underruns = 0
        self._stems = stems
        self._on_stream_finished = on_stream_finished
        self._stream_factory = stream_factory
        self._stream = None
        self._position = 0
        self._seek_to: int | None = None
        self._target_gains = list(gains)
        self._current_gains = list(gains)
        self._master = 0.0
        self._pausing = False
        self._resume = False
        self._ended = False

    @property
    def position(self) -> int:
        return self._position

    @property
    def heard_position(self) -> int:
        """Where the sound coming out is, behind what the callback has already rendered."""
        stream = self._stream
        delay = int(stream.latency * SAMPLE_RATE) if stream is not None else 0
        return max(0, self._position - delay)

    @property
    def is_playing(self) -> bool:
        return self._stream is not None and not self._pausing

    def set_gains(self, gains: Sequence[float]) -> None:
        self._target_gains = list(gains)

    def seek(self, frame: int) -> None:
        frame = min(max(frame, 0), self.frames)
        if self._stream is None:
            self._position = frame
        else:
            self._seek_to = frame

    def play(self) -> None:
        """Raises sd.PortAudioError if the sound device cannot be used."""
        self._pausing = False
        if self._stream is not None:
            self._resume = True  # a pause may be ending right now
            return
        self._resume = False
        if self._position >= self.frames:
            self._position = 0
        self._master = 0.0
        self._current_gains = list(self._target_gains)
        self._ended = False
        stream = self._stream_factory(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_FRAMES,
            channels=CHANNELS,
            dtype="float32",
            latency="high",
            callback=self._callback,
            finished_callback=self._on_finished,
        )
        self._stream = stream
        stream.start()

    def pause(self) -> None:
        """Fades out and stops. The stream ends by itself right after."""
        self._resume = False
        if self._stream is not None:
            self._pausing = True

    def stream_ended(self) -> None:
        """Releases the stream that finished, on the thread that controls the player."""
        stream = self._stream
        if stream is None or not self._ended:
            return
        stream.close()
        self._stream = None
        self._pausing = False
        if self._seek_to is not None:
            self._position = self._seek_to
            self._seek_to = None
        if self._resume:
            self.play()

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        self._resume = False
        if stream is not None:
            stream.close()

    def _on_finished(self) -> None:
        self._ended = True
        self._on_stream_finished()

    def _callback(self, out: np.ndarray, frames: int, time: object, status: sd.CallbackFlags):
        if status.output_underflow:
            self.underruns += 1
        target = self._target_gains
        seek_to = self._seek_to
        pausing = self._pausing
        position = self._position

        render(out, self._stems, position, self._current_gains, target)
        self._current_gains = target

        # Changing where the sound is without a fade would make a click.
        master_end = 0.0 if pausing or seek_to is not None else 1.0
        if self._master != master_end:
            out *= np.linspace(self._master, master_end, frames, dtype=np.float32)[:, np.newaxis]
        self._master = master_end
        # The mix can pass full scale; export scales it down, and here it can only be limited.
        np.clip(out, -1.0, 1.0, out=out)

        self._position = position + frames
        if seek_to is not None:
            self._position = seek_to
            self._seek_to = None
        if pausing:
            raise sd.CallbackStop
        if self._position >= self.frames:
            self._position = self.frames
            self._resume = False
            raise sd.CallbackStop
