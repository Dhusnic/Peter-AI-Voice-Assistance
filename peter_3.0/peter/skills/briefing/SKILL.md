# briefing

On-demand status for the automatic morning briefing (`peter/briefing.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `daily_briefing` | read | Build and return today's briefing: calendar, unread mail, reminders, to-dos (already phrased for speech). |
| `briefing_schedule` | read | Report when the automatic daily briefing is next due to run. |

## Setup

Always registered — no gate in `registry.py`'s `_REQUIRES`. Controlled by
`BriefingConfig` (`integrations.briefing` in `config.yml`): `enabled` (true by
default), `time` (`HH:MM`, validated), `include` (which sections to fold in —
defaults to `["calendar", "mail", "reminders", "todos"]`, and can add
`"weather"`/`"news"`), `max_emails`, `max_events`. Each section it draws on
degrades independently — an unconfigured one (no weather location, mail not
set up) lands in a "not set up" bucket rather than failing the whole briefing.

## Design notes & gotchas

- **Deliberately thin.** Both tools are one-line calls into `peter/briefing.py`
  (`build_briefing()`, `next_briefing_time()`) — the assembly logic lives
  there once, shared by this on-request tool and the scheduled job
  (`peter/scheduler/jobs.py`), so the two paths cannot drift apart by one
  being patched and the other forgotten.
- `daily_briefing`'s docstring tells the model to read the result back
  "close to as written" — it is pre-phrased for speech, not raw data meant
  to be re-summarised.
- `briefing_schedule` reports plainly when `integrations.briefing.enabled` is
  false, rather than guessing at a next-run time that will never happen.

## Future extension ideas

- `include` currently only toggles whole sections on/off; a per-section
  limit (e.g. only urgent mail, only today's events) would need new config
  fields, not a code change to this thin tool layer.
- No tool exists to change `briefing.time` or `include` by voice — both are
  config.yml-only today, consistent with how most other schedule-shaped
  settings in this codebase (poll intervals, thresholds) are edited by hand
  rather than exposed as a write tool.
