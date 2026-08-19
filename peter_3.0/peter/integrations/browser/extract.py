"""Turning a rendered page into something an LLM can read cheaply.

**The central bet of this module:** almost every e-commerce product page already
publishes its price, availability and title as machine-readable structured data,
because Google Shopping and rich search results require it. Amazon, Flipkart,
Myntra, Blinkit and Zepto all emit JSON-LD `Product` blocks or OpenGraph
product tags.

That data is put there deliberately, for machines, by the site itself. Reading
it is both cheaper and far more stable than the alternatives:

    JSON-LD          ~50 tokens, survives redesigns, exact values
    readable text    ~1,500 tokens, survives redesigns, needs interpretation
    screenshot       ~1,500 tokens, survives redesigns, needs vision, slow
    CSS selectors    ~0 tokens, breaks on every redesign

So the order is: structured data, then trimmed readable text, and a screenshot
only when a caller explicitly asks for one. The plan originally assumed
screenshots would be the primary channel; they are the fallback instead,
because a price is a number the page is already telling you.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# Currency symbols and codes seen on Indian storefronts.
_PRICE_RE = re.compile(
    r"(?:₹|Rs\.?|INR|\$|USD|£|GBP|€|EUR)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I
)

# Symbol/code -> ISO currency, for prices scraped out of visible text.
_CURRENCY_BY_SYMBOL = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "usd": "USD",
    "£": "GBP", "gbp": "GBP",
    "€": "EUR", "eur": "EUR",
}

# Spoken forms, because this text is read aloud. A TTS engine given "₹1,299"
# says nothing useful for the symbol; "1,299 rupees" is what a person says.
_CURRENCY_SPOKEN = {
    "INR": "rupees", "USD": "dollars", "GBP": "pounds", "EUR": "euros",
}

_IN_STOCK_WORDS = ("instock", "in stock", "available", "limitedavailability")
_OUT_OF_STOCK_WORDS = (
    "outofstock", "out of stock", "sold out", "unavailable",
    "currently unavailable", "notify me", "soldout",
)


def tidy_name(raw: Any) -> str:
    """Collapse the whitespace that <title> and og:title routinely carry."""
    return " ".join(str(raw or "").split())[:200]


@dataclass(slots=True)
class Product:
    """A product as the page itself describes it."""

    name: str = ""
    price: float | None = None
    currency: str = "INR"
    availability: str = ""          # "in stock" | "out of stock" | ""
    brand: str = ""
    rating: float | None = None
    review_count: int | None = None
    image: str = ""
    source: str = ""                # which extractor found it

    @property
    def found(self) -> bool:
        return bool(self.name or self.price is not None)

    def spoken(self) -> str:
        """Phrased for a speech synthesiser: words, not currency symbols.

        A TTS engine reads "₹1,299" as "1,299" at best. It also cannot be
        printed to a cp1252 Windows console without raising.
        """
        parts = []
        if self.name:
            parts.append(self.name if len(self.name) < 80 else self.name[:77] + "...")
        if self.price is not None:
            unit = _CURRENCY_SPOKEN.get(self.currency, self.currency or "rupees")
            parts.append(f"{self.price:,.0f} {unit}")
        if self.availability:
            parts.append(self.availability)
        if self.rating is not None:
            parts.append(f"rated {self.rating}")
        return ", ".join(parts) if parts else "no product details found"


@dataclass(slots=True)
class PageContent:
    """Everything extraction found, in cost order."""

    url: str = ""
    title: str = ""
    product: Product = field(default_factory=Product)
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)

    def as_prompt(self, max_chars: int) -> str:
        """Render for the model. Structured data first — it is the reliable part."""
        blocks = [f"URL: {self.url}", f"Title: {self.title}"]

        if self.product.found:
            blocks.append("")
            blocks.append("Structured product data (published by the site):")
            p = self.product
            if p.name:
                blocks.append(f"  name: {p.name}")
            if p.price is not None:
                blocks.append(f"  price: {p.currency} {p.price:,.2f}")
            if p.availability:
                blocks.append(f"  availability: {p.availability}")
            if p.brand:
                blocks.append(f"  brand: {p.brand}")
            if p.rating is not None:
                blocks.append(f"  rating: {p.rating}" + (
                    f" from {p.review_count} reviews" if p.review_count else ""
                ))
            blocks.append(f"  (source: {p.source})")

        if self.text:
            blocks.append("")
            blocks.append("Page text:")
            blocks.append(self.text[:max_chars])
            if len(self.text) > max_chars:
                blocks.append(f"... trimmed at {max_chars} characters.")

        return "\n".join(blocks)


# ------------------------------------------------------------------ helpers
def parse_price(raw: Any) -> float | None:
    """Prices arrive as '1,299.00', '₹1299', 1299, or None. Normalise all of them."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None

    text = str(raw).strip()
    if not text:
        return None

    match = _PRICE_RE.search(text)
    candidate = match.group(1) if match else text

    cleaned = re.sub(r"[^\d.]", "", candidate.replace(",", ""))
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def normalise_availability(raw: Any) -> str:
    if not raw:
        return ""
    text = str(raw).lower().rsplit("/", 1)[-1]  # schema.org/InStock -> instock
    if any(word in text for word in _OUT_OF_STOCK_WORDS):
        return "out of stock"
    if any(word in text for word in _IN_STOCK_WORDS):
        return "in stock"
    return ""


