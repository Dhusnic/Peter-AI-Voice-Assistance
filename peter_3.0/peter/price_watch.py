"""Standing price and stock watches.

Phase 4 of the plan, and the cheapest big feature in the codebase: the hard
parts already existed. `check_price` proved a product page can be read from its
own published structured data; the browser layer already spaces requests per
domain; the scheduler already survives restarts. All this adds is the list of
things being watched and the rule for when a change is worth interrupting you
about.

**The rule matters more than the polling.** A watcher that announces every
one-rupee wobble gets muted within a day, so an alert fires only when

    the price reaches a target you set,
    it falls by a meaningful percentage on its own, or
    something you were waiting for comes back in stock

and never twice for the same price. `evaluate()` is a pure function of the
stored watch and the freshly-read product precisely so that rule can be tested
without a browser anywhere near it.

**Polling stays slow on purpose.** Every read goes through the same per-domain
rate limit as any other browse, so a sweep of several watches on one site takes
minutes. That spacing is the main thing standing between this and a flagged
account; the fix for "the sweep is slow" is fewer watches, not a shorter gap.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from peter.core.db import Db
from peter.core.errors import PeterError

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_watches (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    url               TEXT NOT NULL UNIQUE,
    label             TEXT NOT NULL DEFAULT '',
    target_price      REAL,
    currency          TEXT NOT NULL DEFAULT 'INR',
    last_price        REAL,
    last_availability TEXT NOT NULL DEFAULT '',
    best_price        REAL,
    alerted_price     REAL,
    failures          INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL,
    checked_at        REAL
);
"""

_SPOKEN_CURRENCY = {
    "INR": "rupees", "USD": "dollars", "GBP": "pounds", "EUR": "euros",
}


@dataclass(slots=True)
class Watch:
    id: int
    url: str
    label: str = ""
    target_price: float | None = None
    currency: str = "INR"
    last_price: float | None = None
    last_availability: str = ""
    best_price: float | None = None
    alerted_price: float | None = None
    failures: int = 0
    created_at: float = 0.0
    checked_at: float | None = None

    @property
    def name(self) -> str:
        return self.label or _short_url(self.url)


