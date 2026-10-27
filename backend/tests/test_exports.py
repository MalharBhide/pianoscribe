"""Exports: SVG, MusicXML, MIDI, PDF derive from the same Score."""
import io
import xml.etree.ElementTree as ET

import mido

from piano_scribe.notation import (render_svg, to_midi_bytes, to_musicxml,
                                   to_pdf_bytes)
from piano_scribe.score import Note, Score, TempoMap


def make_score():
    s = Score(title="Export Test", mode="piano")
    s.tempo = TempoMap(beats=[], bpm=120.0, beats_per_bar=4)
    s.notes = [
        Note(id="n1", pitch=60, start_sec=0.0, end_sec=0.5, hand="R", provenance="detected", finger=1),
        Note(id="n2", pitch=64, start_sec=0.0, end_sec=0.5, hand="R", provenance="arranged", finger=3),
        Note(id="n3", pitch=48, start_sec=0.0, end_sec=1.8, hand="L", provenance="user-edited", finger=5),
    ]
    s.chords = [{"start_sec": 0.0, "end_sec": 2.0, "label": "0:maj", "score": 0.9, "root": 0}]
    return s


def test_svg_contains_notes_and_measures():
    svg = render_svg(make_score())
    assert "<svg" in svg and "n1" in svg and "data-measure" in svg
    assert "Export Test" in svg


def test_musicxml_wellformed_and_complete():
    xml = to_musicxml(make_score())
    root = ET.fromstring(xml)  # raises if malformed
    pitches = root.findall(".//pitch")
    assert len(pitches) == 3
    assert root.find(".//time/beats").text == "4"
    harmony = root.find(".//harmony")
    assert harmony is not None
    assert harmony.find("root/root-step").text == "C"
    assert harmony.find("kind").text == "major"
    fingers = [f.text for f in root.findall(".//fingering")]
    assert sorted(fingers) == ["1", "3", "5"]


def test_svg_has_chords_and_fingers():
    svg = render_svg(make_score())
    assert "chord-sym" in svg and ">C<" in svg
    assert 'class="fing"' in svg


def test_midi_roundtrip_note_count():
    data = to_midi_bytes(make_score())
    mid = mido.MidiFile(file=io.BytesIO(data))
    ons = [m for tr in mid.tracks for m in tr if m.type == "note_on" and m.velocity > 0]
    assert len(ons) == 3
    assert sorted(m.note for m in ons) == [48, 60, 64]


def test_midi_overlap_merge_does_not_drift_track():
    s = Score(title="Overlap", mode="piano")
    s.tempo = TempoMap(beats=[], bpm=120.0, beats_per_bar=4)
    s.notes = [
        Note(id="a", pitch=60, start_sec=0.0, end_sec=1.0, hand="R"),
        Note(id="b", pitch=60, start_sec=0.5, end_sec=1.5, hand="R"),  # overlaps a
        Note(id="c", pitch=64, start_sec=2.0, end_sec=2.5, hand="R"),  # must not drift
    ]
    data = to_midi_bytes(s)
    mid = mido.MidiFile(file=io.BytesIO(data))
    ons = []
    for tr in mid.tracks:
        at = 0
        for m in tr:
            at += m.time
            if m.type == "note_on" and m.velocity > 0:
                ons.append((m.note, at))
    # 120bpm: 1 beat = 0.5s = 480 ticks; c at 2.0s = beat 4 = tick 1920
    c_ticks = [t for p, t in ons if p == 64]
    assert c_ticks == [1920], ons


def test_pdf_magic_and_title():
    data = to_pdf_bytes(make_score())
    assert data[:4] == b"%PDF"
    assert len(data) > 1000


def test_display_value_mapping():
    from piano_scribe.notation import display_value
    assert display_value(4.0) == ("whole", 0, 0)
    assert display_value(2.0) == ("half", 0, 0)
    assert display_value(1.5) == ("quarter", 1, 0)
    assert display_value(1.0) == ("quarter", 0, 0)
    assert display_value(0.5) == ("eighth", 0, 1)
    assert display_value(0.25) == ("16th", 0, 2)
    assert display_value(1 / 3) == ("eighth", 0, 1)
    assert display_value(0.9) == ("quarter", 0, 0)


def test_notation_beats_fallback():
    from piano_scribe.notation import notation_beats
    n = Note(id="x", pitch=60, start_sec=0.0, end_sec=1.0, hand="R")
    assert n.beat_onset is None and n.beat_dur is None
    on, du = notation_beats(n, TempoMap(beats=[], bpm=120.0, beats_per_bar=4))
    assert on == 0.0 and abs(du - 2.0) < 1e-9


