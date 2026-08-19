"""Google Calendar.

Wraps googleapiclient in something the tool layer can use without knowing about
RFC 3339, HttpError status codes, or the difference between a timed event and an
all-day event (Google represents them with different keys, `dateTime` vs
`date` — the single most common source of off-by-one-day bugs in calendar code).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.auth import build_service

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CalendarEvent:
    id: str
    summary: str
    start: datetime | None
    end: datetime | None
    all_day: bool = False
    location: str = ""
    description: str = ""

    def when(self) -> str:
        if self.start is None:
            return "no time set"
        if self.all_day:
            return f"all day {self.start.strftime('%a %d %b')}"
        start = self.start.strftime("%I:%M %p").lstrip("0")
        if self.end is not None:
            return f"{start} to {self.end.strftime('%I:%M %p').lstrip('0')}"
        return start

    def spoken(self) -> str:
        line = f"{self.when()}, {self.summary}"
        return f"{line}, at {self.location}" if self.location else line


def _parse_when(node: dict) -> tuple[datetime | None, bool]:
    """Google gives `dateTime` for timed events and `date` for all-day ones."""
    if not node:
        return (None, False)
    if "dateTime" in node:
        raw = node["dateTime"].replace("Z", "+00:00")
        try:
            return (datetime.fromisoformat(raw).astimezone(), False)
        except ValueError:
            return (None, False)
    if "date" in node:
        try:
            naive = datetime.fromisoformat(node["date"])
            return (naive.astimezone(), True)
        except ValueError:
            return (None, True)
    return (None, False)


def _to_event(raw: dict) -> CalendarEvent:
    start, all_day = _parse_when(raw.get("start", {}))
    end, _ = _parse_when(raw.get("end", {}))
    return CalendarEvent(
        id=raw.get("id", ""),
        summary=raw.get("summary", "(no title)"),
        start=start,
        end=end,
        all_day=all_day,
        location=raw.get("location", "") or "",
        description=(raw.get("description", "") or "")[:500],
    )


class CalendarClient:
    def __init__(self, config: Config):
        self.config = config
        self.calendar_id = config.integrations.google.calendar_id
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self.config, "calendar", "v3")
        return self._service

    def _call(self, request, what: str):
        """Execute a googleapiclient request and translate its failures."""
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
            if status == 404:
                raise IntegrationError(
                    f"not found while {what}", service="google"
                ) from exc
            raise IntegrationError(
                f"Calendar error while {what}: {exc}",
                service="google",
                recoverable=status >= 500 or status == 429,
            ) from exc
        except OSError as exc:
            raise IntegrationError(
                f"could not reach Google while {what}: {exc}",
                service="google",
                recoverable=True,
                user_action="Check your internet connection.",
            ) from exc

    def ping(self) -> bool:
        self._call(self.service.calendarList().list(maxResults=1), "checking access")
        return True

    # -------------------------------------------------------------- reading
    def list_events(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10,
        query: str = "",
    ) -> list[CalendarEvent]:
        start = start or datetime.now().astimezone()
        end = end or (start + timedelta(days=7))

        request = self.service.events().list(
            calendarId=self.calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            maxResults=min(limit, 50),
            singleEvents=True,          # expand recurring events into instances
            orderBy="startTime",
            q=query or None,
        )
        payload = self._call(request, "listing events")
        return [_to_event(item) for item in payload.get("items", [])]

    def events_on(self, day: datetime, limit: int = 20) -> list[CalendarEvent]:
        start = datetime.combine(day.date(), time.min).astimezone()
        return self.list_events(start, start + timedelta(days=1), limit)

    def next_event(self) -> CalendarEvent | None:
        events = self.list_events(limit=1)
        return events[0] if events else None

    # -------------------------------------------------------------- writing
    def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime | None = None,
        location: str = "",
        description: str = "",
        all_day: bool = False,
    ) -> CalendarEvent:
        if all_day:
            body = {
                "summary": summary,
                "start": {"date": start.date().isoformat()},
                "end": {"date": (start.date() + timedelta(days=1)).isoformat()},
            }
        else:
            end = end or (start + timedelta(hours=1))
            body = {
                "summary": summary,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
            }
        if location:
            body["location"] = location
        if description:
            body["description"] = description

        created = self._call(
            self.service.events().insert(calendarId=self.calendar_id, body=body),
            "creating an event",
        )
        return _to_event(created)

    def delete_event(self, event_id: str) -> None:
        self._call(
            self.service.events().delete(
                calendarId=self.calendar_id, eventId=event_id
            ),
            "deleting an event",
        )

    def find_events(self, text: str, days: int = 30) -> list[CalendarEvent]:
        now = datetime.now().astimezone()
        return self.list_events(now, now + timedelta(days=days), 25, query=text)
