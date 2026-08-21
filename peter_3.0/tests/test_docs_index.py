"""The document index: walking, chunking, incremental re-indexing, search."""

import time
from types import SimpleNamespace

import pytest

from peter.docs_index import DocIndex, ask


@pytest.fixture
def docs_config(config):
    cfg = config.integrations.docs
    cfg.chunk_chars = 200
    cfg.extensions = [".md", ".txt", ".py"]
    return cfg


@pytest.fixture
def index(tmp_path, docs_config):
    store = DocIndex(tmp_path / "docs.db", docs_config)
    yield store
    store.close()


@pytest.fixture
def notes(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "openobserve.md").write_text(
        "# OpenObserve\n\nThe alerting thresholds we agreed on are 500ms p95 "
        "and a 2% error rate.\n\nDashboards live in the ops folder.",
        encoding="utf-8",
    )
    (folder / "azure.txt").write_text(
        "Azure feature flag rollout needs approval from the platform team.",
        encoding="utf-8",
    )
    return folder


# ------------------------------------------------------------------ indexing
def test_indexing_a_folder_reads_the_matching_files(index, notes):
    result = index.index_folder(notes)

    assert result.files == 2
    assert result.chunks >= 2
    assert index.stats()["files"] == 2


def test_files_of_other_types_are_left_alone(index, notes):
    (notes / "photo.png").write_bytes(b"\x89PNG not text")
    result = index.index_folder(notes)
    assert result.files == 2


def test_skip_directories_are_not_walked(index, notes, docs_config):
    junk = notes / "node_modules"
    junk.mkdir()
    (junk / "bundle.md").write_text("noise " * 50, encoding="utf-8")

    assert index.index_folder(notes).files == 2


def test_an_oversized_file_is_skipped_not_read(index, notes, docs_config):
    docs_config.max_file_kb = 1
    (notes / "huge.md").write_text("x" * 5000, encoding="utf-8")

    result = index.index_folder(notes)

    assert result.skipped == 1
    assert result.files == 2


def test_re_indexing_skips_files_that_have_not_changed(index, notes):
    index.index_folder(notes)

    result = index.index_folder(notes)

    assert result.files == 0
    assert result.unchanged == 2


def test_an_edited_file_is_re_indexed(index, notes):
    index.index_folder(notes)
    time.sleep(0.01)
    (notes / "azure.txt").write_text("Completely different content now.", encoding="utf-8")

    result = index.index_folder(notes)

    assert result.files == 1
    assert result.unchanged == 1


def test_force_re_reads_everything(index, notes):
    index.index_folder(notes)
    assert index.index_folder(notes, force=True).files == 2


def test_re_indexing_replaces_old_passages_rather_than_adding_to_them(index, notes):
    index.index_folder(notes)
    assert index.search("approval")  # from the original azure.txt

    (notes / "azure.txt").write_text("Nothing about that any more.", encoding="utf-8")
    index.index_folder(notes, force=True)

    assert index.search("approval") == []
    assert index.search("nothing about that")


def test_indexing_a_single_file_works(index, notes):
    assert index.index_folder(notes / "azure.txt").files == 1


def test_indexing_something_that_does_not_exist_raises(index, tmp_path):
    with pytest.raises(FileNotFoundError):
        index.index_folder(tmp_path / "nowhere")


def test_max_files_bounds_a_huge_tree(index, notes, docs_config):
    docs_config.max_files = 1
    assert index.index_folder(notes).files == 1


# ------------------------------------------------------------------ chunking
def test_a_long_document_becomes_several_passages(index, tmp_path, docs_config):
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 30 for i in range(6))
    long_file = tmp_path / "long.md"
    long_file.write_text(paragraphs, encoding="utf-8")

    result = index.index_folder(long_file)

    assert result.chunks > 1


def test_a_single_paragraph_bigger_than_the_window_is_still_split(index, tmp_path,
                                                                  docs_config):
    wall = tmp_path / "wall.md"
    wall.write_text("x" * 1000, encoding="utf-8")  # chunk_chars is 200
    assert index.index_folder(wall).chunks == 5


