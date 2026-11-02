import os

import pytest


@pytest.fixture(autouse=True)
def runtime_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("KEPLERV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KEPLERV_RUNTIME_TOKEN", "test-runtime-token-with-32-characters")
