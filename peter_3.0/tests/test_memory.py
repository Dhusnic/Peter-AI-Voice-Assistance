def test_facts_round_trip(store):
    store.set_fact("home_city", "Coimbatore")
    assert store.get_fact("home_city") == "Coimbatore"


def test_set_fact_overwrites_same_key(store):
    store.set_fact("home_city", "Coimbatore")
    store.set_fact("home_city", "Chennai")
    assert store.get_fact("home_city") == "Chennai"
    assert len(store.all_facts()) == 1


def test_delete_fact(store):
    store.set_fact("temp", "value")
    assert store.delete_fact("temp") is True
    assert store.delete_fact("temp") is False
    assert store.get_fact("temp") is None


def test_fts_search_finds_by_value(store):
    store.set_fact("usual_groceries", "milk, eggs, bread and filter coffee")
    store.set_fact("home_city", "Coimbatore")

    hits = store.search_facts("what groceries do I usually buy")
    assert ("usual_groceries", "milk, eggs, bread and filter coffee") in hits


def test_fts_survives_punctuation_in_speech(store):
    """FTS5 treats quotes and operators as syntax — raw speech must not crash it."""
    store.set_fact("bus_route", "route 70 to Gandhipuram")
    assert store.search_facts('"OR" AND (NOT) -- route*') is not None
    assert store.search_facts("!!! ??? ***") is not None


def test_search_falls_back_to_recent_when_query_is_all_stopwords(store):
    store.set_fact("a_fact", "something")
    hits = store.search_facts("what is the")
    assert hits == [("a_fact", "something")]


def test_preferences(store):
    store.set_preference("reply_length", "under two sentences")
    assert store.all_preferences() == [("reply_length", "under two sentences")]
    assert store.delete_preference("reply_length") is True
    assert store.all_preferences() == []


def test_todos(store):
    first = store.add_todo("submit DBMS assignment")
    store.add_todo("buy a notebook")

    open_items = store.list_todos()
    assert len(open_items) == 2

    assert store.complete_todo(first) is True
    assert store.complete_todo(first) is False, "already-done items cannot re-complete"

    assert len(store.list_todos()) == 1
    assert len(store.list_todos(include_done=True)) == 2


def test_find_todos_is_case_insensitive(store):
    store.add_todo("Submit DBMS Assignment")
    assert len(store.find_todos("dbms")) == 1


def test_episodes_return_newest_first(store):
    store.add_episode("talked about buses")
    store.add_episode("talked about groceries")
    assert store.recent_episodes(limit=1) == ["talked about groceries"]


def test_context_block_is_empty_when_nothing_stored(store):
    assert store.context_block("hello") == ""


def test_context_block_includes_preferences_and_matching_facts(store):
    store.set_preference("reply_length", "short")
    store.set_fact("bus_route", "route 70 to Gandhipuram")
    store.set_fact("unrelated", "likes filter coffee")

    block = store.context_block("which bus do I take")
    assert "<memory>" in block and "</memory>" in block
    assert "reply_length" in block
    assert "bus_route" in block
