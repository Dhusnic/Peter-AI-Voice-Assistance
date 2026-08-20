"""The briefing must degrade, never fail.

It runs at 7:30am while nobody is watching a terminal. If the wifi is down or
Google authorisation lapsed overnight, it has to deliver what it can and say one
short line about what it could not — a briefing that raises is silence at
exactly the moment you are relying on it.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from peter.briefing import build_briefing, next_briefing_time
from peter.core.errors import AuthError, IntegrationError


class FakeCalendar:
    def __init__(self, events=(), error=None):
        self.events = list(events)
        self.error = error

    def events_on(self, day, limit=10):
        if self.error:
            raise self.error
        return self.events[:limit]


class FakeMail:
    def __init__(self, unread=0, summaries=(), error=None):
        self.unread = unread
        self.summaries = list(summaries)
        self.error = error

    def count_unread(self):
        if self.error:
            raise self.error
        return self.unread

    def list_messages(self, criteria="UNSEEN", limit=25, folder=None):
        if self.error:
            raise self.error
        return self.summaries[:limit]


def event(summary, hour=10, all_day=False):
    start = datetime.now().astimezone().replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return SimpleNamespace(
        summary=summary,
        all_day=all_day,
        start=start,
        when=lambda: f"{hour}:00",
    )


def summary(sender):
    return SimpleNamespace(sender=sender)


@pytest.fixture
def briefing_container(container, monkeypatch):
    """Container with stubbed integrations and a stubbed scheduler."""
    container.scheduler = SimpleNamespace(list_jobs=lambda: [])
    return container


def wire(container, *, calendar=None, mail=None):
    container.calendar = lambda: calendar or FakeCalendar()
    container.mail = lambda: mail or FakeMail()


# --------------------------------------------------------------- happy path
def test_briefing_includes_every_section(briefing_container):
    wire(
        briefing_container,
        calendar=FakeCalendar([event("DBMS lecture", 9), event("Project review", 15)]),
        mail=FakeMail(unread=3, summaries=[summary("Amma"), summary("HDFC")]),
    )
    briefing_container.memory.add_todo("submit assignment")

    text = build_briefing()

    assert briefing_container.config.app.user_name in text
    assert "DBMS lecture" in text
    assert "3 unread" in text
    assert "Amma" in text
    assert "submit assignment" in text


def test_weather_section_appears_when_included_and_configured(briefing_container, monkeypatch):
    from peter.integrations import weather

    wire(briefing_container)
    briefing_container.config.integrations.briefing.include = ["weather"]
    monkeypatch.setattr(weather, "current", lambda cfg: "Chennai: clear sky, 31C.")

    text = build_briefing()

    assert "Chennai: clear sky, 31C." in text


def test_weather_section_degrades_gracefully_when_not_configured(briefing_container):
    """No location set — must join the "not set up" bucket, not crash the
    whole briefing the way build_briefing's docstring promises for every
    other section."""
    wire(briefing_container)
    briefing_container.config.integrations.briefing.include = ["weather"]
    briefing_container.config.integrations.weather.location = ""
    briefing_container.config.integrations.weather.latitude = 0.0
    briefing_container.config.integrations.weather.longitude = 0.0

    text = build_briefing()  # must not raise

    assert "weather" in text.lower()
    assert "not set up" in text


def test_greeting_matches_the_time_of_day(briefing_container):
    wire(briefing_container)
    text = build_briefing()
    assert any(word in text for word in ("Morning", "Afternoon", "Evening"))


def test_empty_day_says_so_rather_than_saying_nothing(briefing_container):
    wire(briefing_container)
    text = build_briefing()
    assert "Nothing on the calendar today." in text
    assert "Inbox is clear." in text


def test_all_day_events_are_reported_separately(briefing_container):
    wire(briefing_container, calendar=FakeCalendar([event("Holiday", all_day=True)]))
    assert "All day: Holiday." in build_briefing()


# ------------------------------------------------------------- degradation
def test_unreachable_mail_does_not_lose_the_calendar(briefing_container):
    """The whole point: one broken integration must not take the rest down."""
    wire(
        briefing_container,
        calendar=FakeCalendar([event("DBMS lecture", 9)]),
        mail=FakeMail(error=IntegrationError("no route to host", service="mail",
                                             recoverable=True)),
    )

    text = build_briefing()

    assert "DBMS lecture" in text
    assert "could not reach mail" in text


def test_expired_google_auth_does_not_lose_the_inbox(briefing_container):
    wire(
        briefing_container,
        calendar=FakeCalendar(error=AuthError("token expired", service="google")),
        mail=FakeMail(unread=2, summaries=[summary("Amma")]),
    )

    text = build_briefing()

    assert "2 unread" in text
    assert "could not reach calendar" in text


def test_both_integrations_down_still_produces_a_greeting(briefing_container):
    wire(
        briefing_container,
        calendar=FakeCalendar(error=IntegrationError("down", service="google")),
        mail=FakeMail(error=IntegrationError("down", service="mail")),
    )

    text = build_briefing()

    assert briefing_container.config.app.user_name in text
    assert "could not reach" in text


def test_an_unexpected_crash_is_contained(briefing_container):
    """Even a bug in one section must not silence the briefing."""
    class Exploding:
        def events_on(self, day, limit=10):
            raise RuntimeError("programming error")

    wire(briefing_container, calendar=Exploding(), mail=FakeMail(unread=1,
                                                                summaries=[summary("A")]))

    text = build_briefing()

    assert "1 unread" in text
    assert "could not reach calendar" in text


def test_build_briefing_never_raises(briefing_container):
    briefing_container.calendar = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    briefing_container.mail = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert isinstance(build_briefing(), str)


# ---------------------------------------------------------------- scheduling
def test_next_briefing_time_is_always_in_the_future(config):
    when = next_briefing_time(config)
    assert when > datetime.now().astimezone()
    assert when.hour == config.integrations.briefing.hour
    assert when.minute == config.integrations.briefing.minute


def test_next_briefing_rolls_to_tomorrow_when_today_has_passed(config):
    when = next_briefing_time(config)
    assert when - datetime.now().astimezone() < timedelta(days=1, seconds=1)


def test_disabled_briefing_is_not_scheduled(config, monkeypatch):
    from peter.briefing import schedule_briefing

    calls = []
    scheduler = SimpleNamespace(add_daily_job=lambda **kw: calls.append(kw))
    monkeypatch.setattr(config.integrations.briefing, "enabled", False)

    schedule_briefing(scheduler, config)

    assert calls == []


def test_enabled_briefing_uses_a_stable_job_id(config):
    """A changing id would install a duplicate job on every restart."""
    from peter.briefing import schedule_briefing

    calls = []
    scheduler = SimpleNamespace(add_daily_job=lambda **kw: calls.append(kw))

    schedule_briefing(scheduler, config)
    schedule_briefing(scheduler, config)

    assert len(calls) == 2
    assert calls[0]["job_id"] == calls[1]["job_id"] == "daily-briefing"
