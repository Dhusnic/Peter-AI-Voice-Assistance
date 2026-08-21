"""Google Drive (full read/write).

No real network access: googleapiclient's chained call interface is faked
with plain objects mirroring just the shape DriveClient uses, same
`_call()`/HttpError-translation pattern test_contacts.py already establishes.
"""

from __future__ import annotations

import pytest

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.drive import DriveClient, DriveFile, DriveStorage, _to_file


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


class _FakeFiles:
    def __init__(self, pages=None, exc=None, exports=None, media=None, get_result=None):
        self.pages = list(pages or [{"files": []}])
        self.exc = exc
        self.exports = exports or {}
        self.media = media or {}
        self.get_result = get_result
        self.calls: list[tuple[str, dict]] = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        page = self.pages.pop(0) if self.pages else {"files": []}
        return _FakeRequest(page)

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        return _FakeRequest(self.get_result or {"id": kwargs.get("fileId", "")})

    def export(self, **kwargs):
        self.calls.append(("export", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        return _FakeRequest(self.exports.get(kwargs.get("fileId"), b""))

    def get_media(self, **kwargs):
        self.calls.append(("get_media", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        return _FakeRequest(self.media.get(kwargs.get("fileId"), b""))

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        body = kwargs.get("body", {})
        return _FakeRequest({"id": "new1", "name": body.get("name", ""), "mimeType": body.get("mimeType", "")})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        body = kwargs.get("body", {})
        return _FakeRequest({"id": kwargs.get("fileId", ""), "name": body.get("name", "f"), "trashed": body.get("trashed", False)})


class _FakeAbout:
    def __init__(self, result=None, exc=None):
        self._result = result if result is not None else {"storageQuota": {}}
        self._exc = exc
        self.calls: list[dict] = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeRequest(self._result, exc=self._exc)


class _FakePermissions:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        return _FakeRequest({})


class _FakeService:
    def __init__(self, **kwargs):
        perm_exc = kwargs.pop("perm_exc", None)
        about_result = kwargs.pop("about_result", None)
        about_exc = kwargs.pop("about_exc", None)
        self._files = _FakeFiles(**kwargs)
        self._permissions = _FakePermissions(exc=perm_exc)
        self._about = _FakeAbout(result=about_result, exc=about_exc)

    def files(self):
        return self._files

    def permissions(self):
        return self._permissions

    def about(self):
        return self._about


def _client(**kwargs) -> DriveClient:
    client = DriveClient(Config())
    client._service = _FakeService(**kwargs)
    return client


# ------------------------------------------------------------------ parsing
def test_to_file_reads_common_fields():
    raw = {"id": "f1", "name": "notes.txt", "mimeType": "text/plain",
           "modifiedTime": "2026-02-01T10:00:00.000Z", "size": "42",
           "parents": ["p1"], "webViewLink": "https://x", "trashed": False}
    f = _to_file(raw)
    assert f.id == "f1" and f.name == "notes.txt" and f.size == 42
    assert f.parents == ["p1"] and f.trashed is False


def test_file_spoken_names_folders_distinctly():
    folder = DriveFile("f1", "Trip", mime_type="application/vnd.google-apps.folder")
    assert "folder" in folder.spoken()
    plain = DriveFile("f2", "notes.txt", mime_type="text/plain")
    assert "file" in plain.spoken()


# ------------------------------------------------------------------- listing
def test_list_files_filters_by_folder_and_query():
    client = _client(pages=[{"files": []}])
    client.list_files(folder_id="fold1", query="report")
    q = client._service._files.calls[0][1]["q"]
    assert "'fold1' in parents" in q
    assert "name contains 'report'" in q
    assert "trashed = false" in q


def test_search_files_returns_matches():
    pages = [{"files": [{"id": "f1", "name": "budget.xlsx"}]}]
    client = _client(pages=pages)
    results = client.search_files("budget")
    assert [f.name for f in results] == ["budget.xlsx"]


def test_get_file_returns_metadata():
    client = _client(get_result={"id": "f1", "name": "notes.txt", "mimeType": "text/plain"})
    f = client.get_file("f1")
    assert f.name == "notes.txt"


# --------------------------------------------------------------------- read
def test_read_file_text_exports_google_docs():
    client = _client(
        get_result={"id": "d1", "mimeType": "application/vnd.google-apps.document", "name": "Doc"},
        exports={"d1": b"exported text"},
    )
    assert client.read_file_text("d1") == "exported text"
    assert client._service._files.calls[-1][0] == "export"


def test_read_file_text_uses_get_media_for_plain_files():
    client = _client(
        get_result={"id": "t1", "mimeType": "text/plain", "name": "notes.txt"},
        media={"t1": b"plain content"},
    )
    assert client.read_file_text("t1") == "plain content"
    assert client._service._files.calls[-1][0] == "get_media"


def test_read_file_text_rejects_binary_content():
    client = _client(
        get_result={"id": "b1", "mimeType": "image/png", "name": "photo.png"},
        media={"b1": b"\xff\xd8\xff\xe0not utf8 \x80\x81"},
    )
    with pytest.raises(IntegrationError) as excinfo:
        client.read_file_text("b1")
    assert "not a text-readable file" in str(excinfo.value)


# -------------------------------------------------------------------- write
def test_create_text_file_uploads_content():
    client = _client()
    created = client.create_text_file("notes.txt", "hello world", folder_id="fold1")
    assert created.name == "notes.txt"
    kwargs = client._service._files.calls[-1][1]
    assert kwargs["body"]["parents"] == ["fold1"]


def test_create_folder_sets_folder_mime_type():
    client = _client()
    client.create_folder("Trip Photos")
    body = client._service._files.calls[-1][1]["body"]
    assert body["mimeType"] == "application/vnd.google-apps.folder"


def test_move_file_swaps_parents():
    client = _client(get_result={"id": "f1", "name": "x", "parents": ["old1"]})
    client.move_file("f1", "new1")
    update_call = [c for c in client._service._files.calls if c[0] == "update"][0][1]
    assert update_call["addParents"] == "new1"
    assert update_call["removeParents"] == "old1"


def test_rename_file_sends_new_name():
    client = _client()
    renamed = client.rename_file("f1", "renamed.txt")
    body = [c for c in client._service._files.calls if c[0] == "update"][0][1]["body"]
    assert body["name"] == "renamed.txt"
    assert renamed.name == "renamed.txt"


def test_trash_file_never_calls_delete():
    client = _client()
    trashed = client.trash_file("f1")
    assert trashed.trashed is True
    body = [c for c in client._service._files.calls if c[0] == "update"][0][1]["body"]
    assert body == {"trashed": True}
    assert not hasattr(client._service._files, "delete")


def test_share_file_creates_a_permission():
    client = _client()
    client.share_file("f1", "friend@example.com", role="writer")
    call = client._service._permissions.calls[0]
    assert call["body"] == {"type": "user", "role": "writer", "emailAddress": "friend@example.com"}


def test_share_file_rejects_an_invalid_role():
    client = _client()
    with pytest.raises(ValueError):
        client.share_file("f1", "friend@example.com", role="owner")
    assert client._service._permissions.calls == []


# ------------------------------------------------------------- error handling
def test_401_becomes_auth_error_naming_the_fix():
    client = _client(exc=_http_error(401))
    with pytest.raises(AuthError) as excinfo:
        client.list_files()
    assert "google-auth" in excinfo.value.user_action


def test_403_becomes_auth_error():
    client = _client(exc=_http_error(403))
    with pytest.raises(AuthError):
        client.ping()


def test_server_error_is_recoverable():
    client = _client(exc=_http_error(500))
    with pytest.raises(IntegrationError) as excinfo:
        client.list_files()
    assert excinfo.value.recoverable is True


def test_client_error_is_not_recoverable():
    client = _client(exc=_http_error(400))
    with pytest.raises(IntegrationError) as excinfo:
        client.list_files()
    assert excinfo.value.recoverable is False


def test_get_storage_quota_reports_usage_and_limit():
    client = _client(about_result={
        "storageQuota": {"usage": "3221225472", "limit": "16106127360"},
    })
    quota = client.get_storage_quota()
    assert quota.usage_bytes == 3221225472
    assert quota.limit_bytes == 16106127360
    assert quota.free_bytes == 16106127360 - 3221225472


def test_get_storage_quota_handles_unlimited_storage():
    """Some Workspace plans report no `limit` field at all rather than a
    huge number — this must not be mistaken for zero free space."""
    client = _client(about_result={"storageQuota": {"usage": "999"}})
    quota = client.get_storage_quota()
    assert quota.limit_bytes is None
    assert quota.free_bytes is None
    assert "unlimited" in quota.spoken()


def test_storage_spoken_form_reads_naturally():
    quota = DriveStorage(usage_bytes=3_000_000_000, limit_bytes=15_000_000_000)
    spoken = quota.spoken()
    assert "3.0 GB used of 15.0 GB" in spoken
    assert "12.0 GB free" in spoken


def test_a_network_failure_is_recoverable():
    client = _client()
    client._service._files.exc = OSError("network unreachable")
    with pytest.raises(IntegrationError) as excinfo:
        client.list_files()
    assert excinfo.value.recoverable is True


# ------------------------------------------------------------------- tools
def test_list_drive_files_tool_reports_no_match(monkeypatch, container):
    from peter.skills.drive.tools import list_drive_files

    monkeypatch.setattr(container, "drive", lambda: _client(pages=[{"files": []}]))
    assert "No matching" in list_drive_files()


def test_read_drive_file_tool_truncates_long_text(monkeypatch, container):
    from peter.skills.drive.tools import read_drive_file

    long_text = "x" * 5000
    client = _client(
        get_result={"id": "f1", "mimeType": "text/plain", "name": "big.txt"},
        media={"f1": long_text.encode("utf-8")},
    )
    monkeypatch.setattr(container, "drive", lambda: client)
    result = read_drive_file(file_id="f1")
    assert result.endswith("(truncated)")
    assert len(result) < 5000


def test_drive_storage_status_tool_reports_free_space(monkeypatch, container):
    from peter.skills.drive.tools import drive_storage_status

    client = _client(about_result={
        "storageQuota": {"usage": "3000000000", "limit": "15000000000"},
    })
    monkeypatch.setattr(container, "drive", lambda: client)

    result = drive_storage_status()

    assert "free" in result
    assert "GB" in result


def test_create_drive_file_tool_rejects_empty_name(monkeypatch, container):
    from peter.skills.drive.tools import create_drive_file

    assert "Give the file a name" in create_drive_file(name="  ", content="x")


def test_trash_drive_file_tool_reports_success(monkeypatch, container):
    from peter.skills.drive.tools import trash_drive_file

    monkeypatch.setattr(container, "drive", lambda: _client())
    assert "Trashed" in trash_drive_file(file_id="f1")


def test_share_drive_file_tool_rejects_empty_email(monkeypatch, container):
    from peter.skills.drive.tools import share_drive_file

    assert "Give an email" in share_drive_file(file_id="f1", email="  ")


def test_share_drive_file_tool_reports_invalid_role(monkeypatch, container):
    from peter.skills.drive.tools import share_drive_file

    monkeypatch.setattr(container, "drive", lambda: _client())
    result = share_drive_file(file_id="f1", email="x@example.com", role="owner")
    assert "role must be one of" in result
