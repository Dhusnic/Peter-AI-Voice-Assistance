"""Google Maps tools — geocoding, directions, places.

Off unless integrations.maps.enabled is true and GOOGLE_MAPS_API_KEY is set
in .env — see docs/USER_MANUAL.md before turning this on, it needs a Google
Cloud Billing account attached to the project, unlike every other
integration here. Until then these calls raise NotConfiguredError, caught
below and returned as spoken text — same self-contained pattern as
weather_tools.py, since maps.py (like weather.py) is plain functions with no
lazy client to gate through services().
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.errors import PeterError
from peter.core.services import services

register_skill(SkillManifest(
    name="maps", version="1.0.0",
    description="Google Maps: geocoding, directions, and place search (needs a paid API key).",
    module=__name__, permissions=("network",),
    tools=("geocode_address", "reverse_geocode", "get_directions",
           "find_nearby_places", "get_place_details"),
))


def _cfg_and_secrets():
    cfg = services().config
    return cfg.integrations.maps, cfg.secrets


@peter_tool(tier="read")
def geocode_address(address: str) -> str:
    """Resolve an address to coordinates and a formatted address.

    Args:
        address: The address or place name to look up.
    """
    from peter.integrations import maps

    if not address.strip():
        return "Give an address to look up."
    cfg, secrets = _cfg_and_secrets()
    try:
        return maps.geocode(address.strip(), cfg, secrets)
    except PeterError as exc:
        return exc.spoken()


@peter_tool(tier="read")
def reverse_geocode(latitude: float, longitude: float) -> str:
    """Resolve coordinates to a human-readable address.

    Args:
        latitude: The latitude.
        longitude: The longitude.
    """
    from peter.integrations import maps

    cfg, secrets = _cfg_and_secrets()
    try:
        return maps.reverse_geocode(latitude, longitude, cfg, secrets)
    except PeterError as exc:
        return exc.spoken()


@peter_tool(tier="read")
def get_directions(origin: str, destination: str, mode: str = "driving") -> str:
    """Get directions between two places.

    Args:
        origin: Starting address or place name.
        destination: Destination address or place name.
        mode: One of "driving", "walking", "bicycling", "transit".
    """
    from peter.integrations import maps

    if not origin.strip() or not destination.strip():
        return "Give both an origin and a destination."
    cfg, secrets = _cfg_and_secrets()
    try:
        return maps.directions(origin.strip(), destination.strip(), cfg, secrets, mode=mode)
    except PeterError as exc:
        return exc.spoken()


@peter_tool(tier="read")
def find_nearby_places(query: str, near: str = "") -> str:
    """Search for places matching a description, optionally near a place.

    Args:
        query: What to search for, e.g. "coffee shop" or "pharmacy".
        near: Optional place name to bias the search around.
    """
    from peter.integrations import maps

    if not query.strip():
        return "Give something to search for."
    cfg, secrets = _cfg_and_secrets()
    try:
        return maps.find_places(query.strip(), cfg, secrets, near=near.strip())
    except PeterError as exc:
        return exc.spoken()


@peter_tool(tier="read")
def get_place_details(place_id: str) -> str:
    """Get details (address, phone, rating, hours) for a specific place.

    Args:
        place_id: The place id, from find_nearby_places.
    """
    from peter.integrations import maps

    cfg, secrets = _cfg_and_secrets()
    try:
        return maps.place_details(place_id.strip(), cfg, secrets)
    except PeterError as exc:
        return exc.spoken()
