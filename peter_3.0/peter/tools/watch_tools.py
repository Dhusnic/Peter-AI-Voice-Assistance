"""Price and stock watch tools.

Reading a price once is `check_price`. These are for the standing question —
"tell me when this gets cheaper" — which is a different thing: it outlives the
conversation, survives a restart, and speaks up on its own.
"""

from __future__ import annotations

from datetime import datetime

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services
from peter.price_watch import _money

register_skill(SkillManifest(
    name="price_watch", version="1.0.0",
    description="Standing price/stock watches that speak up on a drop or restock.",
    module=__name__, permissions=("network", "browser"),
    tools=("watch_price", "list_price_watches", "cancel_price_watch",
           "check_watches_now"),
))


@peter_tool(tier="write")
def watch_price(url: str, target_price: float = 0.0, label: str = "") -> str:
    """Watch a product page and speak up when its price drops.

    Checks periodically in the background and announces a fall to the target,
    a meaningful drop on its own, or a return to stock. Watching the same URL
    again just updates the target.

    Args:
        url: The product page URL.
        target_price: Announce when the price reaches or falls below this.
            0 means no target — announce meaningful drops instead.
        label: What to call it when announcing, e.g. "the 27-inch monitor".
            Defaults to whatever the page calls itself.
    """
    container = services()
    cfg = container.config.integrations.price_watch
    store = container.watches()

    if store.by_url(url) is None and store.count() >= cfg.max_watches:
        return (
            f"Already watching the maximum of {cfg.max_watches} items. Cancel one "
            "first, or raise integrations.price_watch.max_watches in config.yml."
        )

    # Read it once now, so the answer is "watching it, it is ₹X today" rather
    # than a promise about the future — and so the first real sweep has a
    # baseline to compare against instead of announcing a drop from nothing.
    product = None
    try:
        product = container.browser().read_page(url).product
    except Exception:
        pass

    if not label and product is not None and product.name:
        label = product.name

    watch = store.add(url, label=label, target_price=target_price or None)
    if product is not None and product.found:
        store.record_check(
            watch.id, product.price, product.currency, product.availability,
            alerted=False,
        )

    named = watch.label or url
    parts = [f"Watching {named}."]
    if product is not None and product.price is not None:
        parts.append(f"It is {_money(product.price, product.currency)} right now.")
    if target_price:
        parts.append(
            f"I will tell you when it reaches "
            f"{_money(target_price, product.currency if product else 'INR')}."
        )
    else:
        parts.append(
            f"I will tell you if it drops by {cfg.drop_percent:g}% or more."
        )
    parts.append(f"Checking every {cfg.poll_interval_minutes} minutes.")
    return " ".join(parts)


@peter_tool(tier="read")
def list_price_watches() -> str:
    """List everything currently being watched, with the last price seen."""
    return _listing()


def _listing() -> str:
    """The rendered watch list.

    A plain function rather than calling the tool above: the decorator returns
    the SDK's wrapped tool object, so tools are not callable from Python the
    way an ordinary function is.
    """
    watches = services().watches().all()
    if not watches:
        return "Nothing is being watched right now."

    lines = []
    for watch in watches:
        bits = [f"{watch.id}. {watch.name}"]
        if watch.last_price is not None:
            bits.append(f"now {_money(watch.last_price, watch.currency)}")
        if watch.target_price:
            bits.append(f"target {_money(watch.target_price, watch.currency)}")
        if watch.best_price is not None and watch.best_price != watch.last_price:
            bits.append(f"lowest seen {_money(watch.best_price, watch.currency)}")
        if watch.last_availability:
            bits.append(watch.last_availability)
        if watch.checked_at:
            bits.append(
                "checked " + datetime.fromtimestamp(watch.checked_at).strftime("%d %b %H:%M")
            )
        elif watch.failures:
            bits.append(f"{watch.failures} failed check(s)")
        lines.append(" — ".join(bits))
    return "\n".join(lines)


@peter_tool(tier="write")
def cancel_price_watch(which: str) -> str:
    """Stop watching something.

    Args:
        which: The watch's number from list_price_watches, or part of its
            label or URL.
    """
    store = services().watches()
    needle = which.strip()

    if needle.isdigit():
        watch = store.get(int(needle))
        if watch is None:
            return f"There is no watch number {needle}."
        store.delete(watch.id)
        return f"Stopped watching {watch.name}."

    matches = store.find(needle)
    if not matches:
        return f"Nothing being watched matches {needle!r}."
    if len(matches) > 1:
        listing = ", ".join(f"{m.id}. {m.name}" for m in matches)
        return f"That matches several: {listing}. Which number?"

    store.delete(matches[0].id)
    return f"Stopped watching {matches[0].name}."


@peter_tool(tier="read")
def check_watches_now() -> str:
    """Check every watched item immediately instead of waiting for the sweep.

    Slow by design — page reads are spaced out to keep the accounts involved
    un-flagged — so expect it to take a while with several items on one site.
    """
    from peter.price_watch import check_price_watches

    store = services().watches()
    if not store.all():
        return "Nothing is being watched right now."

    check_price_watches()
    return "Checked everything.\n" + _listing()
