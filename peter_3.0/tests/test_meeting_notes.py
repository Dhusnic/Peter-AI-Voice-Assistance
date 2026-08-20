"""Recording, transcribing and summarising a meeting.

Two things carry real risk here and both are tested hard: the session state
machine (a recorder left running, or a second recording started over a live
one, loses audio irrecoverably), and the fact that transcription must never
block a conversation — an hour of audio takes minutes, and a tool call that
takes minutes is a hang.
"""

import wave
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from peter import meeting_notes
from peter.integrations.desktop.recorder import Source, duration_seconds


class FakeRecorder:
    """Stands in for the sounddevice-backed Recorder."""

    instances: list["FakeRecorder"] = []

    def __init__(self, sample_rate=16000, capture_system_audio=True, fail=False,
                 kind="system audio"):
        self.sample_rate = sample_rate
        self.capture_system_audio = capture_system_audio
        self.fail = fail
        self.kind = kind
        self.path = None
        self.stopped = False
        FakeRecorder.instances.append(self)

    def start(self, path):
        if self.fail:
            raise RuntimeError("no audio device")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A real WAV, big enough to pass the "did anything land" check.
        with wave.open(str(self.path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x01" * 2000)
        return Source(name="Speakers", kind=self.kind, sample_rate=16000, channels=2)

    def stop(self):
        self.stopped = True
        return self.path


@pytest.fixture(autouse=True)
def _clear_session():
    meeting_notes._recorder = None
    meeting_notes._session = None
    FakeRecorder.instances.clear()
    yield
    meeting_notes._recorder = None
    meeting_notes._session = None


@pytest.fixture
def recording(container, tmp_path, monkeypatch):
    """A container whose recordings land in tmp_path, with a fake recorder."""
    monkeypatch.setattr(
        type(container.config), "recordings_dir",
        property(lambda self: tmp_path / "recordings"),
    )
    monkeypatch.setattr("peter.integrations.desktop.recorder.Recorder", FakeRecorder)
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    # Nothing in these tests should reach a real transcription.
    monkeypatch.setattr(
        meeting_notes, "_process",
        lambda path, label: said.append(f"processed {path.name}"),
    )
    return SimpleNamespace(container=container, said=said, dir=tmp_path / "recordings")


# ------------------------------------------------------------------ starting
def test_starting_names_the_source_it_captured_from(recording):
    result = meeting_notes.start("sprint planning")

    assert "Recording sprint planning" in result
    assert "system audio" in result


def test_a_microphone_fallback_says_what_it_will_miss(recording, monkeypatch):
    """Half a meeting transcribed is worth having, but the user has to know
    which half."""
    monkeypatch.setattr(
        "peter.integrations.desktop.recorder.Recorder",
        lambda **kw: FakeRecorder(kind="microphone", **kw),
    )

    result = meeting_notes.start("standup")

    assert "your side of the conversation" in result


def test_a_second_recording_cannot_start_over_a_live_one(recording):
    meeting_notes.start("first")

    result = meeting_notes.start("second")

    assert "Already recording first" in result
    assert len(FakeRecorder.instances) == 1


def test_a_failed_start_leaves_no_session_behind(recording, monkeypatch):
    monkeypatch.setattr(
        "peter.integrations.desktop.recorder.Recorder",
        lambda **kw: FakeRecorder(fail=True, **kw),
    )

    result = meeting_notes.start("doomed")

    assert "could not start recording" in result
    assert meeting_notes.status() == "Nothing is being recorded."


def test_recording_can_be_switched_off(recording, monkeypatch):
    monkeypatch.setattr(recording.container.config.integrations.recorder,
                        "enabled", False)
    assert "switched off" in meeting_notes.start("anything")


def test_the_file_is_named_after_the_label(recording):
    meeting_notes.start("Sprint Planning!")
    assert "sprint-planning" in FakeRecorder.instances[0].path.name


def test_an_unlabelled_recording_still_gets_a_name(recording):
    meeting_notes.start("")
    assert FakeRecorder.instances[0].path.name.endswith("-recording.wav")


# -------------------------------------------------------------------- status
def test_status_with_nothing_running():
    assert meeting_notes.status() == "Nothing is being recorded."


def test_status_names_what_is_being_recorded(recording):
    meeting_notes.start("sprint planning")
    assert "Recording sprint planning" in meeting_notes.status()


# ------------------------------------------------------------------ stopping
def test_stopping_hands_off_to_the_background_and_returns_at_once(recording):
    """The whole point: an hour of audio must not block the conversation."""
    meeting_notes.start("sprint planning")

    result = meeting_notes.stop()

    assert "Transcribing now" in result
    assert "tell you when the notes are ready" in result


def test_stopping_closes_the_recorder(recording):
    meeting_notes.start("x")
    recorder = FakeRecorder.instances[0]

    meeting_notes.stop()

    assert recorder.stopped is True
    assert meeting_notes.status() == "Nothing is being recorded."


def test_stopping_when_nothing_is_recording(recording):
    assert meeting_notes.stop() == "Nothing is being recorded."


def test_an_empty_recording_is_reported_rather_than_transcribed(recording, monkeypatch):
    class SilentRecorder(FakeRecorder):
        def start(self, path):
            source = super().start(path)
            self.path.write_bytes(b"")  # nothing landed
            return source

    monkeypatch.setattr(
        "peter.integrations.desktop.recorder.Recorder",
        lambda **kw: SilentRecorder(**kw),
    )
    meeting_notes.start("x")

    result = meeting_notes.stop()

    assert "nothing was captured" in result


def test_a_stopped_session_can_be_started_again(recording):
    meeting_notes.start("first")
    meeting_notes.stop()

    assert "Recording second" in meeting_notes.start("second")


# ---------------------------------------------------------------- summarising
class FakeProvider:
    def __init__(self, reply="## Summary\nWe agreed to ship on Friday."):
        self.reply = reply
        self.sent = ""
        self.closed = False

    def add_user(self, text):
        self.sent = text

    def complete(self, tools):
        from peter.llm.base import ProviderResponse

        assert tools == []
        return ProviderResponse(text=self.reply)

    def close(self):
        self.closed = True


def test_the_summary_prompt_is_given_the_transcript(container, monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr("peter.llm.factory.build_provider", lambda *a, **k: provider)

    notes = meeting_notes.summarise("we talked about the migration", "sprint planning")

    assert "ship on Friday" in notes
    assert "we talked about the migration" in provider.sent
    assert "Meeting: sprint planning" in provider.sent
    assert provider.closed is True


def test_a_very_long_transcript_is_trimmed_not_refused(container, monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr("peter.llm.factory.build_provider", lambda *a, **k: provider)

    meeting_notes.summarise("word " * 40000)

    assert "[transcript truncated]" in provider.sent


def test_a_model_failure_still_points_at_the_saved_transcript(container, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("peter.llm.factory.build_provider", boom)

    notes = meeting_notes.summarise("something was said")

    assert "could not be summarised" in notes
    assert ".txt file" in notes


# ------------------------------------------------------- the background pass
def test_processing_writes_a_transcript_and_notes_and_remembers_them(
    container, tmp_path, monkeypatch
):
    audio = tmp_path / "20260820-1000-sprint.wav"
    audio.write_bytes(b"fake audio")
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    monkeypatch.setattr(
        meeting_notes, "transcribe", lambda path: "we agreed to ship on friday"
    )
    monkeypatch.setattr(
        "peter.llm.factory.build_provider", lambda *a, **k: FakeProvider()
    )

    meeting_notes._process(audio, "sprint planning")

    assert audio.with_suffix(".txt").read_text(encoding="utf-8") == (
        "we agreed to ship on friday"
    )
    assert "ship on Friday" in audio.with_suffix(".md").read_text(encoding="utf-8")
    assert any("Meeting notes" in e for e in container.memory.recent_episodes(limit=1))
    assert any("notes from sprint planning are ready" in s.lower() for s in said)


def test_a_transcription_failure_is_reported_not_raised(container, tmp_path, monkeypatch):
    audio = tmp_path / "broken.wav"
    audio.write_bytes(b"fake audio")
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))

    def boom(path):
        raise RuntimeError("whisper exploded")

    monkeypatch.setattr(meeting_notes, "transcribe", boom)

    meeting_notes._process(audio, "x")  # must not raise

    assert any("could not transcribe" in s for s in said)


def test_a_silent_recording_says_so_rather_than_summarising_nothing(
    container, tmp_path, monkeypatch
):
    audio = tmp_path / "silent.wav"
    audio.write_bytes(b"fake audio")
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    monkeypatch.setattr(meeting_notes, "transcribe", lambda path: "   ")
    monkeypatch.setattr(
        "peter.llm.factory.build_provider",
        lambda *a, **k: pytest.fail("should not summarise silence"),
    )

    meeting_notes._process(audio, "x")

    assert any("came out silent" in s for s in said)


# ------------------------------------------------------------------ browsing
def test_recordings_are_listed_newest_first(container, tmp_path, monkeypatch):
    import os
    import time

    folder = tmp_path / "recordings"
    folder.mkdir()
    for index, name in enumerate(["old.wav", "new.wav"]):
        path = folder / name
        path.write_bytes(b"x")
        os.utime(path, (time.time() + index, time.time() + index))

    monkeypatch.setattr(
        type(container.config), "recordings_dir", property(lambda self: folder)
    )

    assert [p.name for p in meeting_notes.recordings()] == ["new.wav", "old.wav"]


def test_no_recordings_directory_yet_lists_nothing(container, tmp_path, monkeypatch):
    monkeypatch.setattr(
        type(container.config), "recordings_dir",
        property(lambda self: tmp_path / "never-created"),
    )
    assert meeting_notes.recordings() == []


def test_a_recording_is_found_by_part_of_its_name(container, tmp_path, monkeypatch):
    folder = tmp_path / "recordings"
    folder.mkdir()
    (folder / "20260820-1000-sprint-planning.wav").write_bytes(b"x")
    monkeypatch.setattr(
        type(container.config), "recordings_dir", property(lambda self: folder)
    )

    assert meeting_notes.find_recording("sprint") is not None
    assert meeting_notes.find_recording("nothing like it") is None
    assert meeting_notes.find_recording("") is None


# ------------------------------------------------------------------- helpers
def test_the_first_paragraph_skips_markdown_headings():
    notes = "## Summary\n\nWe agreed to ship on Friday.\n\n## Decisions\n\n- ship it"
    assert meeting_notes._first_paragraph(notes) == "We agreed to ship on Friday."


def test_the_first_paragraph_of_empty_notes_is_still_speakable():
    assert meeting_notes._first_paragraph("") == "The notes are saved."


def test_a_label_becomes_a_filesystem_safe_slug():
    assert meeting_notes._slug("Sprint Planning: Q3!") == "sprint-planning-q3"
    assert meeting_notes._slug("") == ""


def test_elapsed_minutes_rounds_to_whole_minutes():
    started = datetime.now().astimezone() - timedelta(minutes=90, seconds=20)
    assert meeting_notes._elapsed_minutes(started) == 90


# ---------------------------------------------------------------- wav timing
def test_duration_is_read_from_the_wav_header(tmp_path):
    path = tmp_path / "one-second.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 16000)

    assert duration_seconds(path) == pytest.approx(1.0)


def test_duration_of_something_that_is_not_a_wav_is_zero(tmp_path):
    broken = tmp_path / "not-audio.wav"
    broken.write_bytes(b"definitely not a wav")
    assert duration_seconds(broken) == 0.0
