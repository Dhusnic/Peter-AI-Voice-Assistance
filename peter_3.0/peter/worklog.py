"""What actually happened today, and the standup that comes out of it.

Peter already holds every piece of this and never joined them up: commits sit
in git, meetings in the calendar, focus sessions and meeting notes in
episodes, finished work in the to-do list. The work log is the join.

**Everything here is assembled from records, not remembered.** The daily job
writes one episode summarising the day, so the answer to "what was I doing
last Tuesday" survives long after the conversation that day is out of the
context window. The standup is the only part that calls a model, and only to
phrase the material — it is given the facts, and told not to invent any.

Every source degrades independently: no git, no calendar, no mail, nothing
configured at all — you still get a log of whatever the rest could see.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

_STANDUP_SYSTEM = (
    "You write a developer's standup from a factual activity log. Three "
    "headings exactly: Yesterday, Today, Blockers. Two to four short bullets "
    "under each. Use only what the log contains — never invent a task, a "
    "meeting or a blocker. Where the log shows nothing for a heading, say so "
    "in one short line rather than padding it. Group related commits into one "
    "bullet describing the work, not a list of commit messages. No preamble."
)


@dataclass(slots=True)
class WorkLog:
    since: datetime
    commits: list[str] = field(default_factory=list)
    meetings: list[str] = field(default_factory=list)
    focus: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    open_todos: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.commits or self.meetings or self.focus or self.completed)

    def as_text(self) -> str:
        """The log as a person would read it — and as the model is given it."""
        blocks = []
        if self.commits:
            blocks.append("Commits:\n" + "\n".join(f"- {c}" for c in self.commits))
        if self.meetings:
            blocks.append("Meetings:\n" + "\n".join(f"- {m}" for m in self.meetings))
        if self.focus:
            blocks.append("Focus sessions:\n" + "\n".join(f"- {f}" for f in self.focus))
        if self.completed:
            blocks.append(
                "Finished to-dos:\n" + "\n".join(f"- {t}" for t in self.completed)
            )
        if self.open_todos:
            blocks.append("Still open:\n" + "\n".join(f"- {t}" for t in self.open_todos))
        if self.notes:
            blocks.append("Notes:\n" + "\n".join(f"- {n}" for n in self.notes))
        if not blocks:
            return "Nothing recorded."
        return "\n\n".join(blocks)

    def spoken(self) -> str:
        """A one-line summary, for reading aloud."""
        if self.empty:
            return "Nothing recorded for that period."
        bits = []
        if self.commits:
            bits.append(f"{len(self.commits)} commit{'s' if len(self.commits) != 1 else ''}")
        if self.meetings:
            bits.append(f"{len(self.meetings)} meeting{'s' if len(self.meetings) != 1 else ''}")
        if self.focus:
            bits.append(f"{len(self.focus)} focus session{'s' if len(self.focus) != 1 else ''}")
        if self.completed:
            bits.append(f"{len(self.completed)} to-do{'s' if len(self.completed) != 1 else ''} finished")
        return ", ".join(bits) + "."


def build_worklog(days: int = 1) -> WorkLog:
    """Assemble the log. Every source is optional and failure is not fatal."""
    from peter.core.services import services

    container = services()
    since = datetime.now().astimezone() - timedelta(days=max(1, days))
    log_entry = WorkLog(since=since)

    _add_commits(log_entry, container, days)
    _add_meetings(log_entry, container, since)
    _add_memory(log_entry, container, since)
    return log_entry


def _add_commits(entry: WorkLog, container, days: int) -> None:
    from peter.integrations.dev import git, repos

    cfg = container.config.integrations.dev
    if not (cfg.enabled and cfg.repos):
        return

    for repo in repos(cfg):
        try:
            author = cfg.git_author or git.author_email(repo.path, cfg.git_timeout_seconds)
            found = git.commits(
                repo.path,
                since=f"{days} day ago" if days == 1 else f"{days} days ago",
                author=author,
                repo_name=repo.name,
                timeout=cfg.git_timeout_seconds,
            )
        except Exception:
            log.debug("worklog: could not read %s", repo.name, exc_info=True)
            continue
        entry.commits.extend(commit.spoken() for commit in found)


def _add_meetings(entry: WorkLog, container, since: datetime) -> None:
    try:
        events = container.calendar().list_events(
            since, datetime.now().astimezone(), limit=20
        )
    except Exception:
        log.debug("worklog: calendar unavailable", exc_info=True)
        return
    for event in events:
        if getattr(event, "all_day", False):
            continue
        when = event.start.strftime("%a %H:%M") if event.start else ""
        entry.meetings.append(f"{event.summary} ({when})".strip())


def _add_memory(entry: WorkLog, container, since: datetime) -> None:
    try:
        memory = container.require_memory()
    except Exception:
        return

    stamp = since.timestamp()
    try:
        for summary in memory.episodes_since(stamp):
            if summary.lower().startswith("focus session"):
                entry.focus.append(summary)
            elif summary.lower().startswith("meeting notes"):
                entry.notes.append(summary)
            elif summary.lower().startswith("work log"):
                continue  # do not fold yesterday's own summary back in
            else:
                entry.notes.append(summary)
    except Exception:
        log.debug("worklog: could not read episodes", exc_info=True)

    try:
        entry.completed = memory.completed_todos_since(stamp)
        entry.open_todos = [text for _id, text, _done in memory.list_todos()][:10]
    except Exception:
        log.debug("worklog: could not read todos", exc_info=True)


# ------------------------------------------------------------------ standup
def standup(days: int = 1) -> str:
    """Yesterday / today / blockers, phrased by the model from the log."""
    from peter.core.services import services
    from peter.llm import factory

    container = services()
    entry = build_worklog(days)
    today = _today_ahead(container)

    material = (
        f"Activity log for the last {days} day(s):\n\n{entry.as_text()}\n\n"
        f"Coming up today:\n{today}"
    )

    try:
        provider = factory.build_provider(container.config, _STANDUP_SYSTEM)
        try:
            provider.add_user(material)
            response = provider.complete([])
        finally:
            provider.close()
    except Exception:
        log.exception("standup: model unavailable, returning the raw log")
        return f"I could not phrase a standup, so here is the raw log:\n\n{material}"

    return (response.text or "").strip() or material


def _today_ahead(container) -> str:
    """Today's remaining meetings and open to-dos — the 'Today' material."""
    lines = []
    now = datetime.now().astimezone()
    try:
        events = container.calendar().list_events(
            now, now.replace(hour=23, minute=59, second=59), limit=10
        )
        lines += [
            f"- {e.summary} at {e.start:%H:%M}" for e in events
            if e.start and not getattr(e, "all_day", False)
        ]
    except Exception:
        log.debug("standup: calendar unavailable", exc_info=True)

    try:
        todos = container.require_memory().list_todos()
        lines += [f"- to-do: {text}" for _id, text, _done in todos[:8]]
    except Exception:
        pass

    return "\n".join(lines) if lines else "Nothing scheduled."


