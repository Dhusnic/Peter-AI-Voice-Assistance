"""Parsing bank/UPI SMS into a personal expense ledger.

Real-shaped rows throughout, taken from actual bank SMS seen on a live
device during development — the format variance across banks is the whole
source of risk here, not the SQLite side, which is a thin copy of
peter/spend.py's already-tested pattern.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from peter.expenses import ExpenseStore, expense_report, parse_transaction, scan
from peter.integrations.phone import adb


def ago(minutes=1):
    return datetime.now() - timedelta(minutes=minutes)


@pytest.fixture
def store(tmp_path):
    s = ExpenseStore(tmp_path / "expenses.db")
    yield s
    s.close()


# ------------------------------------------------------------------ parsing
def test_a_upi_debit_is_parsed_with_payee_and_reference():
    body = (
        "Sent Rs.60.00\nFrom HDFC Bank A/C *3371\nTo SHIVA KUMAR\nOn 19/08/26\n"
        "Ref 201234311186\nNot You? Call 18002586161/SMS BLOCK UPI to 7308080808"
    )
    txn = parse_transaction(body, ago())

    assert txn.amount == 60.0
    assert txn.direction == "debit"
    assert txn.counterparty == "SHIVA KUMAR"
    assert txn.bank == "HDFC Bank"
    assert txn.reference == "201234311186"


def test_a_credit_extracts_the_sender_not_the_trailing_clause():
    """Real bug caught during development: a naive "from X" match ran to the
    end of the sentence, swallowing the date and reference number into the
    counterparty name."""
    body = "Rs.500 credited to your account XX1234 from RAVI KUMAR on 20-08-26. Ref No 998877."
    txn = parse_transaction(body, ago())

    assert txn.direction == "credit"
    assert txn.counterparty == "RAVI KUMAR"
    assert txn.reference == "998877"


def test_a_card_spend_extracts_the_merchant_via_at():
    body = "Your account is debited Rs.1,234.50 for purchase of groceries at BigBasket on 18-08-26."
    txn = parse_transaction(body, ago())

    assert txn.amount == 1234.50
    assert txn.direction == "debit"
    assert txn.counterparty == "BigBasket"


def test_a_future_dated_mandate_notice_is_not_a_transaction():
    """Not money that has moved yet — must not be counted."""
    body = (
        "E-Mandate!\nRs.1000.00 will be deducted on 21/08/26, 00:00:00\n"
        "For Google Cloud mandate\nUMN 59578fcdcfc3b4f0e063b22fb00afbb4@oksbi\n"
        "Maintain Balance\n-HDFC Bank"
    )
    assert parse_transaction(body, ago()) is None


def test_a_completed_mandate_debit_is_still_counted():
    """The exclusion is specifically for future-tense wording, not the word
    "mandate" itself — a completed e-mandate debit is a real transaction."""
    body = "E-Mandate debited Rs.1000.00 for Google Cloud mandate. Ref 5551234."
    txn = parse_transaction(body, ago())
    assert txn is not None
    assert txn.direction == "debit"


def test_an_otp_message_is_not_a_transaction():
    assert parse_transaction("Your OTP is 445566. Do not share it.", ago()) is None


def test_a_message_with_no_amount_is_not_a_transaction():
    assert parse_transaction("Your account was accessed from a new device.", ago()) is None


# -------------------------------------------------------------------- store
def test_recording_a_transaction_shows_up_in_the_total(store):
    txn = parse_transaction("Sent Rs.100.00 to X, Ref 111111", ago())
    store.record("k1", txn, sender="VM-HDFCBK-T")
    assert store.total(days=7, direction="debit") == 100.0


def test_a_credit_does_not_count_toward_the_debit_total(store):
    txn = parse_transaction("Rs.100 credited from X on 20-08-26. Ref 222222.", ago())
    store.record("k2", txn, sender="VM-HDFCBK-T")
    assert store.total(days=7, direction="debit") == 0.0
    assert store.total(days=7, direction="credit") == 100.0


def test_by_counterparty_groups_and_sums(store):
    for i in range(3):
        txn = parse_transaction(f"Sent Rs.50.00 to Swiggy, Ref {100000 + i}", ago())
        store.record(f"k{i}", txn, sender="VM-HDFCBK-T")

    top = store.by_counterparty(days=7)
    assert top == [("Swiggy", 3, 150.0)]


def test_exists_is_true_only_after_recording(store):
    assert store.exists("k1") is False
    txn = parse_transaction("Sent Rs.10.00 to X, Ref 333333", ago())
    store.record("k1", txn, sender="X")
    assert store.exists("k1") is True


# --------------------------------------------------------------------- scan
def phone_config(**kwargs):
    base = dict(enabled=True, adb_path="adb", device_serial="", sms_limit=10,
               otp_window_minutes=10, timeout_seconds=5.0)
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_scan_records_new_transactions_and_skips_non_transactions(monkeypatch, store, tmp_path):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    monkeypatch.setattr(adb, "_contacts_cached", {})
    when_ms = int(ago(5).timestamp() * 1000)

    def run(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f"Row: 0 address=VM-HDFCBK-T, body=Sent Rs.60.00 to SHIVA KUMAR, "
                f"Ref 201234311186, date={when_ms}\n"
                f"Row: 1 address=OTP-SENDER, body=Your OTP is 445566, date={when_ms}\n"
            ),
            stderr="",
        )

    import subprocess
    monkeypatch.setattr(subprocess, "run", run)

    added = scan(phone_config(), store, hours=24)

    assert added == 1
    assert store.total(days=7, direction="debit") == 60.0


def test_scan_does_not_double_count_on_a_second_pass(monkeypatch, store):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    monkeypatch.setattr(adb, "_contacts_cached", {})
    when_ms = int(ago(5).timestamp() * 1000)

    def run(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=f"Row: 0 address=VM-HDFCBK-T, body=Sent Rs.60.00 to X, "
                   f"Ref 201234311186, date={when_ms}\n",
            stderr="",
        )

    import subprocess
    monkeypatch.setattr(subprocess, "run", run)

    first = scan(phone_config(), store, hours=24)
    second = scan(phone_config(), store, hours=24)

    assert first == 1
    assert second == 0
    assert store.total(days=7, direction="debit") == 60.0


# ------------------------------------------------------------------- report
def test_report_says_nothing_recorded_when_empty(store, monkeypatch):
    from peter.core.services import ServiceContainer, set_container
    from peter.core.config import Config

    container = ServiceContainer(Config())
    container._expenses = store
    set_container(container)
    try:
        assert "No transactions recorded" in expense_report(30)
    finally:
        set_container(None)


def test_report_includes_totals_and_top_counterparty(store, monkeypatch):
    from peter.core.services import ServiceContainer, set_container
    from peter.core.config import Config

    txn = parse_transaction("Sent Rs.250.00 to Amazon, Ref 444444", ago())
    store.record("k1", txn, sender="X")

    container = ServiceContainer(Config())
    container._expenses = store
    set_container(container)
    try:
        result = expense_report(30)
        assert "spent 250.00" in result
        assert "Amazon" in result
    finally:
        set_container(None)


# -------------------------------------------------------------------- tools
def test_scan_bank_sms_tool_reports_the_count(container, monkeypatch):
    from peter.agent import registry
    import peter.expenses as expenses_module

    registry.reset_for_tests()
    from peter.tools import expense_tools  # noqa: F401

    monkeypatch.setattr(expenses_module, "scan", lambda cfg, store, hours: 3)

    result = registry.get_record("scan_bank_sms").raw_fn()

    assert "3 new transaction" in result


def test_scan_bank_sms_tool_reports_a_phone_error_speakably(container, monkeypatch):
    from peter.agent import registry
    import peter.expenses as expenses_module
    from peter.core.errors import IntegrationError

    registry.reset_for_tests()
    from peter.tools import expense_tools  # noqa: F401

    def boom(cfg, store, hours):
        raise IntegrationError("no device", service="phone",
                               user_action="Connect the phone by USB.")

    monkeypatch.setattr(expenses_module, "scan", boom)

    result = registry.get_record("scan_bank_sms").raw_fn()

    assert "Connect the phone by USB" in result


def test_expense_report_tool_delegates_to_the_report_function(container, monkeypatch):
    from peter.agent import registry
    import peter.expenses as expenses_module

    registry.reset_for_tests()
    from peter.tools import expense_tools  # noqa: F401

    monkeypatch.setattr(expenses_module, "expense_report", lambda days: "a report")

    assert registry.get_record("expense_report").raw_fn() == "a report"
