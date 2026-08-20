"""Recording and meeting-notes tools.

Recording someone is a consequential act, so the wording of these matters:
Peter only ever records when asked to, says out loud when it starts and what
it is capturing from, and never starts one silently. The one automatic path —
`integrations.recorder.auto_record_meetings` — is off by default and still
announces itself when it fires.
"""

from __future__ import annotations

from datetime import datetime

from peter.agent.registry import peter_tool
from peter.core.services import services


@peter_tool(tier="write")
def start_recording(label: str = "") -> str:
    """Start recording audio, for a meeting or a call.

    Captures what the speakers are playing (so, the other people on the call)
    where the system allows it, and falls back to the microphone otherwise.
    Stop it with stop_recording, which then transcribes and summarises in the
    background.

    Args:
        label: What this is, e.g. "sprint planning". Used to name the file and
            to head the notes.
    """
    from peter import meeting_notes

    return meeting_notes.start(label)


@peter_tool(tier="write")
def stop_recording() -> str:
    """Stop the current recording and transcribe it.

    Returns immediately. Transcription and the summary run in the background,
    and Peter announces the notes when they are ready.
    """
    from peter import meeting_notes

    return meeting_notes.stop()


@peter_tool(tier="read")
def recording_status() -> str:
    """Report whether anything is being recorded, and for how long."""
    from peter import meeting_notes

    return meeting_notes.status()


@peter_tool(tier="read")
def list_recordings(limit: int = 10) -> str:
    """List saved recordings, newest first, and whether notes exist for each.

    Args:
        limit: How many to list.
    """
    from peter import meeting_notes
    from peter.integrations.desktop.recorder import duration_seconds

    found = meeting_notes.recordings()[: max(1, limit)]
    if not found:
        return "There are no recordings yet."

    lines = []
    for path in found:
        minutes = duration_seconds(path) / 60
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d %b %H:%M")
        notes = "notes ready" if path.with_suffix(".md").exists() else "no notes yet"
        lines.append(f"{path.stem} — {when}, {minutes:.0f} min, {notes}")
    return "\n".join(lines)


@peter_tool(tier="read")
def read_meeting_notes(which: str) -> str:
    """Read back the notes from a past recording.

    Args:
        which: Part of the recording's name, e.g. "sprint planning" or a date
            like "20 Aug". Check list_recordings if unsure.
    """
    from peter import meeting_notes

    path = meeting_notes.find_recording(which)
    if path is None:
        return f"No recording matches {which!r}. Try list_recordings."

    notes = path.with_suffix(".md")
    if notes.exists():
        return notes.read_text(encoding="utf-8")

    transcript = path.with_suffix(".txt")
    if transcript.exists():
        return (
            "There are no notes for that one, but here is the transcript:\n\n"
            + transcript.read_text(encoding="utf-8")[:4000]
        )
    return f"{path.stem} has not been transcribed yet."


@peter_tool(tier="write")
def summarise_recording(which: str) -> str:
    """Transcribe and summarise a past recording again, in the background.

    Use this for a recording that was never transcribed, or when the notes
    should be redone.

    Args:
        which: Part of the recording's name. Check list_recordings if unsure.
    """
    import threading

    from peter import meeting_notes

    path = meeting_notes.find_recording(which)
    if path is None:
        return f"No recording matches {which!r}. Try list_recordings."

    threading.Thread(
        target=meeting_notes._process,
        args=(path, path.stem),
        name="meeting-transcribe",
        daemon=True,
    ).start()
    return f"Transcribing {path.stem} now. I will tell you when the notes are ready."


@peter_tool(tier="read")
def audio_sources() -> str:
    """Report what recording would capture from on this machine.

    Useful when a recording came out with only your own voice on it: it says
    whether system-audio capture is actually available here.
    """
    cfg = services().config.integrations.recorder
    try:
        import sounddevice as sd
    except Exception as exc:
        return f"The audio stack is unavailable: {exc}"

    lines = []
    try:
        output = sd.query_devices(sd.default.device[1], "output")
        loopback = hasattr(sd, "WasapiSettings")
        lines.append(
            f"System audio: {output['name']}"
            + ("" if loopback else " — not capturable by this sounddevice build")
        )
    except Exception:
        lines.append("System audio: no default output device")

    try:
        source = sd.query_devices(sd.default.device[0], "input")
        lines.append(f"Microphone: {source['name']}")
    except Exception:
        lines.append("Microphone: no default input device")

    preference = (
        "system audio, falling back to the microphone"
        if cfg.capture_system_audio
        else "the microphone only (capture_system_audio is off)"
    )
    lines.append(f"Recording will use {preference}.")
    return "\n".join(lines)
