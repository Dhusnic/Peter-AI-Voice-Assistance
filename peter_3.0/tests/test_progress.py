"""peter.ui.progress — the CLI spinner's stage-to-text mapping.

Only the pure formatting logic is tested here. ProgressReporter itself wraps
a rich.status.Status and is exercised live (see main.py's run_text), not
unit-tested — there is nothing to assert about a spinner animation.
"""

from peter.ui.progress import describe_tool


def test_a_known_tool_uses_its_template_and_arguments():
    assert describe_tool("open_app", {"name": "Notepad"}) == "🚀 Opening Notepad"


def test_a_mapped_tool_with_no_useful_arguments_still_reads_fine():
    assert describe_tool("get_current_time", {}) == "🕒 Checking the time"


def test_an_unmapped_tool_falls_back_to_a_humanized_name():
    assert describe_tool("some_future_tool", {"x": 1}) == "🔧 Some future tool"


def test_a_missing_argument_leaves_a_blank_not_a_crash():
    """A template can outlive a signature change without raising."""
    result = describe_tool("open_app", {})
    assert result == "🚀 Opening"


def test_no_arguments_at_all_is_fine():
    assert describe_tool("lock_workstation") == "🔒 Locking the workstation"


def test_control_playback_translates_the_raw_action_to_a_verb():
    """The action argument is snake_case ("volume_up"); echoing it back
    verbatim would read like a debug log, not something Peter is doing."""
    assert describe_tool("control_playback", {"action": "volume_up"}) == "🎵 Turning it up"


def test_control_playback_with_an_unknown_action_still_says_something():
    assert describe_tool("control_playback", {"action": "wat"}) == "🎵 Adjusting playback"


def test_a_very_long_argument_is_truncated_so_the_status_line_stays_short():
    result = describe_tool("run_powershell", {"command": "x" * 300})
    assert len(result) <= 93  # emoji + space + 90 chars max
    assert result.endswith("...")


def test_send_email_shows_the_recipient_not_the_body():
    result = describe_tool(
        "send_email", {"to": "a@b.com", "subject": "Hi", "body": "secret stuff"}
    )
    assert result == "📤 Sending an email to a@b.com"
    assert "secret stuff" not in result
