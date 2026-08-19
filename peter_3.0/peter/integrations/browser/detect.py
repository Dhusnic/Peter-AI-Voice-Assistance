"""Bot-wall detection.

When a site puts up a CAPTCHA or a challenge page, that is a **stop signal**.
Peter surfaces it and asks the human to take over.

Peter does not solve it, does not route it to a solving service, and does not
retry behind a different fingerprint. Two reasons, and the second is the one
that matters:

1. Evasion is an arms race against companies with more engineers than this
   project has files, and losing it escalates from a CAPTCHA to a banned
   account — the account being risked is the user's real one.
2. A challenge is the site stating a preference. Working around it is the point
   at which "automating my own browsing" becomes "circumventing an access
   control", and that is not a line this project crosses.

Detection is signature-based and errs toward false positives. Wrongly stopping
is a small cost; wrongly continuing means hammering a site that already asked
you to stop.
"""

from __future__ import annotations

import re

from peter.core.errors import IntegrationError


class BotWallError(IntegrationError):
    """The site is showing a challenge. A human must take over."""

    def __init__(self, url: str, signal: str):
        super().__init__(
            f"blocked by a bot check on {url} ({signal})",
            service="browser",
            recoverable=False,
            user_action=(
                "Open the browser window, solve the check by hand, then ask "
                "again. If it keeps happening, that site is rate-limiting you — "
                "leave it alone for a few hours."
            ),
        )
        self.url = url
        self.signal = signal


# Text that appears on challenge interstitials. Matched case-insensitively
# against the visible page text.
_TEXT_SIGNALS = (
    "enter the characters you see",
    "type the characters you see",
    "are you a human",
    "are you a robot",
    "verify you are human",
    "verifying you are human",
    "checking your browser",
    "unusual traffic",
    "automated access",
    "access denied",
    "please enable javascript and cookies",
    "sorry, we just need to make sure you're not a robot",
    "to discuss automated access to amazon data",
    "captcha",
)

# Selectors and URL fragments belonging to commercial anti-bot vendors.
_DOM_SIGNALS = (
    "#captchacharacters",          # Amazon
    "form[action*='/errors/validateCaptcha']",
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "#cf-challenge-running",       # Cloudflare
    "#challenge-form",
    "div[class*='px-captcha']",    # PerimeterX
    "#px-captcha",
)

_URL_SIGNALS = (
    "/errors/validatecaptcha",
    "/challenge",
    "__cf_chl",
    "/px/captcha",
    "/sorry/index",                # Google
)

# Blocked-by-policy status codes. 429 is rate limiting, which is the same
# instruction wearing a different hat.
_BLOCKED_STATUSES = (403, 429, 503)


def check_url(url: str) -> str | None:
    lowered = (url or "").lower()
    for signal in _URL_SIGNALS:
        if signal in lowered:
            return f"url contains {signal!r}"
    return None


def check_status(status: int | None) -> str | None:
    if status in _BLOCKED_STATUSES:
        return f"HTTP {status}"
    return None


def check_text(text: str) -> str | None:
    lowered = (text or "").lower()
    for signal in _TEXT_SIGNALS:
        if signal in lowered:
            return f"page says {signal!r}"
    return None


def check_title(title: str) -> str | None:
    lowered = (title or "").lower()
    if re.search(r"\b(just a moment|attention required|robot check|access denied)\b",
                 lowered):
        return f"title is {title!r}"
    return None


def dom_signals() -> tuple[str, ...]:
    """Selectors for the caller to test against a live page."""
    return _DOM_SIGNALS


def assess(
    url: str,
    status: int | None = None,
    title: str = "",
    text: str = "",
    matched_selectors: tuple[str, ...] = (),
) -> str | None:
    """Return the first signal that fires, or None if the page looks normal."""
    for signal in (
        check_url(url),
        check_status(status),
        check_title(title),
        check_text(text[:5000]),  # challenge text is always near the top
    ):
        if signal:
            return signal
    if matched_selectors:
        return f"matched {matched_selectors[0]!r}"
    return None
