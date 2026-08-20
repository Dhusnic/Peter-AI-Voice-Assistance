"""Browser layer: the interlock, rate limiting, extraction, and bot detection.

All of this is pure logic deliberately kept out of the Playwright classes, so
the parts that matter most for safety can be tested without launching a browser.
"""

import json
import time

import pytest

from peter.integrations.browser import detect, extract, interlock
from peter.integrations.browser.ratelimit import RateLimiter, domain_of


# =========================================================== the interlock
# This is the most safety-critical code in the project. A false negative here
# spends the user's money.
@pytest.mark.parametrize(
    "label",
    [
        "Place your order",
        "PLACE ORDER",
        "Buy Now",
        "Pay Now",
        "Proceed to Payment",
        "Continue to payment",
        "Confirm order",
        "Confirm & Pay",
        "Make Payment",
        "Complete payment",
        "Checkout",
        "Order Now",
        "Book Now",
        "Book Ticket",
        "Subscribe",
        "Start free trial",
        "Pay with UPI",
        "Pay using Credit Card",
        "Net Banking",
        "Cash on Delivery",
        "Confirm Booking",
    ],
)
def test_money_committing_labels_are_blocked(label):
    blocked, reason = interlock.is_purchase_action(label)
    assert blocked, f"{label!r} must be blocked"
    assert reason


@pytest.mark.parametrize(
    "label",
    [
        "Add to Cart",
        "Add to Bag",
        "Add to Wishlist",
        "Sign in",
        "Search",
        "Apply coupon",
        "Select size",
        "Next page",
        "Sort by price",
        "View details",
        "Change address",
        "Continue shopping",
        "View cart",
        "Go to cart",
        "Back to cart",
    ],
)
def test_ordinary_labels_are_allowed(label):
    blocked, _ = interlock.is_purchase_action(label)
    assert not blocked, f"{label!r} should not be blocked"


def test_amount_paired_with_a_verb_is_blocked():
    """Buttons like 'Confirm ₹500' pair a verb with an amount and no keyword."""
    blocked, reason = interlock.is_purchase_action("Confirm ₹500")
    assert blocked
    assert "amount" in reason


def test_ampersand_does_not_evade_the_interlock():
    """A regression: 'Confirm & Pay' slipped past a list containing
    'confirm and pay'. Punctuation is exactly how a real label evades a list."""
    for variant in ("Confirm & Pay", "Confirm&Pay", "Review & Pay", "Pay!"):
        blocked, _ = interlock.is_purchase_action(variant)
        assert blocked, f"{variant!r} evaded the interlock"


def test_safe_context_wins_over_a_scary_word():
    """'View cart (₹500)' contains an amount but commits nothing."""
    blocked, _ = interlock.is_purchase_action("View cart (₹500)")
    assert not blocked


def test_case_and_whitespace_do_not_evade_the_interlock():
    for variant in ("place   order", "  PLACE ORDER  ", "PlAcE OrDeR"):
        blocked, _ = interlock.is_purchase_action(variant)
        assert blocked, f"{variant!r} evaded the interlock"


def test_empty_label_is_not_blocked():
    assert interlock.is_purchase_action("")[0] is False
    assert interlock.is_purchase_action(None)[0] is False


def test_guard_raises_with_a_speakable_explanation():
    with pytest.raises(interlock.PurchaseBlocked) as excinfo:
        interlock.guard("Place your order")

    spoken = excinfo.value.spoken()
    assert "Place your order" in spoken
    assert "authorise the payment yourself" in spoken


def test_guard_is_silent_on_safe_labels():
    interlock.guard("Add to Cart")  # must not raise


def test_the_interlock_takes_no_override_argument():
    """An interlock with a bypass is a suggestion. Its signature must not
    offer one, so no caller can opt out of it."""
    import inspect

    for fn in (interlock.guard, interlock.is_purchase_action):
        params = list(inspect.signature(fn).parameters)
        assert params == ["label"], f"{fn.__name__} takes more than a label: {params}"


# ============================================================ rate limiting
def test_domain_normalises_www():
    assert domain_of("https://www.amazon.in/dp/B123") == "amazon.in"
    assert domain_of("https://amazon.in/dp/B123") == "amazon.in"


def test_domain_of_garbage_is_empty():
    assert domain_of("not a url") == ""
    assert domain_of("") == ""


