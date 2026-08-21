# skills

The meta-skill: what capability packages Peter has, and which are actually
usable right now (`peter/agent/skills.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `list_skills` | read | List every skill loaded this session: its tools, and whether it's fully usable or missing credentials/config. |

## Setup

Always registered — no config flag, no credential (it would be strange for
the tool that reports on configuration to itself require configuration).

## Design notes & gotchas

- **`list_skills` and `python -m peter.main --skill-list` intentionally see
  different amounts of the world, and both say so — this is the single most
  important thing to know before "fixing" either to match the other.**
  `--skill-list` calls `registry.load_all_tools()` with no config, so it
  loads *every* module, gated or not, because that process prints and exits
  — there's no live tool list it could accidentally widen. `list_skills`,
  called mid-conversation, only reports on whatever `usable_modules()`
  actually loaded at startup (the credential-gated subset). Reloading
  everything inside a live session would have a real side effect —
  permanently unlocking previously-hidden tool schemas for the rest of the
  conversation, since `registry.tool_specs()` returns every currently
  *registered* record. `list_skills`'s own docstring points at
  `--skill-list` for the complete catalog rather than trying to match it.
- **Every skill declares one `SkillManifest`** (name, version, description, a
  `module` field set to its own `__name__`, a `permissions` tuple, and the
  exact tool names it owns), registered at import time via `register_skill()`
  right next to the `@peter_tool` functions it describes — a small typed
  Python object, not a second file format (`SKILL.md` files are docs-only
  and never parsed by anything).
- **`permissions` is purely advisory — it enforces nothing.** The existing
  policy gate already sits above every tool call regardless of which module
  registered it; the manifest's short, fixed-vocabulary permission tags
  (`network`, `filesystem`, `shell`, `phone`, `browser`) exist only so
  `list_skills`/`--skill-list` can show at a glance what kind of resource a
  skill touches. A skill cannot grant itself more access by under-claiming
  its tools' real tiers in its manifest.
- **One test enforces the whole thing stays honest:**
  `tests/test_skills.py::test_every_registered_tool_is_covered_by_exactly_one_skill`
  asserts the union of every manifest's declared tools equals the full set
  of names in `registry.all_records()` — a tool added later without
  updating its skill's manifest fails a test rather than `list_skills`
  silently going stale.
- **Stage 1 of a longer ecosystem plan — explicitly not remote install, a
  public registry, or sandboxing.** Fetching and executing third-party skill
  code without a sandbox that doesn't exist yet would be a real security
  regression for an assistant with call/SMS/shell/file access, not a
  missing convenience. All of that waits for an actual external skill to
  justify building it — see §2.21 in `docs/ARCHITECTURE.md`.

## Future extension ideas

- No tool filters `list_skills` output by permission tag or by
  usable-vs-not — it's a flat listing today; fine at 32 skills, would
  benefit from filtering if the count keeps growing.
- No version-mismatch or update-check concept exists yet — `version` on
  `SkillManifest` is currently decorative, waiting on the Stage 2
  infrastructure noted above.
