import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("PIANO_SCRIBE_STORAGE", str(tmp_path / "storage"))
    monkeypatch.setenv("PIANO_SCRIBE_MAX_UPLOAD_MB", "50")
    monkeypatch.setenv("PIANO_SCRIBE_RATE_UPLOADS_PER_MIN", "1000")
    monkeypatch.setenv("PIANO_SCRIBE_RATE_REQUESTS_PER_MIN", "100000")
    from piano_scribe.api import reset_rate_limits
    reset_rate_limits()
    return tmp_path
