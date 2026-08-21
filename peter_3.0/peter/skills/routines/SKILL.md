# routines

Named chains of Peter's own tools, run as one voice command
(`peter/routines.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `run_routine` | write | Run a named, config-defined chain of tool calls, one after another. |
| `list_routines` | read | List every configured routine and the tools each runs, in order. |

## Setup

`integrations.routines.enabled` **and** `bool(integrations.routines.defs)` —
gated together, same reasoning as `dev` needing at least one repo: an empty
routine list can only ever say "nothing is configured," so there's no reason
to spend tokens describing an empty group every turn. `RoutinesConfig.defs`
is empty (`{}`) by default and must be hand-edited into `config.yml`:

```yaml
routines:
  defs:
    good night:
      - tool: pause_music_on_phone
        args: {}
      - tool: lock_workstation
        args: {}
```

## Design notes & gotchas

- **Pure orchestration — zero new integrations, zero new credentials.** A
  routine names existing registered tools and their arguments; running one
  is exactly as capable (and exactly as limited) as the tools it chains.
- **`routines.run()` deliberately bypasses the policy gate for individual
  steps — a bypass that needs its own justification, and has one.** It
  calls each step's `ToolRecord.raw_fn` directly rather than going through
  `sdk_tool` — the `raw_fn` slot `registry.py` has carried since day one but
  nothing else uses. `run_routine` itself is still a normal `write`-tier
  tool call that passes through the gate exactly once; asking "proceed?"
  again for every step inside it would defeat the entire point of naming a
  routine. The trust model is the same one `policy.standing_rules` already
  uses: **writing the routine into `config.yml` by hand *is* the standing
  instruction**, made once and deliberately, not re-confirmed per
  invocation.
- **The one thing this must never allow regardless of that trust: an
  auto-executing `spend` action.** `routines.run()` refuses any step whose
  tier is `spend` outright, belt-and-braces — no `spend`-tier tool exists
  anywhere in this codebase today, and the interceptor is precisely what
  keeps it that way. Do not weaken this check even though it currently has
  nothing to catch.
- **A failed step is reported and the rest still run** — "3 of 4 done" beats
  an all-or-nothing rollback for something this low-stakes.
- `run_routine`'s own docstring warns against inventing a routine name that
  wasn't actually configured — it instructs calling `list_routines` first
  when unsure what exists, rather than guessing at a plausible-sounding name.

## Future extension ideas

- No conditional steps (run step B only if step A succeeded/returned a
  particular value) — every routine is a flat, unconditional sequence.
- No parameterised routines — a routine's `args` are fixed in config.yml;
  "run my good night routine, but mute for 2 hours instead of the usual" has
  no way to override a single step's argument at call time.
