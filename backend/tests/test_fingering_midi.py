"""Fingering, chord symbols, MIDI import."""
import io

import mido

from piano_scribe.fingering import suggest_fingering
from piano_scribe.midi_import import chords_from_notes, import_midi_file
from piano_scribe.notation import chord_symbol
from piano_scribe.score import Note, Score


def _scale(hand="R"):
    s = Score()
    t = 0.0
    for p in [60, 62, 64, 65, 67]:
        s.notes.append(Note(id=f"n{p}", pitch=p, start_sec=t, end_sec=t + 0.4, hand=hand))
        t += 0.5
    return s


def test_scale_walks_fingers():
    s = _scale("R")
    assert suggest_fingering(s) == 5
    fs = [n.finger for n in s.notes]
    assert fs == [1, 2, 3, 4, 5], fs
    s = _scale("L")
    suggest_fingering(s)
    assert [n.finger for n in s.notes] == [5, 4, 3, 2, 1]


def test_repeated_note_changes_fingers():
    s = Score()
    for i in range(3):
        s.notes.append(Note(id=f"r{i}", pitch=72, start_sec=i * 0.5, end_sec=i * 0.5 + 0.3, hand="R"))
    suggest_fingering(s)
    fs = [n.finger for n in s.notes]
    assert len(set(fs)) == 3, fs
    assert all(1 <= f <= 5 for f in fs)


def test_chord_spreads_across_hand():
    s = Score()
    for i, p in enumerate([60, 64, 67]):
        s.notes.append(Note(id=f"c{i}", pitch=p, start_sec=0.0, end_sec=1.0, hand="R"))
    suggest_fingering(s)
    assert sorted(n.finger for n in s.notes) == [1, 3, 5]


def test_chord_symbol():
    assert chord_symbol("0:maj") == "C"
    assert chord_symbol("9:min") == "Am"
    assert chord_symbol("7:7") == "G7"
    assert chord_symbol("1:maj") == "Db"
    assert chord_symbol("unknown") == ""
    assert chord_symbol("bogus") == ""


def _mid_bytes():
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    for i, p in enumerate([60, 64, 67, 48]):
        tr.append(mido.Message("note_on", note=p, velocity=90, time=0 if i == 0 else 480))
        tr.append(mido.Message("note_off", note=p, velocity=0, time=480))
    mid.tracks.append(tr)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def test_midi_import_roundtrip(tmp_path):
    p = tmp_path / "t.mid"
    p.write_bytes(_mid_bytes())
    score = import_midi_file(str(p), "T")
    assert len(score.notes) == 4
    assert {n.hand for n in score.notes} == {"L", "R"}  # split by pitch
    assert all(n.provenance == "imported" for n in score.notes)
    assert all(n.finger in (1, 2, 3, 4, 5) for n in score.notes)
    assert score.processing["model"] == "midi-import"
    assert abs(score.notes[0].start_sec - 0.0) < 0.01
    assert abs(score.notes[1].start_sec - 1.0) < 0.01  # 120bpm quarter


def test_midi_import_rejects_garbage(tmp_path):
    from piano_scribe.midi_import import MidiError
    p = tmp_path / "x.mid"
    p.write_bytes(b"not midi at all")
    try:
        import_midi_file(str(p))
    except MidiError:
        pass
    else:
        raise AssertionError("expected MidiError")


def test_chords_from_exact_notes():
    s = Score()
    for i, p in enumerate([48, 52, 55]):  # C major triad, bar 1
        s.notes.append(Note(id=f"k{i}", pitch=p, start_sec=0.1, end_sec=1.0, hand="L"))
    chords = chords_from_notes(s)
    assert chords and chords[0]["label"] == "0:maj", chords
