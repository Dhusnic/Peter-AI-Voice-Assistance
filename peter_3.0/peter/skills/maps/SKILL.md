# maps

Google Maps Platform — geocoding, directions, places
(`peter/integrations/maps.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `geocode_address` | read | Resolve an address/place name to coordinates + formatted address. |
| `reverse_geocode` | read | Resolve coordinates to a human-readable address. |
| `get_directions` | read | Directions between two places (driving/walking/bicycling/transit). |
| `find_nearby_places` | read | Search for places by description, optionally near a place. |
| `get_place_details` | read | Address/phone/rating/hours for a specific place id. |

## Setup

1. `integrations.maps.enabled: true` in `config.yml` — **off by default**,
   one of only two integrations in this codebase that default off (the other
   being `keep`, for a different reason).
2. `GOOGLE_MAPS_API_KEY` in `.env` (`secrets.has_maps`).
3. Both are required — `_REQUIRES` in `registry.py` gates the whole module on
   `integrations.maps.enabled and secrets.has_maps`.

**Different cost profile from `weather`/`news`.** Those two are free,
no-key APIs (Open-Meteo, Google News RSS) chosen specifically to avoid a
metered dependency. Maps Platform has no free-tier-without-billing option at
all — it needs a Google Cloud Billing account attached to the project even
for personal, low-volume use. That real-world prerequisite (only completable
in Cloud Console, not by Peter) is why this defaults off; see
`docs/USER_MANUAL.md` before enabling it.

## Design notes & gotchas

- **Plain functions, no client class — same shape as `weather.py`, not the
  Google OAuth clients.** `maps.py` has no lazy stateful client behind
  `services()`; every tool here calls a free function in
  `peter.integrations.maps` directly, catches `PeterError`, and returns
  `.spoken()`. This is the self-contained pattern `weather_tools.py` also
  uses, distinct from how `calendar`/`drive`/`sheets`/`gdocs` go through a
  cached client on the service container.
- Uses a plain API key, not OAuth — Maps Platform's own auth model, separate
  from the shared Google OAuth client every other Google skill here uses.
  Losing this key does not touch Calendar/Drive/Sheets/Docs access at all,
  and vice versa.
- Every tool wraps its call in `try/except PeterError: return exc.spoken()`
  — a `NotConfiguredError` (missing key or disabled) surfaces as one spoken
  sentence, not a stack trace.

## Future extension ideas

- No caching of geocode results the way `weather.py` caches a city's
  coordinates for the process lifetime — worth adding if the same address
  gets looked up repeatedly in a session, since Maps Platform billing is
  per-call and this is the one integration here where that actually costs
  money.
- `find_nearby_places`/`get_place_details` are read-only by nature (Places
  API has no write concept for a personal-use case), so this skill has no
  natural `write`-tier tool coming — unlike most other skills' trajectory.
