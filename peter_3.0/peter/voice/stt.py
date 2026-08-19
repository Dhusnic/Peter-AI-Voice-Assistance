"""Speech recognition.

faster-whisper, running locally. Two jobs:

**Endpointing** — deciding when the user has stopped talking. This is done with
an energy threshold calibrated against the room's own noise floor at startup,
rather than a fixed number, because a fixed threshold that works in a quiet room
cuts people off mid-sentence next to a running fan. peter_1.0 used
`r.pause_threshold = 1` and inherited whatever ambient calibration
speech_recognition happened to do; this is the same idea made explicit and
tunable.

**Transcription** — the recorded utterance goes to Whisper in one pass. Whisper
is not a streaming model; feeding it partial audio produces worse text than
waiting the extra 300ms.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from peter.core.config import get_config
from peter.voice.audio import FRAME_SECONDS, SAMPLE_RATE, Microphone, rms

log = logging.getLogger(__name__)

class Transcriber:
    """Loads Whisper once and reuses it. Construction is slow; calls are not."""

    def __init__(self) -> None:
        from faster_whisper import WhisperModel

        self.cfg = get_config().voice.stt
        log.info(
            "loading Whisper %s on %s (%s)",
            self.cfg.model, self.cfg.device, self.cfg.compute_type,
        )
        self._model = WhisperModel(
            self.cfg.model,
            device=self.cfg.device,
            compute_type=self.cfg.compute_type,
        )
        self.noise_floor = self.cfg.min_threshold / self.cfg.noise_margin

    # ------------------------------------------------------------ calibration
    def calibrate(self, mic: Microphone, seconds: float = 0.7) -> float:
        """Measure the room's noise floor. Call once at startup."""
        levels: list[float] = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            frame = mic.read(timeout=0.5)
            if frame is not None:
                levels.append(rms(frame))
        if levels:
            self.noise_floor = float(np.median(levels))
        log.info("noise floor %.4f (speech threshold %.4f)", self.noise_floor,
                 self.threshold)
        return self.noise_floor

    @property
    def threshold(self) -> float:
        return max(self.cfg.min_threshold, self.noise_floor * self.cfg.noise_margin)

    # -------------------------------------------------------------- recording
    def record_utterance(self, mic: Microphone) -> np.ndarray | None:
        """Record until the user stops talking. None if they never started."""
        frames: list[np.ndarray] = []
        speaking = False
        silence = 0.0
        lead_in = 0.0
        threshold = self.threshold

        while True:
            frame = mic.read(timeout=1.0)
            if frame is None:
                if speaking:
                    break
                lead_in += 1.0
                if lead_in >= self.cfg.max_lead_in:
                    return None
                continue

            level = rms(frame)

            if not speaking:
                lead_in += FRAME_SECONDS
                if level >= threshold:
                    speaking = True
                    frames.append(frame)
                elif lead_in >= self.cfg.max_lead_in:
                    return None
                else:
                    # Keep the last few frames so the first syllable is not
                    # clipped once speech is detected.
                    frames.append(frame)
                    if len(frames) > 4:
                        frames.pop(0)
                continue

            frames.append(frame)
            silence = silence + FRAME_SECONDS if level < threshold else 0.0

            if silence >= self.cfg.silence_to_end:
                break
            if len(frames) * FRAME_SECONDS >= self.cfg.max_utterance:
                log.info("hit the %.0fs utterance cap", self.cfg.max_utterance)
                break

        if not frames:
            return None
        audio = np.concatenate(frames)
        if len(audio) / SAMPLE_RATE < self.cfg.min_speech:
            return None
        return audio

    # ---------------------------------------------------------- transcription
    def transcribe(self, audio_int16: np.ndarray) -> str:
        audio = audio_int16.astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            beam_size=1,          # greedy: markedly faster, no real accuracy cost
            vad_filter=True,      # drop the trailing silence before decoding
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def listen(self, mic: Microphone) -> str:
        """Record one utterance and return its text ("" if nothing was said)."""
        audio = self.record_utterance(mic)
        if audio is None:
            return ""
        started = time.perf_counter()
        text = self.transcribe(audio)
        log.debug(
            "transcribed %.1fs of audio in %.0fms",
            len(audio) / SAMPLE_RATE,
            (time.perf_counter() - started) * 1000,
        )
        return text
