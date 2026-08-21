# phone

Reading and controlling the phone over ADB: SMS, calls, media, alarms, apps,
files, contacts, device settings, notifications, location, and a raw shell
escape hatch (`peter/integrations/phone/adb.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `read_sms` | read | Recent text messages. |
| `latest_code` | read | Most recent OTP/one-time code, read digit by digit. |
| `phone_status` | read | Connected? Battery level? |
| `read_call_log` | read | Recent calls: who, when, how long. |
| `read_phone_screen` | read | Screenshot the phone, describe it or answer a question (via the vision pipeline). |
| `open_link_on_phone` | write | Open a web link on the phone's screen (`http(s)` only). |
| `save_phone_screenshot` | write | Pull the newest phone screenshot onto this computer. |
| `transcribe_phone_voice_note` | write | Pull the newest voice note and transcribe it locally. |
| `call_contact` | write | Call a saved contact by name — no confirmation needed. |
| `make_phone_call` | write | Call a raw number — confirms first (standing rule). |
| `answer_phone_call` | write | Answer the ringing call. |
| `hang_up_phone_call` | write | End the active call / reject a ringing one. |
| `play_music_on_phone` | write | Play (Spotify) by query, or resume. |
| `pause_music_on_phone` | write | Pause whatever's playing. |
| `skip_track_on_phone` | write | Skip to the next track. |
| `set_phone_alarm` | write | Set an alarm (also how to set a phone-side "reminder"). |
| `stop_phone_alarm` | write | Dismiss the ringing alarm. |
| `list_phone_apps` | read | List installed package ids. |
| `launch_phone_app` | write | Launch an app by package id. |
| `uninstall_phone_app` | write | Uninstall an app — confirms first (standing rule). |
| `push_file_to_phone` | write | Copy a local file onto the phone. |
| `list_phone_files` | read | List files in a phone directory. |
| `delete_phone_file` | write | Delete one file — confirms first (standing rule). |
| `list_phone_contacts` | read | List/filter saved contacts. |
| `add_phone_contact` | write | Add a contact, read back to confirm it landed. |
| `set_phone_wifi` | write | WiFi on/off. |
| `set_phone_bluetooth` | write | Bluetooth on/off (best-effort on Android 12+). |
| `set_phone_airplane_mode` | write | Airplane mode on/off — confirms first (standing rule). |
| `set_phone_volume` | write | Media volume 0–100. |
| `set_phone_brightness` | write | Screen brightness 0–100 (forces manual mode). |
| `reboot_phone` | write | Reboot — confirms first (standing rule). |
| `read_phone_notifications` | read | Currently posted notifications, every app. |
| `phone_location` | read | Last known (cached) location. |
| `run_phone_shell_command` | write | Raw `adb shell` escape hatch — confirms first (standing rule). |

## Setup

- `integrations.phone.enabled: true` in `config.yml` — **off by default**;
  needs USB debugging on and this machine authorised on the handset, a
  deliberate act, not a default state. `_REQUIRES` gates the whole module on
  this one flag.
- `PhoneConfig`: `adb_path` (empty = search PATH), `device_serial` (needed
  only with more than one attached device), `wireless_address` (`"<ip>:<port>"`
  for ADB-over-WiFi, empty = USB-only), `raw_shell_enabled` (default
  **false**, a second, independent opt-in beyond `enabled` — gates
  `run_phone_shell_command` specifically, same "off until you explicitly
  opt in beyond the integration switch" shape `KeepConfig`/`MapsConfig` use
  for their own riskiest capability), plus `sms_limit`, `otp_window_minutes`,
  `pull_dirs`, `voice_note_dirs`, `spotify_package`, `push_default_dir`.
- **`expenses` and `deliveries` both require this skill too** — they read
  the same SMS stream, gated in the registry as
  `<their own flag> and integrations.phone.enabled`. See those skills'
  SKILL.md files.
- Six tools are pulled into `policy.standing_rules: confirm` on top of their
  `write` tier: `make_phone_call`, `uninstall_phone_app`,
  `delete_phone_file`, `reboot_phone`, `set_phone_airplane_mode`,
  `run_phone_shell_command` — because they destroy data, are highly
  disruptive, can sever Peter's own connection to the device, or are an
  intentionally unrestricted escape hatch.

## Design notes & gotchas

- **There is no path from Peter to sending a text as you, ever — by design,
  not by omission.** Sending as you needs default-SMS-app privileges or
  Android-version-specific `service call isms` incantations, and is a bad
  idea for something driven by speech recognition. Do not add a
  `send_sms` tool without re-reading this reasoning.
- **`call_contact` vs. `make_phone_call` — a deliberate two-tool split, not
  an optional argument.** `call_contact` only ever dials a number already
  saved under a real name in the phone's own contacts — materially lower
  risk than a raw number the model transcribed from speech, so it skips
  confirmation. `make_phone_call` is for digits the user actually spoke, and
  always confirms. The policy gate applies a tier/rule *per tool*, not per
  argument, so one tool with an optional `contact_name` could not have
  carried two different confirmation behaviours. **Once a number is sitting
  in the conversation from an earlier `call_contact` result, always re-call
  `call_contact` with the name — never `make_phone_call` with that number** —
  reaching for the latter defeats the entire point of the split. Both tools'
  docstrings say this explicitly.
- **Every free-text value handed to the phone's shell goes through
  `_quote()` (`shlex.quote`) — a real, previously-shipped vulnerability,
  fixed.** `adb shell` re-splits its argument in the *phone's* own shell.
  An early version of `open_link_on_phone` double-quoted a URL and escaped
  embedded double quotes by hand — which does not stop `$(...)`/backtick/
  `$VAR` expansion *inside* double quotes in a POSIX shell, so a crafted URL
  like `https://x/$(reboot)` would have executed on the phone. Single-quoting
  via `_quote()` disables all such expansion. `call_number` additionally
  validates the number against an allow-list of phone-number characters
  before it ever reaches a shell string, as a second, independent layer.
- **`content query`'s field separator must be `:`, not `,`.** A
  comma-separated `--projection a,b,c` works for `content://sms/inbox` but
  fails outright for `content://call_log/calls` and the contacts provider —
  both broken from the moment they shipped, caught only by running against a
  real device (mocked tests never exercise the real tokenizer). A
  colon-separated projection works identically across all three providers.
  The contacts provider has a second gotcha on top: its friendly `number`
  column alias does not resolve through the raw `content` CLI at all — the
  real column is `data1`.
- **A message body can contain a literal embedded newline** (routine for a
  multi-line bank SMS) — `_rows()` splits only on a newline immediately
  followed by the next `Row: N ` marker (not every `\n`), and both `_ROW`/
  `_FIELD` regexes run with `re.DOTALL`, or a multi-line body truncates and
  silently drops whatever field came after the break (which for SMS is
  `date`, making the message read back as 1 Jan 1970).
- **`screenshot_bytes()` bypasses the shared `_run()` helper deliberately**
  — `_run` runs adb in text mode, which on Windows rewrites line endings and
  would corrupt a binary PNG. It shells out separately in binary mode.
- **Wireless ADB self-heals.** With `wireless_address` set, every command
  that talks to the device retries exactly once through
  `adb connect <address>` on a "no device"/"device offline" failure (never
  "unauthorized," which reconnecting can't fix) before raising — via a
  shared `_retry_if_disconnected()` helper. USB-only setups pay nothing for
  this: the check is a single string test, not a proactive poll.
- **Contact name resolution is best-effort and cached 10 minutes
  in-process**, normalising both sides to the last 10 digits so different
  formattings of the same number line up. `call_contact`'s matching is
  two-pass: literal substring of the saved name first, falling back to
  matching on individual *words* — the case that motivated the fallback is
  real: a contact saved as bare "Ancy" was called "Ancy Mom" out loud.
- **`add_phone_contact` reads back what it wrote rather than trusting the
  insert** — some OEM sync adapters (observed on Samsung/Xiaomi) reject or
  mangle an account-less raw contact; the sequence creates, re-queries for
  the row id, inserts name/number, then re-queries by name to confirm before
  declaring success, best-effort cleaning up a half-written contact on
  failure.
- **`run_phone_shell_command` deliberately skips `_run()`'s failure
  heuristic and applies no `_quote()`.** `_run` treats a nonzero exit code
  or the literal word "error:" as failure — wrong for an arbitrary command
  (`grep` with no match legitimately returns nonzero). This tool returns
  whatever happened, exit code included, and applies no quoting at all:
  transparency and flexibility are the entire point of an unrestricted
  escape hatch, the phone-side counterpart to `run_powershell` in `system`.
- **`adb_path` resolves against the project root, not the working
  directory** — a real bug, found by actually running it: a relative
  `adb_path` only worked by accident when launched from inside `peter_3.0/`.
  One deliberate exception: a bare `"adb"` (no directory component) is left
  untouched, meaning "search PATH."
- **Deliberately declined: any tap/swipe/typed-keystroke UI automation.**
  Every tool here acts through named Android intents, content-provider
  calls, or `dumpsys`/`settings` commands — never generic UI automation, the
  same line the `browser` skill draws around purchasing.
- `phone_location` and `read_phone_notifications` are explicitly
  best-effort, parsing diagnostic `dumpsys` text dumps whose format can
  drift across Android versions/OEM skins — a record that doesn't parse is
  skipped, not treated as a whole-read failure. Location specifically is a
  **cache**, possibly hours stale or empty right after a reboot — never
  presented as a live fix.

## Future extension ideas

- No send-SMS tool, and per the design notes above, none should be added
  without re-litigating that reasoning from scratch.
- Bluetooth toggling is unreliable on Android 12+ with no fully reliable
  no-root fix known — worth revisiting only if a newer public API appears.
- `list_phone_apps` returns package ids only, with no human-readable name —
  resolving that needs `aapt dump badging` against the APK, a desktop SDK
  tool with no on-device equivalent; not solvable without adding that
  dependency.
