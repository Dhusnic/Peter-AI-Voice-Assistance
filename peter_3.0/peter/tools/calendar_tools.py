"""Calendar and Google Tasks tools.

Times are passed as ISO 8601 strings. Claude gets the current local time on
every turn, so it does the "next Tuesday" arithmetic itself — which it does
better than a date-parsing library, and which fails loudly instead of silently
booking the wrong week.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services

register_skill(SkillManifest(
    name="calendar", version="1.0.0",
    description="Calendar events and Google Tasks.",
    module=__name__, permissions=("network",),
    tools=("check_calendar", "upcoming_events", "next_event",
           "create_calendar_event", "delete_calendar_event",
           "list_google_tasks", "add_google_task", "complete_google_task"),
))


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.astimezone()


def _parse_day(value: str) -> datetime:
    """Accept a date or a full datetime; anchor to local midnight."""
    text = value.strip()
    if not text or text.lower() == "today":
        base = datetime.now()
    elif text.lower() == "tomorrow":
        base = datetime.now() + timedelta(days=1)
    else:
        base = _parse_iso(text)
    return datetime.combine(base.date(), time.min).astimezone()


# ------------------------------------------------------------------ calendar
@peter_tool(tier="read")
def check_calendar(day: str = "today", limit: int = 10) -> str:
    """List calendar events for one day.

    Args:
        day: "today", "tomorrow", or a date like "2026-08-25".
        limit: Maximum events to return.
    """
    target = _parse_day(day)
    events = services().calendar().events_on(target, limit=limit)
    label = target.strftime("%A %d %B")
    if not events:
        return f"Nothing scheduled for {label}."
    lines = [f"{len(events)} on {label}:"]
    lines += [f"[{e.id}] {e.spoken()}" for e in events]
    return "\n".join(lines)


@peter_tool(tier="read")
def upcoming_events(days: int = 7, limit: int = 10) -> str:
    """List events over the next few days.

    Args:
        days: How far ahead to look.
        limit: Maximum events to return.
    """
    now = datetime.now().astimezone()
    events = services().calendar().list_events(
        now, now + timedelta(days=days), limit=limit
    )
    if not events:
        return f"Nothing scheduled in the next {days} days."
    lines = [f"{len(events)} coming up:"]
    for event in events:
        prefix = event.start.strftime("%a %d %b") if event.start else "?"
        lines.append(f"[{event.id}] {prefix}, {event.spoken()}")
    return "\n".join(lines)


@peter_tool(tier="read")
def next_event() -> str:
    """What is the very next thing on the calendar."""
    event = services().calendar().next_event()
    if event is None:
        return "Nothing on the calendar."
    when = event.start.strftime("%a %d %b") if event.start else "?"
    return f"{event.summary}, {when} at {event.when()}"


@peter_tool(tier="write")
def create_calendar_event(
    title: str,
    start_iso: str,
    end_iso: str = "",
    location: str = "",
    description: str = "",
    all_day: bool = False,
) -> str:
    """Add an event to the calendar.

    Args:
        title: What the event is called.
        start_iso: Start time, ISO 8601 local, e.g. "2026-08-25T15:00:00".
        end_iso: End time. Defaults to one hour after the start.
        location: Optional place.
        description: Optional notes.
        all_day: True for an all-day event; the time part of start_iso is then
            ignored.
    """
    try:
        start = _parse_iso(start_iso)
    except ValueError:
        return f"Could not read {start_iso!r} as an ISO 8601 datetime."

    end = None
    if end_iso.strip():
        try:
            end = _parse_iso(end_iso)
        except ValueError:
            return f"Could not read {end_iso!r} as an ISO 8601 datetime."
        if end <= start:
            return "The end time is not after the start time."

    event = services().calendar().create_event(
        summary=title,
        start=start,
        end=end,
        location=location,
        description=description,
        all_day=all_day,
    )
    day = event.start.strftime("%a %d %b") if event.start else "?"
    return f"Added {title} on {day}, {event.when()}."


@peter_tool(tier="write")
def delete_calendar_event(event_id: str = "", matching_text: str = "") -> str:
    """Remove an event from the calendar.

    Give either the bracketed id from check_calendar, or text to search for. If
    the text matches more than one event, nothing is deleted and the matches are
    listed so you can ask which was meant.

    Args:
        event_id: Exact event id.
        matching_text: Part of the event title, used when no id is known.
    """
    calendar = services().calendar()

    if event_id.strip():
        calendar.delete_event(event_id.strip())
        return "Event deleted."

    if not matching_text.strip():
        return "Give either event_id or matching_text."

    matches = calendar.find_events(matching_text)
    if not matches:
        return f"No upcoming event matches {matching_text!r}."
    if len(matches) > 1:
        listing = "\n".join(
            f"[{e.id}] {e.start.strftime('%a %d %b') if e.start else '?'}, {e.summary}"
            for e in matches
        )
        return f"That matches {len(matches)} events — ask which one:\n{listing}"

    calendar.delete_event(matches[0].id)
    return f"Deleted {matches[0].summary}."


# --------------------------------------------------------------- google tasks
@peter_tool(tier="read")
def list_google_tasks(include_completed: bool = False) -> str:
    """List tasks from Google Tasks, which syncs with the user's phone.

    This is separate from the local to-do list (list_todos). Prefer this one
    when the user wants something they will see on their phone.

    Args:
        include_completed: True to include finished tasks.
    """
    tasks = services().tasks().list_tasks(include_completed=include_completed)
    if not tasks:
        return "No tasks in Google Tasks."
    return "\n".join(
        f"[{t.id}] {'[done] ' if t.completed else ''}{t.spoken()}" for t in tasks
    )


@peter_tool(tier="write")
def add_google_task(title: str, notes: str = "", due_iso: str = "") -> str:
    """Add a task to Google Tasks so it appears on the user's phone.

    Args:
        title: The task.
        notes: Optional detail.
        due_iso: Optional due date, ISO 8601, e.g. "2026-08-25". Google Tasks
            stores dates only and ignores any time given.
    """
    due = None
    if due_iso.strip():
        try:
            due = _parse_iso(due_iso)
        except ValueError:
            return f"Could not read {due_iso!r} as a date."

    task = services().tasks().add_task(title=title, notes=notes, due=due)
    return f"Added to Google Tasks: {task.title}"


@peter_tool(tier="write")
def complete_google_task(task_id: str = "", matching_text: str = "") -> str:
    """Mark a Google Task as done.

    Args:
        task_id: Exact task id from list_google_tasks.
        matching_text: Part of the task title, used when no id is known.
    """
    client = services().tasks()

    if task_id.strip():
        task = client.complete_task(task_id.strip())
        return f"Completed {task.title}."

    if not matching_text.strip():
        return "Give either task_id or matching_text."

    matches = [t for t in client.find_tasks(matching_text) if not t.completed]
    if not matches:
        return f"No open task matches {matching_text!r}."
    if len(matches) > 1:
        listing = "\n".join(f"[{t.id}] {t.title}" for t in matches)
        return f"That matches {len(matches)} tasks — ask which one:\n{listing}"

    client.complete_task(matches[0].id)
    return f"Completed {matches[0].title}."
