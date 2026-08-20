"""Exception hierarchy.

One root (`PeterError`) so callers can catch everything Peter raises without
also swallowing `KeyboardInterrupt` or a genuine bug in the standard library —
the thing peter_1.0's bare `except: pass` got wrong.

The split that matters is `IntegrationError.recoverable`. A dropped IMAP socket
should be retried; a wrong password should not. Retrying a credential failure
just locks the account.
"""

from __future__ import annotations


class PeterError(Exception):
    """Base for everything Peter raises deliberately."""


class ConfigError(PeterError):
    """config.yml or .env is missing, malformed, or invalid."""


class IntegrationError(PeterError):
    """An external service failed.

    Args:
        message: What went wrong, phrased for a log line.
        service: Which integration, e.g. "mail" or "google".
        recoverable: True if retrying the same call might succeed.
        user_action: What the human must do, if anything. Surfaced verbatim.
    """

    def __init__(
        self,
        message: str,
        *,
        service: str = "",
        recoverable: bool = False,
        user_action: str = "",
    ):
        super().__init__(message)
        self.service = service
        self.recoverable = recoverable
        self.user_action = user_action

    def spoken(self) -> str:
        """A one-line explanation suitable for reading aloud."""
        where = f" with {self.service}" if self.service else ""
        if self.user_action:
            return f"Something went wrong{where}. {self.user_action}"
        return f"Something went wrong{where}: {self}"


class AuthError(IntegrationError):
    """Credentials are missing, wrong, or expired.

    Never recoverable by retrying — it needs a human to fix a credential.
    """

    def __init__(self, message: str, *, service: str = "", user_action: str = ""):
        super().__init__(
            message, service=service, recoverable=False, user_action=user_action
        )


class NotConfiguredError(IntegrationError):
    """The integration is switched off, or its secrets were never filled in.

    Distinct from AuthError: nothing is broken, the feature just was not set up.
    """

    def __init__(self, service: str, user_action: str = ""):
        super().__init__(
            f"{service} is not configured",
            service=service,
            recoverable=False,
            user_action=user_action,
        )


class ToolError(PeterError):
    """A tool could not do what was asked, for an ordinary reason."""


class VoiceError(IntegrationError):
    """Mic, wake-word, STT, or TTS failed.

    Defaults to recoverable=True: almost every voice failure (a dropped audio
    callback, a flaky Edge TTS request, one bad Whisper call) is worth trying
    again next turn rather than treating as fatal. Startup-time failures
    (missing model file, bad device index) pass recoverable=False explicitly.
    """

    def __init__(self, message: str, *, recoverable: bool = True, user_action: str = ""):
        super().__init__(
            message, service="voice", recoverable=recoverable, user_action=user_action
        )
