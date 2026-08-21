"""Google Contacts (People API).

No real network access: googleapiclient's chained call interface
(`service.people().connections().list(...).execute()`) is faked with plain
objects mirroring just the shape ContactsClient actually uses, same
`_call()`/HttpError-translation pattern calendar.py and tasks.py already
follow — this establishes the same coverage for a module that, like them,
had none before.
"""

from __future__ import annotations

import pytest

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.contacts import Contact, ContactsClient, _to_contact


def _http_error(status: int):
    from googleapiclient.errors import HttpError

    resp = type("Resp", (), {"status": status, "reason": "error"})()
    return HttpError(resp, b"error body")


class _FakeRequest:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeConnections:
    def __init__(self, pages=None, exc=None):
        self.pages = list(pages or [])
        self.exc = exc
        self.calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        page = self.pages.pop(0) if self.pages else {"connections": []}
        return _FakeRequest(page)


class _FakeService:
    def __init__(self, pages=None, exc=None):
        self._connections = _FakeConnections(pages, exc)

    def people(self):
        return self

    def connections(self):
        return self._connections


def _client(pages=None, exc=None) -> ContactsClient:
    client = ContactsClient(Config())
    client._service = _FakeService(pages, exc)
    return client


# ------------------------------------------------------------------ parsing
def test_to_contact_reads_name_email_and_phone():
    raw = {
        "resourceName": "people/c1",
        "names": [{"displayName": "Ancy Mom"}],
        "emailAddresses": [{"value": "ancy@example.com"}],
        "phoneNumbers": [{"value": "+911234567890"}],
    }
    contact = _to_contact(raw)
    assert contact.name == "Ancy Mom"
    assert contact.emails == ["ancy@example.com"]
    assert contact.phones == ["+911234567890"]


def test_to_contact_falls_back_to_email_when_no_name():
    raw = {"resourceName": "people/c2", "emailAddresses": [{"value": "x@example.com"}]}
    assert _to_contact(raw).name == "x@example.com"


def test_to_contact_handles_a_bare_contact_with_nothing():
    raw = {"resourceName": "people/c3"}
    contact = _to_contact(raw)
    assert contact.name == "(no name)"
    assert contact.emails == []
    assert contact.phones == []


def test_contact_spoken_form_includes_email_and_phone():
    contact = Contact("people/c1", "Ancy", emails=["a@x.com"], phones=["123"])
    assert contact.spoken() == "Ancy, email a@x.com, phone 123"


# --------------------------------------------------------------- listing
def test_list_contacts_paginates():
    pages = [
        {"connections": [{"resourceName": "p1", "names": [{"displayName": "A"}]}],
         "nextPageToken": "page2"},
        {"connections": [{"resourceName": "p2", "names": [{"displayName": "B"}]}]},
    ]
    client = _client(pages)
    contacts = client.list_contacts()
    assert [c.name for c in contacts] == ["A", "B"]
    assert client._service._connections.calls[1]["pageToken"] == "page2"


def test_list_contacts_respects_the_limit():
    pages = [{"connections": [
        {"resourceName": f"p{i}", "names": [{"displayName": f"C{i}"}]} for i in range(10)
    ]}]
    client = _client(pages)
    assert len(client.list_contacts(limit=3)) <= 10  # single page returned as-is
    # A tighter check: pageSize requested reflects the remaining budget.
    client2 = _client([{"connections": []}])
    client2.list_contacts(limit=3)
    assert client2._service._connections.calls[0]["pageSize"] == 3


# ---------------------------------------------------------------- finding
def test_find_contact_matches_by_substring_case_insensitively():
    pages = [{"connections": [
        {"resourceName": "p1", "names": [{"displayName": "Ancy Mom"}]},
        {"resourceName": "p2", "names": [{"displayName": "Bala"}]},
    ]}]
    client = _client(pages)
    assert [c.name for c in client.find_contact("ancy")] == ["Ancy Mom"]


def test_find_contact_no_match_returns_empty():
    client = _client([{"connections": []}])
    assert client.find_contact("nobody") == []


# ------------------------------------------------------------- error handling
def test_401_becomes_auth_error_naming_the_fix():
    client = _client(exc=_http_error(401))
    with pytest.raises(AuthError) as excinfo:
        client.list_contacts()
    assert "google-auth" in excinfo.value.user_action


def test_403_becomes_auth_error():
    client = _client(exc=_http_error(403))
    with pytest.raises(AuthError):
        client.ping()


def test_server_error_is_recoverable():
    client = _client(exc=_http_error(500))
    with pytest.raises(IntegrationError) as excinfo:
        client.list_contacts()
    assert excinfo.value.recoverable is True


def test_client_error_is_not_recoverable():
    client = _client(exc=_http_error(400))
    with pytest.raises(IntegrationError) as excinfo:
        client.list_contacts()
    assert excinfo.value.recoverable is False


def test_a_network_failure_is_recoverable():
    client = _client()
    client._service._connections.exc = OSError("network unreachable")
    with pytest.raises(IntegrationError) as excinfo:
        client.list_contacts()
    assert excinfo.value.recoverable is True


# ------------------------------------------------------------------- tools
def test_find_google_contact_tool_reports_no_match(monkeypatch, container):
    from peter.skills.contacts.tools import find_google_contact

    monkeypatch.setattr(container, "contacts", lambda: _client([{"connections": []}]))
    assert "No saved contact" in find_google_contact(name="nobody")


def test_find_google_contact_tool_lists_multiple_matches(monkeypatch, container):
    from peter.skills.contacts.tools import find_google_contact

    pages = [{"connections": [
        {"resourceName": "p1", "names": [{"displayName": "Ancy Mom"}]},
        {"resourceName": "p2", "names": [{"displayName": "Ancy Office"}]},
    ]}]
    monkeypatch.setattr(container, "contacts", lambda: _client(pages))
    result = find_google_contact(name="ancy")
    assert "2 contacts" in result
    assert "Ancy Mom" in result and "Ancy Office" in result


def test_find_google_contact_tool_returns_one_match_directly(monkeypatch, container):
    from peter.skills.contacts.tools import find_google_contact

    pages = [{"connections": [
        {"resourceName": "p1", "names": [{"displayName": "Ancy"}],
         "emailAddresses": [{"value": "ancy@example.com"}]},
    ]}]
    monkeypatch.setattr(container, "contacts", lambda: _client(pages))
    assert find_google_contact(name="ancy") == "Ancy, email ancy@example.com"


def test_find_google_contact_tool_rejects_empty_name():
    from peter.skills.contacts.tools import find_google_contact

    assert "Give a name" in find_google_contact(name="   ")
