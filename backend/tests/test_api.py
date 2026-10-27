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


def test_edit_requantizes_notation():
    from piano_scribe.score import TempoMap
    s = Score(title="Q")
    s.tempo = TempoMap(beats=[], bpm=120.0, beats_per_bar=4)  # beat = 0.5s
    s.notes = [Note(id="q", pitch=60, start_sec=0.0, end_sec=0.5, hand="R")]
    save_project("prj_q", "Q", "piano", "/tmp/none.wav", s.to_dict())
    before = client.get("/api/projects/prj_q").json()["score"]["notes"][0]["beat_dur"]
    assert before is None  # hand-built score was never quantized
    r = client.patch("/api/projects/prj_q/notes",
                     json={"op": "update", "id": "q", "patch": {"end_sec": 1.5}})
    assert r.status_code == 200
    after = r.json()["score"]["notes"][0]["beat_dur"]
    assert after == 3.0  # notation re-derived from the edited 1.5s duration


def test_sniff_kinds():
    from piano_scribe.api import sniff_audio_kind
    assert sniff_audio_kind(b"RIFF....WAVE" + b"\x00" * 100) == "wav"
    assert sniff_audio_kind(b"ID3\x04\x00" + b"\x00" * 100) == "mp3"
    assert sniff_audio_kind(b"\xff\xfb\x90\x00" + b"\x00" * 100) == "mp3"
    assert sniff_audio_kind(b"\x00" * 100) == "unknown"
    assert sniff_audio_kind(b"") == "unknown"


def test_upload_trusts_content_over_filename():
    # WAV bytes misnamed .mp3 are saved by detected kind and queued.
    r = client.post("/api/uploads", files={"file": ("song.mp3", _wav_bytes(secs=0.2), "audio/mpeg")})
    assert r.status_code == 200, r.text
    from piano_scribe.store import get_job
    job = get_job(r.json()["job_id"])
    assert job is not None and job["audio_path"].endswith(".wav")


def test_garbage_bytes_rejected_with_friendly_message():
    r = client.post("/api/uploads", files={"file": ("x.wav", b"\x00" * 5000, "audio/wav")})
    assert r.status_code == 400
    assert "couldn't recognize" in r.json()["detail"]


def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setenv("PIANO_SCRIBE_RATE_UPLOADS_PER_MIN", "1")
    r1 = client.post("/api/uploads", files={"file": ("t.wav", _wav_bytes(secs=0.2), "audio/wav")})
    assert r1.status_code == 200
    r2 = client.post("/api/uploads", files={"file": ("t.wav", _wav_bytes(secs=0.2), "audio/wav")})
    assert r2.status_code == 429


def test_ready_reports_worker_honestly():
    r = client.get("/ready")
    assert r.status_code == 503  # no worker heartbeat in tests
    assert r.json()["checks"]["worker"]["ok"] is False
    assert r.json()["checks"]["storage"]["ok"] is True


def test_security_headers_present():
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "X-Request-ID" in r.headers


def test_rename_duplicate_delete_project():
    s = Score(title="Orig")
    s.notes = [Note(id="a", pitch=60, start_sec=0.0, end_sec=0.5, hand="R")]
    save_project("prj_r", "Orig", "piano", "/tmp/none.wav", s.to_dict())
    r = client.patch("/api/projects/prj_r", json={"title": "Renamed"})
    assert r.status_code == 200 and r.json()["title"] == "Renamed"
    assert client.get("/api/projects/prj_r").json()["title"] == "Renamed"
    d = client.post("/api/projects/prj_r/duplicate")
    assert d.status_code == 200
    dup = client.get(f"/api/projects/{d.json()['id']}").json()
    assert len(dup["score"]["notes"]) == 1 and dup["score"]["version"] == 1
    assert "(copy)" in dup["title"]
    assert client.delete(f"/api/projects/{d.json()['id']}").status_code == 200
    assert client.get(f"/api/projects/{d.json()['id']}").status_code == 404
    # original untouched
    assert client.get("/api/projects/prj_r").status_code == 200


def test_jobs_list_endpoint():
    client.post("/api/uploads", files={"file": ("t.wav", _wav_bytes(secs=0.2), "audio/wav")})
    r = client.get("/api/jobs?limit=5")
    assert r.status_code == 200 and len(r.json()["jobs"]) >= 1


def test_project_list_includes_counts():
    s = Score(title="Counted")
    s.notes = [Note(id="a", pitch=60, start_sec=0.0, end_sec=2.5, hand="R"),
               Note(id="b", pitch=48, start_sec=0.0, end_sec=1.0, hand="L")]
    save_project("prj_c", "Counted", "piano", "/tmp/none.wav", s.to_dict())
    rows = {p["id"]: p for p in client.get("/api/projects").json()["projects"]}
    assert rows["prj_c"]["note_count"] == 2
    assert rows["prj_c"]["duration_sec"] == 2.5


