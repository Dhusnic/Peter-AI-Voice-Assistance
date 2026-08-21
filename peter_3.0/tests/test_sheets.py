"""Google Sheets.

No real network access: googleapiclient's chained call interface is faked
with plain objects, same `_call()`/HttpError-translation pattern
test_contacts.py and test_drive.py already establish.
"""

from __future__ import annotations

import pytest

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.sheets import SheetsClient, Spreadsheet


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


class _FakeValues:
    def __init__(self, exc=None, values_result=None):
        self.exc = exc
        self.values_result = values_result if values_result is not None else {"values": []}
        self.calls: list[tuple[str, dict]] = []

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        return _FakeRequest(self.values_result)

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        rows = kwargs["body"]["values"]
        cells = sum(len(r) for r in rows)
        return _FakeRequest({"updatedCells": cells})

    def append(self, **kwargs):
        self.calls.append(("append", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        rows = kwargs["body"]["values"]
        cells = sum(len(r) for r in rows)
        return _FakeRequest({"updates": {"updatedCells": cells}})


class _FakeSpreadsheets:
    def __init__(self, exc=None, create_result=None, get_result=None, **value_kwargs):
        self.exc = exc
        self.create_result = create_result
        self.get_result = get_result
        self._values = _FakeValues(exc=exc, **value_kwargs)
        self.calls: list[tuple[str, dict]] = []

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        title = kwargs["body"]["properties"]["title"]
        return _FakeRequest(self.create_result or {
            "spreadsheetId": "sheet1", "properties": {"title": title},
            "sheets": [{"properties": {"title": "Sheet1"}}],
            "spreadsheetUrl": "https://x",
        })

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        if self.exc is not None:
            return _FakeRequest(exc=self.exc)
        return _FakeRequest(self.get_result or {"sheets": [{"properties": {"title": "Sheet1"}}]})

    def values(self):
        return self._values


class _FakeService:
    def __init__(self, **kwargs):
        self._spreadsheets = _FakeSpreadsheets(**kwargs)

    def spreadsheets(self):
        return self._spreadsheets


def _client(**kwargs) -> SheetsClient:
    client = SheetsClient(Config())
    client._service = _FakeService(**kwargs)
    return client


# ----------------------------------------------------------------- creating
def test_create_spreadsheet_returns_title_and_id():
    client = _client()
    sheet = client.create_spreadsheet("Budget 2026")
    assert sheet.id == "sheet1"
    assert sheet.title == "Budget 2026"
    assert sheet.sheet_names == ["Sheet1"]


def test_spreadsheet_spoken_lists_tabs():
    sheet = Spreadsheet("s1", "Budget", sheet_names=["Jan", "Feb"])
    assert "Jan" in sheet.spoken() and "Feb" in sheet.spoken()


def test_list_tabs_reads_sheet_titles():
    client = _client(get_result={"sheets": [
        {"properties": {"title": "Jan"}}, {"properties": {"title": "Feb"}},
    ]})
    assert client.list_tabs("sheet1") == ["Jan", "Feb"]


# ------------------------------------------------------------------ ranges
def test_read_range_returns_values():
    client = _client(values_result={"values": [["a", "b"], ["c", "d"]]})
    rows = client.read_range("sheet1", "Sheet1!A1:B2")
    assert rows == [["a", "b"], ["c", "d"]]


def test_read_range_returns_empty_list_for_empty_range():
    client = _client()
    assert client.read_range("sheet1", "Sheet1!A1:B2") == []


def test_write_range_uses_user_entered_and_returns_cell_count():
    client = _client()
    updated = client.write_range("sheet1", "Sheet1!A1", [["x", "y"]])
    assert updated == 2
    kwargs = client._service._spreadsheets._values.calls[-1][1]
    assert kwargs["valueInputOption"] == "USER_ENTERED"


def test_append_rows_inserts_new_rows():
    client = _client()
    updated = client.append_rows("sheet1", "Sheet1!A:B", [["Alice", "10"]])
    assert updated == 2
    kwargs = client._service._spreadsheets._values.calls[-1][1]
    assert kwargs["insertDataOption"] == "INSERT_ROWS"


# ------------------------------------------------------------- error handling
def test_401_becomes_auth_error_naming_the_fix():
    client = _client(exc=_http_error(401))
    with pytest.raises(AuthError) as excinfo:
        client.list_tabs("sheet1")
    assert "google-auth" in excinfo.value.user_action


def test_server_error_is_recoverable():
    client = _client(exc=_http_error(500))
    with pytest.raises(IntegrationError) as excinfo:
        client.read_range("sheet1", "A1")
    assert excinfo.value.recoverable is True


def test_a_network_failure_is_recoverable():
    client = _client()
    client._service._spreadsheets.exc = OSError("network unreachable")
    client._service._spreadsheets._values.exc = OSError("network unreachable")
    with pytest.raises(IntegrationError) as excinfo:
        client.read_range("sheet1", "A1")
    assert excinfo.value.recoverable is True


# ------------------------------------------------------------------- tools
def test_create_google_sheet_tool_rejects_empty_title():
    from peter.skills.sheets.tools import create_google_sheet

    assert "Give the spreadsheet a title" in create_google_sheet(title="  ")


def test_read_sheet_range_tool_reports_empty_range(monkeypatch, container):
    from peter.skills.sheets.tools import read_sheet_range

    monkeypatch.setattr(container, "sheets", lambda: _client())
    assert "empty" in read_sheet_range(spreadsheet_id="s1", range_a1="A1:B2")


def test_write_sheet_range_tool_parses_csv_grammar(monkeypatch, container):
    from peter.skills.sheets.tools import write_sheet_range

    client = _client()
    monkeypatch.setattr(container, "sheets", lambda: client)
    result = write_sheet_range(spreadsheet_id="s1", range_a1="A1", values_csv="a,b;c,d")
    assert "Updated" in result
    body = client._service._spreadsheets._values.calls[-1][1]["body"]
    assert body["values"] == [["a", "b"], ["c", "d"]]


def test_write_sheet_range_tool_rejects_malformed_csv():
    from peter.skills.sheets.tools import write_sheet_range

    result = write_sheet_range(spreadsheet_id="s1", range_a1="A1", values_csv="   ")
    assert "Give values like" in result


def test_append_sheet_rows_tool_reports_row_count(monkeypatch, container):
    from peter.skills.sheets.tools import append_sheet_rows

    monkeypatch.setattr(container, "sheets", lambda: _client())
    result = append_sheet_rows(spreadsheet_id="s1", range_a1="A:B", values_csv="Alice,10")
    assert "Appended 1 row" in result
