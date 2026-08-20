"""The work log and the standup built from it.

The behaviour that matters is degradation: this joins four sources that are
each independently allowed to be missing, unconfigured or broken, and it has
to produce whatever the rest could see rather than nothing at all.
"""

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from peter.core.errors import NotConfiguredError
from peter.integrations.dev import git
from peter.worklog import (
    WorkLog,
    build_worklog,
    record_daily_worklog,
    schedule_worklog,
    standup,
)


def commit(subject, repo="peter", when="2 hours ago"):
    return git.Commit(sha="abc", when=when, subject=subject, author="D", repo=repo)


def event(summary, hours_ago=2):
    start = datetime.now().astimezone() - timedelta(hours=hours_ago)
    return SimpleNamespace(summary=summary, start=start, all_day=False)


@pytest.fixture
def worked(container, monkeypatch):
    """A container with one repo, a calendar, and nothing else configured."""
    container.config.integrations.dev.repos = {"peter": "D:/peter"}
    monkeypatch.setattr(git, "author_email", lambda *a, **k: "me@example.com")
    monkeypatch.setattr(
        git, "commits",
        lambda *a, **k: [commit("Add the spend ledger"), commit("Fix the digest")],
    )
    container.calendar = lambda: SimpleNamespace(
        list_events=lambda start, end, limit=10: [event("Team Meeting")]
    )
    return container


# ----------------------------------------------------------------- assembly
def test_the_log_gathers_commits_and_meetings(worked):
    entry = build_worklog(days=1)

    assert len(entry.commits) == 2
    assert len(entry.meetings) == 1
    assert entry.meetings[0].startswith("Team Meeting (")
    assert entry.empty is False


def test_focus_sessions_are_picked_out_of_episodes(worked):
    worked.memory.add_episode("Focus session on the migration ran 90 minute(s), completed.")
    worked.memory.add_episode("Meeting notes — standup: we agreed to ship Friday.")
    worked.memory.add_episode("Something else entirely.")

    entry = build_worklog(days=1)

    assert len(entry.focus) == 1
    assert any("Meeting notes" in n for n in entry.notes)


def test_yesterdays_own_work_log_is_not_folded_back_in(worked):
    """Otherwise each day's summary quotes the previous day's summary, for ever."""
    worked.memory.add_episode("Work log 19 Aug: 4 commits, 1 meeting.")

    entry = build_worklog(days=1)

    assert not any("Work log" in note for note in entry.notes)


def test_finished_todos_are_included(worked):
    todo_id = worked.memory.add_todo("write the migration plan")
    worked.memory.complete_todo(todo_id)

    entry = build_worklog(days=1)

    assert entry.completed == ["write the migration plan"]


def test_a_todo_finished_before_the_window_is_not_counted(worked):
    todo_id = worked.memory.add_todo("old thing")
    worked.memory.complete_todo(todo_id)
    # Push its completion far into the past.
    worked.memory._conn.execute(
        "UPDATE todos SET completed_at = ? WHERE id = ?",
        (time.time() - 10 * 86400, todo_id),
    )
    worked.memory._conn.commit()

    assert build_worklog(days=1).completed == []


def test_open_todos_are_listed_separately_from_finished_ones(worked):
    worked.memory.add_todo("still to do")

    entry = build_worklog(days=1)

    assert entry.open_todos == ["still to do"]
    assert entry.completed == []


# -------------------------------------------------------------- degradation
def test_no_repositories_configured_still_gives_a_log(container):
    container.config.integrations.dev.repos = {}
    container.calendar = lambda: SimpleNamespace(
        list_events=lambda *a, **k: [event("Team Meeting")]
    )

    entry = build_worklog(days=1)

    assert entry.commits == []
    assert entry.meetings


def test_an_unreachable_calendar_still_gives_a_log(worked):
    def boom():
        raise NotConfiguredError("google")

    worked.calendar = boom

    entry = build_worklog(days=1)

    assert entry.commits  # git still worked
    assert entry.meetings == []


