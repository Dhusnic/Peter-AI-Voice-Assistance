"""Top headlines, via Google News' public RSS feed. See peter/integrations/news.py."""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.core.errors import PeterError
from peter.core.services import services


@peter_tool(tier="read")
def get_news(topic: str = "") -> str:
    """Report today's top headlines.

    Args:
        topic: A topic or search term to narrow the headlines to, e.g.
            "technology" or "cricket". Empty uses integrations.news.topic,
            or general top headlines if that is also empty.
    """
    from peter.integrations import news

    try:
        return news.headlines(services().config.integrations.news,
                              topic_override=topic.strip() or None)
    except PeterError as exc:
        return exc.spoken()
