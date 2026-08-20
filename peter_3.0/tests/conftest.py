import sys
from pathlib import Path

# Tests import `peter.*`, which lives at the project root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from peter.core.config import Config, load_config  # noqa: E402
from peter.core.services import ServiceContainer, set_container  # noqa: E402
from peter.memory.store import MemoryStore  # noqa: E402
from peter.policy.audit import AuditLog  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point `Config.data_dir` at a per-test directory, for every test.

    Autouse and applied at the class level on purpose. Almost everything Peter
    persists derives its path from `data_dir` — memory, the scheduler jobstore,
    the audit log, the spend ledger, price watches, workspaces, the document
    index, screenshots, recordings. Redirecting them one fixture at a time
    leaves a gap the moment a test builds its own container, and the failure
    is silent: the suite passes while writing rows into the user's real
    peter.db. (It did exactly that, until this fixture existed.)

    Patching the single property they all resolve through closes every one of
    those paths at once, including the ones added later.
    """
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Config, "data_dir", property(lambda self: data))
    return data


@pytest.fixture
def config() -> Config:
    """The real config/config.yml, so tests catch a broken shipped config."""
    return load_config()


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    s = MemoryStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def audit(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def container(config, store, audit):
    """A container with the local services wired and integrations left unset.

    The lazy feature stores (spend, price watches, workspaces, documents) are
    left alone: `isolated_data_dir` above already puts them under tmp_path,
    since every one of them resolves its path through `config.data_dir`.
    """
    c = ServiceContainer(config)
    c.memory = store
    c.audit = audit
    set_container(c)
    yield c
    c.close()
    set_container(None)