def test_first_request_to_a_domain_is_immediate():
    limiter = RateLimiter(min_interval_seconds=5)
    assert limiter.wait("https://example.com/a") == 0.0


def test_second_request_to_the_same_domain_waits():
    limiter = RateLimiter(min_interval_seconds=0.3)
    limiter.wait("https://example.com/a")

    started = time.monotonic()
    limiter.wait("https://example.com/b")
    elapsed = time.monotonic() - started

    assert elapsed >= 0.25, "must have slept before hitting the same domain again"


def test_different_domains_do_not_block_each_other():
    limiter = RateLimiter(min_interval_seconds=5)
    limiter.wait("https://amazon.in/x")

    started = time.monotonic()
    limiter.wait("https://flipkart.com/y")

    assert time.monotonic() - started < 0.1


def test_zero_interval_disables_waiting():
    limiter = RateLimiter(min_interval_seconds=0)
    limiter.wait("https://example.com")
    assert limiter.wait("https://example.com") == 0.0


def test_time_until_ready_does_not_block():
    limiter = RateLimiter(min_interval_seconds=10)
    limiter.wait("https://example.com")

    started = time.monotonic()
    remaining = limiter.time_until_ready("https://example.com")

    assert time.monotonic() - started < 0.05
    assert 8 < remaining <= 10


def test_reset_clears_the_budget():
    limiter = RateLimiter(min_interval_seconds=10)
    limiter.wait("https://example.com")
    limiter.reset("https://example.com")
    assert limiter.time_until_ready("https://example.com") == 0.0


# ============================================================== extraction
def test_price_parsing_handles_real_formats():
    assert extract.parse_price("₹1,299.00") == 1299.0
    assert extract.parse_price("Rs. 450") == 450.0
    assert extract.parse_price("INR 2,50,000") == 250000.0
    assert extract.parse_price("1299") == 1299.0
    assert extract.parse_price(1299) == 1299.0
    assert extract.parse_price(1299.5) == 1299.5


def test_price_parsing_rejects_nonsense():
    assert extract.parse_price("") is None
    assert extract.parse_price(None) is None
    assert extract.parse_price("out of stock") is None
    assert extract.parse_price(0) is None
    assert extract.parse_price("1.2.3") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://schema.org/InStock", "in stock"),
        ("InStock", "in stock"),
        ("https://schema.org/OutOfStock", "out of stock"),
        ("Currently unavailable", "out of stock"),
        ("Sold Out", "out of stock"),
        ("", ""),
        (None, ""),
    ],
)
def test_availability_normalisation(raw, expected):
    assert extract.normalise_availability(raw) == expected


def test_jsonld_product_is_extracted():
    payload = json.dumps({
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Boat Rockerz 255 Pro",
        "brand": {"@type": "Brand", "name": "boAt"},
        "offers": {
            "@type": "Offer",
            "price": "1299.00",
            "priceCurrency": "INR",
            "availability": "https://schema.org/InStock",
        },
        "aggregateRating": {"ratingValue": "4.1", "reviewCount": "23451"},
    })

    product = extract.from_jsonld([payload])

    assert product.name == "Boat Rockerz 255 Pro"
    assert product.price == 1299.0
    assert product.currency == "INR"
    assert product.availability == "in stock"
    assert product.brand == "boAt"
    assert product.rating == 4.1
    assert product.review_count == 23451
    assert product.source == "json-ld"


def test_jsonld_inside_a_graph_is_found():
    """Many sites wrap everything in @graph rather than emitting a bare Product."""
    payload = json.dumps({
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": "Some Shop"},
            {
                "@type": "Product",
                "name": "Widget",
                "offers": {"price": 99, "priceCurrency": "INR"},
            },
        ],
    })

    product = extract.from_jsonld([payload])
    assert product.name == "Widget"
    assert product.price == 99.0


def test_jsonld_as_a_list_is_found():
    payload = json.dumps([
        {"@type": "BreadcrumbList"},
        {"@type": "Product", "name": "Widget", "offers": {"price": "50"}},
    ])
    assert extract.from_jsonld([payload]).name == "Widget"


