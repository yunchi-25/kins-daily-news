import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest


@pytest.fixture(autouse=True)
def isolate_env_file(monkeypatch, tmp_path):
    """테스트가 실제 .env(API 키)를 읽지 않도록 한다."""
    from src import config

    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no.env")
