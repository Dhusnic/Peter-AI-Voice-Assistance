# vision

Actually looking at something — the screen, an image file, the browser page —
and answering a question about it (`peter/llm/vision.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `look_at_screen` | read | Capture the screen (all monitors or just the primary) and answer a question. |
| `look_at_image` | read | Look at an image file and answer a question. |
| `look_at_browser_page` | read | Screenshot the current browser page and answer a question. |

## Setup

`agent.vision.enabled` (default true, under `AgentConfig.vision` /
`VisionConfig`) is the registry gate. Also relevant: `provider`/`model`
(empty = whatever `agent.provider` is — every current model from all three
vendors reads images), `max_width` (default 1600px), `jpeg_quality` (default
80), `max_tokens` (default 1200).

## Design notes & gotchas

- **Why this closes a real gap rather than duplicating `take_screenshot`.**
  `system`'s `take_screenshot` saves a PNG and returns a path — useful to a
  human, meaningless to the model, which then has to say "I saved a
  screenshot" and stop. These tools capture, downscale, actually send the
  image to a vision-capable model, and return an answer.
- **Deliberately *not* part of `LLMProvider` / the ongoing conversation —
  this is the single most important design fact about this skill.** The
  provider interface models one long conversation with history, caching and
  tools; an image question is the opposite: one isolated call, no tools, no
  history, an input you never want re-sent. Bolting it onto the main
  conversation would leave a megapixel screenshot sitting in context for the
  rest of the session, re-sent (and re-billed) on every later turn — "the
  most expensive mistake available," per the architecture notes. Do not
  route a vision call through the normal turn loop.
- **Downscaling happens before the image is ever sent** — a 3840px-wide
  capture costs several times what a 1600px one does and reads no better,
  since the model is reading text/content, not pixel-peeping. An image
  already small enough is passed through untouched, since re-encoding a
  screenshot as JPEG only adds compression artefacts to text being read.
- `look_at_screen`'s `primary_only` exists specifically for multi-monitor
  setups, where a wide combined grab makes on-screen text small enough to
  misread.
- `look_at_browser_page` is the fallback tier below `browse_page`/
  `check_price` in the `browser` skill — those read structured data far more
  cheaply; this exists for what they can't answer (a rendered seat map, a
  chart, a layout question, a CAPTCHA-shaped obstacle).
- `look_at_image`'s path resolution: a relative path is resolved against
  `config.data_dir`, not the process's working directory.
- The phone has its own screen-reading tool, `read_phone_screen` in the
  `phone` skill — it pipes an ADB screenshot into this exact same
  `vision.describe_image()` function rather than duplicating the vision
  pipeline.

## Future extension ideas

- No tool crops or zooms into a region of a captured image before sending it
  — a "read that tiny text in the corner" request relies entirely on the
  model reading a downscaled full capture.
- No caching of a repeated "what does the screen say" call within a short
  window — each call is a fresh capture and a fresh (billed) vision call,
  consistent with the "never re-send, never keep in history" design, but
  worth knowing if a user asks the same framing twice in a row.
