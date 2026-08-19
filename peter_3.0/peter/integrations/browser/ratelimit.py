"""Per-domain politeness.

The single most effective thing you can do to avoid getting your account
banned is **not hitting the site very often**. Fingerprint evasion, header
spoofing and proxy rotation are an arms race you lose; a request rate that
looks like a person browsing is not.

So this is deliberately blunt: a minimum interval between requests to the same
domain, enforced by sleeping. There is no burst allowance and no queue depth to
tune. If a caller wants to check ten prices, it takes ten intervals.

Phase 4's trackers will poll on a 30-60 minute cadence, which sits far inside
any rate this enforces. The limiter exists to catch the case where something —
a retry loop, an over-eager agent turn — tries to go faster than a human could.
"""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def domain_of(url: str) -> str:
    """Registrable-ish domain, so www.amazon.in and amazon.in share a budget."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


class RateLimiter:
    """Enforces a minimum gap between requests to any one domain."""

    def __init__(self, min_interval_seconds: float = 20.0):
        self.min_interval = max(0.0, min_interval_seconds)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> float:
        """Block until this domain may be hit again. Returns seconds waited."""
        if self.min_interval <= 0:
            return 0.0

        domain = domain_of(url)
        if not domain:
            return 0.0

        with self._lock:
            now = time.monotonic()
            last = self._last.get(domain)
            delay = 0.0
            if last is not None:
                elapsed = now - last
                if elapsed < self.min_interval:
                    delay = self.min_interval - elapsed
            # Reserve the slot before releasing the lock, so two threads racing
            # on the same domain queue up instead of both deciding they may go.
            self._last[domain] = now + delay

        if delay > 0:
            log.info("waiting %.0fs before hitting %s again", delay, domain)
            time.sleep(delay)
        return delay

    def time_until_ready(self, url: str) -> float:
        """How long a wait() would block, without blocking. For status output."""
        if self.min_interval <= 0:
            return 0.0
        domain = domain_of(url)
        with self._lock:
            last = self._last.get(domain)
        if last is None:
            return 0.0
        return max(0.0, self.min_interval - (time.monotonic() - last))

    def reset(self, url: str | None = None) -> None:
        with self._lock:
            if url is None:
                self._last.clear()
            else:
                self._last.pop(domain_of(url), None)
