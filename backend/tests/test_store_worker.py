"""Store robustness: stale recovery, heartbeats, retries."""
import time

from piano_scribe.store import (claim_next_job, create_job, get_job,
                                heartbeat_status, record_heartbeat,
                                recover_stale_jobs, update_job)
from piano_scribe.worker import _after_abort, _looks_transient


def _age_job(jid: str, attempts: int) -> None:
    import sqlite3

    from piano_scribe import store as store_mod
    c = sqlite3.connect(store_mod._db_path())
    c.execute("UPDATE jobs SET updated_at=?, attempts=? WHERE id=?",
              (time.time() - 3600, attempts, jid))
    c.commit()
    c.close()


def test_recover_stale_jobs_requeues_then_fails():
    j = create_job("piano", "/tmp/x.wav", "stale")
    claim_next_job()
    # Fresh working job: untouched.
    assert recover_stale_jobs(older_than_sec=60, max_attempts=3) == {"requeued": 0, "failed": 0}
    # Aged working job with attempts left: requeued.
    _age_job(j["id"], 1)
    assert recover_stale_jobs(older_than_sec=60, max_attempts=3) == {"requeued": 1, "failed": 0}
    assert get_job(j["id"])["status"] == "queued"
    # Aged working job out of attempts: failed with a clear reason.
    claim_next_job()
    _age_job(j["id"], 3)
    out = recover_stale_jobs(older_than_sec=60, max_attempts=3)
    assert out == {"requeued": 0, "failed": 1}
    failed = get_job(j["id"])
    assert failed["status"] == "failed" and "giving up" in failed["error"]


def test_heartbeat_lifecycle():
    assert heartbeat_status(60)["alive"] is False
    record_heartbeat("w1")
    hb = heartbeat_status(60)
    assert hb["alive"] is True and hb["worker_id"] == "w1"
    assert heartbeat_status(0)["alive"] is False  # anything is stale at 0s


def test_after_abort_retries_then_gives_up(monkeypatch):
    import piano_scribe.worker as w
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    j = create_job("piano", "/tmp/x.wav", "abort")
    claim_next_job()
    assert _after_abort(j["id"], 1, 3, "boom", "crashed") == ""
    assert get_job(j["id"])["status"] == "queued"
    assert _after_abort(j["id"], 3, 3, "boom", "crashed") == ""
    failed = get_job(j["id"])
    assert failed["status"] == "failed" and "giving up" in failed["error"]


def test_transient_detection():
    assert _looks_transient("database is locked") is True
    assert _looks_transient("AudioError: corrupt file") is False
    assert _looks_transient("") is False
