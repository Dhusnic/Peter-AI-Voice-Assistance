"""A shipment tracker, built by parsing courier SMS.

Same pipeline and the same honesty caveat as `peter/expenses.py`: courier
SMS formats vary across carriers, and a message this does not recognise is
silently skipped rather than guessed at.

Dedup and status-advancing both key on the tracking number when the SMS has
one — the same shipment produces several messages over its life ("shipped",
then "out for delivery", then "delivered"), and each new message should
*advance* the stored status rather than create a duplicate row or regress a
"delivered" shipment back to "shipped" because an earlier message arrived
late. Without a tracking number, the fallback key (carrier + day + status)
cannot tell two different shipments from the same carrier apart on the same
day — an accepted, documented limitation rather than a silent one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from peter.core.db import Db

log = logging.getLogger(__name__)

_CARRIER_HINTS = {
    "delhivery": "Delhivery", "bluedart": "Bluedart", "blue dart": "Bluedart",
    "dtdc": "DTDC", "ecom express": "Ecom Express", "xpressbees": "XpressBees",
    "shadowfax": "Shadowfax", "india post": "India Post", "speed post": "India Post",
    "ekart": "Ekart", "fedex": "FedEx", "dhl": "DHL", "amazon": "Amazon Logistics",
}

# Checked in this order — a message can plausibly mention several stages
# ("was shipped and is now out for delivery"), and the most advanced one
# present is the one that matters.
_STATUS_HINTS = (
    ("delivered", "delivered"),
    ("out for delivery", "out_for_delivery"),
    ("will be delivered", "in_transit"),
    ("shipped", "shipped"),
    ("dispatched", "shipped"),
)
_STATUS_RANK = {"shipped": 1, "in_transit": 2, "out_for_delivery": 3, "delivered": 4}

_TRACKING = re.compile(
    r"\b(?:awb|tracking\s*(?:id|no\.?|number)?)\s*[:\-]?\s*([A-Za-z0-9]{6,20})\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Shipment:
    when: datetime | None
    carrier: str | None
    tracking: str | None
    status: str
    raw: str


def parse_shipment(body: str, when: datetime | None) -> Shipment | None:
    """A shipment update from one SMS body, or None if it doesn't look like
    one at all."""
    lowered = body.lower()
    status = None
    for hint, code in _STATUS_HINTS:
        if hint in lowered:
            status = code
            break
    if status is None:
        return None

    carrier = None
    for hint, name in _CARRIER_HINTS.items():
        if hint in lowered:
            carrier = name
            break

    match = _TRACKING.search(body)
    tracking = match.group(1) if match else None

    return Shipment(when=when, carrier=carrier, tracking=tracking, status=status,
                    raw=body.strip()[:300])


def _fallback_key(shipment: Shipment) -> str:
    day = shipment.when.strftime("%Y-%m-%d") if shipment.when else "unknown"
    return f"{shipment.carrier or 'unknown'}|{day}"


# ------------------------------------------------------------------- storage
_SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key  TEXT NOT NULL UNIQUE,
    ts         REAL,
    carrier    TEXT,
    tracking   TEXT,
    status     TEXT NOT NULL,
    raw        TEXT
);
CREATE INDEX IF NOT EXISTS shipments_status ON shipments(status);
"""


class DeliveryStore:
    def __init__(self, db_path: Path):
        self.db = Db(db_path, _SCHEMA)

    def close(self) -> None:
        self.db.close()

    def upsert(self, dedup_key: str, shipment: Shipment) -> bool:
        """Insert a new shipment, or advance an existing one's status if the
        new message is further along. Returns True if anything changed."""
        existing = self.db.one(
            "SELECT status FROM shipments WHERE dedup_key = ?", (dedup_key,)
        )
        if existing is None:
            self.db.execute(
                """INSERT INTO shipments (dedup_key, ts, carrier, tracking, status, raw)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (dedup_key, shipment.when.timestamp() if shipment.when else None,
                 shipment.carrier, shipment.tracking, shipment.status, shipment.raw),
            )
            return True
        if _STATUS_RANK.get(shipment.status, 0) > _STATUS_RANK.get(existing["status"], 0):
            self.db.execute(
                "UPDATE shipments SET status = ? WHERE dedup_key = ?",
                (shipment.status, dedup_key),
            )
            return True
        return False

    def pending(self):
        return self.db.query(
            "SELECT * FROM shipments WHERE status != 'delivered' ORDER BY ts DESC"
        )


# --------------------------------------------------------------------- scan
def scan(cfg_phone, store: DeliveryStore, hours: int = 24) -> int:
    """Read recent SMS, parse shipment updates, upsert them.

    Returns how many rows were newly created or advanced to a later status.
    """
    from peter.integrations.phone import adb

    messages = adb.messages(cfg_phone, since_minutes=max(1, hours) * 60, limit=200)
    changed = 0
    for message in messages:
        shipment = parse_shipment(message.body, message.when)
        if shipment is None:
            continue
        key = shipment.tracking or _fallback_key(shipment)
        if store.upsert(key, shipment):
            changed += 1
    return changed


# ------------------------------------------------------------------- report
def pending_deliveries() -> str:
    """A human-readable list of shipments not yet marked delivered."""
    from peter.core.services import services

    rows = services().deliveries().pending()
    if not rows:
        return (
            "Nothing pending — either everything tracked has been delivered, "
            "or scan_delivery_sms hasn't been run yet."
        )

    lines = ["Pending deliveries:"]
    for row in rows:
        carrier = row["carrier"] or "Unknown carrier"
        tracking = f" ({row['tracking']})" if row["tracking"] else ""
        lines.append(f"  {carrier}{tracking} — {row['status'].replace('_', ' ')}")
    return "\n".join(lines)
