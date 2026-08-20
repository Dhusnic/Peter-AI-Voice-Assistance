# Peter 3.0

An always-on voice assistant that runs locally on Windows, with system access,
email, calendar, memory, and a permission gate in front of anything that changes
the world.

Peter 1.0 and 2.0 matched keywords (`if "date" in mytext:`). Peter 3.0 hands the
transcript to Claude with a registry of tools attached and lets the model decide
what to do. Adding a capability is one decorated function, not another `elif`.

```
mic ─▶ openWakeWord ─▶ faster-whisper ─▶  LLM  ─▶ Piper ─▶ speaker
 CLI ────────────────────────────────────▶ │ ◀──────────── Telegram
                              Claude · GPT · Gemini
                                           │
                                     policy gate ──▶ 146 tools
                                           │               │
                                     audit log       ┌─────┴───────────────┐
                                                     │                     │
                                            SQLite memory            integrations
                                            APScheduler       mail · calendar · browser
                                              desktop · dev · phone · docs · weather · news
```

**[→ USER MANUAL](docs/USER_MANUAL.md)** — everything Peter can do, how to
switch each part on, and what to say to use it.
[→ ARCHITECTURE](docs/ARCHITECTURE.md) — how it is built and why.

---

## Configuration: two files, one rule

| File | Holds | In git? |
|---|---|---|
| `config/config.yml` | Everything — models, voice, wake word, policy, integrations | **Yes** |
| `.env` | Secrets only — API keys, passwords, OAuth client secret | **Never** |

The whole behaviour of the system is reviewable in a diff; none of the
credentials are. Every value is validated by a pydantic model at startup, so a
typo in `config.yml` fails immediately with the field name, not three hours
later inside a tool.

Override any config value for one run without editing the file:

```powershell
$env:PETER__APP__LOG_LEVEL = "DEBUG"
$env:PETER__VOICE__TTS__ENGINE = "edge"
```

---

## Setup

**1. Environment** (Python 3.11 — best Windows wheel coverage for the audio stack):

```powershell
cd peter_3.0
& "C:\Program Files\Python311\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set **at least one** of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or
`GEMINI_API_KEY` in `.env`. Which one answers is `agent.provider` in
`config.yml`.

**2. Check it, without touching audio:**

```powershell
.venv\Scripts\python.exe -m pytest tests\ -q
.venv\Scripts\python.exe -m peter.main --health
.venv\Scripts\python.exe -m peter.main --text
```

Text mode is the fastest way to develop — it skips the audio stack entirely and
lets you type at the same agent.

**3. Voice** (optional). Piper is the local default and needs a voice model:

```powershell
mkdir data\voices
curl.exe -L -o data\voices\en_US-lessac-medium.onnx `
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
curl.exe -L -o data\voices\en_US-lessac-medium.onnx.json `
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

That path is already the default in `config.yml`. To skip it entirely, set
`voice.tts.engine: edge` — no local model, but your reply text goes to
Microsoft.

Then pick a microphone and run:

```powershell
.venv\Scripts\python.exe -m peter.main --devices    # note the index
.venv\Scripts\python.exe -m peter.main
```

Say **"hey Jarvis"** — see the wake word section below.

---

## Three LLM providers

Peter runs on Claude, GPT or Gemini. Pick one in `config.yml`:

```yaml
agent:
  provider: anthropic       # anthropic | openai | gemini
  models:
    anthropic: claude-opus-5      # $5 / $25 per Mtok
    openai: gpt-5.6-terra         # $2 / $12
    gemini: gemini-3.5-flash      # $0.30 / $2.50
```

Override for one run, or switch by voice mid-session:

```powershell
.venv\Scripts\python.exe -m peter.main --provider gemini
```

> "Peter, switch to Gemini" · "which model am I talking to?" · "what's this
> session cost so far?"

Switching **restarts the conversation** — the three vendors use incompatible
history formats and translating one mid-conversation is lossy in both
directions. Stored memory and the running cost total carry over, which is what
actually matters.

### How the abstraction works

One shared agentic loop ([peter/llm/loop.py](peter/llm/loop.py)) drives all
three. Each provider implements four methods and keeps its own history in its
own native format — there is deliberately no "universal message format", because
that is lossy in both directions and every vendor then needs conversion code
*plus* workarounds for what the format cannot express.

What the providers genuinely share is narrow: JSON Schema tool definitions, a
tool call, a tool result, and a stop reason. That is all `peter/llm/base.py`
defines.

