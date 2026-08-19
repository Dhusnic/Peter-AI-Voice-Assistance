"""Focus mode: mute, time-box, restore, summarize.

The one thing that must never happen is the volume staying muted after a
session ends — by any path: the timer firing normally, ending early, or
Peter restarting mid-session. Everything else here is secondary to that.
"""

from types import SimpleNamespace

import pytest

from peter import focus
from peter.scheduler.jobs import Scheduler
from peter.tools import focus_tools


@pytest.fixture(autouse=True)
def _clear_active():
    focus._active = None
    yield
    focus._active = None


@pytest.fixture
def focus_container(container, tmp_path):
    """Adds a real (SQLite-backed), running scheduler — focus mode's restore
    job is only meaningfully tested against the real persistence it relies
    on, and APScheduler only computes next_run_time once actually started."""
    scheduler = Scheduler(tmp_path / "jobs.db")
    scheduler.start()
    container.scheduler = scheduler
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    yield SimpleNamespace(container=container, scheduler=scheduler, spoken=said)
    scheduler.shutdown()


@pytest.fixture
def fake_volume(monkeypatch):
    level = {"value": 62}

    def get():
        return level["value"]

    def set_(percent):
        level["value"] = percent
        return True

    monkeypatch.setattr(focus.volume, "get", get)
    monkeypatch.setattr(focus.volume, "set", set_)
    return level


# ------------------------------------------------------------------- starting
def test_starting_mutes_and_confirms(focus_container, fake_volume):
    result = focus.start(25, "the migration")

    assert fake_volume["value"] == 0
    assert "25-minute focus session on the migration" in result
    assert "Muting" in result


def test_starting_schedules_a_restore_job(focus_container, fake_volume):
    focus.start(25, "")

    jobs = focus_container.scheduler.list_jobs(include_system=True)
    assert any(j["id"] == "focus-session-restore" for j in jobs)


def test_cannot_start_a_second_session_while_one_is_running(focus_container, fake_volume):
    focus.start(25, "first thing")
    fake_volume["value"] = 999  # sentinel: a second start must not touch this

    result = focus.start(10, "second thing")

    assert "Already in a focus session" in result
    assert "first thing" in result
    assert fake_volume["value"] == 999


def test_a_session_with_no_label_still_works(focus_container, fake_volume):
    result = focus.start(15, "")
    assert "15-minute focus session." in result


def test_volume_unavailable_still_starts_the_timer(focus_container, monkeypatch):
    """If pycaw is not available (or COM fails), focus mode should not refuse
    to run at all — the timer and the summary are still worth having."""
    monkeypatch.setattr(focus.volume, "get", lambda: None)
    set_calls = []
    monkeypatch.setattr(focus.volume, "set", lambda p: set_calls.append(p) or True)

    result = focus.start(20, "")

    assert "Starting a 20-minute focus session." in result
    assert "Muting" not in result
    assert set_calls == []  # nothing to mute to, and nothing to restore later


# --------------------------------------------------------------------- status
def test_status_with_no_session():
    assert focus.status() == "No focus session running."


def test_status_reports_time_left(focus_container, fake_volume):
    focus.start(30, "deep work")
    result = focus.status()
    assert "deep work" in result
    assert "minute(s) left" in result


# ------------------------------------------------------------------- ending
def test_ending_early_restores_volume_and_cancels_the_job(focus_container, fake_volume):
    focus.start(25, "the migration")
    assert fake_volume["value"] == 0

    result = focus.end()

    assert fake_volume["value"] == 62  # back to what it was
    assert "ended early" in result
    assert focus.status() == "No focus session running."
    assert not any(j["id"] == "focus-session-restore"
                    for j in focus_container.scheduler.list_jobs(include_system=True))


def test_ending_with_nothing_running_says_so(focus_container):
    assert focus.end() == "No focus session running."


def test_ending_records_an_episode(focus_container, fake_volume):
    focus.start(25, "the migration")
    focus.end()

    episodes = focus_container.container.memory.recent_episodes(limit=1)
    assert "the migration" in episodes[0]
    assert "stopped early" in episodes[0]


def test_ending_does_not_speak_directly(focus_container, fake_volume):
    """A manual end happens inside a live turn — the tool's return value is
    what gets spoken, not a second, separate announcement."""
    focus.start(25, "")
    focus.end()
    assert focus_container.spoken == []


# --------------------------------------------------------- the scheduled completion
def test_natural_completion_restores_and_announces(focus_container, fake_volume):
    focus.start(25, "the migration")
    started_iso = focus._active.started_at.isoformat()

    focus.complete_focus_session(62, "the migration", started_iso)

    assert fake_volume["value"] == 62
    assert focus.status() == "No focus session running."
    assert len(focus_container.spoken) == 1
    assert "is done" in focus_container.spoken[0]
    assert "the migration" in focus_container.spoken[0]


def test_natural_completion_works_even_with_no_in_process_state(focus_container, fake_volume):
    """The realistic restart scenario: Peter restarted, _active is gone, but
    the persisted job still fires with its baked-in arguments."""
    import datetime as dt

    fake_volume["value"] = 40
    focus._active = None
    started_iso = dt.datetime.now().astimezone().isoformat()

    focus.complete_focus_session(77, "recovered session", started_iso)

    assert fake_volume["value"] == 77
    assert len(focus_container.spoken) == 1


# ---------------------------------------------------------------------- tools
def test_start_tool_delegates_to_focus_start(focus_container, fake_volume):
    result = focus_tools.start_focus_session(minutes=25, label="reviewing PRs")
    assert "reviewing PRs" in result
    assert fake_volume["value"] == 0


def test_end_tool_delegates_to_focus_end(focus_container, fake_volume):
    focus_tools.start_focus_session(minutes=25, label="")
    result = focus_tools.end_focus_session()
    assert "ended early" in result
    assert fake_volume["value"] == 62


def test_status_tool_delegates_to_focus_status(focus_container, fake_volume):
    focus_tools.start_focus_session(minutes=10, label="")
    assert "minute(s) left" in focus_tools.focus_status()
