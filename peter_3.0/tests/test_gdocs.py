"""Google Docs (Docs API v1 for writing, Drive export for reading).

No real network access: googleapiclient's chained call interface is faked
with plain objects, same `_call()`/HttpError-translation pattern
test_contacts.py/test_drive.py already establish. The Drive-export fake
mirrors the shape test_docs_index.py already uses for the same endpoint.
"""

from __future__ import annotations

import pytest

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.gdocs import GDoc, GDocsClient


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


class _FakeDocuments:
    def __init__(self, exc=None, create_result=None):
        self.exc = exc
        self.create_result = create_result
        self.calls: list[tuple[str, dict]] = []

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        title = kwargs["body"]["title"]
        return _FakeRequest(self.create_result or {"documentId": "doc1", "title": title})

    def batchUpdate(self, **kwargs):  # noqa: N802 - matches googleapiclient's own naming
        self.calls.append(("batchUpdate", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        return _FakeRequest({"documentId": kwargs.get("documentId", "")})


class _FakeDocsService:
    def __init__(self, **kwargs):
        self._documents = _FakeDocuments(**kwargs)

    def documents(self):
        return self._documents


class _FakeDriveFiles:
    def __init__(self, exports=None, exc=None):
        self.exports = exports or {}
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def export(self, **kwargs):
        self.calls.append(("export", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        return _FakeRequest(self.exports.get(kwargs.get("fileId"), b""))


class _FakeDriveService:
    def __init__(self, **kwargs):
        self._files = _FakeDriveFiles(**kwargs)

    def files(self):
        return self._files


def _client(docs_exc=None, docs_create_result=None, export_map=None, export_exc=None) -> GDocsClient:
    client = GDocsClient(Config())
    client._service = _FakeDocsService(exc=docs_exc, create_result=docs_create_result)
    client._drive_service = _FakeDriveService(exports=export_map, exc=export_exc)
    return client


# ----------------------------------------------------------------- creating
def test_create_doc_returns_id_and_title():
    client = _client()
    doc = client.create_doc("Meeting Notes")
    assert doc.id == "doc1"
    assert doc.title == "Meeting Notes"
    assert "doc1" in doc.url


def test_create_doc_with_text_also_appends():
    client = _client()
    client.create_doc("Notes", text="hello world")
    calls = [c for c in client._service._documents.calls if c[0] == "batchUpdate"]
    assert len(calls) == 1
    assert calls[0][1]["body"]["requests"][0]["insertText"]["text"] == "hello world"


def test_create_doc_without_text_does_not_append():
    client = _client()
    client.create_doc("Notes")
    calls = [c for c in client._service._documents.calls if c[0] == "batchUpdate"]
    assert calls == []


def test_gdoc_spoken_is_the_title():
    assert GDoc("d1", "Meeting Notes").spoken() == "Meeting Notes"


# -------------------------------------------------------------------- read
def test_read_doc_delegates_to_drive_export():
    client = _client(export_map={"doc1": b"the document body"})
    assert client.read_doc("doc1") == "the document body"
    assert client._drive_service._files.calls[0][1]["mimeType"] == "text/plain"


# ------------------------------------------------------------------ append
def test_append_text_sends_insert_at_end_of_segment():
    client = _client()
    client.append_text("doc1", "more text")
    call = client._service._documents.calls[-1][1]
    req = call["body"]["requests"][0]["insertText"]
    assert req["text"] == "more text"
    assert req["endOfSegmentLocation"] == {"segmentId": ""}


# ------------------------------------------------------------- error handling
def test_401_on_create_becomes_auth_error():
    client = _client(docs_exc=_http_error(401))
    with pytest.raises(AuthError) as excinfo:
        client.create_doc("x")
    assert "google-auth" in excinfo.value.user_action


def test_server_error_on_append_is_recoverable():
    client = _client(docs_exc=_http_error(500))
    with pytest.raises(IntegrationError) as excinfo:
        client.append_text("doc1", "x")
    assert excinfo.value.recoverable is True


def test_401_on_read_becomes_auth_error():
    client = _client(export_exc=_http_error(401))
    with pytest.raises(AuthError):
        client.read_doc("doc1")


# ------------------------------------------------------------------- tools
def test_create_google_doc_tool_rejects_empty_title():
    from peter.skills.gdocs.tools import create_google_doc

    assert "Give the document a title" in create_google_doc(title="  ")


def test_read_google_doc_tool_truncates_long_text(monkeypatch, container):
    from peter.skills.gdocs.tools import read_google_doc

    long_text = "y" * 5000
    monkeypatch.setattr(
        container, "gdocs", lambda: _client(export_map={"doc1": long_text.encode("utf-8")})
    )
    result = read_google_doc(document_id="doc1")
    assert result.endswith("(truncated)")
    assert len(result) < 5000


def test_append_to_google_doc_tool_rejects_empty_text():
    from peter.skills.gdocs.tools import append_to_google_doc

    assert "Give some text" in append_to_google_doc(document_id="doc1", text="  ")


def test_append_to_google_doc_tool_reports_success(monkeypatch, container):
    from peter.skills.gdocs.tools import append_to_google_doc

    monkeypatch.setattr(container, "gdocs", lambda: _client())
    assert append_to_google_doc(document_id="doc1", text="hello") == "Appended."
