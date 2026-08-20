"""A personal spend ledger, built by parsing bank/UPI SMS.

Distinct from `peter/spend.py`, which tracks what *Peter* costs in LLM API
calls — this tracks what *you* spend, reusing the SMS-reading pipeline
already built and hardened for `read_sms`/`latest_code`.

**Heuristic, not authoritative.** Indian bank and UPI SMS formats vary
enormously — there is no standard, and every bank phrases things slightly
differently. This errs toward under-counting: a message it does not
recognise is silently skipped rather than guessed at, since a wrong number
that looks confident is worse than an honest gap. Cross-check against the
actual bank statement; treat this as a rough running total, not an
accountant. A future-dated E-Mandate notice ("Rs.1000.00 will be deducted
on...") is deliberately excluded — it is not a completed transaction yet.

Dedup uses the bank's own reference number when the SMS has one (they are
unique per transaction), falling back to a hash of sender+amount+timestamp
for the messages that don't — which means re-scanning an overlapping time
window never double-counts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from peter.core.db import Db

log = logging.getLogger(__name__)

_AMOUNT = re.compile(r"(?:rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_REF = re.compile(r"\bref\.?\s*(?:no\.?)?\s*:?\s*(\d{6,20})", re.IGNORECASE)
# Stops at a comma, newline, period, "on <date>", or "Ref" — whichever comes
# first, since real messages punctuate the clause after a name in all of
# these ways ("To X, on Y", "from X on 20-08-26. Ref No...", "at X\n").
_STOP = r"(?=[,.\n]|\s+on\s+\d|\s+ref\b|$)"
_COUNTERPARTY_TO = re.compile(
    rf"\bto\s+([A-Za-z0-9][A-Za-z0-9 .&'-]{{1,40}}?){_STOP}", re.IGNORECASE
)
_COUNTERPARTY_AT = re.compile(
    rf"\bat\s+([A-Za-z0-9][A-Za-z0-9 .&'-]{{1,40}}?){_STOP}", re.IGNORECASE
)
_COUNTERPARTY_FROM = re.compile(
    rf"\bfrom\s+([A-Za-z0-9][A-Za-z0-9 .&'-]{{1,40}}?){_STOP}", re.IGNORECASE
)

_DEBIT_WORDS = ("sent", "debited", "spent", "paid", "withdrawn", "purchase of")
_CREDIT_WORDS = ("credited", "received", "deposited", "credit of")
# A message about money that has not moved yet — must not be counted as a
# completed transaction. Checked before anything else. Deliberately narrow:
# "e-mandate" alone would also catch a completed e-mandate debit
# *confirmation*, which is a real transaction that should be counted.
_FUTURE_HINTS = ("will be deducted", "will be debited", "scheduled to be",
                  "is due on")

_BANK_HINTS = {
    "hdfc": "HDFC Bank", "sbi": "SBI", "icici": "ICICI Bank", "axis": "Axis Bank",
    "kotak": "Kotak", "pnb": "PNB", "canara": "Canara Bank", "idbi": "IDBI",
    "yes bank": "Yes Bank", "yesbank": "Yes Bank", "indusind": "IndusInd",
    "union bank": "Union Bank", "federal bank": "Federal Bank", "rbl": "RBL Bank",
    "iob": "Indian Overseas Bank", "bank of baroda": "Bank of Baroda",
    "boi": "Bank of India", "paytm": "Paytm", "phonepe": "PhonePe",
}


@dataclass(slots=True)
class Transaction:
    when: datetime | None
    amount: float
    direction: str  # "debit" | "credit"
    counterparty: str | None
    bank: str | None
    reference: str | None
    raw: str


def parse_transaction(body: str, when: datetime | None) -> Transaction | None:
    """A completed transaction from one SMS body, or None if this message
    is not one — no amount, no debit/credit wording, or a future-dated
    notice rather than something that already happened."""
    lowered = body.lower()
    if any(hint in lowered for hint in _FUTURE_HINTS):
        return None

    amount_match = _AMOUNT.search(body)
    if not amount_match:
        return None
    amount = float(amount_match.group(1).replace(",", ""))

    direction = None
    if any(word in lowered for word in _DEBIT_WORDS):
        direction = "debit"
    if any(word in lowered for word in _CREDIT_WORDS):
        # Rare for a real message to trip both; credit wins if it does, since
        # "received" messages sometimes also mention "paid" in an unrelated
        # clause ("paid via UPI"), the reverse is not seen in practice.
        direction = "credit"
    if direction is None:
        return None

    counterparty = None
    if direction == "debit":
        match = _COUNTERPARTY_TO.search(body) or _COUNTERPARTY_AT.search(body)
    else:
        match = _COUNTERPARTY_FROM.search(body)
    if match:
        counterparty = match.group(1).strip()

    ref_match = _REF.search(body)
    reference = ref_match.group(1) if ref_match else None

    bank = None
    for hint, name in _BANK_HINTS.items():
        if hint in lowered:
            bank = name
            break

    return Transaction(
        when=when, amount=amount, direction=direction,
        counterparty=counterparty, bank=bank, reference=reference,
        raw=body.strip()[:300],
    )


def _fallback_key(sender: str, amount: float, when: datetime | None) -> str:
    stamp = when.isoformat() if when else "unknown"
    return f"{sender}|{amount}|{stamp}"


# ------------------------------------------------------------------- storage
_SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key    TEXT NOT NULL UNIQUE,
    ts           REAL,
    day          TEXT,
    amount       REAL NOT NULL,
    direction    TEXT NOT NULL,
    counterparty TEXT,
    bank         TEXT,
    sender       TEXT,
    reference    TEXT,
    raw          TEXT
);
CREATE INDEX IF NOT EXISTS expenses_day ON expenses(day);
CREATE INDEX IF NOT EXISTS expenses_direction ON expenses(direction);
"""


