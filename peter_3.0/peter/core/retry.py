"""Retry with exponential backoff and jitter.

Deliberately narrow: it retries only `IntegrationError` marked `recoverable`,
plus the socket-level exceptions the caller names. Everything else propagates
immediately.

That restraint is the whole point. A blanket "retry on any exception" turns a
wrong password into three failed logins and a locked account, and turns a
programming error into the same programming error three times, more slowly.
"""

from __future__ import annotations

import functools
import logging
import random
import time
from typing import Callable, TypeVar

from peter.core.errors import IntegrationError

log = logging.getLogger(__name__)

T = TypeVar("T")


# (attempt, attempts, delay_seconds, error) -> None. Called right before the
# sleep on every retry, e.g. to speak/print "retrying (2/5) in 8s...".
OnRetry = Callable[[int, int, float, BaseException], None]


def call_with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retry_on: tuple[type[BaseException], ...] = (),
    on_retry: OnRetry | None = None,
) -> T:
    """Call `fn` (no args — wrap with a lambda/partial), retrying on transient
    failure. Same policy as `retry()` below, but usable at a single call site
    instead of a whole function, and able to report each attempt as it happens.
    """
    last: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except IntegrationError as exc:
            if not exc.recoverable:
                raise
            last = exc
        except retry_on as exc:  # type: ignore[misc]
            last = exc

        if attempt == attempts:
            break

        # Full jitter: spreads retries out so concurrent callers do not
        # all wake up and hammer the same recovering service together.
        delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
        delay = random.uniform(0, delay)
        log.warning(
            "call failed (%s), retrying in %.1fs [%d/%d]",
            last, delay, attempt, attempts - 1,
        )
        if on_retry is not None:
            try:
                on_retry(attempt, attempts, delay, last)
            except Exception:
                log.debug("on_retry callback raised", exc_info=True)
        time.sleep(delay)

    assert last is not None
    raise last


def retry(
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retry_on: tuple[type[BaseException], ...] = (),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry a call that failed for a transient reason.

    Args:
        attempts: Total tries, including the first. 3 means two retries.
        base_delay: Seconds before the first retry; doubles each time.
        max_delay: Ceiling for the delay.
        retry_on: Extra exception types to treat as transient — typically
            socket.error, imaplib.IMAP4.abort, or similar.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            return call_with_retry(
                lambda: fn(*args, **kwargs),
                attempts=attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                retry_on=retry_on,
            )

        return wrapper

    return decorator