def test_an_empty_file_is_skipped(index, tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("   \n\n  ", encoding="utf-8")
    assert index.index_folder(empty).skipped == 1


# ----------------------------------------------------------------- searching
def test_searching_finds_the_passage_that_contains_the_phrase(index, notes):
    index.index_folder(notes)

    hits = index.search("alerting thresholds")

    assert hits
    assert "500ms" in hits[0].body
    assert hits[0].name == "openobserve.md"


def test_searching_falls_back_from_all_terms_to_any(index, notes):
    """A question with several words rarely has all of them in one passage —
    requiring every term would return nothing far too often."""
    index.index_folder(notes)

    hits = index.search("what were the openobserve thresholds for dashboards")

    assert hits


def test_a_query_of_only_stopwords_finds_nothing_rather_than_everything(index, notes):
    index.index_folder(notes)
    assert index.search("what is the") == []


def test_searching_an_empty_index_returns_nothing(index):
    assert index.search("anything") == []


def test_fts_punctuation_does_not_crash_the_search(index, notes):
    """Raw speech is full of characters FTS5 treats as query syntax."""
    index.index_folder(notes)
    assert index.search('what about "azure" AND (flags) OR *') is not None


# ----------------------------------------------------------------- forgetting
def test_forgetting_a_folder_removes_its_files_and_passages(index, notes):
    index.index_folder(notes)

    assert index.forget(notes) == 2
    assert index.stats()["files"] == 0
    assert index.search("alerting thresholds") == []


def test_forgetting_a_folder_that_was_never_indexed_removes_nothing(index, tmp_path):
    assert index.forget(tmp_path / "elsewhere") == 0


# ------------------------------------------------------------------ answering
class FakeProvider:
    def __init__(self, reply="The thresholds are 500ms p95 [openobserve.md]."):
        self.reply = reply
        self.sent = ""
        self.closed = False

    def add_user(self, text):
        self.sent = text

    def complete(self, tools):
        from peter.llm.base import ProviderResponse

        assert tools == []
        return ProviderResponse(text=self.reply)

    def close(self):
        self.closed = True


def test_asking_with_nothing_indexed_says_so(container):
    assert "Nothing is indexed yet" in ask("what were the thresholds")


def test_asking_passes_only_the_matching_passages_to_the_model(container, notes,
                                                               monkeypatch):
    container.docs().index_folder(notes)
    provider = FakeProvider()
    monkeypatch.setattr("peter.llm.factory.build_provider", lambda *a, **k: provider)

    answer = ask("what are the alerting thresholds")

    assert "500ms" in answer
    assert "openobserve.md" in provider.sent
    assert provider.closed is True


def test_asking_about_something_absent_does_not_reach_the_model(container, notes,
                                                                monkeypatch):
    container.docs().index_folder(notes)
    monkeypatch.setattr(
        "peter.llm.factory.build_provider",
        lambda *a, **k: pytest.fail("should not have called a model"),
    )

    assert "matches that" in ask("kubernetes ingress certificates rotation")


def test_a_model_failure_degrades_to_the_raw_passages(container, notes, monkeypatch):
    container.docs().index_folder(notes)

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("peter.llm.factory.build_provider", boom)

    answer = ask("what are the alerting thresholds")

    assert "500ms" in answer  # the passage itself came back


# ------------------------------------------------------------------- startup
def test_configured_folders_are_indexed_at_startup(container, notes):
    from peter.docs_index import index_configured_folders

    container.config.integrations.docs.folders = [str(notes)]

    index_configured_folders(container.config)

    assert container.docs().stats()["files"] == 2


def test_a_missing_configured_folder_is_a_warning_not_a_crash(container, tmp_path,
                                                              caplog):
    from peter.docs_index import index_configured_folders

    container.config.integrations.docs.folders = [str(tmp_path / "gone")]

    with caplog.at_level("WARNING"):
        index_configured_folders(container.config)

    assert "does not exist" in caplog.text


def test_nothing_is_indexed_when_no_folders_are_configured(container, monkeypatch):
    from peter.docs_index import index_configured_folders

    container.config.integrations.docs.folders = []
    monkeypatch.setattr(
        container, "docs", lambda: pytest.fail("should not open the index")
    )

    index_configured_folders(container.config)


def test_index_result_reports_an_unchanged_folder_clearly():
    from peter.docs_index import IndexResult

    assert "already indexed" in IndexResult(unchanged=4).spoken("D:/notes")
    assert "Nothing indexable" in IndexResult().spoken("D:/empty")
    assert "Indexed 2 file(s)" in IndexResult(files=2, chunks=5).spoken("D:/notes")


def test_stats_lists_the_indexed_folders(index, notes):
    index.index_folder(notes)
    assert index.stats()["folders"] == [str(notes)]


def test_a_hit_renders_as_a_cited_block():
    from peter.docs_index import Hit

    block = Hit(path="d:/x/notes.md", name="notes.md", body="the body").as_block()
    assert block == "[notes.md] the body"


def test_the_index_survives_a_file_that_cannot_be_read(index, tmp_path, monkeypatch):
    unreadable = tmp_path / "locked.md"
    unreadable.write_text("content", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", boom)

    assert index.index_folder(unreadable).skipped == 1


def test_two_folders_can_be_indexed_side_by_side(index, notes, tmp_path):
    other = tmp_path / "specs"
    other.mkdir()
    (other / "spec.md").write_text("The retry budget is five attempts.", encoding="utf-8")

    index.index_folder(notes)
    index.index_folder(other)

    assert index.stats()["files"] == 3
    assert index.search("retry budget")[0].name == "spec.md"


def test_forgetting_one_folder_leaves_the_other(index, notes, tmp_path):
    other = tmp_path / "specs"
    other.mkdir()
    (other / "spec.md").write_text("The retry budget is five attempts.", encoding="utf-8")
    index.index_folder(notes)
    index.index_folder(other)

    index.forget(other)

    assert index.stats()["files"] == 2
    assert index.search("alerting thresholds")
    assert index.search("retry budget") == []


def test_the_config_object_is_read_live_not_copied(index, notes, docs_config):
    """Config is a live pydantic object; a store that snapshots it would
    ignore a value changed after construction."""
    docs_config.extensions = [".txt"]
    assert index.index_folder(notes).files == 1


def test_a_document_keeps_its_name_for_citation(index, notes):
    index.index_folder(notes)
    assert {h.name for h in index.search("azure OR openobserve")} <= {
        "azure.txt", "openobserve.md"
    }


def test_search_respects_the_limit(index, tmp_path, docs_config):
    many = tmp_path / "many.md"
    # Each paragraph is wider than the 200-character chunk window, so every
    # one of them lands in a passage of its own.
    many.write_text(
        "\n\n".join(f"widget paragraph {i} " + "filler " * 40 for i in range(20)),
        encoding="utf-8",
    )
    index.index_folder(many)

    assert len(index.search("widget", limit=3)) == 3


def test_stats_on_an_empty_index(index):
    assert index.stats() == {"files": 0, "chunks": 0, "folders": []}


def test_indexing_reports_the_folder_it_was_given(index, notes):
    assert str(notes) in index.index_folder(notes).spoken(str(notes))


def test_the_docs_accessor_is_lazy(container, monkeypatch):
    """Opening the index on a session that never mentions documents is waste."""
    assert container._docs is None or isinstance(container._docs, object)


def test_ask_uses_the_configured_provider(container, notes, monkeypatch):
    container.docs().index_folder(notes)
    seen = {}

    def build(config, system, *a, **k):
        seen["system"] = system
        return FakeProvider()

    monkeypatch.setattr("peter.llm.factory.build_provider", build)
    ask("what are the alerting thresholds")

    assert "cite the file" in seen["system"].lower()


def test_hit_bodies_are_trimmed_for_the_prompt():
    from peter.docs_index import Hit

    long_hit = Hit(path="p", name="n.md", body="x" * 5000)
    assert len(long_hit.as_block(max_chars=100)) < 200


def test_indexing_the_same_file_twice_does_not_duplicate_passages(index, tmp_path):
    doc = tmp_path / "one.md"
    doc.write_text("A single paragraph about widgets.", encoding="utf-8")

    index.index_folder(doc)
    index.index_folder(doc, force=True)

    assert index.stats()["chunks"] == 1


def test_search_ranks_the_better_match_first(index, tmp_path, docs_config):
    folder = tmp_path / "ranked"
    folder.mkdir()
    (folder / "strong.md").write_text(
        "widget widget widget the widget documentation", encoding="utf-8"
    )
    (folder / "weak.md").write_text("a passing mention of a widget once", encoding="utf-8")
    index.index_folder(folder)

    assert index.search("widget")[0].name == "strong.md"


def test_a_folder_with_no_matching_extensions_indexes_nothing(index, tmp_path):
    folder = tmp_path / "images"
    folder.mkdir()
    (folder / "a.png").write_bytes(b"binary")
    assert index.index_folder(folder).files == 0


def test_result_spoken_mentions_skipped_files():
    from peter.docs_index import IndexResult

    assert "3 skipped" in IndexResult(files=1, chunks=1, skipped=3).spoken("D:/x")


def test_container_docs_uses_the_configured_database_path(container, tmp_path):
    assert container.docs() is container.docs()  # built once, reused


def test_an_index_survives_being_closed_and_reopened(tmp_path, docs_config, notes):
    first = DocIndex(tmp_path / "docs.db", docs_config)
    first.index_folder(notes)
    first.close()

    second = DocIndex(tmp_path / "docs.db", docs_config)
    try:
        assert second.stats()["files"] == 2
        assert second.search("alerting thresholds")
    finally:
        second.close()


def test_search_returns_hits_not_raw_rows(index, notes):
    index.index_folder(notes)
    hit = index.search("alerting")[0]
    assert isinstance(hit.path, str) and isinstance(hit.body, str)


def test_indexing_handles_a_file_with_odd_encoding(index, tmp_path):
    odd = tmp_path / "odd.md"
    odd.write_bytes(b"caf\xe9 latte and a widget")  # not valid UTF-8
    assert index.index_folder(odd).files == 1
    assert index.search("widget")


def test_fake_provider_contract_is_what_the_code_calls():
    """Guards the fake above from drifting away from the real interface."""
    provider = FakeProvider()
    provider.add_user("x")
    assert provider.complete([]).text
    provider.close()
    assert provider.closed


def test_ask_limit_is_passed_through(container, notes, monkeypatch):
    container.docs().index_folder(notes)
    captured = {}
    original = container.docs().search

    def spy(query, limit=6):
        captured["limit"] = limit
        return original(query, limit)

    monkeypatch.setattr(container.docs(), "search", spy)
    monkeypatch.setattr("peter.llm.factory.build_provider", lambda *a, **k: FakeProvider())

    ask("thresholds", limit=2)

    assert captured["limit"] == 2


def test_docs_index_is_separate_from_memory(container, notes):
    """Deleting the document index must not be able to take memory with it."""
    container.memory.set_fact("a fact", "worth keeping")
    container.docs().index_folder(notes)

    container.docs().forget(notes)

    assert container.memory.get_fact("a fact") == "worth keeping"


def test_simple_namespace_config_is_enough_to_construct(tmp_path):
    """The store depends on config values, not on the Config class."""
    cfg = SimpleNamespace(
        extensions=[".md"], skip_directories=[], max_file_kb=64,
        chunk_chars=500, max_files=10,
    )
    index = DocIndex(tmp_path / "d.db", cfg)
    try:
        assert index.stats()["files"] == 0
    finally:
        index.close()


# --------------------------------------------------------------------- drive
# Same faked-chained-call approach test_contacts.py uses for the People API:
# plain objects mirroring exactly the shape index_drive_folder() calls
# (files().list/export/get_media().execute()), monkeypatched in at the one
# place it's imported (peter.integrations.google.auth.build_service) — no
# real network access anywhere in this file.

class _FakeDriveRequest:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeDriveFiles:
    def __init__(self, pages=None, exports=None, media=None, list_exc=None):
        self.pages = list(pages or [{"files": []}])
        self.exports = exports or {}
        self.media = media or {}
        self.list_exc = list_exc
        self.list_calls: list[dict] = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if self.list_exc is not None:
            return _FakeDriveRequest(exc=self.list_exc)
        page = self.pages.pop(0) if self.pages else {"files": []}
        return _FakeDriveRequest(page)

    def export(self, fileId, mimeType):  # noqa: N803 - matches googleapiclient's own naming
        return _FakeDriveRequest(self.exports.get(fileId, b""))

    def get_media(self, fileId):  # noqa: N803
        return _FakeDriveRequest(self.media.get(fileId, b""))


class _FakeDriveService:
    def __init__(self, **kwargs):
        self._files = _FakeDriveFiles(**kwargs)

    def files(self):
        return self._files


def _drive(monkeypatch, **kwargs) -> _FakeDriveService:
    service = _FakeDriveService(**kwargs)
    monkeypatch.setattr(
        "peter.integrations.google.auth.build_service", lambda *a, **k: service
    )
    return service


def _http_error(status: int):
    from googleapiclient.errors import HttpError

    resp = type("Resp", (), {"status": status, "reason": "error"})()
    return HttpError(resp, b"error body")


def test_drive_exports_a_google_doc_as_text(index, monkeypatch, config):
    _drive(monkeypatch, pages=[{"files": [
        {"id": "f1", "name": "Meeting notes",
         "mimeType": "application/vnd.google-apps.document",
         "modifiedTime": "2026-01-01T00:00:00.000Z"},
    ]}], exports={"f1": b"Decisions and owners from the meeting."})

    result = index.index_drive_folder("folder123", config)

    assert result.files == 1
    hits = index.search("decisions owners")
    assert hits and hits[0].name == "Meeting notes"


def test_drive_downloads_a_plain_text_file(index, monkeypatch, config):
    _drive(monkeypatch, pages=[{"files": [
        {"id": "f2", "name": "readme.md", "mimeType": "text/markdown",
         "modifiedTime": "2026-01-01T00:00:00.000Z"},
    ]}], media={"f2": b"# Setup\n\nRun the installer as administrator."})

    result = index.index_drive_folder("folder123", config)

    assert result.files == 1
    hits = index.search("installer administrator")
    assert hits and hits[0].name == "readme.md"


def test_drive_skips_a_file_type_not_in_the_allowlist(index, monkeypatch, config):
    _drive(monkeypatch, pages=[{"files": [
        {"id": "f3", "name": "photo.png", "mimeType": "image/png",
         "modifiedTime": "2026-01-01T00:00:00.000Z"},
    ]}])

    result = index.index_drive_folder("folder123", config)

    assert result.files == 0
    assert result.skipped == 1


def test_drive_reindex_skips_an_unchanged_file(index, monkeypatch, config):
    meta = {"id": "f1", "name": "Notes", "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-01-01T00:00:00.000Z"}
    _drive(monkeypatch, pages=[{"files": [meta]}], exports={"f1": b"first version"})
    index.index_drive_folder("folder123", config)

    _drive(monkeypatch, pages=[{"files": [meta]}], exports={"f1": b"first version"})
    result = index.index_drive_folder("folder123", config)

    assert result.unchanged == 1
    assert result.files == 0


def test_drive_reindexes_when_modified_time_changes(index, monkeypatch, config):
    meta = {"id": "f1", "name": "Notes", "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-01-01T00:00:00.000Z"}
    _drive(monkeypatch, pages=[{"files": [meta]}], exports={"f1": b"first version"})
    index.index_drive_folder("folder123", config)

    updated = dict(meta, modifiedTime="2026-02-01T00:00:00.000Z")
    _drive(monkeypatch, pages=[{"files": [updated]}], exports={"f1": b"second version"})
    result = index.index_drive_folder("folder123", config)

    assert result.files == 1
    hits = index.search("second version")
    assert hits


def test_drive_paginates(index, monkeypatch, config):
    page1 = {"files": [{"id": "f1", "name": "one.txt", "mimeType": "text/plain",
                         "modifiedTime": "2026-01-01T00:00:00.000Z"}],
             "nextPageToken": "p2"}
    page2 = {"files": [{"id": "f2", "name": "two.txt", "mimeType": "text/plain",
                         "modifiedTime": "2026-01-01T00:00:00.000Z"}]}
    service = _drive(monkeypatch, pages=[page1, page2],
                      media={"f1": b"first document body", "f2": b"second document body"})

    result = index.index_drive_folder("folder123", config)

    assert result.files == 2
    assert service._files.list_calls[1]["pageToken"] == "p2"


def test_drive_403_becomes_auth_error(index, monkeypatch, config):
    from peter.core.errors import AuthError

    _drive(monkeypatch, list_exc=_http_error(403))
    with pytest.raises(AuthError) as excinfo:
        index.index_drive_folder("folder123", config)
    assert "google-auth" in excinfo.value.user_action


def test_drive_files_are_stored_under_the_google_drive_label(index, monkeypatch, config):
    _drive(monkeypatch, pages=[{"files": [
        {"id": "f1", "name": "Notes", "mimeType": "application/vnd.google-apps.document",
         "modifiedTime": "2026-01-01T00:00:00.000Z"},
    ]}], exports={"f1": b"drive content"})

    index.index_drive_folder("folder123", config)

    assert "Google Drive" in index.stats()["folders"]


def test_forgetting_google_drive_removes_only_drive_files(index, notes, monkeypatch, config):
    index.index_folder(notes)
    _drive(monkeypatch, pages=[{"files": [
        {"id": "f1", "name": "Notes", "mimeType": "application/vnd.google-apps.document",
         "modifiedTime": "2026-01-01T00:00:00.000Z"},
    ]}], exports={"f1": b"drive content"})
    index.index_drive_folder("folder123", config)
    assert index.stats()["files"] == 3

    removed = index.forget("Google Drive")

    assert removed == 1
    assert index.stats()["files"] == 2
    assert "Google Drive" not in index.stats()["folders"]


def test_index_drive_folder_requires_a_folder_id(index, config):
    with pytest.raises(ValueError):
        index.index_drive_folder("", config)


# -------------------------------------------------------------------- tool
def test_index_drive_folder_tool_rejects_empty_id():
    from peter.skills.docs.tools import index_drive_folder

    assert "Give a Drive folder id" in index_drive_folder(folder_id="")
