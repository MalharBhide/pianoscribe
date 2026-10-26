"""Score: edits, undo/redo, raw preservation."""
from piano_scribe.score import Note, Score


def make_score():
    s = Score(title="t")
    s.notes = [Note(id="a", pitch=60, start_sec=0.0, end_sec=0.5),
               Note(id="b", pitch=64, start_sec=0.5, end_sec=1.0)]
    s.raw_detections = [{"pitch": 60, "start_sec": 0.0}]
    return s


def test_update_undo_redo():
    s = make_score()
    s.update_note("a", {"pitch": 62})
    assert s.get("a").pitch == 62 and s.get("a").provenance == "user-edited"
    assert s.undo() and s.get("a").pitch == 60
    assert s.redo() and s.get("a").pitch == 62


def test_delete_transpose_and_raw_preserved():
    s = make_score()
    assert s.transpose(["a", "b"], -2) == 2
    assert s.delete_notes(["a"]) == 1
    assert s.raw_detections == [{"pitch": 60, "start_sec": 0.0}]
    assert s.undo() and s.get("a") is not None


def test_add_note_roundtrip():
    s = make_score()
    n = s.add_note({"pitch": 72, "start_sec": 1.0, "end_sec": 1.5})
    assert s.get(n.id) is not None
    d = Score.from_dict(s.to_dict())
    assert d.get(n.id).pitch == 72
