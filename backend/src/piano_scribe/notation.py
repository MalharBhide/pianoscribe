"""Notation: quantization, hand assignment, SVG, MusicXML, MIDI, PDF.

All outputs derive from the same canonical Score. Quantization is conservative
and reversible: performance seconds are never overwritten; notation beats live
alongside them.
"""
from __future__ import annotations

import io
import math
from typing import Any, Dict, List, Tuple
from xml.sax.saxutils import escape

from .score import Note, Score, TempoMap

DIVISIONS = 16  # MusicXML divisions per quarter
DIA = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}  # pc -> diatonic class C..B
PC_OF_DIA = [0, 2, 4, 5, 7, 9, 11]
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


# -- tempo map helpers --------------------------------------------------------
def seconds_to_beats(t: float, tempo: TempoMap) -> float:
    b = tempo.beats
    if len(b) >= 2:
        if t <= b[0]:
            step = b[1] - b[0]
            return (t - b[0]) / step
        for i in range(len(b) - 1):
            if b[i] <= t < b[i + 1]:
                frac = (t - b[i]) / max(1e-6, b[i + 1] - b[i])
                return i + frac
        step = b[-1] - b[-2]
        return len(b) - 1 + (t - b[-1]) / step
    step = 60.0 / max(1.0, tempo.bpm)
    return t / step


def beats_to_seconds(bt: float, tempo: TempoMap) -> float:
    b = tempo.beats
    if len(b) >= 2:
        if bt <= 0:
            return b[0] + bt * (b[1] - b[0])
        i = int(math.floor(bt))
        frac = bt - i
        if i >= len(b) - 1:
            return b[-1] + (bt - (len(b) - 1)) * (b[-1] - b[-2])
        return b[i] + frac * (b[i + 1] - b[i])
    return bt * 60.0 / max(1.0, tempo.bpm)


def measure_bounds(tempo: TempoMap, end_sec: float) -> List[Tuple[float, float]]:
    """Measure (start_sec, end_sec) list from the tempo map."""
    bpb = max(1, tempo.beats_per_bar)
    total_beats = max(bpb, math.ceil(seconds_to_beats(end_sec, tempo)))
    n_bars = max(1, math.ceil(total_beats / bpb))
    out = []
    for m in range(n_bars):
        out.append((beats_to_seconds(m * bpb, tempo), beats_to_seconds((m + 1) * bpb, tempo)))
    return out


# -- quantization --------------------------------------------------------------
GRID_BEATS = [0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0]


def quantize(score: Score, snap_window_sec: float = 0.025) -> Score:
    """Conservative snap: onsets move only inside a small window; durations snap
    only when close to a standard value; nothing is merged or split."""
    sixteenth = 0.25
    for n in score.notes:
        raw_beat = seconds_to_beats(n.start_sec, score.tempo)
        snapped = round(raw_beat / sixteenth) * sixteenth
        snapped = max(0.0, snapped)
        if abs(beats_to_seconds(snapped, score.tempo) - n.start_sec) <= snap_window_sec:
            n.beat_onset = snapped
        else:
            n.beat_onset = raw_beat  # keep unquantized position, explicitly
        raw_dur_beats = seconds_to_beats(n.end_sec, score.tempo) - raw_beat
        raw_dur_beats = max(0.05, raw_dur_beats)
        best = min(GRID_BEATS, key=lambda g: abs(g - raw_dur_beats))
        if abs(best - raw_dur_beats) <= max(0.06, 0.15 * raw_dur_beats):
            n.beat_dur = best
        else:
            n.beat_dur = round(raw_dur_beats, 3)
    return score


def assign_hands_piano(score: Score, split_pitch: int = 60) -> Score:
    """Split-hands with simultaneity integrity: notes starting within 40 ms
    stay in one hand (majority vote), so performed chords are never torn apart."""
    notes = sorted(score.notes, key=lambda n: n.start_sec)
    groups: List[List[Note]] = []
    for n in notes:
        if groups and n.start_sec - groups[-1][0].start_sec <= 0.04:
            groups[-1].append(n)
        else:
            groups.append([n])
    for g in groups:
        votes = sum(1 for n in g if n.pitch >= split_pitch)
        hand = "R" if votes * 2 >= len(g) else "L"
        for n in g:
            if n.hand not in ("L", "R") or n.provenance in ("detected", "imported"):
                n.hand = hand
    return score


# -- pitch spelling --------------------------------------------------------------
def spell(pitch: int) -> Tuple[str, int, int]:
    """Return (step, alter, octave) with sharp preference, no key signature."""
    octave = pitch // 12 - 1
    pc = pitch % 12
    if pc in DIA:
        return "CDEFGAB"[DIA[pc]], 0, octave
    # sharp of the diatonic below
    below = max(p for p in DIA if p < pc)
    return "CDEFGAB"[DIA[below]], 1, octave


def staff_steps(pitch: int, clef: str) -> Tuple[int, int]:
    """Diatonic steps above the staff bottom line + accidental alter."""
    step, alter, octave = spell(pitch)
    dia_idx = "CDEFGAB".index(step) + octave * 7
    if clef == "treble":
        base = "CDEFGAB".index("E") + 4 * 7  # E4 bottom line
    else:
        base = "CDEFGAB".index("G") + 2 * 7  # G2 bottom line
    return dia_idx - base, alter


# -- chord symbols ---------------------------------------------------------------
CHORD_PC_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
CHORD_SUFFIX = {"maj": "", "min": "m", "dim": "dim", "aug": "aug",
                "7": "7", "maj7": "maj7", "m7": "m7"}
CHORD_XML_KIND = {"maj": "major", "min": "minor", "dim": "diminished",
                  "aug": "augmented", "7": "dominant", "maj7": "major-seventh",
                  "m7": "minor-seventh"}


def chord_symbol(label: str) -> str:
    """'9:min' -> 'Am'. Returns '' for unknown labels."""
    try:
        root_s, quality = label.split(":")
        root = int(root_s) % 12
    except (ValueError, AttributeError):
        return ""
    suffix = CHORD_SUFFIX.get(quality)
    if suffix is None:
        return ""
    return f"{CHORD_PC_NAMES[root]}{suffix}"


