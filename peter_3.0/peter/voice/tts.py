"""Speech output.

Three engines behind one interface, chosen by voice.tts.engine in config.yml:

    piper   fully local neural TTS. The default — nothing leaves the machine.
    edge    Microsoft Edge's online voices. Free, excellent Indian-English
            voices, but it is a network call and your text goes to Microsoft.
    sapi    Windows' built-in SAPI5 via pyttsx3. Always available, sounds it.

Two things matter more than voice quality here:

**Sentence streaming.** Text is spoken sentence by sentence as the agent
produces it, so the first words start playing while the rest is still being
written. Waiting for a complete reply before speaking is what makes assistants
feel dead.

**Barge-in.** `stop()` must cut playback within a few tens of milliseconds, so
the user can interrupt mid-sentence. Every engine writes audio in small blocks
and checks the stop flag between them, rather than handing a whole utterance to
a blocking play call.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from peter.core.config import get_config
from peter.core.errors import VoiceError

log = logging.getLogger(__name__)

# Frames per write. Small enough that a stop lands fast, large enough not to
# underrun: 1024 frames at 22.05kHz is ~46ms.
_BLOCK = 1024

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")

# Splitting after these produces a fragment, not a sentence: "Rs." / "500" or
# "Dr." / "Reddy". Each is checked against the last whitespace-delimited word.
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.",
    "rs.", "no.", "vs.", "etc.", "e.g.", "i.e.", "approx.", "a.m.", "p.m.",
}


def split_sentences(text: str) -> list[str]:
    """Split text into chunks that can be spoken one at a time.

    The point is latency: the first chunk starts playing while the rest is still
    being synthesised. So the bias is towards splitting — a chunk held back is a
    chunk the user waits for.

    Two things are merged back, because speaking them alone sounds wrong:
    a fragment following an abbreviation ("Rs." + "500"), and a single-word
    tail ("Ok."). Anything two words or longer stands on its own.
    """
    parts = [p.strip() for p in _SENTENCE_END.split(text or "") if p and p.strip()]

    merged: list[str] = []
    for part in parts:
        previous = merged[-1] if merged else ""
        last_word = previous.rsplit(maxsplit=1)[-1].lower() if previous else ""
        joins_abbreviation = last_word in _ABBREVIATIONS
        is_single_word = len(part.split()) < 2

        if previous and (joins_abbreviation or is_single_word):
            merged[-1] = f"{previous} {part}"
        else:
            merged.append(part)
    return merged


class _Engine:
    sample_rate = 22050

    def stream(self, text: str):
        """Yield float32 mono numpy blocks."""
        raise NotImplementedError


class PiperEngine(_Engine):
    def __init__(self, voice_path: str):
        from piper import PiperVoice

        path = Path(voice_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Piper voice model not found at {path}. Download one from "
                "https://huggingface.co/rhasspy/piper-voices and set "
                "voice.tts.piper_voice in config.yml, or switch "
                "voice.tts.engine to edge."
            )
        self._voice = PiperVoice.load(path)
        self.sample_rate = self._voice.config.sample_rate

    def stream(self, text: str):
        for chunk in self._voice.synthesize(text):
            audio = np.asarray(chunk.audio_float_array, dtype=np.float32)
            for start in range(0, len(audio), _BLOCK):
                yield audio[start : start + _BLOCK]


class EdgeEngine(_Engine):
    """edge-tts returns MP3; decode it with PyAV (already present via faster-whisper)."""

    sample_rate = 24000

    def __init__(self, voice: str):
        self.voice = voice

    def _synthesise_mp3(self, text: str) -> bytes:
        import asyncio

        import edge_tts

        async def run() -> bytes:
            buf = bytearray()
            comm = edge_tts.Communicate(text, self.voice)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)

        return asyncio.run(run())

    def stream(self, text: str):
        import io

        import av

        mp3 = self._synthesise_mp3(text)
        if not mp3:
            return
        container = av.open(io.BytesIO(mp3))
        resampler = av.audio.resampler.AudioResampler(
            format="flt", layout="mono", rate=self.sample_rate
        )
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                audio = resampled.to_ndarray().reshape(-1).astype(np.float32)
                for start in range(0, len(audio), _BLOCK):
                    yield audio[start : start + _BLOCK]


class SapiEngine(_Engine):
    """Windows SAPI5. Blocking and uninterruptible mid-utterance.

    pyttsx3 gives no way to stream samples, so barge-in only lands between
    sentences here. Acceptable for a fallback; another reason Piper is default.
    """

    def __init__(self, rate: int = 185) -> None:
        import pyttsx3

        self._engine = pyttsx3.init("sapi5")
        self._engine.setProperty("rate", rate)
        self._engine.setProperty("volume", 1.0)
        log.info(
            "using the Windows SAPI voice — barge-in only lands between "
            "sentences here, not mid-word like Piper/Edge, because pyttsx3 "
            "has no streaming API to interrupt mid-utterance."
        )

    def speak_blocking(self, text: str) -> None:
        self._engine.say(text)
        self._engine.runAndWait()

    def stop(self) -> None:
        try:
            self._engine.stop()
        except Exception:
            pass

    def stream(self, text: str):  # pragma: no cover - not used
        return iter(())


def build_engine() -> _Engine:
    """Build the configured engine, falling back rather than refusing to start.

    A missing Piper voice file is a setup step nobody has done yet, not a fatal
    error — Peter should still talk, and say so, rather than fail to boot.
    Tries the configured engine first, then the remaining two in a fixed
    piper -> edge -> sapi order, so any single broken engine (missing voice
    file, no network, no SAPI voices registered) still leaves two more
    chances before Peter goes silent.
    """
    cfg = get_config().voice.tts
    choice = cfg.engine.strip().lower()
    if choice not in ("piper", "edge", "sapi"):
        log.warning("unknown voice.tts.engine %r, using edge", choice)
        choice = "edge"

    builders = {
        "piper": lambda: PiperEngine(cfg.piper_voice),
        "edge": lambda: EdgeEngine(cfg.edge_voice),
        "sapi": lambda: SapiEngine(cfg.sapi_rate),
    }
    order = [choice] + [name for name in ("piper", "edge", "sapi") if name != choice]

    last_exc: Exception | None = None
    for name in order:
        try:
            engine = builders[name]()
        except Exception as exc:
            log.warning(
                "%s unavailable (%s)%s", name, exc,
                "" if name == choice else " — trying the next fallback",
            )
            last_exc = exc
            continue
        return engine

    raise VoiceError(
        f"no text-to-speech engine could be started: {last_exc}",
        recoverable=False,
        user_action="Check voice.tts settings in config.yml.",
    )


class Speaker:
    """Queued, interruptible speech output."""

    def __init__(self, engine: _Engine | None = None):
        self.engine = engine or build_engine()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        # Runtime fallback bookkeeping: two failed sentences in a row (each
        # itself already retried once) downgrades to the SAPI engine for the
        # rest of the session, so a flaky network (edge-tts) or a broken
        # Piper voice degrades Peter's voice instead of going silent.
        self._consecutive_failures = 0
        self._degraded = False
        # First-audio tracking: "time to first sound" is what perceived
        # latency actually is, distinct from total wall time (which is
        # mostly just however long the reply is). _first_audio_event fires
        # the moment the current reply's first block starts playing (or
        # immediately, for a reply with nothing to say), so a caller on
        # another thread can wait for it without waiting for the whole
        # reply to finish.
        self._first_audio_started = 0.0
        self._first_audio_at: float | None = None
        self._first_audio_event = threading.Event()
        self._worker = threading.Thread(
            target=self._run, name="peter-tts", daemon=True
        )
        self._worker.start()

    # -------------------------------------------------------------- interface
    def say(self, text: str) -> None:
        """Queue text for speech. Returns immediately."""
        sentences = split_sentences(text)
        self._first_audio_started = time.perf_counter()
        self._first_audio_at = None
        self._first_audio_event.clear()
        if not sentences:
            self._first_audio_event.set()  # nothing will ever play — don't block a waiter
            return
        for sentence in sentences:
            self._idle.clear()
            self._queue.put(sentence)

    def wait_for_first_audio(self, timeout: float | None = None) -> float | None:
        """Block until the current reply's first audio block starts playing
        (or times out, or nothing will ever play), then return the elapsed
        milliseconds since the say() call that started it. None if it timed
        out or no audio was ever produced (e.g. every engine failed)."""
        if not self._first_audio_event.wait(timeout):
            return None
        if self._first_audio_at is None:
            return None
        return (self._first_audio_at - self._first_audio_started) * 1000

    def stop(self) -> None:
        """Cut playback now and drop anything queued. This is barge-in."""
        self._stop.set()
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        if isinstance(self.engine, SapiEngine):
            self.engine.stop()

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    @property
    def is_speaking(self) -> bool:
        return not self._idle.is_set()

    def shutdown(self) -> None:
        self.stop()
        self._queue.put(None)

    # ----------------------------------------------------------------- worker
    def _run(self) -> None:
        while True:
            sentence = self._queue.get()
            if sentence is None:
                self._queue.task_done()
                return

            # A stop that arrived while this item sat in the queue applies to it.
            self._stop.clear()
            try:
                self._speak_with_retry(sentence)
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()

    def _speak_with_retry(self, sentence: str) -> None:
        """Speak one sentence, retrying once, degrading the engine on repeat
        failure. A barge-in (stop() mid-sentence) is not a failure — it is
        the normal `stream.abort()` early-return in `_speak_one`, not an
        exception — so only genuine engine errors count here."""
        try:
            self._speak_one(sentence)
            self._consecutive_failures = 0
            return
        except Exception as exc:
            log.warning("TTS failed for %.60s (%s); retrying once", sentence, exc)

        try:
            self._speak_one(sentence)
            self._consecutive_failures = 0
            return
        except Exception:
            log.exception("TTS failed twice for: %.60s", sentence)

        self._consecutive_failures += 1
        if self._consecutive_failures >= 2 and not self._degraded:
            self._degrade_to_sapi()

    def _degrade_to_sapi(self) -> None:
        if isinstance(self.engine, SapiEngine):
            return
        log.warning(
            "TTS engine having repeated trouble — switching to the Windows "
            "SAPI fallback for the rest of this session. Restart Peter to "
            "retry the configured engine."
        )
        try:
            self.engine = SapiEngine(get_config().voice.tts.sapi_rate)
            self._degraded = True
        except Exception:
            log.exception("SAPI fallback also failed to start; TTS may stay silent")

    def _speak_one(self, sentence: str) -> None:
        if isinstance(self.engine, SapiEngine):
            self.engine.speak_blocking(sentence)
            return

        device = get_config().voice.audio.output_device
        stream = sd.OutputStream(
            samplerate=self.engine.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=_BLOCK,
            device=device,
        )
        with stream:
            for block in self.engine.stream(sentence):
                if self._stop.is_set():
                    stream.abort()
                    return
                if self._first_audio_at is None:
                    self._first_audio_at = time.perf_counter()
                    self._first_audio_event.set()
                stream.write(np.ascontiguousarray(block, dtype=np.float32))


class PrintSpeaker:
    """Stand-in for text mode and tests. Same surface, no audio.

    In text mode `console` is the same Rich Console driving the status
    spinner. Printing through it (rather than bare `print()`) is what keeps a
    mid-turn announcement — a retry notice, mainly — from corrupting the
    spinner's line; Rich coordinates the two because they share one console.
    Left unset, this falls back to plain `print()`, which is all the test
    suite needs.
    """

    def __init__(self, console=None) -> None:
        self.spoken: list[str] = []
        self._console = console

    def say(self, text: str) -> None:
        self.spoken.append(text)
        if self._console is not None:
            from rich.panel import Panel

            # A bordered box separates what Peter said from the log lines
            # around it at a glance — the thing that made a multi-fact answer
            # ("CPU is X, memory is Y, disk is Z...") read as one grey wall of
            # text mixed in with INFO/WARNING noise above it.
            self._console.print(
                Panel(text, title="peter", title_align="left",
                      border_style="bright_cyan", padding=(0, 1))
            )
        else:
            print(f"\n[peter] {text}\n")

    def stop(self) -> None: ...

    def wait_until_idle(self, timeout: float | None = None) -> bool:  # noqa: ARG002
        return True

    @property
    def is_speaking(self) -> bool:
        return False

    def shutdown(self) -> None: ...
