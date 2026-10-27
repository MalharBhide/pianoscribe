"""Neural spike: adapter seam, MIDI conversion, registry override.

All tests run offline (no torch, no weights): the unavailable path and the
pure conversion/registration logic are what's pinned here. Weight download
and neural eval happen on a networked machine per docs/NEURAL_SPIKE.md.
"""
import numpy as np
import pytest

from piano_scribe.audio_models import REGISTRY, Transcriber, TranscriptionResult
from piano_scribe.neural_models import (KongPianoTranscriber,
                                        ModelNotAvailable, midi_events_to_notes,
                                        register_optional_models)
from piano_scribe.pipeline import transcribe_signal


def test_adapter_imports_without_heavy_deps():
    assert KongPianoTranscriber.name == "kong-piano"
    assert "transcribe" in dir(KongPianoTranscriber)


def test_transcribe_missing_stack_raises_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PIANO_SCRIBE_MODEL_DIR", str(tmp_path))
    tx = KongPianoTranscriber()
    with pytest.raises(ModelNotAvailable) as ei:
        tx.transcribe(np.zeros(22050, dtype=np.float32), 22050)
    assert "fetch_weights.py" in str(ei.value)


def test_midi_events_to_notes(tmp_path):
    import mido
    p = str(tmp_path / "t.mid")
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    tr.append(mido.Message("note_on", note=60, velocity=90, time=0))
    tr.append(mido.Message("note_on", note=64, velocity=80, time=240))
    tr.append(mido.Message("note_off", note=60, velocity=0, time=240))
    tr.append(mido.Message("note_off", note=64, velocity=0, time=480))
    tr.append(mido.Message("note_on", note=67, velocity=70, time=0))  # unterminated
    mid.save(p)
    notes = midi_events_to_notes(p)
    by_pitch = {n["pitch"]: n for n in notes}
    assert by_pitch[60]["start_sec"] == pytest.approx(0.0)
    assert by_pitch[60]["end_sec"] == pytest.approx(0.5)
    assert by_pitch[60]["velocity"] == 90
    assert by_pitch[64]["start_sec"] == pytest.approx(0.25)
    assert by_pitch[64]["end_sec"] == pytest.approx(1.0)
    assert by_pitch[67]["end_sec"] >= by_pitch[67]["start_sec"]  # closed at EOF
    assert all(n["evidence"].get("source") == "neural" for n in notes)


def test_register_skips_gracefully_without_stack(monkeypatch, tmp_path):
    monkeypatch.setenv("PIANO_SCRIBE_MODEL_DIR", str(tmp_path))
    reg = dict(REGISTRY)
    added = register_optional_models(reg)
    assert added == []  # no upstream package and/or no checkpoint here
    assert set(reg) == set(REGISTRY)


def test_pipeline_override_selects_registry_key(monkeypatch):
    class Fake(Transcriber):
        name = "fake-test"

        def transcribe(self, mono, sr, progress=None, config=None):
            return TranscriptionResult(
                notes=[{"pitch": 60, "start_sec": 0.0, "end_sec": 0.5,
                        "velocity": 90}], beats=[], bpm=99.0, meta={"model": "fake-test"})

    monkeypatch.setitem(REGISTRY, "fake-test", Fake())
    try:
        out = transcribe_signal(np.zeros(22050, dtype=np.float32), 22050,
                                "piano", "standard",
                                transcriber_override={"piano": "fake-test"})
    finally:
        monkeypatch.delitem(REGISTRY, "fake-test")
    assert out["notes"][0]["pitch"] == 60
    assert out["meta"]["model"] == "fake-test"
    with pytest.raises(KeyError):
        transcribe_signal(np.zeros(22050, dtype=np.float32), 22050,
                          "piano", "standard",
                          transcriber_override={"piano": "no-such-key"})