def test_frontend_assets_served():
    html = client.get("/").text
    assert "/app.js" in html and "/styles.css" in html
    assert client.get("/app.js").status_code == 200
    css = client.get("/styles.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]


def test_samples_list_and_transcribe():
    r = client.get("/api/samples")
    assert r.status_code == 200
    samples = r.json()["samples"]
    assert len(samples) == 4 and all(s["available"] for s in samples)
    assert samples[0]["duration_sec"] and samples[0]["duration_sec"] > 0
    t = client.post("/api/samples/synth_melody/transcribe", json={"mode": "piano"})
    assert t.status_code == 200
    from piano_scribe.store import get_job
    job = get_job(t.json()["job_id"])
    assert job and job["status"] == "queued" and "synth_melody" in job["audio_path"]
    assert client.post("/api/samples/nope/transcribe", json={}).status_code == 404


def test_midi_import_endpoint():
    import io as _io
    import mido
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    tr.append(mido.Message("note_on", note=60, velocity=90, time=0))
    tr.append(mido.Message("note_off", note=60, velocity=0, time=480))
    tr.append(mido.Message("note_on", note=48, velocity=90, time=0))
    tr.append(mido.Message("note_off", note=48, velocity=0, time=480))
    mid.tracks.append(tr)
    buf = _io.BytesIO()
    mid.save(file=buf)
    r = client.post("/api/imports", files={"file": ("t.mid", buf.getvalue(), "audio/midi")})
    assert r.status_code == 200, r.text
    p = client.get(f"/api/projects/{r.json()['project_id']}").json()
    assert len(p["score"]["notes"]) == 2
    assert p["score"]["processing"]["model"] == "midi-import"
    assert "<svg" in p["svg"]
    bad = client.post("/api/imports", files={"file": ("t.mid", b"garbage", "audio/midi")})
    assert bad.status_code == 400


def test_rerun_creates_retarget_job(tmp_path):
    wav = tmp_path / "r.wav"
    with open(wav, "wb") as f:
        f.write(_wav_bytes(secs=0.5))
    s = Score(title="RR")
    s.notes = [Note(id="a", pitch=60, start_sec=0.1, end_sec=0.4, hand="R")]
    save_project("prj_rr", "RR", "piano", str(wav), s.to_dict())
    r = client.post("/api/projects/prj_rr/rerun", json={"density": "simple"})
    assert r.status_code == 200, r.text
    from piano_scribe.store import claim_next_job, get_job
    import json as _json
    job = get_job(r.json()["job_id"])
    assert _json.loads(job["params"])["retarget"] == "prj_rr"
    assert job["density"] == "simple"
    # Execute the retarget in-process: same project, version+1, undo restores.
    from piano_scribe.worker import run_job
    pid = run_job(claim_next_job())
    assert pid == "prj_rr"
    p = client.get("/api/projects/prj_rr").json()
    assert p["score"]["version"] == 2
    assert p["score"]["history"][-1]["label"].startswith("re-run")
    u = client.patch("/api/projects/prj_rr/notes", json={"op": "undo"}).json()
    assert [n["pitch"] for n in u["score"]["notes"]] == [60]


def test_assistant_transpose_all_and_display():
    s = Score(title="TA")
    s.notes = [Note(id="a", pitch=60, start_sec=0.1, end_sec=0.4, hand="R"),
               Note(id="b", pitch=64, start_sec=0.5, end_sec=0.9, hand="R")]
    save_project("prj_ta", "TA", "piano", "/tmp/none.wav", s.to_dict())
    r = client.post("/api/projects/prj_ta/assistant",
                    json={"text": "transpose everything down 2 semitones", "selection": []})
    assert r.json()["actions"] and r.json()["actions"][0]["type"] == "transpose_all"
    a = client.post("/api/projects/prj_ta/assistant/apply",
                    json={"action": r.json()["actions"][0]}).json()
    assert a["ok"] is True
    assert sorted(n["pitch"] for n in a["score"]["notes"]) == [58, 62]
    d = client.post("/api/projects/prj_ta/assistant",
                    json={"text": "hide chord symbols", "selection": []}).json()
    assert d["actions"][0] == {"type": "display", "key": "chords", "value": False}
    v0 = client.get("/api/projects/prj_ta").json()["score"]["version"]
    da = client.post("/api/projects/prj_ta/assistant/apply",
                     json={"action": d["actions"][0]}).json()
    assert da["ok"] is True and "score" not in da  # display-only: nothing persisted
    assert client.get("/api/projects/prj_ta").json()["score"]["version"] == v0


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
