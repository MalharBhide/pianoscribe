"""Baseline models on clean synthetic tones (honest DSP expectations)."""
import math

import numpy as np

from piano_scribe.audio_models import (BaselineArranger,
                                       BaselinePianoTranscriber)
from piano_scribe.audio_metrics import evaluate

SR = 22050


def synth(notes, dur):
    buf = np.zeros(int(dur * SR), dtype=np.float32)
    for p, s, e in notes:
        f = 440.0 * 2 ** ((p - 69) / 12)
        i0, i1 = int(s * SR), int(e * SR)
        t = np.arange(i1 - i0) / SR
        sig = (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(4 * np.pi * f * t)
               + 0.2 * np.sin(6 * np.pi * f * t))
        sig *= np.minimum(1, t / 0.01) * np.exp(-1.2 * t)
        buf[i0:i1] += sig * 0.4
    return buf / max(1e-6, np.abs(buf).max()) * 0.89


def test_piano_baseline_finds_clean_melody():
    notes = [(60, 0.2, 0.6), (64, 0.7, 1.1), (67, 1.2, 1.6)]
    mono = synth(notes, 2.0)
    res = BaselinePianoTranscriber().transcribe(mono, SR)
    ref = [{"pitch": p, "start_sec": s, "end_sec": e, "velocity": 90} for p, s, e in notes]
    r = evaluate(ref, res.notes, duration_sec=2.0)
    assert r["recall"] >= 0.66, r  # at least 2 of 3 clean tones
    assert r["precision"] >= 0.30, r
    assert res.meta["model_version"] == "0.1.0"


def test_piano_baseline_keeps_repeats():
    notes = [(72, 0.2, 0.34), (72, 0.5, 0.64), (72, 0.8, 0.94)]
    mono = synth(notes, 1.4)
    res = BaselinePianoTranscriber().transcribe(mono, SR)
    ref = [{"pitch": p, "start_sec": s, "end_sec": e, "velocity": 90} for p, s, e in notes]
    r = evaluate(ref, res.notes, duration_sec=1.4)
    assert r["repeat_rate"] >= 0.5, r


def test_arranger_marks_provenance_and_rests_unknown():
    notes = [(60, 0.2, 0.6), (64, 0.7, 1.1)]
    mono = synth(notes, 1.6)
    res = BaselineArranger().transcribe(mono, SR, config={"density": "standard"})
    assert res.notes, "arranger produced nothing"
    assert all(n.get("provenance") == "arranged" for n in res.notes)
    assert all("evidence" in n for n in res.notes)
