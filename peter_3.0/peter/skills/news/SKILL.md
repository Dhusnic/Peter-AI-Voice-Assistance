# news

Top headlines via Google News' public RSS feed — free, no API key
(`peter/integrations/news.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `get_news` | read | Today's top headlines, optionally narrowed to a topic. |

## Setup

`integrations.news.enabled` (default true) is the only gate — no secret
needed. `NewsConfig`: `topic` (empty = general top headlines), `max_items`
(default 5), `region` (default `"IN"`), `language` (default `"en"`),
`timeout_seconds`.

## Design notes & gotchas

- **Reuses `weather.py`'s exact shape, swapping JSON for XML.** Same
  reasoning as weather for choosing a free, no-signup source over a metered
  news API — see `weather`'s SKILL.md for that trade. Parsed with the
  stdlib `xml.etree.ElementTree` rather than adding a dependency, consistent
  with every other integration here being `urllib`-only.
- **This is RSS consumption of a feed Google publishes specifically to be
  read this way** — not scraping a logged-in surface, so none of the
  `browser` skill's ToS/bot-detection caveats apply here.
- **Unlike weather's geocode, headlines are never cached.** Coordinates for
  a city are permanent within a process lifetime; a headline is stale
  within the hour, so every call to `get_news` is a fresh fetch. This is
  the one deliberate difference from the otherwise-identical `weather`
  pattern.
- Folds into the morning briefing (`_SECTIONS["news"]` in `peter/briefing.py`)
  behind the same opt-in-via-`briefing.include` and graceful-degradation
  machinery weather uses — see the `briefing` skill's SKILL.md.

## Future extension ideas

- No per-topic subscription or standing digest — every call is a fresh,
  one-off request; a "tell me if anything big happens in tech today" style
  standing watch would need new state, closer in shape to `price_watch`
  than to this thin wrapper.
- `region`/`language` are global config, not per-call — a one-off "news from
  the UK" request has no override parameter the way `get_weather`'s
  `location` does.
