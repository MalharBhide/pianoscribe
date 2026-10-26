"""Metrics: matching, errors, repeats, chords, melody."""
from piano_scribe.audio_metrics import evaluate, match_notes


def N(p, s, e, role="unknown"):
    return {"pitch": p, "start_sec": s, "end_sec": e, "velocity": 80, "role": role}


def test_perfect_match():
    ref = [N(60, 0.0, 0.5), N(64, 0.5, 1.0)]
    m, miss, extra = match_notes(ref, [dict(r) for r in ref])
    assert len(m) == 2 and not miss and not extra
    r = evaluate(ref, [dict(x) for x in ref], duration_sec=1.0)
    assert r["f1"] == 1.0 and r["extra"] == 0 and r["missing"] == 0


def test_wrong_pitch_is_extra_plus_missing_not_hit():
    ref = [N(60, 0.0, 0.5)]
    r = evaluate(ref, [N(61, 0.01, 0.5)], duration_sec=1.0)
    assert r["f1"] == 0.0 and r["extra"] == 1 and r["missing"] == 1


def test_late_pitch_counted_separately_from_wrong_note():
    ref = [N(60, 0.0, 0.5)]
    r = evaluate(ref, [N(60, 0.2, 0.7)], tol_sec=0.05, duration_sec=1.0)
    assert r["f1"] == 0.0  # outside tolerance: missing + extra
    r2 = evaluate(ref, [N(60, 0.04, 0.5)], tol_sec=0.05, duration_sec=1.0)
    assert r2["f1"] == 1.0 and abs(r2["onset_err_sec"]["mean"] - 0.04) < 1e-6


def test_repeats_and_chords():
    ref = [N(60, 0.0, 0.4), N(60, 0.3, 0.6),
           N(48, 1.0, 2.0), N(52, 1.0, 2.0), N(55, 1.01, 2.0)]
    # estimate collapses the repeat and drops a chord tone
    est = [N(60, 0.0, 0.6), N(48, 1.0, 2.0), N(52, 1.0, 2.0)]
    r = evaluate(ref, est, duration_sec=2.0)
    assert r["repeat_total"] == 1 and r["repeat_rate"] == 0.0
    assert r["chords_total"] == 1 and r["chord_recovery"] == 0.0


def test_melody_subset():
    ref = [N(72, 0.0, 0.5, "melody"), N(48, 0.0, 0.5, "bass")]
    r = evaluate(ref, [N(72, 0.01, 0.5, "melody")], duration_sec=1.0)
    assert r["melody_f1"] == 1.0 and r["recall"] == 0.5
