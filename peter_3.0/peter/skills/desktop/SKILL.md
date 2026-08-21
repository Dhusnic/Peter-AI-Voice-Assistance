# desktop

Driving the software already installed on the machine — the user's own
browser, its bookmarks, whatever is playing, and local folders
(`peter/integrations/desktop/`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `open_website` | write | Open a URL in the preferred browser/profile from config. |
| `open_named_site` | write | Open a site by spoken name ("gmail", "youtube", + config-defined `sites`). |
| `search_bookmarks` | read | Search bookmarks across Firefox/Chrome/Edge/Brave without opening anything. |
| `open_bookmark` | write | Open a saved bookmark by fuzzy description. |
| `play_youtube` | write | Find and play the top YouTube search result. |
| `control_playback` | write | Media keys: play/pause, next, previous, stop, mute, volume up/down. |
| `list_locations` | read | List local folders that can be opened by name. |
| `open_location` | write | Open a local folder in File Explorer by fuzzy name. |

## Setup

`integrations.desktop.enabled` (default true) — the only gate; no
credentials needed. Relevant `DesktopConfig` fields: `preferred_browser`
(default `"default"`, follows the Windows setting), `browser_profile`,
`youtube_browser`/`youtube_browser_profile` (independent override, empty =
no override), `gmail_accounts` (spoken label → account index/address),
`places` (spoken name → folder path, on top of standard Windows folders),
`bookmark_sources` (empty = search every installed browser), `sites`
(spoken name → URL template, config-defined shortcuts on top of the
built-ins).

## Design notes & gotchas

- **This is the user's own browser, opened for them to look at — a
  genuinely different thing from the `browser` skill's Playwright instance,**
  which is always a separate, Peter-owned profile driving a scripted
  session. `open_website`/`open_named_site`/`open_bookmark` never touch that
  automation profile; they just launch whatever's installed. Don't conflate
  the two "browser" concepts when extending either skill.
- **Ambiguity is a result, not a failure — the shape every fuzzy-match tool
  here shares.** With 104+ real bookmarks, "log search" genuinely matches
  several. `matching.rank()` only reports a confident winner when the top
  score is high *and* clears the runner-up by a margin; otherwise the tool
  returns the candidates as a normal result and the model asks which one —
  silently opening one of several similar bookmarks would be worse than one
  short question. `open_named_site`, `open_bookmark`, and `open_location`
  all go through this same `_ask_which()` helper.
- **Character similarity alone cannot carry a match — found live, not
  theorised.** `"zzz nothing"` scored 0.44 against `"HDFC Net Banking"` on
  incidental shared letters before this was fixed. Fuzzy scoring is now
  discounted by half unless at least one *word* actually overlaps — but
  per-word similarity is kept, so `"dashbord"` still finds `"dashboard"`,
  which is what a real speech transcript produces.
- **Firefox's bookmark database is copied before reading** — `places.sqlite`
  is locked exactly when you'd want it, while Firefox is open — so
  `search_bookmarks`/`open_bookmark` copy it to temp and query the copy.
- **Playback uses real media keys (`keybd_event`), not page automation.**
  Windows routes them to whatever currently holds media focus — YouTube,
  Spotify, VLC, anything — which is more robust than driving the YouTube
  page through Playwright (would only work for YouTube, only in Peter's own
  browser, and would break whenever the markup changed).
- **YouTube can open in a different browser than everything else.**
  `desktop.youtube_browser` overrides `preferred_browser` specifically for
  `youtube.com`/`youtu.be` URLs, checked once in `_open_with_preferred()` so
  it applies uniformly whether the video came from `play_youtube`,
  `open_named_site("youtube")`, or a YouTube bookmark.
- **YouTube search has no API key, no quota, no browser** — `play_youtube`
  fetches the search page and reads `"videoId"` fields out of the JSON
  embedded in its HTML. This depends on an internal page format, so it fails
  cleanly: if the pattern stops matching, it opens the search results page
  instead of doing nothing.

## Future extension ideas

- `bookmark_sources` filters which browsers get searched, but there's no
  per-source priority when the same bookmark exists in more than one
  browser — first match wins, not the most-recently-used one.
- No tool renames/deletes a bookmark — read and open only, consistent with
  this being about *using* what's already there rather than curating it.
