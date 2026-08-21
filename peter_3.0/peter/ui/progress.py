"""What the CLI spinner shows while a turn is in flight.

The old spinner just said "Peter is thinking..." for the whole turn, which is
honest but useless — a turn that calls five tools looks identical to one that
calls none. This module turns the loop's raw progress events (thinking, back
for another look, running tool X with these arguments) into a short, specific,
present-continuous line, and drives a `rich.status.Status` that changes both
its text and its spinner animation to match what stage it's in.

Kept out of peter/llm/loop.py on purpose: the loop knows *that* a tool is
about to run, not how to describe it charmingly. That split is what keeps the
loop testable without Rich in the room.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable

from rich.console import Console
from rich.spinner import SPINNERS
from rich.status import Status

# Branded animations, one per stage, added to Rich's spinner table under
# names of their own — none of the built-ins land on "thinking" or "using a
# tool" the way these do. Registered here (a plain dict mutation) rather than
# vendored into Rich itself, so this is the one place that owns the look.
SPINNERS.setdefault("peterThink", {
    "interval": 220,
    # A brain that breathes — dots filling in and draining back out. Every
    # frame is the same length so the status line does not jitter sideways
    # as it animates, which loose emoji-cycling spinners tend to do.
    "frames": ["🧠    ", "🧠.   ", "🧠..  ", "🧠... ", "🧠....", "🧠... ", "🧠..  ", "🧠.   "],
})
SPINNERS.setdefault("peterTool", {
    "interval": 150,
    "frames": ["⚙️ ", "🔧 ", "🔩 ", "🔧 "],
})

# name -> (emoji, template). Template fields are filled from the tool call's
# own arguments via str.format_map with missing keys silently blank, so a
# template never raises just because a tool's signature changes.
_TOOL_LABELS: dict[str, tuple[str, str]] = {
    # system.py
    "open_app": ("🚀", "Opening {name}"),
    "open_url": ("🌐", "Opening {url}"),
    "list_files": ("📁", "Listing files in {directory}"),
    "read_file": ("📄", "Reading {path}"),
    "search_files": ("🔍", "Searching {directory} for {pattern}"),
    "write_file": ("✍️", "Writing to {path}"),
    "delete_file": ("🗑️", "Deleting {path}"),
    "move_file": ("📦", "Moving {source}"),
    "set_volume": ("🔊", "Adjusting the volume"),
    "take_screenshot": ("📸", "Taking a screenshot"),
    "get_clipboard": ("📋", "Checking the clipboard"),
    "set_clipboard": ("📋", "Updating the clipboard"),
    "run_powershell": ("💻", "Running: {command}"),
    "system_stats": ("📊", "Checking system stats"),
    "lock_workstation": ("🔒", "Locking the workstation"),
    "get_current_time": ("🕒", "Checking the time"),
    # time_tools.py
    "set_reminder": ("⏰", "Setting a reminder: {text}"),
    "set_timer": ("⏱️", "Setting a {minutes}-minute timer"),
    "set_alarm": ("⏰", "Setting an alarm"),
    "list_reminders": ("📋", "Checking your reminders"),
    "cancel_reminder": ("🗑️", "Cancelling that reminder"),
    "add_todo": ("✅", "Adding to your to-do list: {text}"),
    "complete_todo": ("✅", "Marking that todo done"),
    "list_todos": ("📋", "Checking your to-do list"),
    # memory_tools.py
    "remember_fact": ("🧠", "Remembering: {key}"),
    "forget_fact": ("🧹", "Forgetting that"),
    "set_preference": ("⚙️", "Saving that preference"),
    "forget_preference": ("🧹", "Clearing that preference"),
    "list_preferences": ("⚙️", "Checking your preferences"),
    "recall": ("🔍", "Searching memory for {query}"),
    # mail_tools.py
    "check_email": ("📬", "Checking your inbox"),
    "read_email": ("📧", "Reading that email"),
    "search_email": ("🔍", "Searching your email"),
    "count_unread_email": ("📬", "Counting unread email"),
    "mark_email_read": ("📖", "Marking it read"),
    "star_email": ("⭐", "Starring that email"),
    "archive_email": ("🗄️", "Archiving that email"),
    "delete_email": ("🗑️", "Deleting that email"),
    "send_email": ("📤", "Sending an email to {to}"),
    # calendar_tools.py
    "check_calendar": ("📅", "Checking your calendar"),
    "next_event": ("📅", "Checking what's next"),
    "upcoming_events": ("📅", "Checking upcoming events"),
    "create_calendar_event": ("📅", "Adding {title} to your calendar"),
    "delete_calendar_event": ("🗑️", "Deleting that event"),
    "add_google_task": ("✅", "Adding a task"),
    "list_google_tasks": ("📋", "Checking your tasks"),
    "complete_google_task": ("✅", "Completing that task"),
    # browser_tools.py
    "browse_page": ("🌐", "Opening {url}"),
    "check_price": ("💰", "Checking the price"),
    "browser_status": ("🌐", "Checking the browser"),
    "find_on_page": ("🔍", "Searching the page for {text}"),
    "take_page_screenshot": ("📸", "Capturing the page"),
    "browser_click": ("🖱️", "Clicking {description}"),
    "browser_type": ("⌨️", "Typing into {field_description}"),
    "browser_login": ("🔑", "Logging in"),
    "close_browser": ("🌐", "Closing the browser"),
    # desktop_tools.py
    "open_website": ("🌐", "Opening {url}"),
    "open_named_site": ("🌐", "Opening {name}"),
    "search_bookmarks": ("🔖", "Searching your bookmarks"),
    "open_bookmark": ("🔖", "Opening that bookmark"),
    "play_youtube": ("▶️", 'Finding "{query}" on YouTube'),
    "control_playback": ("🎵", "{action}"),
    "list_locations": ("📁", "Checking known locations"),
    "open_location": ("📁", "Opening {name}"),
    # llm_tools.py
    "llm_status": ("🤖", "Checking model status"),
    "switch_llm_provider": ("🔀", "Switching models"),
    # briefing_tools.py
    "briefing_schedule": ("📰", "Checking the briefing schedule"),
    "daily_briefing": ("📰", "Pulling together your briefing"),
    # focus_tools.py
    "start_focus_session": ("🎯", "Starting a {minutes}-minute focus session"),
    "end_focus_session": ("🎯", "Ending the focus session"),
    "focus_status": ("🎯", "Checking the focus session"),
    # vision_tools.py
    "look_at_screen": ("👀", "Looking at your screen"),
    "look_at_image": ("👀", "Looking at {path}"),
    "look_at_browser_page": ("👀", "Looking at the page"),
    # watch_tools.py
    "watch_price": ("📉", "Watching the price of {url}"),
    "list_price_watches": ("📉", "Checking what you are watching"),
    "cancel_price_watch": ("🗑️", "Stopping that price watch"),
    "check_watches_now": ("📉", "Checking every watched item"),
    # workspace_tools.py
    "save_workspace": ("🗂️", "Saving your workspace as {name}"),
    "restore_workspace": ("🗂️", "Restoring the {name} workspace"),
    "list_workspaces": ("🗂️", "Checking your saved workspaces"),
    "delete_workspace": ("🗑️", "Deleting the {name} workspace"),
    # docs_tools.py
    "index_folder": ("📚", "Indexing {path}"),
    "search_docs": ("🔎", "Searching your documents for {query}"),
    "ask_docs": ("📚", "Reading your documents"),
    "docs_index_status": ("📚", "Checking the document index"),
    "forget_folder": ("🧹", "Dropping {path} from the index"),
    # recorder_tools.py
    "start_recording": ("🎤", "Starting to record {label}"),
    "stop_recording": ("⏹️", "Stopping the recording"),
    "recording_status": ("🎤", "Checking the recording"),
    "list_recordings": ("🎤", "Checking your recordings"),
    "read_meeting_notes": ("📝", "Reading the notes from {which}"),
    "summarise_recording": ("📝", "Transcribing {which}"),
    "audio_sources": ("🔊", "Checking the audio sources"),
    # dev_tools.py
    "list_repos": ("📁", "Checking your repositories"),
    "git_status": ("🔀", "Checking the repo state"),
    "recent_commits": ("📜", "Reading recent commits"),
    "my_pull_requests": ("🔀", "Checking what is waiting on you"),
    "ci_status": ("🚦", "Checking the builds"),
    "work_log": ("📓", "Working out what you got done"),
    "standup_notes": ("📓", "Writing your standup"),
    # telegram_tools.py
    "send_to_phone": ("📲", "Sending that to your phone"),
    "telegram_status": ("📲", "Checking the phone bridge"),
    # phone_tools.py
    "read_sms": ("💬", "Reading your messages"),
    "latest_code": ("🔑", "Looking for the code"),
    "phone_status": ("📱", "Checking the phone"),
    # mail_tools.py (waiting-on)
    "waiting_on": ("📬", "Checking what nobody replied to"),
    "inbox_digest": ("📬", "Triaging your inbox"),
    # browser_tools.py (multi-site)
    "compare_across_sites": ("⚖️", "Comparing those pages"),
    # llm_tools.py (spend)
    "spend_report": ("💸", "Adding up what this has cost"),
}

# control_playback's own `action` argument is snake_case ("volume_up"); worth
# a friendlier verb rather than echoing it back verbatim.
_PLAYBACK_VERBS = {
    "play_pause": "Toggling playback", "next": "Skipping ahead",
    "previous": "Going back", "stop": "Stopping playback",
    "mute": "Toggling mute", "volume_up": "Turning it up",
    "volume_down": "Turning it down",
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def describe_tool(name: str, arguments: dict[str, Any] | None = None) -> str:
    """A short, present-continuous line for what this tool call is doing."""
    args = dict(arguments or {})
    if name == "control_playback":
        args["action"] = _PLAYBACK_VERBS.get(str(args.get("action", "")), "Adjusting playback")

    emoji, template = _TOOL_LABELS.get(name, ("🔧", _humanize(name)))
    try:
        text = template.format_map(_SafeDict(args))
    except (IndexError, ValueError):
        text = _humanize(name)
    text = " ".join(text.split())  # collapse gaps left by blank placeholders
    if len(text) > 90:
        text = text[:87] + "..."
    return f"{emoji} {text}"


def _humanize(name: str) -> str:
    """Fallback for any tool not in the map above — still readable, just plain."""
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Working on it"


# On-progress event, as passed by peter.llm.loop.run_turn: a stage name
# ("thinking" | "continuing" | "tool") plus the tool call when stage=="tool".
OnProgress = Callable[[str, Any], None]


class ProgressReporter:
    """Owns the CLI's one Status line and what it shows at each stage.

    Deliberately one Status object reused for the whole session (not a fresh
    `with console.status(...)` per turn) — recreating it would flash the
    terminal and lose the smooth spinner transition between stages.
    """

    def __init__(self, console: Console, assistant_name: str) -> None:
        self._console = console
        self._name = assistant_name
        self._status = Status(
            self._thinking_text(), console=console,
            spinner="peterThink", spinner_style="bright_cyan",
        )

    def start(self) -> None:
        self.thinking()
        self._status.start()

    def stop(self) -> None:
        self._status.stop()

    def on_progress(self, stage: str, call: Any) -> None:
        """Bridge for peter.llm.loop.run_turn's on_progress callback."""
        if stage == "tool" and call is not None:
            self.tool(call.name, call.arguments)
        elif stage == "continuing":
            self.continuing()
        else:
            self.thinking()

    def thinking(self) -> None:
        self._status.update(
            self._thinking_text(), spinner="peterThink", spinner_style="bright_cyan"
        )

    def continuing(self) -> None:
        # A filling bar for "putting the pieces together" — visually distinct
        # from the thinking pulse, so a glance at the terminal tells you which
        # stage a turn is in without reading the words.
        self._status.update(
            f"[bold magenta]🧩 {self._name} is putting that together...[/]",
            spinner="aesthetic", spinner_style="bright_magenta",
        )

    def tool(self, name: str, arguments: dict[str, Any] | None = None) -> None:
        text = describe_tool(name, arguments)
        self._status.update(
            f"[bold yellow]{text}...[/]", spinner="peterTool", spinner_style="yellow"
        )

    def switching_model(self, from_model: str, to_model: str) -> None:
        """A provider silently substituted a same-tier model mid-turn
        (currently Gemini only). Without this the spinner just sits on
        "thinking..." for however long that takes, which reads as frozen."""
        self._status.update(
            f"[bold yellow]⚠️  {from_model} unavailable — trying {to_model}...[/]",
            spinner="clock", spinner_style="yellow",
        )

    def retrying(self, provider: str, attempt: int, attempts: int, delay: float) -> None:
        self._status.update(
            f"[bold red]⚠️  {provider} isn't responding — retrying in {delay:.0f}s "
            f"(attempt {attempt}/{attempts})...[/]",
            spinner="clock", spinner_style="red",
        )

    @contextmanager
    def suspend(self):
        """Pause the spinner for something that needs the terminal line to
        itself — a confirm prompt reading stdin, mainly."""
        self._status.stop()
        try:
            yield
        finally:
            self._status.start()

    def _thinking_text(self) -> str:
        return f"[bold bright_cyan]{self._name} is thinking...[/]"
