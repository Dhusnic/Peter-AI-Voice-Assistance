"""Top headlines via Google News' public RSS feed.

No real network calls here — `_get_xml` is monkeypatched at the module level,
the one seam every headline fetch goes through.
"""

from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from peter.core.errors import IntegrationError
from peter.integrations import news


def news_config(**kwargs):
    base = dict(enabled=True, topic="", max_items=5, region="IN", language="en",
               timeout_seconds=10.0)
    base.update(kwargs)
    return SimpleNamespace(**base)


def rss(*items: tuple[str, str]) -> ElementTree.Element:
    """Build a minimal RSS tree: items is a list of (title, source)."""
    parts = ["<rss><channel>"]
    for title, source in items:
        parts.append(
            f"<item><title>{title}</title><source>{source}</source></item>"
        )
    parts.append("</channel></rss>")
    return ElementTree.fromstring("".join(parts))


def test_headlines_lists_titles_and_sources(monkeypatch):
    monkeypatch.setattr(
        news, "_get_xml",
        lambda url, params, timeout: rss(("Big story", "The Hindu"), ("Second story", "NDTV")),
    )

    result = news.headlines(news_config())

    assert "1. Big story (The Hindu)" in result
    assert "2. Second story (NDTV)" in result
    assert "Top headlines:" in result


def test_headlines_uses_search_endpoint_and_reports_the_topic(monkeypatch):
    seen = {}

    def get_xml(url, params, timeout):
        seen["url"] = url
        seen["params"] = params
        return rss(("Chip shortage eases", "Reuters"))

    monkeypatch.setattr(news, "_get_xml", get_xml)

    result = news.headlines(news_config(), topic_override="technology")

    assert "news.google.com/rss/search" in seen["url"]
    assert seen["params"]["q"] == "technology"
    assert "Top headlines for technology:" in result


def test_configured_topic_is_used_when_no_override_given(monkeypatch):
    seen = {}

    def get_xml(url, params, timeout):
        seen["params"] = params
        return rss(("A", "B"))

    monkeypatch.setattr(news, "_get_xml", get_xml)

    news.headlines(news_config(topic="cricket"))

    assert seen["params"]["q"] == "cricket"


def test_falls_back_to_splitting_the_title_when_no_source_element(monkeypatch):
    tree = ElementTree.fromstring(
        "<rss><channel><item><title>Headline text - Some Source</title></item></channel></rss>"
    )
    monkeypatch.setattr(news, "_get_xml", lambda *a, **k: tree)

    result = news.headlines(news_config())

    assert "Headline text (Some Source)" in result


def test_max_items_caps_the_list(monkeypatch):
    monkeypatch.setattr(
        news, "_get_xml",
        lambda *a, **k: rss(("A", "S"), ("B", "S"), ("C", "S")),
    )

    result = news.headlines(news_config(max_items=2))

    assert "1. A" in result
    assert "2. B" in result
    assert "3." not in result


def test_no_items_says_so(monkeypatch):
    monkeypatch.setattr(news, "_get_xml", lambda *a, **k: rss())

    assert "No headlines found" in news.headlines(news_config())


def test_disabled_reports_switched_off_without_a_network_call(monkeypatch):
    calls = []
    monkeypatch.setattr(news, "_get_xml", lambda *a, **k: calls.append(1))

    assert news.headlines(news_config(enabled=False)) == "News is switched off in config.yml."
    assert calls == []


def test_a_network_failure_is_reported_as_recoverable(monkeypatch):
    def get_xml(url, params, timeout):
        raise IntegrationError("news feed unreachable: x", service="news", recoverable=True)

    monkeypatch.setattr(news, "_get_xml", get_xml)

    with pytest.raises(IntegrationError) as caught:
        news.headlines(news_config())
    assert caught.value.recoverable is True


# -------------------------------------------------------------------- tool
def test_get_news_tool_reports_headlines(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.news import tools as news_tools  # noqa: F401

    monkeypatch.setattr(news, "headlines", lambda cfg, topic_override=None: "1. Big story")

    assert registry.get_record("get_news").raw_fn() == "1. Big story"


def test_get_news_tool_passes_through_a_topic(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.news import tools as news_tools  # noqa: F401

    seen = {}
    monkeypatch.setattr(
        news, "headlines",
        lambda cfg, topic_override=None: seen.setdefault("topic", topic_override) or "ok",
    )

    registry.get_record("get_news").raw_fn(topic="cricket")

    assert seen["topic"] == "cricket"


def test_get_news_tool_reports_an_error_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.news import tools as news_tools  # noqa: F401

    def boom(cfg, topic_override=None):
        raise IntegrationError("news feed unreachable: x", service="news", recoverable=True)

    monkeypatch.setattr(news, "headlines", boom)

    result = registry.get_record("get_news").raw_fn()

    assert "news" in result.lower()
