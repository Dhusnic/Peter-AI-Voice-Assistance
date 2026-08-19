"""Controlling the machine's own software: browser, bookmarks, media, folders.

The shape most of these share is worth stating once. A spoken request rarely
names a thing exactly — "open the staging dashboard" against 114 bookmarks. So
a lookup either lands on one clear winner and acts, or comes back with the two
or three it might have been **as a normal result**, for Peter to ask about.
Guessing between two similar bookmarks is worse than one short question, and
raising an error would end the turn instead of continuing the conversation.
"""

from __future__ import annotations

import subprocess

from peter.agent.registry import peter_tool
from peter.core.services import services
from peter.integrations.desktop import browsers, matching, media, places, youtube


def _desktop():
    return services().config.integrations.desktop


def _open_with_preferred(url: str, new_window: bool = False) -> str:
    cfg = _desktop()
    return browsers.open_url(
        url, browser=cfg.preferred_browser, profile=cfg.browser_profile,
        new_window=new_window,
    )


def _ask_which(candidates, describe) -> str:
    """Phrase an ambiguous lookup as a question, not a failure."""
    listed = "; ".join(describe(c) for c in candidates[:4])
    return (
        f"Found {len(candidates)} close matches: {listed}. "
        "Ask which one they meant, then call this again with a more specific query."
    )


# ------------------------------------------------------------------ browser
@peter_tool(tier="write")
def open_website(url: str, new_window: bool = False) -> str:
    """Open a web address in the user's preferred browser.

    Use this rather than open_url — it respects the browser and profile set in
    config, where open_url always uses the Windows default.

    Args:
        url: Full address, e.g. "https://github.com".
        new_window: Open a new window instead of a tab.
    """
    clean = url.strip()
    if not clean.lower().startswith(("http://", "https://")):
        clean = "https://" + clean
    where = _open_with_preferred(clean, new_window)
    return f"Opened {clean} — {where}."


@peter_tool(tier="write")
def open_named_site(name: str, account: str = "") -> str:
    """Open a site the user refers to by name, like "gmail" or "youtube".

    Knows the common sites plus anything listed under desktop.sites in config.
    For Gmail, `account` picks which signed-in account to open when more than
    one is configured.

    Args:
        name: What the user called it, e.g. "gmail", "youtube", "drive".
        account: Which account, for sites that support it — e.g. "work".
    """
    cfg = _desktop()
    key = name.strip().lower()

    builtin = {
        "gmail": "https://mail.google.com/mail/u/{account}/",
        "mail": "https://mail.google.com/mail/u/{account}/",
        "youtube": "https://www.youtube.com/",
        "drive": "https://drive.google.com/",
        "calendar": "https://calendar.google.com/",
        "maps": "https://www.google.com/maps",
        "github": "https://github.com/",
        "whatsapp": "https://web.whatsapp.com/",
    }

    # Configured sites win, so a personal shortcut can override a builtin.
    known = {**builtin, **{k.lower(): v for k, v in cfg.sites.items()}}
    template = known.get(key)
    if template is None:
        match = matching.rank(key, sorted(known), key=lambda s: s)
        if match.best and match.confident:
            template = known[match.best]
        elif match.candidates:
            return _ask_which(match.candidates, lambda s: s)
        else:
            return (
                f"No site called {name!r}. Known: {', '.join(sorted(known))}. "
                "Try open_bookmark for a saved page, or open_website for a URL."
            )

    url = template
    if "{account}" in template:
        # Gmail addresses accounts positionally in the URL; a label in config
        # maps a spoken name onto that index (or onto a full address).
        slot = "0"
        if account:
            resolved = cfg.gmail_accounts.get(account.strip().lower())
            if resolved is None:
                available = ", ".join(cfg.gmail_accounts) or "none configured"
                return (
                    f"No account called {account!r}. Configured accounts: "
                    f"{available}. Add it under desktop.gmail_accounts in config.yml."
                )
            slot = resolved
        url = template.format(account=slot)

    where = _open_with_preferred(url)
    which = f" ({account} account)" if account else ""
    return f"Opened {key}{which} — {where}."


# ---------------------------------------------------------------- bookmarks
@peter_tool(tier="read")
def search_bookmarks(query: str, limit: int = 8) -> str:
    """Search the user's browser bookmarks without opening anything.

    Reads Firefox, Chrome, Edge and Brave. Use this when they ask what they
    have saved; use open_bookmark when they want it opened.

    Args:
        query: What to look for. Empty lists everything.
        limit: How many to return.
    """
    marks = browsers.read_bookmarks(tuple(_desktop().bookmark_sources))
    if not marks:
        return "No bookmarks found in any installed browser."

    if not query.strip():
        chosen = marks[:limit]
    else:
        match = matching.rank(
            query, marks, key=lambda b: f"{b.title} {b.folder}", limit=limit
        )
        chosen = match.candidates
        if not chosen:
            return f"No bookmark matches {query!r} out of {len(marks)} saved."

    lines = [f"- {b.title} ({b.browser}){' in ' + b.folder if b.folder else ''}"
             for b in chosen]
    return f"{len(chosen)} of {len(marks)} bookmarks:\n" + "\n".join(lines)


