import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("PIANO_SCRIBE_STORAGE", str(tmp_path / "storage"))
    monkeypatch.setenv("PIANO_SCRIBE_MAX_UPLOAD_MB", "50")
    return tmp_path
