"""The browser session.

One browser instance (Chromium by default, or Firefox — see
`BrowserConfig.engine`), one persistent profile, one page at a time.

**Persistent profile, logged in by hand.** `launch_persistent_context` against a
real profile directory means cookies survive restarts and you log into each site
once, yourself, in a visible window. Peter never sees or stores a site password.
A fresh headless browser is fingerprinted as automation immediately; a profile
you have actually been using is not.

**Headed, not headless.** Slower and it puts a window on your screen, which is
the point — you can see what it is doing, and stop it. Headless Chromium is also
the single loudest automation signal there is.

**One page, one lock.** Phase 4's background price pollers and interactive turns
will both want the browser. Serialising them through an RLock keeps two callers
from navigating the same tab out from under each other, and has the side effect
of making the rate limiter meaningful.

**Everything is guarded on the way out.** Every navigation runs the bot-wall
check before its content is returned. There is no path that yields page content
without passing detection.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from peter.core.config import BrowserConfig
from peter.core.errors import IntegrationError
from peter.integrations.browser import detect, extract
from peter.integrations.browser.ratelimit import RateLimiter

log = logging.getLogger(__name__)

# Pulls every JSON-LD payload out of the rendered DOM.
_JSONLD_JS = """
() => Array.from(
    document.querySelectorAll('script[type="application/ld+json"]')
).map(s => s.textContent || '').filter(Boolean)
"""

# Name/property meta tags, flattened into one dict.
_META_JS = """
() => {
    const out = {};
    document.querySelectorAll('meta').forEach(m => {
        const key = m.getAttribute('property') || m.getAttribute('name');
        const val = m.getAttribute('content');
        if (key && val) out[key] = val;
    });
    const t = document.querySelector('title');
    if (t) out['title'] = t.textContent || '';
    return out;
}
"""

# innerText, not textContent — innerText respects display:none, so hidden
# navigation menus and SEO keyword stuffing do not end up in the prompt.
_TEXT_JS = "() => document.body ? document.body.innerText : ''"

_LINKS_JS = """
() => Array.from(document.querySelectorAll('a[href]'))
    .map(a => [(a.innerText || '').trim().slice(0, 100), a.href])
    .filter(([t, h]) => t && h.startsWith('http'))
    .slice(0, 60)
