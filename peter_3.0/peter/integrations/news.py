"""Top headlines, via Google News' public RSS feed.

Needs no API key and no signup — the same reasoning as weather.py: a feed
that works with nothing in .env beats a metered news API for something this
low-stakes. RSS is Google News' own supported, documented consumption path
(the same feed any RSS reader points at), not scraping a logged-in surface —
unlike the browser layer, there is no ToS/bot-detection risk here at all.

No caching: unlike weather's geocode cache (coordinates never change),
headlines change by the hour, so every call is a fresh fetch.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree

from peter.core.errors import IntegrationError

log = logging.getLogger(__name__)

_FEED_URL = "https://news.google.com/rss"
_SEARCH_URL = "https://news.google.com/rss/search"

# Google News blocks the default urllib User-Agent on some requests; a plain
# browser-like one avoids that without pretending to be anything else.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PeterAssistant/1.0)"}


def _get_xml(url: str, params: dict, timeout: float) -> ElementTree.Element:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full_url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise IntegrationError(
            f"news feed returned {exc.code}", service="news",
            recoverable=exc.code >= 500,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IntegrationError(
            f"news feed unreachable: {exc}", service="news", recoverable=True,
        ) from exc
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise IntegrationError(
            "news feed returned something unreadable", service="news",
            recoverable=True,
        ) from exc


def headlines(cfg, topic_override: str | None = None) -> str:
    """Top headlines as a short numbered list, one per line.

    Args:
        topic_override: A topic/query to search instead of the configured
            default, without changing config. Empty uses general top
            headlines.
    """
    if not cfg.enabled:
        return "News is switched off in config.yml."

    topic = (topic_override if topic_override is not None else cfg.topic).strip()
    locale = f"{cfg.language}-{cfg.region}"
    params = {"hl": locale, "gl": cfg.region, "ceid": f"{cfg.region}:{cfg.language}"}
    if topic:
        params["q"] = topic
        url = _SEARCH_URL
    else:
        url = _FEED_URL

    root = _get_xml(url, params, cfg.timeout_seconds)
    items = root.findall("./channel/item")[: cfg.max_items]
    if not items:
        return f"No headlines found for {topic!r}." if topic else "No headlines found."

    lines = []
    for i, item in enumerate(items, 1):
        title = (item.findtext("title") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        lines.append(f"{i}. {title}" + (f" ({source})" if source else ""))

    header = f"Top headlines for {topic}:" if topic else "Top headlines:"
    return header + "\n" + "\n".join(lines)