| | Anthropic | OpenAI | Gemini |
|---|---|---|---|
| Endpoint | `messages.create` | Responses API | `generate_content` |
| System prompt | cached message block | `instructions` | config field |
| Tool schema key | `input_schema` | `parameters` | `parameters_json_schema` |
| Tool arguments | dict | **JSON string** | dict |
| Tool call id | provided | provided | **none — synthesised** |
| Caching | explicit `cache_control` | automatic | explicit |

**Every vendor SDK ships an auto-executing tool runner, and Peter uses none of
them.** All three would run tools without passing through the permission gate,
which is the one layer that exists to stop exactly that. Gemini's automatic
function calling is explicitly disabled for the same reason.

Anthropic's SDK stays a hard dependency even when running on GPT or Gemini: it
does the signature-and-docstring inference that turns a decorated Python
function into JSON Schema, and that output feeds all three. It is the derivation
engine, not the privileged provider.

---

## Email setup

Peter uses **IMAP with an app password**, not the Gmail API. This is deliberate.

Gmail API scopes are classified *restricted*. A personal Google Cloud project in
"Testing" status gets refresh tokens that expire after **7 days**, and escaping
that requires Google's full verification including a third-party security audit.
An app password has no expiry, needs no OAuth dance, and survives reboots.

1. Turn on 2-step verification on your Google account.
2. **myaccount.google.com → Security → App passwords** → create one.
3. Gmail settings → **Forwarding and POP/IMAP** → enable IMAP.
4. Put the address and the 16-character password in `.env`:

```
PETER_MAIL_ADDRESS=you@gmail.com
PETER_MAIL_APP_PASSWORD=abcdefghijklmnop
```

```powershell
.venv\Scripts\python.exe -m peter.main --health   # mail should say "ok"
```

---

## Calendar and Tasks setup

Calendar and Tasks scopes are only *sensitive*, not *restricted*, so they avoid
Gmail's problem — but only if you do step 4.

1. **console.cloud.google.com** → new project.
2. Enable the **Google Calendar API** and **Google Tasks API**.
3. **Credentials → Create credentials → OAuth client ID → Desktop app.** Put the
   client ID and secret in `.env`.
4. **OAuth consent screen → publish to "In Production".** Do not leave it in
   Testing — that is the 7-day token expiry, and it will silently stop your
   calendar working every week. Sensitive scopes need no security audit to
   publish; you will just see an "unverified app" warning once.
5. Authorise:

```powershell
.venv\Scripts\python.exe -m peter.main --google-auth
```

A browser opens. If you see "Google hasn't verified this app", click
**Advanced → Go to Peter (unsafe)**. That warning is expected for a personal
project.

The resulting `data/google_token.json` holds a live refresh token. It is
gitignored and chmod 600 — treat it as a password, not a cache.

---

## Browsing sites that have no API

Blinkit, Zepto, Myntra, Meesho, Swiggy, Zomato and TNSTC publish no developer
API at all. The only way to reach them is a real logged-in browser, so Peter
drives one — a persistent Chromium profile you log into by hand, once per site.

```powershell
.venv\Scripts\python.exe -m playwright install chromium
```

Then ask Peter to `browser_login` for a site, log in yourself in the window that
opens, and the session is reused from then on. **Peter never sees a site
password.**

### Four rules this layer follows

**It reads structured data, not screenshots.** Nearly every product page embeds
a JSON-LD `Product` block with price and availability, because Google Shopping
requires it. Reading what the site already publishes for machines costs about 50
tokens; screenshotting the same page costs about 1,500 and needs vision.
Screenshots are the fallback for when that fails, not the default.

**It waits between requests.** `min_interval_seconds` (default 20) enforces a
gap between hits on one domain. This is the single most effective protection
against getting an account flagged — far more than any fingerprint trick. Do not
lower it to make price checks faster.

**It stops at a bot check.** A CAPTCHA or challenge page raises and pages you.
Peter does not solve it, route it to a solving service, or retry behind a
different fingerprint. That is both an arms race you lose and the point where
"automating my own browsing" becomes "circumventing an access control".

**It cannot buy anything.** There is no order-placing tool, and `browser_click`
refuses any label that commits money — "Place order", "Pay now", "Confirm &
Pay", "Checkout", "Subscribe". That refusal is a hard stop with no override
flag, not a confirmation prompt. Peter takes you to the payment screen and hands
over; RBI rules require you to authorise the payment yourself anyway.

Set `allowed_domains` in `config.yml` to restrict which sites Peter may open at
all. It is the cheapest way to bound the blast radius of a misheard instruction.

### The risk, stated plainly

