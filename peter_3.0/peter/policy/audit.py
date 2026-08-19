"""Append-only audit log.

One JSON object per line, one line per tool call — including the ones that were
refused. When Peter does something surprising, this file is the only record of
what actually happened, so it is written before anything can go wrong with the
result and never rewritten in place.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()

# Never write these argument values to disk, whatever tool they arrive on.
_REDACT_KEYS = {"password", "passwd", "secret", "token", "api_key", "otp", "pin", "cvv"}
_MAX_VALUE_CHARS = 500


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if k.lower() in _REDACT_KEYS else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
        return value[:_MAX_VALUE_CHARS] + f"... <truncated {len(value)} chars>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)[:_MAX_VALUE_CHARS]


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        tool: str,
        tier: str,
        decision: str,
        args: dict | None = None,
        result_summary: str = "",
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool,
            "tier": tier,
            "decision": decision,
            "args": _scrub(args or {}),
            "result": _scrub(result_summary),
        }
        if duration_ms is not None:
            entry["duration_ms"] = round(duration_ms, 1)
        if error:
            entry["error"] = _scrub(error)

        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def tail(self, n: int = 20) -> list[dict]:
        """Last n entries, oldest first. For the tray inspector and debugging."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
