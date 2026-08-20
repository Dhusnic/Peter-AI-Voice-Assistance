"""Semantic memory search: hybrid retrieval, preference scoping, and the
fallbacks that keep a missing model from mattering.

Most tests here use a deterministic `FakeEmbedder` rather than the real ONNX
model, so they run without a 23MB download and so similarity is exactly
controllable instead of approximately so. The handful of tests that need the
real thing are marked and skip themselves when it is not present.

The properties worth protecting, in order:

  1. With no embedder, everything behaves exactly as it did before this
     existed — an optional feature that breaks the base case is not optional.
  2. A relevance threshold means an unrelated turn retrieves *nothing*,
     rather than padding the prompt with the least-bad match.
  3. An `always` preference is never dropped by retrieval, because a
     preference that silently stops applying is worse than a wasted token.
"""

from __future__ import annotations

import numpy as np
import pytest

from peter.core.config import load_config
from peter.memory import embeddings
from peter.memory.store import SCOPE_ALWAYS, SCOPE_CONTEXTUAL, MemoryStore


class FakeEmbedder:
    """Maps text to a unit vector on one axis, chosen by keyword.

    Deterministic and exactly controllable: two texts on the same axis have
    similarity 1.0, on different axes 0.0. That makes threshold behaviour
    testable without depending on what a real model happens to score.
    """

    AXES = {
        "transport": ("bus", "commute", "work", "travel", "route"),
        "food": ("coffee", "eat", "drink", "lunch", "sugar"),
        "health": ("doctor", "medicine", "allergy", "penicillin", "physician"),
    }

    def __init__(self, available: bool = True):
        self._available = available

    def available(self) -> bool:
        return self._available

    def encode_one(self, text: str):
        vectors = self.encode([text])
        return None if vectors is None else vectors[0]

    def encode(self, texts):
        if not self._available:
            return None
        out = []
        for text in texts:
            lowered = text.lower()
            vec = np.zeros(len(self.AXES) + 1, dtype=np.float32)
            for i, (_, words) in enumerate(self.AXES.items()):
                if any(w in lowered for w in words):
                    vec[i] = 1.0
                    break
            else:
                vec[-1] = 1.0  # "unrelated" axis
            out.append(vec)
        return np.vstack(out)


@pytest.fixture
def semantic_store(tmp_path):
    store = MemoryStore(tmp_path / "sem.db", embedder=FakeEmbedder())
    yield store
    store.close()


# ------------------------------------------------- fallback when unavailable
def test_a_store_with_no_embedder_still_works(store):
    """The base case. An optional feature that breaks it is not optional."""
    store.set_fact("commute", "Takes route 70 bus to Gandhipuram")
    assert store._embedding_ready() is False
    assert ("commute", "Takes route 70 bus to Gandhipuram") in store.search_facts_hybrid("bus route")


def test_an_unavailable_embedder_falls_back_to_keywords(tmp_path):
    s = MemoryStore(tmp_path / "x.db", embedder=FakeEmbedder(available=False))
    try:
        s.set_fact("commute", "Takes route 70 bus to Gandhipuram")
        assert s._embedding_ready() is False
        assert [k for k, _ in s.search_facts_hybrid("bus")] == ["commute"]
    finally:
        s.close()


def test_an_embedder_that_raises_does_not_break_retrieval(tmp_path):
    class Exploding:
        def available(self): return True
        def encode_one(self, text): raise RuntimeError("model died")
        def encode(self, texts): raise RuntimeError("model died")

    s = MemoryStore(tmp_path / "y.db", embedder=Exploding())
    try:
        s.set_fact("commute", "Takes route 70 bus to Gandhipuram")  # must not raise
        assert [k for k, _ in s.search_facts_hybrid("bus")] == ["commute"]
    finally:
        s.close()


# -------------------------------------------------------- hybrid retrieval
def test_semantic_search_finds_a_paraphrase_keywords_cannot(semantic_store):
    """The whole reason this exists: 'how do I get to work' shares no words
    with 'route 70 bus', so FTS5 alone returns nothing."""
    semantic_store.set_fact("commute", "Takes route 70 bus to Gandhipuram")

    assert semantic_store.search_facts("how do I get to work") == []          # keyword: miss
    hits = semantic_store.search_facts_hybrid("how do I get to work", threshold=0.5)
    assert [k for k, _ in hits] == ["commute"]                            # hybrid: hit


