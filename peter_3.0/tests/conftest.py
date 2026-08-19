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
def container(config, store, audit, tmp_path):
    """A container with the local services wired and integrations left unset."""
    c = ServiceContainer(config)
    c.memory = store
    c.audit = audit
    set_container(c)
    yield c
    set_container(None)
