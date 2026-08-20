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

from peter import perf as perf_module
from peter.core.config import get_config
from peter.core.errors import VoiceError
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
        try:
            self._model = WhisperModel(
                self.cfg.model,
                device=self.cfg.device,
                compute_type=self.cfg.compute_type,
            )
        except Exception as exc:
            raise VoiceError(
                f"Whisper model {self.cfg.model!r} failed to load: {exc}",
                recoverable=False,
                user_action=(
                    "Check voice.stt.model, voice.stt.device, and "
                    "voice.stt.compute_type in config.yml."
                ),
            ) from exc
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
        lead_in_levels: list[float] = []
        speaking = False
        silence = 0.0
        lead_in = 0.0
        threshold = self.threshold

        try:
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
                    lead_in_levels.append(level)
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
        finally:
            self._update_noise_floor(lead_in_levels)

    def _update_noise_floor(self, levels: list[float]) -> None:
        """Blend this utterance's pre-speech ambient level into the noise floor.

        A one-shot startup calibration goes stale if the room changes later —
        a fan turns on, traffic dies down at night. Blending a little of every
        utterance's lead-in keeps it current without a full recalibration
        pass, and costs nothing extra since these frames are already read.
        """
        if not self.cfg.adaptive_noise or not levels:
            return
        rate = self.cfg.adaptive_rate
        self.noise_floor = (1 - rate) * self.noise_floor + rate * float(np.median(levels))

    # ---------------------------------------------------------- transcription
    def transcribe(self, audio_int16: np.ndarray) -> str:
        audio = audio_int16.astype(np.float32) / 32768.0
        try:
            segments, _info = self._model.transcribe(
                audio,
                language="en",
                beam_size=1,          # greedy: markedly faster, no real accuracy cost
                vad_filter=True,      # drop the trailing silence before decoding
                condition_on_previous_text=False,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as exc:
            raise VoiceError(f"transcription failed: {exc}") from exc

    def listen(self, mic: Microphone) -> str:
        """Record one utterance and return its text ("" if nothing was said).

        Raises VoiceError if Whisper itself fails; a caller that wants voice
        turns to keep going after a bad transcription should catch it.
        """
        with perf_module.phase("record"):
            audio = self.record_utterance(mic)
        if audio is None:
            return ""
        started = time.perf_counter()
        with perf_module.phase("transcribe"):
            text = self.transcribe(audio)
        log.debug(
            "transcribed %.1fs of audio in %.0fms",
            len(audio) / SAMPLE_RATE,
            (time.perf_counter() - started) * 1000,
        )
        return text