Automated access breaks the terms of service of every one of these sites. The
account at risk is your real one. The mitigations above make a ban unlikely, not
impossible. If that matters to you, use a secondary account for tracking.

---

## The wake word

openWakeWord ships models for **hey jarvis**, **alexa**, **hey mycroft** and
**hey rhasspy**. There is no "Peter" model, so `config.yml` defaults to
`hey_jarvis`.

To actually say "hey Peter", train a custom model with the Colab notebook in the
[openWakeWord repo](https://github.com/dscripka/openWakeWord) — about an hour
using synthetic speech — then set `voice.wake.model` to the resulting `.onnx`
path.

---

## Permissions

Every tool declares a tier. `config.yml` maps tiers to decisions and lets you
override individual tools.

| Tier | Meaning | Default |
|---|---|---|
| `read` | Observes; changes nothing | runs immediately |
| `write` | Changes this machine, or sends something outward | asks first |
| `spend` | Commits money | never runs — hands off to you |

`spend` exists because of a legal constraint, not a design preference: RBI's
2026 authentication rules require you to authorise every digital payment
personally. Peter can fill a cart and walk checkout to the payment screen; you
tap the OTP. No assistant can do otherwise in India.

When you decline something, Claude receives "the user declined" as a normal tool
result and adapts. It never sees an exception, and it is told not to retry.

Every call — allowed, declined, or handed off — appends a line to
`data/audit.jsonl`, with passwords and OTPs redacted.

**Sending email always confirms.** Unlike a deleted file, a sent email cannot be
recovered.

---

## Commands

```powershell
python -m peter.main                 # voice mode
python -m peter.main --text          # type instead of speaking
python -m peter.main --health        # check every subsystem
python -m peter.main --briefing      # print today's briefing
python -m peter.main --devices       # list audio devices
python -m peter.main --google-auth   # authorise Calendar and Tasks
```

---

## Adding a tool

Write the function, decorate it, add its module to `registry.TOOL_MODULES`:

```python
from peter.agent.registry import peter_tool

@peter_tool(tier="read")
def battery_level() -> str:
    """Report the laptop's current battery percentage."""
    import psutil
    return f"{psutil.sensors_battery().percent:.0f} percent"
```

Three rules:

1. **The docstring is the prompt.** Claude picks tools by reading it. Write it
   for someone who has never seen the code, and document every argument under
   `Args:` — the JSON schema is generated from those lines.
2. **Return a string a person could hear.** Not JSON, not a dict — this text is
   read aloud.
3. **Pick the tier honestly.** If it changes anything, it is `write`.

---

## Layout

| Path | What lives there |
|---|---|
| [config/config.yml](config/config.yml) | All non-secret configuration |
| [peter/core/config.py](peter/core/config.py) | Loads and validates it; owns the secret boundary |
| [peter/core/errors.py](peter/core/errors.py) | Exception hierarchy; `recoverable` drives retries |
| [peter/core/retry.py](peter/core/retry.py) | Backoff that retries only what should be retried |
| [peter/core/logging.py](peter/core/logging.py) | rich/json handlers, credential redaction |
| [peter/core/services.py](peter/core/services.py) | Service container; integrations connect lazily |
| [peter/agent/registry.py](peter/agent/registry.py) | `@peter_tool`, schema generation, the gate hook |
| [peter/agent/brain.py](peter/agent/brain.py) | Memory injection, tool routing, provider switching |
| [peter/llm/base.py](peter/llm/base.py) | The narrow set of concepts all three vendors share |
| [peter/llm/loop.py](peter/llm/loop.py) | One agentic loop, three vendors |
| [peter/llm/providers/](peter/llm/providers/) | Anthropic, OpenAI and Gemini adapters |
| [peter/llm/pricing.py](peter/llm/pricing.py) | Per-model rate card, so cost comparison is evidence-based |
| [peter/agent/prompts.py](peter/agent/prompts.py) | System prompt — **must stay byte-identical between turns** |
| [peter/policy/gate.py](peter/policy/gate.py) | Tier → decision, confirmation, error containment |
| [peter/memory/store.py](peter/memory/store.py) | SQLite + FTS5: facts, preferences, episodes, todos |
| [peter/integrations/mail/](peter/integrations/mail/) | IMAP/SMTP client and the MIME parsing that feeds it |
| [peter/integrations/google/](peter/integrations/google/) | OAuth, Calendar, Tasks |
| [peter/integrations/browser/](peter/integrations/browser/) | Playwright session, extraction, rate limiting, bot detection |
| [.../browser/interlock.py](peter/integrations/browser/interlock.py) | Refuses any click that commits money |
| [peter/briefing.py](peter/briefing.py) | The daily briefing, assembled section by section |
| [peter/tools/](peter/tools/) | The 146 tools themselves, grouped into skills |
| [peter/voice/](peter/voice/) | Mic, wake word, Whisper, TTS |
| [peter/main.py](peter/main.py) | Wiring, CLI, and the turn loop |

---

## Five things that will bite you

**Do not put anything variable in the system prompt.** It is the cached prefix.
A timestamp or session id in there silently voids the cache on every request and
multiplies your bill. Volatile context goes in the user message —
`Brain._build_user_content` is where. `brain.usage.summary()` shows cache hits;
if `cache r` stays at 0 across turns, something is invalidating.

**`pause_turn` is not a finish.** An Anthropic server-side tool can pause
mid-turn. Treating that as "done" produces a silently truncated answer — no
error, no warning. The shared loop re-sends to resume it. If you rewrite that
loop, keep it.

**Scheduler job targets are stored by import path.** Renaming or moving
`fire_reminder` or `deliver_briefing` silently breaks every job already in the
database. They must stay module-level functions at their current paths.

**A browser tool cannot have one permission tier.** Reading a page is a `read`,
clicking is a `write`, and clicking "Place order" is a `spend` — decided at call
time by a string the model chose. That is why these are separate tools rather
than one `browse(url, goal)`, and why the purchase interlock lives in the
browser layer instead of the gate. If you add a browser tool, tier it honestly
and route it through `interlock.guard`.

**Do not try to redact secrets by shape.** A Gmail app password is sixteen random
lowercase letters — structurally identical to four English words. A regex broad
enough to catch one also redacts "wake word detected". The logging filter scrubs
the *actual* secret values instead, which is exact.

---

## Status

**Phase 1** — voice loop, agent core, memory, permission gate, audit log,
Windows system control, reminders/alarms/timers, to-do list, web search.

**Phase 2** — email (read, search, send, archive, star), Google Calendar,
Google Tasks, and a daily spoken briefing that degrades section by section when
something is unreachable.

**Multi-provider** — Claude, GPT and Gemini behind one loop, switchable by
voice, with per-model cost accounting and a persistent spend ledger in rupees.

**Phase 3** — the browser layer: persistent logged-in session, structured-data
extraction, per-domain rate limiting, bot-wall detection, and a purchase
interlock. Read-only browsing and price checks run freely; clicking confirms;
buying is refused.

**Phase 4** — price and stock watchers: standing watches on product pages,
swept in the background, alerting on a target reached, a meaningful drop, or a
return to stock — and never twice for the same price.

**Phase 6** — the phone, two ways. A Telegram bridge carries both directions
(you ask Peter anything from anywhere; every proactive nudge finds you), and an
ADB bridge reads SMS for one-time codes. Both read-only where it matters:
unknown Telegram chats get no reply at all, and there is no send-SMS tool.

**Phase 7** — subagents: comparing a question across several pages fans the
page-reading out to isolated per-page model calls, so the main conversation
sees a comparison rather than five pages of raw text.

**Desktop control** — open installed apps, named sites and Gmail accounts in
your preferred browser, search and open browser bookmarks (with a YouTube
override browser), play/control YouTube via media keys, and open local folders
by name.

**Vision** — Peter can look at your screen, a file, or the current browser page
and answer a question about it. "What's this error?" while pointing at a stack
trace.

**Meetings** — local recording of system audio (what the other people are
saying), transcribed on-device with faster-whisper in the background, then
summarised into decisions and action items and written to memory. Audio never
leaves the machine.

**Development** — git status and commits, GitHub review requests and CI runs
through the `gh` CLI, a build-failure watcher, an end-of-day work log, and a
standup written from real activity rather than recollection. Read-only: there
is no commit, push or checkout tool.

**Documents** — FTS5 index over folders you point at, with incremental
re-indexing, plus answers built from the passages and cited back to the file.

**Workspaces** — save the set of applications you have open and reopen them
later; skips anything already running.

**Proactive features** — morning briefing, meeting-prep nudge, read-only inbox
digest, waiting-on tracker for mail nobody answered, price sweeps, CI failures,
focus-mode timer, and the daily work log. All poll rather than pre-schedule, all
survive restarts, all degrade quietly when a service is unreachable, and all
refuse to repeat themselves.

**Not built** — cart-building hand-off (Phase 5). Peter can walk a checkout to
the payment screen, but RBI two-factor rules mean it can never complete one, and
the scraping needed to build a cart breaks constantly. Deliberately skipped.
