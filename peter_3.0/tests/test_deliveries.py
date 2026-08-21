"""Parsing courier SMS into a shipment tracker."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from peter.deliveries import DeliveryStore, parse_shipment, pending_deliveries, scan
from peter.integrations.phone import adb


def ago(minutes=1):
    return datetime.now() - timedelta(minutes=minutes)


@pytest.fixture
def store(tmp_path):
    s = DeliveryStore(tmp_path / "deliveries.db")
    yield s
    s.close()


# ------------------------------------------------------------------ parsing
def test_a_shipped_message_is_parsed_with_carrier_and_tracking():
    body = "Your order has been shipped via Delhivery. AWB: SF123456789. Track at delhivery.com"
    shipment = parse_shipment(body, ago())

    assert shipment.status == "shipped"
    assert shipment.carrier == "Delhivery"
    assert shipment.tracking == "SF123456789"


def test_out_for_delivery_beats_shipped_when_both_are_mentioned():
    body = "Your item was shipped and is now out for delivery with Ekart. Tracking No: EK998877."
    assert parse_shipment(body, ago()).status == "out_for_delivery"


def test_a_delivered_message_needs_no_tracking_number():
    shipment = parse_shipment("Your Amazon order has been delivered. Thank you!", ago())
    assert shipment.status == "delivered"
    assert shipment.carrier == "Amazon Logistics"
    assert shipment.tracking is None


def test_an_otp_message_is_not_a_shipment():
    assert parse_shipment("Your OTP for login is 445566.", ago()) is None


def test_an_unrelated_message_is_not_a_shipment():
    assert parse_shipment("Your account statement is ready for viewing.", ago()) is None


# -------------------------------------------------------------------- store
def test_upsert_inserts_a_new_shipment(store):
    shipment = parse_shipment("Shipped via DTDC. AWB 1234567.", ago())
    changed = store.upsert("1234567", shipment)
    assert changed is True
    assert len(store.pending()) == 1


def test_upsert_advances_status_for_the_same_tracking_number(store):
    shipped = parse_shipment("Shipped via DTDC. AWB 1234567.", ago(60))
    store.upsert("1234567", shipped)

    delivered = parse_shipment("Delivered! DTDC AWB 1234567 has arrived.", ago(1))
    changed = store.upsert("1234567", delivered)

    assert changed is True
    assert store.pending() == []  # no longer pending, it's delivered


def test_upsert_does_not_regress_a_more_advanced_status(store):
    """An older message processed after a newer one must not un-deliver a
    shipment that already arrived."""
    delivered = parse_shipment("Delivered! DTDC AWB 1234567 has arrived.", ago(1))
    store.upsert("1234567", delivered)

    stale_shipped = parse_shipment("Shipped via DTDC. AWB 1234567.", ago(60))
    changed = store.upsert("1234567", stale_shipped)

    assert changed is False
    assert store.pending() == []  # still delivered, not reverted


def test_pending_excludes_delivered_shipments(store):
    store.upsert("A111111", parse_shipment("Shipped via DTDC. AWB A111111.", ago()))
    store.upsert("B222222", parse_shipment("Delivered! Ekart AWB B222222 arrived.", ago()))

    pending = store.pending()
    assert len(pending) == 1
    assert pending[0]["tracking"] == "A111111"


# --------------------------------------------------------------------- scan
def phone_config(**kwargs):
    base = dict(enabled=True, adb_path="adb", device_serial="", sms_limit=10,
               otp_window_minutes=10, timeout_seconds=5.0)
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_scan_records_shipment_updates_and_skips_the_rest(monkeypatch, store):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    monkeypatch.setattr(adb, "_contacts_cached", {})
    when_ms = int(ago(5).timestamp() * 1000)

    def run(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f"Row: 0 address=VM-DELHVR-T, body=Shipped via Delhivery. "
                f"AWB SF1234567., date={when_ms}\n"
                f"Row: 1 address=OTP-SENDER, body=Your OTP is 445566, date={when_ms}\n"
            ),
            stderr="",
        )

    import subprocess
    monkeypatch.setattr(subprocess, "run", run)

    changed = scan(phone_config(), store, hours=24)

    assert changed == 1
    assert len(store.pending()) == 1


# ------------------------------------------------------------------- report
def test_pending_deliveries_reports_nothing_tracked(monkeypatch, store):
    from peter.core.services import ServiceContainer, set_container
    from peter.core.config import Config

    container = ServiceContainer(Config())
    container._deliveries = store
    set_container(container)
    try:
        assert "Nothing pending" in pending_deliveries()
    finally:
        set_container(None)


def test_pending_deliveries_lists_carrier_and_status(monkeypatch, store):
    from peter.core.services import ServiceContainer, set_container
    from peter.core.config import Config

    store.upsert("A111111", parse_shipment("Shipped via DTDC. AWB A111111.", ago()))

    container = ServiceContainer(Config())
    container._deliveries = store
    set_container(container)
    try:
        result = pending_deliveries()
        assert "DTDC" in result
        assert "A111111" in result
        assert "shipped" in result
    finally:
        set_container(None)


# -------------------------------------------------------------------- tools
def test_scan_delivery_sms_tool_reports_the_count(container, monkeypatch):
    from peter.agent import registry
    import peter.deliveries as deliveries_module

    registry.reset_for_tests()
    from peter.skills.deliveries import tools as delivery_tools  # noqa: F401

    monkeypatch.setattr(deliveries_module, "scan", lambda cfg, store, hours: 2)

    result = registry.get_record("scan_delivery_sms").raw_fn()

    assert "2 shipment update" in result


def test_scan_delivery_sms_tool_reports_a_phone_error_speakably(container, monkeypatch):
    from peter.agent import registry
    import peter.deliveries as deliveries_module
    from peter.core.errors import IntegrationError

    registry.reset_for_tests()
    from peter.skills.deliveries import tools as delivery_tools  # noqa: F401

    def boom(cfg, store, hours):
        raise IntegrationError("no device", service="phone",
                               user_action="Connect the phone by USB.")

    monkeypatch.setattr(deliveries_module, "scan", boom)

    result = registry.get_record("scan_delivery_sms").raw_fn()

    assert "Connect the phone by USB" in result


def test_pending_deliveries_tool_delegates_to_the_report_function(container, monkeypatch):
    from peter.agent import registry
    import peter.deliveries as deliveries_module

    registry.reset_for_tests()
    from peter.skills.deliveries import tools as delivery_tools  # noqa: F401

    monkeypatch.setattr(deliveries_module, "pending_deliveries", lambda: "a report")

    assert registry.get_record("pending_deliveries").raw_fn() == "a report"
