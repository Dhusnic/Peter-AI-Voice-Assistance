"""Google Docs, via Docs API v1 for writing and Drive's export endpoint for
reading.

Named `gdocs` throughout — module, `GDocsClient`, and the `services().gdocs()`
accessor — because `services().docs()` already means something else: the
local RAG `DocIndex` store in `peter/docs_index.py`. Reusing `docs` here
would silently shadow that accessor.

Reading uses Drive's `files().export(mimeType="text/plain")`, the same proven
path `peter/docs_index.py`'s `index_drive_folder` already uses for indexing
Google Docs, rather than parsing the Docs API's structural JSON body — far
simpler for "give me the text," and one less thing to get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.auth import build_service


@dataclass(slots=True)
class GDoc:
    id: str
    title: str
    url: str = ""

    def spoken(self) -> str:
        return self.title


class GDocsClient:
    def __init__(self, config: Config):
        self.config = config
        self._service = None
        self._drive_service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self.config, "docs", "v1")
        return self._service

    @property
    def _drive(self):
        if self._drive_service is None:
            self._drive_service = build_service(self.config, "drive", "v3")
        return self._drive_service

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
                        "Run: python -m peter.main --google-auth. If Docs "
                        "was just enabled, the stored token needs a re-run "
                        "to pick up the new scope."
                    ),
                ) from exc
            if status == 404:
                raise IntegrationError(
                    f"not found while {what}", service="google"
                ) from exc
            raise IntegrationError(
                f"Docs error while {what}: {exc}",
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

    def create_doc(self, title: str, text: str = "") -> GDoc:
        raw = self._call(
            self.service.documents().create(body={"title": title}),
            "creating a document",
        )
        doc = GDoc(
            id=raw.get("documentId", ""),
            title=raw.get("title", title),
            url=f"https://docs.google.com/document/d/{raw.get('documentId', '')}/edit",
        )
        if text.strip():
            self.append_text(doc.id, text)
        return doc

    def read_doc(self, document_id: str) -> str:
        raw = self._call(
            self._drive.files().export(fileId=document_id, mimeType="text/plain"),
            "reading a document",
        )
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    def append_text(self, document_id: str, text: str) -> None:
        self._call(
            self.service.documents().batchUpdate(
                documentId=document_id,
                body={
                    "requests": [
                        {
                            "insertText": {
                                "text": text,
                                "endOfSegmentLocation": {"segmentId": ""},
                            }
                        }
                    ]
                },
            ),
            "appending text",
        )
