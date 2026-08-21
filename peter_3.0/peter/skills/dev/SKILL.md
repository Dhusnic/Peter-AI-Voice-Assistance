# dev

Development state: git status, commits, pull requests, CI, work log, standup
— read-only (`peter/integrations/dev/`, `peter/worklog.py`,
`peter/ci_watch.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `list_repos` | read | List configured repositories and each one's current branch. |
| `git_status` | read | Branch, uncommitted changes, sync state for one repo. |
| `recent_commits` | read | Commits in the last N days, optionally filtered to your own. |
| `my_pull_requests` | read | PRs waiting on your review + your own open PRs, across every repo GitHub can see. |
| `ci_status` | read | Latest CI runs for a repo, with a failure count. |
| `work_log` | read | What actually got done — commits, meetings, focus sessions, to-dos. |
| `standup_notes` | read | Yesterday/today/blockers, phrased from real activity. |

**Every tool here is `read` tier — no exceptions.** There is no commit,
push, merge, checkout, or branch tool anywhere in this skill, and none
should be added: an assistant that rewrites the working tree on a misheard
sentence is a liability, and the upside (saving a `git commit` keystroke) is
small.

## Setup

`integrations.dev.enabled` (default true) **and** `bool(integrations.dev.repos)`
— the registry gates the whole module on having at least one repo
configured, since with none, every tool here can only answer "no
repositories are configured." `DevConfig`: `repos` (spoken name → path, the
first is the default when none is named), `git_author` (empty = match the
repo's own configured `user.email`), `git_timeout_seconds`, `gh_path`
(default `"gh"`), `gh_timeout_seconds`, `ci_watch` (`CiWatchConfig` — this
tunes the separate *proactive* CI-failure nudge in `peter/ci_watch.py`, not
`ci_status` directly, though both read the same `gh run list` data).

## Design notes & gotchas

- **Shells out to `git` and `gh` rather than talking to the GitHub API
  directly — this is why Peter never sees or stores a GitHub token.**
  `gh auth login` already keeps credentials in the OS keychain, so private
  repos and enterprise hosts work with zero extra configuration here, and
  the integration is one subprocess call plus a `--json`/`--porcelain` flag.
- **Everything is parsed from machine-readable formats, never human-readable
  output** — `git status --porcelain=v2` and `gh --json`, which don't
  change shape between versions or localise. Commit lines use a unit
  separator (`\x1f`) between fields specifically because commit subjects
  routinely contain colons, pipes, and dashes.
- **`work_log` is a join, not a memory of its own.** Commits sit in git,
  meetings in the calendar, focus sessions/meeting notes in episodes,
  finished work in the to-do list — nothing joins them up elsewhere.
  `build_worklog()` assembles all four and (via the scheduled daily job in
  `WorklogConfig`) writes one memory episode, so "what was I doing last
  Tuesday" survives long after that conversation left context. Every source
  degrades independently: no git, no calendar, no `gh` configured at all —
  you still get whatever the rest could see.
- **`standup_notes` is the only tool here that calls a model, and only to
  *phrase* material it's handed** — built from commits, calendar, focus
  sessions, and to-dos, with an explicit instruction never to invent a task,
  meeting, or blocker. It reflects what happened, not what you remember
  happening.
- `ci_status` is on-demand and stateless; the *proactive* CI watcher
  (`peter/ci_watch.py`, not a tool in this skill) is mostly its own dedup —
  it primes silently on first sweep after startup (recording what's already
  failing without announcing it) so restarting Peter doesn't produce a
  burst of alerts about last week's broken build, then only announces a
  failing run once rather than every ten minutes it stays in `gh run list`.

## Future extension ideas

- No git-blame or diff-reading tool — `recent_commits` gives subjects/authors/
  times, not content; reading an actual diff would need a new, careful
  read-only tool (large diffs are expensive to speak aloud, worth thinking
  through before adding).
- `standup_notes`/`work_log` both take a `days` parameter but there's no
  saved "my usual standup format" preference — every call re-derives
  phrasing from scratch.