def _type_dot(quarters: float) -> Tuple[str, int]:
    """Display (type, dots) for a duration in quarter notes."""
    q = max(0.03, quarters)
    vals = ((4.0, "whole"), (2.0, "half"), (1.0, "quarter"), (0.5, "eighth"),
            (0.25, "16th"), (0.125, "32nd"), (0.0625, "64th"))
    for base, name in vals:
        if abs(q - base) <= 0.08 * base + 0.015:
            return name, 0
    for base, name in vals:
        if abs(q - 1.5 * base) <= 0.06 * base + 0.01:
            return name, 1
    for base, name in vals:
        if abs(q - 1.75 * base) <= 0.06 * base + 0.01:
            return name, 2
    _, name = min(((4.0, "whole"), (2.0, "half"), (1.0, "quarter"),
                   (0.5, "eighth"), (0.25, "16th"), (0.125, "32nd")),
                  key=lambda v: abs(math.log(q / v[0])))
    return name, 0


def _harmony_xml(chord: Dict[str, Any]) -> str:
    root = int(chord.get("root", 0)) % 12
    quality = str(chord.get("label", "")).split(":")[-1]
    name = CHORD_PC_NAMES[root]
    step, alter = (name[0], -1) if len(name) == 2 else (name, 0)
    kind = CHORD_XML_KIND.get(quality, "major")
    return (f"<harmony><root><root-step>{step}</root-step>"
            + (f"<root-alter>{alter}</root-alter>" if alter else "")
            + f"</root><kind>{kind}</kind></harmony>")


# -- MusicXML ---------------------------------------------------------------------
def to_musicxml(score: Score) -> str:
    end_sec = max([n.end_sec for n in score.notes], default=1.0)
    bounds = measure_bounds(score.tempo, end_sec)
    bpb = score.tempo.beats_per_bar
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write('<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
              '"http://www.musicxml.org/dtds/partwise.dtd">\n')
    out.write('<score-partwise version="3.1"><work><work-title>%s</work-title></work>\n'
              % escape(score.title))
    out.write('<part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>\n')
    out.write('<part id="P1">\n')
    for m, (ms, me) in enumerate(bounds):
        out.write(f'<measure number="{m + 1}">\n')
        if m == 0:
            out.write('<attributes><divisions>%d</divisions>'
                      '<staves>2</staves>'
                      '<clef number="1"><sign>G</sign><line>2</line></clef>'
                      '<clef number="2"><sign>F</sign><line>4</line></clef>'
                      '<time><beats>%d</beats><beat-type>4</beat-type></time></attributes>\n'
                      % (DIVISIONS, bpb))
        hams = [c for c in score.chords
                if c.get("label") != "unknown" and ms - 1e-6 <= float(c.get("start_sec", -1)) < me - 1e-6]
        for staff_no, hand in ((1, "R"), (2, "L")):
            segs = _measure_segments(score, hand, m, ms, me)
            _write_voice(out, segs, staff_no, ms, me,
                         harmonies=hams if staff_no == 1 else None)
        out.write('</measure>\n')
    out.write('</part></score-partwise>\n')
    return out.getvalue()


def _measure_segments(score: Score, hand: str, m: int, ms: float, me: float):
    """Clip hand notes to [ms, me), splitting ties across barlines."""
    segs = []
    for n in sorted(score.notes, key=lambda x: (x.start_sec, x.pitch)):
        if n.hand != hand:
            continue
        if n.end_sec <= ms or n.start_sec >= me:
            continue
        s = max(n.start_sec, ms)
        e = min(n.end_sec, me)
        tie_start = n.start_sec < ms - 1e-6
        tie_stop = n.end_sec > me + 1e-6
        tie_type = "stop" if tie_start and not tie_stop else ("start" if tie_stop and not tie_start else
                                                              ("continue" if tie_start and tie_stop else None))
        qlen = max(1, int(round((e - s) / max(1e-6, me - ms) * score.tempo.beats_per_bar * DIVISIONS)))
        segs.append({"note": n, "start": s, "dur_div": qlen, "tie": tie_type,
                     "new_onset": n.start_sec >= ms - 1e-6})
    return segs


def _write_voice(out: io.StringIO, segs: List[Dict[str, Any]], staff_no: int, ms: float, me: float,
                 harmonies: Any = None):
    order = sorted(segs, key=lambda s: (s["start"], s["note"].pitch))
    # rest-fill cursor in divisions
    def div_at(t: float) -> int:
        frac = (t - ms) / max(1e-6, me - ms)
        return int(round(frac * 4 * DIVISIONS))
    total = div_at(me)
    hams = sorted(((div_at(float(c.get("start_sec", ms))), _harmony_xml(c)) for c in (harmonies or [])),
                  key=lambda x: x[0])
    hip = 0

    def flush_harmonies(upto: int) -> None:
        nonlocal hip
        while hip < len(hams) and hams[hip][0] <= upto:
            out.write(hams[hip][1])
            hip += 1

    cur = 0
    idx = 0
    while idx < len(order):
        onset_div = div_at(order[idx]["start"])
        flush_harmonies(onset_div)
        if onset_div > cur:
            _rest(out, onset_div - cur, staff_no)
            cur = onset_div
        # chord group
        grp = [order[idx]]
        j = idx + 1
        while j < len(order) and abs(order[j]["start"] - order[idx]["start"]) < 0.03:
            grp.append(order[j])
            j += 1
        maxdv = 0
        for k, s in enumerate(grp):
            n: Note = s["note"]
            step, alter, octave = spell(n.pitch)
            out.write('<note>')
            if k > 0:
                out.write('<chord/>')
            out.write(f'<pitch><step>{step}</step>'
                      + (f'<alter>{alter}</alter>' if alter else '')
                      + f'<octave>{octave}</octave></pitch>'
                      f'<duration>{s["dur_div"]}</duration><voice>1</voice>'
                      f'<staff>{staff_no}</staff>')
            if s["tie"] in ("start", "continue"):
                out.write('<tie type="start"/>')
            if s["tie"] in ("stop", "continue"):
                out.write('<tie type="stop"/>')
            tname, ndots = _type_dot(s["dur_div"] / DIVISIONS)
            out.write(f'<type>{tname}</type>' + '<dot/>' * ndots)
            notations = []
            if s["tie"] in ("start", "continue"):
                notations.append('<tied type="start"/>')
            if s["tie"] in ("stop", "continue"):
                notations.append('<tied type="stop"/>')
            if n.finger:
                notations.append(f'<technical><fingering>{int(n.finger)}</fingering></technical>')
            if notations:
                out.write('<notations>' + ''.join(notations) + '</notations>')
            out.write('</note>')
            maxdv = max(maxdv, s["dur_div"])
        cur += maxdv
        idx = j
    flush_harmonies(total)
    if cur < total:
        _rest(out, total - cur, staff_no)


