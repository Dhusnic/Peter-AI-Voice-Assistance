"""Google Maps Platform — geocoding, directions, places.

Same shape as weather.py: plain functions, `_get_json` over `urllib`, no new
dependency. But Maps Platform reports failures *inside* a 200 response body
via a `status` field (`"ZERO_RESULTS"`, `"REQUEST_DENIED"`,
`"OVER_QUERY_LIMIT"`, ...), not via HTTP status codes — `_check_status`
below is the branch weather.py has no equivalent of.

Unlike weather (no key, no signup), this needs a Google Cloud API key with a
Billing account attached to the project — see docs/USER_MANUAL.md before
turning this on. `integrations.maps.enabled` defaults to False for exactly
that reason (see MapsConfig's docstring).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from peter.core.errors import IntegrationError, NotConfiguredError

log = logging.getLogger(__name__)

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
_PLACES_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

_STATUS_HINTS = {
    "ZERO_RESULTS": "no results for that query",
    "REQUEST_DENIED": (
        "request denied — check the API key is valid, the relevant API "
        "(Geocoding/Directions/Places) is enabled, and billing is attached "
        "to the project"
    ),
    "OVER_QUERY_LIMIT": "over the query limit — check billing/quota in Cloud Console",
    "INVALID_REQUEST": "invalid request — a required parameter was missing or malformed",
    "NOT_FOUND": "not found",
}


def _get_json(url: str, params: dict, timeout: float) -> dict:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(full_url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise IntegrationError(
            f"maps service returned {exc.code}", service="maps",
            recoverable=exc.code >= 500,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IntegrationError(
            f"maps service unreachable: {exc}", service="maps", recoverable=True
        ) from exc
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            "maps service returned something unreadable", service="maps",
            recoverable=True,
        ) from exc


def _check_status(data: dict) -> None:
    """Maps Platform reports failure inside a 200 body, not via HTTP status."""
    status = data.get("status", "OK")
    if status == "OK":
        return
    hint = _STATUS_HINTS.get(status, status)
    raise IntegrationError(
        f"maps request failed: {hint}", service="maps",
        recoverable=status in ("OVER_QUERY_LIMIT", "UNKNOWN_ERROR"),
    )


def _require_key(cfg, secrets) -> str:
    if not cfg.enabled:
        raise NotConfiguredError(
            "maps", "Set integrations.maps.enabled to true in config.yml."
        )
    key = secrets.maps_key
    if not key:
        raise NotConfiguredError(
            "maps",
            "Set GOOGLE_MAPS_API_KEY in .env. See docs/USER_MANUAL.md for "
            "how to create and restrict the key (needs a Cloud Billing "
            "account attached to the project).",
        )
    return key


def geocode(address: str, cfg, secrets) -> str:
    """Resolve an address to coordinates and a formatted address."""
    key = _require_key(cfg, secrets)
    data = _get_json(_GEOCODE_URL, {"address": address, "key": key}, cfg.timeout_seconds)
    _check_status(data)
    result = data["results"][0]
    loc = result["geometry"]["location"]
    return f"{result['formatted_address']} ({loc['lat']:.5f}, {loc['lng']:.5f})"


def reverse_geocode(lat: float, lon: float, cfg, secrets) -> str:
    """Resolve coordinates to a human-readable address."""
    key = _require_key(cfg, secrets)
    data = _get_json(
        _GEOCODE_URL, {"latlng": f"{lat},{lon}", "key": key}, cfg.timeout_seconds
    )
    _check_status(data)
    return data["results"][0]["formatted_address"]


def directions(origin: str, destination: str, cfg, secrets, mode: str = "driving") -> str:
    """Turn-by-turn directions between two places."""
    key = _require_key(cfg, secrets)
    data = _get_json(
        _DIRECTIONS_URL,
        {"origin": origin, "destination": destination, "mode": mode, "key": key},
        cfg.timeout_seconds,
    )
    _check_status(data)
    route = data["routes"][0]
    leg = route["legs"][0]
    distance = leg["distance"]["text"]
    duration = leg["duration"]["text"]
    steps = "; ".join(
        step["html_instructions"].replace("<b>", "").replace("</b>", "")
        .replace('<div style="font-size:0.9em">', ", ").replace("</div>", "")
        for step in leg["steps"][:8]
    )
    return f"{origin} to {destination}: {distance}, {duration} by {mode}. {steps}."


def find_places(query: str, cfg, secrets, near: str = "") -> str:
    """Search for places matching a text query, optionally biased near a place."""
    key = _require_key(cfg, secrets)
    text_query = f"{query} near {near}" if near.strip() else query
    data = _get_json(
        _PLACES_SEARCH_URL, {"query": text_query, "key": key}, cfg.timeout_seconds
    )
    _check_status(data)
    results = data.get("results", [])[:8]
    if not results:
        return f"No places found for {query!r}."
    lines = [
        f"[{r['place_id']}] {r['name']} — {r.get('formatted_address', '')}"
        + (f", rating {r['rating']}" if r.get("rating") else "")
        for r in results
    ]
    return "\n".join(lines)


def place_details(place_id: str, cfg, secrets) -> str:
    """Details for a specific place — address, phone, hours, rating."""
    key = _require_key(cfg, secrets)
    data = _get_json(
        _PLACE_DETAILS_URL,
        {
            "place_id": place_id, "key": key,
            "fields": "name,formatted_address,formatted_phone_number,"
                      "opening_hours,rating,website",
        },
        cfg.timeout_seconds,
    )
    _check_status(data)
    result = data["result"]
    parts = [result.get("name", ""), result.get("formatted_address", "")]
    if result.get("formatted_phone_number"):
        parts.append(f"phone {result['formatted_phone_number']}")
    if result.get("rating"):
        parts.append(f"rating {result['rating']}")
    if "opening_hours" in result:
        open_now = result["opening_hours"].get("open_now")
        if open_now is not None:
            parts.append("open now" if open_now else "closed now")
    return ", ".join(p for p in parts if p)
