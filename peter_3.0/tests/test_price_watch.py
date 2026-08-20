"""Price watches: the alert rule, the store, and the sweep.

The rule is what makes or breaks this feature. A watcher that announces every
small wobble gets muted inside a day, so most of these tests are about
*silence* — the cases where nothing should be said at all.
"""

from types import SimpleNamespace

import pytest

from peter.core.errors import IntegrationError
from peter.price_watch import (
    Watch,
    WatchStore,
    check_price_watches,
    evaluate,
    schedule_price_watches,
)


def product(price=None, availability="", currency="INR", name="Monitor"):
    return SimpleNamespace(
        price=price, availability=availability, currency=currency, name=name,
        found=price is not None or bool(name),
    )


def watch(**kwargs):
    base = dict(id=1, url="https://shop.example/item", label="the monitor")
    base.update(kwargs)
    return Watch(**base)


@pytest.fixture
def watches(tmp_path):
    store = WatchStore(tmp_path / "watches.db")
    yield store
    store.close()


# ------------------------------------------------------------------ the rule
def test_a_price_at_the_target_is_announced():
    text = evaluate(watch(target_price=20000, last_price=24000),
                    product(price=19500), drop_percent=5, alert_on_restock=True)
    assert "19,500 rupees" in text
    assert "target" in text


def test_a_price_above_the_target_does_not_claim_the_target_was_met():
    text = evaluate(watch(target_price=20000, last_price=24000),
                    product(price=23900), 5, True)
    assert text == ""


def test_a_big_fall_short_of_the_target_is_still_worth_saying():
    """Not reaching the target is not the same as nothing having happened —
    a 12% fall is news whether or not it cleared the number you named."""
    text = evaluate(watch(target_price=20000, last_price=24000),
                    product(price=21000), 5, True)
    assert "dropped 12%" in text
    assert "target" not in text


def test_the_same_price_is_not_announced_twice():
    """The whole point of alerted_price: a target met on Monday must not be
    re-announced every sweep for the rest of the week."""
    already = watch(target_price=20000, last_price=19500, alerted_price=19500)
    assert evaluate(already, product(price=19500), 5, True) == ""


def test_a_further_fall_after_an_alert_is_announced_again():
    already = watch(target_price=20000, last_price=19500, alerted_price=19500)
    text = evaluate(already, product(price=18000), 5, True)
    assert "18,000 rupees" in text


def test_a_meaningful_drop_is_announced_with_no_target_set():
    text = evaluate(watch(last_price=10000), product(price=9000), drop_percent=5,
                    alert_on_restock=True)
    assert "dropped 10%" in text
    assert "10,000 rupees to 9,000 rupees" in text


def test_a_small_drop_is_not_worth_interrupting_for():
    assert evaluate(watch(last_price=10000), product(price=9800), 5, True) == ""


def test_a_price_rise_says_nothing():
    assert evaluate(watch(last_price=9000), product(price=11000), 5, True) == ""


def test_a_first_reading_with_no_history_says_nothing():
    """Adding a watch must not immediately announce a 'drop' from nothing."""
    assert evaluate(watch(), product(price=9000), 5, True) == ""


def test_a_restock_is_announced():
    text = evaluate(watch(last_availability="out of stock"),
                    product(price=9000, availability="in stock"), 5, True)
    assert "back in stock" in text
    assert "9,000 rupees" in text


def test_a_restock_can_be_switched_off():
    assert evaluate(watch(last_availability="out of stock"),
                    product(price=9000, availability="in stock"),
                    5, alert_on_restock=False) == ""


def test_something_still_in_stock_is_not_announced_as_a_restock():
    assert evaluate(watch(last_availability="in stock"),
                    product(price=9000, availability="in stock"), 5, True) == ""


def test_an_unreadable_price_says_nothing_rather_than_guessing():
    assert evaluate(watch(target_price=20000, last_price=24000),
                    product(price=None), 5, True) == ""


def test_the_page_name_is_preferred_over_the_stored_label():
    text = evaluate(watch(label="the monitor", last_price=10000),
                    product(price=9000, name="Dell U2723QE"), 5, True)
    assert "Dell U2723QE" in text


# ----------------------------------------------------------------- the store
def test_adding_and_reading_back_a_watch(watches):
    added = watches.add("https://shop.example/x", label="a thing", target_price=500)

    assert added.label == "a thing"
    assert added.target_price == 500
    assert watches.count() == 1


def test_watching_the_same_url_updates_rather_than_duplicating(watches):
    watches.add("https://shop.example/x", label="a thing", target_price=500)
    watches.add("https://shop.example/x", target_price=400)

    assert watches.count() == 1
    assert watches.by_url("https://shop.example/x").target_price == 400


def test_an_updated_target_clears_the_already_announced_price(watches):
    """A new target is a new question — the old 'I already told you' must go."""
    added = watches.add("https://shop.example/x", target_price=500)
    watches.record_check(added.id, 480, "INR", "in stock", alerted=True)
    assert watches.get(added.id).alerted_price == 480

    watches.add("https://shop.example/x", target_price=400)
    assert watches.get(added.id).alerted_price is None


