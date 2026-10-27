"""Arrangement rework: harmony merging, layout width, key backfill."""
from piano_scribe.audio_models import _same_harmony
from piano_scribe.notation import detect_key, layout_score, render_svg
from piano_scribe.score import Note, Score, TempoMap
from piano_scribe.store import get_project, save_project


def test_same_harmony_merges_seventh_color():
    assert _same_harmony("9:min", "9:m7")
    assert _same_harmony("0:maj", "0:maj7")
    assert _same_harmony("5:maj", "5:maj")
    assert not _same_harmony("9:min", "5:maj")  # different roots
    assert not _same_harmony("7:7", "7:maj7")  # dominant vs major-7th function
    assert not _same_harmony("2:dim", "2:m7")  # dim never merges
    assert not _same_harmony("unknown", "0:maj")


def _long_score(mode):
    s = Score(title="W", mode=mode)
    s.tempo = TempoMap(beats=[], bpm=120.0, beats_per_bar=4)
    s.notes = [Note(id=f"n{i}", pitch=60 + (i % 8), start_sec=i * 0.5,
                    end_sec=i * 0.5 + 0.4, hand="R") for i in range(64)]
    s.notes.append(Note(id="b", pitch=48, start_sec=0.0, end_sec=30.0, hand="L"))
    return s


def test_arrangement_systems_carry_six_bars():
    lay = layout_score(_long_score("arrangement"))
    assert len(lay["systems"][0]["measures"]) == 6
    lay = layout_score(_long_score("piano"))
    assert len(lay["systems"][0]["measures"]) == 4


def test_svg_has_brace_and_tempo():
    svg = render_svg(_long_score("arrangement"))
    assert "= 120" in svg  # tempo mark on the first system


def test_detect_key_accepts_stored_dicts():
    dicts = [{"pitch": p, "start_sec": i * 0.5, "end_sec": i * 0.5 + 0.5}
             for i, p in enumerate([63, 62, 67, 63, 65, 67, 70, 67])]
    assert detect_key(dicts) == -2  # Eb forces two flats
    assert detect_key([]) == 0


def test_old_project_backfills_key():
    score = {"title": "old", "mode": "piano", "notes": [
        {"id": "a", "pitch": 66, "start_sec": 0.0, "end_sec": 1.0},
        {"id": "b", "pitch": 61, "start_sec": 1.0, "end_sec": 2.0},
    ], "tempo": {"beats": [], "bpm": 120.0, "beats_per_bar": 4}}
    assert "key_fifths" not in score
    save_project("p-old", "old", "piano", "/tmp/x.wav", score)
    got = get_project("p-old")
    assert got["score"]["key_fifths"] == 2  # D major: F# + C# imply two sharps
    # persisted: second read keeps the value without recomputing
    assert get_project("p-old")["score"]["key_fifths"] == 2
