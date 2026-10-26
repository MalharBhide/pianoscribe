"""MIDI import: turn a MIDI file into a canonical Score (MIDI-to-score).

No inference runs here — notes come straight from the file. Hands are split
by pitch, beats come from the file's own tempo map, chords are derived from
the exact notes per bar, and fingering is suggested like any other score.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from . import PROCESSING_VERSION
from .audio_models import CHORD_TEMPLATES
from .fingering import suggest_fingering
from .notation import assign_hands_piano, measure_bounds, quantize
from .score import Note, Score, TempoMap, new_id


class MidiError(Exception):
    pass


def _tick_to_sec_fn(tempos: List[tuple], ticks_per_beat: int):
    """Piecewise tick->seconds through the file's tempo changes."""
    pts = sorted(tempos)
    bounds = [t for t, _ in pts] + [float("inf")]
    secs = [0.0]
    for i in range(len(pts)):
        tick, us = pts[i]
        span_ticks = bounds[i + 1] - tick if bounds[i + 1] != float("inf") else 0
        secs.append(secs[-1] + span_ticks / ticks_per_beat * us / 1e6)

    def conv(tick: int) -> float:
        i = 0
        for j, (bt, _) in enumerate(pts):
            if tick >= bt:
                i = j
        base_tick, us = pts[i]
        return secs[i] + (tick - base_tick) / ticks_per_beat * us / 1e6

    return conv


def import_midi_file(path: str, title: str = "") -> Score:
    import mido
    try:
        mid = mido.MidiFile(filename=path)
    except Exception as e:
        raise MidiError(f"could not read MIDI file: {e}")
    tpb = mid.ticks_per_beat or 480
    tempos: List[tuple] = []
    beats_per_bar = 4
    for tr in mid.tracks:
        at = 0
        for msg in tr:
            at += msg.time
            if msg.type == "set_tempo":
                tempos.append((at, msg.tempo))
            elif msg.type == "time_signature" and getattr(msg, "numerator", 0):
                beats_per_bar = int(msg.numerator)
    if not tempos:
        tempos = [(0, mido.bpm2tempo(120))]
    conv = _tick_to_sec_fn(tempos, tpb)
    notes: List[Note] = []
    for tr in mid.tracks:
        at = 0
        open_n: Dict[int, tuple] = {}
        for msg in tr:
            at += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                if getattr(msg, "channel", 0) == 9:
                    continue  # skip drums: this is a piano score
                if msg.note in open_n:  # overlap: close first (MIDI semantics)
                    t0, v = open_n.pop(msg.note)
                    notes.append(_mk(msg.note, conv(t0), conv(at), v))
                open_n[msg.note] = (at, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in open_n:
                    t0, v = open_n.pop(msg.note)
                    if at > t0:
                        notes.append(_mk(msg.note, conv(t0), conv(at), v))
    if not notes:
        raise MidiError("no piano notes found in that MIDI file (drums-only files are skipped)")
    bpm = mido.tempo2bpm(tempos[0][1])
    score = Score(notes=notes, tempo=TempoMap(beats=[], bpm=float(bpm), beats_per_bar=beats_per_bar),
                  title=title or os.path.basename(path), mode="piano")
    score.processing = {"model": "midi-import", "model_version": "0.1.0",
                        "config": {"ticks_per_beat": tpb, "tracks": len(mid.tracks)},
                        "processing_version": PROCESSING_VERSION}
    score.raw_detections = [{"pitch": n.pitch, "start_sec": round(n.start_sec, 4),
                             "end_sec": round(n.end_sec, 4), "velocity": n.velocity}
                            for n in notes]
    assign_hands_piano(score)
    quantize(score)
    score.chords = chords_from_notes(score)
    suggest_fingering(score)
    return score


def _mk(pitch: int, s: float, e: float, vel: int) -> Note:
    return Note(id=new_id(), pitch=max(0, min(127, pitch)), start_sec=float(s),
                end_sec=float(max(s + 0.03, e)), velocity=max(1, min(127, vel)),
                provenance="imported")


def chords_from_notes(score: Score) -> List[Dict[str, Any]]:
    """Chord label per bar from exact note pitch classes (for imported scores)."""
    end_sec = max([n.end_sec for n in score.notes], default=0.0)
    bounds = measure_bounds(score.tempo, end_sec)
    out: List[Dict[str, Any]] = []
    for ms, me in bounds:
        pcs = {n.pitch % 12 for n in score.notes if n.start_sec < me - 1e-6 and n.end_sec > ms + 1e-6}
        if len(pcs) < 2:
            continue
        best = ("unknown", 0, 0.0)
        for root in range(12):
            for cname, ivals in CHORD_TEMPLATES.items():
                tmpl = {(root + iv) % 12 for iv in ivals}
                hit = len(pcs & tmpl)
                extra = len(pcs - tmpl)
                sc = hit - 0.6 * extra
                if hit >= 2 and sc > best[2]:
                    best = (cname, root, sc)
        if best[2] > 0:
            out.append({"start_sec": round(ms, 3), "end_sec": round(me, 3),
                        "label": f"{best[1]}:{best[0]}", "score": round(best[2], 3),
                        "root": best[1]})
    return out
