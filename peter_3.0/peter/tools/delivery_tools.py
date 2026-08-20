"""Shipment tracking, built from courier SMS.

Needs integrations.phone.enabled — same SMS pipeline as expense tracking.
See peter/deliveries.py for what it does and does not recognise. On-demand
only — no background sweep — for the same reason as expense scanning.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.core.errors import PeterError
from peter.core.services import services


@peter_tool(tier="write")
def scan_delivery_sms(hours: int = 24) -> str:
    """Scan recent SMS for courier/shipment updates and record them.

    Each shipment's status only ever advances (shipped -> out for delivery ->
    delivered) — an older message arriving after a newer one does not
    regress an already-delivered shipment.

    Args:
        hours: How far back to look.
    """
    from peter.deliveries import scan

    try:
        changed = scan(services().config.integrations.phone, services().deliveries(),
                       hours=hours)
    except PeterError as exc:
        return exc.spoken()

    if changed == 0:
        return f"No new shipment updates found in the last {hours} hour(s)."
    return f"Recorded {changed} shipment update(s)."


@peter_tool(tier="read")
def pending_deliveries() -> str:
    """List shipments that have not yet been marked delivered.

    Reports only what scan_delivery_sms has already recorded — run that
    first if this says nothing is tracked yet.
    """
    from peter.deliveries import pending_deliveries as _pending

    return _pending()
