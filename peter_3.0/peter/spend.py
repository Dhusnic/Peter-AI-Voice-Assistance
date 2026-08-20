"""What Peter costs, per turn, kept.

`Brain.usage_summary()` has always reported the session total, which vanishes
when the process exits. Every actual question about cost is about a period
longer than one session: is Gemini cheaper than Claude in practice, did
switching to `auto` help, what does a working day cost, is that price rise a
trend or one expensive afternoon.

So each turn's usage is appended here as a row, in USD — the unit every vendor
bills in — and converted to rupees only when a human reads it. Storing the
converted figure would freeze the day's exchange rate into history and make
last month's numbers uncomparable with this month's.

The daily cap built on top is deliberately blunt: it counts what has been
spent today and either says something or stops. It cannot be a per-request
estimate, because you do not know a turn's cost until after it has run.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from peter.core.db import Db

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    day           TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read    INTEGER NOT NULL DEFAULT 0,
    cache_write   INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS spend_day ON spend(day);
"""


@dataclass(slots=True)
class DayTotal:
    day: str
    turns: int
    cost_usd: float
    input_tokens: int = 0
    output_tokens: int = 0


class SpendLog:
    def __init__(self, db_path: Path):
        self.db = Db(db_path, _SCHEMA)

    def close(self) -> None:
        self.db.close()

    def record(
        self,
        provider: str,
        model: str,
        cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        when: float | None = None,
    ) -> None:
        """Append one turn. A zero-cost turn is still worth a row — it is
        evidence the cache is working."""
        stamp = when if when is not None else time.time()
        self.db.execute(
            """INSERT INTO spend
                   (ts, day, provider, model, input_tokens, output_tokens,
                    cache_read, cache_write, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stamp,
                datetime.fromtimestamp(stamp).strftime("%Y-%m-%d"),
                provider, model, input_tokens, output_tokens,
                cache_read, cache_write, max(0.0, cost_usd),
            ),
        )

    # ---------------------------------------------------------------- reading
    def today_usd(self) -> float:
        return float(self.db.scalar(
            "SELECT SUM(cost_usd) FROM spend WHERE day = ?",
            (date.today().strftime("%Y-%m-%d"),),
            default=0.0,
        ))

    def since_usd(self, days: int) -> float:
        return float(self.db.scalar(
            "SELECT SUM(cost_usd) FROM spend WHERE ts >= ?",
            (_days_ago(days),), default=0.0,
        ))

    def by_day(self, days: int = 7) -> list[DayTotal]:
        rows = self.db.query(
            """SELECT day, COUNT(*) AS turns, SUM(cost_usd) AS cost,
                      SUM(input_tokens) AS tin, SUM(output_tokens) AS tout
                 FROM spend WHERE ts >= ?
             GROUP BY day ORDER BY day DESC""",
            (_days_ago(days),),
        )
        return [
            DayTotal(r["day"], r["turns"], r["cost"] or 0.0, r["tin"] or 0, r["tout"] or 0)
            for r in rows
        ]

    def by_provider(self, days: int = 7) -> list[tuple[str, str, int, float]]:
        rows = self.db.query(
            """SELECT provider, model, COUNT(*) AS turns, SUM(cost_usd) AS cost
                 FROM spend WHERE ts >= ?
             GROUP BY provider, model ORDER BY cost DESC""",
            (_days_ago(days),),
        )
        return [(r["provider"], r["model"], r["turns"], r["cost"] or 0.0) for r in rows]

    def turn_count(self, days: int = 7) -> int:
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM spend WHERE ts >= ?", (_days_ago(days),)
        ))

    def prune(self, keep_days: int = 400) -> int:
        """Drop rows older than `keep_days`. A year of history is plenty."""
        return self.db.execute(
            "DELETE FROM spend WHERE ts < ?", (_days_ago(keep_days),)
        ).rowcount


# ------------------------------------------------------------------ the cap
@dataclass(slots=True)
class BudgetState:
    limit_inr: float
    spent_inr: float
    action: str

    @property
    def enabled(self) -> bool:
        return self.limit_inr > 0

    @property
    def exceeded(self) -> bool:
        return self.enabled and self.spent_inr >= self.limit_inr

    @property
    def blocked(self) -> bool:
        return self.exceeded and self.action == "block"

    def message(self) -> str:
        return (
            f"Today's spend is {self.spent_inr:.2f} rupees, past the "
            f"{self.limit_inr:.0f} rupee daily cap in config.yml."
        )


def budget_state(config, spend_log: SpendLog) -> BudgetState:
    cfg = config.agent.budget
    rate = config.agent.usd_to_inr_rate
    return BudgetState(
        limit_inr=cfg.daily_inr,
        spent_inr=spend_log.today_usd() * rate,
        action=cfg.action,
    )


# ------------------------------------------------------------------ the report
def report(days: int = 7) -> str:
    """A human-readable spend summary in rupees."""
    from peter.core.services import services

    container = services()
    rate = container.config.agent.usd_to_inr_rate
    log_store = container.spend()

    turns = log_store.turn_count(days)
    if not turns:
        return f"No usage recorded in the last {days} day(s)."

    total = log_store.since_usd(days) * rate
    today = log_store.today_usd() * rate

    lines = [
        f"Last {days} day(s): {total:.2f} rupees over {turns} turns "
        f"({total / max(1, turns):.2f} per turn). Today: {today:.2f}."
    ]

    daily = log_store.by_day(days)
    if len(daily) > 1:
        lines.append("By day:")
        lines += [
            f"  {d.day}  {d.cost_usd * rate:7.2f}  ({d.turns} turns)" for d in daily
        ]

    providers = log_store.by_provider(days)
    if providers:
        lines.append("By model:")
        lines += [
            f"  {provider}/{model}  {cost * rate:7.2f}  ({count} turns)"
            for provider, model, count, cost in providers
        ]

    state = budget_state(container.config, log_store)
    if state.enabled:
        left = state.limit_inr - state.spent_inr
        lines.append(
            f"Daily cap {state.limit_inr:.0f} rupees — "
            + (f"{left:.2f} left today." if left > 0 else "reached for today.")
        )
    return "\n".join(lines)


def _days_ago(days: int) -> float:
    return (datetime.now() - timedelta(days=max(1, days))).timestamp()
