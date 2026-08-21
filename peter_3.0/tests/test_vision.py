"""Looking at an image.

The vendor calls themselves are three thin adapters; what is worth testing is
everything around them — that images are shrunk before being sent, that the
right model is chosen, and that a failure comes back as something speakable
rather than a traceback out of a tool.
"""

from types import SimpleNamespace

import pytest

from peter.core.errors import IntegrationError, NotConfiguredError
from peter.llm import vision


@pytest.fixture
def image(tmp_path):
    from PIL import Image

    path = tmp_path / "wide.png"
    Image.new("RGB", (3840, 2160), "white").save(path)
    return path


@pytest.fixture
def small_image(tmp_path):
    from PIL import Image

    path = tmp_path / "small.png"
    Image.new("RGB", (800, 600), "white").save(path)
    return path


def with_key(config, provider="anthropic"):
    config.agent.provider = provider
    config.agent.vision.provider = ""
    config.agent.vision.model = ""
    config.secrets.anthropic_api_key.get_secret_value = lambda: "key"  # type: ignore[method-assign]
    config.secrets.openai_api_key.get_secret_value = lambda: "key"  # type: ignore[method-assign]
    config.secrets.gemini_api_key.get_secret_value = lambda: "key"  # type: ignore[method-assign]
    return config


# ------------------------------------------------------------------ shrinking
def test_a_wide_image_is_resized_before_being_sent(config, image):
    from PIL import Image

    data, media_type = vision.shrink(image, config)

    assert media_type == "image/jpeg"
    import io

    with Image.open(io.BytesIO(data)) as resized:
        assert resized.width == config.agent.vision.max_width


def test_an_already_small_image_is_sent_untouched(config, small_image):
    """Re-encoding a screenshot as JPEG only adds artefacts to the text the
    model is being asked to read."""
    data, media_type = vision.shrink(small_image, config)

    assert media_type == "image/png"
    assert data == small_image.read_bytes()