def _rest(out: io.StringIO, dur: int, staff_no: int):
    if dur <= 0:
        return
    tname, ndots = _type_dot(dur / DIVISIONS)
    out.write(f'<note><rest/><duration>{dur}</duration><voice>1</voice><staff>{staff_no}</staff>'
              f'<type>{tname}</type>' + '<dot/>' * ndots + '</note>')


# -- MIDI ----------------------------------------------------------------------------
def midi_beat_shift(score: Score) -> float:
    """Shift (in beats) the MIDI writer adds so the earliest event sits at tick 0.

    MIDI has no negative time; pickup notes before the first detected beat
    would otherwise collapse onto tick 0 and corrupt timing. The shift
    preserves all relative timing; absolute alignment to the original audio
    lives in the project (score tempo map + audio), not the MIDI file.
    """
    if not score.notes:
        return 0.0
    lo = min(seconds_to_beats(n.start_sec, score.tempo) for n in score.notes)
    return max(0.0, -lo)


def to_midi_bytes(score: Score) -> bytes:
    import mido
    mid = mido.MidiFile(ticks_per_beat=480)
    shift = midi_beat_shift(score)
    tempo_us = mido.bpm2tempo(max(20.0, min(300.0, score.tempo.bpm)))
    for hand, name, ch in (("R", "Right Hand", 0), ("L", "Left Hand", 1)):
        tr = mido.MidiTrack()
        tr.name = name
        tr.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
        events = []
        for n in score.notes:
            if n.hand != hand:
                continue
            on_beat = seconds_to_beats(n.start_sec, score.tempo) + shift
            off_beat = seconds_to_beats(n.end_sec, score.tempo) + shift
            events.append((on_beat, "on", n))
            events.append((max(on_beat + 0.02, off_beat), "off", n))
        events.sort(key=lambda e: (e[0], 0 if e[1] == "off" else 1))
        last_tick = 0  # last WRITTEN tick; skipped events must not advance it
        open_p: set = set()
        for beat, kind, n in events:
            tick = int(round(max(0.0, beat) * 480))
            if kind == "on":
                if n.pitch in open_p:
                    # overlapping same pitch: close first (MIDI has no
                    # per-note identity); repeated sequential notes unaffected.
                    tr.append(mido.Message("note_off", channel=ch, note=n.pitch,
                                           velocity=64, time=max(0, tick - last_tick)))
                    last_tick = tick
                open_p.add(n.pitch)
                tr.append(mido.Message("note_on", channel=ch, note=n.pitch,
                                       velocity=max(1, min(127, n.velocity)),
                                       time=max(0, tick - last_tick)))
                last_tick = tick
            else:
                if n.pitch in open_p:
                    open_p.discard(n.pitch)
                    tr.append(mido.Message("note_off", channel=ch, note=n.pitch,
                                           velocity=64, time=max(0, tick - last_tick)))
                    last_tick = tick
                # else: merged into an earlier close; write nothing and do
                # not advance last_tick, or the track drifts early.
        mid.tracks.append(tr)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


# -- engraving -------------------------------------------------------------------------
# Shared layout drives SVG (screen) and PDF (print) so they always agree.
# Layout space is SVG pixels, y down. Note x positions stay time-proportional
# so the playback cursor keeps aligning with the notation.
#
# All musical glyphs (clefs, accidentals, rests, flags, ties) are vector
# paths: no font dependency, identical everywhere.
SPACE = 9.0
STAFF_LEFT = 70.0
SYSTEM_W = 880.0
LINE_W = SYSTEM_W - STAFF_LEFT - 20.0
HEAD_RX, HEAD_RY = 6.0, 4.4
STEM_LEN = 32.0
CHORD_ONSET_TOL = 0.03
SECOND_DX = 12.0

DISPLAY_VALS = [(4.0, "whole", 0), (2.0, "half", 0), (1.0, "quarter", 0),
                (0.5, "eighth", 1), (0.25, "16th", 2), (0.125, "32nd", 3)]


def display_value(dur_beats: float) -> Tuple[str, int, int]:
    """Map a beat duration to (name, dots, flags). Whole/half are hollow,
    whole has no stem. Triplet-ish values map to the plain note below."""
    d = max(0.05, dur_beats)
    if abs(d - 1 / 3) <= 0.02:
        return "eighth", 0, 1
    if abs(d - 2 / 3) <= 0.03:
        return "quarter", 0, 0
    # Prefer simple values: all exact matches beat all dotted ones.
    for base, name, flags in DISPLAY_VALS:
        if abs(d - base) <= 0.10 * base + 0.02:
            return name, 0, flags
    for base, name, flags in DISPLAY_VALS:
        if abs(d - 1.5 * base) <= 0.08 * base + 0.015:
            return name, 1, flags
    for base, name, flags in DISPLAY_VALS:
        if abs(d - 1.75 * base) <= 0.08 * base + 0.015:
            return name, 2, flags
    _, name, flags = min(DISPLAY_VALS, key=lambda v: abs(math.log(d / v[0])))
    return name, 0, flags


def notation_beats(n: Note, tempo: TempoMap) -> Tuple[float, float]:
    """Quantized (onset, duration) in beats, derived from seconds when the
    note was never quantized (stale edits, hand-built scores)."""
    if n.beat_onset is not None and n.beat_dur is not None:
        return n.beat_onset, max(0.05, n.beat_dur)
    on = seconds_to_beats(n.start_sec, tempo)
    return on, max(0.05, seconds_to_beats(n.end_sec, tempo) - on)


def _pitch_y(pitch: int, clef: str, top_y: float) -> float:
    steps, _ = staff_steps(pitch, clef)
    bottom_y = top_y + 4 * SPACE  # 5 lines
    return bottom_y - steps * (SPACE / 2.0)


