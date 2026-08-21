"""Google Maps — geocoding, directions, places.

No real network calls: `_get_json` is monkeypatched at the module level,
same seam test_weather.py already uses for its own `_get_json`. The one
thing maps.py has that weather.py does not is `_check_status` — Maps
Platform reports failure *inside* a 200 body via a `status` field, not via
HTTP status codes, so that gets its own dedicated test coverage.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from peter.core.errors import IntegrationError, NotConfiguredError
from peter.integrations import maps


def maps_config(**kwargs):
    base = dict(enabled=True, timeout_seconds=10.0)
    base.update(kwargs)
    return SimpleNamespace(**base)


def maps_secrets(key="test-key"):
    return SimpleNamespace(maps_key=key)


def fake_json(monkeypatch, responses):
    """responses: dict of url -> return value, matched by substring."""
    def get_json(url, params, timeout):
        for needle, value in responses.items():
            if needle in url:
                return value
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(maps, "_get_json", get_json)


GEOCODE_RESPONSE = {
    "status": "OK",
    "results": [{
        "formatted_address": "1600 Amphitheatre Pkwy, Mountain View, CA",
        "geometry": {"location": {"lat": 37.4224, "lng": -122.0842}},
    }],
}

DIRECTIONS_RESPONSE = {
    "status": "OK",
    "routes": [{
        "legs": [{
            "distance": {"text": "5.2 km"},
            "duration": {"text": "12 mins"},
            "steps": [
                {"html_instructions": "Head <b>north</b> on Main St"},
                {"html_instructions": "Turn <b>right</b> onto 2nd Ave"},
            ],
        }],
    }],
}

PLACES_RESPONSE = {
    "status": "OK",
    "results": [
        {"place_id": "p1", "name": "Blue Bottle Coffee", "formatted_address": "1st St",
         "rating": 4.5},
    ],
}

PLACE_DETAILS_RESPONSE = {
    "status": "OK",
    "result": {
        "name": "Blue Bottle Coffee", "formatted_address": "1st St",
        "formatted_phone_number": "555-1234", "rating": 4.5,
        "opening_hours": {"open_now": True},
    },
}


# ---------------------------------------------------------------- geocoding
def test_geocode_reports_address_and_coordinates(monkeypatch):
    fake_json(monkeypatch, {"geocode": GEOCODE_RESPONSE})
    result = maps.geocode("1600 Amphitheatre Pkwy", maps_config(), maps_secrets())
    assert "Mountain View" in result
    assert "37.42240" in result


def test_reverse_geocode_reports_formatted_address(monkeypatch):
    fake_json(monkeypatch, {"geocode": GEOCODE_RESPONSE})
    result = maps.reverse_geocode(37.4224, -122.0842, maps_config(), maps_secrets())
    assert "Mountain View" in result


# --------------------------------------------------------------- directions
def test_directions_reports_distance_duration_and_steps(monkeypatch):
    fake_json(monkeypatch, {"directions": DIRECTIONS_RESPONSE})
    result = maps.directions("Home", "Work", maps_config(), maps_secrets())
    assert "5.2 km" in result
    assert "12 mins" in result
    assert "Head north on Main St" in result


# ------------------------------------------------------------------ places
def test_find_places_lists_matches(monkeypatch):
    fake_json(monkeypatch, {"textsearch": PLACES_RESPONSE})
    result = maps.find_places("coffee", maps_config(), maps_secrets())
    assert "Blue Bottle Coffee" in result
    assert "rating 4.5" in result
    assert "[p1]" in result


def test_find_places_reports_no_results(monkeypatch):
    fake_json(monkeypatch, {"textsearch": {"status": "ZERO_RESULTS", "results": []}})
    with pytest.raises(IntegrationError):
        maps.find_places("nonexistentplace", maps_config(), maps_secrets())


def test_place_details_reports_phone_and_hours(monkeypatch):
    fake_json(monkeypatch, {"details": PLACE_DETAILS_RESPONSE})
    result = maps.place_details("p1", maps_config(), maps_secrets())
    assert "555-1234" in result
    assert "open now" in result


# ---------------------------------------------------- response-body status
def test_request_denied_status_raises_with_billing_hint(monkeypatch):
    fake_json(monkeypatch, {"geocode": {"status": "REQUEST_DENIED", "results": []}})
    with pytest.raises(IntegrationError) as excinfo:
        maps.geocode("somewhere", maps_config(), maps_secrets())
    assert "billing" in str(excinfo.value)


def test_over_query_limit_is_recoverable(monkeypatch):
    fake_json(monkeypatch, {"geocode": {"status": "OVER_QUERY_LIMIT", "results": []}})
    with pytest.raises(IntegrationError) as excinfo:
        maps.geocode("somewhere", maps_config(), maps_secrets())
    assert excinfo.value.recoverable is True


def test_zero_results_is_not_recoverable(monkeypatch):
    fake_json(monkeypatch, {"geocode": {"status": "ZERO_RESULTS", "results": []}})
    with pytest.raises(IntegrationError) as excinfo:
        maps.geocode("nowhereville", maps_config(), maps_secrets())
    assert excinfo.value.recoverable is False


# ------------------------------------------------------------- not configured
def test_geocode_raises_not_configured_when_disabled():
    with pytest.raises(NotConfiguredError):
        maps.geocode("x", maps_config(enabled=False), maps_secrets())


def test_geocode_raises_not_configured_with_no_key():
    with pytest.raises(NotConfiguredError):
        maps.geocode("x", maps_config(), maps_secrets(key=""))


# ------------------------------------------------------------------- tools
def test_geocode_address_tool_rejects_empty_address():
    from peter.skills.maps.tools import geocode_address

    assert "Give an address" in geocode_address(address="  ")


def test_geocode_address_tool_reports_not_configured(container, monkeypatch):
    from peter.skills.maps.tools import geocode_address

    # Force the disabled/no-key state explicitly rather than relying on the
    # ambient config.yml — a real deployment may have this switched on with
    # a real key, and this test must never make a real network call.
    monkeypatch.setattr(container.config.integrations.maps, "enabled", False)
    result = geocode_address(address="Mountain View")
    assert "Set integrations.maps.enabled" in result


def test_get_directions_tool_rejects_missing_destination():
    from peter.skills.maps.tools import get_directions

    assert "Give both" in get_directions(origin="Home", destination="  ")


def test_find_nearby_places_tool_rejects_empty_query():
    from peter.skills.maps.tools import find_nearby_places

    assert "Give something to search for" in find_nearby_places(query="  ")


def test_reverse_geocode_tool_reports_not_configured(container, monkeypatch):
    from peter.skills.maps.tools import reverse_geocode

    # Force the enabled-but-no-key state explicitly — same reasoning as
    # test_geocode_address_tool_reports_not_configured above.
    monkeypatch.setattr(container.config.secrets, "google_maps_api_key", SecretStr(""))
    result = reverse_geocode(latitude=1.0, longitude=1.0)
    assert "GOOGLE_MAPS_API_KEY" in result
