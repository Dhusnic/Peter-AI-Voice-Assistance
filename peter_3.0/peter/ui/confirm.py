"""Asking the human before acting.

Two implementations, because the right question depends on where the user's
attention is. In a voice session, a spoken "delete that file — yes or no?" is
natural and hands-free. But speech recognition is fallible, and a misheard
"yes" on a destructive action is exactly the failure this layer exists to
prevent, so an unclear answer falls through to a modal dialog rather than
guessing.

Every confirmer fails closed: timeout, error, or ambiguity all mean no.
"""

from __future__ import annotations

import ctypes
import logging
import time

log = logging.getLogger(__name__)

_YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead", "do it",
        "confirm", "confirmed", "please do", "correct", "right", "affirmative"}
_NO = {"no", "nope", "nah", "cancel", "stop", "don't", "dont", "no thanks",
       "negative", "abort", "never mind", "nevermind", "wait"}

# Win32 MessageBox flags
_MB_YESNO = 0x4
_MB_ICONWARNING = 0x30
_MB_SYSTEMMODAL = 0x1000
_MB_SETFOREGROUND = 0x10000
_IDYES = 6


class DesktopConfirmer:
    """A modal Windows dialog. Blocking, unmissable, and thread-safe."""

    def __init__(self, title: str = "Peter needs permission"):
        self.title = title

    def ask(self, prompt: str, timeout: float) -> bool:
        body = f"Peter wants to run:\n\n{prompt}\n\nAllow this?"
        flags = _MB_YESNO | _MB_ICONWARNING | _MB_SYSTEMMODAL | _MB_SETFOREGROUND
        try:
            # MessageBoxTimeoutW is undocumented but present on every supported
            # Windows build; fall back to the blocking dialog if it is missing.
            fn = getattr(ctypes.windll.user32, "MessageBoxTimeoutW", None)
            if fn is not None:
                result = fn(
                    None, body, self.title, flags, 0, int(max(1, timeout) * 1000)
                )
            else:
                result = ctypes.windll.user32.MessageBoxW(None, body, self.title, flags)
        except Exception:
            log.exception("confirmation dialog failed; denying")
            return False
        return result == _IDYES


class VoiceConfirmer:
    """Ask out loud, listen for yes or no, fall back to a dialog if unclear."""

    def __init__(self, speaker, transcriber, mic, fallback=None):
        self.speaker = speaker
        self.transcriber = transcriber
        self.mic = mic
        self.fallback = fallback or DesktopConfirmer()

    def ask(self, prompt: str, timeout: float) -> bool:
        question = _spoken_form(prompt)
        self.speaker.say(f"{question} Yes or no?")
        self.speaker.wait_until_idle(timeout=15)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.mic.flush()
            heard = self.transcriber.listen(self.mic).strip().lower().rstrip(".!?")
            if not heard:
                continue
            log.debug("confirmation heard: %r", heard)
            if _matches(heard, _YES):
                return True
            if _matches(heard, _NO):
                return False
            self.speaker.say("Sorry, was that a yes or a no?")
            self.speaker.wait_until_idle(timeout=10)

        # Never assume. An unheard answer is not consent.
        log.info("no clear spoken answer; escalating to dialog")
        return self.fallback.ask(prompt, timeout)


def _matches(heard: str, vocabulary: set[str]) -> bool:
    if heard in vocabulary:
        return True
    # "yes do it", "no don't" — check the leading word only, so that
    # "no, actually yes" stays ambiguous and re-asks.
    first = heard.split()[0] if heard.split() else ""
    return first in vocabulary


def _spoken_form(prompt: str) -> str:
    """Turn `delete_file(path=D:/x.txt)` into something speakable."""
    name, _, args = prompt.partition("(")
    action = name.replace("_", " ").strip()
    args = args.rstrip(")").strip()
    if not args:
        return f"Can I {action}?"
    if len(args) > 120:
        args = args[:120] + ", and more"
    return f"Can I {action}, with {args}?"
