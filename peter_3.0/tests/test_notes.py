"""The personal journal: quick timestamped notes, searchable later."""

import pytest

from peter.notes import NoteStore, spoken


@pytest.fixture
def notes(tmp_path) -> NoteStore:
    store = NoteStore(tmp_path / "notes.db")
    yield store
    store.close()


def test_add_returns_an_id(notes):
    note_id = notes.add("call the plumber tomorrow")
    assert isinstance(note_id, int) and note_id > 0


def test_recent_returns_newest_first(notes):
    notes.add("first note")
    notes.add("second note")
    notes.add("third note")

    rows = notes.recent(limit=10)

    assert [text for _, text, _ in rows] == ["third note", "second note", "first note"]


def test_recent_respects_the_limit(notes):
    for i in range(5):
        notes.add(f"note {i}")

    assert len(notes.recent(limit=2)) == 2


def test_search_finds_a_note_by_keyword(notes):
    notes.add("client demo moved to Friday")
    notes.add("wifi password is on the fridge")

    hits = notes.search("demo")

    assert len(hits) == 1
    assert "client demo" in hits[0][1]


def test_search_with_no_matching_keywords_falls_back_to_recent(notes):
    notes.add("only note here")

    hits = notes.search("the a an")  # all stopwords -> no usable FTS query

    assert len(hits) == 1
    assert hits[0][1] == "only note here"


def test_search_with_no_hits_returns_empty(notes):
    notes.add("something entirely unrelated")

    assert notes.search("nonexistentkeyword12345") == []


def test_delete_removes_a_note(notes):
    note_id = notes.add("temporary note")

    assert notes.delete(note_id) is True
    assert notes.recent() == []


def test_delete_of_unknown_id_returns_false(notes):
    assert notes.delete(9999) is False


def test_deleted_notes_do_not_appear_in_search(notes):
    note_id = notes.add("delete me please")
    notes.delete(note_id)

    assert notes.search("delete") == []


# ------------------------------------------------------------------ spoken
def test_spoken_with_no_rows_says_so():
    assert spoken([]) == "No notes found."


def test_spoken_formats_id_time_and_text():
    text = spoken([(3, "buy milk", 1_700_000_000.0)])
    assert text.startswith("[3]")
    assert "buy milk" in text


# -------------------------------------------------------------------- tools
def test_add_note_tool_stores_and_confirms(container):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import notes_tools  # noqa: F401

    result = registry.get_record("add_note").raw_fn(text="pick up dry cleaning")

    assert "Noted" in result
    assert "pick up dry cleaning" in registry.get_record("recent_notes").raw_fn()


def test_add_note_tool_refuses_empty_text(container):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import notes_tools  # noqa: F401

    result = registry.get_record("add_note").raw_fn(text="   ")

    assert "nothing to note" in result


def test_search_notes_tool_finds_a_stored_note(container):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import notes_tools  # noqa: F401

    registry.get_record("add_note").raw_fn(text="parking code is 4471")
    result = registry.get_record("search_notes").raw_fn(query="parking")

    assert "4471" in result


def test_delete_note_tool_removes_by_id(container):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import notes_tools  # noqa: F401

    add_result = registry.get_record("add_note").raw_fn(text="throwaway note")
    note_id = int(add_result.split("#")[1].rstrip(")."))

    result = registry.get_record("delete_note").raw_fn(note_id=note_id)

    assert f"Deleted note #{note_id}" in result
    assert "throwaway note" not in registry.get_record("recent_notes").raw_fn()


def test_delete_note_tool_reports_unknown_id(container):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import notes_tools  # noqa: F401

    result = registry.get_record("delete_note").raw_fn(note_id=99999)

    assert "No note numbered 99999" in result
