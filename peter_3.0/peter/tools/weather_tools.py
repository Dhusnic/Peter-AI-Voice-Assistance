"""Current weather, via Open-Meteo — free, no API key. See
peter/integrations/weather.py.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.core.errors import PeterError
from peter.core.services import services


@peter_tool(tier="read")
def get_weather(location: str = "") -> str:
    """Report the current weather.

    Args:
        location: A city name to check instead of the configured default,
            e.g. "Mumbai". Empty uses integrations.weather.location.
    """
    from peter.integrations import weather

    try:
        return weather.current(services().config.integrations.weather,
                               location_override=location.strip() or None)
    except PeterError as exc:
        return exc.spoken()