def test_an_empty_label_does_not_wipe_an_existing_one(watches):
    watches.add("https://shop.example/x", label="the monitor")
    watches.add("https://shop.example/x", target_price=100)
    assert watches.by_url("https://shop.example/x").label == "the monitor"


def test_recording_a_check_tracks_the_lowest_price_ever_seen(watches):
    added = watches.add("https://shop.example/x")

    watches.record_check(added.id, 900, "INR", "in stock", alerted=False)
    watches.record_check(added.id, 700, "INR", "in stock", alerted=False)
    watches.record_check(added.id, 800, "INR", "in stock", alerted=False)

    stored = watches.get(added.id)
    assert stored.last_price == 800
    assert stored.best_price == 700


def test_a_failed_check_counts_failures_without_losing_the_last_price(watches):
    added = watches.add("https://shop.example/x")
    watches.record_check(added.id, 900, "INR", "in stock", alerted=False)

    watches.record_check(added.id, None, "INR", "", alerted=False)

    stored = watches.get(added.id)
    assert stored.last_price == 900
    assert stored.failures == 1


def test_a_successful_check_resets_the_failure_count(watches):
    added = watches.add("https://shop.example/x")
    watches.record_check(added.id, None, "INR", "", alerted=False)
    watches.record_check(added.id, 900, "INR", "in stock", alerted=False)
    assert watches.get(added.id).failures == 0


def test_finding_a_watch_by_part_of_its_label(watches):
    watches.add("https://shop.example/x", label="the 27-inch monitor")
    watches.add("https://shop.example/y", label="a keyboard")

    assert len(watches.find("monitor")) == 1
    assert watches.find("nothing here") == []


def test_deleting_a_watch(watches):
    added = watches.add("https://shop.example/x")
    assert watches.delete(added.id) is True
    assert watches.count() == 0


# ----------------------------------------------------------------- the sweep
class FakeBrowser:
    def __init__(self, products=None, error=None):
        self.products = products or {}
        self.error = error
        self.reads = []

    def read_page(self, url):
        self.reads.append(url)
        if self.error:
            raise self.error
        return SimpleNamespace(product=self.products.get(url, product(price=None)))


@pytest.fixture
def sweeping(container, watches):
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    container.watches = lambda: watches
    return SimpleNamespace(said=said, watches=watches, container=container)


def test_a_sweep_with_no_watches_touches_nothing(sweeping):
    sweeping.container.browser = lambda: pytest.fail("must not open a browser")
    check_price_watches()
    assert sweeping.said == []


def test_a_sweep_announces_a_target_being_met(sweeping):
    url = "https://shop.example/x"
    added = sweeping.watches.add(url, label="the monitor", target_price=20000)
    sweeping.watches.record_check(added.id, 24000, "INR", "in stock", alerted=False)
    sweeping.container.browser = lambda: FakeBrowser({url: product(price=19000)})

    check_price_watches()

    assert len(sweeping.said) == 1
    assert "19,000 rupees" in sweeping.said[0]
    assert sweeping.watches.get(added.id).alerted_price == 19000


def test_a_sweep_stays_quiet_when_nothing_changed(sweeping):
    url = "https://shop.example/x"
    added = sweeping.watches.add(url, target_price=20000)
    sweeping.watches.record_check(added.id, 24000, "INR", "in stock", alerted=False)
    sweeping.container.browser = lambda: FakeBrowser({url: product(price=24000)})

    check_price_watches()

    assert sweeping.said == []


def test_one_unreadable_page_does_not_stop_the_others(sweeping):
    good = "https://shop.example/good"
    bad = "https://shop.example/bad"
    sweeping.watches.add(bad)
    added = sweeping.watches.add(good, target_price=20000)
    sweeping.watches.record_check(added.id, 24000, "INR", "", alerted=False)

    class PartlyBroken(FakeBrowser):
        def read_page(self, url):
            if url == bad:
                raise IntegrationError("timed out", service="browser")
            return SimpleNamespace(product=product(price=19000))

    sweeping.container.browser = lambda: PartlyBroken()

    check_price_watches()

    assert any("19,000" in s for s in sweeping.said)


def test_a_disabled_watcher_does_nothing(sweeping, monkeypatch):
    monkeypatch.setattr(
        sweeping.container.config.integrations.price_watch, "enabled", False
    )
    sweeping.watches.add("https://shop.example/x", target_price=1)
    sweeping.container.browser = lambda: pytest.fail("must not open a browser")

    check_price_watches()

    assert sweeping.said == []


# ------------------------------------------------------------- the scheduling
def test_a_disabled_watcher_is_not_scheduled(config, monkeypatch):
    calls = []
    scheduler = SimpleNamespace(add_interval_job=lambda **kw: calls.append(kw))
    monkeypatch.setattr(config.integrations.price_watch, "enabled", False)

    schedule_price_watches(scheduler, config)

    assert calls == []


def test_the_sweep_uses_a_stable_job_id(config):
    calls = []
    scheduler = SimpleNamespace(add_interval_job=lambda **kw: calls.append(kw))

    schedule_price_watches(scheduler, config)
    schedule_price_watches(scheduler, config)

    assert calls[0]["job_id"] == calls[1]["job_id"] == "price-watch-sweep"
