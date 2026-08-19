"""Meeting-prep nudge: calendar + memory + scheduler, proactive.

Same discipline as briefing.py's tests — a poll must never raise out of the
scheduler, and must not repeat itself for the same event on the next poll.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from peter.core.errors import AuthError, IntegrationError
from peter.meeting_prep import check_meeting_prep, schedule_meeting_prep


@pytest.fixture(autouse=True)
def _clear_notified():
    """Module-level dedup state must not leak between tests."""
    from peter import meeting_prep

    meeting_prep._notified.clear()
    yield
    meeting_prep._notified.clear()


class FakeCalendar:
    def __init__(self, events=(), error=None):
        self.events = list(events)
        self.error = error
        self.calls = []

    def list_events(self, start, end, limit=10):
        self.calls.append((start, end, limit))
        if self.error:
            raise self.error
        return self.events[:limit]


def event(event_id, summary, minutes_away=5, attendees=(), location="", all_day=False):
    start = datetime.now().astimezone() + timedelta(minutes=minutes_away)
    return SimpleNamespace(
        id=event_id, summary=summary, start=start, end=start + timedelta(hours=1),
        all_day=all_day, location=location, attendees=list(attendees),
    )


@pytest.fixture
def spoken(container):
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    return said


def wire(container, calendar):
    container.calendar = lambda: calendar


# --------------------------------------------------------------- happy path
def test_announces_an_event_inside_the_lead_window(container, spoken):
    wire(container, FakeCalendar([event("e1", "Team Meeting", minutes_away=8,
                                         attendees=["Priya", "Arjun"])]))

    check_meeting_prep()

    assert len(spoken) == 1
    assert "Team Meeting" in spoken[0]
    assert "Priya and Arjun" in spoken[0]


def test_a_single_attendee_reads_naturally(container, spoken):
    wire(container, FakeCalendar([event("e1", "1:1", attendees=["Priya"])]))
    check_meeting_prep()
    assert "It's with Priya." in spoken[0]


def test_location_is_mentioned_when_present(container, spoken):
    wire(container, FakeCalendar([event("e1", "Standup", location="Room 4")]))
    check_meeting_prep()
    assert "At Room 4." in spoken[0]


def test_pulls_in_a_related_memory_when_one_exists(container, spoken):
    container.memory.set_fact("openobserve_rules", "the alerting thresholds we agreed on")
    wire(container, FakeCalendar([event("e1", "OpenObserve rules review")]))

    check_meeting_prep()

    assert "alerting thresholds" in spoken[0]


# ------------------------------------------------------------------ filters
def test_all_day_events_are_never_announced(container, spoken):
    wire(container, FakeCalendar([event("e1", "Holiday", all_day=True)]))
    check_meeting_prep()
    assert spoken == []


def test_an_event_already_underway_is_not_announced(container, spoken):
    """list_events can return something whose start already passed; the exact
    window semantics are Google's, not ours — guard the edge ourselves."""
    started = SimpleNamespace(
        id="e1", summary="Standup", start=datetime.now().astimezone() - timedelta(minutes=5),
        end=None, all_day=False, location="", attendees=[],
    )
    wire(container, FakeCalendar([started]))
    check_meeting_prep()
    assert spoken == []


def test_the_same_event_is_not_announced_twice(container, spoken):
    calendar = FakeCalendar([event("e1", "Team Meeting")])
    wire(container, calendar)

    check_meeting_prep()
    check_meeting_prep()

    assert len(spoken) == 1


def test_two_different_events_both_get_announced(container, spoken):
    wire(container, FakeCalendar([event("e1", "Standup"), event("e2", "Review")]))
    check_meeting_prep()
    assert len(spoken) == 2


# ------------------------------------------------------------- degradation
def test_disabled_meeting_prep_does_nothing(container, spoken, monkeypatch):
    monkeypatch.setattr(container.config.integrations.meeting_prep, "enabled", False)
    wire(container, FakeCalendar([event("e1", "Team Meeting")]))
    check_meeting_prep()
    assert spoken == []


def test_an_unreachable_calendar_is_swallowed_not_raised(container, spoken):
    wire(container, FakeCalendar(error=IntegrationError("down", service="google")))
    check_meeting_prep()  # must not raise
    assert spoken == []


def test_expired_google_auth_is_swallowed_not_raised(container, spoken):
    wire(container, FakeCalendar(error=AuthError("token expired", service="google")))
    check_meeting_prep()
    assert spoken == []


def test_google_not_configured_is_swallowed_quietly(container, spoken):
    """calendar() itself raises NotConfiguredError when google integration
    is off — the common case for anyone who has not set it up."""
    def boom():
        from peter.core.errors import NotConfiguredError
        raise NotConfiguredError("google")

    container.calendar = boom
    check_meeting_prep()
    assert spoken == []


def test_a_bug_in_one_announcement_does_not_stop_the_rest(container, spoken):
    """One malformed event must not swallow every other event in the poll."""
    good = event("e1", "Standup")
    exploding = SimpleNamespace(
        id="e2", summary="Broken", start=good.start, end=None, all_day=False,
        location="", attendees=object(),  # not a list — _join_and(...) raises
    )
    wire(container, FakeCalendar([exploding, good]))

    check_meeting_prep()

    assert any("Standup" in s for s in spoken)


# ---------------------------------------------------------------- scheduling
def test_disabled_meeting_prep_is_not_scheduled(config, monkeypatch):
    calls = []
    scheduler = SimpleNamespace(add_interval_job=lambda **kw: calls.append(kw))
    monkeypatch.setattr(config.integrations.meeting_prep, "enabled", False)

    schedule_meeting_prep(scheduler, config)

    assert calls == []


def test_enabled_meeting_prep_uses_a_stable_job_id(config):
    calls = []
    scheduler = SimpleNamespace(add_interval_job=lambda **kw: calls.append(kw))

    schedule_meeting_prep(scheduler, config)
    schedule_meeting_prep(scheduler, config)

    assert len(calls) == 2
    assert calls[0]["job_id"] == calls[1]["job_id"] == "meeting-prep-poll"
    assert calls[0]["minutes"] == config.integrations.meeting_prep.poll_interval_minutes
