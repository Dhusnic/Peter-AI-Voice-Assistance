# Peter 3.0 — User Manual

Everything Peter can do, how to switch each part on, and what to say to use it.

This is the **operator's** manual: what to type, what to expect back, and what
to do when something is not working. For *how it is built*, see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Contents

1. [Start here — five minutes to a working Peter](#1-start-here)
2. [Running Peter](#2-running-peter)
3. [The two configuration files](#3-the-two-configuration-files)
4. [What Peter can do — by capability](#4-what-peter-can-do)
   - [4.1 Your machine](#41-your-machine)
   - [4.2 Time, reminders and to-dos](#42-time-reminders-and-to-dos)
   - [4.3 Focus mode](#43-focus-mode)
   - [4.4 Memory](#44-memory)
   - [4.5 Email](#45-email)
   - [4.6 Calendar and tasks](#46-calendar-and-tasks)
   - [4.7 The web and shopping](#47-the-web-and-shopping)
   - [4.8 Price watches](#48-price-watches)
   - [4.9 Looking at your screen](#49-looking-at-your-screen)
   - [4.10 Meetings — recording and notes](#410-meetings--recording-and-notes)
   - [4.11 Development — git, PRs, CI, standup](#411-development)
   - [4.12 Your documents](#412-your-documents)
   - [4.13 Workspaces](#413-workspaces)
   - [4.14 Your phone — Telegram](#414-your-phone--telegram)
   - [4.15 Your phone — SMS over ADB](#415-your-phone--sms-over-adb)
   - [4.16 Cost and models](#416-cost-and-models)
5. [What Peter does without being asked](#5-what-peter-does-without-being-asked)
6. [Permissions — what stops and asks](#6-permissions)
7. [Setup guides for each integration](#7-setup-guides)
8. [Command-line reference](#8-command-line-reference)
9. [Complete tool reference](#9-complete-tool-reference)
10. [Troubleshooting](#10-troubleshooting)
11. [Privacy — what leaves this machine](#11-privacy)

---

<a name="1-start-here"></a>
## 1. Start here

You need **one** LLM API key. Everything else is optional and can be added later.

```powershell
cd D:\Peter-AI-Voice-Assistance\peter_3.0

# 1. Dependencies (once)
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Secrets
copy .env.example .env
notepad .env          # fill in ONE of the three API keys

# 3. Check it
.venv\Scripts\python.exe -m peter.main --health

# 4. Run it
.venv\Scripts\python.exe -m peter.main --text
```

Then type: `what time is it`

> **The single most common problem** is running `python` instead of
> `.venv\Scripts\python.exe`. The system Python does not have Peter's
> dependencies installed and you will get `ModuleNotFoundError`. Either use the
> full path every time, or activate the venv first with
> `.venv\Scripts\Activate.ps1`.

**What to turn on next**, in order of payoff:

| Order | Feature | Effort | Why first |
|---|---|---|---|
| 1 | [Telegram](#414-your-phone--telegram) | 3 min | Makes every other feature reach you when you are away from the desk |
| 2 | [Email](#45-email) | 5 min | Unlocks the inbox digest, waiting-on tracker, and the morning briefing |
| 3 | [Dev tools](#411-development) | 1 min | Just point at your repos; `gh` is optional |
| 4 | [Calendar](#46-calendar-and-tasks) | 10 min | Unlocks meeting-prep nudges |
| 5 | [Documents](#412-your-documents) | 1 min | Point at a notes folder |

---

<a name="2-running-peter"></a>
## 2. Running Peter

Peter has two modes. **Same brain, same tools, same memory, same permissions** —
only the input and output differ.

```powershell
.venv\Scripts\python.exe -m peter.main --text     # type, read replies
.venv\Scripts\python.exe -m peter.main --voice    # wake word, mic, spoken replies
.venv\Scripts\python.exe -m peter.main            # asks which one
```

**Text mode** shows a live status line telling you what Peter is doing right
now — not just "thinking", but "Reading D:/notes/spec.md" or "Searching your
email". Replies come back in a panel.

**Voice mode** listens for the wake word (`hey jarvis` by default — see
[§10](#10-troubleshooting) for why it is not "hey Peter"), transcribes what you
say, and speaks the answer. Saying the wake word while Peter is talking cuts
him off and starts listening — that is deliberate.

Quit with `Ctrl-C`, or by typing `quit` in text mode.

---

<a name="3-the-two-configuration-files"></a>
## 3. The two configuration files

There is one rule and it never bends:

| File | Contains | In git? |
|---|---|---|
| `config/config.yml` | Everything that is not a secret | **Yes** |
| `.env` | API keys, passwords, tokens | **Never** |

That split means the whole behaviour of the system is reviewable in a diff,
while nothing sensitive is.

**Changing one setting for one run** — without editing either file — is done
with an environment variable spelling out the config path:

```powershell
$env:PETER__APP__LOG_LEVEL = "DEBUG"
$env:PETER__AGENT__PROVIDER = "gemini"
.venv\Scripts\python.exe -m peter.main --text
```

Every value in `config.yml` is validated at startup. A typo fails immediately
with a message naming the field, rather than three hours later inside a tool.

---

<a name="4-what-peter-can-do"></a>
## 4. What Peter can do

110 tools across 16 areas. You never name a tool — you say what you want and
Peter picks. The examples below are things you can say verbatim.

<a name="41-your-machine"></a>
### 4.1 Your machine

> "open notepad"
> "what's using my memory?"
> "read D:/notes/todo.md"
> "find every file with 'invoice' in the name under Documents"
> "copy that to the clipboard"
> "take a screenshot"
> "lock the computer"
> "turn the volume down to 20"
> "open my downloads folder"
> "open YouTube and play lo-fi beats"
> "search my bookmarks for the OpenObserve dashboard"

Browsers are configurable: everything opens in Firefox by default, **except
YouTube, which opens in Brave** so video does not pile up in the browser you
work in. Both are set under `integrations.desktop` in `config.yml`.

`run_powershell` exists and is the escape hatch that makes "full system access"
real. It always asks first, and every invocation is logged verbatim.

<a name="42-time-reminders-and-to-dos"></a>
### 4.2 Time, reminders and to-dos

> "remind me in 20 minutes to check the deploy"
> "set an alarm for 6:30 tomorrow"
> "set a 10 minute timer"
> "what reminders do I have?"
> "add 'write the migration plan' to my to-do list"
> "what's on my list?"
> "mark the migration plan done"

Reminders survive a restart. They are stored in SQLite, not in memory, so a
reminder set at 11pm still fires at 7am even if the machine rebooted in
between. A reminder missed because the laptop was asleep still fires when it
wakes, within an hour.

<a name="43-focus-mode"></a>
### 4.3 Focus mode

> "start a 90-minute focus session on the migration"
> "how long is left?"
> "end the focus session"

Peter mutes the system volume, starts a timer, and when it ends puts the volume
back exactly where it was and tells you how long you actually worked. The
session is recorded in memory, so it shows up in your work log and standup.

**The restore is bulletproof by design.** The volume to restore to is baked into
the scheduled job itself, so even if Peter is restarted mid-session, the restore
still fires with the right value. The volume never stays muted.

<a name="44-memory"></a>
### 4.4 Memory

> "remember that my Azure subscription id is 1234-5678"
> "remember I prefer short answers"
> "what do you know about my college?"
> "forget that"

Three kinds, used differently:

- **Facts** — durable statements about your world. Searched, and only the
  relevant ones are injected into a turn.
- **Preferences** — how Peter should behave. Always injected.
- **Episodes** — rolling summaries. Written automatically by focus sessions,
  meeting notes, the daily work log, and by conversations ageing out of context.

<a name="45-email"></a>
### 4.5 Email

*Requires [email setup](#72-email).*

> "any new email?"
> "read the one from Priya"
> "search my email for the Azure invoice"
> "archive that"
> "star it"
> "send Priya an email saying I'll review the PR tomorrow"
> **"what needs a reply?"**
> **"what am I waiting on?"**

The last two are the interesting ones.

**Inbox digest** (`what needs a reply?`) separates "this needs you" from routine
mail using one small model call over sender and subject lines only — never
bodies. Read-only: it reports, it never drafts or sends.

**Waiting-on** (`what am I waiting on?`) is the counterpart, and covers the
thing that actually falls through the cracks: mail *you* sent that nobody
answered. Nothing in a mail client shows you an absence.

> It is a heuristic — replies are matched by subject line. A reply whose subject
> was rewritten will be missed, and an unrelated message sharing a subject
> counts as a reply. Fine for a nudge; it never acts on the result.

Sending email always asks for confirmation first. That one cannot be unsent.

<a name="46-calendar-and-tasks"></a>
### 4.6 Calendar and tasks

*Requires [Google setup](#73-calendar-and-tasks).*

> "what's on today?"
> "what's my next meeting?"
> "book a 30-minute call with the platform team tomorrow at 3"
> "add 'renew the domain' to my Google tasks"
> "what's on my briefing schedule?"

Deleting a calendar event asks first.

<a name="47-the-web-and-shopping"></a>
### 4.7 The web and shopping

> "what's the weather in Chennai?" *(web search — fast, no browser)*
> "what does this page say: <url>" *(opens a real browser)*
> "how much is the Dell U2723QE on Amazon?"
> "is it in stock on Flipkart?"
> **"compare these three and tell me which is cheapest: <url>, <url>, <url>"**

Peter drives a real, headed browser using your own logged-in sessions for sites
with no API — Blinkit, Zepto, Myntra, Swiggy, Flipkart, TNSTC. It reads the
structured product data those pages already publish for Google Shopping, which
is both cheaper and far more stable than scraping their HTML.

**Comparing several sites** fans out: each page gets its own small isolated
model call that answers your question about that page alone, and only the short
findings come back. Without that, five pages would put ~7,500 tokens of raw
page text into your conversation history — re-sent on every subsequent turn.

Requests to one site are spaced ~20 seconds apart on purpose. That spacing is
the main thing keeping your accounts un-flagged. **Do not lower it to make
things faster.**

> **Peter cannot buy anything.** Not "will not" — cannot. Clicking anything that
> commits money is refused outright by the browser layer, with no override.
> Indian regulation (RBI two-factor authentication, mandatory since April 2026)
> requires you to authorise payments personally. Peter builds the cart and hands
> you the payment screen.

<a name="48-price-watches"></a>
### 4.8 Price watches

> "watch this and tell me when it drops below 20,000: <url>"
> "watch the monitor for me"
> "what am I watching?"
> "stop watching the keyboard"
> "check all my watches now"

Peter checks in the background and speaks up when:

- the price reaches a **target** you set,
- it falls by **5% or more** on its own (configurable), or
- something you were waiting for **comes back in stock**.

It never announces the same price twice — only a *further* fall is news. That
rule is the whole feature; a watcher that mentions every one-rupee wobble gets
muted within a day.

A sweep of several watches on one site takes minutes, because of the request
spacing above. That is expected.

<a name="49-looking-at-your-screen"></a>
### 4.9 Looking at your screen

> **"what's this error?"**
> "read me that number"
> "is this the right branch?"
> "what does this dialog say?"
> "look at this image: D:/screenshots/thing.png"

Peter captures the screen, sizes it down, and actually reads it. This is the
one that saves the most typing day to day — point at a stack trace and ask.

If a multi-monitor grab makes the text too small, say "look at just my main
screen".

<a name="410-meetings--recording-and-notes"></a>
### 4.10 Meetings — recording and notes

> "start recording the sprint planning"
> "stop recording"
> "read me the notes from sprint planning"
> "what recordings do I have?"
> "what would recording capture?" *(diagnostic)*

Peter records **what your speakers are playing** — i.e. the other people on the
call — using Windows loopback capture. No virtual audio cable needed. If the
installed audio stack cannot do loopback it falls back to the microphone and
**tells you**, because "captures your side only" is a meaningful difference.

When you stop, the tool returns immediately and transcription runs in the
background — an hour of audio takes minutes, and a tool call that blocks for
minutes is a hang, not a tool. When it is done Peter speaks the summary, pushes
it to your phone, saves `.txt` (transcript) and `.md` (notes) next to the audio,
and writes it into memory.

That last part is what makes meeting-prep nudges good weeks later: *"your last
conversation with Priya was about the alerting thresholds."*

**Everything here is local.** The audio never leaves the machine —
faster-whisper transcribes on your CPU. Only the final text summary is a model
call.

> Recording only ever happens when you ask. There is an
> `auto_record_meetings` option that starts a recording when a meeting-prep
> nudge fires; it is **off by default** and stays that way unless you turn it
> on deliberately.

<a name="411-development"></a>
### 4.11 Development

*Requires `integrations.dev.repos` in `config.yml`. `gh` is optional but unlocks
PRs and CI.*

> "what's the state of the peter repo?"
> "what did I commit today?"
> **"what's waiting on me?"**
> "are the builds green?"
> **"write my standup"**
> "what did I get done this week?"

`what's waiting on me?` covers **every** repo your GitHub account can see, not
just the configured ones — review requests plus your own open PRs.

`write my standup` is built from real activity: commits, calendar, focus
sessions, finished to-dos. The model is given the facts and told not to invent
any. It is not asked to remember your week.

Peter also announces a **build that breaks**, once per run. The first sweep
after starting only records what is already failing without shouting about
history you already know about.

> **All read-only.** There is no commit, push, merge or checkout tool, on
> purpose. An assistant that rewrites your working tree on a misheard sentence
> is a liability, and the upside — saving you from typing `git commit` — is not
> worth it.

<a name="412-your-documents"></a>
### 4.12 Your documents

> "index D:/notes"
> "search my documents for the alerting thresholds"
> **"what did we agree the retry budget was?"**
> "what's indexed?"

Full-text search over folders you point Peter at, and answers built from them
**with citations**. If the documents do not answer, Peter says so rather than
filling the gap from general knowledge.

Indexing is incremental — a file unchanged since last time is skipped without
being read, so re-indexing a large tree after editing two files costs two
files' work. Folders listed in `integrations.docs.folders` are indexed at
startup, in the background.

Lives in its own database (`data/docs.db`), so you can delete and rebuild it
without touching memory.

<a name="413-workspaces"></a>
### 4.13 Workspaces

> "save this as my migration workspace"
> "restore my migration workspace"
> "what workspaces do I have?"

Captures the applications you actually have open (visible windows only — not
sixty background services) and reopens them later. Anything already running is
left alone rather than opened twice.

<a name="414-your-phone--telegram"></a>
### 4.14 Your phone — Telegram

*[Setup: 3 minutes](#71-telegram).*

This is the highest-leverage thing in this manual, because it changes every
other feature: a tray toast only exists if you are sitting in front of the
machine. A Telegram message finds you.

**Two directions:**

1. **You → Peter.** Message the bot from anywhere. Same brain, same tools, same
   memory. "what's on my calendar tomorrow", "what needs a reply", "remind me
   to call the bank at 4".
2. **Peter → you.** Every proactive announcement is mirrored to your phone:
   reminders, meeting prep, the inbox digest, finished focus sessions, price
   alerts, CI failures, meeting notes.

You can also send things deliberately: *"send that address to my phone"*.

**Security, stated plainly:**

- A bot token is effectively a public endpoint — anyone who finds your bot's
  name can message it. The `allowed_chat_ids` list is what stops them.
- **An unknown chat gets no reply at all.** Not even "you are not authorised" —
  that would confirm the bot is alive and worth attacking.
- Messages queued while Peter was off are **discarded** at startup, not
  executed. Otherwise everything sent overnight would fire in a burst.
- Anything that would normally stop and ask for confirmation is **declined**
  when the request came in remotely, with an explanation, rather than hanging
  on a console prompt nobody is standing at. Destructive things stay at the
  desk.

<a name="415-your-phone--sms-over-adb"></a>
### 4.15 Your phone — SMS over ADB

*Off by default. [Setup](#75-phone-sms-over-adb).*

> "read my messages"
> **"what's the code?"**
> "is my phone charging?"

The one that earns its keep is `what's the code?`. Peter can walk a checkout
right up to the payment screen but cannot legally complete it — the OTP is
yours to enter. Reading it aloud so you can type it is exactly as far as
automation should reach into that.

Peter reads the code **digit by digit** ("1 2 3 4 5 6"), because a speech engine
given "123456" says "one hundred and twenty-three thousand…".

Read-only. There is no send-SMS tool: sending a message as you is both
technically unreliable across Android versions and a bad idea for something
driven by speech recognition.

<a name="416-cost-and-models"></a>
### 4.16 Cost and models

> "which model are you using?"
> "switch to Gemini"
> **"how much have I spent this week?"**
> "what did today cost?"

Every turn's cost is recorded and kept. `spend_report` totals it in **rupees**,
broken down by day and by model — which is the only honest way to answer "is
Gemini actually cheaper for my work".

> Costs are *stored* in USD (the unit every vendor bills in) and converted only
> when you read them. Storing rupees would freeze each day's exchange rate into
> history and make last month's numbers uncomparable. Keep
> `agent.usd_to_inr_rate` roughly current.

**A daily cap** is available and off by default:

```yaml
agent:
  budget:
    daily_inr: 200
    action: warn      # or: block
```

Look at a week of real numbers before setting one. A budget that trips
mid-sentence is worse than no budget.

---

<a name="5-what-peter-does-without-being-asked"></a>
## 5. What Peter does without being asked

Everything here is scheduled, survives restarts, and can be switched off
individually in `config.yml`. All of it degrades quietly — a poll that cannot
reach its service tries again next time rather than crashing.

| When | What | Turn off with |
|---|---|---|
| 07:30 daily | **Morning briefing** — calendar, unread mail, reminders, to-dos | `integrations.briefing.enabled` |
| Every 5 min | **Meeting prep** — "you have X in 10 minutes, it's with Y and Z, your last related note was…" | `integrations.meeting_prep.enabled` |
| Every 60 min | **Inbox digest** — "23 unread, 3 look like they need a response" | `integrations.inbox_digest.enabled` |
| Every 90 min | **Price sweep** — target reached, meaningful drop, or back in stock | `integrations.price_watch.enabled` |
| Every 10 min | **CI watch** — a build that just broke | `integrations.dev.ci_watch.enabled` |
| 18:30 daily | **Work log** — the day's commits, meetings, focus sessions and finished to-dos, written to memory | `integrations.worklog.enabled` |
| On the timer | **Focus session ends** — volume restored, summary spoken | — |
| When ready | **Meeting notes** — transcription finished in the background | — |

Two design rules run through all of them:

- **Never repeat yourself.** The meeting nudge fires once per event; the inbox
  digest only speaks when the count changed; a price is announced once; a broken
  build is announced once.
- **Never shout about history.** The CI watcher's first sweep after startup
  records what is already failing without announcing it.

You can add two more sections to the morning briefing if you want them —
they cost extra network calls, so they are opt-in:

```yaml
integrations:
  briefing:
    include: [calendar, mail, reminders, todos, waiting_on, pull_requests]
```

---

<a name="6-permissions"></a>
## 6. Permissions

Every tool call passes through a gate before it runs. Tools carry a tier:

| Tier | Meaning | Default |
|---|---|---|
| `read` | Observes; changes nothing | runs |
| `write` | Changes something on this machine, or sends something outward | **runs** |
| `spend` | Commits money | **never runs** — hands off to you |

`write` runs without asking, because most write-tier tools — opening an app,
playing a video, creating a calendar event, saving a memory — are cheap and
reversible, and a `y/N` prompt on every one is just friction.

**These six always ask**, and are listed explicitly in `config.yml`:

```
delete_file · delete_email · delete_calendar_event
run_powershell · lock_workstation · send_email
```

Destroy data, run arbitrary commands, or send something to another person that
cannot be unsent. Change that list if you want, understanding the cost.

A refusal is not an error. Peter is told "the user declined", and adapts —
apologises, offers an alternative, asks what you would prefer.

**Everything is audited.** `data/audit.jsonl` gets one JSON line per tool call:
timestamp, tool, arguments, tier, decision, result summary, duration. It is the
only forensic trail when Peter does something surprising.

---

<a name="7-setup-guides"></a>
## 7. Setup guides

<a name="71-telegram"></a>
### 7.1 Telegram — 3 minutes

1. Open Telegram, message **@BotFather**, send `/newbot`, follow the prompts.
2. Copy the token into `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   ```
3. Find your chat id:
   ```powershell
   .venv\Scripts\python.exe -m peter.main --telegram-setup
   ```
   It waits up to 60 seconds. Send your bot any message and it prints the id.
4. Paste it into `config/config.yml`:
   ```yaml
   integrations:
     telegram:
       allowed_chat_ids:
         - 123456789
   ```
5. Restart Peter. `--health` should show `telegram  ok - @yourbot`.

<a name="72-email"></a>
### 7.2 Email — 5 minutes

Peter uses **IMAP with an app password**, not the Gmail API. That is deliberate:
Gmail API scopes are "restricted", which caps personal projects at a refresh
token that **expires every 7 days**. An app password does not expire.

1. Turn on 2-step verification on your Google account.
2. Go to **myaccount.google.com → Security → App passwords**, generate one.
3. Enable IMAP in Gmail settings → *Forwarding and POP/IMAP*.
4. Put both in `.env`:
   ```
   PETER_MAIL_ADDRESS=you@gmail.com
   PETER_MAIL_APP_PASSWORD=abcd efgh ijkl mnop
   ```

<a name="73-calendar-and-tasks"></a>
### 7.3 Calendar and tasks — 10 minutes

1. **console.cloud.google.com** → APIs & Services → Credentials →
   Create credentials → OAuth client ID → **Desktop app**.
2. Enable the **Google Calendar API** and **Google Tasks API** for the project.
3. Set the OAuth consent screen to **In Production**. In "Testing" status Google
   expires your refresh token after 7 days. Calendar and Tasks scopes are only
   *sensitive*, not *restricted*, so this needs no security audit — you will see
   an "unverified app" warning once, which you can click through.
4. Put the client id and secret in `.env`.
5. Authorise:
   ```powershell
   .venv\Scripts\python.exe -m peter.main --google-auth
   ```

<a name="74-dev-tools"></a>
### 7.4 Dev tools — 1 minute

Point at your repositories in `config/config.yml`:

```yaml
integrations:
  dev:
    repos:
      peter: D:/Peter-AI-Voice-Assistance
      work: D:/work/platform
```

That alone gives you `git_status`, `recent_commits`, `work_log` and
`standup_notes`.

For pull requests and CI, install the **GitHub CLI** from cli.github.com and run
`gh auth login`. Peter never handles a GitHub token — `gh` keeps its own
credentials in the OS keychain.

<a name="75-phone-sms-over-adb"></a>
### 7.5 Phone (SMS over ADB) — 5 minutes

1. Install **Android Platform Tools**, put `adb` on PATH.
2. On the phone: Settings → About → tap Build number 7 times → Developer
   options → **USB debugging** on.
3. Plug in over USB and accept the "Allow USB debugging" prompt on the handset.
4. Switch it on in `config/config.yml`:
   ```yaml
   integrations:
     phone:
       enabled: true
   ```
5. Check with `--health`, which reports `phone  ok - 1 device(s)`.

<a name="76-documents"></a>
### 7.6 Documents — 1 minute

```yaml
integrations:
  docs:
    folders:
      - D:/notes
      - D:/work/specs
```

Or index on demand: *"index D:/notes"*.

<a name="77-the-browser"></a>
### 7.7 The browser — once per site

```
you> log me into Myntra
```

Peter opens a visible window and hands you the keyboard. Peter never handles
site passwords. The session is saved in `data/browser_profile/` and reused, so
this is once per site.

---

<a name="8-command-line-reference"></a>
## 8. Command-line reference

```powershell
.venv\Scripts\python.exe -m peter.main [options]
```

| Option | What it does |
|---|---|
| `--text` | CLI assistant: type input, read replies |
| `--voice` | Voice assistant: wake word, microphone, spoken replies |
| `--provider anthropic\|openai\|gemini` | Override the LLM provider for this run |
| `--health` | Check every subsystem and exit |
| `--devices` | List audio devices and exit |
| `--google-auth` | Authorise Google Calendar and Tasks |
| `--telegram-setup` | Find your Telegram chat id |
| `--briefing` | Print today's briefing and exit |

`--health` is the first thing to run when anything seems wrong. It reports every
subsystem, distinguishing **disabled** (you turned it off), **not configured**
(no credentials), and **failed** (configured but broken).

---

<a name="9-complete-tool-reference"></a>
## 9. Complete tool reference

110 tools. `[r]` read, `[w]` write, `[!]` always confirms.

**System** — `open_app` [w] · `list_files` [r] · `read_file` [r] ·
`search_files` [r] · `write_file` [w] · `delete_file` [!] · `move_file` [w] ·
`set_volume` [w] · `take_screenshot` [r] · `get_clipboard` [r] ·
`set_clipboard` [w] · `run_powershell` [!] · `system_stats` [r] ·
`lock_workstation` [!] · `get_current_time` [r]

**Time** — `set_alarm` [w] · `set_timer` [w] · `set_reminder` [w] ·
`list_reminders` [r] · `cancel_reminder` [w] · `add_todo` [w] ·
`list_todos` [r] · `complete_todo` [w]

**Focus** — `start_focus_session` [w] · `end_focus_session` [w] ·
`focus_status` [r]

**Memory** — `remember_fact` [w] · `forget_fact` [w] · `recall` [r] ·
`set_preference` [w] · `forget_preference` [w] · `list_preferences` [r]

**Email** — `check_email` [r] · `read_email` [r] · `search_email` [r] ·
`count_unread_email` [r] · `inbox_digest` [r] · `waiting_on` [r] ·
`mark_email_read` [w] · `star_email` [w] · `archive_email` [w] ·
`delete_email` [!] · `send_email` [!]

**Calendar & tasks** — `check_calendar` [r] · `upcoming_events` [r] ·
`next_event` [r] · `create_calendar_event` [w] · `delete_calendar_event` [!] ·
`add_google_task` [w] · `list_google_tasks` [r] · `complete_google_task` [w]

**Briefing** — `daily_briefing` [r] · `briefing_schedule` [r]

**Browser** — `browse_page` [r] · `check_price` [r] ·
`compare_across_sites` [r] · `browser_status` [r] · `take_page_screenshot` [r] ·
`find_on_page` [r] · `browser_click` [w] · `browser_type` [w] ·
`browser_login` [w] · `close_browser` [w]

**Price watches** — `watch_price` [w] · `list_price_watches` [r] ·
`cancel_price_watch` [w] · `check_watches_now` [r]

**Vision** — `look_at_screen` [r] · `look_at_image` [r] ·
`look_at_browser_page` [r]

**Recording** — `start_recording` [w] · `stop_recording` [w] ·
`recording_status` [r] · `list_recordings` [r] · `read_meeting_notes` [r] ·
`summarise_recording` [w] · `audio_sources` [r]

**Development** — `list_repos` [r] · `git_status` [r] · `recent_commits` [r] ·
`my_pull_requests` [r] · `ci_status` [r] · `work_log` [r] · `standup_notes` [r]

**Documents** — `index_folder` [w] · `search_docs` [r] · `ask_docs` [r] ·
`docs_index_status` [r] · `forget_folder` [w]

**Workspaces** — `save_workspace` [w] · `restore_workspace` [w] ·
`list_workspaces` [r] · `delete_workspace` [w]

**Telegram** — `send_to_phone` [w] · `telegram_status` [r]

**Phone** — `read_sms` [r] · `latest_code` [r] · `phone_status` [r]

**Desktop** — `open_url` [w] · `open_website` [w] · `open_named_site` [w] ·
`play_youtube` [w] · `control_playback` [w] · `search_bookmarks` [r] ·
`open_bookmark` [w] · `list_locations` [r] · `open_location` [w]

**Models & cost** — `llm_status` [r] · `switch_llm_provider` [w] ·
`spend_report` [r]

> Tool groups whose credentials are missing are **not registered at all**. Every
> tool schema is re-sent on every API call, so an unusable group is pure cost —
> ~1,000 tokens per request describing actions that can only fail. Peter still
> knows the feature exists and will tell you it is not set up.

---

<a name="10-troubleshooting"></a>
## 10. Troubleshooting

**`ModuleNotFoundError: No module named 'peter'` (or anthropic, or yaml)**
You are running the system Python. Use `.venv\Scripts\python.exe`, or activate
the venv first. This is the most common problem by a wide margin.

**`--health` says "no API key"**
`.env` is missing or the key is blank. Check you copied `.env.example` to `.env`
(not `.env.txt` — Notepad does that).

**Google stopped working after a week**
Your OAuth consent screen is still in "Testing" status, which expires refresh
tokens after 7 days. Set it to "In Production" — see [§7.3](#73-calendar-and-tasks).

**The wake word is "hey jarvis", not "hey Peter"**
openWakeWord ships four pre-trained models (`alexa`, `hey_mycroft`,
`hey_jarvis`, `hey_rhasspy`) and there is no "Peter" among them. Training a
custom one is possible; point `voice.wake.model` at the resulting `.onnx` file.

**Voice mode does not hear me / triggers constantly**
Run `--devices` to check the right microphone is default. Then tune
`voice.wake.threshold` (lower = more sensitive) and `voice.stt.noise_margin`.

**The recording only has my voice on it**
System-audio capture was unavailable and it fell back to the microphone. Ask
*"what would recording capture?"* — if it says the sounddevice build cannot do
loopback, upgrade sounddevice.

**Price checks are slow**
By design — ~20 seconds between requests to the same domain. That spacing is
what keeps your accounts un-flagged. Watch fewer things, don't shorten the gap.

**Telegram is not responding**
`--health` reports the bridge. The three failure modes are: no token, no
`allowed_chat_ids` (a token alone does nothing), or the wrong chat id — an
unknown chat is silently ignored by design, so it looks identical to "not
running". Re-run `--telegram-setup`.

**`gh` tools say "not authenticated"**
Run `gh auth login`. Peter does not manage GitHub credentials.

**The phone is not found**
`--health` distinguishes "adb not installed", "no device attached", and
"attached but not ready" (which means you have not accepted the USB debugging
prompt on the handset).

**Costs look higher than expected**
Ask for `spend_report`, which breaks down by model. If `cache_read` is 0 across
turns in `llm_status`, prompt caching is not working and something volatile got
into the system prompt.

**Something surprising happened**
`data/audit.jsonl` has one line per tool call with arguments and results.
`data/peter.log` has the full application log.

---

<a name="11-privacy"></a>
## 11. Privacy

What stays on this machine, always:

- **Wake-word detection.** No audio leaves the machine until the wake word fires.
- **Meeting audio and transcription.** faster-whisper runs on your CPU. Only the
  final text summary is a model call.
- **Memory, documents, price watches, workspaces, the spend ledger, the audit
  log.** All local SQLite in `data/`.
- **Site passwords.** Peter never handles them; you log in by hand and the
  browser profile is reused.
- **GitHub and phone credentials.** Held by `gh` and by ADB's per-machine
  authorisation. Peter never stores either.

What is sent to your LLM provider:

- What you say, plus injected memory context and the current time.
- Tool results — page text, email subject lines, document passages, commit
  subjects, meeting transcripts (as text, for summarising).
- Screenshots, when you ask Peter to look at something.

What is sent to Telegram, if you enable it: your messages to Peter, Peter's
replies, and mirrored proactive announcements.

Peter never sends your email address, API keys, or passwords anywhere. The
logging layer scrubs known secret values from every log line as a backstop
against a formatting mistake leaking one.
