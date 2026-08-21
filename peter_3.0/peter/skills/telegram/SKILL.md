# telegram

Sending something to the user's own phone over Telegram, deliberately — a
separate concept from the automatic proactive-notification bridge
(`peter/integrations/telegram/`, `peter/telegram_bridge.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `send_to_phone` | write | Send a message to every allowed chat. |
| `telegram_status` | read | Whether the bridge is connected, and to which chats. |

## Setup

All three required together, gated in the registry's `_REQUIRES`:
1. `integrations.telegram.enabled: true`.
2. `TELEGRAM_BOT_TOKEN` in `.env` (`secrets.has_telegram`).
3. `integrations.telegram.allowed_chat_ids` non-empty — run
   `python -m peter.main --telegram-setup` to find your own chat id.

`TelegramConfig` also holds `long_poll_seconds` (default 25),
`forward_notifications` (default true — the *separate* automatic mirroring
path, see below), `max_message_chars` (default 3800, under Telegram's 4096
hard limit), `timeout_seconds`.

## Design notes & gotchas

- **This skill is not how proactive nudges reach the phone.** `notify()` in
  `peter/core/notify.py` is the second channel that mirrors reminders,
  meeting prep, the inbox digest, focus completion, price alerts, and CI
  failures — none of those go through `send_to_phone`. This skill exists
  purely for when the user explicitly asks: "send that address to my
  phone," "message me the summary when you're done."
- **An empty `allowed_chat_ids` means *nobody*, not *everybody* — the only
  safe default.** A bot token is effectively a public endpoint; anyone who
  finds the bot's name can message it. `telegram_status` reflects this
  plainly rather than treating an empty list as "not yet restricted."
- **No tool takes an arbitrary chat id.** `send_to_phone` only ever sends to
  the chats already in `allowed_chat_ids` — there is deliberately no
  "message this stranger" capability reachable from a voice assistant.
- **A submodule silently shadowed the client accessor — a real, shipped bug,
  found by running `--telegram-setup` twice, not by reading the code. Worth
  knowing before touching this package's internals again.**
  `peter/integrations/telegram/__init__.py` defines a `client(config)`
  function; the package also had a submodule of the exact same name
  (`client.py`). Python binds an imported submodule onto its parent
  package's namespace under the submodule's own name as a side effect of
  the import system — so the moment `client()`'s own body imported that
  submodule on first use, the package's `client` attribute silently flipped
  from *function* to *module*. The first call still worked (the function
  reference had already been retrieved); every call after raised
  `'module' object is not callable`. Existing tests never caught this
  because they patched `telegram.client` directly with a lambda rather than
  exercising the real import path. Fixed by renaming the submodule to
  `api.py`, removing the name collision entirely. Don't reintroduce a
  same-named submodule inside this package.
- **`getUpdates` is a long poll, not repeated short polling** — a 25-second
  held HTTP request that returns the moment a message arrives, cheaper and
  more responsive than polling every second. Reached with plain `urllib`,
  no new dependency, matching `weather`/`news`'s stdlib-only pattern.
- Confirmations from a remote (Telegram) turn are declined immediately by a
  `RemoteConfirmer`, never left blocking for the full confirmation timeout —
  a `confirm`-tier tool reads the local console/microphone, neither of
  which a phone can reach.

## Future extension ideas

- No per-chat message history or threading — every send is a one-shot,
  context-free message, consistent with `send_to_phone`'s own docstring
  instruction to "keep it self-contained."
- No rich formatting (Markdown/HTML) is used deliberately simple plain
  text — worth revisiting only if a use case needs, say, a clickable link
  formatted distinctly from surrounding text.
