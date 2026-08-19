"""Alarms, timers, reminders, and the daily briefing.

APScheduler with a SQLite jobstore. The persistence matters more than it looks:
a reminder set at 11pm must still fire at 7am even if Peter crashed, was killed,
or the machine rebooted in between. An in-memory scheduler quietly forgets,
which is worse than never having offered the feature.

**Job targets must be module-level functions.** APScheduler serialises a
reference by import path, so a bound method, a lambda, or a closure cannot be
stored — and moving or renaming a target function silently breaks every job
already sitting in the database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from peter.core.notify import notify

log = logging.getLogger(__name__)

# A reminder that was missed because the laptop was asleep should still fire
# when it wakes, within reason. An hour is the useful window; beyond that,
# "your 7am reminder" arriving at 4pm is noise.
MISFIRE_GRACE_SECONDS = 3600


def fire_reminder(text: str) -> None:
    """Job target for reminders. Must stay importable at this exact path."""
    from peter.core.services import services

    log.info("reminder fired: %s", text)
    services().say(f"Reminder: {text}")
    notify("Peter — reminder", text)


class Scheduler:
    def __init__(self, db_path: Path):
        url = f"sqlite:///{Path(db_path).as_posix()}"
        self._scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=url)},
            job_defaults={
                "misfire_grace_time": MISFIRE_GRACE_SECONDS,
                "coalesce": True,       # one catch-up run, not a burst
                "max_instances": 1,
            },
        )

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
            log.debug("scheduler started with %d job(s)", len(self.list_jobs()))

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    @property
    def running(self) -> bool:
        return self._scheduler.running

    # ------------------------------------------------------------ scheduling
    def add_once(self, when: datetime, text: str, job_id: str | None = None) -> str:
        job = self._scheduler.add_job(
            fire_reminder,
            trigger=DateTrigger(run_date=when),
            args=[text],
            id=job_id,
            name=text,
            replace_existing=bool(job_id),
        )
        return job.id

    def add_in(self, delta: timedelta, text: str) -> tuple[str, datetime]:
        when = datetime.now().astimezone() + delta
        return self.add_once(when, text), when

    def add_daily(self, hour: int, minute: int, text: str) -> tuple[str, datetime]:
        job = self._scheduler.add_job(
            fire_reminder,
            trigger=CronTrigger(hour=hour, minute=minute),
            args=[text],
            name=text,
        )
        return job.id, job.next_run_time

    def add_daily_job(
        self,
        job_id: str,
        hour: int,
        minute: int,
        func: Callable,
        name: str = "",
    ) -> str:
        """Install a named recurring system job, replacing any previous version.

        Used for the briefing. `replace_existing` matters: without it, changing
        the briefing time in config.yml would leave the old job in the database
        and you would get briefed twice.
        """
        job = self._scheduler.add_job(
            func,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=job_id,
            name=name or job_id,
            replace_existing=True,
        )
        return job.id

    def add_interval_job(
        self,
        job_id: str,
        minutes: float,
        func: Callable,
        name: str = "",
    ) -> str:
        """Install a named recurring poll job, replacing any previous version.

        Used by the meeting-prep and inbox-digest watchers — both re-check
        live state (the calendar, the inbox) on a timer rather than being
        pre-scheduled once, since what they are watching changes underneath
        them (an event gets moved, new mail arrives).
        """
        job = self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(minutes=minutes),
            id=job_id,
            name=name or job_id,
            replace_existing=True,
        )
        return job.id

    def add_one_off_job(
        self,
        job_id: str,
        when: datetime,
        func: Callable,
        args: list | None = None,
        name: str = "",
    ) -> str:
        """Install a named one-off job at an exact time, replacing any
        previous job under the same id.

        Used for the focus-session restore: `args` is stored in the SQLite
        jobstore along with everything else, so the restore still fires with
        the correct saved volume even if Peter gets restarted mid-session —
        only in-process state (focus_status(), ending it early) would not
        survive that, not the actual restore.
        """
        job = self._scheduler.add_job(
            func,
            trigger=DateTrigger(run_date=when),
            id=job_id,
            args=args or [],
            name=name or job_id,
            replace_existing=True,
        )
        return job.id

    # --------------------------------------------------------------- querying
    def list_jobs(self, include_system: bool = False) -> list[dict]:
        """Pending jobs. System jobs (the briefing) are hidden by default —
        the user asking "what reminders do I have" does not mean them."""
        out = []
        for job in self._scheduler.get_jobs():
            if not include_system and job.func is not fire_reminder:
                continue
            out.append(
                {
                    "id": job.id,
                    "text": job.name,
                    "next_run_at": job.next_run_time,
                    "next_run": job.next_run_time.strftime("%a %d %b, %I:%M %p")
                    if job.next_run_time
                    else "paused",
                }
            )
        return sorted(out, key=lambda j: j["next_run_at"] or datetime.max.replace(
            tzinfo=None
        ).astimezone())

    def cancel(self, job_id: str) -> bool:
        try:
            self._scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    def find_by_text(self, needle: str) -> list[str]:
        needle = needle.lower().strip()
        if not needle:
            return []
        return [j["id"] for j in self.list_jobs() if needle in (j["text"] or "").lower()]
