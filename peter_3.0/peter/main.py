"""Peter 3.0 entrypoint.

    python -m peter.main                 voice mode
    python -m peter.main --text          type instead of speaking
    python -m peter.main --devices       list audio devices and exit
    python -m peter.main --health        check every subsystem and exit
    python -m peter.main --google-auth   authorise Calendar and Tasks
    python -m peter.main --briefing      print today's briefing and exit

Two things this does that peter_1.0 did not:

**Each turn is isolated.** A failure inside one turn is caught, spoken, and
logged, and the loop returns to waiting. peter_1.0 wrapped its entire main loop
in `except: pass`, which meant a broken turn either died silently or killed the
program with no trace of why.

**Nothing is a global.** Services are constructed here, put in one container,
and torn down on exit.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from peter.agent import registry
from peter.agent.brain import Brain
from peter.briefing import build_briefing, schedule_briefing
from peter.core.config import Config, get_config
from peter.core.errors import ConfigError, PeterError
from peter.core.logging import configure_logging
from peter.core.services import ServiceContainer, set_container
from peter.memory.store import MemoryStore
from peter.policy.audit import AuditLog
from peter.policy.gate import ConsoleConfirmer, Policy, PolicyGate
from peter.scheduler.jobs import Scheduler
from peter.voice.tts import PrintSpeaker

log = logging.getLogger("peter")


class Peter:
    """Owns every long-lived object and the turn loop."""

    def __init__(self, config: Config, text_mode: bool = False,
                 provider: str | None = None):
        self.config = config
        self.text_mode = text_mode
        self.stopping = threading.Event()

        registry.load_all_tools(config=config)

        self.container = ServiceContainer(config)
        self.container.memory = MemoryStore(config.db_path)
        self.container.audit = AuditLog(config.audit_path)
        self.container.scheduler = Scheduler(config.db_path)
        set_container(self.container)

        self.gate = PolicyGate(
            Policy.from_config(config.policy), self.container.audit
        )
        registry.set_interceptor(self.gate)

        self.brain = Brain(
            memory=self.container.memory, config=config, provider_name=provider
        )
        self.container.brain = self.brain

        # Voice-only pieces, left unset in text mode.
        self.mic = None
        self.transcriber = None
        self.detector = None
        self.speaker = None
        self.tray = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.container.scheduler.start()
        schedule_briefing(self.container.scheduler, self.config)
        log.info(
            "%s/%s | %d tools | %d job(s) scheduled",
            self.brain.provider_name,
            self.brain.model,
            len(registry.all_records()),
            len(self.container.scheduler.list_jobs(include_system=True)),
        )

    def shutdown(self) -> None:
        self.stopping.set()
        if self.speaker is not None:
            self.speaker.shutdown()
        if self.mic is not None:
            self.mic.stop()
        if self.tray is not None:
            self.tray.stop()
        if self.container.scheduler is not None:
            self.container.scheduler.shutdown()
        # Before the usage log, so a held prompt cache stops billing storage
        # even if something below throws.
        self.brain.close()
        self.container.close()
        set_container(None)
        log.info("usage this session: %s", self.brain.usage_summary())

    # ------------------------------------------------------------- turn logic
    def handle(self, text: str) -> str:
        """Run one turn. Never raises — a failure becomes something speakable."""
        try:
            result = self.brain.ask(text)
        except PeterError as exc:
            log.warning("turn failed: %s", exc)
            return str(exc)
        except Exception as exc:
            log.exception("turn failed")
            return f"Something went wrong: {type(exc).__name__}."
        if result.tool_calls:
            log.debug("tools used: %s", ", ".join(result.tool_calls))
        return result.text

    # ------------------------------------------------------------- text mode
    def run_text(self) -> None:
        from contextlib import contextmanager

        from rich.console import Console

        self.speaker = PrintSpeaker()
        self.container.speaker = self.speaker
        console = Console()
        name = self.config.app.assistant_name
        status = console.status(f"[bold cyan]{name} is thinking...[/]",
                                 spinner="dots12", spinner_style="cyan")

        @contextmanager
        def suspend_spinner():
            # A confirmation prompt reads stdin while the spinner is still
            # animating — both redraw the same terminal line, which mangles
            # the "[y/N]" prompt and whatever gets typed in response. Stop the
            # spinner for exactly the duration of the prompt, resume after, so
            # a turn with several confirmations still shows it between them.
            status.stop()
            try:
                yield
            finally:
                status.start()

        self.gate.set_confirmer(ConsoleConfirmer(suspend=suspend_spinner))

        print(f"{name} 3.0 - text mode. Ctrl-C or 'quit' to exit.\n")
        while not self.stopping.is_set():
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in ("quit", "exit", "bye"):
                break
            status.start()
            try:
                reply = self.handle(line)
            finally:
                status.stop()
            self.speaker.say(reply)
            log.debug("usage: %s", self.brain.usage_summary())

    # ------------------------------------------------------------ voice mode
    def run_voice(self) -> None:
        from peter.ui.confirm import VoiceConfirmer
        from peter.ui.tray import Tray
        from peter.voice.audio import Microphone
        from peter.voice.stt import Transcriber
        from peter.voice.tts import Speaker
        from peter.voice.wake import WakeWordDetector

        self.speaker = Speaker()
        self.container.speaker = self.speaker

        self.mic = Microphone()
        self.mic.start()

        self.transcriber = Transcriber()
        self.transcriber.calibrate(self.mic)
        self.detector = WakeWordDetector()

        self.gate.set_confirmer(
            VoiceConfirmer(self.speaker, self.transcriber, self.mic)
        )

        self.tray = Tray(on_quit=self.stopping.set)
        self.tray.start()

        log.info("listening for the wake word (%s)", self.detector.model_name)
        self.speaker.say(f"{self.config.app.assistant_name} here.")

        while not self.stopping.is_set():
            try:
                self._voice_tick()
            except KeyboardInterrupt:
                break
            except Exception:
                # One bad turn must not end the session.
                log.exception("voice loop error")
                self.tray.set_state("idle")
                time.sleep(0.5)

    def _voice_tick(self) -> None:
        if self.tray.paused:
            time.sleep(0.3)
            return

        frame = self.mic.read(timeout=0.5)
        if frame is None:
            return

        if self.speaker.is_speaking:
            # Barge-in: the wake word during a reply cuts it off and listens.
            if self.detector.process(frame):
                self.speaker.stop()
            else:
                return
        elif not self.detector.process(frame):
            return

        started = time.perf_counter()
        self.tray.set_state("listening")
        self.mic.flush()

        heard = self.transcriber.listen(self.mic)
        if not heard:
            self.tray.set_state("idle")
            return

        log.info("heard: %s", heard)
        self.tray.set_state("thinking")
        reply = self.handle(heard)

        self.tray.set_state("speaking")
        log.info("saying: %s", reply)
        self.speaker.say(reply)
        log.debug(
            "wake to first audio: %.0fms | %s",
            (time.perf_counter() - started) * 1000,
            self.brain.usage_summary(),
        )

        self.speaker.wait_until_idle(timeout=120)
        self.detector.reset()
        self.mic.flush()
        self.tray.set_state("idle")


# ------------------------------------------------------------------ commands
def _cmd_devices() -> int:
    from peter.voice.audio import list_devices

    print(list_devices())
    return 0


def _cmd_health(config: Config) -> int:
    registry.load_all_tools(config=config)
    container = ServiceContainer(config)
    container.memory = MemoryStore(config.db_path)
    container.scheduler = Scheduler(config.db_path)
    set_container(container)

    print(f"\n{config.app.assistant_name} 3.0 - health check\n")
    report = container.health()
    width = max(len(k) for k in report)
    failed = 0
    for name, status in report.items():
        # Statuses carry detail now ("ok - claude-opus-5 (active)"), so match
        # the prefix rather than the whole string.
        ok = status.startswith("ok")
        mark = "ok " if ok else "-- "
        if not ok and status != "disabled":
            failed += 1
        print(f"  [{mark}] {name.ljust(width)}  {status}")

    print(f"\n  {len(registry.all_records())} tools registered")
    print()
    container.close()
    set_container(None)
    return 0 if failed == 0 else 1


def _cmd_google_auth(config: Config) -> int:
    from peter.integrations.google.auth import authorise_interactively

    try:
        print(authorise_interactively(config))
    except PeterError as exc:
        print(f"\nFailed: {exc}")
        if getattr(exc, "user_action", ""):
            print(f"\n{exc.user_action}")
        return 1
    return 0


def _cmd_briefing(config: Config) -> int:
    registry.load_all_tools(config=config)
    container = ServiceContainer(config)
    container.memory = MemoryStore(config.db_path)
    container.scheduler = Scheduler(config.db_path)
    set_container(container)
    print()
    print(build_briefing())
    print()
    container.close()
    set_container(None)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="peter", description="Peter 3.0")
    parser.add_argument("--text", action="store_true",
                        help="run as a CLI assistant: type instead of speaking")
    parser.add_argument("--voice", action="store_true",
                        help="run as a voice assistant (mic + wake word + TTS)")
    parser.add_argument("--provider", choices=("anthropic", "openai", "gemini"),
                        help="override the LLM provider for this run")
    parser.add_argument("--devices", action="store_true",
                        help="list audio devices and exit")
    parser.add_argument("--health", action="store_true",
                        help="check every subsystem and exit")
    parser.add_argument("--google-auth", action="store_true",
                        help="authorise Google Calendar and Tasks")
    parser.add_argument("--briefing", action="store_true",
                        help="print today's briefing and exit")
    args = parser.parse_args(argv)

    # Windows consoles default to cp1252, which cannot encode a rupee sign or
    # an em dash. Peter speaks about prices constantly, so a print must never
    # be able to kill the process.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        config = get_config()
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 2

    configure_logging(
        level=config.app.log_level,
        fmt=config.app.log_format,
        log_file=config.data_dir / "peter.log",
        # The real values, so no formatting mistake anywhere can leak one.
        secret_literals=(
            config.secrets.anthropic_key,
            config.secrets.mail_password,
            config.secrets.google_secret,
        ),
    )

    if args.devices:
        return _cmd_devices()
    if args.google_auth:
        return _cmd_google_auth(config)
    if args.health:
        return _cmd_health(config)
    if args.briefing:
        return _cmd_briefing(config)

    if not config.secrets.any_llm_key:
        log.error(
            "No LLM API key set. Copy .env.example to .env and fill in at least "
            "one of ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY."
        )
        return 1

    # Neither mode was named on the command line — ask once, interactively,
    # instead of silently defaulting to a mic that may not be set up. Only
    # matters for a live terminal; scripts should always pass --text/--voice.
    text_mode = args.text
    if not args.text and not args.voice:
        text_mode = _prompt_for_mode(config.app.assistant_name)

    try:
        peter = Peter(config, text_mode=text_mode, provider=args.provider)
    except PeterError as exc:
        log.error("%s", exc)
        if getattr(exc, "user_action", ""):
            log.error("%s", exc.user_action)
        return 1
    try:
        peter.start()
        if text_mode:
            peter.run_text()
        else:
            peter.run_voice()
    except KeyboardInterrupt:
        pass
    finally:
        peter.shutdown()
    return 0


def _prompt_for_mode(assistant_name: str) -> bool:
    """Ask how to run this session. Returns True for text (CLI) mode.

    CLI mode is everything voice mode is except listening and speaking —
    same brain, same tools, same memory, same policy gate. Output is printed,
    not spoken.
    """
    print(f"\nHow should {assistant_name} run this session?")
    print("  [1] CLI assistant  - type input, read replies, no mic/speakers")
    print("  [2] Voice assistant - wake word, microphone, spoken replies")
    try:
        choice = input("Choose 1 or 2 [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        choice = "1"
    return choice != "2"


if __name__ == "__main__":
    sys.exit(main())
