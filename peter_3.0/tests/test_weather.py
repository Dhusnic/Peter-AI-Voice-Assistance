"""Current weather via Open-Meteo.

No real network calls here — `_get_json` is monkeypatched at the module
level, since it is the one seam both geocoding and the forecast call go
through.
"""

from types import SimpleNamespace

import pytest

from peter.core.errors import IntegrationError, NotConfiguredError
from peter.integrations import weather


def weather_config(**kwargs):
    base = dict(enabled=True, location="", latitude=0.0, longitude=0.0,
               units="metric", timeout_seconds=10.0)
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset_geocode_cache(monkeypatch):
    monkeypatch.setattr(weather, "_geocode_cache", {})


def fake_json(monkeypatch, responses):
    """responses: dict of url -> return value, matched by substring."""
    def get_json(url, params, timeout):
        for needle, value in responses.items():
            if needle in url:
                return value
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(weather, "_get_json", get_json)


FORECAST_RESPONSE = {
    "current": {
        "temperature_2m": 31.4, "relative_humidity_2m": 60,
        "weather_code": 2, "wind_speed_10m": 12.3,
    }
}
GEOCODE_RESPONSE = {
    "results": [{"latitude": 13.08, "longitude": 80.27, "name": "Chennai", "country": "India"}]
}


def test_current_reports_condition_temperature_and_wind(monkeypatch):
    fake_json(monkeypatch, {
        "geocoding-api": GEOCODE_RESPONSE,
        "api.open-meteo.com": FORECAST_RESPONSE,
    })

    result = weather.current(weather_config(location="Chennai"))

    assert "Chennai, India" in result
    assert "partly cloudy" in result
    assert "31" in result
    assert "60% humidity" in result
    assert "12 km/h" in result


def test_current_uses_imperial_units_when_configured(monkeypatch):
    fake_json(monkeypatch, {
        "geocoding-api": GEOCODE_RESPONSE,
        "api.open-meteo.com": FORECAST_RESPONSE,
    })

    result = weather.current(weather_config(location="Chennai", units="imperial"))

    assert "°F" in result
    assert "mph" in result


def test_current_skips_geocoding_when_lat_lon_are_set(monkeypatch):
    calls = []

    def get_json(url, params, timeout):
        calls.append(url)
        return FORECAST_RESPONSE

    monkeypatch.setattr(weather, "_get_json", get_json)

    result = weather.current(weather_config(latitude=13.08, longitude=80.27, location="Chennai"))

    assert len(calls) == 1  # only the forecast call, no geocoding
    assert "Chennai" in result


def test_current_reports_switched_off_without_a_network_call(monkeypatch):
    calls = []
    monkeypatch.setattr(weather, "_get_json", lambda *a, **k: calls.append(1))

    assert weather.current(weather_config(enabled=False)) == "Weather is switched off in config.yml."
    assert calls == []


def test_current_raises_not_configured_with_no_location_or_coordinates(monkeypatch):
    with pytest.raises(NotConfiguredError):
        weather.current(weather_config())


def test_location_override_bypasses_the_configured_location(monkeypatch):
    calls = []

    def get_json(url, params, timeout):
        calls.append(params.get("name", params))
        if "geocoding" in url:
            return {"results": [{"latitude": 35.6, "longitude": 139.7,
                                  "name": "Tokyo", "country": "Japan"}]}
        return FORECAST_RESPONSE

    monkeypatch.setattr(weather, "_get_json", get_json)

    result = weather.current(weather_config(location="Chennai"), location_override="Tokyo")

    assert "Tokyo, Japan" in result
    assert calls[0] == "Tokyo"  # the override was geocoded, not "Chennai"


def test_geocode_is_cached_across_calls(monkeypatch):
    calls = []

    def get_json(url, params, timeout):
        calls.append(url)
        if "geocoding" in url:
            return GEOCODE_RESPONSE
        return FORECAST_RESPONSE

    monkeypatch.setattr(weather, "_get_json", get_json)

    weather.current(weather_config(location="Chennai"))
    weather.current(weather_config(location="Chennai"))

    geocode_calls = [c for c in calls if "geocoding" in c]
    assert len(geocode_calls) == 1  # second call reused the cache


def test_an_unknown_place_raises_a_speakable_error(monkeypatch):
    fake_json(monkeypatch, {"geocoding-api": {"results": []}})

    with pytest.raises(IntegrationError) as caught:
        weather.current(weather_config(location="Nowhereville"))
    assert "Nowhereville" in str(caught.value)


def test_a_network_failure_is_reported_as_recoverable(monkeypatch):
    def get_json(url, params, timeout):
        raise IntegrationError("weather service unreachable: x", service="weather",
                               recoverable=True)

    monkeypatch.setattr(weather, "_get_json", get_json)

    with pytest.raises(IntegrationError) as caught:
        weather.current(weather_config(latitude=1.0, longitude=1.0))
    assert caught.value.recoverable is True


# -------------------------------------------------------------------- tool
def test_get_weather_tool_reports_the_conditions(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import weather_tools  # noqa: F401

    monkeypatch.setattr(weather, "current", lambda cfg, location_override=None: "sunny")

    assert registry.get_record("get_weather").raw_fn() == "sunny"


def test_get_weather_tool_passes_through_a_location_override(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import weather_tools  # noqa: F401

    seen = {}
    monkeypatch.setattr(
        weather, "current",
        lambda cfg, location_override=None: seen.setdefault("loc", location_override) or "ok",
    )

    registry.get_record("get_weather").raw_fn(location="Mumbai")

    assert seen["loc"] == "Mumbai"


def test_get_weather_tool_reports_an_error_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import weather_tools  # noqa: F401

    def boom(cfg, location_override=None):
        raise NotConfiguredError("weather", "Set integrations.weather.location.")

    monkeypatch.setattr(weather, "current", boom)

    result = registry.get_record("get_weather").raw_fn()

    assert "Set integrations.weather.location" in result
