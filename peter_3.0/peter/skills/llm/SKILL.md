# llm

Switching/inspecting the active LLM provider, and the cost ledger
(`peter/llm/`, `peter/spend.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `switch_llm_provider` | write | Change provider (anthropic/openai/gemini) — restarts the conversation, keeps memory and cost. |
| `llm_status` | read | Current provider/model, session cost, smart-routing state, what else is configured. |
| `spend_report` | read | Cost in rupees over N days, per day and per model. |

## Setup

Always registered — no gate in `_REQUIRES`. Needs at least one of
`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` (or `GOOGLE_API_KEY`)
in `.env` for any provider to actually answer; `AgentConfig` holds
`provider` (default `"anthropic"`), `models` (per-vendor model names, one of
which — Gemini — accepts the literal string `"auto"`), `gemini_auto`
(`GeminiAutoConfig`: `light_model`/`heavy_model`/`heavy_word_threshold`),
`gemini_fallbacks`, `cache` (`CacheConfig`), `budget` (`BudgetConfig`:
`daily_inr`, default 0 = disabled; `action`: `warn`|`block`).

## Design notes & gotchas

- **All three vendor SDKs' own auto-executing tool runners are deliberately
  never used.** Anthropic's `tool_runner`, OpenAI's implicit execution,
  Gemini's `automatic_function_calling` would each run tool calls
  themselves, bypassing the Policy Gate entirely. The shared `loop.py` is
  the only thing allowed to decide when a tool actually runs. This is why
  switching providers doesn't change *how* tools execute — only which
  vendor's wire format `loop.py` is translating to/from.
- **Switching provider keeps memory and cost, never conversation history.**
  The three vendors' formats are structurally incompatible (Anthropic
  content blocks vs. OpenAI response items vs. Gemini `Content`/`Part`
  trees) and cannot be translated between mid-conversation.
  `switch_llm_provider`'s docstring instructs saying plainly that the
  conversation is starting fresh, so the user isn't surprised by lost
  context.
- **When `agent.models.gemini == "auto"`, a free regex router picks light vs.
  heavy per turn — no extra API call.** `llm_status` surfaces the last
  routing decision and its reason when this is active. The router checks
  list/reminder bookkeeping *first* as an unconditional cheap-tier escape —
  earlier orderings escalated most ordinary conversation to the expensive
  model on bare words like "why," or escalated "add buy milk to my todo
  list" because "buy" appeared inside the todo text.
- **Same-tier Gemini fallback is a fast hedge, distinct from the outer
  exponential backoff.** `gemini_fallbacks` maps a model to same-*price*
  substitutes tried immediately with zero delay; the backoff in
  `peter/core/retry.py` only engages once every fallback candidate has also
  failed — meaning the whole deployment, not just one model, is down.
  Fallback is sticky per session (`_last_good[base]`), so a session that
  fell back once starts there next turn rather than re-probing the dead
  primary. `llm_status` cannot see this from outside; it's internal to the
  Gemini provider.
- **Cost is stored in USD, displayed in ₹ only at read time** —
  `agent.usd_to_inr_rate` is a manually maintained rate, no live FX feed;
  storing the converted figure would freeze each day's rate into history and
  make different months uncomparable. `spend_report` converts at display
  time from `peter/spend.py`'s ledger, built by *subtracting* cumulative
  usage counters before/after each turn (providers report cumulative, not
  per-call).
- **The daily budget cap has no "drop to the cheap model" option, on
  purpose.** Gemini's `auto` routing already overwrites the model choice
  every turn; a budget-imposed downgrade would silently not apply on the
  one setup most likely to want it. Only `warn` and `block` exist, checked
  *before* a turn — the only moment a cap can actually stop anything, since
  a turn's cost isn't knowable until it's been paid for.
- `llm_status` warns explicitly when the current model isn't in
  `pricing.py`'s price table — the cost total shown is then an
  undercount, stated rather than hidden.

## Future extension ideas

- No tool edits `agent.budget.daily_inr` or `action` at runtime — both are
  config.yml-only; a "raise today's cap" request has no voice path.
- `switch_llm_provider` has no "switch back" shortcut — the model has to be
  named again explicitly even to return to the previous one.