def test_an_unreadable_image_is_sent_as_is_rather_than_failing(config, tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not actually a png")

    data, media_type = vision.shrink(broken, config)

    assert data == b"not actually a png"
    assert media_type == "image/png"


def test_the_media_type_follows_the_extension(config, tmp_path):
    from PIL import Image

    jpeg = tmp_path / "photo.jpg"
    Image.new("RGB", (100, 100)).save(jpeg)

    assert vision.shrink(jpeg, config)[1] == "image/jpeg"


# ------------------------------------------------------------ model choice
def test_the_configured_provider_is_used_by_default(config):
    config.agent.provider = "openai"
    assert vision._default_model(config, "openai") == config.agent.models["openai"]


def test_gemini_auto_falls_back_to_the_light_model(config):
    """"auto" is a routing instruction for the conversation loop, not a model
    name — sending it as one would be rejected by the API."""
    config.agent.models["gemini"] = "auto"

    chosen = vision._default_model(config, "gemini")

    assert chosen == config.agent.gemini_auto.light_model


def test_an_explicit_vision_model_wins(config, small_image, monkeypatch):
    with_key(config)
    config.agent.vision.model = "claude-haiku-4-5"
    seen = {}
    monkeypatch.setattr(vision, "_ask_anthropic",
                        lambda key, model, *a: seen.setdefault("model", model) or "ok")

    vision.describe_image(small_image, "what is this", config)

    assert seen["model"] == "claude-haiku-4-5"


def test_the_vision_provider_can_differ_from_the_conversation_one(config, small_image,
                                                                  monkeypatch):
    with_key(config, provider="anthropic")
    config.agent.vision.provider = "gemini"
    called = []
    monkeypatch.setattr(vision, "_ask_gemini", lambda *a: called.append("gemini") or "ok")

    vision.describe_image(small_image, "what is this", config)

    assert called == ["gemini"]


# --------------------------------------------------------------- describing
def test_a_missing_file_is_an_integration_error(config, tmp_path):
    with_key(config)
    with pytest.raises(IntegrationError, match="does not exist"):
        vision.describe_image(tmp_path / "nothing.png", "?", config)


def test_no_api_key_for_the_vision_provider_says_so(config, small_image):
    config.agent.provider = "anthropic"
    config.agent.vision.provider = ""
    config.secrets.anthropic_api_key.get_secret_value = lambda: ""  # type: ignore[method-assign]

    with pytest.raises(NotConfiguredError):
        vision.describe_image(small_image, "?", config)


def test_vision_can_be_switched_off(config, small_image):
    config.agent.vision.enabled = False
    assert "switched off" in vision.describe_image(small_image, "?", config)


def test_an_empty_question_still_asks_something(config, small_image, monkeypatch):
    with_key(config)
    seen = {}
    monkeypatch.setattr(
        vision, "_ask_anthropic",
        lambda key, model, data, media, question, tokens: seen.setdefault("q", question),
    )

    vision.describe_image(small_image, "   ", config)

    assert "Describe what is on this screen" in seen["q"]


def test_a_vendor_failure_becomes_an_integration_error(config, small_image, monkeypatch):
    with_key(config, provider="gemini")

    from google import genai

    class BrokenModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("quota exceeded")

    monkeypatch.setattr(
        genai, "Client", lambda api_key: SimpleNamespace(models=BrokenModels())
    )

    with pytest.raises(IntegrationError, match="gemini vision call failed"):
        vision.describe_image(small_image, "?", config)


# -------------------------------------------------------------- the capture
def test_capturing_the_screen_writes_a_sized_jpeg(config, monkeypatch, tmp_path):
    from PIL import Image, ImageGrab

    monkeypatch.setattr(config, "_secrets", config.secrets)  # keep config intact
    monkeypatch.setattr(ImageGrab, "grab", lambda all_screens=True: Image.new(
        "RGB", (3840, 2160), "white"
    ))
    monkeypatch.setattr(
        type(config), "screenshot_dir", property(lambda self: tmp_path)
    )

    path = vision.capture_screen(config)

    assert path.suffix == ".jpg"
    with Image.open(path) as saved:
        assert saved.width == config.agent.vision.max_width


def test_capturing_only_the_primary_screen_is_possible(config, monkeypatch, tmp_path):
    from PIL import Image, ImageGrab

    seen = {}

    def grab(all_screens=True):
        seen["all_screens"] = all_screens
        return Image.new("RGB", (800, 600), "white")

    monkeypatch.setattr(ImageGrab, "grab", grab)
    monkeypatch.setattr(
        type(config), "screenshot_dir", property(lambda self: tmp_path)
    )

    vision.capture_screen(config, region="primary")

    assert seen["all_screens"] is False


# ----------------------------------------------------------------- the tools
def test_the_screen_tool_returns_the_models_answer(container, monkeypatch, tmp_path):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.vision import tools as vision_tools  # noqa: F401

    monkeypatch.setattr(vision, "capture_screen", lambda config, region="": tmp_path / "x.jpg")
    monkeypatch.setattr(
        vision, "describe_image", lambda path, question, config: "It says AttributeError."
    )

    record = registry.get_record("look_at_screen")
    assert record.raw_fn(question="what's this error") == "It says AttributeError."


def test_the_screen_tool_reports_a_failure_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.vision import tools as vision_tools  # noqa: F401

    def boom(*a, **k):
        raise RuntimeError("no display")

    monkeypatch.setattr(vision, "capture_screen", boom)

    result = registry.get_record("look_at_screen").raw_fn(question="?")

    assert "could not look at the screen" in result
    assert "RuntimeError" in result


def test_the_image_tool_reports_a_missing_file(container):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.vision import tools as vision_tools  # noqa: F401

    result = registry.get_record("look_at_image").raw_fn(
        path="nowhere/at/all.png", question="?"
    )

    assert "There is no file at" in result
