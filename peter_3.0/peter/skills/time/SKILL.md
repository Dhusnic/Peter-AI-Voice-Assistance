# time

Time, reminders, alarms, timers, and the **local** to-do list
(`peter/scheduler/`, `peter/memory/store.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `get_current_time` | read | Current date/time, local or an IANA timezone. |
| `set_reminder` | write | One-off spoken reminder at an absolute time. |
| `set_timer` | write | Countdown timer, minutes from now. |
| `set_alarm` | write | Alarm at a clock time, optionally daily. |
| `list_reminders` | read | List every pending reminder/timer/alarm with next-run time. |
| `cancel_reminder` | write | Cancel one, by id or fuzzy text match. |
| `add_todo` | write | Add an item to the **local** to-do list (no time attached). |
| `list_todos` | read | List local to-do items. |
| `complete_todo` | write | Mark a local to-do done, by number or fuzzy text match. |

**`add_todo`/`list_todos`/`complete_todo` are the local, SQLite-backed to-do
list — not Google Tasks.** They never leave this machine and never appear on
the phone. For a to-do that should sync to the phone, use the `calendar`
skill's `list_google_tasks`/`add_google_task`/`complete_google_task` instead
— `add_todo`'s own docstring exists specifically to draw this line ("A to-do
has no time attached. If the user gave a time, use set_reminder instead").
See the `calendar` skill's SKILL.md for the Google Tasks side of this split.

## Setup

Always registered — core, no config flag, no credential. Reminders/timers/
alarms persist via APScheduler with a SQLite jobstore at the same `db_path`
memory uses; to-dos live in the same `peter.db`.

## Design notes & gotchas

- **Absolute times are ISO 8601, not free text** — Claude is given the
  current local time every turn and does "next Tuesday" arithmetic itself,
  better than a date-parsing library and failing loudly (a bad ISO string)
  instead of silently scheduling the wrong week.
- **Survives a restart, by construction, not by accident.** Alarms, timers
  and reminders are three thin wrappers (`add_once`/`add_in`/`add_daily`)
  over one APScheduler + SQLite jobstore — see §2.8 in
  `docs/ARCHITECTURE.md`. The one hard rule that makes this work: **job
  targets must be module-level functions**, never bound methods or lambdas —
  APScheduler serializes a job by its Python *import path* to survive a
  restart, and a bound method/lambda has no stable import path. This is why
  `fire_reminder()` is a free function, not a method — don't refactor it
  into one.
- `cancel_reminder`/`complete_todo` both use the "id or matching_text" shape
  — the same "list matches, ask which one" pattern used across
  `keep`/`contacts`/`calendar`.
- `set_timer` accepts fractional minutes (`0.5` = 30 seconds).
- Reading a code like "123456" digit-by-digit matters for OTPs (see `phone`
  skill's `latest_code`) but not here — `set_reminder`'s `text` is spoken
  verbatim, phrased as the reminder itself ("call Amma", not "remind the
  user to call Amma").

## Future extension ideas

- No recurring reminder besides daily alarms — a weekly or weekday-only
  reminder currently needs `set_alarm`'s `days` concept, which doesn't
  exist here (only in the `phone` skill's `set_phone_alarm`). Worth
  unifying if that gap is ever felt.
- To-dos have no due date or priority field — deliberately minimal ("no time
  attached" is the whole design), but a "what's overdue" question has
  nowhere to attach to today.
