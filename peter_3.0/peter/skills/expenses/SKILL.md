# expenses

A personal spend ledger built by parsing bank/UPI SMS (`peter/expenses.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `scan_bank_sms` | write | Scan recent SMS, add new transactions to the ledger. |
| `expense_report` | read | Summarise recorded spending: totals, top counterparties. |

## Setup

`integrations.expenses.enabled` (default true) **and**
`integrations.phone.enabled` (default false) — both required, gated
together in the registry's `_REQUIRES`. This skill has no credentials of its
own; it reuses the same ADB SMS-reading pipeline the `phone` skill's
`read_sms`/`latest_code` already read and hardened. See `phone`'s SKILL.md
for the ADB trust model and parsing details this depends on.

## Design notes & gotchas

- **Heuristic, not authoritative — by design, not a shortcoming to fix
  later.** `parse_transaction()` looks for an amount plus a debit/credit
  verb ("sent", "debited", "credited", "received") and pulls a counterparty,
  bank name, and the bank's own reference number where present. Indian bank/
  UPI SMS have no shared format across banks, and this errs toward
  *under*-counting — skipping a message it doesn't recognise rather than
  guessing at one it half-understands. Treat the report as a rough running
  total, never a substitute for the actual bank statement.
- **`scan_bank_sms` is safe to re-run with overlapping time windows** — a
  transaction already recorded (matched by the bank's own reference number,
  or a fallback key) is never double-counted.
- **Two real parsing bugs, both found by testing against real captured SMS,
  not invented fixtures — worth knowing before touching the regexes again.**
  (1) `_COUNTERPARTY_FROM` originally stopped only at a comma/newline; a real
  credit SMS ("...from RAVI KUMAR on 20-08-26. Ref No 998877.") has neither
  before the date clause, so the match ran to the end of the string and
  swallowed the date and reference number into the counterparty name — fixed
  by adding a period and an `on <date>`/`Ref` lookahead to the stop
  condition. (2) `_FUTURE_HINTS` originally excluded any SMS containing the
  bare string "e-mandate" (to skip a future-dated mandate *notice*), which
  also excluded a genuinely completed e-mandate debit *confirmation* — a
  real transaction that should count. Narrowed to the actual future-tense
  phrases ("will be deducted", "will be debited", "scheduled to be", "is due
  on").
- **On-demand only, no background sweep, deliberately.** A ledger silently
  mis-parsing or double-counting unattended is a worse failure mode than one
  that only runs when asked — unlike, say, meeting-prep or the inbox digest,
  which do poll in the background.
- Shares its "skip what you don't recognise" philosophy with `deliveries` —
  both reuse the same phone-SMS pipeline but parse for different verbs
  (transaction verbs here, shipment-status verbs there). See `deliveries`'
  SKILL.md.

## Future extension ideas

- No categorisation (groceries vs. bills vs. subscriptions) — `expense_report`
  reports totals and top counterparties only, not spend-by-category.
- No export to a spreadsheet — pairing with the `sheets` skill (write a
  monthly summary into a Google Sheet) would be a natural, low-effort
  addition given both already exist.
