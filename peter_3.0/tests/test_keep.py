"""Google Keep, via the unofficial gkeepapi client.

gkeepapi is genuinely installed in this environment (it is a real
requirements.txt dependency now), so these tests use its real exception
classes — the ones _authenticate()/_sync() actually catch — but replace
`gkeepapi.Keep` itself with a fake that never makes a network call. That
means error translation is verified against the real exception hierarchy,
not a guess at its shape, while nothing here needs a real account, a real
master token, or network access.
"""

from __future__ import annotations

import gkeepapi
import pytest

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError, NotConfiguredError
from peter.integrations.google.keep import KeepClient, Note, _to_note


class _FakeGkeepNote:
    def __init__(self, id, title="", text="", pinned=False, archived=False, trashed=False):
        self.id = id
        self.title = title
        self.text = text
        self.pinned = pinned
        self.archived = archived
        self.trashed = trashed

    def trash(self):
        self.trashed = True


class _FakeKeep:
    """Stands in for gkeepapi.Keep — no network, no real account."""

    def __init__(self, notes=None, auth_exc=None, sync_exc=None):
        self.notes = list(notes or [])
        self.auth_exc = auth_exc
        self.sync_exc = sync_exc
        self.authenticated = False
        self.sync_calls = 0

    def authenticate(self, email, master_token, **kwargs):
        if self.auth_exc is not None:
            raise self.auth_exc
        self.authenticated = True

    def sync(self, resync=False):
        self.sync_calls += 1
        if self.sync_exc is not None:
            raise self.sync_exc

    def find(self, query=None, archived=None, trashed=False, **kwargs):
        for n in self.notes:
            if trashed is False and n.trashed:
                continue
            if archived is not None and n.archived != archived:
                continue
            if query and query.lower() not in f"{n.title} {n.text}".lower():
                continue
            yield n

    def get(self, note_id):
        return next((n for n in self.notes if n.id == note_id), None)

    def createNote(self, title, text):
        note = _FakeGkeepNote(id=f"new-{len(self.notes)}", title=title or "", text=text or "")
        self.notes.append(note)
        return note


def _client(fake: _FakeKeep, monkeypatch) -> KeepClient:
    client = KeepClient(Config())
    monkeypatch.setattr(gkeepapi, "Keep", lambda: fake)
    return client


# ------------------------------------------------------------------ parsing
def test_to_note_strips_whitespace_and_reads_flags():
    raw = _FakeGkeepNote("n1", title=" Groceries ", text=" milk, eggs ", pinned=True)
    note = _to_note(raw)
    assert note == Note("n1", "Groceries", "milk, eggs", pinned=True, archived=False)


def test_note_spoken_form_prefers_title():
    assert Note("n1", "Groceries", "milk, eggs").spoken() == "Groceries"


def test_note_spoken_form_falls_back_to_text_when_untitled():
    assert Note("n1", "", "Remember to call the plumber").spoken() == "Remember to call the plumber"


def test_note_spoken_form_flags_pinned():
    assert Note("n1", "Groceries", "milk", pinned=True).spoken() == "Groceries (pinned)"


# --------------------------------------------------------------- auth/sync
def test_authenticate_wraps_a_login_failure(monkeypatch):
    fake = _FakeKeep(auth_exc=gkeepapi.exception.LoginException("bad token"))
    client = _client(fake, monkeypatch)
    with pytest.raises(AuthError) as excinfo:
        client.ping()
    assert "master token" in excinfo.value.user_action.lower()


def test_authenticate_wraps_a_network_failure(monkeypatch):
    fake = _FakeKeep(auth_exc=OSError("no route to host"))
    client = _client(fake, monkeypatch)
    with pytest.raises(IntegrationError) as excinfo:
        client.ping()
    assert excinfo.value.recoverable is True


def test_sync_failure_wraps_login_exception_and_forces_relogin(monkeypatch):
    fake = _FakeKeep()
    client = _client(fake, monkeypatch)
    client.keep  # authenticate once, successfully
    fake.sync_exc = gkeepapi.exception.LoginException("session expired")

    with pytest.raises(AuthError):
        client.list_notes()
    assert client._keep is None  # forced to re-authenticate next time


def test_sync_failure_wraps_api_exception_as_recoverable(monkeypatch):
    fake = _FakeKeep()
    client = _client(fake, monkeypatch)
    fake.sync_exc = gkeepapi.exception.APIException(-1, "server hiccup")

    with pytest.raises(IntegrationError) as excinfo:
        client.list_notes()
    assert excinfo.value.recoverable is True


def test_successful_ping_does_not_raise(monkeypatch):
    client = _client(_FakeKeep(), monkeypatch)
    assert client.ping() is True


# --------------------------------------------------------------- listing
def test_list_notes_excludes_archived_by_default(monkeypatch):
    fake = _FakeKeep(notes=[
        _FakeGkeepNote("n1", title="Active"),
        _FakeGkeepNote("n2", title="Archived", archived=True),
    ])
    client = _client(fake, monkeypatch)
    assert [n.title for n in client.list_notes()] == ["Active"]


