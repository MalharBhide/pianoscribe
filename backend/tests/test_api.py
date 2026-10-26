"""API: config honesty, upload validation, edit/export coherence."""
import io
import math
import os
import struct
import wave

from fastapi.testclient import TestClient

from piano_scribe.api import app
from piano_scribe.score import Note, Score
from piano_scribe.store import save_project

client = TestClient(app)


def test_config_matches_env(monkeypatch):
    monkeypatch.setenv("PIANO_SCRIBE_MAX_UPLOAD_MB", "77")
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["max_upload_mb"] == 77
    assert r.json()["max_upload_bytes"] == 77 * 1024 * 1024


def test_upload_rejects_bad_inputs():
    r = client.post("/api/uploads", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/uploads", files={"file": ("e.wav", b"", "audio/wav")})
    assert r.status_code == 400


def _wav_bytes(sr=22050, secs=1.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        n = int(sr * secs)
        w.writeframes(struct.pack("<%dh" % n, *[int(0.4 * 32767 * math.sin(2 * math.pi * 440 * i / sr)) for i in range(n)]))
    return buf.getvalue()


def test_upload_queues_job_and_project_flow():
    r = client.post("/api/uploads",
                    files={"file": ("t.wav", _wav_bytes(), "audio/wav")},
                    data={"mode": "piano", "density": "standard", "title": "T"})
    assert r.status_code == 200
    jid = r.json()["job_id"]
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "queued" and j["mode"] == "piano"


def test_edit_export_version_coherence():
    s = Score(title="V")
    s.notes = [Note(id="a", pitch=60, start_sec=0.0, end_sec=0.5, hand="R")]
    save_project("prj_x", "V", "piano", "/tmp/none.wav", s.to_dict())
    v0 = client.get("/api/projects/prj_x").json()["score"]["version"]
    r = client.patch("/api/projects/prj_x/notes",
                     json={"op": "update", "id": "a", "patch": {"pitch": 62}})
    assert r.status_code == 200
    assert r.json()["score"]["version"] == v0 + 1
    for fmt in ("mid", "musicxml", "pdf"):
        e = client.get(f"/api/projects/prj_x/export?format={fmt}")
        assert e.status_code == 200
        assert e.headers["X-Score-Version"] == str(v0 + 1)


def test_assistant_needs_confirmation():
    s = Score(title="A")
    s.notes = [Note(id="a", pitch=60, start_sec=0.1, end_sec=0.5, hand="R")]
    save_project("prj_a", "A", "piano", "/tmp/none.wav", s.to_dict())
    r = client.post("/api/projects/prj_a/assistant",
                    json={"text": "transpose this selection down 2 semitones", "selection": ["a"]})
    assert r.json()["actions"] and r.json()["actions"][0]["semitones"] == -2
    # proposing changed nothing
    assert client.get("/api/projects/prj_a").json()["score"]["notes"][0]["pitch"] == 60
    r2 = client.post("/api/projects/prj_a/assistant/apply",
                     json={"action": r.json()["actions"][0]})
    assert r2.json()["ok"] is True
    assert client.get("/api/projects/prj_a").json()["score"]["notes"][0]["pitch"] == 58
