"""Desktop control: matching, bookmarks, media keys, places, and the tools.

The matcher gets the most attention here because it is the part that decides
whether Peter opens the right thing, opens the wrong thing, or asks. Those
three outcomes are the whole feature — a fast lookup that confidently opens the
wrong bookmark is worse than a slow one that asks.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from peter.integrations.desktop import browsers, matching, media, places, youtube


# ============================================================== the matcher
def test_an_exact_name_wins_outright():
    items = ["Staging Dashboard", "Localhost Report", "HDFC Net Banking"]
    result = matching.rank("staging dashboard", items, key=lambda s: s)
    assert result.best == "Staging Dashboard"
    assert result.confident


def test_word_order_and_filler_do_not_matter():
    """Speech does not reproduce a saved title word for word."""
    items = ["Staging Dashboard", "Downloads"]
    result = matching.rank("open the dashboard for staging please", items, key=lambda s: s)
    assert result.best == "Staging Dashboard"


def test_a_clipped_word_still_matches():
    items = ["Staging Dashboard", "Recycle Bin"]
    assert matching.rank("staging dash", items, key=lambda s: s).best == "Staging Dashboard"


def test_a_mis_heard_word_still_matches():
    """'dashbord' is what a transcript actually produces for 'dashboard'."""
    items = ["Staging Dashboard", "Recycle Bin"]
    assert matching.rank("staging dashbord", items, key=lambda s: s).best == "Staging Dashboard"


def test_unrelated_text_matches_nothing():
    """Character similarity alone is not evidence: 'zzz nothing' scores 0.44
    against 'HDFC Net Banking' on incidental letters. A real regression that
    surfaced against live bookmarks."""
    items = ["HDFC Net Banking", "Staging Dashboard", "Localhost Report"]
    for nonsense in ("zzz nothing", "asdfgh qwerty", "quantum physics"):
        assert matching.rank(nonsense, items, key=lambda s: s).best is None, nonsense


def test_two_similar_names_are_ambiguous_not_a_guess():
    """Silently opening one of two near-identical bookmarks is worse than
    asking. This is the behaviour the whole 'ask which' flow rests on."""
    items = ["Localhost Log Search", "Staging Log Search"]
    result = matching.rank("log search", items, key=lambda s: s)

    assert result.best is not None
    assert not result.confident
    assert len(result.candidates) == 2


def test_a_query_of_only_filler_words_does_not_match_everything():
    items = ["Staging Dashboard", "Localhost Report"]
    result = matching.rank("the my please", items, key=lambda s: s)
    assert result.best is None or not result.confident


def test_ranking_an_empty_list_is_safe():
    assert matching.rank("anything", [], key=lambda s: s).best is None


def test_tokens_drop_noise_but_never_everything():
    assert matching.tokens("open the dashboard") == ["dashboard"]
    assert matching.tokens("the my a") == ["the", "my", "a"]


# ============================================================== bookmarks
def test_firefox_bookmarks_are_read_from_a_locked_database(tmp_path, monkeypatch):
    """places.sqlite is locked exactly when you want it — while Firefox is
    running — so it is copied before being read."""
    profile = tmp_path / "Profiles" / "abc.default"
    profile.mkdir(parents=True)
    db = profile / "places.sqlite"

    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE moz_bookmarks (
            id INTEGER PRIMARY KEY, fk INTEGER, type INTEGER,
            title TEXT, parent INTEGER
        );
        INSERT INTO moz_places VALUES (1, 'https://example.com/dash');
        INSERT INTO moz_places VALUES (2, 'place:internal');
        INSERT INTO moz_bookmarks VALUES (10, NULL, 2, 'Work', NULL);
        INSERT INTO moz_bookmarks VALUES (11, 1, 1, 'Dashboard', 10);
        INSERT INTO moz_bookmarks VALUES (12, 2, 1, 'Internal', 10);
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(browsers, "_FIREFOX_PROFILES", str(tmp_path / "Profiles"))
    monkeypatch.setattr(browsers, "_CHROMIUM_DATA", {})

    marks = browsers.read_bookmarks(("firefox",))
    assert [m.title for m in marks] == ["Dashboard"]
    assert marks[0].folder == "Work"
    assert marks[0].url == "https://example.com/dash"


def test_chromium_bookmarks_are_read_from_json(tmp_path, monkeypatch):
    profile = tmp_path / "Default"
    profile.mkdir(parents=True)
    (profile / "Bookmarks").write_text(json.dumps({
        "roots": {"bookmark_bar": {
            "type": "folder", "name": "Bar",
            "children": [
                {"type": "url", "name": "Report", "url": "https://example.com/r"},
                {"type": "folder", "name": "Deep", "children": [
                    {"type": "url", "name": "Nested", "url": "https://example.com/n"},
                ]},
            ],
        }}
    }), encoding="utf-8")

    monkeypatch.setattr(browsers, "_FIREFOX_PROFILES", str(tmp_path / "nope"))
    monkeypatch.setattr(browsers, "_CHROMIUM_DATA", {"chrome": str(tmp_path)})

    titles = {m.title for m in browsers.read_bookmarks(("chrome",))}
    assert titles == {"Report", "Nested"}


def test_the_same_url_saved_twice_appears_once(tmp_path, monkeypatch):
    for name in ("Default", "Profile 1"):
        profile = tmp_path / name
        profile.mkdir(parents=True)
        (profile / "Bookmarks").write_text(json.dumps({
            "roots": {"bar": {"type": "folder", "name": "Bar", "children": [
                {"type": "url", "name": "Report", "url": "https://example.com/r"},
            ]}}
        }), encoding="utf-8")

    monkeypatch.setattr(browsers, "_FIREFOX_PROFILES", str(tmp_path / "nope"))
    monkeypatch.setattr(browsers, "_CHROMIUM_DATA", {"chrome": str(tmp_path)})
    assert len(browsers.read_bookmarks(("chrome",))) == 1


def test_a_missing_browser_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(browsers, "_FIREFOX_PROFILES", str(tmp_path / "nope"))
    monkeypatch.setattr(browsers, "_CHROMIUM_DATA", {"chrome": str(tmp_path / "gone")})
    assert browsers.read_bookmarks() == []


# ================================================================ browsers
def test_an_uninstalled_browser_falls_back_and_says_so(monkeypatch):
    """Silently opening a different browser than asked for is worse than
    saying which one was used."""
    monkeypatch.setattr(browsers, "detect_browsers", lambda: {})
    opened = []
    monkeypatch.setattr(
        "webbrowser.open_new_tab", lambda url: opened.append(url)
    )

    note = browsers.open_url("https://example.com", browser="firefox")
    assert opened == ["https://example.com"]
    assert "not installed" in note


def test_the_named_browser_and_profile_reach_the_command_line(tmp_path, monkeypatch):
    exe = tmp_path / "firefox.exe"
    exe.write_text("")
    monkeypatch.setattr(browsers, "detect_browsers", lambda: {"firefox": exe})
    calls = []
    monkeypatch.setattr(browsers.subprocess, "Popen", lambda args, **k: calls.append(args))

    browsers.open_url("https://example.com", browser="firefox", profile="work")
    assert calls[0][:3] == [str(exe), "-P", "work"]
    assert calls[0][-1] == "https://example.com"


def test_chromium_profiles_use_their_own_flag(tmp_path, monkeypatch):
    """Firefox wants -P <name>; Chromium wants --profile-directory=<dir>."""
    exe = tmp_path / "chrome.exe"
    exe.write_text("")
    monkeypatch.setattr(browsers, "detect_browsers", lambda: {"chrome": exe})
    calls = []
    monkeypatch.setattr(browsers.subprocess, "Popen", lambda args, **k: calls.append(args))

    browsers.open_url("https://example.com", browser="chrome", profile="Profile 1")
    assert "--profile-directory=Profile 1" in calls[0]


# =================================================================== media
def test_every_advertised_action_maps_to_a_key(monkeypatch):
    sent = []
    monkeypatch.setattr(
        media.ctypes, "windll",
        SimpleNamespace(user32=SimpleNamespace(
            keybd_event=lambda code, a, b, c: sent.append(code)
        )),
    )
    for action in media.ACTIONS:
        assert media.send(action), action
    # Two events per press (down, up).
    assert len(sent) == len(media.ACTIONS) * 2


def test_an_unknown_action_is_refused_not_guessed(monkeypatch):
    monkeypatch.setattr(
        media.ctypes, "windll",
        SimpleNamespace(user32=SimpleNamespace(keybd_event=lambda *a: None)),
    )
    assert media.send("explode") is False


def test_repeat_presses_the_key_that_many_times(monkeypatch):
    sent = []
    monkeypatch.setattr(
        media.ctypes, "windll",
        SimpleNamespace(user32=SimpleNamespace(
            keybd_event=lambda code, a, b, c: sent.append(code)
        )),
    )
    monkeypatch.setattr(media.time, "sleep", lambda _s: None)
    media.send("volume_up", repeat=3)
    assert len(sent) == 6


# ================================================================== places
def test_standard_folders_are_known_without_configuration():
    names = {p.name for p in places.known_places()}
    assert {"downloads", "documents", "desktop"} <= names


def test_configured_places_are_added_and_marked(tmp_path):
    found = places.known_places({"my project": str(tmp_path)})
    mine = [p for p in found if p.name == "my project"]
    assert mine and mine[0].configured


def test_a_configured_place_that_does_not_exist_is_dropped():
    found = places.known_places({"ghost": "Z:/nowhere/at/all"})
    assert not any(p.name == "ghost" for p in found)


def test_the_recycle_bin_survives_the_existence_check():
    """It is a shell: alias, not a directory — is_dir() would drop it."""
    names = {p.name for p in places.known_places()}
    assert "recycle bin" in names


# ================================================================= youtube
class _FakeResponse:
    """`with` looks dunders up on the type, so this cannot be a SimpleNamespace."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_first_video_id_is_extracted(monkeypatch):
    html = (
        '{"videoId":"dQw4w9WgXcQ","title":{"runs":[{"text":"Lofi Radio"}]}}'
        '{"videoId":"aaaaaaaaaaa"}'
    )
    monkeypatch.setattr(
        youtube.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(html.encode()),
    )
    assert youtube.first_result("lofi") == ("dQw4w9WgXcQ", "Lofi Radio")


