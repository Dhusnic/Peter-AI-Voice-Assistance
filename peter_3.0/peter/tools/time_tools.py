"""Time, reminders, alarms, timers, and the to-do list.

Absolute times are passed as ISO 8601 strings rather than free text ("next
Tuesday"), because Claude is given the current local time on every turn and is
far better at that arithmetic than any date-parsing library. Keeping the tool
strict means a misheard "Tuesday" fails loudly instead of silently scheduling
something for the wrong week.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from peter.agent.registry import peter_tool
from peter.core.services import services


def _parse_iso(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret naive input as local time
    return dt


@peter_tool(tier="read")
def get_current_time(timezone: str = "local") -> str:
    """Get the current date and time.

    Args:
        timezone: "local" for this machine's timezone, or an IANA timezone name
            such as "Asia/Kolkata" or "UTC".
    """
    if timezone.strip().lower() in ("", "local"):
        now = datetime.now().astimezone()
    else:
        try:
            now = datetime.now(ZoneInfo(timezone.strip()))
        except (ZoneInfoNotFoundError, ValueError):
            return f"Unknown timezone {timezone!r}."
    return now.strftime("%A, %d %B %Y, %I:%M %p %Z")


@peter_tool(tier="write")
def set_reminder(text: str, at_iso: str) -> str:
    """Schedule a one-off reminder to be spoken aloud at a specific time.

    Compute the absolute time yourself from the current time given in the
    conversation. Reminders persist across restarts.

    Args:
        text: What to say when it fires, phrased as the reminder itself
            (e.g. "call Amma", not "remind the user to call Amma").
        at_iso: When to fire, ISO 8601 local time, e.g. "2026-08-18T19:30:00".
    """
    try:
        when = _parse_iso(at_iso)
    except ValueError:
        return f"Could not read {at_iso!r} as an ISO 8601 datetime."

    if when <= datetime.now().astimezone():
        return f"{at_iso} is in the past. Ask the user which time they meant."

    job_id = services().require_scheduler().add_once(when, text)
    return f"Reminder set for {when.strftime('%a %d %b, %I:%M %p')} (id {job_id})."


@peter_tool(tier="write")
def set_timer(text: str, minutes: float) -> str:
    """Set a countdown timer that speaks when it expires.

    Args:
        text: What to say when it goes off, e.g. "the rice is done".
        minutes: How many minutes from now. Fractions are allowed (0.5 = 30s).
    """
    if minutes <= 0:
        return "Timer length must be greater than zero."
    job_id, when = services().require_scheduler().add_in(
        timedelta(minutes=minutes), text
    )
    return f"Timer set for {when.strftime('%I:%M %p')} (id {job_id})."


@peter_tool(tier="write")
def set_alarm(text: str, hour: int, minute: int, repeat_daily: bool = False) -> str:
    """Set an alarm at a clock time.

    Args:
        text: What to say when it fires.
        hour: Hour on a 24-hour clock, 0-23.
        minute: Minute, 0-59.
        repeat_daily: True to repeat every day at this time, False for one-off.
    """
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return "Hour must be 0-23 and minute 0-59."

    scheduler = services().require_scheduler()
    if repeat_daily:
        job_id, when = scheduler.add_daily(hour, minute, text)
        return f"Daily alarm set for {hour:02d}:{minute:02d} (id {job_id})."

    now = datetime.now().astimezone()
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    job_id = scheduler.add_once(when, text)
    return f"Alarm set for {when.strftime('%a %d %b, %I:%M %p')} (id {job_id})."


@peter_tool(tier="read")
def list_reminders() -> str:
    """List every pending reminder, timer and alarm with its next run time."""
    jobs = services().require_scheduler().list_jobs()
    if not jobs:
        return "Nothing scheduled."
    return "\n".join(f"[{j['id']}] {j['text']} — {j['next_run']}" for j in jobs)


@peter_tool(tier="write")
def cancel_reminder(job_id: str = "", matching_text: str = "") -> str:
    """Cancel a scheduled reminder, timer or alarm.

    Give either the exact id from list_reminders, or some of its text to search
    for. If the text matches more than one, nothing is cancelled and the
    matches are listed so you can ask the user which they meant.

    Args:
        job_id: Exact scheduler id.
        matching_text: Part of the reminder's text, used when no id is known.
    """
    scheduler = services().require_scheduler()

    if job_id.strip():
        return (
            f"Cancelled {job_id}."
            if scheduler.cancel(job_id.strip())
            else f"No scheduled item with id {job_id!r}."
        )

    if not matching_text.strip():
        return "Give either job_id or matching_text."

    matches = scheduler.find_by_text(matching_text)
    if not matches:
        return f"Nothing scheduled matching {matching_text!r}."
    if len(matches) > 1:
        listing = "\n".join(
            f"[{j['id']}] {j['text']} — {j['next_run']}"
            for j in scheduler.list_jobs()
            if j["id"] in matches
        )
        return f"That matches {len(matches)} items — ask which one:\n{listing}"

    scheduler.cancel(matches[0])
    return f"Cancelled {matches[0]}."


@peter_tool(tier="write")
def add_todo(text: str) -> str:
    """Add an item to the user's to-do list.

    A to-do has no time attached. If the user gave a time, use set_reminder
    instead — or both, if they want to be nudged about a listed task.

    Args:
        text: The task, e.g. "submit the DBMS assignment".
    """
    todo_id = services().require_memory().add_todo(text)
    return f"Added to-do #{todo_id}: {text}"


@peter_tool(tier="read")
def list_todos(include_done: bool = False) -> str:
    """List to-do items.

    Args:
        include_done: True to include already-completed items.
    """
    items = services().require_memory().list_todos(include_done=include_done)
    if not items:
        return "The to-do list is empty."
    return "\n".join(
        f"#{i} {'[done] ' if done else ''}{text}" for i, text, done in items
    )


@peter_tool(tier="write")
def complete_todo(todo_id: int = 0, matching_text: str = "") -> str:
    """Mark a to-do item as done.

    Give either its number from list_todos, or some of its text.

    Args:
        todo_id: The item's number.
        matching_text: Part of the item's text, used when no number is known.
    """
    memory = services().require_memory()

    if todo_id:
        return (
            f"Marked #{todo_id} done."
            if memory.complete_todo(todo_id)
            else f"No open to-do numbered {todo_id}."
        )

    if not matching_text.strip():
        return "Give either todo_id or matching_text."

    matches = memory.find_todos(matching_text)
    if not matches:
        return f"No open to-do matching {matching_text!r}."
    if len(matches) > 1:
        listing = "\n".join(f"#{i} {t}" for i, t in matches)
        return f"That matches {len(matches)} items — ask which one:\n{listing}"

    memory.complete_todo(matches[0][0])
    return f"Marked #{matches[0][0]} done: {matches[0][1]}"
