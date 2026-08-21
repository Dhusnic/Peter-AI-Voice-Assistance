# weather

Current weather via Open-Meteo — free, no API key
(`peter/integrations/weather.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `get_weather` | read | Current weather for the configured location, or an ad-hoc override. |

## Setup

`integrations.weather.enabled` (default true) is the only gate — no secret
needed. `WeatherConfig`: `location` (a city name, geocoded once and cached
for the process lifetime) or `latitude`/`longitude` directly to skip
geocoding, `units` (`metric`|`imperial`), `timeout_seconds`.

## Design notes & gotchas

- **The one integration in this codebase that isn't a package.**
  `peter/integrations/weather.py` is a single stateless module making two
  possible HTTP calls (geocode, then forecast) via `urllib`, with no client
  class and no state beyond an in-process geocoding cache — unlike every
  other integration here (`mail/`, `google/`, `telegram/`, `phone/`, `dev/`,
  `desktop/`), which hold a stateful connection or span several files. The
  `maps` skill's `peter/integrations/maps.py` follows this exact same
  plain-functions shape.
- **Open-Meteo specifically because it needs no API key** — the one
  integration here that doesn't need a line in `.env`. Same reasoning
  `news` uses for Google's RSS feed over a metered news API. This is a
  materially different cost/setup profile from `maps`, which needs a paid
  Google Cloud Billing account even for light personal use — see `maps`'s
  SKILL.md.
- **A named location is geocoded once and cached for the process lifetime**
  (`_geocode_cache`, keyed on the lowercased name) — coordinates for a place
  don't go stale within a session, so a second lookup of the same city is
  free. `get_weather(location=...)` accepts an ad-hoc override without
  touching config, geocoded and cached the same way as the configured
  default.
- Folded into the morning briefing (`_SECTIONS["weather"]`) behind the same
  opt-in-via-`briefing.include` and graceful-degradation machinery every
  other optional briefing section uses — an unconfigured location lands in
  the "not set up" bucket via `NotConfiguredError`, not a crash. See the
  `briefing` skill's SKILL.md.

## Future extension ideas

- No multi-day forecast — only current conditions. Open-Meteo's API
  supports forecasts; this integration deliberately only wraps the current
  reading.
- No severe-weather alerting — a standing "tell me if it's going to rain
  today" watch would need new state, closer in shape to `price_watch` than
  to this stateless wrapper.
