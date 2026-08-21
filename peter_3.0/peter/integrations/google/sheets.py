"""Google Sheets, via Sheets API v4.

Same shape as contacts.py/drive.py: a thin client over googleapiclient, a
`_call()` wrapper translating HttpError into something with a spoken
`user_action`, built lazily off the shared OAuth module.

Range values stay plain `list[list[str]]` — the API's own shape — rather than
a dataclass; there is nothing to model beyond what Sheets already returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.auth import build_service


@dataclass(slots=True)
class Spreadsheet:
    id: str
    title: str
    sheet_names: list[str] = field(default_factory=list)
    url: str = ""

    def spoken(self) -> str:
        tabs = f" ({', '.join(self.sheet_names)})" if self.sheet_names else ""
        return f"{self.title}{tabs}"


class SheetsClient:
    def __init__(self, config: Config):
        self.config = config
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self.config, "sheets", "v4")
        return self._service

    def _call(self, request, what: str):
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            if status in (401, 403):
                raise AuthError(
                    f"Google rejected the request ({status}) while {what}",
                    service="google",
                    user_action=(
                        "Run: python -m peter.main --google-auth. If Sheets "
                        "was just enabled, the stored token needs a re-run "
                        "to pick up the new scope."
                    ),
                ) from exc
            if status == 404:
                raise IntegrationError(
                    f"not found while {what}", service="google"
                ) from exc
            raise IntegrationError(
                f"Sheets error while {what}: {exc}",
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

    def create_spreadsheet(self, title: str) -> Spreadsheet:
        raw = self._call(
            self.service.spreadsheets().create(
                body={"properties": {"title": title}},
                fields="spreadsheetId, properties.title, sheets.properties.title, spreadsheetUrl",
            ),
            "creating a spreadsheet",
        )
        return self._to_spreadsheet(raw)

    def list_tabs(self, spreadsheet_id: str) -> list[str]:
        raw = self._call(
            self.service.spreadsheets().get(
                spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
            ),
            "listing sheet tabs",
        )
        return [s["properties"]["title"] for s in raw.get("sheets", [])]

    def read_range(self, spreadsheet_id: str, range_a1: str) -> list[list[str]]:
        raw = self._call(
            self.service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range=range_a1
            ),
            "reading a range",
        )
        return raw.get("values", [])

    def write_range(self, spreadsheet_id: str, range_a1: str, rows: list[list[str]]) -> int:
        raw = self._call(
            self.service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            ),
            "writing a range",
        )
        return int(raw.get("updatedCells", 0))

    def append_rows(self, spreadsheet_id: str, range_a1: str, rows: list[list[str]]) -> int:
        raw = self._call(
            self.service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ),
            "appending rows",
        )
        updates = raw.get("updates", {})
        return int(updates.get("updatedCells", 0))

    @staticmethod
    def _to_spreadsheet(raw: dict) -> Spreadsheet:
        return Spreadsheet(
            id=raw.get("spreadsheetId", ""),
            title=raw.get("properties", {}).get("title", "(untitled)"),
            sheet_names=[s["properties"]["title"] for s in raw.get("sheets", [])],
            url=raw.get("spreadsheetUrl", ""),
        )