# -- vector glyphs ---------------------------------------------------------------
# A glyph is a list of subpaths; a subpath is a list of ops:
# ("M",x,y) ("L",x,y) ("C",x1,y1,x2,y2,x,y) ("Q",x1,y1,x,y) ("Z",).
# Coordinates absolute in layout space.
def _svg_d(ops: List[tuple]) -> str:
    parts = []
    for op in ops:
        parts.append(op[0] + " " + " ".join(f"{v:.1f}" for v in op[1:]))
    return " ".join(parts)


def svg_paths(subpaths: List[List[tuple]], stroke: str, width: float,
              fill: str = "none") -> str:
    return "".join(
        f'<path d="{_svg_d(sp)}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{width:.1f}" stroke-linecap="round"/>\n' for sp in subpaths)


def glyph_g_clef(cx: float, top: float) -> List[List[tuple]]:
    s = SPACE
    gy = top + 3 * s  # G line (2nd from bottom)
    return [
        [("M", cx, top - 17), ("C", cx + 1.5, top + 2, cx - 1.5, top + 18, cx, top + 4 * s + 13)],
        [("M", cx, top + 4 * s + 13), ("C", cx - 1, top + 4 * s + 17, cx - 6, top + 4 * s + 19,
                                       cx - 9, top + 4 * s + 16)],
        [("M", cx, top - 17), ("C", cx - 5, top - 15, cx - 7, top - 11, cx - 6, top - 7)],
        [("M", cx + 6.5, gy), ("C", cx + 6.5, gy - 3.3, cx + 3.6, gy - 6, cx - 0.5, gy - 6),
         ("C", cx - 5.5, gy - 6, cx - 7.5, gy - 3, cx - 7.5, gy + 0.5),
         ("C", cx - 7.5, gy + 4, cx - 4, gy + 6.5, cx - 0.5, gy + 6.5),
         ("C", cx + 2.5, gy + 6.5, cx + 4.5, gy + 5, cx + 5.5, gy + 3)],
    ]


def glyph_f_clef(cx: float, top: float) -> List[List[tuple]]:
    s = SPACE
    fy = top + s  # F line (2nd from top)
    return [
        [("M", cx + 6, fy - 1), ("C", cx - 6, fy - 1, cx - 9, fy - 10, cx - 4, fy - 17)],
        [("M", cx + 6, fy - 1), ("C", cx + 2, fy + 2, cx - 2, fy + 4, cx - 5, fy + 3)],
    ]


def glyph_sharp(x: float, y: float) -> List[List[tuple]]:
    return [
        [("M", x + 3.5, y - 9), ("L", x + 3.5, y + 9)],
        [("M", x + 8.5, y - 9), ("L", x + 8.5, y + 9)],
        [("M", x - 1, y - 2.5), ("L", x + 13, y - 5)],
        [("M", x - 1, y + 5), ("L", x + 13, y + 2.5)],
    ]


def glyph_natural(x: float, y: float) -> List[List[tuple]]:
    return [
        [("M", x + 2.5, y + 3), ("L", x + 2.5, y - 9)],
        [("M", x + 8.5, y - 3), ("L", x + 8.5, y + 9)],
        [("M", x + 2.5, y - 3), ("L", x + 8.5, y - 3)],
        [("M", x + 2.5, y + 3), ("L", x + 8.5, y + 3)],
    ]


def glyph_quarter_rest(cx: float, top: float) -> List[List[tuple]]:
    mid = top + 2 * SPACE
    return [[("M", cx - 1.5, mid - 12), ("L", cx + 2.5, mid - 5), ("L", cx - 2.5, mid + 1),
             ("L", cx + 2, mid + 6), ("C", cx + 4, mid + 8, cx + 1, mid + 11.5, cx - 3, mid + 11)]]


def glyph_8th_rest(cx: float, top: float, second_dot: bool = False) -> List[List[tuple]]:
    mid = top + 2 * SPACE
    ops: List[List[tuple]] = [[("M", cx + 1, mid - 4), ("Q", cx + 6, mid + 1, cx - 2, mid + 9)]]
    if second_dot:
        ops.append([("M", cx + 1, mid + 3), ("Q", cx + 6, mid + 8, cx - 2, mid + 16)])
    return ops


def glyph_flag(stem_x: float, tip_y: float, stem_up: bool, count: int) -> List[List[tuple]]:
    out = []
    for k in range(count):
        b = tip_y + (k * 8 if stem_up else -k * 8)
        if stem_up:
            out.append([("M", stem_x, b), ("C", stem_x + 1, b + 4, stem_x + 6, b + 5,
                                           stem_x + 5, b + 11)])
        else:
            out.append([("M", stem_x, b), ("C", stem_x + 1, b - 4, stem_x + 6, b - 5,
                                           stem_x + 5, b - 11)])
    return out


def glyph_tie(x1: float, y: float, x2: float, below: bool) -> List[tuple]:
    s = 1.0 if below else -1.0
    return [("M", x1, y + 2 * s), ("Q", (x1 + x2) / 2, y + 9 * s, x2, y + 2 * s)]


