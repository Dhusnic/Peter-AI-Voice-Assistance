# sheets

Google Sheets, via the shared Google OAuth client
(`peter/integrations/google/sheets.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `create_google_sheet` | write | Create a new spreadsheet. |
| `list_sheet_tabs` | read | List the tab names inside a spreadsheet. |
| `read_sheet_range` | read | Read cell values from an A1-notation range. |
| `write_sheet_range` | write | Overwrite a range with new values. |
| `append_sheet_rows` | write | Append rows after the last row with data. |

## Setup

- `integrations.google.enabled: true` and `secrets.has_google` — same shared
  OAuth client and gate as `calendar`/`contacts`/`drive`/`gdocs`.
- Needs the `spreadsheets` scope in `GoogleConfig.scopes`; same
  scope-added-after-token-issued caveat as `drive` (re-run `--google-auth`
  once, covers every Google skill).
- Needed literally no new transport code — `googleapiclient.discovery.build()`'s
  generic discovery mechanism covers Sheets the same way it covers Calendar;
  only `build_service(config, "sheets", "v4")` differs.

## Design notes & gotchas

- **`values_csv` grammar, not a nested-list argument.** `write_sheet_range`/
  `append_sheet_rows` take a flat string — `;` separates rows, `,` separates
  cells (e.g. `"Name,Score;Alice,10;Bob,8"`) — instead of a JSON
  array-of-arrays. A raw nested list is awkward both for tool-call
  generation and for a spoken interface where the user is dictating values,
  not writing JSON. `_parse_csv_grid()` is the one place this parsing lives;
  don't reintroduce a second grid format elsewhere in this module.
- `read_sheet_range` caps display at 30 rows (`_MAX_READ_ROWS`), with a
  visible "(N more rows)" suffix rather than silently truncating.
- No cell-formatting, formula, or chart support — this is values-only, which
  matches what a spoken interface can usefully dictate or read back anyway.

## Future extension ideas

- No `delete_sheet_tab`/`clear_range` tool — only create/read/write/append
  exist. A "clear this range" voice command currently has to be phrased as
  writing empty values over it.
- The CSV grammar breaks if a cell value legitimately contains a comma or
  semicolon — worth revisiting (e.g. an escape convention) if real usage
  hits that.
