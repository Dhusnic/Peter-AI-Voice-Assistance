"""Browser tools.

**Why these are separate tools rather than one `browse(url, goal)`.**

The permission gate classifies a tool once, at registration. A single browser
tool cannot be classified: reading a product page is a `read`, clicking
"Add to Cart" is a `write`, and clicking "Place your order" is a `spend`. One
tool spanning three tiers would mean either confirming every page view, or
letting a click through unconfirmed. Splitting them keeps each tool honestly
tiered and leaves the gate model intact.

Reading is free. Interacting confirms. Buying does not exist as a tool at all —
and `browser_click` refuses labels that commit money, so it cannot be assembled
out of the pieces either.
"""

from __future__ import annotations

from datetime import datetime

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.errors import IntegrationError
from peter.core.services import services
from peter.integrations.browser.interlock import PurchaseBlocked, guard

register_skill(SkillManifest(
    name="browser", version="1.0.0",
    description="Scripted browser automation — read pages, click, type, log "
                 "in, compare across sites. Never places an order.",
    module=__name__, permissions=("network", "browser"),
    tools=("browse_page", "check_price", "compare_across_sites",
           "browser_status", "take_page_screenshot", "find_on_page",
           "browser_click", "browser_type", "browser_login", "close_browser"),
))


def _check_allowed(url: str) -> str | None:
    """Enforce integrations.browser.allowed_domains, when it is set."""
    from peter.integrations.browser.ratelimit import domain_of

    allowed = services().config.integrations.browser.allowed_domains
    if not allowed:
        return None
    domain = domain_of(url)
    if any(domain == a or domain.endswith("." + a) for a in allowed):
        return None
    return (
        f"{domain or url} is not in the allowed list in config.yml "
        f"(allowed: {', '.join(allowed)})."
    )


# ------------------------------------------------------------------ reading
@peter_tool(tier="read")
def browse_page(url: str) -> str:
    """Open a web page and read it.

    Use this for sites with no API — Blinkit, Zepto, Myntra, Swiggy, Flipkart,
    TNSTC — and when the user asks what a specific page says. For general
    questions, prefer web_search, which is faster and does not open a browser.

    Returns the page's structured product data when it publishes any, plus its
    readable text. Requests to one site are spaced out deliberately, so a call
    may take a few seconds.

    Args:
        url: Full URL. https:// is added if missing.
    """
    blocked = _check_allowed(url)
    if blocked:
        return blocked

    content = services().browser().read_page(url)
    return content.as_prompt(services().config.integrations.browser.max_page_chars)


@peter_tool(tier="read")
def check_price(url: str) -> str:
    """Read the price and stock status of one product page.

    Cheaper and more reliable than browse_page for this: it returns only the
    structured data the site publishes for search engines, not the whole page.
    Use it when the user asks what something costs or whether it is available.

    Args:
        url: The product page URL.
    """
    blocked = _check_allowed(url)
    if blocked:
        return blocked

    content = services().browser().read_page(url)
    product = content.product

    if not product.found:
        return (
            f"No structured product data on {content.title or url}. "
            "Use browse_page to read the page, or take_page_screenshot to look at it."
        )

    lines = [product.spoken()]
    if product.price is None:
        lines.append("The page did not publish a price.")
    if "low confidence" in product.source:
        lines.append(
            "That price was scraped from visible text, so it may be an MRP or an "
            "EMI figure rather than the selling price — say so when reporting it."
        )
    return " ".join(lines)


@peter_tool(tier="read")
def compare_across_sites(urls: str, question: str) -> str:
    """Read several pages at once and compare them.

    Use this for "which of these is cheapest", "is it in stock anywhere",
    "compare the specs on these three". Each page is read by its own small
    background model call, so only the comparison comes back rather than
    several pages of raw text — far cheaper than calling browse_page on each.

    Pages are fetched one after another with the usual spacing between
    requests, so this takes a while. Expect tens of seconds.

    Args:
        urls: The pages to compare, comma-separated. Two to five of them.
        question: What to compare, e.g. "which is cheapest and is it in stock".
    """
    from peter.agent.subagents import compare

    targets = [u.strip() for u in urls.split(",") if u.strip()]
    blocked = [_check_allowed(u) for u in targets]
    refused = [b for b in blocked if b]
    if refused:
        return refused[0]

    return compare(targets, question)


