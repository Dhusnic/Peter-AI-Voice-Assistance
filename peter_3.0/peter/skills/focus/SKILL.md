# focus

Timed, distraction-muted work blocks (`peter/focus.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `start_focus_session` | write | Mute system volume for N minutes, auto-restore when done. |
| `end_focus_session` | write | End the session early, restore volume. |
| `focus_status` | read | Whether a session is running and how much time is left. |

## Setup

Always registered — no config flag, no credential.

## Design notes & gotchas

- **Deliberately thin.** All three tools are one-line calls into
  `peter/focus.py`, which owns the actual state — in particular the
  scheduled restore job. Kept here only so the tool-facing surface (real
  docstrings, registered through the same gate as everything else) matches
  every other skill.
- **Only one session runs at a time.** `start_focus_session`'s docstring
  tells the model to check `focus_status` first if unsure one is already
  running — starting a second session on top of a first is not handled as a
  stacking/extension operation.
- The restore is a scheduled job under the hood (same scheduler infra `time`
  uses for reminders/alarms/timers), so it fires even if the conversation
  that started the session has long since ended.
- `label` is read back in both the start confirmation and the end-of-session
  summary — it exists purely for that phrasing, not stored as structured
  data elsewhere.
- This mutes *system* volume specifically — the same mechanism `system`
  skill's `set_volume` tool controls, not an app-level mute.

## Future extension ideas

- No stacking/extension of an in-progress session — "add 15 more minutes"
  today means ending and restarting rather than adjusting the existing
  timer.
- Muting volume is the only "distraction" lever pulled; no integration with,
  say, Windows Focus Assist or notification suppression beyond audio.