def test_a_failed_search_returns_none_rather_than_raising(monkeypatch):
    """The caller falls back to opening the results page — the turn must not
    die because YouTube changed its markup."""
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(youtube.urllib.request, "urlopen", boom)
    assert youtube.first_result("anything") is None


def test_markup_without_a_video_id_returns_none(monkeypatch):
    monkeypatch.setattr(
        youtube.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(b"<html>nothing here</html>"),
    )
    assert youtube.first_result("anything") is None


def test_the_search_url_is_escaped():
    assert "lofi+hip+hop" in youtube.search_url("lofi hip hop")


# =================================================================== tools
@pytest.fixture
def desktop_tools(container, monkeypatch):
    from peter.skills.desktop import tools as tools

    opened = []
    monkeypatch.setattr(
        tools.browsers, "open_url",
        lambda url, **k: opened.append(url) or "opened in firefox",
    )
    return tools, opened


def test_open_named_site_knows_gmail(desktop_tools):
    tools, opened = desktop_tools
    result = tools.open_named_site(name="gmail")
    assert "mail.google.com" in opened[0]
    assert "Opened gmail" in result


def test_gmail_opens_the_requested_account(desktop_tools, container):
    tools, opened = desktop_tools
    container.config.integrations.desktop.gmail_accounts = {"work": "1"}

    tools.open_named_site(name="gmail", account="work")
    assert "/mail/u/1/" in opened[0]


