# deliveries

A shipment tracker built by parsing courier SMS (`peter/deliveries.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `scan_delivery_sms` | write | Scan recent SMS, record shipment status updates. |
| `pending_deliveries` | read | List shipments not yet marked delivered. |

## Setup

`integrations.deliveries.enabled` (default true) **and**
`integrations.phone.enabled` (default false) — both required, gated
together in the registry's `_REQUIRES`. Same SMS-reading pipeline as
`expenses`; no credentials of its own. See `phone`'s SKILL.md for the ADB
side, and `expenses`' SKILL.md for the sibling ledger built the same way.

## Design notes & gotchas

- **`parse_shipment()` looks for a shipment-status verb** ("shipped", "out
  for delivery", "delivered") plus a carrier name and an AWB number — the
  courier-SMS counterpart to `expenses.py`'s transaction parsing. Same
  honesty stance: courier SMS formats vary carrier-to-carrier, and an
  unrecognised message is silently skipped rather than guessed at.
- **Status only ever advances, never regresses — the one property this
  skill is really built around.** A shipment produces several SMS over its
  life (shipped → out for delivery → delivered), not guaranteed to arrive in
  that order. `DeliveryStore.upsert()` keys on the tracking number when
  present and only writes a new status if it outranks (`_STATUS_RANK`)
  what's already stored — a late-arriving "shipped" message after
  "delivered" has already landed cannot un-deliver a package.
- **Without a tracking number, the fallback key is `carrier + day`** — this
  cannot distinguish two same-day shipments from the same carrier. That's an
  accepted, documented gap, not a silent one: some carriers' SMS genuinely
  don't include an AWB number.
- **On-demand only, no background sweep**, for the same reason as
  `expenses`: an unattended ledger mis-parsing or double-counting is worse
  than one that only runs when asked.

## Future extension ideas

- No per-item tracking-number lookup tool — `pending_deliveries` lists
  everything not yet delivered; there's no "where's my order from Amazon"
  filter by carrier or rough item description.
- The `carrier + day` collision gap (two same-day, same-carrier shipments
  with no AWB) has no mitigation today beyond noting it — would need either
  a smarter heuristic or accepting occasional merges.
