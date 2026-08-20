"""Tests for the voice pipeline's error handling, fallback, and spoken
feedback — the paths tests/test_voice.py never touches because it stays
away from Microphone/Transcriber/WakeWordDetector/Speaker entirely.

Nothing here opens a real audio device or loads a real Whisper/openWakeWord
model: every external boundary (sounddevice, faster-whisper, openwakeword,
pyttsx3) is monkeypatched or replaced with a fake, same style test_registry.py
and test_policy.py already use for their own boundaries.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pytest

import peter.voice.audio as audio_mod
import peter.voice.stt as stt_mod
import peter.voice.tts as tts_mod
import peter.voice.wake as wake_mod
from peter.core.config import Config, SttConfig
from peter.core.errors import IntegrationError, VoiceError
from peter.voice.audio import FRAME_SAMPLES, Microphone
from peter.voice.stt import Transcriber
from peter.voice.tts import Speaker, build_engine
from peter.voice.wake import WakeWordDetector


# --------------------------------------------------------------- VoiceError
def test_voice_error_is_an_integration_error_with_the_voice_service():
    exc = VoiceError("mic exploded", user_action="Try again.")
    assert isinstance(exc, IntegrationError)
    assert exc.service == "voice"
    assert exc.recoverable is True
    assert exc.spoken() == "Something went wrong with voice. Try again."


def test_voice_error_can_be_marked_unrecoverable():
    exc = VoiceError("bad config", recoverable=False)
    assert exc.recoverable is False


# ------------------------------------------------------------------- stt.py
def test_transcriber_init_wraps_whisper_load_failure(monkeypatch):
    import faster_whisper

    class Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("no such model")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Boom)
    with pytest.raises(VoiceError):
        Transcriber()


def test_transcribe_wraps_a_whisper_failure():
    t = Transcriber.__new__(Transcriber)

    class BoomModel:
        def transcribe(self, *a, **kw):
            raise RuntimeError("bad audio")

    t._model = BoomModel()
    with pytest.raises(VoiceError):
        t.transcribe(np.zeros(1600, dtype=np.int16))


def test_adaptive_noise_floor_blends_toward_the_new_median():
    t = Transcriber.__new__(Transcriber)
    t.cfg = SttConfig(adaptive_noise=True, adaptive_rate=0.5)
    t.noise_floor = 0.1

    t._update_noise_floor([0.2, 0.2, 0.2])

    assert t.noise_floor == pytest.approx(0.15)


def test_adaptive_noise_floor_disabled_is_a_no_op():
    t = Transcriber.__new__(Transcriber)
    t.cfg = SttConfig(adaptive_noise=False)
    t.noise_floor = 0.1

    t._update_noise_floor([0.9, 0.9])

    assert t.noise_floor == 0.1


def test_adaptive_noise_floor_ignores_empty_levels():
    t = Transcriber.__new__(Transcriber)
    t.cfg = SttConfig(adaptive_noise=True, adaptive_rate=0.5)
    t.noise_floor = 0.1

    t._update_noise_floor([])

    assert t.noise_floor == 0.1


class _FakeMic:
    """Hands out preset frames, then None forever — no real Microphone."""

    def __init__(self, frames):
        self._frames = list(frames)

    def read(self, timeout: float = 1.0):
        return self._frames.pop(0) if self._frames else None


def test_record_utterance_blends_ambient_level_into_noise_floor_on_timeout():
    """A user who never speaks still gives record_utterance three quiet
    lead-in frames before it gives up — those should still feed the
    adaptive noise floor, since they are real ambient-level samples that
    were already being read anyway."""
    t = Transcriber.__new__(Transcriber)
    t.cfg = SttConfig(
        max_lead_in=0.24,  # exactly 3 frames at 80ms each
        adaptive_noise=True,
        adaptive_rate=0.5,
        min_threshold=0.5,
        noise_margin=2.0,
    )
    t.noise_floor = 0.5  # threshold = max(0.5, 0.5*2.0) = 1.0, well above silence

    silent_frames = [np.zeros(FRAME_SAMPLES, dtype=np.int16) for _ in range(3)]
    result = t.record_utterance(_FakeMic(silent_frames))

    assert result is None
    assert t.noise_floor == pytest.approx(0.25)


# ----------------------------------------------------------------- audio.py
def test_microphone_start_wraps_a_portaudio_failure(monkeypatch):
    class Boom:
        def __init__(self, *a, **kw):
            raise OSError("no such device")

    monkeypatch.setattr(audio_mod.sd, "InputStream", Boom)
    mic = Microphone(device=0)  # explicit device skips get_config()

    with pytest.raises(VoiceError):
        mic.start()
    assert mic._stream is None


# ------------------------------------------------------------------ wake.py
def test_wake_word_detector_bad_custom_path_raises_voice_error():
    with pytest.raises(VoiceError):
        WakeWordDetector(model="Z:/definitely/not/a/real/path.onnx")


# ------------------------------------------------------------------- tts.py
def test_build_engine_falls_back_from_piper_to_edge(monkeypatch):
    cfg = Config()
    cfg.voice.tts.engine = "piper"
    monkeypatch.setattr(tts_mod, "get_config", lambda: cfg)

    class BoomPiper:
        def __init__(self, *a, **kw):
            raise FileNotFoundError("no voice file")

    sentinel = object()
    monkeypatch.setattr(tts_mod, "PiperEngine", BoomPiper)
    monkeypatch.setattr(tts_mod, "EdgeEngine", lambda voice: sentinel)

    assert build_engine() is sentinel


def test_build_engine_falls_back_all_the_way_to_sapi(monkeypatch):
    cfg = Config()
    cfg.voice.tts.engine = "piper"
    monkeypatch.setattr(tts_mod, "get_config", lambda: cfg)

    class Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("nope")

    sentinel = object()
    monkeypatch.setattr(tts_mod, "PiperEngine", Boom)
    monkeypatch.setattr(tts_mod, "EdgeEngine", Boom)
    monkeypatch.setattr(tts_mod, "SapiEngine", lambda rate: sentinel)

    assert build_engine() is sentinel


def test_build_engine_raises_voice_error_if_every_engine_fails(monkeypatch):
    cfg = Config()
    cfg.voice.tts.engine = "edge"
    monkeypatch.setattr(tts_mod, "get_config", lambda: cfg)

    class Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("nope")

    monkeypatch.setattr(tts_mod, "PiperEngine", Boom)
    monkeypatch.setattr(tts_mod, "EdgeEngine", Boom)
    monkeypatch.setattr(tts_mod, "SapiEngine", Boom)

    with pytest.raises(VoiceError):
        build_engine()


def _bare_speaker() -> Speaker:
    """A Speaker with no background thread and no real engine — just the
    retry/degrade bookkeeping under test."""
    speaker = Speaker.__new__(Speaker)
    speaker.engine = object()
    speaker._consecutive_failures = 0
    speaker._degraded = False
    return speaker


def test_speaker_retries_once_before_counting_a_failure(monkeypatch):
    speaker = _bare_speaker()
    calls: list[str] = []

    def flaky(sentence):
        calls.append(sentence)
        if len(calls) == 1:
            raise RuntimeError("one dropped block")

    monkeypatch.setattr(speaker, "_speak_one", flaky)
    speaker._speak_with_retry("hello")

    assert len(calls) == 2
    assert speaker._consecutive_failures == 0


def test_speaker_degrades_to_sapi_after_two_consecutive_failures(monkeypatch):
    class FakeSapi:
        def __init__(self, rate):
            self.rate = rate

    speaker = _bare_speaker()
    monkeypatch.setattr(speaker, "_speak_one", MagicMock(side_effect=RuntimeError("broken")))
    monkeypatch.setattr(tts_mod, "SapiEngine", FakeSapi)
    monkeypatch.setattr(tts_mod, "get_config", lambda: Config())

    speaker._speak_with_retry("first sentence")
    assert speaker._consecutive_failures == 1
    assert not isinstance(speaker.engine, FakeSapi)

    speaker._speak_with_retry("second sentence")
    assert speaker._consecutive_failures == 2
    assert isinstance(speaker.engine, FakeSapi)
    assert speaker._degraded is True


def test_speaker_does_not_re_degrade_once_already_on_sapi(monkeypatch):
    class FakeSapi:
        def __init__(self, rate):
            self.rate = rate

    speaker = _bare_speaker()
    speaker.engine = FakeSapi(185)
    speaker._degraded = True
    speaker._consecutive_failures = 2

    build_calls = MagicMock(side_effect=lambda rate: FakeSapi(rate))
    monkeypatch.setattr(tts_mod, "SapiEngine", build_calls)
    monkeypatch.setattr(speaker, "_speak_one", MagicMock(side_effect=RuntimeError("still broken")))
    monkeypatch.setattr(tts_mod, "get_config", lambda: Config())

    speaker._speak_with_retry("third sentence")

    build_calls.assert_not_called()


# ---------------------------------------------------------- VoiceConfirmer
def test_voice_confirmer_escalates_to_fallback_when_stt_fails():
    from peter.ui.confirm import VoiceConfirmer

    speaker = MagicMock()
    transcriber = MagicMock()
    transcriber.listen.side_effect = VoiceError("transcription failed: boom")
    mic = MagicMock()
    fallback = MagicMock()
    fallback.ask.return_value = True

    confirmer = VoiceConfirmer(speaker, transcriber, mic, fallback=fallback)
    result = confirmer.ask("delete_file(path=x)", timeout=1.0)

    assert result is True
    fallback.ask.assert_called_once_with("delete_file(path=x)", 1.0)


def test_voice_confirmer_returns_true_on_a_clear_yes():
    from peter.ui.confirm import VoiceConfirmer

    speaker = MagicMock()
    transcriber = MagicMock()
    transcriber.listen.return_value = "yes"
    mic = MagicMock()
    fallback = MagicMock()

    confirmer = VoiceConfirmer(speaker, transcriber, mic, fallback=fallback)
    result = confirmer.ask("delete_file(path=x)", timeout=1.0)

    assert result is True
    fallback.ask.assert_not_called()


def test_voice_confirmer_returns_false_on_a_clear_no():
    from peter.ui.confirm import VoiceConfirmer

    speaker = MagicMock()
    transcriber = MagicMock()
    transcriber.listen.return_value = "no"
    mic = MagicMock()

    confirmer = VoiceConfirmer(speaker, transcriber, mic, fallback=MagicMock())
    assert confirmer.ask("delete_file(path=x)", timeout=1.0) is False


# --------------------------------------------------------- main._voice_tick
def _bare_peter():
    from peter.main import Peter

    p = Peter.__new__(Peter)
    p.tray = MagicMock()
    p.tray.paused = False
    p.mic = MagicMock()
    p.mic.read.return_value = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    p.speaker = MagicMock()
    p.speaker.is_speaking = False
    p.speaker.wait_for_first_audio.return_value = 42.0
    p.detector = MagicMock()
    p.detector.process.return_value = True  # wake word fires
    p.transcriber = MagicMock()
    p.brain = MagicMock()
    p.brain.usage_summary.return_value = "usage: n/a"
    p.handle = MagicMock(return_value="the reply")
    p.container = MagicMock()
    return p


def test_voice_tick_speaks_an_acknowledgment_when_nothing_usable_was_heard():
    p = _bare_peter()
    p.transcriber.listen.return_value = ""

    p._voice_tick()

    p.speaker.say.assert_called_once_with("Didn't catch that.")
    p.handle.assert_not_called()
    ok = p.container.perf.record.call_args.kwargs.get("ok")
    assert ok is True


def test_voice_tick_speaks_the_error_when_stt_fails():
    p = _bare_peter()
    exc = VoiceError("transcription failed: boom")
    p.transcriber.listen.side_effect = exc

    p._voice_tick()

    p.speaker.say.assert_called_once_with(exc.spoken())
    p.handle.assert_not_called()
    ok = p.container.perf.record.call_args.kwargs.get("ok")
    assert ok is False


def test_voice_tick_normal_success_path_still_records_perf():
    p = _bare_peter()
    p.transcriber.listen.return_value = "what's the weather"

    p._voice_tick()

    p.handle.assert_called_once_with("what's the weather")
    p.speaker.say.assert_called_once_with("the reply")
    args, kwargs = p.container.perf.record.call_args
    assert args[0] == "voice_turn"
    assert kwargs.get("ok") is True


def test_voice_tick_does_nothing_when_wake_word_does_not_fire():
    p = _bare_peter()
    p.detector.process.return_value = False

    p._voice_tick()

    p.speaker.say.assert_not_called()
    p.transcriber.listen.assert_not_called()


def test_voice_tick_barge_in_stops_speaker_then_listens():
    p = _bare_peter()
    p.speaker.is_speaking = True
    p.transcriber.listen.return_value = "stop that"

    p._voice_tick()

    p.speaker.stop.assert_called_once()
    p.handle.assert_called_once_with("stop that")


def test_voice_tick_records_tts_first_audio_phase():
    p = _bare_peter()
    p.transcriber.listen.return_value = "what's the weather"

    p._voice_tick()

    p.speaker.wait_for_first_audio.assert_called_once()
    _, kwargs = p.container.perf.record.call_args
    assert kwargs["phases"]["tts_first_audio"] == 42.0


def test_voice_tick_skips_the_phase_when_no_audio_ever_played():
    p = _bare_peter()
    p.speaker.wait_for_first_audio.return_value = None
    p.transcriber.listen.return_value = "hello"

    p._voice_tick()

    _, kwargs = p.container.perf.record.call_args
    assert "tts_first_audio" not in (kwargs["phases"] or {})


# --------------------------------------------------- run_voice() self-heal
def test_run_voice_falls_back_to_text_mode_when_speaker_fails_to_build(monkeypatch):
    from peter.main import Peter

    p = Peter.__new__(Peter)
    p.mic = None
    p.speaker = None
    p.transcriber = None
    p.detector = None
    p.container = MagicMock()
    p.run_text = MagicMock()

    class BoomSpeaker:
        def __init__(self, *a, **kw):
            raise VoiceError("no text-to-speech engine could be started", recoverable=False)

    monkeypatch.setattr("peter.voice.tts.Speaker", BoomSpeaker)

    p.run_voice()

    p.run_text.assert_called_once()
    assert p.speaker is None
    assert p.mic is None


def test_run_voice_shuts_down_the_speaker_if_mic_fails_afterward(monkeypatch):
    from peter.main import Peter

    p = Peter.__new__(Peter)
    p.mic = None
    p.speaker = None
    p.transcriber = None
    p.detector = None
    p.container = MagicMock()
    p.run_text = MagicMock()

    fake_speaker = MagicMock()
    monkeypatch.setattr("peter.voice.tts.Speaker", lambda *a, **kw: fake_speaker)

    class BoomMic:
        def __init__(self, *a, **kw):
            raise VoiceError("could not open microphone", recoverable=False)

    monkeypatch.setattr("peter.voice.audio.Microphone", BoomMic)

    p.run_voice()

    fake_speaker.shutdown.assert_called_once()
    p.run_text.assert_called_once()
    assert p.speaker is None


# ------------------------------------------------------- Microphone self-heal
class _FakeInputStream:
    """Minimal stand-in for sd.InputStream: tracks how many times a stream
    was opened, and lets a test flip `.active` to simulate the device
    disappearing without going through a real PortAudio callback."""

    def __init__(self, on_create=None, **kw):
        self.active = True
        self.callback = kw.get("callback")
        if on_create is not None:
            on_create()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_microphone_self_heals_after_the_stream_dies(monkeypatch):
    opens = {"n": 0}

    def on_create():
        opens["n"] += 1

    monkeypatch.setattr(
        audio_mod.sd, "InputStream",
        lambda **kw: _FakeInputStream(on_create=on_create, **kw),
    )

    mic = Microphone(device=0)
    mic.start()
    assert opens["n"] == 1

    # Simulate the device disappearing: the stream stops on its own.
    mic._stream.active = False
    mic._last_reconnect_attempt = 0.0  # bypass the min-interval gate for the test

    result = mic.read(timeout=0.01)

    assert result is None  # nothing queued during the outage, but no crash
    assert opens["n"] == 2  # reopened itself
    assert mic._stream.active is True


def test_microphone_reconnect_is_rate_limited(monkeypatch):
    opens = {"n": 0}

    def on_create():
        opens["n"] += 1

    monkeypatch.setattr(
        audio_mod.sd, "InputStream",
        lambda **kw: _FakeInputStream(on_create=on_create, **kw),
    )

    mic = Microphone(device=0)
    mic.start()
    assert opens["n"] == 1

    mic._stream.active = False
    mic.read(timeout=0.01)  # first failure: attempts immediately (gate starts at 0)
    mic.read(timeout=0.01)  # still dead, but too soon to retry again

    assert opens["n"] == 2  # not 3 — the second read did not reopen again


def test_microphone_mute_drops_frames_and_unmute_resumes(monkeypatch):
    captured = {}

    def make_stream(**kw):
        captured["callback"] = kw["callback"]
        return _FakeInputStream(**kw)

    monkeypatch.setattr(audio_mod.sd, "InputStream", make_stream)

    mic = Microphone(device=0)
    mic.start()
    cb = captured["callback"]
    frame = np.ones((FRAME_SAMPLES, 1), dtype=np.int16)

    cb(frame, FRAME_SAMPLES, None, None)
    assert mic.read(timeout=0.01) is not None

    mic.mute()
    cb(frame, FRAME_SAMPLES, None, None)
    assert mic.read(timeout=0.01) is None

    mic.unmute()
    cb(frame, FRAME_SAMPLES, None, None)
    assert mic.read(timeout=0.01) is not None


# --------------------------------------------------------- WakeWordDetector
def test_wake_word_detector_cooldown_suppresses_immediate_refires():
    det = WakeWordDetector.__new__(WakeWordDetector)
    det.model_name = "hey_jarvis"
    det.threshold = 0.5
    det.refractory_frames = 2
    det._cooldown = 0

    class FakeModel:
        def predict(self, frame):
            return {"hey_jarvis": 0.9}

    det._model = FakeModel()
    frame = np.zeros(1, dtype=np.int16)

    assert det.process(frame) is True  # fires
    assert det.process(frame) is False  # cooling down
    assert det.process(frame) is False  # still cooling down
    assert det.process(frame) is True  # cooldown elapsed — fires again


# ---------------------------------------------- SapiEngine barge-in caveat
def test_sapi_engine_logs_the_barge_in_caveat(monkeypatch, caplog):
    import pyttsx3

    class FakePyttsx3Engine:
        def setProperty(self, *a, **kw):
            pass

    monkeypatch.setattr(pyttsx3, "init", lambda name: FakePyttsx3Engine())

    with caplog.at_level("INFO", logger="peter.voice.tts"):
        tts_mod.SapiEngine(rate=185)

    assert any("barge-in" in rec.message for rec in caplog.records)


# ------------------------------------------------------- Speaker (threaded)
class _FakeOutputStream:
    def __init__(self, written: list, **kw):
        self._written = written

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, block) -> None:
        self._written.append(block)
        time.sleep(0.005)  # gives a concurrent stop() a real window to land

    def abort(self) -> None:
        pass


def test_speaker_worker_thread_plays_queued_sentences_and_reports_first_audio(monkeypatch):
    written: list = []
    monkeypatch.setattr(tts_mod.sd, "OutputStream", lambda **kw: _FakeOutputStream(written))
    monkeypatch.setattr(tts_mod, "get_config", lambda: Config())

    class FakeEngine:
        sample_rate = 22050

        def stream(self, text):
            yield np.zeros(4, dtype=np.float32)
            yield np.ones(4, dtype=np.float32)

    speaker = Speaker(engine=FakeEngine())
    try:
        speaker.say("Hello there. Second sentence.")
        assert speaker.wait_until_idle(timeout=5.0) is True
        assert len(written) == 4  # two sentences, two blocks each

        first_audio_ms = speaker.wait_for_first_audio(timeout=1.0)
        assert first_audio_ms is not None
        assert first_audio_ms >= 0
    finally:
        speaker.shutdown()


def test_speaker_stop_aborts_playback_before_it_finishes(monkeypatch):
    written: list = []
    monkeypatch.setattr(tts_mod.sd, "OutputStream", lambda **kw: _FakeOutputStream(written))
    monkeypatch.setattr(tts_mod, "get_config", lambda: Config())

    class ManyBlocksEngine:
        sample_rate = 22050

        def stream(self, text):
            for i in range(500):
                yield np.full(4, float(i), dtype=np.float32)

    speaker = Speaker(engine=ManyBlocksEngine())
    try:
        speaker.say("A long sentence that will be interrupted partway through.")

        deadline = time.monotonic() + 2.0
        while not written and time.monotonic() < deadline:
            time.sleep(0.005)
        assert written  # at least the first block landed before we interrupt

        speaker.stop()
        assert speaker.wait_until_idle(timeout=5.0) is True

        assert 0 < len(written) < 500  # cut off partway, not the full utterance
    finally:
        speaker.shutdown()
