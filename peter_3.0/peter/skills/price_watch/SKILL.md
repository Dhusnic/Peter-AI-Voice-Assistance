# price_watch

Standing price/stock watches on product pages that speak up on their own
(`peter/price_watch.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `watch_price` | write | Start watching a URL; reads it once immediately for a baseline. |
| `list_price_watches` | read | List everything watched, with the last price seen. |
| `cancel_price_watch` | write | Stop watching something, by number or fuzzy match. |
| `check_watches_now` | read | Sweep every watch immediately instead of waiting. |

## Setup

`integrations.price_watch.enabled` **and** `integrations.browser.enabled` —
both required, gated together in the registry's `_REQUIRES`, since every
watch is read through the `browser` skill's Playwright pipeline. Relevant
`PriceWatchConfig`: `poll_interval_minutes` (default 90), `max_watches`
(default 20), `drop_percent` (announce a fall of at least this even with no
target set, default 5.0), `alert_on_restock` (default true).

## Design notes & gotchas

- **The cheapest large feature in this codebase, because the hard parts
  already existed.** The `browser` skill already proved a product page can
  be read from its own structured data, already spaces requests per domain,
  and the scheduler already survives restarts. What this skill actually adds
  is the watch list and `evaluate()` — a pure function of the stored watch
  plus the fresh reading, testable exhaustively with no browser anywhere
  near the test.
- **Never fires twice for the same price — only a *further* fall is news.**
  `evaluate()` fires on: the target price reached, a fall of at least
  `drop_percent`, or a return to stock — each exactly once per new low/state,
  not on every sweep that happens to still be below target.
- **`watch_price` reads the page once, synchronously, at watch-creation
  time** — so the reply is "watching it, it is ₹X today," not a bare promise
  about the future, and the first background sweep has a real baseline to
  compare against instead of announcing a drop from nothing.
- **Sweeps stay slow on purpose — the fix for a slow sweep is fewer
  watches, not a shorter poll interval.** `check_watches_now`'s own
  docstring says to expect it to take a while: page reads are spaced by
  `integrations.browser.min_interval_seconds` per domain, so several watches
  on one site take minutes by design. That spacing is what keeps the
  account un-flagged — see the `browser` skill's SKILL.md.
- `_listing()` (the shared rendering helper behind `list_price_watches` and
  `check_watches_now`) is a plain function, not a call into the decorated
  tool — the `@peter_tool` decorator returns the SDK's wrapped tool object,
  not something callable like an ordinary Python function.

## Future extension ideas

- No per-watch check frequency — every watch shares one global
  `poll_interval_minutes`. A "check this one more often" request has
  nowhere to go today.
- No price-history chart or trend — only the last price and the best price
  ever seen are kept; a "how has this moved over the month" question isn't
  answerable from stored data alone.
