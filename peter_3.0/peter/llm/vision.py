"""Looking at an image: the screen, a browser page, a file on disk.

Deliberately *not* part of `LLMProvider`. That interface models one long
conversation with history, caching and tools; an image question is the
opposite — one call, no tools, no history, and an input you never want re-sent
on subsequent turns. Bolting it on would mean every later turn in the
conversation carrying a megapixel screenshot in its context forever.

So this is a one-shot call per vendor, with the same shape three times over.
The vendor differences are small but real and none of them are worth a shared
abstraction:

    anthropic   image block with base64 source + media type
    openai      Responses API input_image taking a data: URL
    gemini      a Part carrying raw bytes and a mime type

**Images are resized before sending.** A 3840×2160 screen grab costs several
times what a 1600px-wide one does and reads no better — text stays legible
well below native resolution, and the model is reading, not pixel-peeping.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path

from peter.core.errors import IntegrationError, NotConfiguredError
from peter.llm import factory

log = logging.getLogger(__name__)

_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}


def capture_screen(config, region: str = "") -> Path:
    """Grab the screen to a JPEG sized for a model, and return the path.

    Args:
        config: the loaded Config.
        region: "" for every monitor, "primary" for just the main one.
    """
    from PIL import ImageGrab

    cfg = config.agent.vision
    image = ImageGrab.grab(all_screens=region != "primary")

    if image.width > cfg.max_width:
        height = round(image.height * cfg.max_width / image.width)
        image = image.resize((cfg.max_width, height))

    path = config.screenshot_dir / f"look-{datetime.now():%Y%m%d-%H%M%S}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, "JPEG", quality=cfg.jpeg_quality)
    return path


def shrink(path: Path, config) -> tuple[bytes, str]:
    """Read an image, resizing it down if it is larger than needed.

    Returns the bytes to send and their media type. A file already small
    enough is passed through untouched — re-encoding a screenshot as JPEG a
    second time only adds artefacts to the text the model is trying to read.
    """
    cfg = config.agent.vision
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "image/png")
    raw = path.read_bytes()

    try:
        from PIL import Image

        with Image.open(path) as image:
            if image.width <= cfg.max_width:
                return raw, media_type
            height = round(image.height * cfg.max_width / image.width)
            resized = image.convert("RGB").resize((cfg.max_width, height))

        import io

        buffer = io.BytesIO()
        resized.save(buffer, "JPEG", quality=cfg.jpeg_quality)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        log.debug("could not resize %s, sending as-is", path, exc_info=True)
        return raw, media_type


def describe_image(path: Path, question: str, config) -> str:
    """Ask the configured vision model about one image."""
    cfg = config.agent.vision
    if not cfg.enabled:
        return "Looking at images is switched off in config.yml."

    path = Path(path)
    if not path.exists():
        raise IntegrationError(f"{path} does not exist", service="vision")

    provider = (cfg.provider or config.agent.provider).strip().lower()
    key = factory.api_key_for(config, provider)
    if not key:
        raise NotConfiguredError(
            provider, f"No API key for {provider}, which agent.vision is set to use."
        )

    data, media_type = shrink(path, config)
    model = cfg.model or _default_model(config, provider)
    question = question.strip() or "Describe what is on this screen."

    log.info("vision: asking %s/%s about %s", provider, model, path.name)
    if provider == "anthropic":
        return _ask_anthropic(key, model, data, media_type, question, cfg.max_tokens)
    if provider == "openai":
        return _ask_openai(key, model, data, media_type, question, cfg.max_tokens)
    return _ask_gemini(key, model, data, media_type, question, cfg.max_tokens)


def _default_model(config, provider: str) -> str:
    model = config.agent.models.get(provider, "")
    if provider == "gemini" and model.strip().lower() == "auto":
        # "auto" is a routing instruction for the conversation loop, not a
        # model name. Reading an image is a light task; use the light model.
        return config.agent.gemini_auto.light_model
    return model


# ---------------------------------------------------------------- the vendors
#
# Each of these binds its client to a local name before calling, rather than
# the tidier `Client(api_key=key).models.generate(...)`. That one-liner is a
# real bug: the client is a temporary whose refcount drops to zero as soon as
# the sub-object is fetched, so CPython can finalise it — closing the
# underlying HTTP connection pool — while the request is still in flight. It
# fails as "Cannot send a request, as the client has been closed", which names
# neither the cause nor this line.
def _ask_anthropic(key, model, data, media_type, question, max_tokens) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(data).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": question},
                ],
            }],
        )
    except Exception as exc:
        raise IntegrationError(f"anthropic vision call failed: {exc}",
                               service="anthropic", recoverable=True) from exc

    return "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()


def _ask_openai(key, model, data, media_type, question, max_tokens) -> str:
    from openai import OpenAI

    encoded = base64.b64encode(data).decode("ascii")
    client = OpenAI(api_key=key)
    try:
        response = client.responses.create(
            model=model,
            max_output_tokens=max_tokens,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": question},
                    {"type": "input_image",
                     "image_url": f"data:{media_type};base64,{encoded}"},
                ],
            }],
        )
    except Exception as exc:
        raise IntegrationError(f"openai vision call failed: {exc}",
                               service="openai", recoverable=True) from exc

    return (getattr(response, "output_text", "") or "").strip()


def _ask_gemini(key, model, data, media_type, question, max_tokens) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=data, mime_type=media_type),
                question,
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                # No tools are passed, so nothing could be called anyway — but
                # the SDK warns loudly about AFC unless it is explicitly off,
                # and the whole codebase's stance is that the SDK never
                # executes anything without passing the permission gate.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
    except Exception as exc:
        raise IntegrationError(f"gemini vision call failed: {exc}",
                               service="gemini", recoverable=True) from exc

    return (getattr(response, "text", "") or "").strip()
