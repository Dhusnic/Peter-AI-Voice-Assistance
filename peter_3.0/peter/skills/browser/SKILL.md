# browser

Scripted browser automation, via a persistent, logged-in Playwright profile
(`peter/integrations/browser/`) — for sites with no API at all (Blinkit,
Zepto, Myntra, Meesho, Swiggy, Zomato, most of Flipkart).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `browse_page` | read | Open and read a page — structured product data first, full text as fallback. |
| `check_price` | read | Read just the price/stock status of one product page (cheaper than `browse_page`). |
| `compare_across_sites` | read | Read 2–5 pages via parallel subagent fan-out, return the comparison only. |
| `browser_status` | read | What page is open, and whether the browser is even running. |
| `take_page_screenshot` | read | Save a picture of the current page (last-resort fallback). |
| `find_on_page` | read | List the clickable things matching a description — call before `browser_click`. |
| `browser_click` | write | Click something — hard-refuses anything that commits money. |
| `browser_type` | write | Type into a field — refuses passwords/OTP/PIN/card-number fields outright. |
| `browser_login` | write | Open a site in a visible window for the user to log in by hand; session then reused. |
| `close_browser` | write | Shut the browser down, freeing memory (profile/logins persist on disk). |

## Setup

`integrations.browser.enabled` (default true) — **not gated in the
registry's `_REQUIRES` at all**, and deliberately so: per `usable_modules()`'s
own docstring, browser tools stay registered even with no saved login,
because they genuinely work on public pages — dropping a tool that can
succeed would cost accuracy to save tokens, the wrong trade here. Relevant
`BrowserConfig` fields: `engine` (`chromium`|`firefox`, default `chromium`),
`headless` (default **false** — deliberately headed, see below),
`profile_dir`, `default_timeout_seconds`, `min_interval_seconds` (default
20s per-domain — "do not lower this"), `max_page_chars`, `allowed_domains`
(empty = no restriction).

## Design notes & gotchas

- **Why nine tools instead of one `browse(url, goal)`.** The permission gate
  classifies a tool once, at registration — it cannot classify a single tool
  that spans tiers. Reading a page is `read`, clicking "Add to Cart" is
  `write`, and "place your order" isn't a *tier* at all, it's structurally
  unreachable (see below). Splitting keeps every tool honestly tiered
  instead of either confirming every page view or letting a purchase click
  through unconfirmed.
- **The purchase interlock has no bypass parameter, anywhere, on purpose.**
  `browser_click` calls `guard()` against both the requested label *and* the
  actual button text found — a request to click "continue" is still stopped
  if the real button reads "Continue to payment". `guard()` takes only a
  label to check; a dedicated test inspects the function's *signature* to
  assert no override argument was ever added back in by accident. This is
  the code-level enforcement of RBI's mandatory 2FA rule (§1.3 in
  ARCHITECTURE.md) — even if the policy gate were somehow bypassed, this
  layer still can't complete a payment. Do not add an `override`/`force`
  parameter to `guard()` or `browser_click` under any circumstance.
- **`browser_type` refuses credential/payment fields by keyword match on the
  field description** ("password", "otp", "cvv", "pin", "card number",
  "upi") before ever calling into Playwright — Peter never handles site
  passwords or payment details; the browser window stays open for the user
  to type those themselves.
- **Structured data first, screenshots last.** Almost every commerce page
  publishes JSON-LD/OpenGraph product data for Google Shopping — reading
  that costs ~50 tokens vs. ~1,500 for a screenshot. `check_price` reads
  only that structured data and is cheaper/more reliable than `browse_page`
  for a price/stock question; `take_page_screenshot` (and the `vision`
  skill's `look_at_browser_page`) are the fallback when structured data is
  genuinely absent, not the default path.
- **Per-domain rate limiting (`min_interval_seconds`) is the primary
  anti-ban strategy** — more effective in practice than trying to
  out-fingerprint commercial bot detection (Akamai/PerimeterX/Cloudflare). A
  persistent, real, headed browser profile with genuine cookies plus
  human-paced polling avoids most of what would otherwise get an account
  suspended. This is why `headless` defaults to `false`: a headless browser
  is the loudest automation signal there is, and being headed also lets the
  user see and stop what it's doing.
- **A bot-wall (CAPTCHA, block page) is a stop signal, never a puzzle** —
  `detect.py` exists to *recognize* that state and hand off to the user, not
  to solve or evade it.
- **This is always a separate, Peter-owned browser instance with its own
  profile** — never the user's actual installed Chrome/Firefox/Edge, and
  unrelated to `desktop.preferred_browser` in the `desktop` skill, which
  only ever opens plain links for the user to look at. `check_price`/
  `browse_page`/`compare_across_sites` power the `price_watch` and `vision`
  (`look_at_browser_page`) skills underneath — all four share this one
  browser/profile.
- Switching `engine` after the profile directory already has data fails to
  launch — a Chromium user-data directory and a Firefox profile are
  structurally incompatible formats; delete or repoint `profile_dir` after
  changing engines.
- `compare_across_sites` fans out reading-and-extracting across parallel,
  isolated, tool-free model calls (see `peter/agent/subagents.py`) — but the
  page *fetches* themselves stay serial, since there is one browser and
  per-domain rate limiting is deliberate. The parallelism is in the
  extraction, not the network.

## Future extension ideas

- No cart-building hand-off exists, and per §1.3 of ARCHITECTURE.md it was
  deliberately dropped as a phase — the remaining value after "cannot
  complete a purchase" is a cart built by flows that break constantly and
  risk the account, not worth it.
- `allowed_domains` is a flat list with no wildcard/glob support — fine at
  the current scale, would need revisiting if the list grows large.
