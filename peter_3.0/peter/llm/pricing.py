"""Per-model prices, in US dollars per million tokens.

Cost visibility matters more in a multi-provider setup than a single-provider
one, because the whole point of switching providers is usually cost. A running
total that says "$0.31 this session" makes that decision on evidence instead of
vibes.

These are list prices and they go stale. `estimate()` degrades to zero for an
unknown model rather than guessing — a wrong number is worse than no number.
"""

from __future__ import annotations

from peter.llm.base import Usage

# model prefix -> (input, cached input, output) per million tokens
_PRICES: dict[str, tuple[float, float, float]] = {
    # --- Anthropic ---------------------------------------------------------
    # Cache writes cost ~1.25x input; cache reads ~0.1x. Handled in estimate().
    "claude-opus-5": (5.00, 0.50, 25.00),
    "claude-sonnet-5": (3.00, 0.30, 15.00),
    "claude-haiku-4-5": (1.00, 0.10, 5.00),
    "claude-fable-5": (5.00, 0.50, 25.00),
    "claude-opus-4": (5.00, 0.50, 25.00),

    # --- OpenAI ------------------------------------------------------------
    "gpt-5.6-sol": (5.00, 0.50, 30.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.4": (5.00, 0.50, 30.00),

    # --- Google --------------------------------------------------------------
    # Verified against ai.google.dev/gemini-api/docs/pricing. Standard (paid)
    # tier, prompts <= 200k tokens — Gemini 3.1 Pro Preview bills roughly 2x
    # that above 200k, which is not modelled here (rare for a voice turn).
    "gemini-3.7-flash": (0.75, 0.075, 3.75),
    "gemini-3.6-flash": (0.75, 0.075, 3.75),
    "gemini-3.5-flash": (1.50, 0.15, 9.00),
    "gemini-3.1-pro-preview": (2.00, 0.20, 12.00),
}

# Anthropic bills a cache write at a premium over ordinary input.
_CACHE_WRITE_MULTIPLIER = 1.25


def rates(model: str) -> tuple[float, float, float] | None:
    """Longest-prefix match, so dated snapshots resolve to their family."""
    model = (model or "").strip().lower()
    if not model:
        return None
    best: str | None = None
    for prefix in _PRICES:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return _PRICES[best] if best else None


def estimate(model: str, usage: Usage) -> float:
    """Dollars for one response. Returns 0.0 when the model is unknown."""
    found = rates(model)
    if found is None:
        return 0.0
    input_rate, cached_rate, output_rate = found

    return (
        usage.input_tokens * input_rate / 1e6
        + usage.cache_read * cached_rate / 1e6
        + usage.cache_write * input_rate * _CACHE_WRITE_MULTIPLIER / 1e6
        + usage.output_tokens * output_rate / 1e6
    )


def is_known(model: str) -> bool:
    return rates(model) is not None
