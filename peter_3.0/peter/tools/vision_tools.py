"""Tools that let Peter actually look at something.

`take_screenshot` in system.py saves a PNG and returns a path — useful to a
human, meaningless to the model, which then has to say "I saved a screenshot"
and stop. These close that loop: the image is captured, sized, sent, and what
comes back is an answer to the question you asked.

The one this exists for: pointing at a stack trace and saying "what's this
error?" without copying anything anywhere.
"""

from __future__ import annotations

from pathlib import Path

from peter.agent.registry import peter_tool
from peter.core.errors import PeterError
from peter.core.services import services


@peter_tool(tier="read")
def look_at_screen(question: str = "", primary_only: bool = False) -> str:
    """Look at what is on screen right now and answer a question about it.

    Use this whenever the user refers to something they can see — "what's this
    error", "what does this say", "is this the right branch", "read me that
    number" — instead of asking them to type it out.

    Args:
        question: What to look for. Defaults to describing the screen.
        primary_only: True to capture only the main monitor rather than all of
            them. Useful when a wide multi-monitor grab makes the text small.
    """
    from peter.llm import vision

    config = services().config
    try:
        path = vision.capture_screen(config, region="primary" if primary_only else "")
        return vision.describe_image(path, question, config)
    except PeterError as exc:
        return exc.spoken() if hasattr(exc, "spoken") else str(exc)
    except Exception as exc:
        return f"I could not look at the screen: {type(exc).__name__}: {exc}"


@peter_tool(tier="read")
def look_at_image(path: str, question: str = "") -> str:
    """Look at an image file and answer a question about it.

    Args:
        path: The image file, e.g. a screenshot or a photo.
        question: What to look for. Defaults to describing it.
    """
    from peter.llm import vision

    config = services().config
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = (config.data_dir / target).resolve()
    if not target.exists():
        return f"There is no file at {target}."

    try:
        return vision.describe_image(target, question, config)
    except PeterError as exc:
        return exc.spoken() if hasattr(exc, "spoken") else str(exc)
    except Exception as exc:
        return f"I could not read that image: {type(exc).__name__}: {exc}"


@peter_tool(tier="read")
def look_at_browser_page(question: str = "", full_page: bool = False) -> str:
    """Look at the page the browser has open and answer a question about it.

    The fallback for pages that structured extraction cannot answer — a seat
    map, a chart, a captcha-shaped obstacle, a layout question. Prefer
    browse_page first: reading the page's own text is far cheaper.

    Args:
        question: What to look for on the page.
        full_page: True to capture the whole scrollable page rather than just
            what is visible.
    """
    from datetime import datetime

    from peter.llm import vision

    config = services().config
    path = config.screenshot_dir / f"page-{datetime.now():%Y%m%d-%H%M%S}.png"
    try:
        saved = services().browser().screenshot(path, full_page=full_page)
        return vision.describe_image(Path(saved), question, config)
    except PeterError as exc:
        return exc.spoken() if hasattr(exc, "spoken") else str(exc)
    except Exception as exc:
        return f"I could not look at the page: {type(exc).__name__}: {exc}"
