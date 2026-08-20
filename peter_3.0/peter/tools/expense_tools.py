"""Personal expense tracking, built from bank/UPI SMS.

Needs integrations.phone.enabled — the SMS pipeline this reuses lives there.
Heuristic, not authoritative: see peter/expenses.py for exactly what it does
and does not recognise. On-demand only in this first version — no background
sweep — since a ledger silently double-counting or mis-parsing unattended is
a worse failure mode than one that only runs when asked.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.core.errors import PeterError
from peter.core.services import services


@peter_tool(tier="write")
def scan_bank_sms(hours: int = 24) -> str:
    """Scan recent SMS for bank/UPI transactions and add new ones to the
    personal expense ledger.

    Safe to run repeatedly with overlapping windows — a transaction already
    recorded (matched by the bank's own reference number, or a fallback key)
    is never counted twice.

    Args:
        hours: How far back to look.
    """
    from peter.expenses import scan

    try:
        added = scan(services().config.integrations.phone, services().expenses(),
                     hours=hours)
    except PeterError as exc:
        return exc.spoken()

    if added == 0:
        return f"No new transactions found in the last {hours} hour(s)."
    return f"Recorded {added} new transaction(s)."


@peter_tool(tier="read")
def expense_report(days: int = 30) -> str:
    """Summarise recorded personal spending: totals and top counterparties.

    Reports only what scan_bank_sms has already recorded — run that first if
    this says nothing has been tracked yet.

    Args:
        days: How many days back to summarise.
    """
    from peter.expenses import expense_report as _report

    return _report(days)