def _ledger_ys(steps: int, top: float) -> List[float]:
    out = []
    if steps > 8:
        for k in range(1, int((steps - 8 + 1) // 2) + 1):
            out.append(top - k * SPACE)
    elif steps < 0:
        for k in range(1, int((-steps + 1) // 2) + 1):
            out.append(top + 4 * SPACE + k * SPACE)
    return out


def _rest_parts(gap_beats: float) -> List[str]:
    """Greedy decomposition of a rest gap into standard values."""
    parts = []
    rem = gap_beats
    for val, name in ((4.0, "whole"), (2.0, "half"), (1.0, "quarter"),
                      (0.5, "8th"), (0.25, "16th")):
        while rem >= val - 0.06 and len(parts) < 6:
            parts.append(name)
            rem -= val
        if rem < 0.18:
            break
    return parts


def _layout_staff(score: Score, hand: str, clef: str, top: float,
                  ms: float, me: float, x0: float, mw: float,
                  prev_heads: Dict[str, Dict[str, Any]], gidx: int) -> Dict[str, Any]:
    mnotes = [n for n in score.notes
              if n.hand == hand and n.end_sec > ms and n.start_sec < me]
    mnotes.sort(key=lambda n: (max(n.start_sec, ms), n.pitch))
    clusters: List[List[Any]] = []
    for n in mnotes:
        t = max(n.start_sec, ms)
        if clusters and t - clusters[-1][0] <= CHORD_ONSET_TOL:
            clusters[-1][1].append(n)
        else:
            clusters.append([t, [n]])
    heads: List[Dict[str, Any]] = []
    ties: List[Dict[str, Any]] = []
    acc_state: Dict[int, int] = {}
    placed_acc: List[Tuple[float, float]] = []
    for t, grp in clusters:
        frac = (t - ms) / max(1e-6, me - ms)
        cx = x0 + 12 + frac * (mw - 24)
        infos = []
        for n in grp:
            steps, alter = staff_steps(n.pitch, clef)
            _, dur_b = notation_beats(n, score.tempo)
            name, dots, flags = display_value(dur_b)
            infos.append({"n": n, "steps": steps, "alter": alter, "dur_name": name,
                          "dots": dots, "flags": flags,
                          "y": top + 4 * SPACE - steps * (SPACE / 2.0)})
        infos.sort(key=lambda d: -d["steps"])
        mean_steps = sum(d["steps"] for d in infos) / len(infos)
        stem_down = mean_steps >= 4
        all_whole = all(d["dur_name"] == "whole" for d in infos)
        base_off = SECOND_DX if stem_down else -SECOND_DX
        offs = [0.0] * len(infos)
        for i in range(1, len(infos)):
            if infos[i - 1]["steps"] - infos[i]["steps"] == 1:
                offs[i] = base_off if offs[i - 1] != base_off else -base_off
        ys = [d["y"] for d in infos]
        if all_whole:
            stem = None
        elif stem_down:
            stem = {"x": cx - 5.4, "y1": min(ys), "y2": max(ys) + STEM_LEN,
                    "flags": max(d["flags"] for d in infos)}
        else:
            stem = {"x": cx + 5.4, "y1": max(ys), "y2": min(ys) - STEM_LEN,
                    "flags": max(d["flags"] for d in infos)}
        min_hx = cx + min(offs)
        for d, off in zip(infos, offs):
            n = d["n"]
            hx = cx + off
            # accidental with per-measure state + natural cancellation
            acc = None
            if d["alter"] == 1 and acc_state.get(d["steps"]) != 1:
                acc = ("sharp", min_hx - 15)
                acc_state[d["steps"]] = 1
            elif d["alter"] == 0 and acc_state.get(d["steps"]) == 1:
                acc = ("natural", min_hx - 15)
                acc_state[d["steps"]] = 0
            if acc is not None:
                ax = acc[1]
                for px, py in placed_acc:
                    if abs(ax - px) < 9 and abs(d["y"] - py) < 10:
                        ax = px - 9
                placed_acc.append((ax, d["y"]))
                acc = (acc[0], ax)
            color = {"detected": "#1a1a1a", "imported": "#1a1a1a", "arranged": "#2a5db0",
                     "user-edited": "#b05a2a"}.get(n.provenance, "#1a1a1a")
            heads.append({"note": n, "x": hx, "cx": cx, "y": d["y"], "steps": d["steps"],
                          "dur_name": d["dur_name"], "dots": d["dots"], "acc": acc,
                          "stem_down": stem_down, "whole": d["dur_name"] == "whole",
                          "color": color,
                          "ledger": _ledger_ys(d["steps"], top),
                          "finger": n.finger, "top": top})
            # tie from the previous measure's head of the same note
            if n.start_sec < ms - 1e-6:
                prev = prev_heads.get(n.id)
                if prev is not None and prev["gidx"] == gidx - 1:
                    below = not stem_down
                    ties.append({"x1": prev["x"] + 6, "x2": hx - 6, "y": d["y"],
                                 "below": below})
            prev_heads[n.id] = {"x": hx, "gidx": gidx}
        if stem is not None:
            heads.append({"stem": stem, "stem_down": stem_down, "top": top})
    # rests: greedy-fill gaps between sounding intervals
    rests: List[Dict[str, Any]] = []
    spans = sorted([(max(n.start_sec, ms), min(n.end_sec, me)) for n in mnotes])
    merged: List[List[float]] = []
    for s0, s1 in spans:
        if merged and s0 <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], s1)
        else:
            merged.append([s0, s1])
    gaps = []
    cur = ms
    for s0, s1 in merged:
        if s0 > cur + 1e-6:
            gaps.append((cur, s0))
        cur = max(cur, s1)
    if cur < me - 1e-6:
        gaps.append((cur, me))
    if not mnotes:
        rests.append({"kind": "whole", "x": x0 + mw / 2, "top": top})
    else:
        for g0, g1 in gaps:
            gb = seconds_to_beats(g1, score.tempo) - seconds_to_beats(g0, score.tempo)
            if gb < 0.18:
                continue
            b0 = seconds_to_beats(g0, score.tempo)
            for part in _rest_parts(gb):
                val = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "8th": 0.5,
                       "16th": 0.25}[part]
                t = beats_to_seconds(b0, score.tempo)
                frac = max(0.0, min(1.0, (t - ms) / max(1e-6, me - ms)))
                rests.append({"kind": part, "x": x0 + 12 + frac * (mw - 24),
                              "top": top})
                b0 += val
    return {"clef": clef, "top": top, "heads": heads, "rests": rests, "ties": ties}


def layout_score(score: Score, max_measures: int = 400) -> Dict[str, Any]:
    """Full engraving layout shared by the SVG and PDF renderers."""
    end_sec = max([n.end_sec for n in score.notes], default=1.0)
    bounds = measure_bounds(score.tempo, end_sec)[:max_measures]
    per_system = 4
    systems = []
    prev_heads: Dict[str, Dict[str, Any]] = {}
    nbars = len(bounds) or 1
    for si in range(0, nbars, per_system):
        sys_bounds = bounds[si:si + per_system] or [(0.0, 1.0)]
        y0 = 54 + (si // per_system) * 150.0
        treble_top, bass_top = y0 + 8, y0 + 8 + 5 * SPACE + 26
        sys = {"y0": y0, "treble_top": treble_top, "bass_top": bass_top,
               "measures": []}
        for mi, (ms, me) in enumerate(sys_bounds):
            gidx = si + mi
            mw = LINE_W / len(sys_bounds)
            x0 = STAFF_LEFT + mi * mw
            meas = {"gidx": gidx, "ms": ms, "me": me, "x0": x0, "mw": mw,
                    "staves": {
                        "R": _layout_staff(score, "R", "treble", treble_top, ms, me,
                                           x0, mw, prev_heads, gidx),
                        "L": _layout_staff(score, "L", "bass", bass_top, ms, me,
                                           x0, mw, prev_heads, gidx)}}
            for nid in [k for k, v in prev_heads.items()
                        if score.get(k) is not None and score.get(k).end_sec <= ms]:
                del prev_heads[nid]
            sys["measures"].append(meas)
        systems.append(sys)
    H = len(systems) * 150.0 + 60
    return {"systems": systems, "W": SYSTEM_W, "H": H, "title": score.title,
            "bpb": max(1, score.tempo.beats_per_bar)}


def _svg_rest(s: io.StringIO, kind: str, cx: float, top: float) -> None:
    if kind == "whole":
        s.write(f'<rect x="{cx - 5:.1f}" y="{top + SPACE:.1f}" width="10" height="4.5" fill="#1a1a1a"/>\n')
    elif kind == "half":
        s.write(f'<rect x="{cx - 5:.1f}" y="{top + 2 * SPACE - 4.5:.1f}" width="10" height="4.5" fill="#1a1a1a"/>\n')
    elif kind == "quarter":
        s.write(svg_paths(glyph_quarter_rest(cx, top), "#1a1a1a", 1.8))
    elif kind in ("8th", "16th"):
        mid = top + 2 * SPACE
        s.write(f'<circle cx="{cx:.1f}" cy="{mid - 5:.1f}" r="1.9" fill="#1a1a1a"/>\n')
        if kind == "16th":
            s.write(f'<circle cx="{cx - 1:.1f}" cy="{mid + 2:.1f}" r="1.7" fill="#1a1a1a"/>\n')
        s.write(svg_paths(glyph_8th_rest(cx, top, kind == "16th"), "#1a1a1a", 1.6))


def render_svg(score: Score, max_measures: int = 400) -> str:
    lay = layout_score(score, max_measures)
    W, H = lay["W"], lay["H"]
    s = io.StringIO()
    s.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}" role="img" aria-label="Piano score">\n')
    s.write('<style>.nl{stroke:#333;stroke-width:1}'
            '.bar{stroke:#333;stroke-width:1.2}.mno{font:10px sans-serif;fill:#888}'
            '.notehead{cursor:pointer}.chord-sym{font:600 12px Georgia,serif;fill:#17407f}'
            '.fing{font:7.5px sans-serif;fill:#5b6470}.tsig{font:700 15px sans-serif;fill:#1a1a1a}'
            'svg.hide-chords .chord-sym{display:none}svg.hide-fing .fing{display:none}</style>\n')
    s.write(f'<text x="{STAFF_LEFT}" y="22" font-family="sans-serif" font-size="16" font-weight="bold">'
            f'{escape(score.title)}</text>\n')
    n_sys = len(lay["systems"])
    for si, sys in enumerate(lay["systems"]):
        treble_top, bass_top = sys["treble_top"], sys["bass_top"]
        for top in (treble_top, bass_top):
            for ln in range(5):
                yy = top + ln * SPACE
                s.write(f'<line class="nl" x1="{STAFF_LEFT}" y1="{yy}" x2="{STAFF_LEFT + LINE_W}" y2="{yy}"/>\n')
        s.write(svg_paths(glyph_g_clef(38, treble_top), "#1a1a1a", 2.0))
        s.write(svg_paths(glyph_f_clef(38, bass_top), "#1a1a1a", 2.2))
        fy = bass_top + SPACE
        s.write(f'<circle cx="44" cy="{fy - 1:.1f}" r="2" fill="#1a1a1a"/>\n'
                f'<circle cx="48" cy="{fy - 4.5:.1f}" r="1.7" fill="#1a1a1a"/>\n'
                f'<circle cx="48" cy="{fy + 4.5:.1f}" r="1.7" fill="#1a1a1a"/>\n')
        if si == 0:
            for top in (treble_top, bass_top):
                s.write(f'<text class="tsig" x="56" y="{top + SPACE + 5:.1f}" text-anchor="middle">{lay["bpb"]}</text>\n'
                        f'<text class="tsig" x="56" y="{top + 3 * SPACE + 5:.1f}" text-anchor="middle">4</text>\n')
        s.write(f'<line class="bar" x1="{STAFF_LEFT}" y1="{treble_top}" x2="{STAFF_LEFT}" y2="{bass_top + 4 * SPACE}"/>\n')
        for mi, meas in enumerate(sys["measures"]):
            last_overall = si == n_sys - 1 and mi == len(sys["measures"]) - 1
            x0, mw, ms, me = meas["x0"], meas["mw"], meas["ms"], meas["me"]
            s.write(f'<line class="bar" x1="{x0 + mw}" y1="{treble_top}" x2="{x0 + mw}" y2="{bass_top + 4 * SPACE}"/>\n')
            if last_overall:
                s.write(f'<rect x="{x0 + mw + 2.5}" y="{treble_top}" width="3.5" '
                        f'height="{(bass_top + 4 * SPACE) - treble_top}" fill="#333"/>\n')
            s.write(f'<text class="mno" x="{x0 + 3}" y="{treble_top - 4}">m{meas["gidx"] + 1}</text>\n')
            s.write(f'<rect data-measure="{meas["gidx"]}" data-t0="{ms:.3f}" data-t1="{me:.3f}" x="{x0}" '
                    f'y="{treble_top - 14}" width="{mw}" height="{(bass_top + 4 * SPACE) - treble_top + 14}" '
                    f'fill="transparent" class="measure-hit"/>\n')
            for hand in ("R", "L"):
                st = meas["staves"][hand]
                for r in st["rests"]:
                    _svg_rest(s, r["kind"], r["x"], r["top"])
                for h in st["heads"]:
                    if "stem" in h:
                        sm = h["stem"]
                        s.write(f'<line x1="{sm["x"]:.1f}" y1="{sm["y1"]:.1f}" x2="{sm["x"]:.1f}" y2="{sm["y2"]:.1f}" '
                                f'stroke="#1a1a1a" stroke-width="1.3"/>\n')
                        if sm["flags"]:
                            s.write(svg_paths(glyph_flag(sm["x"], sm["y2"], not h["stem_down"],
                                                         sm["flags"]), "#1a1a1a", 1.7))
                        continue
                    n = h["note"]
                    for ly in h["ledger"]:
                        s.write(f'<line class="nl" x1="{h["x"] - 8.5:.1f}" y1="{ly:.1f}" '
                                f'x2="{h["x"] + 8.5:.1f}" y2="{ly:.1f}"/>\n')
                    if h["acc"] is not None:
                        kind, ax = h["acc"]
                        glyph = glyph_sharp(ax, h["y"]) if kind == "sharp" else glyph_natural(ax, h["y"])
                        s.write(svg_paths(glyph, "#1a1a1a", 1.4))
                    hollow = h["dur_name"] in ("whole", "half")
                    if hollow:
                        s.write(f'<ellipse class="notehead" data-note="{n.id}" data-time="{n.start_sec:.3f}" '
                                f'cx="{h["x"]:.1f}" cy="{h["y"]:.1f}" rx="6.5" ry="4.8" fill="#fff" '
                                f'stroke="{h["color"]}" stroke-width="1.6" '
                                f'transform="rotate(-18 {h["x"]:.1f} {h["y"]:.1f})">'
                                f'<title>{NAMES[n.pitch % 12]}{n.pitch // 12 - 1} · {n.start_sec:.2f}s · {n.provenance}</title>'
                                f'</ellipse>\n')
                    else:
                        s.write(f'<ellipse class="notehead" data-note="{n.id}" data-time="{n.start_sec:.3f}" '
                                f'cx="{h["x"]:.1f}" cy="{h["y"]:.1f}" rx="6" ry="4.4" fill="{h["color"]}" '
                                f'transform="rotate(-18 {h["x"]:.1f} {h["y"]:.1f})">'
                                f'<title>{NAMES[n.pitch % 12]}{n.pitch // 12 - 1} · {n.start_sec:.2f}s · {n.provenance}</title>'
                                f'</ellipse>\n')
                    for di in range(h["dots"]):
                        dy = h["y"] if h["steps"] % 2 == 1 else h["y"] - SPACE / 2
                        s.write(f'<circle cx="{h["x"] + 11 + di * 7:.1f}" cy="{dy:.1f}" r="1.7" fill="#1a1a1a"/>\n')
                    if h["finger"]:
                        fx = h["x"] - 10 if not h["stem_down"] else h["x"] + 10
                        s.write(f'<text class="fing" x="{fx:.1f}" y="{h["y"] + 3.5:.1f}" '
                                f'text-anchor="middle">{int(h["finger"])}</text>\n')
                for tie in st["ties"]:
                    s.write(svg_paths([glyph_tie(tie["x1"], tie["y"], tie["x2"], tie["below"])],
                                      "#1a1a1a", 1.3))
    s_chords_open = False
    for cx, cy, sym in _chord_placements(lay, score.chords):
        if not s_chords_open:
            s.write('<g id="chordSyms">\n')
            s_chords_open = True
        s.write(f'<text class="chord-sym" x="{cx:.1f}" y="{cy:.1f}">{escape(sym)}</text>\n')
    if s_chords_open:
        s.write('</g>\n')
    s.write('</svg>\n')
    return s.getvalue()


def _chord_placements(lay: Dict[str, Any], chords: List[Dict[str, Any]]):
    """(x, y, symbol) in layout space, thinned so crowded mid-measure
    changes don't overprint each other. Measure-start symbols always stay."""
    out = []
    last_cx = -100.0
    for c in chords:
        sym = chord_symbol(str(c.get("label", "")))
        if not sym:
            continue
        t = float(c.get("start_sec", 0))
        lay_m = None
        for sys in lay["systems"]:
            for meas in sys["measures"]:
                if meas["ms"] - 1e-6 <= t < meas["me"] - 1e-6:
                    lay_m = (meas, sys["treble_top"])
        if lay_m is None:
            continue
        meas, treble_top = lay_m
        cx = meas["x0"] + 4 + max(0.0, (t - meas["ms"]) / max(1e-6, meas["me"] - meas["ms"])) * (meas["mw"] - 8)
        if cx < last_cx - 100:
            last_cx = -100.0
        if cx - last_cx < 44 and not (t < meas["ms"] + 0.05):
            continue
        last_cx = cx
        out.append((cx, treble_top - 16, sym))
    return out


# -- PDF (same engraving as SVG, via reportlab) ------------------------------------------
def _rl_draw_ops(c: Any, subpaths: List[List[tuple]], tr: Any, width: float) -> None:
    for sp in subpaths:
        p = c.beginPath()
        px = py = 0.0
        for op in sp:
            k = op[0]
            if k == "M":
                px, py = tr(op[1], op[2])
                p.moveTo(px, py)
            elif k == "L":
                px, py = tr(op[1], op[2])
                p.lineTo(px, py)
            elif k == "C":
                x1, y1 = tr(op[1], op[2])
                x2, y2 = tr(op[3], op[4])
                x3, y3 = tr(op[5], op[6])
                p.curveTo(x1, y1, x2, y2, x3, y3)
                px, py = x3, y3
            elif k == "Q":
                x1, y1 = tr(op[1], op[2])
                x3, y3 = tr(op[3], op[4])
                c1 = (px + 2 / 3 * (x1 - px), py + 2 / 3 * (y1 - py))
                c2 = (x3 + 2 / 3 * (x1 - x3), y3 + 2 / 3 * (y1 - y3))
                p.curveTo(c1[0], c1[1], c2[0], c2[1], x3, y3)
                px, py = x3, y3
            elif k == "Z":
                p.close()
        c.setLineWidth(width)
        c.setLineCap(1)
        c.drawPath(p, stroke=1, fill=0)


def to_pdf_bytes(score: Score) -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    lay = layout_score(score, 256)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    W, H = letter
    c.setTitle(score.title)
    k = (W - 72) / SYSTEM_W
    per_page = 5
    page_sys = 0
    cursor = H - 100
    first_page = True
    n_sys = len(lay["systems"])
    for si, sys in enumerate(lay["systems"]):
        if page_sys == per_page:
            c.showPage()
            page_sys = 0
            cursor = H - 60
            first_page = False
        if first_page and page_sys == 0:
            c.setFont("Helvetica-Bold", 16)
            c.drawString(36, H - 46, score.title)
            c.setFont("Helvetica", 9)
            proc = score.processing
            c.drawString(36, H - 60, f'Mode: {score.mode}  ·  Notes: {len(score.notes)}  ·  '
                         f'Model: {proc.get("model", "?")} {proc.get("model_version", "")}  ·  Score v{score.version}')
        y0 = sys["y0"]
        X = lambda x: 36 + x * k
        Y = lambda y: cursor - (y - y0) * k
        c.setStrokeColorRGB(0.2, 0.2, 0.2)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        treble_top, bass_top = sys["treble_top"], sys["bass_top"]
        c.setLineWidth(0.7)
        for top in (treble_top, bass_top):
            for ln in range(5):
                yy = Y(top + ln * SPACE)
                c.line(X(STAFF_LEFT), yy, X(STAFF_LEFT + LINE_W), yy)
        tr = lambda x, y: (X(x), Y(y))
        _rl_draw_ops(c, glyph_g_clef(38, treble_top), tr, 1.2)
        _rl_draw_ops(c, glyph_f_clef(38, bass_top), tr, 1.3)
        fy = bass_top + SPACE
        for dx, dy, r in ((6, -1, 2.0), (10, -4.5, 1.7), (10, 4.5, 1.7)):
            c.circle(X(38 + dx), Y(fy + dy), r * k, stroke=0, fill=1)
        if si == 0:
            c.setFont("Helvetica-Bold", 10)
            for top in (treble_top, bass_top):
                c.drawCentredString(X(56), Y(top + SPACE + 5) - 3, str(lay["bpb"]))
                c.drawCentredString(X(56), Y(top + 3 * SPACE + 5) - 3, "4")
        c.setLineWidth(0.9)
        c.line(X(STAFF_LEFT), Y(treble_top), X(STAFF_LEFT), Y(bass_top + 4 * SPACE))
        for mi, meas in enumerate(sys["measures"]):
            last_overall = si == n_sys - 1 and mi == len(sys["measures"]) - 1
            x0, mw = meas["x0"], meas["mw"]
            c.setLineWidth(0.9)
            c.line(X(x0 + mw), Y(treble_top), X(x0 + mw), Y(bass_top + 4 * SPACE))
            if last_overall:
                c.rect(X(x0 + mw) + 1.5, Y(bass_top + 4 * SPACE),
                       2.2, Y(treble_top) - Y(bass_top + 4 * SPACE), stroke=0, fill=1)
            c.setFont("Helvetica", 6)
            c.setFillGray(0.5)
            c.drawString(X(x0) + 2, Y(treble_top) + 8, f'm{meas["gidx"] + 1}')
            c.setFillGray(0.1)
            for hand in ("R", "L"):
                st = meas["staves"][hand]
                for r in st["rests"]:
                    rx, rtop = X(r["x"]), r["top"]
                    if r["kind"] == "whole":
                        c.rect(rx - 3, Y(rtop + SPACE + 4.5), 6, 4.5 * k, stroke=0, fill=1)
                    elif r["kind"] == "half":
                        c.rect(rx - 3, Y(rtop + 2 * SPACE), 6, 4.5 * k, stroke=0, fill=1)
                    elif r["kind"] == "quarter":
                        _rl_draw_ops(c, glyph_quarter_rest(r["x"], rtop), tr, 1.1)
                    else:
                        mid = rtop + 2 * SPACE
                        c.circle(rx, Y(mid - 5), 1.9 * k, stroke=0, fill=1)
                        if r["kind"] == "16th":
                            c.circle(rx - k, Y(mid + 2), 1.7 * k, stroke=0, fill=1)
                        _rl_draw_ops(c, glyph_8th_rest(r["x"], rtop, r["kind"] == "16th"), tr, 1.0)
                for h in st["heads"]:
                    if "stem" in h:
                        sm = h["stem"]
                        c.setLineWidth(0.9)
                        c.line(X(sm["x"]), Y(sm["y1"]), X(sm["x"]), Y(sm["y2"]))
                        if sm["flags"]:
                            _rl_draw_ops(c, glyph_flag(sm["x"], sm["y2"], not h["stem_down"],
                                                      sm["flags"]), tr, 1.0)
                        continue
                    for ly in h["ledger"]:
                        c.setLineWidth(0.7)
                        c.line(X(h["x"] - 8.5), Y(ly), X(h["x"] + 8.5), Y(ly))
                    if h["acc"] is not None:
                        kind, ax = h["acc"]
                        glyph = glyph_sharp(ax, h["y"]) if kind == "sharp" else glyph_natural(ax, h["y"])
                        _rl_draw_ops(c, glyph, tr, 0.9)
                    hollow = h["dur_name"] in ("whole", "half")
                    rx, ry = (6.5 * k, 4.8 * k) if hollow else (6.0 * k, 4.4 * k)
                    if hollow:
                        c.setLineWidth(1.0)
                        c.ellipse(X(h["x"]) - rx, Y(h["y"]) - ry, X(h["x"]) + rx, Y(h["y"]) + ry,
                                  stroke=1, fill=0)
                    else:
                        c.ellipse(X(h["x"]) - rx, Y(h["y"]) - ry, X(h["x"]) + rx, Y(h["y"]) + ry,
                                  stroke=0, fill=1)
                    for di in range(h["dots"]):
                        dy = h["y"] if h["steps"] % 2 == 1 else h["y"] - SPACE / 2
                        c.circle(X(h["x"] + 11 + di * 7), Y(dy), 1.7 * k, stroke=0, fill=1)
                    if h["finger"]:
                        fx = h["x"] - 10 if not h["stem_down"] else h["x"] + 10
                        c.setFont("Helvetica", 5)
                        c.setFillGray(0.4)
                        c.drawCentredString(X(fx), Y(h["y"]) - 2, str(int(h["finger"])))
                        c.setFillGray(0.1)
                for tie in st["ties"]:
                    _rl_draw_ops(c, [glyph_tie(tie["x1"], tie["y"], tie["x2"], tie["below"])], tr, 0.9)
        for cx, cy, sym in _chord_placements(lay, score.chords):
            if sys["y0"] - 30 <= cy <= sys["y0"] + 150:
                c.setFont("Helvetica-Bold", 8)
                c.setFillColorRGB(23 / 255, 64 / 255, 127 / 255)
                c.drawString(X(cx), Y(cy) - 3, sym)
                c.setFillGray(0.1)
        cursor -= 150 * k + 14
        page_sys += 1
    c.showPage()
    c.save()
    return buf.getvalue()
