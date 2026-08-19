"""Wake word detection.

openWakeWord runs locally on the CPU at a few percent of one core, which is what
makes an always-on microphone acceptable: **no audio leaves this machine until
the wake word fires.** Keep it that way — moving detection to a cloud service
would mean streaming your room to someone else's server all day.

openWakeWord ships pretrained models for "alexa", "hey mycroft", "hey jarvis",
"hey rhasspy". There is no "Peter" model. Options:

  1. Use hey_jarvis to start (the default in .env.example).
  2. Train a custom "hey Peter" model — Google Colab notebooks in the
     openWakeWord repo do this in about an hour from synthetic samples — then
     point voice.wake.model at the resulting .onnx file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from peter.core.config import get_config

log = logging.getLogger(__name__)

BUILTIN_MODELS = ("alexa", "hey_mycroft", "hey_jarvis", "hey_rhasspy")

class WakeWordDetector:
    def __init__(
        self,
        model: str | None = None,
        threshold: float | None = None,
    ):
        cfg = get_config().voice.wake
        self.model_name = (model or cfg.model).strip()
        self.threshold = threshold if threshold is not None else cfg.threshold
        # After firing, ignore detections for a moment so one utterance of the
        # wake word does not trigger several times as it slides through the
        # buffer. 25 frames is about two seconds at 80ms per frame.
        self.refractory_frames = cfg.refractory_frames
        self._cooldown = 0
        self._model = self._load()

    def _load(self):
        from openwakeword.model import Model

        if self.model_name in BUILTIN_MODELS:
            self._ensure_builtin_models()
            spec = [self.model_name]
        else:
            path = Path(self.model_name)
            if not path.is_file():
                raise FileNotFoundError(
                    f"Wake word model {self.model_name!r} is neither a built-in "
                    f"({', '.join(BUILTIN_MODELS)}) nor a path to an .onnx file. "
                    "Set voice.wake.model in config.yml."
                )
            spec = [str(path)]

        log.info("wake word: %s (threshold %.2f)", self.model_name, self.threshold)
        return Model(wakeword_models=spec, inference_framework="onnx")

    @staticmethod
    def _ensure_builtin_models() -> None:
        """Download the pretrained models once, on first run."""
        import openwakeword
        from openwakeword.utils import download_models

        resources = Path(openwakeword.__file__).parent / "resources" / "models"
        if any(resources.glob("*.onnx")):
            return
        log.info("downloading openWakeWord models (first run only)...")
        download_models()

    def process(self, frame: np.ndarray) -> bool:
        """Feed one 80ms int16 frame. True when the wake word just fired."""
        if self._cooldown > 0:
            self._cooldown -= 1
            self._model.predict(frame)  # keep internal buffers advancing
            return False

        scores = self._model.predict(frame)
        best = max(scores.values()) if scores else 0.0
        if best >= self.threshold:
            self._cooldown = self.refractory_frames
            log.info("wake word detected (score %.2f)", best)
            return True
        return False

    def reset(self) -> None:
        self._cooldown = self.refractory_frames
