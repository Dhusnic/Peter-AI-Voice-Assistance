"""Microphone capture.

One input stream owned by one object, shared by both the wake-word detector and
the speech recogniser. Opening two streams on the same device is the classic way
to end up with a wake word that fires but a recogniser that hears silence,
because Windows hands exclusive access to whoever asked first.

Audio is captured at 16 kHz mono int16 — the native format for both
openWakeWord and Whisper, so no resampling anywhere in the hot path.

Run this module directly to list input devices with their indices:

    .venv/Scripts/python.exe -m peter.voice.audio
"""

from __future__ import annotations

import logging
import queue
import threading

import numpy as np
import sounddevice as sd

from peter.core.config import get_config

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
# openWakeWord expects 80ms frames (1280 samples at 16kHz). Everything
# downstream consumes the same size, so there is one frame size in the system.
FRAME_SAMPLES = 1280
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE


class Microphone:
    """A continuously-running input stream that fans frames into a queue."""

    def __init__(self, device: int | None = None, max_queued_frames: int = 200):
        self.device = (
            device if device is not None
            else get_config().voice.audio.input_device
        )
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queued_frames)
        self._stream: sd.InputStream | None = None
        self._muted = threading.Event()

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._stream is not None:
            return

        def callback(indata, frames, time_info, status):  # noqa: ARG001
            if status:
                log.debug("input stream status: %s", status)
            if self._muted.is_set():
                return
            try:
                self._queue.put_nowait(indata[:, 0].copy())
            except queue.Full:
                # Dropping the oldest frame is right: stale audio is worthless
                # and blocking here would stall the audio callback thread.
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(indata[:, 0].copy())
                except queue.Empty:
                    pass

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=self.device,
            callback=callback,
        )
        self._stream.start()
        log.info("microphone open (device=%s)", self.device or "default")

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "Microphone":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ----------------------------------------------------------------- frames
    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Next 80ms frame as int16, or None if nothing arrived in time."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self) -> None:
        """Discard buffered audio.

        Call this before listening for a command, so Peter does not transcribe
        the tail of its own wake word or the audio that queued up while it was
        thinking.
        """
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def mute(self) -> None:
        """Stop accepting frames without closing the device."""
        self._muted.set()
        self.flush()

    def unmute(self) -> None:
        self.flush()
        self._muted.clear()

    @property
    def is_muted(self) -> bool:
        return self._muted.is_set()


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of an int16 frame, normalised to 0..1."""
    if frame.size == 0:
        return 0.0
    samples = frame.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples * samples)))


def list_devices() -> str:
    lines = ["Input devices (set voice.audio.input_device in config.yml):"]
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            default = " (default)" if idx == sd.default.device[0] else ""
            lines.append(f"  [{idx}] {dev['name']}{default}")
    lines.append("")
    lines.append("Output devices (voice.audio.output_device):")
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0:
            default = " (default)" if idx == sd.default.device[1] else ""
            lines.append(f"  [{idx}] {dev['name']}{default}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(list_devices())
