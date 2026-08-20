"""Recording audio to a WAV file.

Two sources, tried in order:

**System audio (WASAPI loopback).** What the speakers are playing — which in a
meeting is everyone *else*. This is the one that makes the feature worth
having, and it needs no virtual cable or extra driver: Windows exposes any
output device as a capture device, and PortAudio surfaces that through
`sd.WasapiSettings(loopback=True)`. Older sounddevice builds have no such
argument, hence the fallback rather than a version pin.

**The microphone.** Captures your side of the conversation only. Worth having
as a fallback — half a meeting transcribed is far better than none — but the
caller is told which one it got, because the difference matters.

Disk writes happen on a separate thread. Blocking inside a PortAudio callback
is how you get dropouts and glitches in the recording, and `wave.writeframes`
on a slow disk absolutely can block.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Frames of audio waiting to be written before the oldest are dropped. At
# 48 kHz stereo int16 with 1024-frame blocks this is ~40 seconds of slack,
# far more than any disk hiccup, and bounded so a stuck writer cannot eat
# memory for the rest of a three-hour recording.
_MAX_QUEUED_BLOCKS = 2000


@dataclass(slots=True)
class Source:
    """Which input a recording actually ended up using."""

    name: str
    kind: str          # "system audio" | "microphone"
    sample_rate: int
    channels: int

    def spoken(self) -> str:
        return f"{self.kind} ({self.name})"


class Recorder:
    """One recording at a time, written straight to disk as it arrives."""

    def __init__(self, sample_rate: int = 16000, capture_system_audio: bool = True):
        self.preferred_rate = sample_rate
        self.capture_system_audio = capture_system_audio
        self._stream = None
        self._wave: wave.Wave_write | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=_MAX_QUEUED_BLOCKS)
        self._writer: threading.Thread | None = None
        self._stop = threading.Event()
        self.path: Path | None = None
        self.source: Source | None = None
        self.started_at: float = 0.0
        self.dropped_blocks = 0

    # ------------------------------------------------------------------ state
    @property
    def active(self) -> bool:
        return self._stream is not None

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started_at if self.active else 0.0

    # ------------------------------------------------------------------ start
    def start(self, path: Path) -> Source:
        """Begin recording to `path`. Returns the source actually opened."""
        if self.active:
            raise RuntimeError("already recording")

        import sounddevice as sd

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        stream, source = self._open_stream(sd)

        self._wave = wave.open(str(path), "wb")
        self._wave.setnchannels(source.channels)
        self._wave.setsampwidth(2)  # int16
        self._wave.setframerate(source.sample_rate)

        self._stop.clear()
        self.dropped_blocks = 0
        self._writer = threading.Thread(
            target=self._drain, name="recorder-writer", daemon=True
        )
        self._writer.start()

        self._stream = stream
        self.path = path
        self.source = source
        self.started_at = time.monotonic()
        stream.start()
        log.info("recording to %s from %s", path.name, source.spoken())
        return source

    def _open_stream(self, sd):
        """Try each capture route in order of usefulness."""
        attempts = []
        if self.capture_system_audio:
            attempts.append(self._loopback_attempt(sd))
        attempts.append(self._microphone_attempt(sd))

        errors = []
        for attempt in attempts:
            if attempt is None:
                continue
            device, source, extra = attempt
            for rate in (self.preferred_rate, int(source.sample_rate)):
                try:
                    stream = sd.InputStream(
                        device=device,
                        samplerate=rate,
                        channels=source.channels,
                        dtype="int16",
                        blocksize=1024,
                        callback=self._callback,
                        extra_settings=extra,
                    )
                except Exception as exc:  # unsupported rate, device in use, ...
                    errors.append(f"{source.kind} at {rate}Hz: {exc}")
                    continue
                return stream, Source(
                    source.name, source.kind, rate, source.channels
                )

        raise RuntimeError("no usable audio input — " + "; ".join(errors[-3:]))

    def _loopback_attempt(self, sd):
        """The output device, opened for capture. None if unsupported."""
        try:
            index = sd.default.device[1]
            info = sd.query_devices(index, "output")
            extra = sd.WasapiSettings(loopback=True)
        except (AttributeError, TypeError):
            # sounddevice too old to expose loopback, or not a WASAPI host.
            log.debug("WASAPI loopback unavailable in this sounddevice build")
            return None
        except Exception:
            log.debug("no usable default output device", exc_info=True)
            return None

        return (
            index,
            Source(
                name=info["name"],
                kind="system audio",
                sample_rate=int(info["default_samplerate"]),
                channels=min(2, int(info["max_output_channels"]) or 2),
            ),
            extra,
        )

    def _microphone_attempt(self, sd):
        try:
            index = sd.default.device[0]
            info = sd.query_devices(index, "input")
        except Exception:
            log.debug("no usable default input device", exc_info=True)
            return None
        return (
            index,
            Source(
                name=info["name"],
                kind="microphone",
                sample_rate=int(info["default_samplerate"]),
                channels=1,
            ),
            None,
        )

    # ------------------------------------------------------------- the stream
    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        if status:
            log.debug("recorder stream status: %s", status)
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            # Drop rather than block: blocking here stalls the audio device
            # itself and corrupts everything after it, whereas a dropped block
            # is 20ms of silence in one place.
            self.dropped_blocks += 1

    def _drain(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                block = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._wave is not None:
                try:
                    self._wave.writeframes(block)
                except Exception:
                    log.exception("recorder: write failed, stopping")
                    return

    # ------------------------------------------------------------------- stop
    def stop(self) -> Path | None:
        """Stop, flush and close the file. Returns the path written."""
        if not self.active:
            return None

        stream, self._stream = self._stream, None
        try:
            stream.stop()
            stream.close()
        except Exception:
            log.debug("recorder: stream close failed", exc_info=True)

        self._stop.set()
        if self._writer is not None:
            self._writer.join(timeout=5)
            self._writer = None

        if self._wave is not None:
            try:
                self._wave.close()
            except Exception:
                log.debug("recorder: wave close failed", exc_info=True)
            self._wave = None

        if self.dropped_blocks:
            log.warning(
                "recorder: dropped %d block(s) — the disk could not keep up",
                self.dropped_blocks,
            )
        path, self.path = self.path, None
        self.source = None
        return path


def duration_seconds(path: Path) -> float:
    """How long a recorded WAV runs, without decoding it."""
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate() or 1)
    except Exception:
        return 0.0
