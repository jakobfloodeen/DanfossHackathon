"""Shared test helpers. Tests never need an API key: the LLM is always a fake function."""
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "repository_demo"


@pytest.fixture(autouse=True)
def _no_real_api(monkeypatch, tmp_path):
    """Make sure no test can reach the real API or write into the real cache."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # .env must not put the key back during tests (found when the real key was added)
    monkeypatch.setattr("comparison_engine.llm._load_env", lambda: None)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("REPO_DIR", str(DEMO))
    monkeypatch.setenv("CUSTOMER_DIR", str(tmp_path / "customers"))       # never the real customer files
    monkeypatch.setenv("USAGE_FILE", str(tmp_path / "usage.csv"))           # never the real cost log
    monkeypatch.setenv("COMMERCIAL_FILE", str(tmp_path / "commercial.csv")) # tests opt in to commercial notes
    monkeypatch.setenv("DOCUMENTS_FILE", str(tmp_path / "documents.csv"))  # no links unless a test writes some


@pytest.fixture
def demo_repo() -> Path:
    return DEMO


@pytest.fixture
def repo_copy(tmp_path) -> Path:
    """A writable copy of the demo repository (for tests that add files)."""
    target = tmp_path / "repo"
    shutil.copytree(DEMO, target)
    return target