def test_keyword_still_finds_an_exact_string_embeddings_are_bad_at(semantic_store):
    """The other half of the union. A registration number is not something a
    sentence model has a useful vector for, but FTS5 matches it exactly."""
    semantic_store.set_fact("car", "Drives a red Maruti Swift TN37BQ4521")
    hits = semantic_store.search_facts_hybrid("TN37BQ4521", threshold=0.5)
    assert [k for k, _ in hits] == ["car"]


def test_nothing_relevant_retrieves_nothing(semantic_store):
    """The threshold's real job — an unrelated turn must not pad the prompt
    with the least-bad match."""
    semantic_store.set_fact("commute", "Takes route 70 bus to Gandhipuram")
    semantic_store.set_fact("allergy", "Reacts badly to penicillin")

    assert semantic_store.search_facts_hybrid("quantum entanglement", threshold=0.5) == []


def test_hybrid_results_are_deduplicated(semantic_store):
    """A fact found by both halves must appear once, not twice."""
    semantic_store.set_fact("coffee", "Filter coffee with no sugar")
    hits = semantic_store.search_facts_hybrid("coffee", threshold=0.5)
    assert [k for k, _ in hits].count("coffee") == 1


def test_the_limit_is_respected(semantic_store):
    for i in range(10):
        semantic_store.set_fact(f"bus_{i}", f"Takes the bus, route {i}")
    assert len(semantic_store.search_facts_hybrid("commute", limit=3, threshold=0.5)) == 3


# ------------------------------------------------------ preference scoping
def test_preferences_default_to_always(semantic_store):
    semantic_store.set_preference("tone", "Be direct.")
    assert semantic_store.preference_scopes()["tone"] == SCOPE_ALWAYS
    assert ("tone", "Be direct.") in semantic_store.preferences_for("anything at all")


def test_a_preference_stored_before_scopes_existed_is_treated_as_always(semantic_store):
    """Backward compatibility, checked by writing the row the old code would
    have written — no preference_scope entry at all."""
    semantic_store._conn.execute(
        "INSERT INTO preferences (key, value, created_at, updated_at) VALUES (?,?,?,?)",
        ("legacy", "An old standing rule.", 0.0, 0.0),
    )
    semantic_store._conn.commit()

    assert semantic_store.preference_scopes().get("legacy") is None
    assert ("legacy", "An old standing rule.") in semantic_store.preferences_for("unrelated")


def test_an_always_preference_survives_an_unrelated_turn(semantic_store):
    """The one that matters most: 'keep replies short' applies to every turn,
    so retrieval must never be able to drop it."""
    semantic_store.set_preference("reply_length", "Keep replies short.", SCOPE_ALWAYS)
    semantic_store.set_preference("shop", "Prefer Amazon.", SCOPE_CONTEXTUAL)

    chosen = dict(semantic_store.preferences_for("what medicine should I avoid", threshold=0.5))
    assert "reply_length" in chosen
    assert "shop" not in chosen          # contextual, and this turn is not about it


def test_a_contextual_preference_is_retrieved_when_relevant(semantic_store):
    semantic_store.set_preference("coffee_pref", "Prefer filter coffee.", SCOPE_CONTEXTUAL)
    chosen = dict(semantic_store.preferences_for("what should I drink", threshold=0.5))
    assert "coffee_pref" in chosen


def test_an_unknown_scope_falls_back_to_always(semantic_store):
    """Safe direction: a typo must not silently stop a preference applying."""
    semantic_store.set_preference("tone", "Be direct.", "nonsense")
    assert semantic_store.preference_scopes()["tone"] == SCOPE_ALWAYS


def test_deleting_a_preference_clears_its_scope_and_vector(semantic_store):
    semantic_store.set_preference("shop", "Prefer Amazon.", SCOPE_CONTEXTUAL)
    assert semantic_store.delete_preference("shop") is True

    assert "shop" not in semantic_store.preference_scopes()
    left = semantic_store._conn.execute(
        "SELECT COUNT(*) c FROM preference_vectors WHERE key='shop'"
    ).fetchone()["c"]
    assert left == 0


# --------------------------------------------------------------- reindexing
def test_reindex_backfills_facts_stored_without_an_embedder(tmp_path):
    """Facts saved before the model was downloaded have no vectors. Without a
    backfill the feature would look like it worked while silently missing
    everything Peter already knew."""
    path = tmp_path / "backfill.db"
    plain = MemoryStore(path)
    plain.set_fact("commute", "Takes route 70 bus to Gandhipuram")
    plain.close()

    upgraded = MemoryStore(path, embedder=FakeEmbedder())
    try:
        assert upgraded.search_facts_hybrid("how do I get to work", threshold=0.5) == []
        assert upgraded.reindex_embeddings() >= 1
        hits = upgraded.search_facts_hybrid("how do I get to work", threshold=0.5)
        assert [k for k, _ in hits] == ["commute"]
    finally:
        upgraded.close()


