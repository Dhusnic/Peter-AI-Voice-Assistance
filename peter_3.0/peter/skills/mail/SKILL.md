# mail

Email over plain IMAP/SMTP with an app password — deliberately **not** the
Gmail API (`peter/integrations/mail/`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `check_email` | read | List unread messages, newest first. |
| `inbox_digest` | read | Triage unread mail: which ones plausibly need a reply. |
| `count_unread_email` | read | Fast unread count, no fetch — used by the briefing. |
| `search_email` | read | ANDed IMAP search by sender/subject/text/days/unread. |
| `read_email` | read | Full text of one message. |
| `mark_email_read` | write | Mark a message read. |
| `star_email` | write | Star/unstar a message. |
| `archive_email` | write | Archive (out of inbox, still searchable). |
| `delete_email` | write | Move to Trash. |
| `send_email` | write | Send a message, optional CC and reply-threading. |
| `waiting_on` | read | Mail *you* sent that never got a reply. |

## Setup

- `integrations.mail.enabled: true` and `secrets.has_mail`
  (`PETER_MAIL_ADDRESS` + `PETER_MAIL_APP_PASSWORD` in `.env`).
- `MailConfig`: `imap_host`/`imap_port` (default `imap.gmail.com`:993),
  `smtp_host`/`smtp_port` (default `smtp.gmail.com`:587), `inbox_folder`,
  `archive_folder`, `trash_folder`, `sent_folder` (read by `waiting_on` to
  see which of your own messages went unanswered), `fetch_limit`,
  `body_chars`.

## Design notes & gotchas

- **Why IMAP/SMTP and not the Gmail API — this is the load-bearing design
  decision for this whole skill.** A personal Google Cloud project in
  "Testing" status issues refresh tokens for Gmail's *restricted* scope that
  expire after 7 days — Peter would silently stop reading email every week
  until manually re-authorized. Moving to "In Production" for a restricted
  scope needs a third-party Google security audit, unrealistic for a personal
  project. Plain IMAP with an app password sidesteps the trap entirely, at
  the cost of Gmail's label/thread richness. This is a different trust/setup
  story from `calendar`/`contacts`/`drive`/`sheets`/`gdocs`, which stay on
  OAuth precisely because their scopes are *sensitive*, not *restricted*.
- **Sending always confirms.** `send_email` is `write` tier and pulled into
  `policy.standing_rules: confirm` — a sent email cannot be recovered the way
  a deleted file can. The docstring instructs reading recipient/subject/body
  back before sending and letting the confirmation prompt do the rest.
- **Every result carries a short bracketed uid**, never a raw message-id
  spoken aloud — `read_email`/`mark_email_read`/etc. all take that handle
  back.
- **Deliberately not wired to `find_google_contact`.** `send_email` still
  requires a real address; resolving a spoken name to one is a separate,
  read-tier step. Same trust-boundary split `contacts`' SKILL.md documents
  for `call_contact`/`make_phone_call` — a write action should never trust a
  name it was merely told.
- `inbox_digest` and `waiting_on` are both read-only by design — they only
  ever report, never draft or send anything, even though a "digest" or
  "follow up" framing might invite otherwise.
- IMAP search strings are quoted; `_escape()` strips backslashes and quotes
  from free text before building a query, since a stray quote would break it.

## Future extension ideas

- No draft-and-review step for `send_email` — the confirmation prompt *is*
  the review. A separate `draft_email` (compose, but don't send) tool would
  be a natural addition if voice dictation of long emails turns out to be
  common.
- `search_email`'s filters are ANDed only; no OR / date-range-pair support.