class WatchStore:
    """The watch list. One row per URL."""

    def __init__(self, db_path: Path):
        self.db = Db(db_path, _SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add(
        self, url: str, label: str = "", target_price: float | None = None
    ) -> Watch:
        """Add a watch, or update the target on one already watching this URL."""
        self.db.execute(
            """INSERT INTO price_watches (url, label, target_price, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   label        = CASE WHEN excluded.label != '' THEN excluded.label
                                       ELSE price_watches.label END,
                   target_price = excluded.target_price,
                   -- A new target is a new question, so the "already told you
                   -- about this price" memory has to be cleared with it.
                   alerted_price = NULL""",
            (url.strip(), label.strip(), target_price, time.time()),
        )
        found = self.by_url(url)
        assert found is not None  # just inserted
        return found

    def by_url(self, url: str) -> Watch | None:
        row = self.db.one("SELECT * FROM price_watches WHERE url = ?", (url.strip(),))
        return _row_to_watch(row) if row else None

    def get(self, watch_id: int) -> Watch | None:
        row = self.db.one("SELECT * FROM price_watches WHERE id = ?", (watch_id,))
        return _row_to_watch(row) if row else None

    def all(self) -> list[Watch]:
        return [
            _row_to_watch(r)
            for r in self.db.query("SELECT * FROM price_watches ORDER BY id")
        ]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM price_watches"))

    def delete(self, watch_id: int) -> bool:
        return self.db.execute(
            "DELETE FROM price_watches WHERE id = ?", (watch_id,)
        ).rowcount > 0

    def find(self, needle: str) -> list[Watch]:
        pattern = f"%{needle.strip().lower()}%"
        return [
            _row_to_watch(r)
            for r in self.db.query(
                "SELECT * FROM price_watches "
                "WHERE lower(label) LIKE ? OR lower(url) LIKE ? ORDER BY id",
                (pattern, pattern),
            )
        ]

    def record_check(
        self,
        watch_id: int,
        price: float | None,
        currency: str,
        availability: str,
        alerted: bool,
    ) -> None:
        """Save what this sweep saw. `alerted` pins the price we announced."""
        self.db.execute(
            """UPDATE price_watches SET
                   last_price        = COALESCE(?, last_price),
                   currency          = ?,
                   last_availability = ?,
                   best_price        = CASE
                       WHEN ? IS NULL THEN best_price
                       WHEN best_price IS NULL OR ? < best_price THEN ?
                       ELSE best_price END,
                   alerted_price     = CASE WHEN ? THEN ? ELSE alerted_price END,
                   failures          = CASE WHEN ? IS NULL THEN failures + 1 ELSE 0 END,
                   checked_at        = ?
               WHERE id = ?""",
            (price, currency or "INR", availability,
             price, price, price,
             1 if alerted else 0, price,
             price, time.time(), watch_id),
        )


# ------------------------------------------------------------------ the rule
def evaluate(watch: Watch, product, drop_percent: float, alert_on_restock: bool) -> str:
    """What (if anything) is worth saying about this reading. Pure function.

    Returns the announcement, or "" for the overwhelmingly common case of
    nothing having meaningfully changed.
    """
    price = getattr(product, "price", None)
    availability = getattr(product, "availability", "") or ""
    currency = getattr(product, "currency", "") or watch.currency
    name = getattr(product, "name", "") or watch.name

    if (
        alert_on_restock
        and watch.last_availability == "out of stock"
        and availability == "in stock"
    ):
        where = f" at {_money(price, currency)}" if price is not None else ""
        return f"{name} is back in stock{where}."

    if price is None:
        return ""

    # Never repeat an announcement for a price already announced. Only a
    # *further* fall is news.
    already_told = watch.alerted_price is not None and price >= watch.alerted_price

    if watch.target_price and price <= watch.target_price and not already_told:
        return (
            f"{name} is down to {_money(price, currency)}, at or below your "
            f"{_money(watch.target_price, currency)} target."
        )

    if watch.last_price and not already_told:
        fall = (watch.last_price - price) / watch.last_price * 100
        if fall >= drop_percent:
            return (
                f"{name} dropped {fall:.0f}% — {_money(watch.last_price, currency)} "
                f"to {_money(price, currency)}."
            )

    return ""


# ------------------------------------------------------------- the sweep job
def check_price_watches() -> None:
    """Scheduler job target. Must stay importable at this exact path."""
    from peter.core.notify import notify
    from peter.core.services import services

    container = services()
    cfg = container.config.integrations.price_watch
    if not cfg.enabled:
        return

    try:
        watches = container.watches().all()
    except Exception:
        log.exception("price watch: could not read the watch list")
        return
    if not watches:
        return

    log.info("price watch: sweeping %d watch(es)", len(watches))
    for watch in watches:
        try:
            _check_one(watch, cfg, container, notify)
        except PeterError as exc:
            log.info("price watch: %s unreadable this sweep (%s)", watch.name, exc)
            container.watches().record_check(watch.id, None, watch.currency, "", False)
        except Exception:
            log.exception("price watch: failed checking %s", watch.url)


def _check_one(watch: Watch, cfg, container, notify) -> None:
    content = container.browser().read_page(watch.url)
    product = content.product

    alert = evaluate(watch, product, cfg.drop_percent, cfg.alert_on_restock)
    container.watches().record_check(
        watch.id,
        product.price,
        product.currency,
        product.availability,
        alerted=bool(alert),
    )
    if not alert:
        return

    log.info("price watch: %s", alert)
    container.say(alert)
    notify("Peter — price watch", f"{alert}\n{watch.url}")


def schedule_price_watches(scheduler, config) -> None:
    """Install (or re-install) the price sweep."""
    cfg = config.integrations.price_watch
    if not cfg.enabled:
        return
    scheduler.add_interval_job(
        job_id="price-watch-sweep",
        minutes=cfg.poll_interval_minutes,
        func=check_price_watches,
        name="price watch sweep",
    )
    log.info(
        "price watch: sweeping every %d minute(s)", cfg.poll_interval_minutes
    )


# ------------------------------------------------------------------ helpers
def _money(value: float | None, currency: str) -> str:
    """Spoken, not symbolic.

    Peter reads these aloud, and a TTS engine given "₹1,299" says "1,299" at
    best. A Windows console in cp1252 cannot even print the symbol.
    """
    if value is None:
        return "an unknown price"
    unit = _SPOKEN_CURRENCY.get((currency or "INR").upper(), currency or "rupees")
    return f"{value:,.0f} {unit}"


def _short_url(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url if "://" in url else f"https://{url}")
    return parts.netloc.replace("www.", "") or url


def _row_to_watch(row) -> Watch:
    return Watch(
        id=row["id"],
        url=row["url"],
        label=row["label"],
        target_price=row["target_price"],
        currency=row["currency"],
        last_price=row["last_price"],
        last_availability=row["last_availability"],
        best_price=row["best_price"],
        alerted_price=row["alerted_price"],
        failures=row["failures"],
        created_at=row["created_at"],
        checked_at=row["checked_at"],
    )