@peter_tool(tier="write")
def open_bookmark(query: str) -> str:
    """Open a saved bookmark by describing it.

    Matches loosely, since a spoken request rarely repeats a bookmark's title
    exactly. If several are close it returns them instead of guessing — ask
    which one, then call again.

    Args:
        query: What the user called it, e.g. "staging dashboard".
    """
    marks = browsers.read_bookmarks(tuple(_desktop().bookmark_sources))
    if not marks:
        return "No bookmarks found in any installed browser."

    match = matching.rank(query, marks, key=lambda b: f"{b.title} {b.folder}")
    if match.best is None:
        return (
            f"No bookmark matches {query!r} out of {len(marks)} saved. "
            "Try search_bookmarks to see what is there."
        )
    if not match.confident:
        return _ask_which(match.candidates, lambda b: b.spoken())

    where = _open_with_preferred(match.best.url)
    return f"Opened bookmark {match.best.title!r} — {where}."


# ------------------------------------------------------------------ youtube
@peter_tool(tier="write")
def play_youtube(query: str) -> str:
    """Find a video on YouTube and start playing it in the browser.

    Plays the top search result. Once it is playing, control_playback handles
    pause, resume and skipping.

    Args:
        query: What to play, e.g. "lofi hip hop radio".
    """
    wanted = query.strip()
    if not wanted:
        return "Say what to play."

    found = youtube.first_result(wanted)
    if found is None:
        # Search worked but the page shape changed, or the network failed —
        # either way, land them on the results rather than nothing.
        where = _open_with_preferred(youtube.search_url(wanted))
        return (
            f"Could not pick a video automatically, so I opened the search "
            f"results for {wanted!r} — {where}."
        )

    video_id, title = found
    where = _open_with_preferred(youtube.watch_url(video_id))
    named = f" — {title}" if title else ""
    return f"Playing{named} on YouTube ({where})."


@peter_tool(tier="write")
def control_playback(action: str, repeat: int = 1) -> str:
    """Control whatever is currently playing — video, music, anything.

    Sends the keyboard's media keys, so it works on YouTube in the browser,
    Spotify, VLC and so on without knowing which is playing. It acts on
    whatever most recently had media focus.

    Args:
        action: One of "play_pause", "next", "previous", "stop", "mute",
            "volume_up", "volume_down". Use "play_pause" for both play and
            pause — it toggles.
        repeat: How many times, for stepping volume or skipping several tracks.
    """
    wanted = action.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "play": "play_pause", "pause": "play_pause", "resume": "play_pause",
        "toggle": "play_pause", "skip": "next", "forward": "next",
        "back": "previous", "prev": "previous", "unmute": "mute",
        "louder": "volume_up", "quieter": "volume_down", "down": "volume_down",
        "up": "volume_up",
    }
    wanted = aliases.get(wanted, wanted)

    if not media.send(wanted, repeat=max(1, min(20, repeat))):
        return (
            f"Unknown playback action {action!r}. Use one of: "
            f"{', '.join(media.ACTIONS)}."
        )

    spoken = {
        "play_pause": "Toggled playback", "next": "Skipped ahead",
        "previous": "Went back", "stop": "Stopped playback",
        "mute": "Toggled mute", "volume_up": "Turned it up",
        "volume_down": "Turned it down",
    }[wanted]
    return f"{spoken}."


# ------------------------------------------------------------------- places
@peter_tool(tier="read")
def list_locations() -> str:
    """List the local folders that can be opened by name."""
    found = places.known_places(_desktop().places)
    if not found:
        return "No known locations."
    lines = [
        f"- {p.name}{' (from config)' if p.configured else ''}" for p in found
    ]
    return f"{len(found)} locations:\n" + "\n".join(lines)


@peter_tool(tier="write")
def open_location(name: str) -> str:
    """Open a local folder in File Explorer by describing it.

    Knows the standard Windows folders (downloads, documents, desktop and so
    on) plus anything added under desktop.places in config. Matches loosely,
    and asks rather than guessing when several are close.

    Args:
        name: What the user called it, e.g. "downloads", "the Peter project".
    """
    found = places.known_places(_desktop().places)
    if not found:
        return "No known locations to open."

    match = matching.rank(name, found, key=lambda p: p.name)
    if match.best is None:
        listed = ", ".join(p.name for p in found[:8])
        return f"No location matches {name!r}. Known: {listed}."
    if not match.confident:
        return _ask_which(match.candidates, lambda p: p.name)

    target = places.resolve(match.best)
    try:
        subprocess.Popen(["explorer", target])
    except OSError as exc:
        return f"Could not open {match.best.name}: {exc}"
    return f"Opened {match.best.name} in File Explorer."