def test_jsonld_type_as_a_list_is_matched():
    payload = json.dumps({
        "@type": ["Product", "IndividualProduct"],
        "name": "Widget",
        "offers": {"price": "50"},
    })
    assert extract.from_jsonld([payload]).found


def test_malformed_jsonld_does_not_raise():
    assert extract.from_jsonld(["{not json"]).found is False
    assert extract.from_jsonld([""]).found is False
    assert extract.from_jsonld([]).found is False


def test_offers_as_a_list_takes_the_first():
    payload = json.dumps({
        "@type": "Product",
        "name": "Widget",
        "offers": [{"price": "100", "priceCurrency": "INR"}, {"price": "200"}],
    })
    assert extract.from_jsonld([payload]).price == 100.0


def test_meta_tags_are_the_fallback():
    product = extract.from_meta({
        "og:title": "Zepto — Amul Milk 500ml",
        "product:price:amount": "34",
        "product:price:currency": "INR",
        "product:availability": "in stock",
    })

    assert "Amul Milk" in product.name
    assert product.price == 34.0
    assert product.availability == "in stock"
    assert product.source == "meta tags"


def test_text_extraction_is_flagged_low_confidence():
    product = extract.from_text("Special price ₹499 MRP ₹999", title="Some Shirt")
    assert product.price == 499.0
    assert "low confidence" in product.source


def test_best_product_prefers_jsonld_over_text():
    jsonld = json.dumps({
        "@type": "Product",
        "name": "Real Name",
        "offers": {"price": "1299", "priceCurrency": "INR"},
    })

    product = extract.best_product(
        jsonld_blocks=[jsonld],
        meta={"og:title": "Meta Name", "product:price:amount": "999"},
        text="Page says ₹1 somewhere",
        title="Title",
    )

    assert product.name == "Real Name"
    assert product.price == 1299.0
    assert product.source == "json-ld"


def test_best_product_falls_through_to_meta():
    product = extract.best_product(
        jsonld_blocks=[],
        meta={"og:title": "Meta Name", "product:price:amount": "999"},
        text="",
        title="Title",
    )
    assert product.price == 999.0


def test_best_product_returns_empty_when_nothing_is_there():
    product = extract.best_product([], {}, "just some words", "A Blog Post")
    assert not product.found


def test_clean_text_collapses_whitespace():
    cleaned = extract.clean_text("Too      many\n\n\n\n\nblanks", 4000)
    assert "     " not in cleaned
    assert "\n\n\n" not in cleaned


def test_clean_text_respects_the_cap():
    assert len(extract.clean_text("word " * 5000, 200)) <= 200


def test_page_content_renders_structured_data_first():
    content = extract.PageContent(
        url="https://shop.example/p/1",
        title="A Product",
        product=extract.Product(name="Widget", price=1299.0, currency="INR",
                                availability="in stock", source="json-ld"),
        text="lots of page text here",
    )

    rendered = content.as_prompt(max_chars=1000)

    assert rendered.index("Structured product data") < rendered.index("Page text")
    assert "INR 1,299.00" in rendered


def test_product_spoken_uses_words_not_currency_symbols():
    """This text is read aloud and printed to a cp1252 Windows console. A TTS
    engine reads a rupee sign as nothing, and printing one used to raise."""
    product = extract.Product(name="boAt Rockerz", price=1299.0, currency="INR",
                              availability="in stock", rating=4.1)
    spoken = product.spoken()

    assert "boAt Rockerz" in spoken
    assert "1,299 rupees" in spoken
    assert "in stock" in spoken
    assert spoken.encode("cp1252"), "must survive a Windows console"


def test_spoken_names_each_currency():
    for code, word in (("USD", "dollars"), ("GBP", "pounds"), ("EUR", "euros")):
        product = extract.Product(name="X", price=10.0, currency=code)
        assert word in product.spoken()


def test_scraped_currency_is_not_assumed_to_be_rupees():
    """A regression: a GBP price on a live page was announced as rupees,
    because from_text defaulted the currency instead of reading the symbol."""
    assert extract.from_text("Price: £51.77", "A Book").currency == "GBP"
    assert extract.from_text("Price: $15.00", "A Book").currency == "USD"
    assert extract.from_text("Price: ₹499", "A Book").currency == "INR"
    assert extract.from_text("Price: Rs. 499", "A Book").currency == "INR"


