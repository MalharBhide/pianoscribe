"""Ingestion: decode/validate/resample/window/merge."""
import math
import os
import struct
import wave

import pytest

from piano_scribe.audio_ingest import (AudioError, Window, iter_windows,
                                       load_audio, merge_window_notes)
from piano_scribe.audio_models import (REGISTRY, Transcriber,
                                       TranscriptionResult)
from piano_scribe.pipeline import build_score, transcribe_signal


def write_wav(path, samples, sr=22050, nch=1):
    with wave.open(path, "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = []
        for s in samples:
            frames.extend([int(max(-1, min(1, s)) * 32767)] * nch)
        w.writeframes(struct.pack("<%dh" % len(frames), *frames))


def test_wav_roundtrip(tmp_path):
    p = str(tmp_path / "a.wav")
    write_wav(p, [math.sin(2 * math.pi * 440 * i / 22050) * 0.5 for i in range(22050)])
    mono, info = load_audio(p)
    assert len(mono) == 22050
    assert info.codec == "wav" and info.src_sr == 22050
    assert abs(info.duration_sec - 1.0) < 1e-6
    assert info.resample_ratio == "22050/22050"


def test_stereo_and_resample(tmp_path):
    p = str(tmp_path / "s.wav")
    write_wav(p, [0.4] * 44100, sr=44100, nch=2)
    mono, info = load_audio(p)
    assert info.channels == 2
    assert info.resample_ratio == "22050/44100"
    assert abs(info.duration_sec - 1.0) < 0.01


def test_empty_corrupt_unsupported(tmp_path):
    e = tmp_path / "empty.wav"
    e.write_bytes(b"")
    with pytest.raises(AudioError, match="empty"):
        load_audio(str(e))
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav file at all....")
    with pytest.raises(AudioError):
        load_audio(str(bad))
    txt = tmp_path / "x.txt"
    txt.write_text("hello")
    with pytest.raises(AudioError, match="unsupported|decode"):
        load_audio(str(txt))


def test_duration_limit(tmp_path):
    p = str(tmp_path / "long.wav")
    write_wav(p, [0.1] * 22050)
    with pytest.raises(AudioError, match="over the configured limit"):
        load_audio(p, max_duration_sec=0.5)


def _tone_wav(path, dur_sec, sr=22050, freq=440.0, nch=1):
    n = int(dur_sec * sr)
    write_wav(path, [0.5 * math.sin(2 * math.pi * freq * i / sr) for i in range(n)],
              sr=sr, nch=nch)


def test_truncated_header_is_audio_error(tmp_path):
    full = str(tmp_path / "full.wav")
    _tone_wav(full, 0.5)
    head = str(tmp_path / "head.wav")
    with open(full, "rb") as f, open(head, "wb") as g:
        g.write(f.read(20))  # chopped inside the RIFF header
    with pytest.raises(AudioError, match="corrupt"):
        load_audio(head)


def test_odd_midframe_truncation_is_audio_error(tmp_path):
    full = str(tmp_path / "full.wav")
    _tone_wav(full, 0.5, nch=2)
    odd = str(tmp_path / "odd.wav")
    with open(full, "rb") as f, open(odd, "wb") as g:
        g.write(f.read(44 + 1458))  # 729 samples: indivisible by 2 channels
    with pytest.raises(AudioError, match="corrupt"):
        load_audio(odd)


def test_subframe_clip_scores_without_crash(tmp_path):
    p = str(tmp_path / "tiny.wav")
    _tone_wav(p, 0.05)  # far shorter than one STFT window
    for mode in ("piano", "arrangement"):
        out = build_score(p, mode, "standard", "tiny")
        assert out["audio_info"]["duration_sec"] == pytest.approx(0.05, abs=0.01)
        assert isinstance(out["score"].notes, list)


def test_all_silence_scores_empty_with_warning(tmp_path):
    p = str(tmp_path / "sil.wav")
    write_wav(p, [0.0] * 22050)  # 1.0 s of digital silence
    mono, info = load_audio(p)
    assert any("quiet" in w for w in info.warnings)
    out = build_score(p, "piano", "standard", "sil")
    assert out["score"].notes == []


def test_extreme_sample_rates_preserve_duration(tmp_path):
    for sr in (8000, 192000):  # polyphase path and linear-fallback path
        p = str(tmp_path / f"r{sr}.wav")
        _tone_wav(p, 1.0, sr=sr)
        mono, info = load_audio(p)
        assert info.duration_sec == pytest.approx(1.0, abs=0.005)
        assert len(mono) == pytest.approx(22050, abs=25)


def test_mp3_named_garbage_is_audio_error(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"ID3" + b"\x00" * 500 + b"not really audio" * 40)
    with pytest.raises(AudioError):
        load_audio(str(bad))


def test_windowed_bpm_comes_from_beat_grid(monkeypatch):
    import numpy as np

    class BeatFake(Transcriber):
        name = "beat-fake"

        def transcribe(self, mono, sr, progress=None, config=None):
            beats = [round(t * 0.4, 3) for t in range(int(len(mono) / sr / 0.4))]
            return TranscriptionResult(notes=[], beats=beats, bpm=77.7,
                                       meta={"model": "beat-fake"})

    monkeypatch.setitem(REGISTRY, "beat-fake", BeatFake())
    try:
        out = transcribe_signal(np.zeros(5 * 22050, dtype=np.float32), 22050,
                                "piano", "standard", window_sec=2.0,
                                overlap_sec=0.5,
                                transcriber_override={"piano": "beat-fake"})
    finally:
        monkeypatch.delitem(REGISTRY, "beat-fake")
    assert out["windows"] == 3
    # 0.4s grid -> 150bpm; neither the 77.7 decoy nor the old fake 120.
    assert out["bpm"] == pytest.approx(150.0)


def test_windows_cover_without_drift():
    sr = 22050
    n = int(65 * sr)  # 65 s
    wins = list(iter_windows(n, 30.0, 5.0, sr))
    assert wins[0].start_sample == 0
    assert wins[-1].end_sample == n
    for a, b in zip(wins, wins[1:]):
        assert b.start_sample - a.start_sample == int(25 * sr)  # exact hop
        assert b.start_sample < a.end_sample  # overlap
    # absolute offsets reconstruct every sample exactly once in core regions
    cores = []
    for i, w in enumerate(wins):
        lo = w.start_sample + (int(5 * sr) if i > 0 else 0)
        cores.append((lo, w.end_sample))
    assert cores[0][0] == 0 and cores[-1][1] == n


def test_merge_dedups_overlap_and_keeps_repeats():
    sr = 22050
    ov = 5.0
    w0 = Window(0, 0, int(30 * sr))
    w1 = Window(1, int(25 * sr), int(50 * sr))
    # same attack seen in both windows' overlap -> merged once, longest release
    a = {"pitch": 60, "start_sec": 26.0, "end_sec": 27.0, "velocity": 80, "attack": True}
    b = {"pitch": 60, "start_sec": 1.0, "end_sec": 2.5, "velocity": 80, "attack": True}
    # genuine repeat: second attack 300 ms later with onset evidence
    c = {"pitch": 60, "start_sec": 1.3, "end_sec": 1.9, "velocity": 80, "attack": True}
    merged = merge_window_notes([(w0, [a]), (w1, [b, c])], sr, ov)
    starts = sorted(round(m["start_sec"], 2) for m in merged)
    assert starts == [26.0, 26.3], starts
    first = [m for m in merged if abs(m["start_sec"] - 26.0) < 0.01][0]
    assert abs(first["end_sec"] - 27.5) < 0.01  # longest release kept


def test_merge_extends_held_note_across_boundary():
    sr = 22050
    w0 = Window(0, 0, int(30 * sr))
    w1 = Window(1, int(25 * sr), int(50 * sr))
    # long hold sounding through the boundary, no re-attack in window 2
    a = {"pitch": 48, "start_sec": 10.0, "end_sec": 29.5, "velocity": 80, "attack": True}
    b = {"pitch": 48, "start_sec": 0.0, "end_sec": 12.0, "velocity": 80, "attack": False}
    merged = merge_window_notes([(w0, [a]), (w1, [b])], sr, 5.0)
    assert len(merged) == 1
    assert abs(merged[0]["end_sec"] - 37.0) < 0.01