def test_a_broken_repository_does_not_stop_the_others(container, monkeypatch):
    container.config.integrations.dev.repos = {"broken": "D:/gone", "ok": "D:/ok"}
    monkeypatch.setattr(git, "author_email", lambda *a, **k: "")

    def commits(path, **kwargs):
        if "gone" in str(path):
            raise OSError("no such directory")
        return [commit("worked fine", repo="ok")]

    monkeypatch.setattr(git, "commits", commits)

    entry = build_worklog(days=1)

    assert len(entry.commits) == 1
    assert "worked fine" in entry.commits[0]


def test_an_entirely_empty_day_is_reported_as_empty(container):
    container.config.integrations.dev.repos = {}
    entry = build_worklog(days=1)
    assert entry.empty is True
    assert entry.spoken() == "Nothing recorded for that period."


# ------------------------------------------------------------------ rendering
def test_the_spoken_summary_counts_each_kind():
    entry = WorkLog(
        since=datetime.now().astimezone(),
        commits=["a", "b", "c"], meetings=["m"], focus=["f"], completed=["t"],
    )
    spoken = entry.spoken()

    assert "3 commits" in spoken
    assert "1 meeting" in spoken
    assert "1 focus session" in spoken
    assert "1 to-do finished" in spoken


def test_the_text_form_groups_by_heading():
    entry = WorkLog(since=datetime.now().astimezone(), commits=["a"], meetings=["m"])
    text = entry.as_text()

    assert "Commits:\n- a" in text
    assert "Meetings:\n- m" in text


def test_an_empty_log_renders_as_nothing_recorded():
    assert WorkLog(since=datetime.now().astimezone()).as_text() == "Nothing recorded."


# ------------------------------------------------------------------- standup
class FakeProvider:
    def __init__(self, reply="Yesterday:\n- shipped it"):
        self.reply = reply
        self.sent = ""

    def add_user(self, text):
        self.sent = text

    def complete(self, tools):
        from peter.llm.base import ProviderResponse

        return ProviderResponse(text=self.reply)

    def close(self): ...


def test_the_standup_is_given_the_facts_not_asked_to_remember_them(worked, monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr("peter.llm.factory.build_provider", lambda *a, **k: provider)

    result = standup(days=1)

    assert "shipped it" in result
    assert "Add the spend ledger" in provider.sent
    assert "Coming up today" in provider.sent


def test_a_model_failure_degrades_to_the_raw_log(worked, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("peter.llm.factory.build_provider", boom)

    result = standup(days=1)

    assert "could not phrase a standup" in result
    assert "Add the spend ledger" in result


def test_the_standup_prompt_forbids_inventing_work(worked, monkeypatch):
    seen = {}

    def build(config, system, *a, **k):
        seen["system"] = system
        return FakeProvider()

    monkeypatch.setattr("peter.llm.factory.build_provider", build)
    standup()

    assert "never invent" in seen["system"].lower()


# ------------------------------------------------------------- the daily job
def test_the_daily_job_records_an_episode(worked):
    record_daily_worklog()

    episodes = worked.memory.recent_episodes(limit=1)
    assert episodes[0].startswith("Work log")
    assert "2 commits" in episodes[0]


def test_an_empty_day_records_nothing(container):
    container.config.integrations.dev.repos = {}

    record_daily_worklog()

    assert container.memory.recent_episodes(limit=1) == []


def test_a_disabled_worklog_does_nothing(worked, monkeypatch):
    monkeypatch.setattr(worked.config.integrations.worklog, "enabled", False)

    record_daily_worklog()

    assert worked.memory.recent_episodes(limit=1) == []


def test_the_daily_job_never_raises(container, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("everything is broken")

    monkeypatch.setattr("peter.worklog.build_worklog", boom)

    record_daily_worklog()  # must not raise


def test_the_worklog_is_scheduled_at_the_configured_time(config):
    calls = []
    scheduler = SimpleNamespace(add_daily_job=lambda **kw: calls.append(kw))

    schedule_worklog(scheduler, config)

    assert calls[0]["job_id"] == "worklog-daily"
    assert calls[0]["hour"] == config.integrations.worklog.hour


def test_a_disabled_worklog_is_not_scheduled(config, monkeypatch):
    calls = []
    scheduler = SimpleNamespace(add_daily_job=lambda **kw: calls.append(kw))
    monkeypatch.setattr(config.integrations.worklog, "enabled", False)

    schedule_worklog(scheduler, config)

    assert calls == []
