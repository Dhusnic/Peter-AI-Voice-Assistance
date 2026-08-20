"""Multi-site comparison.

The point of the feature is what does *not* come back: five pages of raw text
must never reach the main conversation. So the tests check the boundary — each
page goes to its own isolated call, and only short findings are synthesised.
"""

from types import SimpleNamespace

import pytest

from peter.agent.subagents import Finding, _site, compare
from peter.llm.base import ProviderResponse


class FakeBrowser:
    def __init__(self, pages=None, errors=None):
        self.pages = pages or {}
        self.errors = errors or {}
        self.reads = []

    def read_page(self, url):
        self.reads.append(url)
        if url in self.errors:
            raise self.errors[url]
        text = self.pages.get(url, "nothing here")
        return SimpleNamespace(as_prompt=lambda max_chars: text[:max_chars])


class ScriptedProvider:
    """Returns a reply chosen by what the prompt contains."""

    def __init__(self, replies, log):
        self.replies = replies
        self.log = log
        self.sent = ""

    def add_user(self, text):
        self.sent = text

    def complete(self, tools):
        assert tools == []
        self.log.append(self.sent)
        for needle, reply in self.replies.items():
            if needle in self.sent:
                return ProviderResponse(text=reply)
        return ProviderResponse(text="not on this page")

    def close(self): ...


@pytest.fixture
def comparing(container, monkeypatch):
    prompts: list[str] = []
    # Matched in order, so the synthesis prompt (which contains every site
    # name) has to be recognised before the per-page ones.
    replies = {
        "Findings": "Amazon is cheapest at 18,400 rupees.",
        "amazon": "18,400 rupees, in stock",
        "flipkart": "19,900 rupees, in stock",
    }
    monkeypatch.setattr(
        "peter.llm.factory.build_provider",
        lambda *a, **k: ScriptedProvider(replies, prompts),
    )
    return SimpleNamespace(container=container, prompts=prompts)


def wire_browser(container, **kwargs):
    browser = FakeBrowser(**kwargs)
    container.browser = lambda: browser
    return browser


# ------------------------------------------------------------------ the flow
def test_each_page_is_read_and_compared(comparing):
    wire_browser(comparing.container, pages={
        "https://amazon.in/x": "amazon page text, price 18400",
        "https://flipkart.com/x": "flipkart page text, price 19900",
    })

    answer = compare(["https://amazon.in/x", "https://flipkart.com/x"],
                     "which is cheapest")

    assert "Amazon is cheapest" in answer


def test_each_page_goes_to_its_own_isolated_call(comparing):
    """Two pages in one prompt is the thing this feature exists to avoid."""
    wire_browser(comparing.container, pages={
        "https://amazon.in/x": "amazon page text",
        "https://flipkart.com/x": "flipkart page text",
    })

    compare(["https://amazon.in/x", "https://flipkart.com/x"], "price?")

    extraction_prompts = [p for p in comparing.prompts if "Page:" in p]
    assert len(extraction_prompts) == 2
    for prompt in extraction_prompts:
        assert not ("amazon page text" in prompt and "flipkart page text" in prompt)


def test_only_short_findings_reach_the_synthesis_call(comparing):
    wire_browser(comparing.container, pages={
        "https://amazon.in/x": "amazon " + "filler " * 2000,
        "https://flipkart.com/x": "flipkart " + "filler " * 2000,
    })

    compare(["https://amazon.in/x", "https://flipkart.com/x"], "price?")

    synthesis = [p for p in comparing.prompts if "Findings:" in p][0]
    assert "filler filler" not in synthesis
    assert len(synthesis) < 1000


def test_pages_are_fetched_one_at_a_time(comparing):
    """One browser, one page — and the per-domain spacing is what keeps the
    account un-flagged."""
    browser = wire_browser(comparing.container, pages={
        "https://a.com/x": "a", "https://b.com/x": "b", "https://c.com/x": "c",
    })

    compare(["https://a.com/x", "https://b.com/x", "https://c.com/x"], "?")

    assert browser.reads == ["https://a.com/x", "https://b.com/x", "https://c.com/x"]


# ------------------------------------------------------------------- limits
def test_one_url_is_not_a_comparison(comparing):
    assert "at least two" in compare(["https://amazon.in/x"], "?")


def test_duplicate_urls_collapse_to_one(comparing):
    assert "at least two" in compare(
        ["https://amazon.in/x", "https://amazon.in/x"], "?"
    )


