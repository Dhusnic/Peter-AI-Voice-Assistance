"""Google Sheets tools.

write_sheet_range and append_sheet_rows take a simple `values_csv` grammar
(`;` separates rows, `,` separates cells) rather than a raw nested-list
argument — a JSON array-of-arrays is awkward both for tool-call generation
and for a spoken interface where the user is dictating numbers/words, not
writing JSON.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services

register_skill(SkillManifest(
    name="sheets", version="1.0.0",
    description="Google Sheets: create, read, write, and append to spreadsheets.",
    module=__name__, permissions=("network",),
    tools=("create_google_sheet", "list_sheet_tabs", "read_sheet_range",
           "write_sheet_range", "append_sheet_rows"),
))

_MAX_READ_ROWS = 30


def _parse_csv_grid(values_csv: str) -> list[list[str]]:
    rows = [row for row in values_csv.split(";") if row.strip() != ""]
    if not rows:
        raise ValueError(
            "Give values like 'a,b,c;d,e,f' — ';' separates rows, ',' separates cells."
        )
    return [[cell.strip() for cell in row.split(",")] for row in rows]


@peter_tool(tier="write")
def create_google_sheet(title: str) -> str:
    """Create a new Google Sheet.

    Args:
        title: The spreadsheet's title.
    """
    if not title.strip():
        return "Give the spreadsheet a title."
    sheet = services().sheets().create_spreadsheet(title.strip())
    return f"Created: {sheet.spoken()} — id {sheet.id}"


@peter_tool(tier="read")
def list_sheet_tabs(spreadsheet_id: str) -> str:
    """List the tab names within a Google Sheet.

    Args:
        spreadsheet_id: The spreadsheet's id.
    """
    tabs = services().sheets().list_tabs(spreadsheet_id.strip())
    if not tabs:
        return "No tabs found."
    return ", ".join(tabs)


@peter_tool(tier="read")
def read_sheet_range(spreadsheet_id: str, range_a1: str) -> str:
    """Read cell values from a Google Sheet range.

    Args:
        spreadsheet_id: The spreadsheet's id.
        range_a1: An A1 notation range, e.g. "Sheet1!A1:C10".
    """
    rows = services().sheets().read_range(spreadsheet_id.strip(), range_a1.strip())
    if not rows:
        return "That range is empty."
    shown = rows[:_MAX_READ_ROWS]
    lines = [" | ".join(cell for cell in row) for row in shown]
    suffix = f"\n... ({len(rows) - _MAX_READ_ROWS} more rows)" if len(rows) > _MAX_READ_ROWS else ""
    return "\n".join(lines) + suffix


@peter_tool(tier="write")
def write_sheet_range(spreadsheet_id: str, range_a1: str, values_csv: str) -> str:
    """Overwrite a Google Sheet range with new values.

    Args:
        spreadsheet_id: The spreadsheet's id.
        range_a1: An A1 notation range to write into, e.g. "Sheet1!A1".
        values_csv: The values to write. ';' separates rows, ',' separates cells,
            e.g. "Name,Score;Alice,10;Bob,8".
    """
    try:
        rows = _parse_csv_grid(values_csv)
    except ValueError as exc:
        return str(exc)
    updated = services().sheets().write_range(spreadsheet_id.strip(), range_a1.strip(), rows)
    return f"Updated {updated} cell(s)."


@peter_tool(tier="write")
def append_sheet_rows(spreadsheet_id: str, range_a1: str, values_csv: str) -> str:
    """Append new rows to a Google Sheet after the last row with data.

    Args:
        spreadsheet_id: The spreadsheet's id.
        range_a1: An A1 notation range identifying the table to append after,
            e.g. "Sheet1!A:C".
        values_csv: The rows to append. ';' separates rows, ',' separates cells,
            e.g. "Alice,10;Bob,8".
    """
    try:
        rows = _parse_csv_grid(values_csv)
    except ValueError as exc:
        return str(exc)
    updated = services().sheets().append_rows(spreadsheet_id.strip(), range_a1.strip(), rows)
    return f"Appended {len(rows)} row(s), {updated} cell(s) updated."
