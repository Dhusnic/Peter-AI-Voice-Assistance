# recorder

Local meeting capture, transcription, and summarisation
(`peter/meeting_notes.py`, `peter/integrations/desktop/recorder.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `start_recording` | write | Start capturing audio for a meeting/call. |
| `stop_recording` | write | Stop and transcribe — returns immediately, finishes in the background. |
| `recording_status` | read | Whether something is recording, and for how long. |
| `list_recordings` | read | Saved recordings, newest first, with notes-ready status. |
| `read_meeting_notes` | read | Read back the notes (or raw transcript) from a past recording. |
| `summarise_recording` | write | Re-transcribe/re-summarise a past recording, in the background. |
| `audio_sources` | read | What recording would actually capture from on this machine. |

## Setup

`integrations.recorder.enabled` (default true) is the registry gate.
`RecorderConfig`: `capture_system_audio` (default true — WASAPI loopback,
falls back to mic if the installed `sounddevice` build can't do loopback),
`sample_rate` (default 16000), `max_minutes` (default 180), `stt_model`
(default `"small.en"`, independent of the wake-word pipeline's own model —
a recording is transcribed in the background, so a slower/more accurate
model is affordable), `auto_record_meetings` (default **false**, and stays
off unless deliberately enabled).

## Design notes & gotchas

- **Recording someone is treated as a consequential act throughout this
  module — this shapes every design choice here, not just the auto-record
  default.** Peter only ever records when asked, always announces out loud
  when it starts and what it's capturing from, and never starts one
  silently. Even the one automatic path (`auto_record_meetings`, off by
  default) still announces itself when it fires.
- **Capture order: WASAPI loopback first (the *other* people on the call),
  falling back to the microphone.** Windows exposes any output device as a
  capture device, and `sd.WasapiSettings(loopback=True)` surfaces that
  through PortAudio; older `sounddevice` builds have no such argument, hence
  a runtime fallback rather than a version pin. `stop_recording`'s result
  and `audio_sources` both tell the caller which one it actually got, since
  "captures your side only" is a meaningful difference to someone deciding
  whether the recording was worth making.
- **Disk writes run on their own thread, deliberately.** Blocking inside a
  PortAudio callback produces audible dropouts, and `wave.writeframes` on a
  slow disk absolutely can block. The callback pushes to a bounded queue and
  *drops* rather than blocks when full — 20ms of silence in one place beats
  corrupting everything after it.
- **Transcription is deliberately asynchronous — `stop_recording` returns
  immediately.** An hour of audio takes minutes even on `small.en`; a
  daemon thread transcribes, summarises, writes `.txt`/`.md` next to the
  audio, records a memory episode, then speaks and pushes the result. A
  tool call that blocks the conversation for four minutes is not a tool,
  it's a hang.
- **The audio never leaves the machine for transcription** — `faster-whisper`
  was already installed for the wake-word pipeline, so this is essentially
  free; only the final summary is a model call, and that's over text, not
  audio.
- **The stored episode is what closes the loop with meeting-prep.** Weeks
  later, the proactive meeting-prep nudge (outside this skill) can say
  "your last conversation with Priya was about the thresholds," because a
  real transcript was folded into memory rather than a vague recollection.
- `summarise_recording` spins up its own background thread directly
  (calling `meeting_notes._process()`), separate from the automatic
  post-`stop_recording` pipeline — used for a recording that was never
  transcribed, or whose notes should be redone.
- `read_meeting_notes` falls back to the raw transcript (truncated to 4,000
  chars) if notes were never generated, rather than saying nothing exists.
- Same underlying `transcribe()` function is reused, unmodified, by the
  `phone` skill's `transcribe_phone_voice_note` — no meeting-specific logic
  lives in the transcription function itself.

## Future extension ideas

- No speaker diarization — transcripts are a flat stream of text, not
  attributed to individual speakers, which would need a materially
  different (and heavier) pipeline than plain `faster-whisper`.
- No live/streaming transcript during an in-progress recording — the text
  only exists once `stop_recording` triggers the background pipeline.