def test_too_many_sites_are_capped(comparing):
    comparing.container.config.agent.subagent.max_sites = 2
    browser = wire_browser(comparing.container, pages={
        f"https://s{i}.com/x": f"page {i}" for i in range(5)
    })

    compare([f"https://s{i}.com/x" for i in range(5)], "?")

    assert len(browser.reads) == 2


def test_comparison_can_be_switched_off(comparing):
    comparing.container.config.agent.subagent.enabled = False
    assert "switched off" in compare(["https://a.com", "https://b.com"], "?")


# -------------------------------------------------------------- degradation
def test_one_unreadable_page_does_not_lose_the_others(comparing):
    wire_browser(
        comparing.container,
        pages={"https://amazon.in/x": "amazon page text"},
        errors={"https://flipkart.com/x": RuntimeError("timed out")},
    )

    answer = compare(["https://amazon.in/x", "https://flipkart.com/x"], "?")

    assert "Amazon is cheapest" in answer


def test_every_page_failing_reports_it_rather_than_asking_a_model(container,
                                                                  monkeypatch):
    wire_browser(container, errors={
        "https://a.com/x": RuntimeError("down"),
        "https://b.com/x": RuntimeError("down"),
    })
    monkeypatch.setattr(
        "peter.llm.factory.build_provider",
        lambda *a, **k: pytest.fail("should not have called a model"),
    )

    answer = compare(["https://a.com/x", "https://b.com/x"], "?")

    assert "None of those pages could be read" in answer


def test_a_failed_synthesis_falls_back_to_the_raw_findings(container, monkeypatch):
    wire_browser(container, pages={"https://a.com/x": "a", "https://b.com/x": "b"})
    calls = []

    def build(config, system, *a, **k):
        calls.append(system)
        if "compare findings" in system.lower():
            raise RuntimeError("offline")
        return ScriptedProvider({"": "12 rupees"}, [])

    monkeypatch.setattr("peter.llm.factory.build_provider", build)

    answer = compare(["https://a.com/x", "https://b.com/x"], "?")

    assert "a.com" in answer and "b.com" in answer


def test_one_failed_extraction_does_not_lose_the_other(container, monkeypatch):
    """The page was readable; the model call for it was not. That is one site
    missing from the comparison, not a failed comparison."""
    wire_browser(container, pages={"https://a.com/x": "a", "https://b.com/x": "b"})
    extractions = []

    def build(config, system, *a, **k):
        if "one web page" in system:
            extractions.append(1)
            if len(extractions) == 1:
                raise RuntimeError("rate limited")
        return ScriptedProvider({"Findings": "combined answer"}, [])

    monkeypatch.setattr("peter.llm.factory.build_provider", build)

    answer = compare(["https://a.com/x", "https://b.com/x"], "?")

    assert "combined answer" in answer


# ------------------------------------------------------------------ prompts
def test_the_extraction_prompt_forbids_guessing(container, monkeypatch):
    wire_browser(container, pages={"https://a.com/x": "a", "https://b.com/x": "b"})
    systems = []

    def build(config, system, *a, **k):
        systems.append(system)
        return ScriptedProvider({}, [])

    monkeypatch.setattr("peter.llm.factory.build_provider", build)
    compare(["https://a.com/x", "https://b.com/x"], "?")

    assert any("never guess" in s.lower() for s in systems)


def test_the_subagent_can_use_a_cheaper_model(container, monkeypatch):
    container.config.agent.subagent.model = "gemini-3.7-flash"
    wire_browser(container, pages={"https://a.com/x": "a", "https://b.com/x": "b"})
    models = []

    def build(config, system, provider=None, model=None):
        models.append(model)
        return ScriptedProvider({"Findings": "done"}, [])

    monkeypatch.setattr("peter.llm.factory.build_provider", build)
    compare(["https://a.com/x", "https://b.com/x"], "?")

    assert "gemini-3.7-flash" in models
    assert None in models  # the synthesis still uses the configured model


# ------------------------------------------------------------------ helpers
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.amazon.in/dp/B0", "amazon.in"),
        ("https://flipkart.com/x", "flipkart.com"),
        ("blinkit.com/thing", "blinkit.com"),
    ],
)
def test_the_site_name_is_the_host(url, expected):
    assert _site(url) == expected


def test_a_finding_renders_as_one_line():
    assert Finding("https://x", "x.com", "12 rupees").as_line() == "x.com: 12 rupees"