"""


@dataclass(slots=True)
class PageState:
    url: str = ""
    title: str = ""
    logged_in_hint: str = ""


# Launch flags, per engine. Chromium's are load-bearing for evading basic
# bot detection; Firefox has no equivalent flag syntax, so it gets none.
_ENGINE_ARGS: dict[str, list[str]] = {
    "chromium": [
        # Chromium sets navigator.webdriver when this is on; every anti-bot
        # script checks it first.
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--no-first-run",
    ],
    "firefox": [],
}


class BrowserManager:
    def __init__(self, config: BrowserConfig, profile_dir: Path):
        self.config = config
        self.profile_dir = Path(profile_dir)
        self.limiter = RateLimiter(config.min_interval_seconds)
        self._lock = threading.RLock()
        self._playwright = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------ lifecycle
    def _ensure_started(self):
        """Launch on first use. Importing Playwright costs ~1s; launching costs more."""
        if self._context is not None:
            return self._context

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise IntegrationError(
                "Playwright is not installed",
                service="browser",
                user_action=(
                    "Run: pip install playwright && "
                    f"playwright install {self.config.engine}"
                ),
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._playwright = sync_playwright().start()
            engine = getattr(self._playwright, self.config.engine)
            self._context = engine.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.config.headless,
                viewport={"width": 1440, "height": 900},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                # Chromium-only flags. Firefox's automation markers are set
                # differently and does not accept Chromium's --flag syntax —
                # passing these to it fails the launch outright.
                args=_ENGINE_ARGS.get(self.config.engine, []),
            )
            self._context.set_default_timeout(
                self.config.default_timeout_seconds * 1000
            )
        except Exception as exc:
            self._teardown()
            raise IntegrationError(
                f"could not start the browser: {exc}",
                service="browser",
                recoverable=False,
                user_action=(
                    f"If {self.config.engine} is missing, run: "
                    f"python -m playwright install {self.config.engine}. If a "
                    "browser window is already open on this profile, close it "
                    "first. Switching engine (chromium <-> firefox) needs a "
                    "fresh profile_dir — the two use incompatible profile formats."
                ),
            ) from exc

        log.info("browser started (engine=%s, profile=%s, headless=%s)",
                 self.config.engine, self.profile_dir, self.config.headless)
        return self._context

    def _ensure_page(self):
        context = self._ensure_started()
        if self._page is None or self._page.is_closed():
            pages = [p for p in context.pages if not p.is_closed()]
            self._page = pages[0] if pages else context.new_page()
        return self._page

    def _teardown(self) -> None:
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._playwright and self._playwright.stop(),
        ):
            try:
                closer()
            except Exception:
                log.debug("browser teardown step failed", exc_info=True)
        self._context = None
        self._playwright = None
        self._page = None

    def close(self) -> None:
        with self._lock:
            if self._context is not None:
                log.info("closing browser")
            self._teardown()

    @property
    def is_running(self) -> bool:
        return self._context is not None

    def ping(self) -> bool:
        with self._lock:
            self._ensure_page()
            return True

    # ------------------------------------------------------------ navigation
    def goto(self, url: str, wait_for_idle: bool = True) -> PageState:
        """Navigate, then run the bot-wall check before anything is returned."""
        url = _normalise_url(url)

        with self._lock:
            page = self._ensure_page()
            self.limiter.wait(url)

            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.config.default_timeout_seconds * 1000,
                )
            except Exception as exc:
                raise IntegrationError(
                    f"could not open {url}: {_short(exc)}",
                    service="browser",
                    recoverable=True,
                    user_action="Check your internet connection.",
                ) from exc

            if wait_for_idle:
                # Best-effort: many storefronts never go fully idle because of
                # analytics beacons, so a timeout here is normal, not an error.
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

            status = response.status if response is not None else None
            self._guard(page, status)
            return PageState(url=page.url, title=page.title())

    def _guard(self, page, status: int | None = None) -> None:
        """Raise BotWallError if the page is a challenge. Never bypassed."""
        try:
            title = page.title()
        except Exception:
            title = ""
        try:
            text = page.evaluate(_TEXT_JS) or ""
        except Exception:
            text = ""

        matched: list[str] = []
        for selector in detect.dom_signals():
            try:
                if page.locator(selector).count() > 0:
                    matched.append(selector)
                    break
            except Exception:
                continue

        signal = detect.assess(
            url=page.url,
            status=status,
            title=title,
            text=text,
            matched_selectors=tuple(matched),
        )
        if signal:
            log.warning("bot wall on %s: %s", page.url, signal)
            raise detect.BotWallError(page.url, signal)

    # -------------------------------------------------------------- reading
    def read_page(self, url: str | None = None) -> extract.PageContent:
        """Navigate (if given a url) and extract structured data plus text."""
        with self._lock:
            if url:
                self.goto(url)
            page = self._ensure_page()
            self._guard(page)

            content = extract.PageContent(url=page.url)
            try:
                content.title = page.title()
            except Exception:
                content.title = ""

            jsonld = _safe_eval(page, _JSONLD_JS, [])
            meta = _safe_eval(page, _META_JS, {})
            raw_text = _safe_eval(page, _TEXT_JS, "")
            links = _safe_eval(page, _LINKS_JS, [])

            content.text = extract.clean_text(raw_text, self.config.max_page_chars)
            content.links = [(t, h) for t, h in links if t]
            content.product = extract.best_product(
                jsonld_blocks=jsonld,
                meta=meta,
                text=content.text,
                title=content.title,
            )
            return content

    def screenshot(self, path: Path, full_page: bool = False) -> Path:
        """Capture the current page. The fallback when extraction finds nothing."""
        with self._lock:
            page = self._ensure_page()
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(path), full_page=full_page)
            except Exception as exc:
                raise IntegrationError(
                    f"screenshot failed: {_short(exc)}", service="browser"
                ) from exc
            return path

    def state(self) -> PageState:
        with self._lock:
            if self._context is None:
                return PageState()
            page = self._ensure_page()
            try:
                return PageState(url=page.url, title=page.title())
            except Exception:
                return PageState()

    # -------------------------------------------------------------- acting
    def find_candidates(self, description: str, limit: int = 8) -> list[dict]:
        """Locate clickable things matching a description.

        Semantic matching by role and accessible name, not CSS selectors. A
        selector like `div.a-button-inner > span:nth-child(2)` breaks the next
        time the site ships a redesign; "the button called Add to Cart" does not.
        """
        with self._lock:
            page = self._ensure_page()
            needle = description.strip()
            if not needle:
                return []

            found: list[dict] = []
            seen: set[str] = set()

            for role in ("button", "link", "checkbox", "radio", "tab"):
                try:
                    locator = page.get_by_role(role, name=needle)
                    count = min(locator.count(), limit)
                except Exception:
                    continue
                for index in range(count):
                    item = locator.nth(index)
                    entry = _describe(item, role)
                    if entry and entry["label"] not in seen:
                        seen.add(entry["label"])
                        found.append(entry)

            if not found:
                try:
                    locator = page.get_by_text(needle, exact=False)
                    for index in range(min(locator.count(), limit)):
                        entry = _describe(locator.nth(index), "text")
                        if entry and entry["label"] not in seen:
                            seen.add(entry["label"])
                            found.append(entry)
                except Exception:
                    pass

            return found[:limit]

    def click(self, description: str, index: int = 0) -> str:
        """Click one match. The caller is responsible for having confirmed it."""
        with self._lock:
            page = self._ensure_page()
            candidates = self.find_candidates(description)
            if not candidates:
                raise IntegrationError(
                    f"nothing on this page matches {description!r}",
                    service="browser",
                )
            if index >= len(candidates):
                raise IntegrationError(
                    f"only {len(candidates)} matches for {description!r}",
                    service="browser",
                )

            target = candidates[index]
            role = target["role"]
            try:
                if role == "text":
                    page.get_by_text(description, exact=False).nth(
                        target["nth"]
                    ).click(timeout=10000)
                else:
                    page.get_by_role(role, name=description).nth(
                        target["nth"]
                    ).click(timeout=10000)
            except Exception as exc:
                raise IntegrationError(
                    f"could not click {target['label']!r}: {_short(exc)}",
                    service="browser",
                    recoverable=True,
                ) from exc

            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

            self._guard(page)
            return target["label"]

    def type_text(self, description: str, text: str, submit: bool = False) -> str:
        with self._lock:
            page = self._ensure_page()
            try:
                field = page.get_by_role("textbox", name=description).first
                if field.count() == 0:
                    field = page.get_by_placeholder(description).first
                field.fill(text, timeout=10000)
                if submit:
                    field.press("Enter")
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception as exc:
                raise IntegrationError(
                    f"could not type into {description!r}: {_short(exc)}",
                    service="browser",
                ) from exc

            self._guard(page)
            return f"typed into {description}"

    def open_for_login(self, url: str) -> str:
        """Bring up a visible window so the human can log in themselves.

        Peter never handles site credentials. It opens the page and waits; you
        type the password. Nothing to store, nothing to leak.
        """
        if self.config.headless:
            raise IntegrationError(
                "cannot log in while the browser is headless",
                service="browser",
                user_action="Set integrations.browser.headless to false in config.yml.",
            )
        with self._lock:
            page = self._ensure_page()
            self.limiter.wait(url)
            try:
                page.goto(_normalise_url(url), wait_until="domcontentloaded")
                page.bring_to_front()
            except Exception as exc:
                raise IntegrationError(
                    f"could not open {url}: {_short(exc)}", service="browser"
                ) from exc
            return page.url


# ----------------------------------------------------------------- helpers
def _describe(locator, role: str) -> dict | None:
    try:
        if not locator.is_visible(timeout=1500):
            return None
        label = (locator.inner_text(timeout=1500) or "").strip()
    except Exception:
        return None
    if not label:
        return None
    return {
        "role": role,
        "label": label[:120].replace("\n", " "),
        "nth": 0,
    }


def _safe_eval(page, script: str, default):
    try:
        result = page.evaluate(script)
    except Exception:
        log.debug("page evaluate failed", exc_info=True)
        return default
    return result if result is not None else default


def _normalise_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise IntegrationError("no URL given", service="browser")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _short(exc: Exception) -> str:
    """Playwright errors carry a multi-line banner. Keep the first line."""
    return str(exc).split("\n")[0][:200]