class ExpenseStore:
    def __init__(self, db_path: Path):
        self.db = Db(db_path, _SCHEMA)

    def close(self) -> None:
        self.db.close()

    def exists(self, dedup_key: str) -> bool:
        return bool(self.db.scalar(
            "SELECT COUNT(*) FROM expenses WHERE dedup_key = ?", (dedup_key,)
        ))

    def record(self, dedup_key: str, txn: Transaction, sender: str) -> None:
        stamp = txn.when.timestamp() if txn.when else None
        day = txn.when.strftime("%Y-%m-%d") if txn.when else None
        self.db.execute(
            """INSERT OR IGNORE INTO expenses
                   (dedup_key, ts, day, amount, direction, counterparty,
                    bank, sender, reference, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dedup_key, stamp, day, txn.amount, txn.direction, txn.counterparty,
             txn.bank, sender, txn.reference, txn.raw),
        )

    def total(self, days: int, direction: str = "debit") -> float:
        return float(self.db.scalar(
            "SELECT SUM(amount) FROM expenses WHERE direction = ? AND ts >= ?",
            (direction, _days_ago(days)), default=0.0,
        ))

    def by_counterparty(self, days: int, direction: str = "debit", limit: int = 10):
        rows = self.db.query(
            """SELECT COALESCE(counterparty, 'Unknown') AS who,
                      COUNT(*) AS n, SUM(amount) AS total
                 FROM expenses WHERE direction = ? AND ts >= ?
             GROUP BY who ORDER BY total DESC LIMIT ?""",
            (direction, _days_ago(days), limit),
        )
        return [(r["who"], r["n"], r["total"] or 0.0) for r in rows]


def _days_ago(days: int) -> float:
    return (datetime.now() - timedelta(days=max(1, days))).timestamp()


# --------------------------------------------------------------------- scan
def scan(cfg_phone, store: ExpenseStore, hours: int = 24) -> int:
    """Read recent SMS, parse transactions, store the new ones.

    Returns how many were newly recorded. Safe to call repeatedly with
    overlapping windows — already-seen transactions are never re-counted.
    """
    from peter.integrations.phone import adb

    messages = adb.messages(cfg_phone, since_minutes=max(1, hours) * 60, limit=200)
    added = 0
    for message in messages:
        txn = parse_transaction(message.body, message.when)
        if txn is None:
            continue
        key = txn.reference or _fallback_key(message.sender, txn.amount, txn.when)
        if store.exists(key):
            continue
        store.record(key, txn, sender=message.sender)
        added += 1
    return added


# ------------------------------------------------------------------- report
def expense_report(days: int = 30) -> str:
    """A human-readable spend summary."""
    from peter.core.services import services

    store = services().expenses()
    spent = store.total(days, "debit")
    received = store.total(days, "credit")
    if spent == 0 and received == 0:
        return (
            f"No transactions recorded in the last {days} day(s). "
            "Try scan_bank_sms first."
        )

    lines = [f"Last {days} day(s): spent {spent:,.2f}, received {received:,.2f}."]
    top = store.by_counterparty(days)
    if top:
        lines.append("Top spending:")
        lines += [f"  {who}  {total:,.2f}  ({n}x)" for who, n, total in top[:5]]
    return "\n".join(lines)
