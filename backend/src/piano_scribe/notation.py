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
            if n.finger:
                out.write(f'<notations><technical><fingering>{int(n.finger)}</fingering>'
                          f'</technical></notations>')
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
    out.write(f'<note><rest/><duration>{dur}</duration><voice>1</voice><staff>{staff_no}</staff></note>')


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


# -- SVG -------------------------------------------------------------------------------
SPACE = 9.0
STAFF_LEFT = 70.0
SYSTEM_W = 880.0
LINE_W = SYSTEM_W - STAFF_LEFT - 20.0


def _pitch_y(pitch: int, clef: str, top_y: float) -> float:
    steps, _ = staff_steps(pitch, clef)
    bottom_y = top_y + 4 * SPACE  # 5 lines
    return bottom_y - steps * (SPACE / 2.0)


def render_svg(score: Score, max_measures: int = 400) -> str:
    end_sec = max([n.end_sec for n in score.notes], default=1.0)
    bounds = measure_bounds(score.tempo, end_sec)[:max_measures]
    per_system = 4
    systems = [bounds[i:i + per_system] for i in range(0, len(bounds), per_system)] or [[(0.0, 1.0)]]
    sys_h = 150.0
    H = len(systems) * sys_h + 60
    W = SYSTEM_W
    s = io.StringIO()
    s.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}" role="img" aria-label="Piano score">\n')
    s.write('<style>.nl{stroke:#333;stroke-width:1}.mh{fill:#1a1a1a}.acc{font:12px serif}'
            '.bar{stroke:#333;stroke-width:1.2}.mno{font:10px sans-serif;fill:#888}'
            '.notehead{cursor:pointer}.chord-sym{font:600 12px Georgia,serif;fill:#17407f}'
            '.fing{font:7.5px sans-serif;fill:#5b6470}'
            'svg.hide-chords .chord-sym{display:none}svg.hide-fing .fing{display:none}</style>\n')
    s.write(f'<text x="{STAFF_LEFT}" y="22" font-family="sans-serif" font-size="16" font-weight="bold">'
            f'{escape(score.title)}</text>\n')
    layouts: List[Dict[str, Any]] = []
    # state for accidental suppression per system+staff+line
    for si, sys_bounds in enumerate(systems):
        y0 = 54 + si * sys_h
        treble_top, bass_top = y0 + 8, y0 + 8 + 5 * SPACE + 26
        for top in (treble_top, bass_top):
            for ln in range(5):
                yy = top + ln * SPACE
                s.write(f'<line class="nl" x1="{STAFF_LEFT}" y1="{yy}" x2="{STAFF_LEFT + LINE_W}" y2="{yy}"/>\n')
        s.write(f'<text x="30" y="{treble_top + 4 * SPACE}" font-size="26" font-family="serif">𝄞</text>'
                f'<text x="30" y="{bass_top + 4 * SPACE}" font-size="24" font-family="serif">𝄢</text>\n')
        s.write(f'<line class="bar" x1="{STAFF_LEFT}" y1="{treble_top}" x2="{STAFF_LEFT}" y2="{bass_top + 4 * SPACE}"/>\n')
        mw = LINE_W / len(sys_bounds)
        acc_state: Dict[Tuple[str, int], int] = {}
        for mi, (ms, me) in enumerate(sys_bounds):
            x0 = STAFF_LEFT + mi * mw
            gidx = si * per_system + mi
            s.write(f'<line class="bar" x1="{x0 + mw}" y1="{treble_top}" x2="{x0 + mw}" y2="{bass_top + 4 * SPACE}"/>\n')
            s.write(f'<text class="mno" x="{x0 + 3}" y="{treble_top - 4}">m{gidx + 1}</text>\n')
            s.write(f'<rect data-measure="{gidx}" data-t0="{ms:.3f}" data-t1="{me:.3f}" x="{x0}" '
                    f'y="{treble_top - 14}" width="{mw}" height="{(bass_top + 4 * SPACE) - treble_top + 14}" '
                    f'fill="transparent" class="measure-hit"/>\n')
            layouts.append({"ms": ms, "me": me, "x0": x0, "mw": mw, "treble_top": treble_top})
            for n in score.notes:
                if n.end_sec <= ms or n.start_sec >= me:
                    continue
                clef = "treble" if n.hand == "R" else "bass"
                top = treble_top if n.hand == "R" else bass_top
                frac = (max(n.start_sec, ms) - ms) / max(1e-6, me - ms)
                x = x0 + 12 + frac * (mw - 24)
                y = _pitch_y(n.pitch, clef, top)
                steps, alter = staff_steps(n.pitch, clef)
                # ledger lines
                if steps > 8:
                    ln = int((steps - 8 + 1) // 2)
                    for k in range(1, ln + 1):
                        ly = top - k * SPACE
                        s.write(f'<line class="nl" x1="{x - 8}" y1="{ly}" x2="{x + 8}" y2="{ly}"/>\n')
                elif steps < 0:
                    ln = int((-steps + 1) // 2)
                    for k in range(1, ln + 1):
                        ly = top + 4 * SPACE + k * SPACE
                        s.write(f'<line class="nl" x1="{x - 8}" y1="{ly}" x2="{x + 8}" y2="{ly}"/>\n')
                if alter:
                    key = (clef, steps)
                    if acc_state.get(key) != alter:
                        s.write(f'<text class="acc" x="{x - 15}" y="{y + 4}">♯</text>\n')
                        acc_state[key] = alter
                color = {"detected": "#1a1a1a", "imported": "#1a1a1a", "arranged": "#2a5db0", "user-edited": "#b05a2a"}.get(n.provenance, "#1a1a1a")
                s.write(f'<ellipse class="notehead" data-note="{n.id}" data-time="{n.start_sec:.3f}" '
                        f'cx="{x:.1f}" cy="{y:.1f}" rx="6" ry="4.4" fill="{color}" '
                        f'transform="rotate(-18 {x:.1f} {y:.1f})">'
                        f'<title>{NAMES[n.pitch % 12]}{n.pitch // 12 - 1} · {n.start_sec:.2f}s · {n.provenance}</title>'
                        f'</ellipse>\n')
                # stem
                stem_up = y > top + 2 * SPACE
                xstem = x - 5.4 if stem_up else x + 5.4
                y2 = y - 32 if stem_up else y + 32
                s.write(f'<line x1="{xstem:.1f}" y1="{y:.1f}" x2="{xstem:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="1.3"/>\n')
                if n.finger:
                    fx = x + 8 if stem_up else x - 8
                    s.write(f'<text class="fing" x="{fx:.1f}" y="{y + 3:.1f}" text-anchor="middle">{int(n.finger)}</text>\n')
    if score.chords:
        s.write('<g id="chordSyms">\n')
        for c in score.chords:
            sym = chord_symbol(str(c.get("label", "")))
            if not sym:
                continue
            t = float(c.get("start_sec", 0))
            lay = next((L for L in layouts if L["ms"] - 1e-6 <= t < L["me"] - 1e-6), None)
            if lay is None:
                continue
            cx = lay["x0"] + 4 + max(0.0, (t - lay["ms"]) / max(1e-6, lay["me"] - lay["ms"])) * (lay["mw"] - 8)
            s.write(f'<text class="chord-sym" x="{cx:.1f}" y="{lay["treble_top"] - 16:.1f}">{escape(sym)}</text>\n')
        s.write('</g>\n')
    s.write('</svg>\n')
    return s.getvalue()


# -- PDF (simplified engraving via reportlab) ---------------------------------------------
def to_pdf_bytes(score: Score) -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    W, H = letter
    c.setTitle(score.title)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, H - 50, score.title)
    c.setFont("Helvetica", 9)
    proc = score.processing
    c.drawString(50, H - 64, f'Mode: {score.mode}  ·  Notes: {len(score.notes)}  ·  '
                 f'Model: {proc.get("model", "?")} {proc.get("model_version", "")}  ·  Score v{score.version}')
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, H - 76, "Simplified engraving for review — MusicXML export carries full notation.")
    end_sec = max([n.end_sec for n in score.notes], default=1.0)
    bounds = measure_bounds(score.tempo, end_sec)[:256]
    per_system, per_page = 4, 5
    y = H - 120
    sp = 7.0
    left, width = 45, W - 90
    for si in range(0, len(bounds), per_system):
        if si > 0 and (si // per_system) % per_page == 0:
            c.showPage()
            y = H - 70
        sys_b = bounds[si:si + per_system]
        treble, bass = y, y - 5 * sp - 24
        for top in (treble, bass):
            for ln in range(5):
                yy = top - ln * sp
                c.line(left, yy, left + width, yy)
        mw = width / len(sys_b)
        for mi, (ms, me) in enumerate(sys_b):
            x0 = left + mi * mw
            c.setFont("Helvetica", 7)
            c.drawString(x0 + 2, treble + 10, f'm{si + mi + 1}')
            c.line(x0 + mw, treble, x0 + mw, bass - 4 * sp)
            for n in score.notes:
                if n.end_sec <= ms or n.start_sec >= me:
                    continue
                clef = "treble" if n.hand == "R" else "bass"
                top = treble if n.hand == "R" else bass
                frac = (max(n.start_sec, ms) - ms) / max(1e-6, me - ms)
                x = x0 + 10 + frac * (mw - 20)
                steps, _ = staff_steps(n.pitch, clef)
                ny = (top - 4 * sp) + steps * (sp / 2.0)
                c.ellipse(x - 4.5, ny - 3.2, x + 4.5, ny + 3.2, fill=1, stroke=0)
        y = bass - 4 * sp - 34
    c.showPage()
    c.save()
    return buf.getvalue()
