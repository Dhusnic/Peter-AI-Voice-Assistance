"""Recording a meeting, transcribing it locally, and summarising it.

The audio never leaves this machine. faster-whisper is already installed for
the wake-word pipeline, so transcription is local and free; only the final
summary is a model call, and it goes over text, not audio.

**Transcription runs in the background, deliberately.** An hour of audio takes
minutes to transcribe even on a small model, and a tool call that blocks a
conversation for four minutes is not a tool, it is a hang. `stop_recording`
returns straight away and the summary arrives when it is ready — spoken,
pushed to your phone, and written into memory as an episode, which is what
makes the meeting-prep nudge able to say "your last conversation with Priya
was about the alert thresholds" weeks later.

The transcript and the notes are both written next to the audio, so nothing
here depends on a model call having succeeded.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_NOTES_SYSTEM = (
    "You write meeting notes from a raw, unlabelled transcript. The transcript "
    "comes from automatic speech recognition, so it has no speaker labels and "
    "contains mistakes — infer sensibly and do not invent detail that is not "
    "there. Produce, in this order and with these exact headings:\n"
    "## Summary — two or three sentences on what the meeting was about.\n"
    "## Decisions — what was actually decided, one bullet each. Omit the "
    "heading entirely if nothing was decided.\n"
    "## Action items — who agreed to do what, one bullet each, naming the "
    "person when the transcript makes it clear. Omit the heading if there are "
    "none.\n"
    "## Notes — anything else worth keeping, briefly.\n"
    "Be concise. No preamble, no closing remarks."
)

# How much transcript is sent for summarising. Roughly two hours of speech;
# beyond that the tail is trimmed rather than the call being refused, because
# a summary of most of a long meeting beats no summary at all.
_MAX_TRANSCRIPT_CHARS = 60000


@dataclass
class Session:
    label: str
    path: Path
    source: str
    started_at: datetime


_recorder = None
_session: Session | None = None
_lock = threading.Lock()


# --------------------------------------------------------------------- start
def start(label: str = "") -> str:
    """Begin recording. Returns what to say about it."""
    global _recorder, _session
    from peter.core.services import services

    with _lock:
        if _session is not None:
            return (
                f"Already recording {_session.label or 'a session'} — "
                f"{_elapsed_minutes(_session.started_at)} minute(s) so far."
            )

        config = services().config
        cfg = config.integrations.recorder
        if not cfg.enabled:
            return "Recording is switched off in config.yml."

        from peter.integrations.desktop.recorder import Recorder

        label = label.strip()
        stamp = datetime.now()
        slug = _slug(label) or "recording"
        path = config.recordings_dir / f"{stamp:%Y%m%d-%H%M}-{slug}.wav"

        recorder = Recorder(
            sample_rate=cfg.sample_rate,
            capture_system_audio=cfg.capture_system_audio,
        )
        try:
            source = recorder.start(path)
        except Exception as exc:
            log.exception("recording could not start")
            return f"I could not start recording: {exc}"

        _recorder = recorder
        _session = Session(
            label=label, path=path, source=source.spoken(), started_at=stamp.astimezone()
        )

    named = f" {label}" if label else ""
    caveat = (
        ""
        if source.kind == "system audio"
        else " Only the microphone was available, so this captures your side of "
        "the conversation, not theirs."
    )
    return f"Recording{named} from {source.spoken()}.{caveat}"


# ---------------------------------------------------------------------- stop
def stop() -> str:
    """Stop recording and start transcribing in the background."""
    global _recorder, _session
    from peter.core.services import services

    with _lock:
        if _session is None or _recorder is None:
            return "Nothing is being recorded."
        session, _session = _session, None
        recorder, _recorder = _recorder, None

    path = recorder.stop()
    minutes = _elapsed_minutes(session.started_at)

    if path is None or not path.exists() or path.stat().st_size < 1000:
        return f"Recording stopped after {minutes} minute(s), but nothing was captured."

    config = services().config
    if config.integrations.recorder.enabled:
        threading.Thread(
            target=_process,
            args=(path, session.label),
            name="meeting-transcribe",
            daemon=True,
        ).start()

    named = f" of {session.label}" if session.label else ""
    return (
        f"Stopped recording{named} after {minutes} minute(s). Transcribing now — "
        "I will tell you when the notes are ready."
    )


def status() -> str:
    with _lock:
        if _session is None:
            return "Nothing is being recorded."
        session = _session
    return (
        f"Recording {session.label or 'a session'} from {session.source}, "
        f"{_elapsed_minutes(session.started_at)} minute(s) so far."
    )


# ------------------------------------------------------- the background work
def _process(path: Path, label: str) -> None:
    """Transcribe, summarise, save, announce. Never raises — it is a thread."""
    from peter.core.notify import notify
    from peter.core.services import services

    container = services()
    started = time.monotonic()
    try:
        transcript = transcribe(path)
    except Exception:
        log.exception("transcription failed for %s", path.name)
        container.say(f"I could not transcribe {path.name}.")
        return

    if not transcript.strip():
        container.say(f"{path.name} came out silent — there was nothing to transcribe.")
        return

    path.with_suffix(".txt").write_text(transcript, encoding="utf-8")

    notes = summarise(transcript, label)
    notes_path = path.with_suffix(".md")
    notes_path.write_text(
        f"# {label or path.stem}\n\n_{datetime.now():%d %b %Y, %H:%M}_\n\n{notes}\n",
        encoding="utf-8",
    )

    named = label or path.stem
    try:
        container.require_memory().add_episode(
            f"Meeting notes — {named}: {_first_paragraph(notes)}"
        )
    except Exception:
        log.debug("could not record the meeting episode", exc_info=True)

    log.info(
        "transcribed %s in %.0fs (%d chars)",
        path.name, time.monotonic() - started, len(transcript),
    )
    spoken = f"Notes from {named} are ready. {_first_paragraph(notes)}"
    container.say(spoken)
    notify("Peter — meeting notes", f"{named}\n\n{notes[:1500]}")


def transcribe(path: Path) -> str:
    """Run faster-whisper over a recording. Blocking, and slow by nature."""
    from faster_whisper import WhisperModel

    from peter.core.services import services

    config = services().config
    cfg = config.integrations.recorder
    stt = config.voice.stt

    log.info("transcribing %s with %s", path.name, cfg.stt_model)
    model = WhisperModel(cfg.stt_model, device=stt.device, compute_type=stt.compute_type)
    segments, _info = model.transcribe(str(path), vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


def summarise(transcript: str, label: str = "") -> str:
    """Turn a transcript into notes. Degrades to the raw transcript on failure."""
    from peter.core.services import services
    from peter.llm import factory

    text = transcript[:_MAX_TRANSCRIPT_CHARS]
    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        text += "\n\n[transcript truncated]"
    header = f"Meeting: {label}\n\n" if label else ""

    try:
        provider = factory.build_provider(services().config, _NOTES_SYSTEM)
        try:
            provider.add_user(f"{header}Transcript:\n\n{text}")
            response = provider.complete([])
        finally:
            provider.close()
    except Exception:
        log.exception("could not summarise the transcript")
        return (
            "## Summary\nThe transcript could not be summarised — the model was "
            "unreachable. The full text is in the .txt file next to this one."
        )
    return (response.text or "").strip() or "## Summary\nThe model returned nothing."


# ------------------------------------------------------------------ browsing
def recordings() -> list[Path]:
    """Every recording, newest first."""
    from peter.core.services import services

    directory = services().config.recordings_dir
    if not directory.exists():
        return []
    return sorted(directory.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)


def find_recording(needle: str) -> Path | None:
    needle = needle.strip().lower()
    if not needle:
        return None
    for path in recordings():
        if needle in path.stem.lower():
            return path
    return None


# ------------------------------------------------------------------ helpers
def _elapsed_minutes(started: datetime) -> int:
    return max(0, round((datetime.now().astimezone() - started).total_seconds() / 60))


def _first_paragraph(notes: str) -> str:
    """The summary sentence out of the markdown, for speaking aloud."""
    for block in notes.split("\n\n"):
        cleaned = " ".join(
            line for line in block.splitlines() if not line.strip().startswith("#")
        ).strip()
        if cleaned:
            return cleaned[:400]
    return "The notes are saved."


def _slug(text: str) -> str:
    """A filename-safe label. Runs of punctuation collapse to one hyphen —
    "Sprint Planning: Q3!" has two non-alphanumerics in a row, and without
    the empty-part filter that becomes a double hyphen in the filename."""
    keep = "".join(c if c.isalnum() else "-" for c in text.lower())
    return "-".join(part for part in keep.split("-") if part)[:40].strip("-")