def clean_text(raw: str, max_chars: int) -> str:
    """Collapse the whitespace that survives innerText extraction."""
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]

    # Drop navigation chaff: single words repeated down a menu column.
    kept: list[str] = []
    for line in lines:
        if not line:
            if kept and kept[-1]:
                kept.append("")
            continue
        kept.append(line)

    text = _BLANK_LINES.sub("\n\n", "\n".join(kept)).strip()
    return text[:max_chars]


# ------------------------------------------------------------- JSON-LD
def _walk_jsonld(node: Any) -> list[dict]:
    """JSON-LD arrives as an object, a list, or an @graph. Flatten it."""
    found: list[dict] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_walk_jsonld(item))
    elif isinstance(node, dict):
        if "@graph" in node:
            found.extend(_walk_jsonld(node["@graph"]))
        found.append(node)
    return found


def _is_product(node: dict) -> bool:
    node_type = node.get("@type", "")
    if isinstance(node_type, list):
        return any("product" in str(t).lower() for t in node_type)
    return "product" in str(node_type).lower()


def from_jsonld(blocks: list[str]) -> Product:
    """Parse `<script type="application/ld+json">` payloads into a Product."""
    for raw in blocks:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        for node in _walk_jsonld(parsed):
            if not _is_product(node):
                continue

            product = Product(source="json-ld")
            product.name = tidy_name(node.get("name"))

            brand = node.get("brand")
            if isinstance(brand, dict):
                product.brand = str(brand.get("name", "") or "")
            elif brand:
                product.brand = str(brand)

            image = node.get("image")
            if isinstance(image, list) and image:
                image = image[0]
            if isinstance(image, str):
                product.image = image

            offers = node.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if isinstance(offers, dict):
                product.price = parse_price(offers.get("price"))
                product.currency = str(
                    offers.get("priceCurrency", "") or "INR"
                ).upper()
                product.availability = normalise_availability(
                    offers.get("availability")
                )

            rating = node.get("aggregateRating")
            if isinstance(rating, dict):
                try:
                    product.rating = float(rating.get("ratingValue"))
                except (TypeError, ValueError):
                    pass
                try:
                    product.review_count = int(
                        rating.get("reviewCount") or rating.get("ratingCount")
                    )
                except (TypeError, ValueError):
                    pass

            if product.found:
                return product

    return Product()


# ------------------------------------------------------------- meta tags
# Meta keys that only ever appear on a real product page.
_PRODUCT_MARKERS = (
    "product:price:amount", "og:price:amount",
    "product:availability", "og:availability",
    "product:retailer_item_id", "product:brand",
)


def from_meta(meta: dict[str, str]) -> Product:
    """OpenGraph and product meta tags — the fallback when JSON-LD is absent.

    Requires an actual product signal, not just a page title. Every page on the
    web has a <title>; treating that alone as a product would have Peter
    confidently announce a blog post as an item with no price.
    """
    price = None
    for key in ("product:price:amount", "og:price:amount", "twitter:data1"):
        price = parse_price(meta.get(key))
        if price is not None:
            break

    is_product = (
        price is not None
        or str(meta.get("og:type", "")).lower() == "product"
        or any(meta.get(marker) for marker in _PRODUCT_MARKERS)
    )
    if not is_product:
        return Product()

    currency = meta.get("product:price:currency") or meta.get("og:price:currency")
    return Product(
        source="meta tags",
        name=tidy_name(meta.get("og:title") or meta.get("title")),
        price=price,
        currency=currency.upper() if currency else "INR",
        image=meta.get("og:image", ""),
        availability=normalise_availability(
            meta.get("product:availability") or meta.get("og:availability")
        ),
    )


# -------------------------------------------------- last resort: page text
def from_text(text: str, title: str = "") -> Product:
    """Scrape a price out of visible text.

    Genuinely a last resort: the first currency-looking number on a product page
    is often a crossed-out MRP, a delivery fee, or an EMI figure. Flagged as
    low-confidence in `source` so the model knows to hedge when speaking it.
    """
    match = _PRICE_RE.search(text or "")
    price = parse_price(match.group(0)) if match else None

    # Take the currency from the symbol that was actually matched. Defaulting
    # to INR made a GBP price on a UK page get announced as rupees.
    currency = "INR"
    if match:
        symbol = match.group(0)[: match.group(0).find(match.group(1))].strip().lower()
        currency = _CURRENCY_BY_SYMBOL.get(symbol, "INR")

    # A page title is not product data. Without a price this found nothing —
    # returning the title alone would let a blog post masquerade as a product.
    if price is None:
        return Product()

    return Product(
        name=tidy_name(title),
        price=price,
        currency=currency,
        source="page text (low confidence)",
        availability=normalise_availability(text[:3000]),
    )


def best_product(
    jsonld_blocks: list[str],
    meta: dict[str, str],
    text: str,
    title: str,
) -> Product:
    """Try each source in order of reliability and return the first that works."""
    for candidate in (
        from_jsonld(jsonld_blocks),
        from_meta(meta),
        from_text(text, title),
    ):
        if candidate.found:
            # A name without a price is worth keeping, but keep looking for a
            # price in the cheaper-to-trust sources before falling back.
            if candidate.price is not None or candidate.source == "page text (low confidence)":
                return candidate
            fallback = candidate
            break
    else:
        return Product()

    # Name found but no price: try to fill the price from a lower source.
    for other in (from_meta(meta), from_text(text, title)):
        if other.price is not None:
            fallback.price = other.price
            fallback.currency = other.currency
            fallback.availability = fallback.availability or other.availability
            fallback.source = f"{fallback.source} + {other.source}"
            break
    return fallback
