"""The morning briefing.

Assembles one spoken paragraph from the calendar, inbox, reminders and to-do
list. Runs on a schedule, and can be asked for at any time.

**Every section is independently guarded.** If the wifi is down, or Google
authorisation lapsed overnight, the briefing still delivers the parts it can and
says one short line about the part it could not. A briefing that raises because
the inbox was unreachable is worse than no briefing at all — it fails at exactly
the moment you are not looking at the screen.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from peter.core.errors import IntegrationError, NotConfiguredError, PeterError

log = logging.getLogger(__name__)


def _greeting(name: str, now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        part = "Morning"
    elif hour < 17:
        part = "Afternoon"
    else:
        part = "Evening"
    return f"{part}, {name}."


def _calendar_section(config, max_events: int) -> str:
    from peter.core.services import services

    today = datetime.now().astimezone()
    events = services().calendar().events_on(today, limit=max_events)
    if not events:
        return "Nothing on the calendar today."

    timed = [e for e in events if not e.all_day]
    all_day = [e for e in events if e.all_day]

    parts = []
    if timed:
        first = timed[0]
        if len(timed) == 1:
            parts.append(f"One thing today: {first.summary} at {first.when()}.")
        else:
            rest = ", then ".join(f"{e.summary} at {e.when()}" for e in timed[1:3])
            parts.append(
                f"{len(timed)} things today. First, {first.summary} at "
                f"{first.when()}, then {rest}."
            )
    if all_day:
        parts.append(f"All day: {', '.join(e.summary for e in all_day)}.")
    return " ".join(parts)


def _mail_section(config, max_emails: int) -> str:
    from peter.core.services import services

    mail = services().mail()
    count = mail.count_unread()
    if count == 0:
        return "Inbox is clear."

    recent = mail.list_messages("UNSEEN", limit=max_emails)
    senders: list[str] = []
    for item in recent:
        if item.sender not in senders:
            senders.append(item.sender)

    if not senders:
        return f"{count} unread."
    named = ", ".join(senders[:3])
    tail = " and others" if len(senders) > 3 else ""
    return f"{count} unread, from {named}{tail}."


def _reminders_section() -> str:
    from peter.core.services import services

    jobs = services().require_scheduler().list_jobs()
    if not jobs:
        return ""
    today = datetime.now().astimezone().date()
    due_today = [
        j for j in jobs
        if j.get("next_run_at") and j["next_run_at"].date() == today
    ]
    if not due_today:
        return f"{len(jobs)} reminders pending, none today."
    names = ", ".join(j["text"] for j in due_today[:3])
    return f"{len(due_today)} reminders today: {names}."


def _todos_section() -> str:
    from peter.core.services import services

    items = services().require_memory().list_todos()
    if not items:
        return ""
    if len(items) == 1:
        return f"One thing on your list: {items[0][1]}."
    names = ", ".join(text for _, text, _ in items[:3])
    return f"{len(items)} on your to-do list: {names}."


_SECTIONS = {
    "calendar": lambda cfg: _calendar_section(cfg, cfg.max_events),
    "mail": lambda cfg: _mail_section(cfg, cfg.max_emails),
    "reminders": lambda cfg: _reminders_section(),
    "todos": lambda cfg: _todos_section(),
}


def build_briefing() -> str:
    """Assemble the briefing text. Never raises."""
    from peter.core.services import services

    container = services()
    config = container.config
    briefing_cfg = config.integrations.briefing
    now = datetime.now().astimezone()

    lines = [_greeting(config.app.user_name, now)]
    unreachable: list[str] = []
    unconfigured: list[str] = []

    for name in briefing_cfg.include:
        section = _SECTIONS.get(name)
        if section is None:
            log.warning("unknown briefing section %r in config.yml", name)
            continue
        try:
            text = section(briefing_cfg)
            if text:
                lines.append(text)
        except NotConfiguredError as exc:
            # Nothing is broken — this feature was never set up. Saying "I
            # could not reach your calendar" would send the user hunting for a
            # network fault that does not exist.
            log.info("briefing section %s skipped: %s", name, exc)
            unconfigured.append(name)
        except IntegrationError as exc:
            log.info("briefing section %s unavailable: %s", name, exc)
            unreachable.append(name)
        except PeterError as exc:
            log.info("briefing section %s failed: %s", name, exc)
            unreachable.append(name)
        except Exception:
            log.exception("briefing section %s crashed", name)
            unreachable.append(name)

    if unreachable:
        lines.append(f"I could not reach {_join(unreachable)} just now.")
    if unconfigured:
        verb = "is" if len(unconfigured) == 1 else "are"
        lines.append(f"{_join(unconfigured).capitalize()} {verb} not set up yet.")

    return " ".join(lines)


def _join(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} or {names[-1]}"


def deliver_briefing() -> None:
    """Scheduler job target. Must stay importable at this exact path.

    APScheduler stores a reference to the callable by module path, so renaming
    or moving this function silently breaks every already-scheduled briefing in
    the jobstore.
    """
    from peter.core.services import services

    try:
        text = build_briefing()
    except Exception:
        log.exception("briefing failed entirely")
        return

    log.info("briefing: %s", text)
    services().say(text)


def schedule_briefing(scheduler, config) -> None:
    """Install (or re-install) the daily briefing job."""
    cfg = config.integrations.briefing
    if not cfg.enabled:
        return

    scheduler.add_daily_job(
        job_id="daily-briefing",
        hour=cfg.hour,
        minute=cfg.minute,
        func=deliver_briefing,
        name="morning briefing",
    )
    log.info("daily briefing scheduled for %s", cfg.time)


def next_briefing_time(config) -> datetime:
    cfg = config.integrations.briefing
    now = datetime.now().astimezone()
    target = now.replace(hour=cfg.hour, minute=cfg.minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)
