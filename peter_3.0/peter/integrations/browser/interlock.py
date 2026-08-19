"""The purchase interlock.

The permission gate classifies a *tool*. It cannot classify a *click*, because
"click the thing labelled X" is `write` when X is "Add to Cart" and `spend` when
X is "Place your order". One tool, two tiers, decided at call time by a string
the model chose.

Rather than weaken the tier model to accommodate that, the browser layer carries
its own interlock: `browser_click` refuses outright to click anything that looks
like it commits money, and says why. There is no override parameter, no
force flag, and no configuration key that turns it off — an interlock with a
bypass is a suggestion.

This is not only a safety measure, it is the shape of reality. RBI's
authentication rules require the user to authorise every payment personally, so
the last step was always going to be theirs. The interlock makes Peter stop at
the point where it would have been stopped anyway, and hand over cleanly.

Matching is deliberately broad and matches on substrings. A false positive costs
one sentence of explanation; a false negative spends your money.
"""

from __future__ import annotations

import re

# Phrases that commit money, or start an irreversible checkout step.
# Ordered roughly by how often they appear on Indian storefronts.
_PURCHASE_PHRASES = (
    "place order",
    "place your order",
    "buy now",
    "buy it now",
    "pay now",
    "pay ",
    "proceed to pay",
    "proceed to payment",
    "make payment",
    "complete payment",
    "confirm order",
    "confirm and pay",
    "confirm booking",
    "confirm payment",
    "checkout",
    "check out",
    "continue to payment",
    "order now",
    "subscribe",
    "start free trial",
    "upgrade",
    "renew",
    "book now",
    "book ticket",
    "reserve now",
    "pay with",
    "upi",
    "netbanking",
    "net banking",
    "credit card",
    "debit card",
    "add card",
    "wallet",
    "cash on delivery",
    "emi",
)

# Words that make an otherwise-scary phrase harmless. "Buy now, pay later" as a
# label on an information panel, "view cart", and so on.
_SAFE_CONTEXT = (
    "view cart",
    "go to cart",
    "your cart",
    "cart (",
    "back to cart",
    "continue shopping",
    "learn more",
    "know more",
    "terms",
    "policy",
    "how it works",
)

_MONEY_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*[0-9]", re.I)


class PurchaseBlocked(Exception):
    """Raised when a click would commit money."""

    def __init__(self, label: str, reason: str):
        super().__init__(f"refused to click {label!r}: {reason}")
        self.label = label
        self.reason = reason

    def spoken(self) -> str:
        return (
            f"I stopped before clicking {self.label!r} — that step commits money, "
            "and Indian banking rules need you to authorise the payment yourself. "
            "The browser is open on that page; take it from there."
        )


def is_purchase_action(label: str) -> tuple[bool, str]:
    """Would clicking this spend money?

    Returns (blocked, reason). Checked against the element's visible label, which
    is what the user would read on the button.
    """
    text = " ".join((label or "").lower().split())
    if not text:
        return (False, "")

    # "Confirm & Pay" must read the same as "Confirm and Pay". Ampersands and
    # punctuation are exactly how a real button label evades a phrase list.
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9₹.,\s]", " ", text)
    text = " ".join(text.split())

    for safe in _SAFE_CONTEXT:
        if safe in text:
            return (False, "")

    for phrase in _PURCHASE_PHRASES:
        if phrase in text:
            return (True, f"the label contains {phrase.strip()!r}")

    # Bare payment verbs, as whole words. This catches labels the phrase
    # list cannot enumerate - "Review & Pay", "Pay!", "Purchase". Word
    # boundaries matter: without them "buy" fires on "buyer reviews".
    if re.search(r"\b(pay|purchase|buy)\b", text):
        return (True, "the label is a bare payment verb")

    # "Pay ₹1,299" style buttons where the verb is separated from the amount.
    if _MONEY_RE.search(text) and re.search(r"\b(pay|order|book|confirm)\b", text):
        return (True, "the label pairs an amount with a payment verb")

    return (False, "")


def guard(label: str) -> None:
    """Raise PurchaseBlocked if this label commits money."""
    blocked, reason = is_purchase_action(label)
    if blocked:
        raise PurchaseBlocked(label, reason)
