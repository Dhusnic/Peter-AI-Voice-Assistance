"""Core infrastructure: error semantics, retry restraint, log redaction."""

import logging

import pytest

from peter.core.errors import (
    AuthError,
    ConfigError,
    IntegrationError,
    NotConfiguredError,
    PeterError,
)
from peter.core.logging import RedactingFilter
from peter.core.retry import retry


# ------------------------------------------------------------------ errors
def test_everything_shares_one_root():
    for exc in (ConfigError("x"), IntegrationError("x"), AuthError("x"),
                NotConfiguredError("mail")):
        assert isinstance(exc, PeterError)


def test_auth_errors_are_never_retryable():
    """Retrying a wrong password just locks the account."""
    assert AuthError("bad password", service="mail").recoverable is False


def test_not_configured_is_distinct_from_broken():
    exc = NotConfiguredError("mail", "Set PETER_MAIL_ADDRESS in .env.")
    assert isinstance(exc, IntegrationError)
    assert exc.recoverable is False
    assert "PETER_MAIL_ADDRESS" in exc.spoken()


def test_spoken_includes_the_user_action():
    exc = IntegrationError(
        "socket closed", service="mail", user_action="Check your internet connection."
    )
    spoken = exc.spoken()
    assert "mail" in spoken
    assert "Check your internet connection." in spoken


def test_spoken_without_a_user_action_still_reads_as_a_sentence():
    assert "no route to host" in IntegrationError(
        "no route to host", service="mail"
    ).spoken()


# ------------------------------------------------------------------- retry
def test_successful_call_is_not_retried():
    calls = []

    @retry(attempts=3, base_delay=0)
    def works():
        calls.append(1)
        return "ok"

    assert works() == "ok"
    assert len(calls) == 1


def test_recoverable_error_is_retried_then_succeeds():
    calls = []

    @retry(attempts=3, base_delay=0)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise IntegrationError("socket reset", service="mail", recoverable=True)
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 3


def test_unrecoverable_error_is_not_retried():
    """The restraint that keeps a wrong password from becoming three attempts."""
    calls = []

    @retry(attempts=5, base_delay=0)
    def bad_credentials():
        calls.append(1)
        raise AuthError("rejected", service="mail")

    with pytest.raises(AuthError):
        bad_credentials()
    assert len(calls) == 1


def test_unrelated_exception_propagates_immediately():
    """A programming error must surface at once, not three times, slowly."""
    calls = []

    @retry(attempts=5, base_delay=0)
    def broken():
        calls.append(1)
        raise ValueError("this is a bug, not a network blip")

    with pytest.raises(ValueError):
        broken()
    assert len(calls) == 1


def test_named_exception_types_are_retried():
    calls = []

    @retry(attempts=3, base_delay=0, retry_on=(OSError,))
    def flaky_socket():
        calls.append(1)
        raise OSError("connection reset")

    with pytest.raises(OSError):
        flaky_socket()
    assert len(calls) == 3


def test_attempts_are_exhausted_then_the_last_error_is_raised():
    @retry(attempts=2, base_delay=0)
    def always_fails():
        raise IntegrationError("still down", service="mail", recoverable=True)

    with pytest.raises(IntegrationError, match="still down"):
        always_fails()


def test_retry_preserves_the_function_identity():
    @retry(attempts=2, base_delay=0)
    def documented_function(x: int) -> int:
        """A docstring that must survive decoration."""
        return x

    assert documented_function.__name__ == "documented_function"
    assert "must survive" in documented_function.__doc__
    assert documented_function(5) == 5


# --------------------------------------------------------------- redaction
def record(message: str, *args) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, "f", 1, message, args, None)


@pytest.mark.parametrize(
    "message",
    [
        "connecting with sk-ant-api03-abcdefghijklmnop",
        "password=hunter2",
        "api_key: abcd1234efgh",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdef",
        "token: ya29.a0AfH6SMBabcdefghijkl",
    ],
)
def test_credential_shapes_are_scrubbed(message):
    rec = record(message)
    RedactingFilter().filter(rec)
    assert "<redacted>" in rec.getMessage()


def test_literal_secrets_are_scrubbed_whatever_they_look_like():
    """A Gmail app password is 16 random lowercase letters — no regex can spot
    it without also matching prose, so the real value is scrubbed by literal."""
    rec = record("IMAP login as me@example.com using abcdefghijklmnop")
    RedactingFilter(["abcdefghijklmnop"]).filter(rec)

    message = rec.getMessage()
    assert "abcdefghijklmnop" not in message
    assert "me@example.com" in message, "only the secret should go"


def test_short_literals_are_ignored():
    """Scrubbing a 3-character 'secret' would mangle unrelated text."""
    rec = record("the cat sat on the mat")
    RedactingFilter(["cat"]).filter(rec)
    assert rec.getMessage() == "the cat sat on the mat"


def test_ordinary_messages_are_untouched():
    """The regression that motivated dropping the app-password pattern:
    'wake word detected' is four groups of four lowercase letters."""
    for text in (
        "wake word detected (score 0.87)",
        "mail client ready (dhusnic@example.com)",
        "reminder fired: call amma back home soon",
    ):
        rec = record(text)
        RedactingFilter().filter(rec)
        assert rec.getMessage() == text


def test_redaction_survives_lazy_format_arguments():
    rec = record("connecting as %s with password=%s", "me@example.com", "hunter2")
    RedactingFilter().filter(rec)
    assert "hunter2" not in rec.getMessage()


def test_filter_never_blocks_a_record():
    """Redaction must scrub, never swallow — a dropped log is a lost incident."""
    rec = record("anything at all")
    assert RedactingFilter().filter(rec) is True