# =========================================================== bot detection
@pytest.mark.parametrize(
    "text",
    [
        "Enter the characters you see below",
        "Sorry, we just need to make sure you're not a robot",
        "Checking your browser before accessing",
        "Please enable JavaScript and cookies to continue",
        "We have detected unusual traffic from your network",
        "To discuss automated access to Amazon data please contact",
    ],
)
def test_challenge_text_is_detected(text):
    assert detect.check_text(text) is not None


def test_ordinary_product_text_is_not_a_challenge():
    assert detect.check_text(
        "boAt Rockerz 255 Pro Bluetooth Headset. In stock. Free delivery."
    ) is None


@pytest.mark.parametrize("status", [403, 429, 503])
def test_blocked_statuses_are_detected(status):
    assert detect.check_status(status) is not None


@pytest.mark.parametrize("status", [200, 301, 404, None])
def test_ordinary_statuses_pass(status):
    assert detect.check_status(status) is None


def test_challenge_urls_are_detected():
    assert detect.check_url("https://amazon.in/errors/validateCaptcha?x=1")
    assert detect.check_url("https://google.com/sorry/index?continue=x")
    assert detect.check_url("https://shop.com/p/1") is None


def test_challenge_titles_are_detected():
    assert detect.check_title("Just a moment...")
    assert detect.check_title("Attention Required! | Cloudflare")
    assert detect.check_title("Robot Check")
    assert detect.check_title("boAt Rockerz 255 Pro - Buy Online") is None


def test_assess_returns_the_first_signal():
    assert detect.assess("https://shop.com/p", status=200, title="OK",
                         text="normal page") is None
    assert detect.assess("https://shop.com/p", status=403) is not None
    assert detect.assess("https://shop.com/p", matched_selectors=("#px-captcha",))


def test_bot_wall_error_tells_the_user_what_to_do():
    error = detect.BotWallError("https://amazon.in/x", "HTTP 503")
    assert error.recoverable is False, "retrying a bot wall makes it worse"
    assert "solve the check by hand" in error.user_action


# ============================ regressions found by the live smoke test
# Unit fixtures are clean; real pages are not. Each of these was a real bug
# that only surfaced against a live site.
def test_a_page_title_alone_is_not_a_product():
    """books.toscrape.com has no product markup, but every page has a <title>.
    Reporting that as a product made Peter announce items with no price."""
    product = extract.from_meta({"og:title": "Some Blog Post", "title": "A Page"})
    assert not product.found


def test_meta_needs_a_real_product_signal():
    assert extract.from_meta({"og:title": "X", "product:price:amount": "99"}).found
    assert extract.from_meta({"og:title": "X", "og:type": "product"}).found
    assert extract.from_meta({"og:title": "X", "product:availability": "instock"}).found
    assert not extract.from_meta({"og:title": "X", "og:type": "article"}).found


def test_names_from_title_tags_are_tidied():
    """<title> content arrives with newlines and indentation intact."""
    messy = "\n    A Light in the Attic | Books to Scrape\n    "
    assert extract.tidy_name(messy) == "A Light in the Attic | Books to Scrape"
    assert extract.tidy_name(None) == ""


def test_jsonld_names_are_tidied():
    import json as _json

    payload = _json.dumps({
        "@type": "Product",
        "name": "  Widget\n  Pro  ",
        "offers": {"price": "10"},
    })
    assert extract.from_jsonld([payload]).name == "Widget Pro"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("£51.77", 51.77),
        ("GBP 51.77", 51.77),
        ("€19,99", 1999.0),   # comma stripped as a thousands separator
        ("€20", 20.0),
        ("$15.50", 15.5),
    ],
)
def test_non_rupee_currencies_are_parsed(raw, expected):
    """The regex originally knew only ₹/Rs/INR/$/USD and silently returned
    None for a £ price sitting in plain sight on the page."""
    assert extract.parse_price(raw) == expected


# ========================================================= engine selection
# BrowserManager._ensure_started() launches a real browser and is deliberately
# untested end-to-end (see the module docstring above) — but which *engine*
# gets asked for is plain logic, and worth pinning: a Firefox launch call
# accidentally routed to .chromium would silently ignore the config.yml
# setting and nobody would notice until a login session mysteriously vanished.
class _FakeContext:
    def __init__(self):
        self.timeout = None
        self.pages = []

    def set_default_timeout(self, ms):
        self.timeout = ms