def test_an_unknown_gmail_account_says_which_exist(desktop_tools, container):
    tools, opened = desktop_tools
    container.config.integrations.desktop.gmail_accounts = {"work": "1"}

    result = tools.open_named_site(name="gmail", account="school")
    assert opened == [], "nothing should open when the account is unknown"
    assert "work" in result


def test_an_unknown_site_lists_what_is_known(desktop_tools):
    tools, opened = desktop_tools
    result = tools.open_named_site(name="nonexistentplace")
    assert opened == []
    assert "gmail" in result


def test_ambiguous_bookmarks_come_back_as_a_question(desktop_tools, monkeypatch):
    """The core behaviour: ask, do not guess."""
    tools, opened = desktop_tools
    marks = [
        browsers.Bookmark("Localhost Log Search", "http://a", "firefox"),
        browsers.Bookmark("Staging Log Search", "http://b", "firefox"),
    ]
    monkeypatch.setattr(tools.browsers, "read_bookmarks", lambda *a: marks)

    result = tools.open_bookmark(query="log search")
    assert opened == [], "an ambiguous request must not open anything"
    assert "close matches" in result
    assert "Localhost Log Search" in result and "Staging Log Search" in result


def test_a_clear_bookmark_match_opens_immediately(desktop_tools, monkeypatch):
    tools, opened = desktop_tools
    marks = [
        browsers.Bookmark("Staging Dashboard", "http://dash", "firefox"),
        browsers.Bookmark("Recycle Notes", "http://notes", "firefox"),
    ]
    monkeypatch.setattr(tools.browsers, "read_bookmarks", lambda *a: marks)

    result = tools.open_bookmark(query="staging dashboard")
    assert opened == ["http://dash"]
    assert "Staging Dashboard" in result