@peter_tool(tier="read")
def browser_status() -> str:
    """Report what page the browser has open, and whether it is running."""
    browser = services().browser()
    if not browser.is_running:
        return "The browser is not open."
    state = browser.state()
    if not state.url or state.url == "about:blank":
        return "The browser is open on a blank page."
    return f"Open on: {state.title or '(untitled)'} — {state.url}"


@peter_tool(tier="read")
def take_page_screenshot(full_page: bool = False) -> str:
    """Save a picture of the current browser page.

    The fallback for when a page publishes no structured data and its text is
    not enough to answer — a rendered seat map, a chart, a layout question.
    Returns the saved file path.

    Args:
        full_page: True to capture the whole scrollable page, not just the
            visible part.
    """
    config = services().config
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = config.screenshot_dir / f"page-{stamp}.png"
    saved = services().browser().screenshot(path, full_page=full_page)
    return f"Screenshot saved to {saved}"


@peter_tool(tier="read")
def find_on_page(description: str) -> str:
    """List the clickable things on the current page matching a description.

    Call this before browser_click so you know exactly what is there, and can
    tell the user what you are about to click.

    Args:
        description: What to look for, e.g. "Add to Cart" or "Sign in".
    """
    matches = services().browser().find_candidates(description)
    if not matches:
        return f"Nothing on this page matches {description!r}."
    return "\n".join(
        f"{i}: [{m['role']}] {m['label']}" for i, m in enumerate(matches)
    )


# ----------------------------------------------------------------- acting
@peter_tool(tier="write")
def browser_click(description: str, index: int = 0) -> str:
    """Click something on the current browser page.

    Anything that commits money is refused outright — placing an order, paying,
    confirming a booking, starting a subscription. That is a hard stop, not a
    confirmation prompt: Indian banking rules require the user to authorise
    payments personally, so Peter stops at the payment screen and hands over.

    Args:
        description: The visible label of what to click, e.g. "Add to Cart".
        index: Which match to use when several share a label. Check with
            find_on_page first.
    """
    browser = services().browser()

    matches = browser.find_candidates(description)
    if not matches:
        return f"Nothing on this page matches {description!r}."
    if index >= len(matches):
        return f"Only {len(matches)} matches for {description!r}."

    label = matches[index]["label"]

    # Guard on what the button actually says, not on what was asked for — a
    # request to click "continue" must still be stopped if the button reads
    # "Continue to payment".
    try:
        guard(label)
        guard(description)
    except PurchaseBlocked as blocked:
        return blocked.spoken()

    clicked = browser.click(description, index=index)
    state = browser.state()
    return f"Clicked {clicked!r}. Now on: {state.title or state.url}"


@peter_tool(tier="write")
def browser_type(field_description: str, text: str, submit: bool = False) -> str:
    """Type into a field on the current browser page.

    Never use this for passwords, card numbers, OTPs, or UPI PINs. If a page
    wants any of those, stop and tell the user to type it themselves.

    Args:
        field_description: The field's label or placeholder, e.g. "Search".
        text: What to type.
        submit: True to press Enter afterwards.
    """
    lowered = field_description.lower()
    if any(word in lowered for word in
           ("password", "passwd", "otp", "cvv", "pin", "card number", "upi")):
        return (
            f"I will not type into {field_description!r}. Credentials and "
            "payment details are yours to enter — the browser window is open."
        )

    services().browser().type_text(field_description, text, submit=submit)
    state = services().browser().state()
    return f"Typed into {field_description}. Now on: {state.title or state.url}"


@peter_tool(tier="write")
def browser_login(url: str) -> str:
    """Open a site in a visible window so the user can log in by hand.

    Peter never handles site passwords. This opens the page and hands over the
    keyboard; the session is then saved in the browser profile and reused, so it
    only needs doing once per site.

    Args:
        url: The site's login page, e.g. "https://www.myntra.com".
    """
    blocked = _check_allowed(url)
    if blocked:
        return blocked

    try:
        opened = services().browser().open_for_login(url)
    except IntegrationError as exc:
        return exc.spoken()
    return (
        f"Opened {opened} in a visible window. Log in there, then tell me when "
        "you are done — the session is saved and reused after this."
    )


@peter_tool(tier="write")
def close_browser() -> str:
    """Shut the browser down and free its memory.

    The session is kept in the profile on disk, so logins survive.
    """
    browser = services().browser()
    if not browser.is_running:
        return "The browser was not open."
    browser.close()
    return "Browser closed."