class _FakeEngine:
    def __init__(self, name):
        self.name = name
        self.launch_kwargs = None

    def launch_persistent_context(self, **kwargs):
        self.launch_kwargs = kwargs
        return _FakeContext()


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FakeEngine("chromium")
        self.firefox = _FakeEngine("firefox")

    def start(self):
        return self

    def stop(self):
        pass


def browser_config(**kwargs):
    from peter.core.config import BrowserConfig

    return BrowserConfig(**kwargs)


def manager_with_fake_playwright(monkeypatch, tmp_path, **config_kwargs):
    from peter.integrations.browser.manager import BrowserManager

    fake = _FakePlaywright()
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright", lambda: fake
    )
    manager = BrowserManager(browser_config(**config_kwargs), tmp_path / "profile")
    return manager, fake


def test_the_default_engine_is_chromium(monkeypatch, tmp_path):
    manager, fake = manager_with_fake_playwright(monkeypatch, tmp_path)

    manager._ensure_started()

    assert fake.chromium.launch_kwargs is not None
    assert fake.firefox.launch_kwargs is None


def test_firefox_can_be_selected_instead(monkeypatch, tmp_path):
    manager, fake = manager_with_fake_playwright(monkeypatch, tmp_path, engine="firefox")

    manager._ensure_started()

    assert fake.firefox.launch_kwargs is not None
    assert fake.chromium.launch_kwargs is None


def test_chromium_only_flags_are_not_sent_to_firefox(monkeypatch, tmp_path):
    """These flags are Chromium CLI syntax; Firefox does not recognise them
    and passing them fails the launch outright rather than being ignored."""
    manager, fake = manager_with_fake_playwright(monkeypatch, tmp_path, engine="firefox")

    manager._ensure_started()

    assert fake.firefox.launch_kwargs["args"] == []


def test_chromium_keeps_its_anti_detection_flags(monkeypatch, tmp_path):
    manager, fake = manager_with_fake_playwright(monkeypatch, tmp_path, engine="chromium")

    manager._ensure_started()

    assert "--disable-blink-features=AutomationControlled" in fake.chromium.launch_kwargs["args"]


def test_both_engines_get_the_same_profile_and_locale_settings(monkeypatch, tmp_path):
    """The engine differs; the profile directory, locale and viewport must not."""
    manager, fake = manager_with_fake_playwright(monkeypatch, tmp_path, engine="firefox")

    manager._ensure_started()

    kwargs = fake.firefox.launch_kwargs
    assert kwargs["user_data_dir"] == str((tmp_path / "profile").resolve()) or \
        kwargs["user_data_dir"] == str(tmp_path / "profile")
    assert kwargs["locale"] == "en-IN"
    assert kwargs["timezone_id"] == "Asia/Kolkata"


def test_an_unknown_engine_value_cannot_reach_the_config_model():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        browser_config(engine="safari")


def test_the_install_hint_names_the_configured_engine_when_playwright_missing(
    monkeypatch, tmp_path
):
    import builtins

    from peter.core.errors import IntegrationError
    from peter.integrations.browser.manager import BrowserManager

    real_import = builtins.__import__

    def no_playwright(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_playwright)
    manager = BrowserManager(browser_config(engine="firefox"), tmp_path / "profile")

    with pytest.raises(IntegrationError) as caught:
        manager._ensure_started()
    assert "install firefox" in caught.value.user_action


def test_the_launch_failure_hint_mentions_incompatible_profiles(monkeypatch, tmp_path):
    from peter.core.errors import IntegrationError
    from peter.integrations.browser.manager import BrowserManager

    class BrokenEngine:
        def launch_persistent_context(self, **kwargs):
            raise RuntimeError("profile is not a valid firefox profile")

    class BrokenPlaywright:
        def __init__(self):
            self.firefox = BrokenEngine()
            self.chromium = BrokenEngine()

        def start(self):
            return self

        def stop(self):
            pass

    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright", lambda: BrokenPlaywright()
    )
    manager = BrowserManager(browser_config(engine="firefox"), tmp_path / "profile")

    with pytest.raises(IntegrationError) as caught:
        manager._ensure_started()
    assert "fresh profile_dir" in caught.value.user_action
