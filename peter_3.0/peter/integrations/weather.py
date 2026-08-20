"""Current weather, via Open-Meteo.

Open-Meteo needs no API key and no signup — the whole reason it is the
default here rather than a metered provider is that a weather lookup is
exactly the kind of low-stakes, frequent call that should not need a
secret in .env at all.

A location name is geocoded once (Open-Meteo's own free geocoding endpoint)
and the result cached in-process for the rest of the session — a city's
coordinates do not change, so there is no reason to pay a second network
round trip for the same name twice. Setting latitude/longitude directly in
config skips geocoding altogether.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from peter.core.errors import IntegrationError, NotConfiguredError

log = logging.getLogger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Not persisted, not TTL'd — coordinates for a named place do not go stale
# within a process lifetime.
_geocode_cache: dict[str, tuple[float, float, str]] = {}

# WMO weather codes, as Open-Meteo reports them. Not exhaustive — the gaps
# are codes with no common real-world occurrence (e.g. 56/57 freezing
# drizzle), left to fall through to "unknown conditions" rather than
# padding this table for codes that will essentially never appear.
_WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "light showers", 81: "showers", 82: "violent showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _get_json(url: str, params: dict, timeout: float) -> dict:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(full_url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise IntegrationError(
            f"weather service returned {exc.code}", service="weather",
            recoverable=exc.code >= 500,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IntegrationError(
            f"weather service unreachable: {exc}", service="weather", recoverable=True
        ) from exc
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            "weather service returned something unreadable", service="weather",
            recoverable=True,
        ) from exc


def _geocode(location: str, timeout: float) -> tuple[float, float, str]:
    key = location.strip().lower()
    if key in _geocode_cache:
        return _geocode_cache[key]

    data = _get_json(_GEOCODE_URL, {"name": location, "count": 1}, timeout)
    results = data.get("results") or []
    if not results:
        raise IntegrationError(
            f"could not find a place called {location!r}", service="weather",
        )
    place = results[0]
    resolved = (
        place["latitude"], place["longitude"],
        f"{place['name']}, {place.get('country', '')}".rstrip(", "),
    )
    _geocode_cache[key] = resolved
    return resolved


def _coordinates(cfg, location_override: str | None) -> tuple[float, float, str]:
    if location_override:
        return _geocode(location_override, cfg.timeout_seconds)
    if cfg.latitude and cfg.longitude:
        return cfg.latitude, cfg.longitude, cfg.location or "your location"
    if not cfg.location.strip():
        raise NotConfiguredError(
            "weather",
            "Set integrations.weather.location (a city name) or "
            "latitude/longitude in config.yml.",
        )
    return _geocode(cfg.location, cfg.timeout_seconds)


def current(cfg, location_override: str | None = None) -> str:
    """One line describing the current weather.

    Args:
        location_override: A city name to look up instead of the configured
            default, without changing config.
    """
    if not cfg.enabled:
        return "Weather is switched off in config.yml."

    lat, lon, place = _coordinates(cfg, location_override)
    unit_params = (
        {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}
        if cfg.units == "imperial" else {}
    )
    data = _get_json(
        _FORECAST_URL,
        {
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
            **unit_params,
        },
        cfg.timeout_seconds,
    )
    current_data = data.get("current")
    if not current_data:
        raise IntegrationError(
            "weather service returned no current conditions", service="weather",
            recoverable=True,
        )

    temp_unit = "°F" if cfg.units == "imperial" else "°C"
    wind_unit = "mph" if cfg.units == "imperial" else "km/h"
    condition = _WEATHER_CODES.get(current_data.get("weather_code"), "unknown conditions")
    temp = current_data.get("temperature_2m")
    humidity = current_data.get("relative_humidity_2m")
    wind = current_data.get("wind_speed_10m")

    return (
        f"{place}: {condition}, {temp:.0f}{temp_unit}, "
        f"{humidity:.0f}% humidity, wind {wind:.0f} {wind_unit}."
    )
