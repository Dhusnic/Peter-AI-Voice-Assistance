"""Meeting-prep nudge — proactive, calendar + memory + scheduler.

Polls the calendar every few minutes rather than pre-scheduling one job per
event: events get created, moved and cancelled outside Peter's control, and a
poll naturally picks up the current truth every time instead of firing a
stale nudge for a meeting that got rescheduled an hour ago.

Same degrade-never-crash discipline as briefing.py: a poll where the calendar
is unreachable just tries again next time, never raises out of the scheduler.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from peter.core.errors import NotConfiguredError, PeterError
from peter.core.notify import notify

log = logging.getLogger(__name__)

# event id -> when it was announced (epoch seconds). Pruned every poll so
# this never grows past roughly a day's worth of meetings. Lost on restart,
# which just means — at worst — one meeting gets nudged twice if Peter
# restarts inside its own lead window; not worth persisting for that.
_notified: dict[str, float] = {}
_PRUNE_AFTER_SECONDS = 24 * 3600


def _prune() -> None:
    cutoff = time.time() - _PRUNE_AFTER_SECONDS
    for event_id in [k for k, v in _notified.items() if v < cutoff]:
        del _notified[event_id]


def _join_and(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _announce(event, container) -> None:
    now = datetime.now().astimezone()
    minutes = max(1, round((event.start - now).total_seconds() / 60))
    parts = [
        f"You have {event.summary} in {minutes} minute{'s' if minutes != 1 else ''}."
    ]
    if event.attendees:
        parts.append(f"It's with {_join_and(event.attendees)}.")
    if event.location:
        parts.append(f"At {event.location}.")

    try:
        topic = " ".join([event.summary, *event.attendees])
        note = container.require_memory().related_note(topic)
    except Exception:
        note = None
    if note:
        parts.append(f"Your last related note: {note}")

    text = " ".join(parts)
    log.info("meeting prep: %s", text)
    container.say(text)
    notify("Peter — upcoming meeting", text)


def check_meeting_prep() -> None:
    """Scheduler job target. Must stay importable at this exact path."""
    from peter.core.services import services

    container = services()
    cfg = container.config.integrations.meeting_prep
    if not cfg.enabled:
        return

    try:
        calendar = container.calendar()
        now = datetime.now().astimezone()
        events = calendar.list_events(
            now, now + timedelta(minutes=cfg.lead_minutes), limit=10
        )
    except NotConfiguredError:
        return  # never set up — nothing broken, nothing to say
    except PeterError:
        log.info("meeting prep: calendar unreachable this poll", exc_info=True)
        return
    except Exception:
        log.exception("meeting prep: unexpected failure")
        return

    _prune()
    now = datetime.now().astimezone()
    for event in events:
        if event.all_day or event.start is None or event.start < now:
            continue
        if event.id in _notified:
            continue
        _notified[event.id] = time.time()
        try:
            _announce(event, container)
        except Exception:
            log.exception("meeting prep: failed to announce %s", event.id)


def schedule_meeting_prep(scheduler, config) -> None:
    """Install (or re-install) the meeting-prep poll."""
    cfg = config.integrations.meeting_prep
    if not cfg.enabled:
        return
    scheduler.add_interval_job(
        job_id="meeting-prep-poll",
        minutes=cfg.poll_interval_minutes,
        func=check_meeting_prep,
        name="meeting prep watcher",
    )
    log.info(
        "meeting prep watcher: polling every %d minute(s), %d minute lead",
        cfg.poll_interval_minutes, cfg.lead_minutes,
    )
