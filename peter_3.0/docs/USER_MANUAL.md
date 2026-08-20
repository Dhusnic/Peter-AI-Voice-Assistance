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
   - [4.15 Your phone — calls, music, alarms and SMS (ADB)](#415-your-phone--sms-over-adb)
   - [4.16 Cost and models](#416-cost-and-models)
   - [4.17 Expenses and deliveries](#417-expenses-and-deliveries)
   - [4.18 Weather](#418-weather)
   - [4.19 Routines](#419-routines)
   - [4.20 News](#420-news)
   - [4.21 Notes and journal](#421-notes-and-journal)
   - [4.22 Performance](#422-performance)
   - [4.23 Skills](#423-skills)
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

146 tools across 28 areas. You never name a tool — you say what you want and
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

**Peter also learns when you correct him.** You do not have to say "remember
this" — if you tell him he got something wrong and explain what you actually
meant, he works out whether there is a lasting rule in it:

> you: *"summarise my inbox"*
> peter: *(five paragraphs)*
> you: *"no, always keep it to two sentences"*
> peter: *"...Noted — Keep replies to two sentences."*

From then on that applies to every conversation. It works for vocabulary too:
*"when I say the usual I mean filter coffee, no sugar"* is stored as a fact,
so "order the usual" means the right thing afterwards.

Three things worth knowing about how it behaves:

- **It ignores one-offs on purpose.** "Set a 5pm reminder" → "no, make it 6pm"
  is you changing your mind, not a rule, and Peter is built to say nothing
  rather than conclude every reminder should be at 6pm. Most corrections
  teach nothing durable, and that is the expected outcome — if you *want*
  something remembered, phrasing it as a general rule ("always...", "from now
  on...", "when I say...") is what makes it stick.
- **It tells you what it learned**, in the reply. Ask *"list my preferences"*
  any time to see everything in effect, and *"forget the preference X"* to
  drop one.
- **It never quietly deletes an old rule to make room for a new one.** There
  is a cap (25 preferences by default); once it is reached Peter says the list
  is full and asks you to forget one, rather than silently dropping something
  you set deliberately.

To turn any of this off, see `agent.learning` in `config.yml`.

**Peter can search memory by meaning, not just by keyword.** Off by default
until you run a one-off download:

```
.venv\Scripts\python.exe -m peter.main --download-embeddings
```

That fetches a ~23MB model and indexes everything already stored. Without it,
memory falls back to keyword search and nothing breaks — but keyword search
only finds a fact if you happen to reuse the words you stored it with. Asking
*"how do I get to work"* would not find *"takes route 70 bus to Gandhipuram"*,
because the two share no words. Measured across ten such questions, keyword
search found 3; with the model it found all 10.

It runs entirely on your machine — nothing about your memory is sent anywhere,
the same as the wake word. It also makes replies slightly cheaper, because
Peter stops padding every turn with facts that were not relevant.

Preferences additionally have a **scope**:

- `always` (the default) — injected on every turn. Right for anything about
  how Peter should behave: reply length, tone, units.
- `contextual` — only brought in when the turn is actually about it. Right for
  things like *"prefer Amazon over Flipkart"*, which is irrelevant to most
  conversations.

Anything stored before this existed stays `always`, so nothing you already set
changed behaviour. If Peter seems to have forgotten a preference, check
`similarity_threshold` under `memory.embeddings` in `config.yml` — or set that
preference back to `always`.

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
### 4.15 Your phone — calls, music, alarms and SMS (ADB)

*Off by default. [Setup](#75-phone-sms-over-adb).*

> "read my messages"
> **"what's the code?"**
> "is my phone charging?"
> "who called me in the last hour?"
> "what's on my phone screen right now?"
> "call Amma"
> "answer it" / "hang up"
> "play some lofi on Spotify" / "pause the music" / "next song"
> "set an alarm for 6:30 tomorrow, leave for the station" / "stop the alarm"
> "open this checkout page on my phone"

**Reading** is unrestricted: messages, the call log (with names resolved
against your contacts where they match), and now the screen itself — Peter
can take a screenshot of the phone and describe it, or answer a specific
question about it, the same way it can look at your desktop.

**Acting** covers real device control now, not just SMS reading:

- **Calls.** *"call Amma"* looks Amma up in your saved contacts and, if
  exactly one match comes back, dials straight away — no confirmation step,
  since a saved contact matched unambiguously isn't the "misheard number"
  risk the confirmation exists for. More than one contact matching means
  nothing is dialled; you'll be asked which one. *"call 9876543210"* — an
  actual number instead of a name — connects immediately too, but **is**
  held back by a confirmation step (see [§6](#6-permissions)), since a
  misheard digit sequence really can dial the wrong number. *"answer it"* and
  *"hang up"* run straight away either way, since a confirmation step would
  be pointless there — an unanswered call goes to voicemail while you're
  confirming.
- **Music.** *"play [something] on Spotify"* opens Spotify and, with a
  request, searches it; *"pause"* / *"next song"* send the phone's ordinary
  media keys, so they control whatever app is actually playing, Spotify or
  otherwise.
- **Alarms.** *"set an alarm for 6:30, leave for the station"* uses the
  phone's own clock app through Android's standard alarm intent — this is
  also how to set a phone-side reminder, since a reminder is just a labelled
  alarm. *"stop the alarm"* dismisses whatever is currently ringing, when the
  phone's clock app supports it (most do; a few heavily customised ones
  don't, and Peter will say so rather than pretend it worked).
- **Checkout hand-off.** *"open this on my phone"* puts a web page — a
  checkout, a login — directly on the phone's screen, so the OTP/UPI step
  that's legally yours to do stays one tap away instead of "go find your
  phone and type this URL in."
- **Saving a file.** *"grab that screenshot off my phone"* copies the newest
  screenshot from the phone onto this computer.

The one that's been here the longest and still earns its keep: `what's the
code?`. Peter can walk a checkout right up to the payment screen but cannot
legally complete it — the OTP is yours to enter. Reading it aloud so you can
type it is exactly as far as automation should reach into that. Peter reads
the code **digit by digit** ("1 2 3 4 5 6"), because a speech engine given
"123456" says "one hundred and twenty-three thousand…".

**Still no send-SMS tool.** Everything above adds real actions, but sending a
text as you stays out of scope: it needs default-SMS-app privileges or
version-specific `service call isms` incantations, and remains a bad idea for
something driven by speech recognition. Calling is different — it's one
well-documented public Android intent with no equivalent minefield.

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

<a name="417-expenses-and-deliveries"></a>
### 4.17 Expenses and deliveries

*Needs [phone SMS reading](#75-phone-sms-over-adb) switched on. On-demand only —
nothing runs in the background.*

> "scan my bank texts"
> **"how much have I spent this month"**
> "what did I spend on Swiggy"
> "scan for delivery updates"
> "what's still on the way"

Two small ledgers, both built by parsing SMS — the same pipeline that
already reads OTPs. *"Scan my bank texts"* reads recent SMS, picks out the
ones that look like a completed bank/UPI transaction, and adds anything new
to a spend ledger; *"scan for delivery updates"* does the same for courier
SMS, tracking each shipment's status forward (shipped → out for delivery →
delivered) as new messages come in.

**Run the scan before asking for a report** — `expense_report` and
`pending_deliveries` only ever describe what has already been scanned, they
do not read SMS themselves. Both scans are safe to run repeatedly: an
already-recorded transaction or shipment update is never counted twice.

> This is a **rough running total, not an accountant.** Indian bank and
> courier SMS have no shared format — every bank and every carrier phrases
> things slightly differently — so parsing is heuristic. It errs toward
> under-counting: a message it doesn't recognise is silently skipped rather
> than guessed at. Cross-check against your actual bank statement for
> anything that matters.

<a name="418-weather"></a>
### 4.18 Weather

*Off until a location is set. [Setup](#78-weather).*

> **"what's the weather"**
> "weather in Mumbai"

Current conditions via Open-Meteo — free, no API key, no signup. A city
name is geocoded once and the coordinates cached for the session, so asking
again costs nothing extra. Naming a city in the question ("weather in
Mumbai") checks that place instead of the configured default, without
changing config.

Add `weather` to `integrations.briefing.include` to fold it into the
morning briefing.

<a name="419-routines"></a>
### 4.19 Routines

*Off until at least one routine is defined. [Setup](#79-routines).*

> **"run my good night routine"**
> "start work mode"
> "what routines do I have"

A routine is a named chain of Peter's own tools, defined by hand in
`config.yml`, run as one spoken instruction:

```yaml
integrations:
  routines:
    defs:
      good night:
        - tool: pause_music_on_phone
          args: {}
        - tool: lock_workstation
          args: {}
```

Every step runs without asking you to confirm it individually — even a step
that would normally stop and ask (like `lock_workstation`) — because writing
the routine into `config.yml` by hand already **is** the confirmation, made
once rather than every time you say the routine's name. If one step fails,
the rest still run, and Peter tells you which one didn't.

<a name="420-news"></a>
### 4.20 News

> **"what's in the news today"**
> "news about cricket"

Top headlines via Google News' public RSS feed — free, no API key. Naming a
topic narrows it; otherwise it's general top headlines. Add `news` to
`integrations.briefing.include` to fold it into the morning briefing.

<a name="421-notes"></a>
### 4.21 Notes and journal

> **"note that the client wants the demo moved to Friday"**
> "what did I note about the wifi password"
> "read back my recent notes"

A quick, timestamped journal, distinct from memory ([§4.4](#44-memory)):
a note is never recalled automatically on a later turn the way a remembered
fact is — Peter only surfaces one when you search or ask for recent notes.
Use this for one-off things worth writing down, not standing facts about you.

<a name="422-performance"></a>
### 4.22 Performance

> **"how's your performance"**
> "which tools are slow"

Every tool call is timed automatically — how long it took, and how much of
that was actual computation versus waiting on a network call or another
process. Nothing needs switching on; this has been running since the tool
that made the call first ran.

> "worth a native rewrite" only ever means something did real, measurable
> computing for a long time — not just "took a while," since waiting on the
> network is not something a faster language can fix. For most of what Peter
> does, nothing crosses that bar, and the report says so plainly rather than
> manufacturing a candidate that isn't there.

For the full per-tool table, run `python -m peter.main --perf-report`
([§8](#8-command-line-reference)) — it breaks every tool down by call count,
average/P50/P95/max time, and how much of that was CPU versus waiting.

<a name="423-skills"></a>
### 4.23 Skills

> **"what skills do you have"**
> "do you have anything for GitHub"

Every tool Peter has is grouped into a named, versioned skill — "weather,"
"phone," "system" — with a short description and a note on what kind of
resource it touches (network, filesystem, a shell, the phone, the browser).
Asking what Peter can do lists the skills loaded this session; for the
complete catalog including anything not configured yet, run
`python -m peter.main --skill-list` ([§8](#8-command-line-reference)).

This is bookkeeping, not a new layer of trust: every tool in every skill
still passes through the same permission gate ([§6](#6-permissions)) it
always has. A skill's description cannot grant it more access than its own
tools already have.

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
    include: [calendar, mail, reminders, todos, waiting_on, pull_requests, weather, news]
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

**These seven always ask**, and are listed explicitly in `config.yml`:

```
delete_file · delete_email · delete_calendar_event
run_powershell · lock_workstation · send_email · make_phone_call
```

Destroy data, run arbitrary commands, send something to another person that
cannot be unsent, or — `make_phone_call` — connect a real call with no
on-device confirmation screen of its own. `call_contact` (calling a saved
contact by name) is deliberately **not** on this list, even though it also
dials immediately: it only ever calls a number matched unambiguously against
your own saved contacts, which doesn't carry the "misheard number" risk this
list exists to catch. Change the list if you want, understanding the cost.

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
### 7.3 Calendar, tasks, contacts and Drive — 10 minutes

One OAuth client covers all four — Calendar, Tasks, read-only Contacts, and
read-only Drive.

1. **console.cloud.google.com** → APIs & Services → Credentials →
   Create credentials → OAuth client ID → **Desktop app**.
2. Enable the **Google Calendar API**, **Google Tasks API**, **People API**,
   and **Google Drive API** for the project.
3. Set the OAuth consent screen to **In Production**. In "Testing" status Google
   expires your refresh token after 7 days. All four scopes are only
   *sensitive*, not *restricted*, so this needs no security audit — you will see
   an "unverified app" warning once, which you can click through.
4. Put the client id and secret in `.env`.
5. Authorise:
   ```powershell
   .venv\Scripts\python.exe -m peter.main --google-auth
   ```

**Already set this up before Contacts/Drive existed?** Your stored token
still covers Calendar/Tasks fine, but a Contacts or Drive call will fail
with "Google rejected the request (403)" until you re-run step 5 above —
the token needs a fresh consent to pick up the two new scopes. Enable the
People API and Drive API in the console first if you haven't.

Once authorised: *"find Ancy's number"* resolves a saved contact to a real
phone/email so `send_email`/`make_phone_call` can use it — see
[§7.6](#76-documents) for pointing Peter at a Drive folder to search.

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
### 7.5 Phone (calls, music, alarms, SMS over ADB) — 5 minutes

1. Install **Android Platform Tools**, put `adb` on PATH (or unzip it
   straight into the repo and point `adb_path` at it — see below).
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

Everything under this one switch — reading messages, calls, music control,
alarms — needs nothing beyond the above. No extra permission, no app to
install on the phone. Two things worth knowing:

- **Music control assumes Spotify is installed** (`integrations.phone.spotify_package`,
  default `com.spotify.music`) — `pause`/`next` work with whatever app is
  actually playing, but `play` specifically launches Spotify.
- **`adb` unzipped straight into the repo instead of PATH** is supported —
  point `adb_path` at it and it resolves against the project root regardless
  of where Peter is launched from, e.g.:
  ```yaml
  integrations:
    phone:
      adb_path: "./mobile dev/platform-tools/adb.exe"
  ```

**Going wireless — no cable needed after this one-time setup:**

1. On the phone: Settings → Developer options → **Wireless debugging** → on.
2. Tap **"Wireless debugging"** itself (not just the toggle) → **"Pair
   device with pairing code"**. It shows a 6-digit code and a pairing
   `ip:port` (different from the one you'll use to connect).
3. On the PC, pair once:
   ```
   adb pair <pairing-ip>:<pairing-port>
   ```
   (enter the 6-digit code when prompted)
4. Go back to the main Wireless debugging screen — it shows a **different**
   `ip:port`, the connect address. Connect once:
   ```
   adb connect <connect-ip>:<connect-port>
   ```
5. Save that same connect address in `config/config.yml`:
   ```yaml
   integrations:
     phone:
       wireless_address: "<connect-ip>:<connect-port>"
   ```
6. Unplug the cable and check: `--health` should still say
   `phone  ok - 1 device(s)`.

With `wireless_address` set, Peter retries once through `adb connect`
whenever it finds the phone disconnected — a wireless session doesn't
survive the phone leaving Wi-Fi, a reboot on either side, or wireless
debugging being toggled off and on, and this closes that gap automatically
rather than needing `adb connect` re-run by hand each time. **One thing that
can still break it:** the phone's IP can change if your router reassigns it.
If `--health` starts saying "no device attached" again, check whether the
phone's IP moved (Settings → About → Status) and update `wireless_address`
— or set a DHCP reservation on your router so it never changes.

<a name="76-documents"></a>
### 7.6 Documents (local folders and Google Drive) — 1 minute

```yaml
integrations:
  docs:
    folders:
      - D:/notes
      - D:/work/specs
    drive_folder_id: ""    # see below
```

Or index on demand: *"index D:/notes"*.

**Google Drive** shares the same search — `search_docs`/`ask_docs` don't
care whether a passage came from a local file or Drive. Needs the
`drive.readonly` scope from [§7.3](#73-calendar-and-tasks) (on by default;
re-authorise if you set this up before Drive support existed). Find a
folder's id in its URL — the part after `/folders/` — and either set
`drive_folder_id` in config.yml or say *"index Drive folder \<id\>"*. Not
recursive: only files directly inside that one folder. Google Docs/Sheets/
Slides are exported to text automatically; everything else needs a matching
extension in `integrations.docs.extensions`, same allowlist local files use.

<a name="77-the-browser"></a>
### 7.7 The browser — once per site

```
you> log me into Myntra
```

Peter opens a visible window and hands you the keyboard. Peter never handles
site passwords. The session is saved in `data/browser_profile/` and reused, so
this is once per site.

---

<a name="78-weather"></a>
### 7.8 Weather — 1 minute

```yaml
integrations:
  weather:
    location: "Chennai"
```

That's it — no key, no signup. Or skip geocoding entirely by setting
coordinates directly:

```yaml
integrations:
  weather:
    location: ""
    latitude: 13.08
    longitude: 80.27
```

Expenses and deliveries need nothing beyond [phone SMS reading](#75-phone-sms-over-adb)
already being switched on — no separate setup step.

<a name="79-routines"></a>
### 7.9 Routines — 1 minute

```yaml
integrations:
  routines:
    defs:
      good night:
        - tool: pause_music_on_phone
          args: {}
        - tool: lock_workstation
          args: {}
      start work:
        - tool: focus_start
          args: { minutes: 90 }
```

`tool` must be an existing tool's exact name — see [§9](#9-complete-tool-reference)
for the full list. `args` are that tool's arguments, `{}` if it takes none.
No routines are offered at all until at least one is defined here.

News and notes need nothing beyond `integrations.news.enabled` /
`integrations.notes.enabled`, both `true` by default — no separate setup step.

<a name="710-google-keep"></a>
### 7.10 Google Keep — read this before turning it on

**This is not like any other Google integration in this manual.** Calendar,
Tasks, Contacts and Drive all use a scoped OAuth grant — you can see exactly
what it allows, and revoke it any time from myaccount.google.com without
touching your password. Keep has no such option: **there is no official Keep
API for a personal Gmail account.** The real one exists only for Google
Workspace (paid business/education) accounts, gated behind an admin granting
domain-wide delegation — not something a personal `@gmail.com` address can
ever obtain.

The only way to reach Keep at all is [gkeepapi](https://github.com/kiwiz/gkeepapi),
an unofficial client that authenticates with a **master token** — the same
capability as your Google account password, not a scoped, individually
revocable grant. If that token leaks, whoever has it can act as you across
Google, not just in Keep. This is also technically against Google's Terms of
Service, and Google could break gkeepapi at any time by changing something
internally, with no warning.

Weigh that before continuing. If you're comfortable with it:

1. Set `integrations.keep.enabled: true` in `config/config.yml` — it defaults
   to `false` specifically so nothing attempts this without you opting in on
   purpose.
2. Obtain a master token by following gkeepapi's own documented method —
   github.com/kiwiz/gkeepapi. Peter does not do this step for you: automating
   a Google sign-in for an unofficial client is a worse idea than doing it
   once yourself.
3. Put both in `.env`:
   ```
   GOOGLE_KEEP_EMAIL=you@gmail.com
   GOOGLE_KEEP_MASTER_TOKEN=<the token from step 2>
   ```
4. Check with `--health`, or just try *"list my Keep notes"*.

**If it stops working later** — most likely the token was revoked (you
changed your Google password, or Google flagged the unofficial sign-in) —
Peter reports it plainly ("Google Keep sign-in failed...") rather than
silently going quiet, and the fix is generating a fresh token and updating
`.env`.

**To turn it off again**, set `integrations.keep.enabled: false`, or just
remove the two `.env` values — either one is enough.

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
| `--perf-report` | Print per-tool timing stats (last 7 days) and exit |
| `--skill-list` | Print every skill and its usable/not-configured status and exit |
| `--download-embeddings` | Fetch the semantic-memory model (~23MB), index existing memories, exit |

`--health` is the first thing to run when anything seems wrong. It reports every
subsystem, distinguishing **disabled** (you turned it off), **not configured**
(no credentials), and **failed** (configured but broken).

---

<a name="9-complete-tool-reference"></a>
## 9. Complete tool reference

146 tools. `[r]` read, `[w]` write, `[!]` always confirms.

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

**Google Contacts** — `find_google_contact` [r]

**Google Keep** *(off by default — [§7.10](#710-google-keep))* —
`list_keep_notes` [r] · `search_keep_notes` [r] · `create_keep_note` [w] ·
`pin_keep_note` [w] · `archive_keep_note` [w] · `delete_keep_note` [w]

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

**Documents** — `index_folder` [w] · `index_drive_folder` [w] ·
`search_docs` [r] · `ask_docs` [r] · `docs_index_status` [r] ·
`forget_folder` [w]

**Workspaces** — `save_workspace` [w] · `restore_workspace` [w] ·
`list_workspaces` [r] · `delete_workspace` [w]

**Telegram** — `send_to_phone` [w] · `telegram_status` [r]

**Phone** — `read_sms` [r] · `latest_code` [r] · `phone_status` [r] ·
`read_call_log` [r] · `read_phone_screen` [r] · `make_phone_call` [!] ·
`call_contact` [w] · `answer_phone_call` [w] · `hang_up_phone_call` [w] · `play_music_on_phone` [w] ·
`pause_music_on_phone` [w] · `skip_track_on_phone` [w] · `set_phone_alarm` [w] ·
`stop_phone_alarm` [w] · `open_link_on_phone` [w] · `save_phone_screenshot` [w] ·
`transcribe_phone_voice_note` [w]

**Expenses** — `scan_bank_sms` [w] · `expense_report` [r]

**Deliveries** — `scan_delivery_sms` [w] · `pending_deliveries` [r]

**Weather** — `get_weather` [r]

**Routines** — `run_routine` [w] · `list_routines` [r]

**News** — `get_news` [r]

**Notes** — `add_note` [w] · `search_notes` [r] · `recent_notes` [r] ·
`delete_note` [w]

**Performance** — `performance_report` [r]

**Skills** — `list_skills` [r]

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

**"Google rejected the request (403)" on a contact lookup or Drive search**
Not the 7-day expiry above — this is a token authorised before Contacts/Drive
scopes existed. Re-run `--google-auth` once; see [§7.3](#73-calendar-and-tasks).

**"Google Keep sign-in failed"**
The master token is wrong, expired, or was revoked (often by changing your
Google password, which invalidates it). Generate a fresh one and update
`GOOGLE_KEEP_MASTER_TOKEN` in `.env` — see [§7.10](#710-google-keep).

**The wake word is "hey jarvis", not "hey Peter"**
openWakeWord ships four pre-trained models (`alexa`, `hey_mycroft`,
`hey_jarvis`, `hey_rhasspy`) and there is no "Peter" among them. Training a
custom one is possible; point `voice.wake.model` at the resulting `.onnx` file.

**Voice mode does not hear me / triggers constantly**
Run `--devices` to check the right microphone is default. Then tune
`voice.wake.threshold` (lower = more sensitive) and `voice.stt.noise_margin`.
Note that `voice.stt.adaptive_noise` (on by default) blends a little of every
utterance's ambient level into the noise floor, so it drifts with the room
over a session rather than staying fixed at the one startup snapshot — if
that drift is causing trouble in a room with very inconsistent background
noise, set `voice.stt.adaptive_noise: false` to pin it to the startup value.

**Peter says "Didn't catch that."**
Normal, not an error: the wake word fired but nothing usable came through in
time (too quiet, too short, or you paused too long before speaking). Try
again, closer to the mic or a bit louder. This is intentional — previously
this case was silent and indistinguishable from the wake word not firing at
all, which meant you couldn't tell whether Peter had missed you entirely.

**Peter's voice suddenly sounds different / robotic mid-session**
The configured TTS engine (Piper or Edge) failed twice in a row — usually a
missing/corrupt Piper voice file, or no network for Edge — and Peter
automatically fell back to the Windows SAPI voice for the rest of the session
rather than going silent. Check the log for "switching to the Windows SAPI
fallback" to see why, fix the underlying cause (e.g. restore your network
connection for `edge`), and restart Peter to go back to the configured voice.

**Voice mode fails to start**
A bad `voice.stt.model` name, a missing/invalid `voice.wake.model` path, or
an unavailable audio device now prints the reason and drops into `--text`
mode automatically instead of crashing — read the printed message, fix the
setting in `config.yml`, and restart into `--voice` again.

**Peter goes silent for a bit, then hears me again on its own**
Expected, if you unplugged/replugged a USB microphone or a driver briefly
reset the input device — Peter notices the stream died and reopens it on its
own every couple of seconds until it succeeds, no restart needed. Check the
log for "microphone stream is not active" / "microphone reconnected" if you
want to confirm that's what happened.

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

**Wireless ADB stopped working after it was fine earlier**
With `wireless_address` set, Peter already retries once through `adb
connect` automatically before giving up — so if it's still failing after
that, the address itself is probably stale. Most likely cause: the phone's
IP changed (routers reassign IPs unless you've set a reservation). Check
Settings → About → Status on the phone and update
`integrations.phone.wireless_address` if it moved. Less commonly: wireless
debugging got toggled off (some phones turn it off on reboot) — turn it back
on, no need to re-pair, just `adb connect` once more.

**"stop the alarm" says it failed**
Support for `DISMISS_ALARM` depends on the phone's default clock app; Google
Clock and most AOSP-based ones honour it, some heavily customised OEM clocks
don't. There's no way to detect which case you're in short of trying it.

**Music control does nothing**
`play_music_on_phone` launches Spotify specifically — if it's not installed,
this fails outright. `pause`/`next` are generic media keys and only do
anything if some app on the phone is actually playing audio.

**"call [a number]" is asking for confirmation**
That's deliberate — `make_phone_call` is the one phone tool held back to
`confirm` in `config.yml`'s `policy.standing_rules`, since it connects
immediately with no on-device screen of its own, dialling digits transcribed
from speech. See [§6](#6-permissions). Calling a saved contact by name
(`call_contact`) is not held back this way — see the next entry.

**"call [name]" lists several contacts instead of calling**
`call_contact` never guesses between more than one match — it lists every
contact whose name matches (as a phrase, or on a shared word if no phrase
match exists) and asks which one, rather than picking. Say a more specific
name, or add the missing contact's number directly with "call [the number]".

**Costs look higher than expected**
Ask for `spend_report`, which breaks down by model. If `cache_read` is 0 across
turns in `llm_status`, prompt caching is not working and something volatile got
into the system prompt.

**Something surprising happened**
`data/audit.jsonl` has one line per tool call with arguments and results.
`data/peter.log` has the full application log.

**`expense_report` / `pending_deliveries` say nothing is tracked**
They only report what's already been scanned — run `scan_bank_sms` or
`scan_delivery_sms` first. A real transaction/shipment can also simply not
match the parser's patterns; see [§4.17](#417-expenses-and-deliveries) for
why that's a deliberate trade-off, not a bug to report.

**`get_weather` says it could not find a place**
Check the spelling, or be more specific (a well-known city name works best;
a very small town might not be in Open-Meteo's geocoding data at all — set
`latitude`/`longitude` directly in that case).

**"there is no routine called ..." even though I configured one**
Check `integrations.routines.defs` in `config.yml` for a typo in the name, and
say `list_routines` to hear exactly what's configured. The name match is
forgiving (spoken variants like "my good night routine" still find "good
night"), but it still needs some word overlap with the configured name.

**A routine says "not a known tool" for one of its steps**
The `tool:` value must be the tool's exact registered name (see
[§9](#9-complete-tool-reference)), and that tool's module must actually be
loaded — a tool gated behind another integration (e.g. a phone tool with
`integrations.phone.enabled: false`) will not be found either.

---

<a name="11-privacy"></a>
## 11. Privacy

What stays on this machine, always:

- **Wake-word detection.** No audio leaves the machine until the wake word fires.
- **Meeting audio and transcription.** faster-whisper runs on your CPU. Only the
  final text summary is a model call.
- **Memory, documents, price watches, workspaces, the spend ledger, the
  expense and delivery ledgers, notes, the performance log, the audit log.**
  All local SQLite in `data/` — the performance log holds nothing but a
  tool's name and how long it took, never its arguments or its result.
- **Site passwords.** Peter never handles them; you log in by hand and the
  browser profile is reused.
- **GitHub and phone credentials.** Held by `gh` and by ADB's per-machine
  authorisation. Peter never stores either.

What is sent to your LLM provider:

- What you say, plus injected memory context and the current time.
- Tool results — page text, email subject lines, document passages, commit
  subjects, meeting transcripts (as text, for summarising).
- Screenshots, when you ask Peter to look at something — the desktop or the
  phone's screen, over the same vision pipeline either way.

Phone commands (calls, media, alarms, SMS/call-log reads) travel over the
USB/ADB connection between this machine and the handset only — nothing about
*how* they're sent goes near your LLM provider, only the text of what you
asked for and Peter's reply.

**Weather and news are the two features that talk to a third party other than
your LLM provider.** A city name (or coordinates) goes to Open-Meteo, and a
topic or a general request goes to Google News' RSS feed — no account, no
key on either, and nothing else about you goes with the request, but it's
still a network call to a service that isn't Anthropic/OpenAI/Google.
Bank/UPI and courier SMS parsed for expenses and deliveries, and everything
you note down, never leave this machine at all — those are local SQLite
tables, same as everything else in that first list. A routine never adds a
network call of its own — it only runs tools that already exist, so its
privacy profile is exactly the sum of whichever tools you put in it.

**Google Keep is a different case from every other integration on this
page**, worth its own line: Contacts and Drive use a scoped OAuth grant, the
same shape as Calendar/Tasks/Mail — you can see exactly what it allows and
revoke it independently of your password at any time. Keep, being
unofficial, authenticates with a master token instead: full account-wide
capability, revocable only by changing your Google password outright. See
[§7.10](#710-google-keep) before enabling it.

What is sent to Telegram, if you enable it: your messages to Peter, Peter's
replies, and mirrored proactive announcements.

Peter never sends your email address, API keys, or passwords anywhere. The
logging layer scrubs known secret values from every log line as a backstop
against a formatting mistake leaking one.
