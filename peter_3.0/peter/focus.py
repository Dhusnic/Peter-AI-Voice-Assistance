"""Focus mode — a timed block that mutes the system, then restores it.

The restore is scheduled through the same persistent APScheduler jobstore
reminders use, with the volume to restore back to *baked into the job's own
arguments*. That matters: if Peter gets restarted mid-session, the scheduled
restore still fires with the right value even though the in-process
`_active` state below is gone. What does not survive a restart is only
`focus_status()` and ending the session early — the actual system restore
happens regardless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from peter.core.notify import notify
from peter.integrations.desktop import volume

log = logging.getLogger(__name__)

_JOB_ID = "focus-session-restore"


@dataclass
class FocusState:
    label: str
    started_at: datetime
    ends_at: datetime
    previous_volume: int | None


_active: FocusState | None = None


def start(minutes: float, label: str) -> str:
    """Start a focus session: mute, schedule the restore, remember the state."""
    global _active
    from peter.core.services import services

    if _active is not None:
        left = max(
            0, round((_active.ends_at - datetime.now().astimezone()).total_seconds() / 60)
        )
        return f"Already in a focus session ({_active.label or 'unnamed'}), {left} minute(s) left."

    minutes = max(1.0, float(minutes))
    label = label.strip()
    now = datetime.now().astimezone()
    ends_at = now + timedelta(minutes=minutes)

    previous = volume.get()
    muted_note = ""
    if previous is not None:
        volume.set(0)
        muted_note = " Muting the volume."

    container = services()
    container.require_scheduler().add_one_off_job(
        job_id=_JOB_ID,
        when=ends_at,
        func=complete_focus_session,
        args=[previous, label, now.isoformat()],
        name=f"focus: {label or 'session'}",
    )
    _active = FocusState(label=label, started_at=now, ends_at=ends_at, previous_volume=previous)

    named = f" on {label}" if label else ""
    return f"Starting a {minutes:g}-minute focus session{named}.{muted_note}"


def end() -> str:
    """End the current session early, right now, and restore volume."""
    global _active
    from peter.core.services import services

    if _active is None:
        return "No focus session running."

    state = _active
    _active = None
    services().require_scheduler().cancel(_JOB_ID)

    text = _restore_and_summarize(
        state.previous_volume, state.label, state.started_at, services(), early=True
    )
    log.info("focus: %s", text)
    return text


def status() -> str:
    if _active is None:
        return "No focus session running."
    left = max(
        0, round((_active.ends_at - datetime.now().astimezone()).total_seconds() / 60)
    )
    named = f" on {_active.label}" if _active.label else ""
    return f"Focus session{named}, {left} minute(s) left."


def complete_focus_session(previous_volume: int | None, label: str, started_iso: str) -> None:
    """Scheduler job target. Must stay importable at this exact path."""
    global _active
    from peter.core.services import services

    started = datetime.fromisoformat(started_iso)
    _active = None
    container = services()
    text = _restore_and_summarize(previous_volume, label, started, container, early=False)
    log.info("focus: %s", text)
    container.say(text)
    notify("Peter — focus session", text)


def _restore_and_summarize(
    previous_volume: int | None, label: str, started_at: datetime, container, *, early: bool
) -> str:
    """Put the volume back, log an episode, and build the summary text.

    Pure side-effect-plus-return, deliberately not speaking anything itself —
    the two callers above need different delivery: the scheduled completion
    speaks proactively, the manual end() just returns text for the tool
    result the live conversation is already about to read aloud.
    """
    if previous_volume is not None:
        volume.set(previous_volume)

    elapsed = max(0, round((datetime.now().astimezone() - started_at).total_seconds() / 60))
    named = f" on {label}" if label else ""
    verb = "ended early" if early else "is done"
    text = f"Your focus session{named} {verb} after {elapsed} minute(s)."
    if previous_volume is not None:
        text += f" Volume's back to {previous_volume}%."

    try:
        container.require_memory().add_episode(
            f"Focus session{named} ran {elapsed} minute(s), "
            f"{'stopped early' if early else 'completed'}."
        )
    except Exception:
        log.debug("focus: could not record episode", exc_info=True)

    return text
