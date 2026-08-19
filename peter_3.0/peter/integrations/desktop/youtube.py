"""Finding a YouTube video to play, without an API key or a browser.

"Play the lofi hip hop radio" needs a video id, and the two obvious routes both
have problems: the Data API needs a key and burns quota, and driving the search
page through Playwright is slow and breaks whenever the markup shifts.

YouTube's search page embeds its results as JSON inside the HTML, so one plain
HTTP GET and a regex over `"videoId":"..."` gets the top result. There is no
key, no quota, and no browser. It relies on an internal page format, so it is
written to fail cleanly — if the pattern stops matching, the caller falls back
to opening the search results page and letting Dhusnic pick.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

_SEARCH = "https://www.youtube.com/results?search_query={}"
_WATCH = "https://www.youtube.com/watch?v={}"
# Ask for a desktop page; the mobile one has a different shape.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
_TITLE = re.compile(r'"title":\{"runs":\[\{"text":"(.*?)"\}\]')


def search_url(query: str) -> str:
    return _SEARCH.format(urllib.parse.quote_plus(query.strip()))


def watch_url(video_id: str) -> str:
    return _WATCH.format(video_id)


def first_result(query: str, timeout: float = 12.0) -> tuple[str, str] | None:
    """Top video for `query` as (video_id, title), or None if it cannot be read."""
    try:
        request = urllib.request.Request(search_url(query), headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", "replace")
    except Exception as exc:
        log.info("youtube search failed for %r: %s", query, exc)
        return None

    ids = list(dict.fromkeys(_VIDEO_ID.findall(html)))
    if not ids:
        log.info("no videoId in youtube results for %r", query)
        return None

    titles = _TITLE.findall(html)
    title = titles[0] if titles else ""
    try:
        # Titles arrive JSON-escaped (&, \", emoji surrogates).
        title = title.encode().decode("unicode_escape").encode(
            "utf-16", "surrogatepass"
        ).decode("utf-16", "replace")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return ids[0], title.strip()