def test_no_bookmark_match_suggests_searching(desktop_tools, monkeypatch):
    tools, opened = desktop_tools
    marks = [browsers.Bookmark("Staging Dashboard", "http://dash", "firefox")]
    monkeypatch.setattr(tools.browsers, "read_bookmarks", lambda *a: marks)

    result = tools.open_bookmark(query="quantum physics lecture")
    assert opened == []
    assert "search_bookmarks" in result


def test_play_youtube_opens_the_video(desktop_tools, monkeypatch):
    tools, opened = desktop_tools
    monkeypatch.setattr(
        tools.youtube, "first_result", lambda q: ("abc12345678", "Lofi Radio")
    )
    result = tools.play_youtube(query="lofi")
    assert opened == ["https://www.youtube.com/watch?v=abc12345678"]
    assert "Lofi Radio" in result


def test_play_youtube_falls_back_to_search_results(desktop_tools, monkeypatch):
    """If the id cannot be extracted, land them on the search page rather than
    doing nothing."""
    tools, opened = desktop_tools
    monkeypatch.setattr(tools.youtube, "first_result", lambda q: None)

    result = tools.play_youtube(query="something obscure")
    assert opened and "results?search_query" in opened[0]
    assert "search results" in result


def test_playback_aliases_map_to_real_actions(desktop_tools, monkeypatch):
    tools, _ = desktop_tools
    sent = []
    monkeypatch.setattr(tools.media, "send",
                        lambda action, repeat=1: sent.append(action) or True)

    for spoken in ("play", "pause", "resume", "skip", "back", "louder"):
        tools.control_playback(action=spoken)
    assert sent == ["play_pause", "play_pause", "play_pause",
                    "next", "previous", "volume_up"]


def test_an_unknown_playback_action_lists_the_real_ones(desktop_tools, monkeypatch):
    tools, _ = desktop_tools
    monkeypatch.setattr(tools.media, "send", lambda action, repeat=1: False)
    assert "play_pause" in tools.control_playback(action="explode")


def test_open_location_matches_a_standard_folder(desktop_tools, monkeypatch):
    tools, _ = desktop_tools
    launched = []
    monkeypatch.setattr(tools.subprocess, "Popen", lambda args, **k: launched.append(args))

    result = tools.open_location(name="downloads")
    assert launched and launched[0][0] == "explorer"
    assert "downloads" in result.lower()


def test_open_location_refuses_an_unknown_name(desktop_tools, monkeypatch):
    tools, _ = desktop_tools
    launched = []
    monkeypatch.setattr(tools.subprocess, "Popen", lambda args, **k: launched.append(args))

    result = tools.open_location(name="the quantum realm")
    assert launched == []
    assert "No location matches" in result