def test_list_notes_can_include_archived(monkeypatch):
    fake = _FakeKeep(notes=[
        _FakeGkeepNote("n1", title="Active"),
        _FakeGkeepNote("n2", title="Archived", archived=True),
    ])
    client = _client(fake, monkeypatch)
    titles = {n.title for n in client.list_notes(include_archived=True)}
    assert titles == {"Active", "Archived"}


def test_list_notes_excludes_trashed(monkeypatch):
    fake = _FakeKeep(notes=[
        _FakeGkeepNote("n1", title="Kept"),
        _FakeGkeepNote("n2", title="Trashed", trashed=True),
    ])
    client = _client(fake, monkeypatch)
    assert [n.title for n in client.list_notes()] == ["Kept"]


def test_find_notes_matches_title_or_text(monkeypatch):
    fake = _FakeKeep(notes=[
        _FakeGkeepNote("n1", title="Groceries", text="milk, eggs"),
        _FakeGkeepNote("n2", title="Work", text="finish the report"),
    ])
    client = _client(fake, monkeypatch)
    assert [n.title for n in client.find_notes("milk")] == ["Groceries"]


# ---------------------------------------------------------------- writing
def test_create_note_syncs_and_returns_it(monkeypatch):
    fake = _FakeKeep()
    client = _client(fake, monkeypatch)
    note = client.create_note("Groceries", "milk, eggs")
    assert note.title == "Groceries"
    assert fake.sync_calls == 1


def test_create_note_can_be_pinned(monkeypatch):
    fake = _FakeKeep()
    client = _client(fake, monkeypatch)
    note = client.create_note("Groceries", "milk", pinned=True)
    assert note.pinned is True


def test_pin_note_by_id(monkeypatch):
    fake = _FakeKeep(notes=[_FakeGkeepNote("n1", title="Groceries")])
    client = _client(fake, monkeypatch)
    note = client.pin_note("n1")
    assert note.pinned is True


def test_archive_note_by_id(monkeypatch):
    fake = _FakeKeep(notes=[_FakeGkeepNote("n1", title="Groceries")])
    client = _client(fake, monkeypatch)
    note = client.archive_note("n1")
    assert note.archived is True


def test_delete_note_trashes_rather_than_purges(monkeypatch):
    fake = _FakeKeep(notes=[_FakeGkeepNote("n1", title="Groceries")])
    client = _client(fake, monkeypatch)
    client.delete_note("n1")
    assert fake.notes[0].trashed is True


def test_operating_on_an_unknown_id_raises(monkeypatch):
    client = _client(_FakeKeep(), monkeypatch)
    with pytest.raises(IntegrationError):
        client.pin_note("does-not-exist")


def test_operating_on_a_trashed_note_by_id_raises(monkeypatch):
    fake = _FakeKeep(notes=[_FakeGkeepNote("n1", title="Gone", trashed=True)])
    client = _client(fake, monkeypatch)
    with pytest.raises(IntegrationError):
        client.pin_note("n1")


def test_find_by_text_for_disambiguation(monkeypatch):
    fake = _FakeKeep(notes=[
        _FakeGkeepNote("n1", title="Groceries A"),
        _FakeGkeepNote("n2", title="Groceries B"),
    ])
    client = _client(fake, monkeypatch)
    assert len(client.find_by_text("groceries")) == 2


# --------------------------------------------------- NotConfiguredError path
def test_keep_service_not_configured_by_default(container):
    with pytest.raises(NotConfiguredError):
        container.keep()


def test_keep_service_not_configured_without_credentials(container):
    container.config.integrations.keep.enabled = True
    with pytest.raises(NotConfiguredError):
        container.keep()


# ------------------------------------------------------------------- tools
def test_list_keep_notes_tool_reports_none(monkeypatch, container):
    from peter.skills.keep.tools import list_keep_notes

    monkeypatch.setattr(container, "keep", lambda: _client(_FakeKeep(), monkeypatch))
    assert list_keep_notes() == "No Keep notes."


def test_create_keep_note_tool_rejects_empty_text():
    from peter.skills.keep.tools import create_keep_note

    assert "Give some text" in create_keep_note(text="   ")


def test_pin_keep_note_tool_disambiguates_multiple_matches(monkeypatch, container):
    from peter.skills.keep.tools import pin_keep_note

    fake = _FakeKeep(notes=[
        _FakeGkeepNote("n1", title="Groceries A"),
        _FakeGkeepNote("n2", title="Groceries B"),
    ])
    monkeypatch.setattr(container, "keep", lambda: _client(fake, monkeypatch))
    result = pin_keep_note(matching_text="groceries")
    assert "2 notes" in result


def test_delete_keep_note_tool_by_id(monkeypatch, container):
    from peter.skills.keep.tools import delete_keep_note

    fake = _FakeKeep(notes=[_FakeGkeepNote("n1", title="Groceries")])
    monkeypatch.setattr(container, "keep", lambda: _client(fake, monkeypatch))
    assert delete_keep_note(note_id="n1") == "Note deleted."
    assert fake.notes[0].trashed is True
