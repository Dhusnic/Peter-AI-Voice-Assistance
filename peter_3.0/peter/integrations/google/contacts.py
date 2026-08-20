"""Google Contacts, via the People API.

Same shape as calendar.py/tasks.py: a thin client over googleapiclient, a
`_call()` wrapper translating HttpError into something with a spoken
`user_action`, built lazily off the shared OAuth module.

The People API has no server-side "find by name substring" — `find_contact`
lists every connection and filters in Python, the same approach
`peter/integrations/phone/adb.py`'s `find_contact` already takes for the
phone's own contacts. This is read-only on purpose (`contacts.readonly`):
resolving "email Amma" to a real address is the job here; writing to your
contacts is not a need this integration exists to serve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.google.auth import build_service

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Contact:
    resource_name: str
    name: str
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)

    def spoken(self) -> str:
        parts = [self.name]
        if self.emails:
            parts.append(f"email {self.emails[0]}")
        if self.phones:
            parts.append(f"phone {self.phones[0]}")
        return ", ".join(parts)


def _to_contact(raw: dict) -> Contact:
    names = raw.get("names") or []
    display = names[0].get("displayName", "") if names else ""
    emails = [e["value"] for e in (raw.get("emailAddresses") or []) if e.get("value")]
    phones = [
        p["value"] for p in (raw.get("phoneNumbers") or []) if p.get("value")
    ]
    return Contact(
        resource_name=raw.get("resourceName", ""),
        name=display or (emails[0] if emails else "(no name)"),
        emails=emails,
        phones=phones,
    )


class ContactsClient:
    def __init__(self, config: Config):
        self.config = config
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self.config, "people", "v1")
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
                        "Run: python -m peter.main --google-auth. If Contacts "
                        "was just enabled, the stored token needs a re-run to "
                        "pick up the new scope."
                    ),
                ) from exc
            raise IntegrationError(
                f"Contacts error while {what}: {exc}",
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
        self._call(
            self.service.people().connections().list(
                resourceName="people/me", pageSize=1, personFields="names"
            ),
            "checking access",
        )
        return True

    def list_contacts(self, limit: int = 500) -> list[Contact]:
        contacts: list[Contact] = []
        page_token = None
        while len(contacts) < limit:
            payload = self._call(
                self.service.people().connections().list(
                    resourceName="people/me",
                    pageSize=min(limit - len(contacts), 200),
                    personFields="names,emailAddresses,phoneNumbers",
                    pageToken=page_token,
                ),
                "listing contacts",
            )
            contacts += [_to_contact(c) for c in payload.get("connections", [])]
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return contacts

    def find_contact(self, name: str) -> list[Contact]:
        needle = name.lower().strip()
        return [c for c in self.list_contacts() if needle in c.name.lower()]
