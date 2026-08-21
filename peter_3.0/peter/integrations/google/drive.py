"""Google Drive — full read/write, via Drive API v3.

Same shape as contacts.py/calendar.py: a thin client over googleapiclient, a
`_call()` wrapper translating HttpError into something with a spoken
`user_action`, built lazily off the shared OAuth module.

Deleting is always a trash, never `files().delete()` — matches Keep's
`.trash()` reversibility norm: a voice command that misheard a file name
should be recoverable from the Drive trash, not gone.

This is a general-purpose Drive client, distinct from `peter/docs_index.py`'s
`index_drive_folder`, which indexes a Drive folder for RAG search into the
local `documents` table and has its own read/export logic. The two do not
share code, but `read_file_text` below reuses the same export-Google-Apps-
files-to-text approach.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.auth import build_service

# Google Docs/Sheets/Slides have no native downloadable content — they must be
# exported. Everything else is fetched as raw bytes via get_media.
_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

_VALID_ROLES = {"reader", "commenter", "writer"}

_FILE_FIELDS = "id, name, mimeType, modifiedTime, size, parents, webViewLink, trashed"


@dataclass(slots=True)
class DriveStorage:
    """Account-wide usage from `about().get()` — distinct from any single
    file's size. `limit_bytes` is None for unlimited storage (some Workspace
    plans report no `storageQuota.limit` at all rather than a huge number)."""

    usage_bytes: int
    limit_bytes: int | None

    @property
    def free_bytes(self) -> int | None:
        if self.limit_bytes is None:
            return None
        return max(0, self.limit_bytes - self.usage_bytes)

    def spoken(self) -> str:
        used = f"{self.usage_bytes / 1e9:.1f} GB"
        if self.limit_bytes is None:
            return f"{used} used. Storage is unlimited on this account."
        total = f"{self.limit_bytes / 1e9:.1f} GB"
        free = f"{self.free_bytes / 1e9:.1f} GB"
        percent = self.usage_bytes / self.limit_bytes * 100 if self.limit_bytes else 0
        return f"{used} used of {total} ({percent:.0f}%), {free} free."


@dataclass(slots=True)
class DriveFile:
    id: str
    name: str
    mime_type: str = ""
    modified_time: str = ""
    size: int = 0
    parents: list[str] = field(default_factory=list)
    web_view_link: str = ""
    trashed: bool = False

    def spoken(self) -> str:
        kind = "folder" if self.mime_type == "application/vnd.google-apps.folder" else "file"
        when = f", modified {self.modified_time[:10]}" if self.modified_time else ""
        return f"{self.name} ({kind}){when}"


def _to_file(raw: dict) -> DriveFile:
    return DriveFile(
        id=raw.get("id", ""),
        name=raw.get("name", "(untitled)"),
        mime_type=raw.get("mimeType", ""),
        modified_time=raw.get("modifiedTime", ""),
        size=int(raw.get("size") or 0),
        parents=list(raw.get("parents") or []),
        web_view_link=raw.get("webViewLink", ""),
        trashed=bool(raw.get("trashed", False)),
    )


class DriveClient:
    def __init__(self, config: Config):
        self.config = config
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self.config, "drive", "v3")
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
                        "Run: python -m peter.main --google-auth. If Drive "
                        "write access was just enabled, the stored token "
                        "needs a re-run to pick up the new scope."
                    ),
                ) from exc
            if status == 404:
                raise IntegrationError(
                    f"not found while {what}", service="google"
                ) from exc
            raise IntegrationError(
                f"Drive error while {what}: {exc}",
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

    def ping(self) -> bool:
        self._call(self.service.files().list(pageSize=1, fields="files(id)"), "checking access")
        return True

    def get_storage_quota(self) -> DriveStorage:
        raw = self._call(
            self.service.about().get(fields="storageQuota"), "checking storage"
        )
        quota = raw.get("storageQuota", {})
        limit = quota.get("limit")
        return DriveStorage(
            usage_bytes=int(quota.get("usage") or 0),
            limit_bytes=int(limit) if limit is not None else None,
        )

    # -------------------------------------------------------------- reading
    def list_files(self, folder_id: str = "", query: str = "", limit: int = 20) -> list[DriveFile]:
        clauses = ["trashed = false"]
        if folder_id.strip():
            clauses.append(f"'{folder_id.strip()}' in parents")
        if query.strip():
            escaped = query.strip().replace("'", "\\'")
            clauses.append(f"name contains '{escaped}'")

        payload = self._call(
            self.service.files().list(
                q=" and ".join(clauses),
                pageSize=min(limit, 100),
                fields=f"files({_FILE_FIELDS})",
            ),
            "listing files",
        )
        return [_to_file(f) for f in payload.get("files", [])]

    def search_files(self, text: str, limit: int = 20) -> list[DriveFile]:
        return self.list_files(query=text, limit=limit)

    def get_file(self, file_id: str) -> DriveFile:
        raw = self._call(
            self.service.files().get(fileId=file_id, fields=_FILE_FIELDS),
            "getting file metadata",
        )
        return _to_file(raw)

    def read_file_text(self, file_id: str) -> str:
        meta = self.get_file(file_id)
        export_mime = _EXPORT_MIME.get(meta.mime_type)
        if export_mime:
            raw = self._call(
                self.service.files().export(fileId=file_id, mimeType=export_mime),
                "reading a file",
            )
        else:
            raw = self._call(
                self.service.files().get_media(fileId=file_id), "reading a file"
            )

        if isinstance(raw, bytes):
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                raise IntegrationError(
                    f"{meta.name!r} is not a text-readable file", service="google",
                )
        return str(raw)

    # -------------------------------------------------------------- writing
    def create_text_file(
        self, name: str, content: str, folder_id: str = "", mime_type: str = "text/plain"
    ) -> DriveFile:
        from googleapiclient.http import MediaInMemoryUpload

        body: dict = {"name": name, "mimeType": mime_type}
        if folder_id.strip():
            body["parents"] = [folder_id.strip()]
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mime_type)
        raw = self._call(
            self.service.files().create(body=body, media_body=media, fields=_FILE_FIELDS),
            "creating a file",
        )
        return _to_file(raw)

    def create_folder(self, name: str, parent_id: str = "") -> DriveFile:
        body: dict = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id.strip():
            body["parents"] = [parent_id.strip()]
        raw = self._call(
            self.service.files().create(body=body, fields=_FILE_FIELDS),
            "creating a folder",
        )
        return _to_file(raw)

    def move_file(self, file_id: str, new_folder_id: str) -> DriveFile:
        current = self.get_file(file_id)
        raw = self._call(
            self.service.files().update(
                fileId=file_id,
                addParents=new_folder_id,
                removeParents=",".join(current.parents),
                fields=_FILE_FIELDS,
            ),
            "moving a file",
        )
        return _to_file(raw)

    def rename_file(self, file_id: str, new_name: str) -> DriveFile:
        raw = self._call(
            self.service.files().update(
                fileId=file_id, body={"name": new_name}, fields=_FILE_FIELDS
            ),
            "renaming a file",
        )
        return _to_file(raw)

    def trash_file(self, file_id: str) -> DriveFile:
        raw = self._call(
            self.service.files().update(
                fileId=file_id, body={"trashed": True}, fields=_FILE_FIELDS
            ),
            "trashing a file",
        )
        return _to_file(raw)

    def share_file(self, file_id: str, email: str, role: str = "reader") -> None:
        if role not in _VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}, got {role!r}")
        self._call(
            self.service.permissions().create(
                fileId=file_id,
                body={"type": "user", "role": role, "emailAddress": email},
                sendNotificationEmail=False,
            ),
            "sharing a file",
        )

    def upload_file(self, local_path: str, folder_id: str = "", name: str = "") -> DriveFile:
        from pathlib import Path

        from googleapiclient.http import MediaFileUpload

        path = Path(local_path)
        body: dict = {"name": name or path.name}
        if folder_id.strip():
            body["parents"] = [folder_id.strip()]
        media = MediaFileUpload(str(path), resumable=False)
        raw = self._call(
            self.service.files().create(body=body, media_body=media, fields=_FILE_FIELDS),
            "uploading a file",
        )
        return _to_file(raw)

    def download_file(self, file_id: str, local_path: str) -> None:
        from pathlib import Path

        meta = self.get_file(file_id)
        export_mime = _EXPORT_MIME.get(meta.mime_type)
        if export_mime:
            raw = self._call(
                self.service.files().export(fileId=file_id, mimeType=export_mime),
                "downloading a file",
            )
        else:
            raw = self._call(
                self.service.files().get_media(fileId=file_id), "downloading a file"
            )
        Path(local_path).write_bytes(raw if isinstance(raw, bytes) else str(raw).encode("utf-8"))
