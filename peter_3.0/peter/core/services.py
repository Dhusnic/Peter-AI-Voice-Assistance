"""Service container.

Tool functions must keep clean signatures — Claude's JSON schema is derived from
them, so a `store: MemoryStore` parameter would become an argument Claude has to
invent. Tools reach for their dependencies here instead.

Integrations are built **lazily**. Constructing an IMAP connection at startup
would mean Peter refuses to boot when the wifi is down, and would add several
seconds to launch for a feature you might not use that session. They connect on
first use and cache the client afterwards.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Optional

from peter.core.config import Config, get_config
from peter.core.errors import NotConfiguredError

if TYPE_CHECKING:  # pragma: no cover
    from peter.agent.brain import Brain
    from peter.integrations.browser.manager import BrowserManager
    from peter.integrations.google.calendar import CalendarClient
    from peter.integrations.google.tasks import TasksClient
    from peter.integrations.mail.client import MailClient
    from peter.memory.store import MemoryStore
    from peter.policy.audit import AuditLog
    from peter.scheduler.jobs import Scheduler

log = logging.getLogger(__name__)


class ServiceContainer:
    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self._lock = threading.Lock()

        # Eager — cheap, local, and needed by almost every turn.
        self.memory: Optional["MemoryStore"] = None
        self.scheduler: Optional["Scheduler"] = None
        self.audit: Optional["AuditLog"] = None
        self.speaker = None
        # Set by main.py. Lets the LLM tools inspect and switch the provider.
        self.brain: Optional["Brain"] = None

        # Lazy — networked, and optional.
        self._mail: Optional["MailClient"] = None
        self._calendar: Optional["CalendarClient"] = None
        self._tasks: Optional["TasksClient"] = None
        self._browser: Optional["BrowserManager"] = None

    # ------------------------------------------------------------ eager
    def require_memory(self) -> "MemoryStore":
        if self.memory is None:
            raise RuntimeError("memory store not initialised")
        return self.memory

    def require_scheduler(self) -> "Scheduler":
        if self.scheduler is None:
            raise RuntimeError("scheduler not initialised")
        return self.scheduler

    def say(self, text: str) -> None:
        """Speak if a voice backend exists; print otherwise."""
        if self.speaker is not None:
            self.speaker.say(text)
        else:
            print(f"[{self.config.app.assistant_name.lower()}] {text}")

    # ------------------------------------------------------------- lazy
    def mail(self) -> "MailClient":
        cfg = self.config.integrations.mail
        if not cfg.enabled:
            raise NotConfiguredError(
                "mail", "Set integrations.mail.enabled to true in config.yml."
            )
        if not self.config.secrets.has_mail:
            raise NotConfiguredError(
                "mail",
                "Set PETER_MAIL_ADDRESS and PETER_MAIL_APP_PASSWORD in .env. "
                "The app password comes from your Google account security page.",
            )

        with self._lock:
            if self._mail is None:
                from peter.integrations.mail.client import MailClient

                self._mail = MailClient(cfg, self.config.secrets)
                log.info("mail client ready (%s)", self.config.secrets.mail_address)
        return self._mail

    def calendar(self) -> "CalendarClient":
        self._require_google()
        with self._lock:
            if self._calendar is None:
                from peter.integrations.google.calendar import CalendarClient

                self._calendar = CalendarClient(self.config)
        return self._calendar

    def tasks(self) -> "TasksClient":
        self._require_google()
        with self._lock:
            if self._tasks is None:
                from peter.integrations.google.tasks import TasksClient

                self._tasks = TasksClient(self.config)
        return self._tasks

    def browser(self) -> "BrowserManager":
        cfg = self.config.integrations.browser
        if not cfg.enabled:
            raise NotConfiguredError(
                "browser", "Set integrations.browser.enabled to true in config.yml."
            )
        with self._lock:
            if self._browser is None:
                from peter.integrations.browser.manager import BrowserManager

                self._browser = BrowserManager(cfg, self.config.browser_profile_dir)
        return self._browser

    def _require_google(self) -> None:
        cfg = self.config.integrations.google
        if not cfg.enabled:
            raise NotConfiguredError(
                "google", "Set integrations.google.enabled to true in config.yml."
            )
        if not self.config.secrets.has_google:
            raise NotConfiguredError(
                "google",
                "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in "
                ".env, then run: python -m peter.main --google-auth",
            )

    # ---------------------------------------------------------- lifecycle
    def close(self) -> None:
        if self._mail is not None:
            try:
                self._mail.close()
            except Exception:
                log.debug("mail client close failed", exc_info=True)
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                log.debug("browser close failed", exc_info=True)
        if self.memory is not None:
            self.memory.close()

    def health(self) -> dict[str, str]:
        """Report each subsystem's state. Used by --health and the tray."""
        report: dict[str, str] = {
            "memory": "ok" if self.memory is not None else "not initialised",
            "scheduler": "ok" if self.scheduler is not None else "not initialised",
        }

        from peter.llm import factory

        for provider in factory.PROVIDERS:
            if factory.api_key_for(self.config, provider):
                model = self.config.agent.models.get(provider, "?")
                if provider == "gemini" and model.strip().lower() == "auto":
                    ga = self.config.agent.gemini_auto
                    model = f"auto ({ga.light_model} / {ga.heavy_model})"
                active = " (active)" if provider == self.config.agent.provider else ""
                report[provider] = f"ok - {model}{active}"
            else:
                report[provider] = "no API key"

        mail_cfg = self.config.integrations.mail
        if not mail_cfg.enabled:
            report["mail"] = "disabled"
        elif not self.config.secrets.has_mail:
            report["mail"] = "no credentials in .env"
        else:
            try:
                self.mail().ping()
                report["mail"] = "ok"
            except Exception as exc:
                report["mail"] = f"failed: {exc}"

        google_cfg = self.config.integrations.google
        if not google_cfg.enabled:
            report["google"] = "disabled"
        elif not self.config.secrets.has_google:
            report["google"] = "no credentials in .env"
        elif not self.config.google_token_path.exists():
            report["google"] = "not authorised — run --google-auth"
        else:
            try:
                self.calendar().ping()
                report["google"] = "ok"
            except Exception as exc:
                report["google"] = f"failed: {exc}"

        browser_cfg = self.config.integrations.browser
        if not browser_cfg.enabled:
            report["browser"] = "disabled"
        else:
            profile = self.config.browser_profile_dir
            report["browser"] = (
                "ok" if profile.exists()
                else "ready (no profile yet - run browser_login)"
            )

        return report


# The process-wide container. Assigned by main.py at startup; tests build their
# own and swap it in.
_container: ServiceContainer | None = None


def set_container(container: ServiceContainer | None) -> None:
    global _container
    _container = container


def services() -> ServiceContainer:
    global _container
    if _container is None:
        _container = ServiceContainer()
    return _container
