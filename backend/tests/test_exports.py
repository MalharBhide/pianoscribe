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