# ------------------------------------------------------------- the daily job
def record_daily_worklog() -> None:
    """Scheduler job target. Must stay importable at this exact path."""
    from peter.core.services import services

    container = services()
    cfg = container.config.integrations.worklog
    if not cfg.enabled:
        return

    try:
        entry = build_worklog(cfg.days_back)
    except Exception:
        log.exception("worklog: could not assemble the day")
        return

    if entry.empty:
        log.info("worklog: nothing recorded today, not writing an episode")
        return

    summary = f"Work log {datetime.now():%d %b}: {entry.spoken()}"
    detail = "; ".join(entry.commits[:6]) or "; ".join(entry.meetings[:4])
    if detail:
        summary += f" {detail[:400]}"

    try:
        container.require_memory().add_episode(summary)
    except Exception:
        log.exception("worklog: could not record the episode")
        return
    log.info("worklog: %s", summary)


def schedule_worklog(scheduler, config) -> None:
    """Install (or re-install) the end-of-day work log."""
    cfg = config.integrations.worklog
    if not cfg.enabled:
        return
    scheduler.add_daily_job(
        job_id="worklog-daily",
        hour=cfg.hour,
        minute=cfg.minute,
        func=record_daily_worklog,
        name="daily work log",
    )
    log.info("work log: recording daily at %s", cfg.time)