def test_reindex_is_a_no_op_without_an_embedder(store):
    store.set_fact("a", "b")
    assert store.reindex_embeddings() == 0


# ------------------------------------------------------------ context block
def test_context_block_includes_retrieved_facts_and_always_preferences(semantic_store):
    semantic_store.set_preference("reply_length", "Keep replies short.", SCOPE_ALWAYS)
    semantic_store.set_fact("commute", "Takes route 70 bus to Gandhipuram")

    block = semantic_store.context_block("how do I get to work")

    assert "Keep replies short." in block
    assert "route 70 bus" in block


# ------------------------------------------------------- embedding helpers
def test_blob_round_trip():
    vector = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    assert np.allclose(embeddings.from_blob(embeddings.to_blob(vector)), vector)


def test_mean_pool_ignores_padding():
    """Padding tokens carry real values in the model's output; averaging them
    in drags every vector toward a common point and flattens similarity."""
    hidden = np.array([[[1.0, 0.0], [1.0, 0.0], [-99.0, 99.0]]], dtype=np.float32)
    mask = np.array([[1, 1, 0]], dtype=np.int64)   # third token is padding

    pooled = embeddings._mean_pool(hidden, mask)

    assert np.allclose(pooled, [[1.0, 0.0]])       # the padding did not leak in


def test_mean_pool_output_is_unit_length():
    hidden = np.array([[[3.0, 4.0]]], dtype=np.float32)
    pooled = embeddings._mean_pool(hidden, np.array([[1]], dtype=np.int64))
    assert np.isclose(np.linalg.norm(pooled[0]), 1.0)


def test_embedder_reports_unavailable_when_the_model_is_missing(tmp_path):
    e = embeddings.Embedder(tmp_path / "nope")
    assert e.files_present() is False
    assert e.available() is False          # must not raise


# ------------------------------------------------- the real model, if present
def _real_embedder():
    """The genuinely-downloaded model, found without going through
    `config.embeddings_dir`.

    conftest's autouse `isolated_data_dir` fixture repoints `Config.data_dir`
    at a per-test tmp directory — correctly, so tests never touch real data —
    which would send `embeddings_dir` somewhere the model is not. The model is
    a read-only shared artefact rather than test data, so it is located
    directly instead.
    """
    from peter.core.config import PROJECT_ROOT
    from peter.memory.embeddings import Embedder

    embedder = Embedder(PROJECT_ROOT / "data" / "embeddings")
    return embedder if embedder.files_present() else None


real_model = pytest.mark.skipif(
    _real_embedder() is None,
    reason="embedding model not downloaded (run --download-embeddings)",
)


@real_model
def test_real_model_recalls_a_paraphrase(tmp_path):
    """The measured claim, locked in: keyword search scored 3/10 on these
    paraphrased questions, and returned wrong facts for several. This asserts
    the ones that motivated the feature actually work now."""
    s = MemoryStore(tmp_path / "real.db", embedder=_real_embedder())
    try:
        for k, v in {
            "commute": "Takes route 70 bus to Gandhipuram every weekday morning",
            "gym_days": "Trains at the gym on Monday, Wednesday and Friday evenings",
            "car": "Drives a red Maruti Swift, registration TN 37 BQ 4521",
            "allergy": "Reacts badly to penicillin",
        }.items():
            s.set_fact(k, v)

        for question, expected in [
            ("when do I work out", "gym_days"),
            ("what vehicle do I own", "car"),
            ("which medicines should I avoid", "allergy"),
        ]:
            found = [k for k, _ in s.search_facts_hybrid(question, threshold=0.15)]
            assert expected in found, f"{question!r} did not recall {expected!r}"
    finally:
        s.close()


@real_model
def test_real_model_retrieves_nothing_for_an_unrelated_question(tmp_path):
    s = MemoryStore(tmp_path / "real2.db", embedder=_real_embedder())
    try:
        s.set_fact("allergy", "Reacts badly to penicillin")
        assert s.search_facts_hybrid("what is the capital of France", threshold=0.15) == []
    finally:
        s.close()
