"""Logging setup.

Two formats: `rich` for a human watching a terminal, `json` for anything that
ships logs somewhere. Chosen in config.yml, because which one you want depends
on where Peter is running, not on the code.

Secrets are stripped by a filter rather than by discipline at each call site.
Discipline fails eventually; a filter does not.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from rich.console import Console

# Credential *shapes* — these are distinctive enough to match without hitting
# ordinary prose. Note what is deliberately absent: a pattern for Gmail app
# passwords. They are sixteen random lowercase letters, which is structurally
# identical to four ordinary English words, so any regex broad enough to catch
# one redacts half your log file ("wake word detected" matches). Literal values
# are handled below instead, which is exact and has no false positives.
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"ya29\.[A-Za-z0-9_\-]{10,}"),          # Google access tokens
    re.compile(r"\b1//[A-Za-z0-9_\-]{20,}"),           # Google refresh tokens
]

# Shortest literal worth scrubbing. Below this, a "secret" is either unset or
# a placeholder, and blanket-replacing it would mangle unrelated text.
_MIN_LITERAL = 8

_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "anthropic",
    "apscheduler.executors",
    "apscheduler.scheduler",
    "googleapiclient.discovery_cache",
    "google_auth_oauthlib",
    "urllib3",
    "faster_whisper",
)


class RedactingFilter(logging.Filter):
    """Scrub credentials out of every record before it is emitted.

    Two mechanisms, because neither alone is enough:

    * **Patterns** catch credential shapes that are unmistakable (`sk-ant-...`,
      `password=...`), including ones from libraries we do not control.
    * **Literals** catch this process's own actual secret values, whatever they
      look like. This is what covers a Gmail app password, which no regex can
      distinguish from prose.

    Redaction only ever rewrites the message — it never drops a record. A
    swallowed log line is a lost incident.
    """

    def __init__(self, literals: Iterable[str] = ()):
        super().__init__()
        self._literals = sorted(
            {s for s in literals if s and len(s) >= _MIN_LITERAL},
            key=len,
            reverse=True,  # longest first, so a substring cannot pre-empt it
        )

    def add_literal(self, value: str) -> None:
        if value and len(value) >= _MIN_LITERAL and value not in self._literals:
            self._literals.append(value)
            self._literals.sort(key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True

        redacted = message
        for literal in self._literals:
            if literal in redacted:
                redacted = redacted.replace(literal, "<redacted>")
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("<redacted>", redacted)

        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    level: str = "INFO",
    fmt: str = "rich",
    log_file: Path | None = None,
    secret_literals: Iterable[str] = (),
    console: "Console | None" = None,
) -> None:
    """Install handlers. Safe to call more than once.

    Args:
        secret_literals: This process's actual secret values, scrubbed from
            every record wherever they appear. main.py passes the loaded
            credentials so a stray f-string cannot leak one to disk.
        console: Text mode's Rich Console, the same one driving the status
            spinner. Without this, RichHandler opens its own Console — a
            second writer to the same terminal that Rich's Live display has
            no way to coordinate with, so a log line lands mid-frame and
            garbles the spinner. Sharing one Console is what makes Rich
            suspend-and-redraw around it instead. Voice mode and the one-shot
            commands (--health, --briefing) have no spinner to collide with,
            so they simply omit it.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    redactor = RedactingFilter(secret_literals)

    if fmt == "json":
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
    else:
        from rich.logging import RichHandler

        handler = RichHandler(
            console=console, rich_tracebacks=True, show_path=False,
            omit_repeated_times=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))

    handler.addFilter(redactor)
    root.addHandler(handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
