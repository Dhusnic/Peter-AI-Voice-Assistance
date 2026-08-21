"""Google Tasks.

Peter already has a local to-do list in SQLite. This exists so the list is the
*same* list you see in Google Tasks on your phone — a to-do you can only reach
by talking to your laptop is not a to-do list, it is a diary.

The local store stays the source of truth for offline work; `peter/skills/
calendar/tools.py` decides which side a given operation writes to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.auth import build_service

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Task:
    id: str
    title: str
    notes: str = ""
    due: datetime | None = None
    completed: bool = False

    def spoken(self) -> str:
        if self.due is None:
            return self.title
        return f"{self.title}, due {self.due.strftime('%a %d %b')}"


def _to_task(raw: dict) -> Task:
    due = None
    if raw.get("due"):
        try:
            due = datetime.fromisoformat(raw["due"].replace("Z", "+00:00")).astimezone()
        except ValueError:
            due = None
    return Task(
        id=raw.get("id", ""),
        title=raw.get("title", "(untitled)"),
        notes=raw.get("notes", "") or "",
        due=due,
        completed=raw.get("status") == "completed",
    )


class TasksClient:
    def __init__(self, config: Config):
        self.config = config
        self.tasklist_id = config.integrations.google.tasklist_id
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self.config, "tasks", "v1")
        return self._service

    def _call(self, request, what: str):
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            if status in (401, 403):
                raise AuthError(
                    f"Google rejected the request ({status}) while {what}",
                    service="google",
                    user_action="Run: python -m peter.main --google-auth",
                ) from exc
            raise IntegrationError(
                f"Tasks error while {what}: {exc}",
                service="google",
                recoverable=status >= 500 or status == 429,
            ) from exc
        except OSError as exc:
            raise IntegrationError(
                f"could not reach Google while {what}: {exc}",
                service="google",
                recoverable=True,
            ) from exc

    def ping(self) -> bool:
        self._call(self.service.tasklists().list(maxResults=1), "checking access")
        return True

    def list_tasks(self, include_completed: bool = False, limit: int = 50) -> list[Task]:
        payload = self._call(
            self.service.tasks().list(
                tasklist=self.tasklist_id,
                showCompleted=include_completed,
                showHidden=include_completed,
                maxResults=min(limit, 100),
            ),
            "listing tasks",
        )
        return [_to_task(item) for item in payload.get("items", [])]

    def add_task(self, title: str, notes: str = "", due: datetime | None = None) -> Task:
        body: dict = {"title": title}
        if notes:
            body["notes"] = notes
        if due is not None:
            # Google Tasks stores due dates at UTC midnight and ignores the time.
            body["due"] = due.astimezone().replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()

        created = self._call(
            self.service.tasks().insert(tasklist=self.tasklist_id, body=body),
            "adding a task",
        )
        return _to_task(created)

    def complete_task(self, task_id: str) -> Task:
        updated = self._call(
            self.service.tasks().patch(
                tasklist=self.tasklist_id,
                task=task_id,
                body={"status": "completed"},
            ),
            "completing a task",
        )
        return _to_task(updated)

    def delete_task(self, task_id: str) -> None:
        self._call(
            self.service.tasks().delete(tasklist=self.tasklist_id, task=task_id),
            "deleting a task",
        )

    def find_tasks(self, text: str) -> list[Task]:
        needle = text.lower().strip()
        return [t for t in self.list_tasks() if needle in t.title.lower()]