def _quantized_score():
    from piano_scribe.notation import quantize
    s = Score(title="Rhythm", mode="piano")
    s.tempo = TempoMap(beats=[], bpm=120.0, beats_per_bar=4)  # beat = 0.5s, bar = 2s
    s.notes = [
        Note(id="w", pitch=72, start_sec=0.0, end_sec=2.0, hand="R"),   # whole
        Note(id="e", pitch=76, start_sec=2.0, end_sec=2.25, hand="R"),  # eighth
        Note(id="c1", pitch=60, start_sec=2.5, end_sec=3.0, hand="R"),  # 2nds
        Note(id="c2", pitch=62, start_sec=2.5, end_sec=3.0, hand="R"),
        Note(id="tie", pitch=48, start_sec=1.0, end_sec=3.0, hand="L"),  # spans barline
    ]
    return quantize(s)


def test_svg_rhythmic_marks_and_no_tofu():
    svg = render_svg(_quantized_score())
    assert 'fill="#fff"' in svg  # hollow whole head
    assert "<path" in svg  # flags / ties / vector clefs
    assert "tsig" in svg  # time signature
    for tofu in ("𝄞", "𝄢", "♯", "𝄾"):
        assert tofu not in svg  # glyphs are vector paths, never font glyphs


def test_svg_chord_seconds_offset():
    import re
    svg = render_svg(_quantized_score())
    xs = {}
    for m in re.finditer(r'<ellipse class="notehead" data-note="(c1|c2)"[^>]*cx="([\d.]+)"', svg):
        xs[m.group(1)] = float(m.group(2))
    assert set(xs) == {"c1", "c2"} and abs(xs["c1"] - xs["c2"]) > 5


def test_svg_ties_across_barline():
    svg = render_svg(_quantized_score())
    assert svg.count('data-note="tie"') == 2  # head in each spanned measure
    assert " Q " in svg  # tie arc (quadratic curve)


def test_beams_group_same_beat_16ths():
    from piano_scribe.notation import layout_score, quantize
    s = Score(title="Beams", mode="piano")
    s.tempo = TempoMap(beats=[], bpm=120.0, beats_per_bar=4)
    s.notes = [Note(id=f"s{i}", pitch=72 + i, start_sec=i * 0.125, end_sec=i * 0.125 + 0.12, hand="R")
               for i in range(4)]  # four 16ths in beat 0
    s.notes.append(Note(id="q", pitch=72, start_sec=1.0, end_sec=1.5, hand="R"))  # quarter beat 2
    quantize(s)
    lay = layout_score(s)
    beams = lay["systems"][0]["measures"][0]["staves"]["R"]["beams"]
    assert len(beams) == 1
    assert sorted(sp["level"] for sp in beams[0]["spans"]) == [1, 2]
    svg = render_svg(s)
    assert svg.count('height="4.5"') >= 2  # beam rects drawn


def test_detect_key_and_spelling():
    from piano_scribe.notation import KEY_LINES, detect_key, spell
    d_notes = [Note(id=f"d{i}", pitch=p, start_sec=i, end_sec=i + 1, hand="R")
               for i, p in enumerate([62, 66, 69, 73, 62, 66])]  # D F# A C#
    assert detect_key(d_notes) == 2
    f_notes = [Note(id=f"f{i}", pitch=p, start_sec=i, end_sec=i + 1, hand="R")
               for i, p in enumerate([65, 69, 70, 65])]  # F A Bb
    assert detect_key(f_notes) == -1
    assert detect_key([]) == 0
    assert spell(58, -1) == ("B", -1, 3)  # Bb in a flat key
    assert spell(66, 2) == ("F", 1, 4)  # F# in a sharp key
    for clef in ("treble", "bass"):
        for sign in ("#", "b"):
            lines = KEY_LINES[clef][sign]
            assert len(set(lines)) == 7 and all(0 <= v <= 10 for v in lines)


def test_key_signature_renders_and_cancels_accidentals():
    s = _quantized_score()
    s.key_fifths = 2  # D major: F#, C# in the signature
    svg = render_svg(s)
    assert svg.count('stroke-width="1.4"') >= 4  # 2 sharps x 2 staves
    xml = to_musicxml(s)
    assert "<fifths>2</fifths>" in xml


def test_musicxml_types_and_tied():
    xml = to_musicxml(_quantized_score())
    root = ET.fromstring(xml)
    notes = root.findall(".//note")
    assert notes and all(n.findtext("type") for n in notes)
    tied = [t.get("type") for t in root.findall(".//tied")]
    assert "start" in tied and "stop" in tied  # spanning note renders its tie
