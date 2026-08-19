"""Tests for the audio-adjacent logic that does not need a microphone."""

import numpy as np
import pytest

from peter.ui.confirm import _matches, _spoken_form, _NO, _YES
from peter.voice.audio import FRAME_SAMPLES, rms
from peter.voice.tts import PrintSpeaker, split_sentences


# ------------------------------------------------------------ sentence splitting
def test_each_sentence_is_its_own_chunk():
    """Chunks stream to the speaker one at a time — merging them adds latency."""
    assert split_sentences("Reminder set for seven thirty. Anything else? Ok.") == [
        "Reminder set for seven thirty.",
        "Anything else? Ok.",
    ]


def test_two_word_sentences_are_not_swallowed():
    # "Done." is a single word but has nothing before it to merge into, so it
    # leads its own chunk; "Anything else?" is two words and stands alone.
    assert split_sentences("Done. Anything else?") == ["Done.", "Anything else?"]
    assert split_sentences("It is raining. Take an umbrella.") == [
        "It is raining.",
        "Take an umbrella.",
    ]


def test_single_word_tail_merges_backwards():
    assert split_sentences("The bus leaves at six. Right.") == [
        "The bus leaves at six. Right."
    ]


def test_abbreviations_do_not_split():
    assert split_sentences("It costs Rs. 500 in total.") == [
        "It costs Rs. 500 in total."
    ]
    assert split_sentences("Call Dr. Reddy tomorrow.") == ["Call Dr. Reddy tomorrow."]


def test_newlines_split():
    assert split_sentences("First line\nSecond line here") == [
        "First line",
        "Second line here",
    ]


def test_empty_and_whitespace_are_safe():
    assert split_sentences("") == []
    assert split_sentences("   \n  ") == []
    assert split_sentences(None) == []


def test_text_without_punctuation_is_one_chunk():
    assert split_sentences("no punctuation here at all") == [
        "no punctuation here at all"
    ]


# ------------------------------------------------------------------- levels
def test_rms_of_silence_is_zero():
    assert rms(np.zeros(FRAME_SAMPLES, dtype=np.int16)) == 0.0


def test_rms_of_loud_audio_is_higher_than_quiet():
    quiet = (np.random.randn(FRAME_SAMPLES) * 100).astype(np.int16)
    loud = (np.random.randn(FRAME_SAMPLES) * 8000).astype(np.int16)
    assert rms(loud) > rms(quiet)


def test_rms_is_normalised_to_roughly_one_at_full_scale():
    full = np.full(FRAME_SAMPLES, 32767, dtype=np.int16)
    assert 0.99 <= rms(full) <= 1.01


def test_rms_of_empty_frame_does_not_divide_by_zero():
    assert rms(np.array([], dtype=np.int16)) == 0.0


# -------------------------------------------------------------- confirmation
@pytest.mark.parametrize("word", ["yes", "yeah", "sure", "go ahead", "do it"])
def test_affirmatives_are_recognised(word):
    assert _matches(word, _YES)


@pytest.mark.parametrize("word", ["no", "cancel", "stop", "nope", "never mind"])
def test_negatives_are_recognised(word):
    assert _matches(word, _NO)


def test_leading_word_decides():
    assert _matches("yes do it now", _YES)
    assert _matches("no don't", _NO)


def test_ambiguous_answers_match_neither():
    """An unclear answer must re-ask, never be guessed at."""
    for phrase in ("maybe", "i'm not sure", "hold on what"):
        assert not _matches(phrase, _YES)
        assert not _matches(phrase, _NO)


def test_spoken_form_is_readable():
    spoken = _spoken_form("delete_file(path=D:/notes.txt)")
    assert spoken == "Can I delete file, with path=D:/notes.txt?"


def test_spoken_form_without_arguments():
    assert _spoken_form("lock_workstation()") == "Can I lock workstation?"


def test_spoken_form_truncates_long_arguments():
    spoken = _spoken_form(f"write_file(content={'x' * 500})")
    assert len(spoken) < 200
    assert "and more" in spoken


# ------------------------------------------------------------- print speaker
def test_print_speaker_records_and_never_blocks(capsys):
    speaker = PrintSpeaker()
    speaker.say("Hello there.")
    speaker.stop()

    assert speaker.spoken == ["Hello there."]
    assert speaker.is_speaking is False
    assert speaker.wait_until_idle(timeout=0) is True
    assert "Hello there." in capsys.readouterr().out
